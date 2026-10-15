"""Douyin login observation. Only operate controls rendered in the real page."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

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
LOGIN_ENTRY_READY_SECONDS = 120
LOGIN_DOCUMENT_READY_SECONDS = 60


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
    def __init__(
        self, context, page, *, block_media: bool = False, bound_proxy_session=None
    ):
        self.bound_proxy_session = bound_proxy_session
        self.context, self.page = context, page
        self.block_media = block_media
        self.identity = ""
        self.identity_source = ""
        self.identity_conflict = False
        self.identity_observed = asyncio.Event()
        self.sms_selected = False
        self.manual = False
        self.sms_form_seen = False
        self.sms_resend_seconds = None
        self.sms_resend_available_at = None
        self.sms_resend_allowed = False
        self.sms_request_observation_pending = False
        self.network_bytes = 0
        self.request_count = 0
        self.blocked_documents = 0
        self.tasks: set[asyncio.Task] = set()
        self._guard_installed = False
        self.platform_signal = ""
        self.sms_result = "unknown"
        self.last_qr_digest = ""
        self.last_qr_source_digest = ""
        self.last_qr = None
        self.qr_extraction_reason = "not_observed"
        self._closed = False
        self.network_receipts = []
        self.network_revision = 0
        self.latest_login_receipt = None
        self.qr_ticket = ""
        self.qr_ticket_digest = ""
        self.qr_platform_status = ""
        self.qr_response_generation = 0
        self.qr_accepted_generation = -1
        self.current_qr_error = None
        self.login_entry_diagnostic = None
        self.network_failures = Counter()

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def install(self):
        # Native Firefox addon guards redirected documents before transmission.
        # Context routing adds page policy and resource filtering.
        await self.install_guard(self.page)
        self.context.on("page", self._new_page)
        self.context.on("response", self._response)
        self.context.on("request", self._request)
        self.context.on("requestfinished", self._request_finished)
        self.context.on("requestfailed", self._request_failed)
        self.context.on("dialog", self._dialog)
        return self

    def _dialog(self, dialog):
        self.task(dialog.dismiss())

    def _new_page(self, page):
        if getattr(self.context, "_collection_proxy_probe_pending", False):
            page._collection_proxy_probe = True

    async def install_guard(self, page):
        if self._guard_installed:
            return
        await self.context.route("**/*", self._guard_request)
        self._guard_installed = True
        for existing in list(self.context.pages):
            if existing.url not in {"about:blank", ""} and not document_allowed(
                existing.url, top_level=True
            ):
                await existing.close()

    async def _guard_request(self, route):
        request = route.request
        try:
            # Firefox popup initial navigations can precede frame creation.
            try:
                frame = request.frame
            except Exception:
                frame = None
            probe = frame is not None and bool(
                getattr(frame.page, "_collection_proxy_probe", False)
            )
            session = self.bound_proxy_session or getattr(
                self.context, "_bound_proxy_session", None
            )
            if probe:
                # The transport guard owns exact URL, method, redirect and header checks.
                if (
                    session is None
                    or request.url != session.echo_url
                    or request.method != "GET"
                    or request.redirected_from is not None
                ):
                    await route.abort()
                    return
                await route.fallback()
                return
            if request.is_navigation_request() and not document_allowed(
                request.url, top_level=frame is None or frame.parent_frame is None
            ):
                self.blocked_documents += 1
                await route.abort()
                return
            host = urlsplit(request.url).hostname or ""
            if (
                self.block_media
                and request.resource_type == "media"
                and any(
                    host == suffix or host.endswith("." + suffix)
                    for suffix in ("douyinvod.com", "douyinvod.com.cn")
                )
            ):
                await route.abort()
                return
            await asyncio.wait_for(route.fallback(), 3)
        except Exception as exc:
            session = self.bound_proxy_session or getattr(
                self.context, "_bound_proxy_session", None
            )
            if session is not None:
                session.record_browser_guard_stop(
                    "DOCUMENT_GUARD_TIMEOUT"
                    if isinstance(exc, asyncio.TimeoutError)
                    else "DOCUMENT_GUARD_FAILED"
                )
            try:
                await route.abort()
            finally:
                await self.context.close()

    def _request(self, request):
        self.request_count += 1

    def _request_finished(self, request):
        if not self._closed:
            self.task(self._measure_request(request))

    async def _measure_request(self, request):
        try:
            sizes = await asyncio.wait_for(request.sizes(), 3)
            self.network_bytes += int(sizes.get("responseBodySize", 0)) + int(
                sizes.get("responseHeadersSize", 0)
            )
        except Exception:
            pass  # Accounting is diagnostic; safety belongs to the route authority.

    def _response(self, response):
        if response.status >= 400:
            self._record_network_failure(
                "http_" + str(response.status),
                response.url,
                getattr(response.request, "resource_type", "unknown"),
            )
        if not self._closed:
            self.task(self.observe_response(response))

    def _request_failed(self, request):
        failure = str(getattr(request, "failure", "") or "")
        match = re.search(r"net::(ERR_[A-Z_]+)", failure)
        self._record_network_failure(
            match.group(1) if match else "REQUEST_FAILED",
            request.url,
            getattr(request, "resource_type", "unknown"),
        )

    def _record_network_failure(self, code, url, resource_type):
        try:
            host = (urlsplit(url).hostname or "").encode("idna").decode("ascii").lower()
        except (ValueError, UnicodeError, AttributeError):
            return
        if (
            not host
            or len(host) > 253
            or not re.fullmatch(r"[a-z0-9.-]+", host)
            or not re.fullmatch(r"[A-Z0-9_]{3,48}|http_[45][0-9]{2}", str(code))
            or not re.fullmatch(r"[a-z_]{2,24}", str(resource_type))
        ):
            return
        self.network_failures[(str(code), host, str(resource_type))] += 1

    def network_failure_summary(self):
        return [
            {"code": code, "host": host, "resource_type": resource_type, "count": count}
            for (code, host, resource_type), count in self.network_failures.most_common(
                20
            )
        ]

    async def observe_response(self, response):
        # Freeze arrival order before parsing asynchronous response bodies.
        self.qr_response_generation += 1
        generation = self.qr_response_generation
        parsed = urlsplit(response.url)
        if not document_allowed(response.url, top_level=True):
            return
        path = parsed.path.rstrip("/")
        if parsed.hostname in {"login.douyin.com", "www.douyin.com"} and path in {
            "/passport/web/get_qrcode",
            "/passport/web/check_qrconnect",
        }:
            try:
                if self.page is not None and response.frame.page is not self.page:
                    return
            except AttributeError:
                pass
            except Exception:
                return
            try:
                payload = await asyncio.wait_for(response.json(), 3)
                data = payload.get("data") if isinstance(payload, dict) else None
                data = data if isinstance(data, dict) else {}
                if (
                    path.endswith("/get_qrcode")
                    and data.get("error_code") == 0
                    and isinstance(data.get("token"), str)
                ):
                    self.accept_qr_ticket(data, generation=generation)
                elif path.endswith("/check_qrconnect"):
                    query = parse_qs(parsed.query)
                    form = parse_qs(
                        getattr(getattr(response, "request", None), "post_data", None)
                        or ""
                    )
                    token = (form.get("token") or query.get("token") or [""])[0]
                    states = {
                        "1": "new",
                        "new": "new",
                        "2": "scanned",
                        "scanned": "scanned",
                        "3": "confirmed",
                        "confirmed": "confirmed",
                        "4": "expired",
                        "5": "expired",
                        "refused": "refused",
                        "expired": "expired",
                    }
                    status = states.get(str(data.get("status")))
                    if self.qr_ticket and token == self.qr_ticket:
                        code = data.get("error_code")
                        if type(code) is int:
                            self.current_qr_error = code if code != 0 else None
                    if (
                        self.qr_ticket
                        and token == self.qr_ticket
                        and status
                        and data.get("error_code") == 0
                    ):
                        self.qr_platform_status = status
                        if status in {"expired", "refused"}:
                            self.accept_qr_ticket(data, generation=generation)

                receipt = {
                    "token_matches_current": bool(
                        path.endswith("/check_qrconnect")
                        and token == self.qr_ticket
                        and self.qr_ticket
                    ),
                    "source_matches_current": bool(
                        self.qr_ticket_digest
                        and self.last_qr_source_digest == self.qr_ticket_digest
                    ),
                    "has_verification_decision": "verify_center_decision_conf" in data
                    or "verify_center_decision_conf" in payload,
                    "has_secondary_decision": "verify_center_secondary_decision_conf"
                    in data
                    or "verify_center_secondary_decision_conf" in payload,
                    "has_verification_header": any(
                        str(key).lower() == "x-vc-bdturing-parameters"
                        for key in getattr(response, "headers", {})
                    ),
                    "endpoint": "get_qrcode"
                    if path.endswith("/get_qrcode")
                    else "check_qrconnect",
                    "http_status": int(response.status),
                    "error_code": data.get("error_code")
                    if type(data.get("error_code")) is int
                    else None,
                    "status": data.get("status")
                    if isinstance(data.get("status"), str)
                    and re.fullmatch(r"[A-Za-z_ -]{1,32}", data["status"])
                    else None,
                }
                self.network_receipts.append(receipt)
                del self.network_receipts[:-120]
                if receipt != self.latest_login_receipt:
                    self.latest_login_receipt = receipt
                    self.network_revision += 1
            except Exception:
                pass
            return
        if path != "/aweme/v1/web/user/profile/self":
            return
        try:
            payload = await asyncio.wait_for(response.json(), 3)
            identity = douyin_self_response_identity(response.url, payload)
            if identity:
                if self.identity and self.identity != identity:
                    self.identity_conflict = True
                self.identity, self.identity_source = identity, "douyin_self_response"
                self.identity_observed.set()
        except Exception:
            return

    async def navigate(self, entry: str):
        from scripts.crawler_account_login import load_request_scheduler

        gate = self.bound_proxy_session or load_request_scheduler().configured_gate()
        async with gate.slot("login_navigation", timeout=60):
            try:
                await self.page.goto(
                    ENTRY_URLS[entry], wait_until="domcontentloaded", timeout=45000
                )
            except Exception as exc:
                from playwright.async_api import TimeoutError

                if not isinstance(exc, TimeoutError):
                    raise

    def accept_qr_ticket(self, data, *, generation=None):
        if generation is not None and generation <= self.qr_accepted_generation:
            return False
        token = data.get("token")
        encoded = data.get("qrcode")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 4096
            or not isinstance(encoded, str)
            or len(encoded) > 131072
        ):
            return False
        try:
            image = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
            if not image or len(image) > 65536:
                return False
        except ValueError:
            return False
        self.qr_ticket = token
        self.qr_ticket_digest = hashlib.sha256(image).hexdigest()
        self.qr_platform_status = "new"
        self.current_qr_error = None
        if generation is not None:
            self.qr_accepted_generation = generation
        return True

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
            """es => es.map(e => ({href:e.href, text:(e.innerText||'').trim(), label:(e.getAttribute('aria-label')||'').trim(), title:(e.title||'').trim(), visible:!!(e.offsetWidth&&e.offsetHeight)}))"""
        )
        from backend.audit_agent.crawler_account_identity import (
            extract_platform_account_id,
        )

        candidates = {
            extract_platform_account_id("dy", a["href"])
            for a in anchors
            if a["visible"]
            and any(
                str(a.get(field) or "").strip() in {"我的主页", "个人主页", "我的抖音"}
                for field in ("text", "label", "title")
            )
        }
        candidates.discard("")
        if len(candidates) > 1:
            raise ValueError("IDENTITY_UNCONFIRMED")
        if len(candidates) == 1:
            self.identity = candidates.pop()
            self.identity_source = "personal_header_menu"
        return self.identity

    async def visible(self, selector):
        return await self.visible_match(self.page.locator(selector))

    async def visible_match(self, matches):
        locator = matches.first
        try:
            if await locator.is_visible(timeout=100):
                return locator
        except Exception:
            pass
        try:
            count = min(await matches.count(), 20)
        except Exception:
            return None
        for index in range(1, count):
            candidate = matches.nth(index)
            try:
                if await candidate.is_visible(timeout=100):
                    return candidate
            except Exception:
                continue
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
            self.qr_extraction_reason = "image_not_visible"
            return None
        self.last_qr_source_digest = ""
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
            elif png:
                self.last_qr_source_digest = hashlib.sha256(png).hexdigest()
            from scripts.crawler_account_login import is_qr_png

            if not is_qr_png(png):
                # Source decoding and rendering are separate: a loaded image
                # can still be detectable only at its actual rendered size.
                png = await locator.screenshot(type="png", timeout=2000)
                if not is_qr_png(png):
                    self.qr_extraction_reason = "qr_detection_failed"
                    return None
            digest = hashlib.sha256(png).hexdigest()
            self.last_qr_digest = digest
            self.qr_extraction_reason = "decoded"
            return {
                "image_data_url": "data:image/png;base64,"
                + base64.b64encode(png).decode("ascii"),
                "revision": digest,
                "state": "valid",
                "expires_at": None,
            }
        except Exception as exc:
            self.qr_extraction_reason = (
                "capture_timeout"
                if type(exc).__name__ == "TimeoutError"
                else "capture_failed"
            )
            return None

    async def sms_controls(self):
        panel = self.page.locator(PANEL_SELECTOR).first
        phone = panel.locator("input[placeholder*='手机号'], input[type='tel']").first
        code = panel.locator("input[placeholder*='验证码']").first
        request = panel.get_by_text(
            re.compile(r"^(获取验证码|重新获取|重新发送|\d+\s*[sS秒]).*$")
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
        controls = await self.sms_controls()
        request_text = ""
        request_enabled = False
        if controls and await controls["request"].is_visible():
            request_text = await controls["request"].inner_text()
            request_enabled = await controls["request"].is_enabled()
            disabled = await controls["request"].evaluate(
                """e => e.getAttribute('aria-disabled') === 'true' || e.hasAttribute('disabled') || /(?:^|[ _-])disabled(?:$|[ _-])/.test(e.className || '')"""
            )
            request_enabled = request_enabled and not disabled
        countdown = re.search(r"(\d{1,3})\s*(?:秒|[sS])", request_text)
        if countdown:
            seconds = int(countdown.group(1))
            self.sms_resend_seconds = seconds
            if seconds > 0:
                observed_deadline = datetime.now(timezone.utc) + timedelta(
                    seconds=seconds
                )
                # A repeated observation must not restart the cooldown. Platform
                # availability remains authoritative if the displayed timer stalls.
                if self.sms_resend_available_at is None:
                    self.sms_resend_available_at = observed_deadline
                else:
                    self.sms_resend_available_at = min(
                        self.sms_resend_available_at, observed_deadline
                    )
                self.sms_resend_allowed = False
            else:
                self.sms_resend_available_at = None
                self.sms_resend_allowed = request_enabled
        elif request_enabled and any(
            term in request_text for term in ("获取验证码", "重新获取", "重新发送")
        ):
            self.sms_resend_seconds = None
            self.sms_resend_available_at = None
            self.sms_resend_allowed = True
        else:
            self.sms_resend_allowed = False
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
            self.sms_request_observation_pending = False
        elif self.sms_result == "pending" or self.sms_request_observation_pending:
            if countdown and int(countdown.group(1)) > 0:
                self.sms_result = "confirmed"
                self.sms_request_observation_pending = False
        return self.sms_result

    async def observe(self):
        for attempt in range(3):
            try:
                return await self.observe_once()
            except Exception as exc:
                from playwright.async_api import Error, TimeoutError

                if not isinstance(exc, Error):
                    raise
                transient = isinstance(
                    exc, TimeoutError
                ) or "Execution context was destroyed" in str(exc)
                if (
                    not transient
                    or attempt == 2
                    or self.page.is_closed()
                    or not document_allowed(self.page.url, top_level=True)
                ):
                    raise
                await asyncio.sleep(0.2)

    async def observe_once(self):
        await self.page.evaluate(
            "document.querySelectorAll('video').forEach(v=>{v.pause();v.autoplay=false})"
        )
        region = await self.region(challenge_only=True)
        if region:
            if self.bound_proxy_session is not None:
                from tools.bound_proxy import emit_account_risk

                emit_account_risk(self.bound_proxy_session.snapshot)
            return {
                "phase": "challenge_required",
                "region": region,
                "region_signature": await self.region_signature(),
            }
        qr = await self.qr()
        panel = await self.visible(PANEL_SELECTOR)
        text = await panel.inner_text() if panel else ""
        # The QR can remain visible after a successful scan. Only the platform's
        # explicit confirmation instructions establish this transition.
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
        phone_confirmation = any(
            (
                re.fullmatch(
                    r"(?:扫码|扫描)成功[，,。！!\s]*(?:请在手机(?:上)?确认(?:登录)?[。！!]?)?",
                    line.strip(),
                )
                or re.fullmatch(r"请在手机(?:上)?确认(?:登录)?[。！!]?", line.strip())
            )
            for line in text.splitlines()
        )
        correlated = bool(
            self.qr_ticket_digest
            and self.last_qr_source_digest == self.qr_ticket_digest
        )
        network_progress = correlated and self.qr_platform_status in {
            "scanned",
            "confirmed",
        }
        if (
            required_sms
            or (self.sms_selected and not phone_confirmation and not network_progress)
        ) and await self.sms_controls():
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
                    "resend_available_at": self.sms_resend_available_at.isoformat()
                    if self.sms_resend_available_at
                    else None,
                    "resend_allowed": self.sms_resend_allowed,
                    "error": "平台未接受本次操作，请检查输入或稍后重试"
                    if self.sms_result == "rejected"
                    else None,
                    "source": "rendered_login_form",
                },
            }
        if correlated and self.current_qr_error is not None:
            region = await self.region()
            if region:
                return {
                    "phase": "manual_required",
                    "region": region,
                    "region_signature": await self.region_signature(),
                }
            # A historical QR error is not proof of rejection after the native
            # verification panel disappears. Preserve the same page for bounded
            # authentication/identity observation; cookies alone never finish it.
            return {"phase": "identity_check"}
        if correlated and self.qr_platform_status == "scanned":
            return {"phase": "phone_confirm"}
        if correlated and self.qr_platform_status == "confirmed":
            return {"phase": "identity_check"}
        if phone_confirmation:
            return {"phase": "phone_confirm"}
        if self.manual and panel:
            return {
                "phase": "manual_required",
                "region": await self.region(),
                "region_signature": await self.region_signature(),
            }
        if qr:
            self.last_qr = dict(qr)
        expired = (
            correlated and self.qr_platform_status in {"expired", "refused"}
        ) or any(
            term in text
            for term in ("二维码已失效", "二维码已过期", "二维码失效", "点击刷新")
        )
        if expired:
            # Douyin removes the image when it expires. Keep only an actually
            # observed QR for invalid display; a first-load failure invents no image.
            invalid_qr = {**self.last_qr, "state": "expired"} if self.last_qr else None
            return {"phase": "qr_expired", "qr": invalid_qr}
        if qr:
            return {"phase": "qr_ready", "qr": qr}
        if panel:
            region = await self.region()
            if region:
                return {
                    "phase": "manual_required",
                    "region": region,
                    "region_signature": await self.region_signature(),
                }
        return {"phase": "preparing"}

    async def click_text(self, texts):
        panel = self.page.locator(PANEL_SELECTOR).first
        for text in texts:
            locator = panel.get_by_text(text, exact=True).first
            if await locator.is_visible():
                await locator.click(timeout=2000)
                return
        raise ValueError("PLATFORM_CONTROL_UNAVAILABLE")

    async def qr_refresh_control(self):
        panel = self.page.locator(PANEL_SELECTOR).first
        for text in ("刷新二维码", "点击刷新", "刷新"):
            locator = panel.get_by_text(text, exact=True).first
            if await locator.is_visible() and await locator.is_enabled():
                return locator
        return None

    async def open_login(self):
        from scripts.crawler_account_login import PLATFORMS
        from playwright.async_api import TimeoutError

        try:
            configured = float(
                os.getenv(
                    "CRAWLER_LOGIN_V2_PREPARATION_TIMEOUT_SECONDS",
                    str(LOGIN_ENTRY_READY_SECONDS),
                )
            )
        except ValueError:
            raise ValueError("LOGIN_PREPARATION_TIMEOUT_INVALID") from None
        if not math.isfinite(configured):
            raise ValueError("LOGIN_PREPARATION_TIMEOUT_INVALID")
        budget = min(180, max(15, configured))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        max_candidates = 0
        click_attempts = 0
        click_successes = 0
        click_timeouts = 0

        async def opened():
            return bool(
                self.qr_ticket
                or await self.visible(PANEL_SELECTOR)
                or await self.visible(QR_SELECTOR)
            )

        if await opened():
            return
        wait_for_load_state = getattr(self.page, "wait_for_load_state", None)
        if callable(wait_for_load_state):
            try:
                await wait_for_load_state(
                    "domcontentloaded",
                    timeout=min(LOGIN_DOCUMENT_READY_SECONDS, budget) * 1000,
                )
            except TimeoutError:
                # DOM readiness and hydration share this one finite budget.
                pass

        async def candidates():
            values = []
            for selector in PLATFORMS["dy"]["login_triggers"]:
                trigger = await self.visible(selector)
                if trigger is not None:
                    values.append(trigger)
            if document_allowed(self.page.url, top_level=True):
                semantic_matches = []
                get_by_role = getattr(self.page, "get_by_role", None)
                get_by_text = getattr(self.page, "get_by_text", None)
                if callable(get_by_role):
                    semantic_matches.append(
                        get_by_role("button", name="登录", exact=True)
                    )
                if callable(get_by_text):
                    semantic_matches.append(get_by_text("登录", exact=True))
                for matches in semantic_matches:
                    count = min(await matches.count(), 20)
                    for index in range(count):
                        trigger = matches.nth(index)
                        try:
                            if await trigger.is_visible(timeout=100):
                                values.append(trigger)
                        except Exception:
                            continue
            return values

        while True:
            if await opened():
                return
            visible_candidates = await candidates()
            max_candidates = max(max_candidates, len(visible_candidates))
            for trigger in visible_candidates:
                if loop.time() >= deadline:
                    break
                click_attempts += 1
                try:
                    await trigger.click(
                        timeout=min(2000, max(1, (deadline - loop.time()) * 1000))
                    )
                    click_successes += 1
                except TimeoutError:
                    click_timeouts += 1
                    if self.page.is_closed() or not document_allowed(
                        self.page.url, top_level=True
                    ):
                        raise
                    panel_present = await self.visible(PANEL_SELECTOR) is not None
                    qr_present = bool(self.qr_ticket or await self.visible(QR_SELECTOR))
                    self.login_entry_diagnostic = {
                        "method": "click",
                        "timeout_ms": 2000,
                        "panel_present": panel_present,
                        "qr_present": qr_present,
                    }
                    if not (panel_present or qr_present):
                        continue
                postcondition_deadline = min(deadline, loop.time() + 0.2)
                while loop.time() < postcondition_deadline:
                    if await opened():
                        return
                    await asyncio.sleep(0.1)
            if loop.time() >= deadline:
                try:
                    ready_state = await self.page.evaluate("document.readyState")
                except Exception:
                    ready_state = "unknown"
                if ready_state not in {"loading", "interactive", "complete"}:
                    ready_state = "unknown"
                self.login_entry_diagnostic = {
                    "method": "click",
                    "timeout_ms": 2000,
                    "panel_present": False,
                    "qr_present": False,
                    "candidate_count": max_candidates,
                    "click_attempts": click_attempts,
                    "click_successes": click_successes,
                    "click_timeouts": click_timeouts,
                    "ready_state": ready_state,
                }
                raise ValueError("LOGIN_PREPARATION_FAILED")
            await asyncio.sleep(min(0.1, max(0, deadline - loop.time())))

    async def close(self):
        self._closed = True
        for event, callback in (
            ("page", self._new_page),
            ("response", self._response),
            ("requestfailed", self._request_failed),
            ("request", self._request),
            ("requestfinished", self._request_finished),
            ("dialog", self._dialog),
        ):
            try:
                self.context.remove_listener(event, callback)
            except Exception:
                # Closing an already disconnected context must preserve the route failure.
                pass
        if self._guard_installed:
            await self.context.unroute("**/*", self._guard_request)
            self._guard_installed = False
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
