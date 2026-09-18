from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import time
import sys
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np
from playwright.async_api import Locator, TimeoutError as PlaywrightTimeoutError, async_playwright

from backend.audit_agent.crawler_account_identity import (
    candidate_is_personal_link,
    extract_platform_account_id,
)
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_browser import load_account_browser, load_request_scheduler


PLATFORMS = {
    "xhs": {
        "url": "https://www.xiaohongshu.com",
        "qr_selectors": (
            "img.qrcode-img",
            "xpath=//img[contains(@class, 'qrcode-img')]",
        ),
        "login_triggers": (
            "xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",
            "xpath=//button[contains(normalize-space(), '登录')]",
        ),
    },
    "dy": {
        "url": "https://www.douyin.com",
        "qr_selectors": (
            "#animate_qrcode_container img",
            "xpath=//div[@id='animate_qrcode_container']//img",
        ),
        "login_triggers": (
            "xpath=//p[normalize-space() = '登录']",
            "xpath=//button[contains(normalize-space(), '登录')]",
        ),
    },
    "ks": {
        "url": "https://www.kuaishou.com/?isHome=1",
        "qr_selectors": (
            ".qrcode-img img",
            "xpath=//div[contains(@class, 'qrcode-img')]//img",
        ),
        "login_triggers": (
            "xpath=//p[normalize-space() = '登录']",
            "xpath=//button[contains(normalize-space(), '登录')]",
        ),
    },
}

DY_AUTH_COOKIE_NAMES = (
    "sessionid",
    "sessionid_ss",
    "sid_guard",
    "sid_tt",
    "uid_tt",
    "uid_tt_ss",
)
LOGIN_SETTLE_SECONDS = 3
QR_DISAPPEARANCE_CONFIRMATIONS = 2
DY_LOGIN_NETWORK_MARKERS = ("login", "passport", "qrcode", "scan")
DY_DIAGNOSTIC_SAFE_FIELDS = (
    "status", "state", "code", "error", "message", "msg", "reason",
    "description", "verify", "confirm", "result", "login",
)
DY_DIAGNOSTIC_SENSITIVE_FIELDS = (
    "token", "cookie", "ticket", "sign", "session", "secret", "uid", "user",
    "avatar", "nickname", "name", "url", "redirect",
)

# Identity lookup only reads links already rendered by the logged-in homepage. The
# broad fallback selectors require an explicit personal-account label so feed
# author links cannot be mistaken for the selected crawler account.
IDENTITY_LINK_SELECTORS = {
    "xhs": (
        ("xpath=//a[contains(@href, '/user/profile/')][.//span[normalize-space()='我']]", False),
    ),
    "dy": (
        ("header a[href*='/user/']", False),
        ("#douyin-header a[href*='/user/']", False),
        ("a[data-e2e*='user'][href*='/user/']", False),
        ("[data-e2e*='user'] a[href*='/user/']", False),
        ("a[href*='/user/']", True),
    ),
    "ks": (
        ("header a[href*='/profile/']", False),
        ("#header a[href*='/profile/']", False),
        ("a[data-e2e*='user'][href*='/profile/']", False),
        ("[data-e2e*='user'] a[href*='/profile/']", False),
        ("a[href*='/profile/']", True),
    ),
}

