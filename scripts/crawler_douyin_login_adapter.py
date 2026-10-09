"""Douyin login observation. Only operate controls rendered in the real page."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from urllib.parse import urlsplit

from backend.audit_agent.crawler_account_identity import douyin_self_response_identity

ENTRY_URLS = {
    "self": "https://www.douyin.com/user/self?from_nav=1",
    "home": "https://www.douyin.com",
}
PANEL_SELECTOR = ".douyin_login_new_class"
QR_SELECTOR = "#animate_qrcode_container img"
CHALLENGE_SELECTORS = (
    "#captcha_container",
    "#captcha-verify-container",
    "[class*='captcha_verify_container']",
    "iframe[src*='verify']",
    "iframe[src*='captcha']",
)
SAFE_IFRAME_HOSTS = {"lf-rc1.yhgfb-cn-static.com"}


def document_allowed(url: str, *, top_level: bool) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "douyin.com"
        or host.endswith(".douyin.com")
        or (not top_level and host in SAFE_IFRAME_HOSTS)
    )


@dataclass(frozen=True)
class Region:
    x: float
    y: float
    width: float
    height: float

    def clip(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def point(self, x: float, y: float) -> tuple[float, float]:
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("Pointer outside verification region")
        return self.x + x * self.width, self.y + y * self.height


class DouyinLoginAdapter:
    def __init__(self, context, page, *, block_media: bool = False):
        self.context, self.page = context, page
        self.block_media = block_media
        self.cdp = None
        self.identity = ""
        self.identity_source = ""
        self.identity_conflict = False
        self.sms_selected = False
        self.manual = False
        self.sms_form_seen = False
        self.sms_resend_seconds = None
        self.network_bytes = 0
        self.request_count = 0
        self.blocked_documents = 0
        self.tasks: set[asyncio.Task] = set()
        self.guards = []
        self.platform_signal = ""
        self.sms_result = "unknown"
        self.last_qr_digest = ""
        self._closed = False

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def install(self):
        # Fetch only intercepts documents; Playwright route() would disable cache.
        await self.install_guard(self.page)
        self.context.on("page", self._new_page)
        self.context.on("response", self._response)
        self.context.on("dialog", self._dialog)
        return self

    def _dialog(self, dialog):
        # Unknown native prompts are not automatically confirmed.
        self.task(dialog.dismiss())

    def _new_page(self, page):
        if page is not self.page and not self._closed:
            self.task(self.install_guard(page))

    async def install_guard(self, page):
        if page.is_closed():
            return
        cdp = await self.context.new_cdp_session(page)
        tree = await cdp.send("Page.getFrameTree")
        main_frame_id = tree["frameTree"]["frame"]["id"]

        async def paused(event):
            request_id = event["requestId"]
            method = "Fetch.continueRequest"
            params = {"requestId": request_id}
            if not document_allowed(
                event["request"]["url"], top_level=event.get("frameId") == main_frame_id
            ):
                self.blocked_documents += 1
                method, params = (
                    "Fetch.failRequest",
                    {"requestId": request_id, "errorReason": "BlockedByClient"},
                )
            try:
                await asyncio.wait_for(cdp.send(method, params), 3)
            except Exception:
                # A failed guard must not leave a browser navigation parked forever.
                if not page.is_closed():
                    await page.close()

        cdp.on("Fetch.requestPaused", lambda event: self.task(paused(event)))
        await cdp.send(
            "Fetch.enable",
            {
                "patterns": [
                    {
                        "urlPattern": "*",
                        "resourceType": "Document",
                        "requestStage": "Request",
                    }
                ]
            },
        )
        await cdp.send("Network.enable")
        cdp.on(
            "Network.requestWillBeSent",
            lambda _: setattr(self, "request_count", self.request_count + 1),
        )
        cdp.on(
            "Network.loadingFinished",
            lambda e: setattr(
                self,
                "network_bytes",
                self.network_bytes + int(e.get("encodedDataLength", 0)),
            ),
        )
        if self.block_media:
            # Opt-in; do not block images, XHR or verification media indiscriminately.
            await cdp.send(
                "Network.setBlockedURLs",
                {"urls": ["*://*.douyinvod.com/*", "*://*.douyinvod.com.cn/*"]},
            )
        self.guards.append(cdp)
        if page is self.page:
            self.cdp = cdp
        elif page.url not in {"about:blank", ""} and not document_allowed(
            page.url, top_level=True
        ):
            await page.close()

    def _response(self, response):
        if not self._closed:
            self.task(self.observe_response(response))

    async def observe_response(self, response):
        parsed = urlsplit(response.url)
        if not document_allowed(response.url, top_level=True):
            return
        if parsed.path.rstrip("/") != "/aweme/v1/web/user/profile/self":
            return
        try:
            payload = await asyncio.wait_for(response.json(), 3)
            identity = douyin_self_response_identity(response.url, payload)
            if identity:
                if self.identity and self.identity != identity:
                    self.identity_conflict = True
                self.identity, self.identity_source = identity, "douyin_self_response"
        except Exception:
            return

    async def navigate(self, entry: str):
        from scripts.crawler_account_login import load_request_scheduler

        gate = load_request_scheduler().configured_gate()
        async with gate.slot("login_navigation", timeout=60):
            try:
                await self.page.goto(
                    ENTRY_URLS[entry], wait_until="domcontentloaded", timeout=45000
                )
            except Exception as exc:
                from playwright.async_api import TimeoutError

                if not isinstance(exc, TimeoutError):
                    raise

    async def has_auth(self) -> bool:
        cookies = {
            c["name"]: c["value"]
            for c in await asyncio.wait_for(
                self.context.cookies("https://www.douyin.com"), 5
            )
        }
        return bool(cookies.get("sessionid") or cookies.get("sessionid_ss"))

    async def trusted_identity(self) -> str:
        if self.identity_conflict:
            raise ValueError("IDENTITY_UNCONFIRMED")
        if self.identity:
            return self.identity
        # Only an explicitly labelled personal menu, never current /user URL/feed links.
        anchors = await self.page.locator(
            "header a[href*='/user/'], #douyin-header a[href*='/user/']"
        ).evaluate_all(
            """es => es.map(e => ({href:e.href, label:(e.innerText+' '+(e.getAttribute('aria-label')||'')+' '+(e.title||'')).trim(), visible:!!(e.offsetWidth&&e.offsetHeight)}))"""
        )
        from backend.audit_agent.crawler_account_identity import (
            extract_platform_account_id,
        )

        candidates = {
            extract_platform_account_id("dy", a["href"])
            for a in anchors
            if a["visible"]
            and any(
                label in a["label"] for label in ("我的主页", "个人主页", "我的抖音")
            )
        }
        candidates.discard("")
        if len(candidates) == 1:
            self.identity = candidates.pop()
            self.identity_source = "personal_header_menu"
        return self.identity

    async def visible(self, selector):
        locator = self.page.locator(selector).first
        try:
            return locator if await locator.is_visible(timeout=100) else None
        except Exception:
            return None

    async def region(self, challenge_only=False):
        selectors = (
            CHALLENGE_SELECTORS
            if challenge_only
            else (*CHALLENGE_SELECTORS, PANEL_SELECTOR)
        )
        viewport = self.page.viewport_size or {"width": 1000, "height": 760}
        for selector in selectors:
            locator = await self.visible(selector)
            if not locator:
                continue
            box = await locator.bounding_box()
            if not box:
                continue
            x, y = max(0, box["x"]), max(0, box["y"])
            width = min(box["width"], viewport["width"] - x)
            height = min(box["height"], viewport["height"] - y)
            if width >= 80 and height >= 60:
                return Region(x, y, width, height)
        return None

    async def region_signature(self):
        for selector in (*CHALLENGE_SELECTORS, PANEL_SELECTOR):
            locator = await self.visible(selector)
            if locator:
                value = await locator.evaluate(
                    """e => ({src:e.getAttribute('src'),id:e.getAttribute('data-challenge-id'),images:Array.from(e.querySelectorAll('img')).map(i=>i.src)})"""
                )
                # Static image/iframe sources identify a new question, unlike
                # screenshots whose contents change on every drag or drawing stroke.
                import json

                return hashlib.sha256(
                    json.dumps(value, sort_keys=True).encode()
                ).hexdigest()
        return ""

    async def qr(self):
        locator = await self.visible(QR_SELECTOR)
        if locator is None:
            return None
        png = None
        try:
            source = await locator.get_attribute("src") or ""
            if source.startswith("data:image/") and ";base64," in source:
                png = base64.b64decode(source.split(",", 1)[1], validate=True)
            elif source.startswith("blob:"):
                encoded = await locator.evaluate(
                    """async e => { const b=await (await fetch(e.src)).blob();return await new Promise((resolve,reject)=>{let r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(b)}); }"""
                )
                png = base64.b64decode(encoded, validate=True)
            if not png or len(png) > 32768:
                png = await locator.screenshot(type="png", timeout=2000)
            from scripts.crawler_account_login import is_qr_png

            if not is_qr_png(png):
                return None
            digest = hashlib.sha256(png).hexdigest()
            self.last_qr_digest = digest
            return {
                "image_data_url": "data:image/png;base64,"
                + base64.b64encode(png).decode("ascii"),
                "revision": digest,
                "state": "valid",
                "expires_at": None,
            }
        except Exception:
            return None

    async def sms_controls(self):
        panel = self.page.locator(PANEL_SELECTOR).first
        phone = panel.locator("input[placeholder*='手机号'], input[type='tel']").first
        code = panel.locator("input[placeholder*='验证码']").first
        request = panel.get_by_text(
            re.compile(r"^(获取验证码|重新获取|重新发送|\d+\s*[sS秒].*)$")
        ).first
        submit = panel.get_by_text("登录", exact=True).first
        if await code.is_visible() and await submit.is_visible():
            return {"phone": phone, "code": code, "request": request, "submit": submit}
        return None

    async def observe_sms_result(self):
        """A rendered resend countdown proves sending; click completion does not."""
        panel = await self.visible(PANEL_SELECTOR)
        if not panel:
            return self.sms_result
        text = await panel.inner_text()
        if any(
            term in text
            for term in (
                "发送失败",
                "操作频繁",
                "验证码错误",
                "验证码不正确",
                "手机号格式错误",
            )
        ):
            self.sms_result = "rejected"
        elif self.sms_result == "pending":
            countdown = re.search(
                r"(?:重新发送|重新获取)?\s*(\d{1,3})\s*(?:秒|[sS])(?:后|重新|\b)", text
            )
            if countdown:
                self.sms_resend_seconds = int(countdown.group(1))
                self.sms_result = "confirmed"
        return self.sms_result

    async def observe(self):
        await self.page.evaluate(
            "document.querySelectorAll('video').forEach(v=>{v.pause();v.autoplay=false})"
        )
        region = await self.region(challenge_only=True)
        if region:
            return {
                "phase": "challenge_required",
                "region": region,
                "region_signature": await self.region_signature(),
            }
        qr = await self.qr()
        panel = await self.visible(PANEL_SELECTOR)
        text = await panel.inner_text() if panel else ""
        # A coexisting form is normal. Only an explicit requirement or user intent selects it.
        required_sms = any(
            term in text
            for term in (
                "请完成手机验证",
                "请验证手机号",
                "需要验证手机号",
                "短信安全验证",
            )
        )
        if (self.sms_selected or required_sms) and await self.sms_controls():
            controls = await self.sms_controls()
            self.sms_form_seen = True
            phone_visible = await controls["phone"].is_visible()
            code_length = await controls["code"].get_attribute("maxlength")
            await self.observe_sms_result()
            fields = (
                [{"name": "phone", "label": "手机号", "max_length": 20}]
                if phone_visible
                else []
            )
            fields.append(
                {
                    "name": "code",
                    "label": "短信验证码",
                    "max_length": int(code_length)
                    if code_length and code_length.isdigit()
                    else 12,
                }
            )
            return {
                "phase": "sms_required",
                "sms": {
                    "fields": fields,
                    "send_status": self.sms_result,
                    "resend_available_at": (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=self.sms_resend_seconds)
                    ).isoformat()
                    if self.sms_resend_seconds
                    else None,
                    "error": "平台未接受本次操作，请检查输入或稍后重试"
                    if self.sms_result == "rejected"
                    else None,
                    "source": "rendered_login_form",
                },
            }
        if self.manual and panel:
            return {
                "phase": "manual_required",
                "region": await self.region(),
                "region_signature": await self.region_signature(),
            }
        if qr:
            expired = any(
                term in text
                for term in ("二维码已失效", "二维码已过期", "二维码失效", "点击刷新")
            )
            qr["state"] = "expired" if expired else "valid"
            return {"phase": "qr_expired" if expired else "qr_ready", "qr": qr}
        if "请在手机" in text or "手机上确认" in text:
            return {"phase": "phone_confirm"}
        return {"phase": "preparing"}

    async def click_text(self, texts):
        panel = self.page.locator(PANEL_SELECTOR).first
        for text in texts:
            locator = panel.get_by_text(text, exact=True).first
            if await locator.is_visible():
                await locator.click(timeout=2000)
                return
        raise ValueError("PLATFORM_CONTROL_UNAVAILABLE")

    async def open_login(self):
        if await self.visible(PANEL_SELECTOR):
            return
        from scripts.crawler_account_login import PLATFORMS

        for selector in PLATFORMS["dy"]["login_triggers"]:
            trigger = await self.visible(selector)
            if trigger:
                await trigger.click(timeout=2000)
                return

    async def close(self):
        self._closed = True
        self.context.remove_listener("page", self._new_page)
        self.context.remove_listener("response", self._response)
        self.context.remove_listener("dialog", self._dialog)
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
