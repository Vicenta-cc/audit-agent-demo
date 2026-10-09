"""Account-bound QR-first login helper with conditional manual verification."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import sys
import time
import uuid

from backend.audit_agent.crawler_login_protocol import validate_command
from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter
from scripts.crawler_login_media import LoginMediaWriter
from scripts.crawler_login_pages import reset_douyin_credentials_v2

MANUAL_ACTIONS = ["pointer_batch", "key", "text", "scroll", "abort_gesture"]


class DouyinLoginV2:
    def __init__(self, args):
        from backend.audit_agent.config import settings

        self.args = args
        self.operation_id = args.operation_id
        self.media = LoginMediaWriter(args.media_fd)
        self.context = None
        self.adapter = None
        self.profile = None
        self.phase = "checking"
        self.available_actions = []
        self.epoch = 0
        self.controller_attached = False
        self.last_sequence = 0
        self.applied_sequence = 0
        self.region = None
        self.region_signature = ""
        self.area_version = 0
        self.challenge_id = ""
        self.pointer_down = False
        self.gesture_id = ""
        self.last_point_sequence = -1
        self.last_activity = 0.0
        self.handshakes = {
            key: asyncio.Event()
            for key in (
                "reset_ready",
                "guard_ready",
                "prepare_ready",
                "save_committed",
                "cleanup_ready",
            )
        }
        self.stopped = asyncio.Event()
        self.commands = asyncio.Queue(maxsize=32)
        self.results = {}
        self.pending_sms = {}
        self.last_snapshot = None
        self.credentials_changed = False
        self.tasks = []
        self.capture_enabled = True
        self.committed = False
        self.active_fps = settings.crawler_login_v2_active_fps
        self.idle_fps = settings.crawler_login_v2_idle_fps
        self.jpeg_quality = settings.crawler_login_v2_jpeg_quality

    def emit(self, kind, **payload):
        print(
            json.dumps(
                {
                    "type": kind,
                    "protocol_version": 2,
                    "operation_id": self.operation_id,
                    **payload,
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ),
            flush=True,
        )

    async def handshake(self, request, reply):
        self.handshakes[reply].clear()
        self.emit(request)
        await asyncio.wait_for(self.handshakes[reply].wait(), 15)
        if self.stopped.is_set() and not (reply == "save_committed" and self.committed):
            raise ConnectionError("Parent disconnected")

    async def abort_gesture(self):
        if self.pointer_down and self.adapter:
            with suppress(Exception):
                await asyncio.wait_for(self.adapter.page.mouse.up(), 2)
        self.pointer_down = False
        self.gesture_id = ""
        self.last_point_sequence = -1

    async def read_controls(self):
        reader = asyncio.StreamReader(limit=65536)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        try:
            while line := await reader.readline():
                if len(line) > 65536:
                    raise ValueError("CONTROL_LINE_TOO_LARGE")
                message = json.loads(line)
                kind = message.get("type")
                if message.get("operation_id", self.operation_id) != self.operation_id:
                    raise ValueError("OPERATION_MISMATCH")
                if kind in self.handshakes:
                    self.handshakes[kind].set()
                    if kind == "save_committed":
                        self.committed = True
                elif kind == "controller_authorized":
                    await self.abort_gesture()
                    self.epoch = int(message["controller_epoch"])
                    self.controller_attached = bool(message.get("attached", True))
                    self.last_sequence = 0
                    while not self.commands.empty():
                        old = self.commands.get_nowait()
                        self.result(
                            old, "rejected", "not_applicable", "CONTROLLER_REPLACED"
                        )
                    self.emit("controller_ready", controller_epoch=self.epoch)
                elif kind == "command":
                    command = validate_command(message["command"])
                    try:
                        self.commands.put_nowait(command)
                    except asyncio.QueueFull:
                        self.result(
                            command, "rejected", "not_applicable", "INPUT_BACKPRESSURE"
                        )
                elif kind == "abort_gesture":
                    await self.abort_gesture()
                elif kind == "shutdown":
                    self.stopped.set()
                    break
                elif kind == "visibility":
                    self.capture_enabled = bool(message.get("visible"))
                elif kind == "heartbeat":
                    pass
                else:
                    raise ValueError("UNKNOWN_CONTROL_MESSAGE")
        except (ValueError, UnicodeError, ConnectionError):
            self.stopped.set()
        finally:
            await self.abort_gesture()
            self.stopped.set()
            for event in self.handshakes.values():
                event.set()
            transport.close()

    def result(self, command, execution_status, platform_status="unknown", error=None):
        payload = {
            "command_id": command["command_id"],
            "execution_status": execution_status,
            "platform_status": platform_status,
        }
        if error:
            payload["error_code"] = error
        self.results[command["command_id"]] = payload
        # Browser helper dedup is bounded; durable sensitive dedup lives in parent SQLite.
        if len(self.results) > 128:
            self.results.pop(next(iter(self.results)))
        self.emit("command_result", **payload)

    def check_command(self, command):
        if (
            command["operation_id"] != self.operation_id
            or command["controller_epoch"] != self.epoch
            or self.epoch == 0
        ):
            raise ValueError("CONTROLLER_REPLACED")
        capability = (
            command.get("action") if command["type"] == "action" else command["type"]
        )
        if capability not in self.available_actions:
            raise ValueError("ACTION_NOT_AVAILABLE")
        if command["type"] != "action" or "challenge_id" in command:
            if (
                not self.region
                or command.get("challenge_id") != self.challenge_id
                or command.get("area_version") != self.area_version
            ):
                raise ValueError("STALE_AREA")
        if command["sequence"] <= self.last_sequence:
            raise ValueError("STALE_SEQUENCE")

    async def execute_commands(self):
        while not self.stopped.is_set():
            command = await self.commands.get()
            if command["command_id"] in self.results:
                self.emit("command_result", **self.results[command["command_id"]])
                continue
            try:
                self.check_command(command)
            except ValueError as exc:
                self.result(command, "rejected", "not_applicable", str(exc))
                continue
            self.last_sequence = command["sequence"]
            self.emit("command_started", command_id=command["command_id"])
            try:
                await asyncio.wait_for(self.apply_command(command), 8)
                self.applied_sequence = command["sequence"]
                self.last_activity = time.monotonic()
                self.result(
                    command,
                    "executed",
                    "pending"
                    if command.get("action") in {"request_sms", "submit_sms"}
                    else "unknown",
                )
            except Exception as exc:
                await self.abort_gesture()
                # Never include Playwright exception text: it can contain entered phone/code.
                self.result(
                    command,
                    "failed",
                    "unknown",
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "BROWSER_ACTION_FAILED",
                )

    async def apply_command(self, command):
        page = self.adapter.page
        kind = command["type"]
        if kind == "action":
            action = command["action"]
            if action == "open_manual":
                self.adapter.manual = True
            elif action == "close_manual":
                self.adapter.manual = False
            elif action == "switch_to_sms":
                await self.adapter.click_text(("验证码登录",))
                self.adapter.sms_selected = True
            elif action == "refresh_qr":
                await self.adapter.click_text(("刷新二维码", "点击刷新", "刷新"))
            elif action in {"request_sms", "submit_sms"}:
                controls = await self.adapter.sms_controls()
                if not controls:
                    raise ValueError("SMS_FORM_UNAVAILABLE")
                payload = command.get("payload", {})
                if action == "request_sms":
                    if await controls["phone"].is_visible():
                        await controls["phone"].fill(payload.get("phone", ""))
                    await controls["request"].click(timeout=2000)
                else:
                    await controls["code"].fill(payload["code"])
                    await controls["submit"].click(timeout=2000)
                self.adapter.sms_result = "pending"
                self.pending_sms[command["command_id"]] = {
                    "command": command,
                    "action": action,
                    "started": time.monotonic(),
                }
            elif action == "refresh_challenge":
                await self.abort_gesture()
                # Only explicit refresh controls inside the currently selected panel.
                await self.adapter.click_text(("刷新", "换一张"))
                self.area_version += 1
                self.challenge_id = uuid.uuid4().hex
            elif action == "abort_gesture":
                await self.abort_gesture()
            return
        if kind == "abort_gesture":
            await self.abort_gesture()
        elif kind == "pointer_batch":
            gesture_id = command.get("gesture_id", "")
            if self.gesture_id and gesture_id != self.gesture_id:
                await self.abort_gesture()
            if self.gesture_id != gesture_id:
                self.gesture_id, self.last_point_sequence = gesture_id, -1
            for point in command["points"]:
                # Authorization can change while a trajectory is in flight.
                if command["controller_epoch"] != self.epoch:
                    raise ValueError("CONTROLLER_REPLACED")
                if command.get("area_version", self.area_version) != self.area_version:
                    raise ValueError("STALE_AREA")
                point_sequence = point.get(
                    "point_sequence", self.last_point_sequence + 1
                )
                if point_sequence <= self.last_point_sequence:
                    raise ValueError("STALE_POINT")
                self.last_point_sequence = point_sequence
                x, y = self.region.point(point["x"], point["y"])
                await page.mouse.move(x, y)
                if point["event"] == "down":
                    await page.mouse.down()
                    self.pointer_down = True
                elif point["event"] == "up":
                    await page.mouse.up()
                    self.pointer_down = False
                if point.get("dt_ms"):
                    await asyncio.sleep(min(point["dt_ms"], 50) / 1000)
        elif kind == "scroll":
            await self.abort_gesture()
            await page.mouse.move(*self.region.point(command["x"], command["y"]))
            await page.mouse.wheel(command.get("delta_x", 0), command["delta_y"])
            self.area_version += 1
        elif kind in {"key", "text"}:
            # Keyboard input must remain inside the current verification region.
            box = await page.evaluate(
                """() => {let e=document.activeElement;if(!e||!e.matches('input,textarea,[contenteditable=true]'))return null;let r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}}"""
            )
            r = self.region
            if (
                not box
                or box["x"] < r.x
                or box["y"] < r.y
                or box["x"] + box["w"] > r.x + r.width + 1
                or box["y"] + box["h"] > r.y + r.height + 1
            ):
                raise ValueError("INPUT_OUTSIDE_AREA")
            if kind == "text":
                await page.keyboard.insert_text(command["text"])
            else:
                await page.keyboard.press(command["key"])

    def publish(self, observation):
        observation = dict(observation)
        region = observation.pop("region", None)
        signature = observation.pop("region_signature", "")
        phase = observation["phase"]
        if region != self.region or signature != getattr(self, "region_signature", ""):
            self.region = region
            self.region_signature = signature
            self.area_version += 1
            self.challenge_id = uuid.uuid4().hex if region else ""
        actions = ["open_manual"]
        if phase in {"qr_ready", "qr_expired"}:
            actions += ["refresh_qr", "switch_to_sms"]
        elif phase == "sms_required":
            actions += ["request_sms", "submit_sms"]
        elif phase in {"challenge_required", "manual_required"} and region:
            actions += MANUAL_ACTIONS + ["close_manual"]
        elif phase in {"identity_check", "profile_flush", "reuse_check", "db_commit"}:
            actions = []
        challenge = None
        if region:
            challenge = {
                "challenge_id": self.challenge_id,
                "area_version": self.area_version,
                "clip_rect": region.clip(),
                "viewport": self.adapter.page.viewport_size,
                "kind": "unknown",
                "interaction_mode": "draw",
                "image_width": round(region.width),
                "image_height": round(region.height),
                "allowed_actions": MANUAL_ACTIONS,
            }
        snapshot = {
            **observation,
            "available_actions": actions,
            "challenge": challenge,
            "qr": observation.get("qr"),
            "sms": observation.get("sms"),
            "identity": {
                "status": "verified"
                if self.adapter and self.adapter.identity
                else "pending"
            },
        }
        self.phase, self.available_actions = phase, actions
        if snapshot != self.last_snapshot:
            self.last_snapshot = snapshot
            self.emit("snapshot", **snapshot)

    def capture_period(self):
        fps = (
            self.active_fps
            if time.monotonic() - self.last_activity < 2
            else self.idle_fps
        )
        return 1 / fps

    async def capture_frames(self):
        previous = None
        consecutive_failures = 0
        while not self.stopped.is_set():
            if not (
                self.region
                and self.epoch
                and self.controller_attached
                and self.capture_enabled
                and self.phase in {"challenge_required", "manual_required"}
            ):
                await asyncio.sleep(0.1)
                continue
            started = time.monotonic()
            epoch, region, area = self.epoch, self.region, self.area_version
            try:
                jpeg = await self.adapter.page.screenshot(
                    type="jpeg",
                    quality=self.jpeg_quality,
                    clip=region.clip(),
                    timeout=2000,
                )
                consecutive_failures = 0
                digest = (epoch, area, hashlib.sha256(jpeg).digest())
                if (
                    epoch == self.epoch
                    and region == self.region
                    and area == self.area_version
                    and digest != previous
                ):
                    await self.media.send(
                        jpeg,
                        {
                            "operation_id": self.operation_id,
                            "codec": "jpeg",
                            "image_width": round(region.width),
                            "image_height": round(region.height),
                            "clip_rect": region.clip(),
                            "challenge_id": self.challenge_id,
                            "viewport": self.adapter.page.viewport_size,
                            "controller_epoch": epoch,
                            "applied_input_sequence": self.applied_sequence,
                        },
                        area_version=area,
                    )
                    previous = digest
            except (ConnectionError, OSError, asyncio.TimeoutError):
                self.emit(
                    "error",
                    code="MEDIA_CHANNEL_FAILED",
                    message="验证画面连接中断，请重新打开登录窗口",
                )
                self.stopped.set()
            except Exception:
                previous = None
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    self.emit(
                        "error",
                        code="CAPTURE_FAILED",
                        message="无法取得验证画面，请重新打开登录窗口",
                    )
                    self.stopped.set()
                    break
                # Navigation can replace the panel. Retry twice after observing
                # fresh geometry; never keep a permanently broken page spinning.
                self.emit("notice", code="CAPTURE_RETRY", message="验证页面正在更新")
                with suppress(Exception):
                    observation = await self.adapter.observe()
                    await self.abort_gesture()
                    self.publish(observation)
            period = self.capture_period()
            await asyncio.sleep(max(0, period - (time.monotonic() - started)))

    async def open_context(self):
        from scripts.crawler_account_login import load_account_browser, settings

        browser = load_account_browser()
        if settings.crawler_login_browser_version:
            browser.profile_for(
                self.args.account_id,
                settings.crawler_browser_profile_root,
                settings.crawler_login_browser_version,
            )
        self.context, self.profile = await browser.launch_account_context(
            self.args.account_id,
            settings.crawler_browser_profile_root,
            headless=not self.args.headed,
        )
        page = await self.context.new_page()
        await page.set_viewport_size({"width": 1000, "height": 760})
        page.set_default_timeout(2000)
        self.adapter = await DouyinLoginAdapter(
            self.context, page, block_media=getattr(self.args, "block_media", False)
        ).install()

    async def close_context(self):
        await self.abort_gesture()
        if self.adapter:
            await self.adapter.close()
            self.adapter = None
        if self.context:
            context, self.context = self.context, None
            await asyncio.wait_for(context.close(), 15)

    async def flow(self):
        await self.open_context()
        if self.args.intent == "reauth":
            await self.handshake("reset_requested", "reset_ready")
            await self.adapter.close()
            await reset_douyin_credentials_v2(self.context, self.profile)
            page = await self.context.new_page()
            await page.set_viewport_size({"width": 1000, "height": 760})
            page.set_default_timeout(2000)
            self.adapter = await DouyinLoginAdapter(
                self.context, page, block_media=getattr(self.args, "block_media", False)
            ).install()
            self.credentials_changed = True
            self.emit("reset_done")
        await self.adapter.navigate(self.args.entry)
        has_auth = await self.adapter.has_auth()
        if not has_auth and self.args.intent != "reauth":
            await self.handshake("prepare_requested", "prepare_ready")
            self.credentials_changed = True
        await self.handshake("guard_requested", "guard_ready")
        (self.profile["directory"] / "auth-imported").touch(mode=0o600)
        self.emit("guard_established")
        if not has_auth:
            await self.adapter.open_login()
        identity_probe = False
        home_fallback = False
        entry_started = time.monotonic()
        stable_identity = ""
        stable_since = 0.0
        while not self.stopped.is_set():
            observation = await self.adapter.observe()
            if (
                observation.get("region") != self.region
                or observation.get("region_signature", "") != self.region_signature
            ):
                await self.abort_gesture()
            if (
                observation["phase"] == "preparing"
                and not home_fallback
                and time.monotonic() - entry_started > 8
            ):
                home_fallback = True
                await self.adapter.navigate("home")
                await self.adapter.open_login()
            sms_status = (
                await self.adapter.observe_sms_result()
                if self.pending_sms
                else "unknown"
            )
            for command_id, pending in list(self.pending_sms.items()):
                status = (
                    sms_status
                    if pending["action"] == "request_sms"
                    else (
                        "confirmed"
                        if await self.adapter.has_auth()
                        else "rejected"
                        if sms_status == "rejected"
                        else "pending"
                    )
                )
                if status in {"confirmed", "rejected"}:
                    self.result(pending["command"], "executed", status)
                    del self.pending_sms[command_id]
                elif time.monotonic() - pending["started"] > 10:
                    self.result(pending["command"], "executed", "unknown")
                    del self.pending_sms[command_id]
            if (
                observation["phase"]
                not in {"sms_required", "challenge_required", "manual_required"}
                and await self.adapter.has_auth()
            ):
                self.publish({"phase": "identity_check"})
                identity = await self.adapter.trusted_identity()
                if not identity and not identity_probe:
                    identity_probe = True
                    await self.adapter.navigate("self")
                if identity:
                    if (
                        self.args.expected_platform_account_id
                        and identity != self.args.expected_platform_account_id
                    ):
                        if self.credentials_changed:
                            await self.handshake("cleanup_requested", "cleanup_ready")
                            await self.adapter.close()
                            await reset_douyin_credentials_v2(
                                self.context, self.profile
                            )
                            self.emit("cleanup_done")
                        else:
                            self.emit("identity_rejected", cleanup=False)
                        raise ValueError("IDENTITY_MISMATCH")
                    if stable_identity != identity:
                        stable_identity, stable_since = identity, time.monotonic()
                    elif time.monotonic() - stable_since >= 3:
                        break
            else:
                stable_identity, stable_since = "", 0
                self.publish(observation)
            await asyncio.sleep(0.4)
        if self.stopped.is_set():
            return
        self.publish({"phase": "profile_flush"})
        await self.close_context()
        # Reopen exactly the same account registry identity and version. No reset/import.
        await self.open_context()
        self.publish({"phase": "reuse_check"})
        await self.adapter.navigate("self")
        deadline = time.monotonic() + 15
        identity = ""
        while time.monotonic() < deadline and not self.stopped.is_set():
            if await self.adapter.has_auth():
                identity = await self.adapter.trusted_identity()
                if identity:
                    break
            await asyncio.sleep(0.4)
        if identity != stable_identity or not identity:
            raise ValueError("REUSE_CHECK_FAILED")
        state = await asyncio.wait_for(self.context.storage_state(), 15)
        await self.close_context()
        self.emit("phase", phase="db_commit", available_actions=[])
        self.capture_enabled = False
        candidate = {
            "auth_state": state,
            "platform_account_id": identity,
            "identity_verified": True,
            "reuse_verified": True,
            "credentials_changed": self.credentials_changed,
        }
        await self.media.auth(candidate, self.operation_id)
        del state, candidate
        await self.handshake("save_ready", "save_committed")
        # Parent owns success and eligibility. Only commit acknowledgement permits exit.

    async def run(self):
        self.tasks = [
            asyncio.create_task(self.read_controls()),
            asyncio.create_task(self.execute_commands()),
            asyncio.create_task(self.capture_frames()),
        ]
        flow = asyncio.create_task(self.flow())
        stop = asyncio.create_task(self.stopped.wait())
        try:
            done, _ = await asyncio.wait(
                {flow, stop},
                timeout=self.args.timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if flow in done:
                await flow
            elif stop not in done:
                self.emit(
                    "error",
                    code="IDENTITY_UNCONFIRMED"
                    if self.phase == "identity_check"
                    else "LOGIN_TIMEOUT",
                    message="登录会话已超时，请重试",
                )
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) else "BROWSER_LOGIN_FAILED"
            self.emit(
                "error", code=code, message="登录未完成，请重试或查看当前登录步骤"
            )
        finally:
            self.stopped.set()
            for task in [flow, stop, *self.tasks]:
                task.cancel()
            await asyncio.gather(flow, stop, *self.tasks, return_exceptions=True)
            with suppress(Exception):
                await self.close_context()
            self.media.close()


async def run_login_v2(args):
    if args.headed and sys.platform == "linux":
        from scripts.crawler_interactive_login import virtual_display

        with virtual_display():
            await DouyinLoginV2(args).run()
    else:
        await DouyinLoginV2(args).run()
