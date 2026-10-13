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


@pytest.mark.parametrize("role", [True, False])
def test_real_semantic_login_entry_waits_for_hydration_and_opens_qr(tmp_path, role):
    import cv2
    from backend.audit_agent.crawler_browser import load_account_browser
    from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter

    qr = cv2.QRCodeEncoder_create().encode("https://fixture.invalid/login-entry")
    qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    qr = cv2.resize(qr, (180, 180), interpolation=cv2.INTER_NEAREST)
    _, png = cv2.imencode(".png", qr)
    data = base64.b64encode(png).decode()
    attribute = 'role="button"' if role else ""
    html = f"""<div style="display:none">登录</div>
    <div role="button" style="width:60px;height:30px" onclick="window.decoyClicked=true">登录</div>
    <div id="header" {attribute} style="display:none" onclick="panel.style.display='block';window.clicked=true"><span>登录</span></div>
    <div id="panel" class="douyin_login_new_class" style="display:none;width:400px;height:300px">
    <div id="animate_qrcode_container"><img src="data:image/png;base64,{data}"></div></div>
    <script>setTimeout(()=>header.style.display='block',250)</script>"""

    async def run():
        browser = load_account_browser()
        context, _ = await browser.launch_account_context(
            "login-entry-fixture", tmp_path, headless=True
        )
        try:
            page = await context.new_page()

            async def fixture(route):
                if route.request.url == "https://www.douyin.com/login-entry-fixture":
                    await route.fulfill(body=html, content_type="text/html; charset=utf-8")
                else:
                    await route.abort()

            await context.route("**/*", fixture)
            await page.goto("https://www.douyin.com/login-entry-fixture")
            adapter = DouyinLoginAdapter(context, page)
            await adapter.open_login()
            assert await page.evaluate("window.decoyClicked") is True
            assert await page.evaluate("window.clicked") is True
            assert (await adapter.observe())["phase"] == "qr_ready"
            await page.evaluate("window.clicked=false")
            await adapter.open_login()
            assert await page.evaluate("window.clicked") is False
        finally:
            await context.close()

    asyncio.run(run())


def test_real_capture_preserves_native_text_caret(tmp_path):
    import cv2
    import numpy as np
    from backend.audit_agent.crawler_browser import load_account_browser
    from scripts.crawler_douyin_login_adapter import Region
    from scripts.crawler_douyin_login_v2 import DouyinLoginV2

    def caret_pixels(jpeg):
        pixels = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        blue, green, red = cv2.split(pixels.astype(np.int16))
        # JPEG changes a thin caret's brightness; its red chroma still separates
        # it from the black text, white background and blue focus border.
        return np.count_nonzero((red - green > 45) & (red - blue > 15))

    async def run():
        browser = load_account_browser()
        context, _ = await browser.launch_account_context(
            "caret-fixture", tmp_path, headless=True
        )
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(operation_id="caret-fixture", media_fd=left.detach())
        )
        original_media = helper.media
        frames = []
        try:
            page = await context.new_page()
            await page.bring_to_front()
            await page.set_viewport_size({"width": 500, "height": 200})
            html = (
                '<style>body{margin:0;background:white}input{position:absolute;'
                'left:30px;top:30px;width:230px;height:50px;font-size:24px;'
                'color:black;background:white;caret-color:#e00066}</style>'
                '<input aria-label="fixture input" value="abcd">'
            )
            await page.route(
                "https://www.douyin.com/caret-fixture",
                lambda route: route.fulfill(body=html, content_type="text/html"),
            )
            await page.goto(
                "https://www.douyin.com/caret-fixture",
                wait_until="domcontentloaded",
                timeout=5000,
            )
            field = page.locator("input")
            await field.click()
            await field.press("End")
            hidden = await page.screenshot(type="jpeg")
            assert caret_pixels(hidden) == 0
            await field.press("ArrowLeft")
            await field.press("ArrowRight")

            class Media:
                async def send(self, jpeg, metadata, **kwargs):
                    frames.append(jpeg)
                    if caret_pixels(jpeg) or len(frames) >= 4:
                        helper.stopped.set()
                    else:
                        await field.press("ArrowLeft")
                        await field.press("ArrowRight")

            helper.adapter = SimpleNamespace(page=page)
            helper.region = Region(20, 20, 280, 80)
            helper.epoch = 1
            helper.controller_attached = True
            helper.capture_enabled = True
            helper.phase = "manual_required"
            helper.media = Media()
            await asyncio.wait_for(helper.capture_frames(), timeout=5)
            assert any(caret_pixels(frame) > 0 for frame in frames), {
                "frame_count": len(frames),
                "document_focused": await page.evaluate("document.hasFocus()"),
                "caret_color": await field.evaluate("e=>getComputedStyle(e).caretColor"),
            }
            assert await field.evaluate("e=>document.activeElement===e")
            assert await field.input_value() == "abcd"
        finally:
            original_media.close()
            right.close()
            await context.close()

    asyncio.run(run())


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


