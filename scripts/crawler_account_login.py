from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

from playwright.async_api import Locator, TimeoutError as PlaywrightTimeoutError, async_playwright

from backend.audit_agent.crawler_account_identity import (
    candidate_is_personal_link,
    extract_platform_account_id,
)


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


async def first_visible(page, selectors: tuple[str, ...]) -> Locator | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible(timeout=400):
                return locator
        except Exception:
            continue
    return None


async def wait_for_qr(page, config: dict, timeout_seconds: float = 35) -> Locator:
    deadline = time.monotonic() + timeout_seconds
    triggered = False
    while time.monotonic() < deadline:
        locator = await first_visible(page, config["qr_selectors"])
        if locator:
            return locator
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
    raise RuntimeError("未能加载登录二维码，平台页面可能已改版或要求额外验证")


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


async def run_login(platform: str, timeout_seconds: int) -> None:
    config = PLATFORMS[platform]
    login_started_at = time.monotonic()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
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
        page = await context.new_page()
        page.set_default_timeout(12_000)
        try:
            try:
                await page.goto(config["url"], wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightTimeoutError:
                pass

            qr_locator = await wait_for_qr(page, config)
            baseline_cookies = {
                item["name"]: item["value"]
                for item in await context.cookies()
            }
            deadline = login_started_at + timeout_seconds
            last_qr_hash = ""
            next_qr_check = 0.0
            login_confirmed_at: float | None = None

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
                        emit(
                            "success",
                            auth_state=await context.storage_state(),
                            platform_account_id=platform_account_id,
                        )
                        return
                else:
                    if login_confirmed_at is not None:
                        emit("waiting_scan")
                    login_confirmed_at = None

                if login_confirmed_at is None and now >= next_qr_check:
                    visible_qr = await first_visible(page, config["qr_selectors"])
                    if visible_qr:
                        qr_locator = visible_qr
                    try:
                        png = await qr_locator.screenshot(type="png")
                        digest = hashlib.sha256(png).hexdigest()
                        if digest != last_qr_hash:
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
                    except Exception:
                        pass
                    next_qr_check = now + 2.5
                await asyncio.sleep(0.8)

            emit("expired", message="二维码登录已超时，请重新获取二维码")
        finally:
            await context.close()
            await browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless crawler account QR login")
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(run_login(args.platform, max(60, args.timeout)))
    except KeyboardInterrupt:
        emit("error", message="登录进程被中断，请重新获取二维码")
    except Exception as exc:
        emit("error", message=str(exc) or "登录进程异常退出")