def emit(event_type: str, **payload) -> None:
    print(
        json.dumps({"type": event_type, **payload}, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


def diagnostic_url(url: str) -> str:
    """Keep only the request origin and path; login query strings contain secrets."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def safe_login_response_fields(payload, *, prefix: str = "", depth: int = 0) -> dict:
    """Extract status-like fields while excluding identities and login credentials."""
    if depth > 6:
        return {}
    safe: dict[str, object] = {}
    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            key = str(raw_key)
            lowered = key.lower()
            path = f"{prefix}.{key}" if prefix else key
            if any(marker in lowered for marker in DY_DIAGNOSTIC_SENSITIVE_FIELDS):
                continue
            if isinstance(value, (dict, list)):
                safe.update(safe_login_response_fields(value, prefix=path, depth=depth + 1))
            elif value is None or isinstance(value, (bool, int, float, str)):
                if any(marker in lowered for marker in DY_DIAGNOSTIC_SAFE_FIELDS):
                    text = value if not isinstance(value, str) else value[:300]
                    safe[path] = text
    elif isinstance(payload, list):
        for index, value in enumerate(payload[:10]):
            safe.update(
                safe_login_response_fields(value, prefix=f"{prefix}[{index}]", depth=depth + 1)
            )
    return safe


async def capture_douyin_login_response(response, events: list[dict]) -> None:
    """Record only endpoint paths and non-secret status fields from login traffic."""
    raw_url = str(response.url)
    if not any(marker in raw_url.lower() for marker in DY_LOGIN_NETWORK_MARKERS):
        return
    record = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "url": diagnostic_url(raw_url),
        "http_status": response.status,
        "method": response.request.method,
        "resource_type": response.request.resource_type,
    }
    try:
        text = await response.text()
        payload = json.loads(text)
        fields = safe_login_response_fields(payload)
        if fields:
            record["fields"] = fields
        else:
            # Preserve only coarse, non-secret response shape for unknown schemas.
            record["body_shape"] = (
                sorted(str(key) for key in payload)[:50]
                if isinstance(payload, dict)
                else type(payload).__name__
            )
    except Exception as exc:
        record["body_error"] = type(exc).__name__
    events.append(record)
    del events[:-120]


async def capture_douyin_login_diagnostic(
    page,
    context,
    account_id: str,
    baseline_cookies: dict[str, str],
    *,
    reason: str,
    network_events: list[dict] | None = None,
) -> None:
    """Persist post-scan evidence without ever writing cookie values."""
    try:
        diagnostic_dir = Path(
            os.getenv(
                "CRAWLER_LOGIN_DIAGNOSTICS_DIR",
                str(settings.data_dir / "crawler-login-diagnostics"),
            )
        ).expanduser().resolve()
        diagnostic_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(diagnostic_dir, 0o700)
        safe_account_id = "".join(char for char in account_id if char.isalnum()) or "unknown"
        prefix = diagnostic_dir / f"{safe_account_id}-latest"

        cookies = await context.cookies()
        cookie_names = sorted({str(item.get("name") or "") for item in cookies if item.get("name")})
        cookie_map = {
            str(item.get("name") or ""): str(item.get("value") or "")
            for item in cookies
            if item.get("name")
        }
        browser_state = await page.evaluate(
            """
            () => ({
              localStorageKeys: Object.keys(window.localStorage).sort(),
              sessionStorageKeys: Object.keys(window.sessionStorage).sort(),
              hasUserLogin: window.localStorage.getItem('HasUserLogin') || '',
              visibilityState: document.visibilityState,
              bodyText: (document.body?.innerText || '').slice(0, 6000),
            })
            """
        )
        record = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "account_id": safe_account_id,
            "url": page.url,
            "title": await page.title(),
            "cookie_names": cookie_names,
            "changed_cookie_names": sorted(
                name
                for name, value in cookie_map.items()
                if value and value != baseline_cookies.get(name, "")
            ),
            "login_status": cookie_map.get("LOGIN_STATUS", ""),
            "login_network_events": list(network_events or []),
            **browser_state,
        }
        json_path = prefix.with_suffix(".json")
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        os.chmod(json_path, 0o600)
        screenshot_path = prefix.with_suffix(".png")
        await page.screenshot(path=str(screenshot_path), full_page=True)
        os.chmod(screenshot_path, 0o600)
    except Exception as exc:
        print(f"login diagnostic capture failed: {type(exc).__name__}: {exc}", file=sys.stderr)


async def first_visible(page, selectors: tuple[str, ...]) -> Locator | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible(timeout=400):
                return locator
        except Exception:
            continue
    return None


def qr_image_shape(png: bytes) -> tuple[np.ndarray, bool]:
    encoded = np.frombuffer(png, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    if image is None or image.ndim != 2:
        return image, False
    height, width = image.shape
    if min(height, width) < 80 or max(height, width) / min(height, width) > 1.35:
        return image, False
    return image, True


def is_qr_png(png: bytes) -> bool:
    """Verify screenshot contents so ordinary login artwork is never exposed as a QR."""
    image, valid_shape = qr_image_shape(png)
    if not valid_shape:
        return False
    detector = cv2.QRCodeDetector()
    for candidate in (image, cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]):
        try:
            detected, points = detector.detect(candidate)
        except cv2.error:
            continue
        if detected and points is not None:
            return True
    return False


async def first_valid_qr(page, config: dict) -> tuple[Locator, bytes] | None:
    checked = set()
    for selector in config["qr_selectors"]:
        matches = page.locator(selector)
        try:
            count = min(await matches.count(), 8)
        except Exception:
            continue
        for index in range(count):
            locator = matches.nth(index)
            try:
                if not await locator.is_visible(timeout=400):
                    continue
                png = await locator.screenshot(type="png")
                digest = hashlib.sha256(png).digest()
                if digest in checked:
                    continue
                checked.add(digest)
                _, valid_shape = qr_image_shape(png)
                if is_qr_png(png) or (config.get("trust_qr_selector") and valid_shape):
                    return locator, png
            except Exception:
                continue
    return None


async def wait_for_qr(page, config: dict, timeout_seconds: float = 35) -> tuple[Locator, bytes]:
    deadline = time.monotonic() + timeout_seconds
    triggered = False
    while time.monotonic() < deadline:
        qr = await first_valid_qr(page, config)
        if qr:
            return qr
        if not triggered:
            for selector in config["login_triggers"]:
                trigger = page.locator(selector).first
                try:
                    if await trigger.is_visible(timeout=350):
                        await trigger.click(timeout=2500)
                        triggered = True
                        break
                except Exception:
                    continue
        await asyncio.sleep(0.6)
    raise RuntimeError("未识别到有效登录二维码，平台页面可能已改版或要求滑块等额外验证")


async def is_logged_in(platform: str, page, context, baseline_cookies: dict[str, str]) -> bool:
    cookies = {item["name"]: item["value"] for item in await context.cookies()}
    if platform == "xhs":
        current = cookies.get("web_session", "")
        qr_is_visible = await first_visible(page, PLATFORMS[platform]["qr_selectors"])
        return bool(
            current
            and current != baseline_cookies.get("web_session", "")
            and qr_is_visible is None
        )
    if platform == "dy":
        if cookies.get("LOGIN_STATUS") == "1":
            return True
        for candidate_page in list(context.pages):
            try:
                if await candidate_page.evaluate(
                    "window.localStorage.getItem('HasUserLogin') === '1'"
                ):
                    return True
            except Exception:
                continue
        return any(
            cookies.get(name)
            and cookies.get(name) != baseline_cookies.get(name, "")
            for name in DY_AUTH_COOKIE_NAMES
        )
    return bool(cookies.get("passToken"))


async def rendered_link_candidates(page, selector: str) -> list[dict]:
    try:
        return await page.locator(selector).evaluate_all(
            """
            anchors => anchors.map(anchor => {
              const rect = anchor.getBoundingClientRect();
              const style = window.getComputedStyle(anchor);
              return {
                href: anchor.href || anchor.getAttribute('href') || '',
                text: (anchor.innerText || anchor.textContent || '').trim(),
                label: (anchor.getAttribute('aria-label') || anchor.getAttribute('title') || '').trim(),
                hasImage: Boolean(anchor.querySelector('img')),
                inContent: Boolean(anchor.closest('main, article')),
                top: rect.top,
                width: rect.width,
                height: rect.height,
                visible: style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
              };
            })
            """
        )
    except Exception:
        return []


async def identify_logged_in_account(platform: str, context) -> str:
    """Passively identify the current account without making an extra platform request."""
    for candidate_page in list(context.pages):
        current_id = extract_platform_account_id(platform, candidate_page.url)
        if current_id:
            return current_id

        for selector, require_marker in IDENTITY_LINK_SELECTORS.get(platform, ()):
            matched_ids = {
                extract_platform_account_id(platform, candidate.get("href", ""))
                for candidate in await rendered_link_candidates(candidate_page, selector)
                if candidate_is_personal_link(platform, candidate, require_marker)
            }
            matched_ids.discard("")
            if len(matched_ids) == 1:
                return matched_ids.pop()
    return ""


@asynccontextmanager
async def login_context(platform: str, account_id: str = "", *, headless: bool = True):
    if platform == "dy":
        adapter = load_account_browser()
        if settings.crawler_login_browser_version:
            # Existing profiles retain their pinned version; new profiles use the deployment pin.
            adapter.profile_for(account_id, settings.crawler_browser_profile_root, settings.crawler_login_browser_version)
        context, profile = await adapter.launch_account_context(
            account_id, settings.crawler_browser_profile_root, headless=headless,
        )
        try:
            await request_login_reset()
            if headless:
                await adapter.prepare_account_login(context, profile)
            else:
                from scripts.crawler_login_pages import prepare_headed_login
                await prepare_headed_login(context, profile)
            yield context
        finally:
            if sys.exc_info()[0] is not None:
                # Cleanup must not hide the original navigation/reset failure.
                with suppress(Exception):
                    await asyncio.wait_for(context.close(), 15)
            else:
                await asyncio.wait_for(context.close(), 15)
        return

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        try:
            yield context
        finally:
            await context.close()
            await browser.close()


async def request_login_reset():
    """The manager records login_required before this process clears credentials."""
    emit("reauth_started")
    reply = await asyncio.wait_for(asyncio.to_thread(sys.stdin.readline), timeout=10)
    if reply.strip() != "reauth_ready":
        raise RuntimeError("登录管理器未确认状态更新，原浏览器登录态保留")


async def run_login(platform: str, timeout_seconds: int, account_id: str = "", *,
                    headless: bool = True, expected_platform_account_id: str = "") -> None:
    config = PLATFORMS[platform]
    login_started_at = time.monotonic()
    success = None
    async with login_context(platform, account_id, headless=headless) as context:
        page = await context.new_page()
        page.set_default_timeout(12_000)
        login_network_events: list[dict] = []
        login_network_tasks: set[asyncio.Task] = set()
        if platform == "dy":
            def enqueue_login_response(response) -> None:
                task = asyncio.create_task(
                    capture_douyin_login_response(response, login_network_events)
                )
                login_network_tasks.add(task)
                task.add_done_callback(login_network_tasks.discard)

            page.on("response", enqueue_login_response)
        try:
            try:
                if platform == "dy":
                    gate = load_request_scheduler().configured_gate()
                    async with gate.slot('login_navigation', timeout=60):
                        await page.goto(config["url"], wait_until="domcontentloaded", timeout=60_000)
                else:
                    await page.goto(config["url"], wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightTimeoutError:
                pass

            await wait_for_qr(page, config)
            baseline_cookies = {
                item["name"]: item["value"]
                for item in await context.cookies()
            }
            deadline = login_started_at + timeout_seconds
            last_qr_hash = ""
            next_qr_check = 0.0
            login_confirmed_at: float | None = None
            qr_missing_checks = 0
            scanned_emitted = False
            next_scanned_diagnostic = 0.0

            while time.monotonic() < deadline:
                now = time.monotonic()
                if await is_logged_in(platform, page, context, baseline_cookies):
                    if login_confirmed_at is None:
                        login_confirmed_at = now
                        emit("finalizing", duration_seconds=LOGIN_SETTLE_SECONDS)
                    # Douyin writes several cookies after the first login signal. Give the
                    # redirect a moment to settle before serializing the browser state.
                    if now - login_confirmed_at >= LOGIN_SETTLE_SECONDS:
                        platform_account_id = await identify_logged_in_account(platform, context)
                        if (expected_platform_account_id and platform_account_id
                                and platform_account_id != expected_platform_account_id):
                            await context.clear_cookies()
                            await page.evaluate("localStorage.clear(); sessionStorage.clear()")
                            raise RuntimeError("登录的抖音账号与所选账号不一致，请使用原账号重新登录")
                        success = dict(auth_state=await context.storage_state(),
                                       platform_account_id=platform_account_id)
                        break
                else:
                    if login_confirmed_at is not None:
                        emit("waiting_scan")
                    login_confirmed_at = None

                if login_confirmed_at is None and now >= next_qr_check:
                    valid_qr = await first_valid_qr(page, config)
                    if valid_qr:
                        _, png = valid_qr
                        was_scanned = scanned_emitted
                        qr_missing_checks = 0
                        scanned_emitted = False
                        digest = hashlib.sha256(png).hexdigest()
                        if digest != last_qr_hash or was_scanned:
                            last_qr_hash = digest
                            remaining = max(1, int(deadline - now))
                            expires_at = datetime.now(timezone.utc) + timedelta(seconds=min(90, remaining))
                            emit(
                                "qr",
                                image_data_url=(
                                    "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                                ),
                                expires_at=expires_at.isoformat(),
                            )
                    elif last_qr_hash:
                        qr_missing_checks += 1
                        if (
                            qr_missing_checks >= QR_DISAPPEARANCE_CONFIRMATIONS
                            and not scanned_emitted
                        ):
                            scanned_emitted = True
                            emit("scanned")
                    next_qr_check = now + 2.5
                if platform == "dy" and scanned_emitted and now >= next_scanned_diagnostic:
                    if login_network_tasks:
                        await asyncio.gather(*list(login_network_tasks), return_exceptions=True)
                    await capture_douyin_login_diagnostic(
                        page,
                        context,
                        account_id,
                        baseline_cookies,
                        reason="post_scan_wait",
                        network_events=login_network_events,
                    )
                    next_scanned_diagnostic = now + 5
                await asyncio.sleep(0.8)

            if success is None:
                if platform == "dy" and scanned_emitted:
                    if login_network_tasks:
                        await asyncio.gather(*list(login_network_tasks), return_exceptions=True)
                    await capture_douyin_login_diagnostic(
                        page,
                        context,
                        account_id,
                        baseline_cookies,
                        reason="expired_after_scan",
                        network_events=login_network_events,
                    )
                emit("expired", message="二维码登录已超时，请重新获取二维码")
        except Exception:
            # Never export a partially completed login as a successful backup.
            success = None
            raise
    if success is not None:
        emit("success", **success)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless crawler account QR login")
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--account-id", default="")
    parser.add_argument("--expected-platform-account-id", default="")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        if args.interactive:
            if args.platform != "dy":
                raise ValueError("当前平台不支持交互登录")
            from scripts.crawler_interactive_login import run_interactive_login
            asyncio.run(run_interactive_login(args.account_id, max(60, args.timeout), args.expected_platform_account_id))
        else:
            asyncio.run(run_login(args.platform, max(60, args.timeout), args.account_id,
                                 headless=not args.headed,
                                 expected_platform_account_id=args.expected_platform_account_id))
    except KeyboardInterrupt:
        emit("error", message="登录进程被中断，请重新获取二维码")
    except Exception as exc:
        emit("error", message=str(exc) or "登录进程异常退出")
