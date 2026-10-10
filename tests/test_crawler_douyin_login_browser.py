"""Real pinned CloakBrowser fixture; opt in with RUN_CLOAK_LOGIN_TESTS=1."""

import asyncio
import base64
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CLOAK_LOGIN_TESTS") != "1",
    reason="requires installed pinned CloakBrowser",
)


def test_real_browser_qr_sms_graphics_reopen_and_media(tmp_path, monkeypatch):
    import cv2
    from backend.audit_agent.crawler_browser import load_account_browser
    from backend.audit_agent.crawler_login_protocol import read_packet
    from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter
    from scripts.crawler_douyin_login_v2 import DouyinLoginV2
    from scripts.crawler_login_pages import reset_douyin_credentials_v2

    qr = cv2.QRCodeEncoder_create().encode("https://fixture.invalid/qr-login")
    qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    qr = cv2.resize(qr, (180, 180), interpolation=cv2.INTER_NEAREST)
    _, png = cv2.imencode(".png", qr)
    data = base64.b64encode(png).decode()
    html = f"""<style>.douyin_login_new_class {{position:absolute;left:100px;top:100px;width:500px;height:400px;background:white}} #captcha_container {{position:absolute;left:150px;top:180px;width:300px;height:180px;display:none;background:#ddd}}</style>
    <div class="douyin_login_new_class"><div id="animate_qrcode_container"><img src="data:image/png;base64,{data}"></div>
    <span>验证码登录</span><input placeholder="请输入手机号" type="tel"><input placeholder="验证码" maxlength="6">
    <button onclick="window.smsSent=true">获取验证码</button><button onclick="localStorage.setItem('fixtureLogin','saved');window.codeSubmitted=true">登录</button></div>
    <div id="captcha_container"><p>请人工画图或拖动滑块</p><input type="range" id="slider" style="width:260px"><canvas id="drawing" width="280" height="80"></canvas></div>
    <script>window.points=[];drawing.onpointerdown=e=>{{drawing.setPointerCapture(e.pointerId);window.drawingActive=true}};drawing.onpointermove=e=>{{if(window.drawingActive)points.push([e.offsetX,e.offsetY])}};drawing.onpointerup=e=>window.drawingActive=false;</script>"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    async def run():
        browser = load_account_browser()
        c, profile = await browser.launch_account_context(
            "fixture", tmp_path, headless=True
        )
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(operation_id="op", media_fd=left.detach())
        )
        try:
            page = await c.new_page()
            await page.set_viewport_size({"width": 1000, "height": 760})
            await page.goto(origin)
            adapter = await DouyinLoginAdapter(c, page).install()
            from scripts import crawler_douyin_login_adapter as adapter_module

            original_allowed = adapter_module.document_allowed
            monkeypatch.setattr(
                adapter_module,
                "document_allowed",
                lambda url, top_level: url.startswith(origin)
                or original_allowed(url, top_level=top_level),
            )
            original_observe = adapter.observe_once
            attempts = []

            async def transient_panel_observation():
                attempts.append(True)
                if len(attempts) == 1:
                    await page.locator(".douyin_login_new_class").evaluate(
                        "e=>e.id='replacement-panel'"
                    )
                    await page.locator("#missing-old-panel").inner_text(timeout=20)
                return await original_observe()

            adapter.observe_once = transient_panel_observation
            helper.adapter = adapter
            observation = await adapter.observe()
            assert len(attempts) == 2
            adapter.observe_once = original_observe
            assert (
                observation["phase"] == "qr_ready"
                and observation["qr"]["image_data_url"]
            )
            await page.locator(".douyin_login_new_class").evaluate(
                "e=>{let p=document.createElement('p');p.id='scan-state';p.innerText='扫码后请在手机上确认登录';e.append(p)}"
            )
            assert (await adapter.observe())["phase"] == "qr_ready"
            await page.locator("#scan-state").evaluate(
                "e=>e.innerText='扫码成功\\n请在手机上确认登录'"
            )
            assert (await adapter.observe())["phase"] == "phone_confirm"
            assert await page.locator("#animate_qrcode_container img").is_visible()
            await page.locator("#scan-state").evaluate(
                "e=>e.innerText='请完成当前安全验证'"
            )
            await page.locator("#animate_qrcode_container").evaluate(
                "e=>e.style.display='none'"
            )
            unknown = await adapter.observe()
            assert (
                unknown["phase"] == "manual_required" and unknown["region"].width < 1000
            )
            await page.locator("#scan-state").evaluate("e=>e.remove()")
            adapter.sms_selected = True
            assert (await adapter.observe())["phase"] == "sms_required"
            await helper.apply_command(
                {
                    "command_id": "request",
                    "type": "action",
                    "action": "request_sms",
                    "payload": {"phone": "13800000000"},
                }
            )
            assert await page.evaluate("window.smsSent")
            await helper.apply_command(
                {
                    "command_id": "submit",
                    "type": "action",
                    "action": "submit_sms",
                    "payload": {"code": "123456"},
                }
            )
            assert await page.evaluate("window.codeSubmitted")
            await page.locator("#captcha_container").evaluate(
                "e=>e.style.display='block'"
            )
            obs = await adapter.observe()
            assert obs["phase"] == "challenge_required"
            helper.region = obs["region"]
            helper.epoch = 1
            slider_box = await page.locator("#slider").bounding_box()
            slider_y = (
                slider_box["y"] + slider_box["height"] / 2 - helper.region.y
            ) / helper.region.height
            await helper.apply_command(
                {
                    "type": "pointer_batch",
                    "controller_epoch": 1,
                    "points": [
                        {"x": 0.05, "y": slider_y, "event": "down", "dt_ms": 0},
                        {"x": 0.9, "y": slider_y, "event": "move", "dt_ms": 0},
                        {"x": 0.9, "y": slider_y, "event": "up", "dt_ms": 0},
                    ],
                }
            )
            assert int(await page.locator("#slider").input_value()) > 80
            await helper.apply_command(
                {
                    "type": "pointer_batch",
                    "controller_epoch": 1,
                    "points": [
                        {"x": 0.1, "y": 0.65, "event": "down", "dt_ms": 0},
                        {"x": 0.3, "y": 0.7, "event": "move", "dt_ms": 0},
                        {"x": 0.6, "y": 0.8, "event": "move", "dt_ms": 0},
                        {"x": 0.8, "y": 0.7, "event": "up", "dt_ms": 0},
                    ],
                }
            )
            assert await page.evaluate("points.length") >= 2
            jpeg = await page.screenshot(type="jpeg", clip=helper.region.clip())
            await helper.media.send(
                jpeg, {"codec": "jpeg", "operation_id": "op"}, area_version=2
            )
            packet = await asyncio.to_thread(read_packet, right)
            assert packet["payload"] == jpeg and packet["area_version"] == 2
            await helper.media.auth(
                {"auth_state": {"cookies": []}, "identity_verified": True}, "op"
            )
            candidate = await asyncio.to_thread(
                lambda: read_packet(right, allow_auth=True)
            )
            assert candidate["kind"] == 2
            await adapter.close()
            await c.close()
            c, reopened = await browser.launch_account_context(
                "fixture", tmp_path, headless=True
            )
            assert (
                profile["seed"] == reopened["seed"]
                and profile["version"] == reopened["version"]
            )
            page = await c.new_page()
            await page.goto(origin)
            assert (
                await page.evaluate("localStorage.getItem('fixtureLogin')") == "saved"
            )
            # Credential reset affects first-party only, retains unrelated local fixture storage.
            await c.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": "fixture",
                        "domain": ".douyin.com",
                        "path": "/",
                    }
                ]
            )
            await reset_douyin_credentials_v2(c, reopened)
            assert not any(
                k["name"] == "sessionid"
                for k in await c.cookies("https://www.douyin.com")
            )
            page = await c.new_page()
            await page.goto(origin)
            assert (
                await page.evaluate("localStorage.getItem('fixtureLogin')") == "saved"
            )
        finally:
            helper.media.close()
            right.close()
            await c.close()

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()


def test_cdp_document_guard_keeps_warmed_static_cache(tmp_path, monkeypatch):
    from backend.audit_agent.crawler_browser import load_account_browser
    from scripts import crawler_douyin_login_adapter as module

    hits = {"script": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            if self.path == "/asset.js":
                hits["script"] += 1
                self.send_header("Cache-Control", "max-age=3600")
                self.send_header("Content-Type", "application/javascript")
                body = b"window.fixtureAsset=true"
            else:
                self.send_header("Content-Type", "text/html; charset=utf-8")
                body = b'<script src="/asset.js"></script><p>cache fixture</p>'
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    original = module.document_allowed
    monkeypatch.setattr(
        module,
        "document_allowed",
        lambda url, top_level: url.startswith(origin + "/")
        or original(url, top_level=top_level),
    )

    async def run():
        context, _ = await load_account_browser().launch_account_context(
            "cachefixture", tmp_path, headless=True
        )
        try:
            page = await context.new_page()
            adapter = await module.DouyinLoginAdapter(context, page).install()
            await page.goto(origin + "/first", wait_until="load")
            assert await page.evaluate("window.fixtureAsset")
            await page.goto(origin + "/second", wait_until="load")
            assert await page.evaluate("window.fixtureAsset")
            assert hits["script"] == 1
            await adapter.close()
        finally:
            await context.close()

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