def test_real_nested_iframe_edit_target_focus_unicode_clear_and_detachment(
    tmp_path, monkeypatch
):
    from backend.audit_agent.crawler_browser import load_account_browser
    from scripts.crawler_douyin_login_adapter import Region
    from scripts import crawler_login_editables as module

    async def run():
        context, _ = await load_account_browser().launch_account_context(
            "editablefixture", tmp_path, headless=True
        )
        targets = module.EditableTargets()
        monkeypatch.setattr(module, "document_allowed", lambda *args, **kwargs: True)
        try:
            page = await context.new_page()
            await page.set_viewport_size({"width": 1000, "height": 760})
            await page.goto("about:blank")
            await page.goto(
                "data:text/html;charset=utf-8,"
                + __import__("urllib.parse", fromlist=["quote"]).quote(
                    '<input id="outside" style="position:absolute;left:800px"><iframe style="position:absolute;left:100px;top:100px;width:450px;height:300px" srcdoc="&lt;iframe style=\'width:400px;height:200px\' srcdoc=\'&amp;lt;input id=&amp;quot;code&amp;quot; maxlength=&amp;quot;256&amp;quot; style=&amp;quot;width:220px;height:30px&amp;quot;&amp;gt;\'&gt;&lt;/iframe&gt;"></iframe>',
                ),
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(100)
            region = Region(90, 90, 480, 330)
            fields = await targets.inventory(page, region)
            assert len(fields) == 1 and not fields[0]["focused"]
            field_id = fields[0]["field_id"]
            handle = targets.nodes[field_id]
            await handle.click()
            fields = await targets.inventory(page, region)
            assert fields[0]["focused"]
            command = {
                "field_id": field_id,
                "client_edit_revision": 1,
                "value": "中😀文",
                "selection_start": 1,
                "selection_end": 3,
                "selection_direction": "forward",
            }
            assert await targets.edit(command, page, region) == {
                "field_id": field_id,
                "client_edit_revision": 1,
            }
            assert await handle.evaluate(
                "e=>({v:e.value,start:e.selectionStart,end:e.selectionEnd})"
            ) == {"v": "中😀文", "start": 1, "end": 3}
            with pytest.raises(ValueError, match="EDIT_REVISION_STALE"):
                await targets.edit(command, page, region)
            command.update(
                client_edit_revision=2, value="", selection_start=0, selection_end=0
            )
            await targets.edit(command, page, region)
            assert await handle.input_value() == ""
            from scripts.crawler_douyin_login_v2 import DouyinLoginV2

            left, right = socket.socketpair()
            helper = DouyinLoginV2(
                SimpleNamespace(operation_id="key-fixture", media_fd=left.detach())
            )
            helper.adapter = SimpleNamespace(page=page)
            helper.region = region
            await handle.evaluate(
                "e=>e.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();e.dataset.enter='yes'}})"
            )
            try:
                key_targets = await helper.editables.inventory(page, region)
                helper.editable_scope = (
                    helper.epoch,
                    helper.challenge_id,
                    helper.area_version,
                )
                key_field = next(
                    item["field_id"] for item in key_targets if item["focused"]
                )
                await helper.apply_command(
                    {"type": "key", "key": "Enter", "field_id": key_field}
                )
                assert await handle.get_attribute("data-enter") == "yes"
                await handle.evaluate(
                    "e=>{let next=document.createElement('input');next.id='next';document.body.append(next);next.focus()}"
                )
                with pytest.raises(ValueError, match="INPUT_OUTSIDE_AREA"):
                    await helper.apply_command(
                        {"type": "key", "key": "Enter", "field_id": key_field}
                    )
            finally:
                await helper.editables.clear()
                helper.media.close()
                right.close()
            await page.locator("#outside").click()
            with pytest.raises(ValueError, match="EDIT_TARGET_CHANGED"):
                await targets.edit({**command, "client_edit_revision": 3}, page, region)
            await handle.evaluate(
                "e=>{document.querySelector('#next')?.remove();e.remove()}"
            )
            assert not await targets.inventory(page, region)
        finally:
            await targets.clear()
            await context.close()

    asyncio.run(run())


def test_real_shadow_edit_inventory_excludes_sensitive_disabled_and_outside(
    tmp_path, monkeypatch
):
    from backend.audit_agent.crawler_browser import load_account_browser
    from scripts.crawler_douyin_login_adapter import Region
    from scripts.crawler_login_editables import EditableTargets

    async def run():
        context, _ = await load_account_browser().launch_account_context(
            "shadowfixture", tmp_path, headless=True
        )
        targets = EditableTargets()
        try:
            page = await context.new_page()
            await page.goto(
                "data:text/html,<div id='host'></div><input id='out' style='position:absolute;left:800px'>"
            )
            await page.evaluate(
                """()=>{host.attachShadow({mode:'open'}).innerHTML='<textarea id="valid" style="width:220px;height:90px"></textarea><input readonly value="readonly"><input disabled><input type="password" value="secret"><input type="file">'}"""
            )
            area = Region(0, 0, 700, 500)
            fields = await targets.inventory(page, area)
            assert len(fields) == 1 and fields[0]["input_type"] == "textarea"
            assert "secret" not in str(fields)
            target = targets.nodes[fields[0]["field_id"]]
            await target.click()
            assert (await targets.inventory(page, area))[0]["focused"]
            await targets.edit(
                {
                    "field_id": fields[0]["field_id"],
                    "client_edit_revision": 1,
                    "value": "one\ntwo",
                    "selection_start": 7,
                    "selection_end": 7,
                },
                page,
                area,
            )
            await target.evaluate("e=>e.readOnly=true")
            with pytest.raises(ValueError, match="EDIT_TARGET_CHANGED"):
                await targets.edit(
                    {
                        "field_id": fields[0]["field_id"],
                        "client_edit_revision": 2,
                        "value": "x",
                        "selection_start": 1,
                        "selection_end": 1,
                    },
                    page,
                    area,
                )
            assert not await targets.inventory(page, area)
        finally:
            await targets.clear()
            await context.close()

    asyncio.run(run())


def test_real_edit_inventory_multiple_frames_have_independent_node_ids(
    tmp_path, monkeypatch
):
    from backend.audit_agent.crawler_browser import load_account_browser
    from scripts.crawler_douyin_login_adapter import Region
    from scripts import crawler_login_editables as module

    async def run():
        context, _ = await load_account_browser().launch_account_context(
            "framesfixture", tmp_path, headless=True
        )
        targets = module.EditableTargets()
        monkeypatch.setattr(module, "document_allowed", lambda *a, **kw: True)
        try:
            page = await context.new_page()
            html = '<input id="main"><iframe srcdoc="&lt;input id=first&gt;"></iframe><iframe srcdoc="&lt;input id=second&gt;"></iframe>'
            await page.goto(
                "data:text/html,"
                + __import__("urllib.parse", fromlist=["quote"]).quote(html)
            )
            fields = await targets.inventory(page, Region(0, 0, 1000, 760))
            assert len(fields) == 3 and len({f["field_id"] for f in fields}) == 3
            again = await targets.inventory(page, Region(0, 0, 1000, 760))
            assert [f["field_id"] for f in again] == [f["field_id"] for f in fields]
            await targets.clear()
            await page.evaluate("document.body.innerHTML='<input>'.repeat(8)")
            sample = await page.locator("input").first.element_handle()
            handle_type = type(sample)
            original_dispose = handle_type.dispose
            disposed = []

            async def record_dispose(handle):
                disposed.append(handle)
                await original_dispose(handle)

            monkeypatch.setattr(handle_type, "dispose", record_dispose)
            for _ in range(3):
                assert len(await targets.inventory(page, Region(0, 0, 1000, 760))) == 4
            assert len(disposed) >= 20

        finally:
            await targets.clear()
            await context.close()

    asyncio.run(run())


def test_real_covered_background_fields_are_not_exposed_editables(
    tmp_path, monkeypatch
):
    from backend.audit_agent.crawler_browser import load_account_browser
    from scripts.crawler_douyin_login_adapter import Region
    from scripts import crawler_login_editables as module

    async def run():
        context, _ = await load_account_browser().launch_account_context(
            "coveredfixture", tmp_path, headless=True
        )
        targets = module.EditableTargets()
        monkeypatch.setattr(module, "document_allowed", lambda *a, **kw: True)
        try:
            page = await context.new_page()
            html = '<input id="background" style="position:absolute;left:100px;top:100px;width:220px;height:30px"><input style="opacity:0;position:absolute;left:100px;top:160px"><iframe id="verification" style="position:absolute;left:80px;top:80px;width:400px;height:300px;background:white" srcdoc="&lt;h2&gt;Choose SMS verification&lt;/h2&gt;"></iframe>'
            await page.goto(
                "data:text/html,"
                + __import__("urllib.parse", fromlist=["quote"]).quote(html)
            )
            area = Region(0, 0, 700, 600)
            assert await targets.inventory(page, area) == []
            await page.locator("#verification").evaluate(
                "e=>e.srcdoc='<input id=real style=\"width:220px;height:30px\">'"
            )
            await page.wait_for_timeout(50)
            fields = await targets.inventory(page, area)
            assert len(fields) == 1
            assert (
                await targets.nodes[fields[0]["field_id"]].get_attribute("id") == "real"
            )
            await page.locator("#verification").evaluate("e=>e.style.opacity='0'")
            assert await targets.inventory(page, area) == []
        finally:
            await targets.clear()
            await context.close()

    asyncio.run(run())
