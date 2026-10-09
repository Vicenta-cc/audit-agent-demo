import asyncio
from types import SimpleNamespace

from backend.audit_agent.crawler_account_identity import douyin_self_response_identity
from scripts.crawler_douyin_login_adapter import Region, document_allowed
from scripts.crawler_douyin_login_v2 import DouyinLoginV2


def test_self_identity_rejects_author_and_anonymous_responses():
    payload = {"status_code": 0, "user": {"sec_uid": "MS4wLjABAAAA-personal"}}
    assert douyin_self_response_identity(
        "https://www.douyin.com/aweme/v1/web/user/profile/self/", payload
    )
    assert not douyin_self_response_identity(
        "https://www.douyin.com/aweme/v1/web/user/profile/other/", payload
    )
    assert not douyin_self_response_identity(
        "https://evil.invalid/aweme/v1/web/user/profile/self/", payload
    )
    assert not douyin_self_response_identity(
        "https://www.douyin.com/aweme/v1/web/query/user/", {"user_uid": "1234"}
    )
    assert not douyin_self_response_identity(
        "https://www.douyin.com/aweme/v1/web/user/profile/self/", {"status_code": 0}
    )


def test_document_allowlist_is_https_and_exact_host():
    assert document_allowed("https://www.douyin.com/user/self", top_level=True)
    assert not document_allowed("https://douyin.com.evil.invalid", top_level=True)
    assert not document_allowed("http://www.douyin.com", top_level=True)
    assert document_allowed(
        "https://lf-rc1.yhgfb-cn-static.com/verify", top_level=False
    )
    assert not document_allowed(
        "https://lf-rc1.yhgfb-cn-static.com/verify", top_level=True
    )


def test_region_translates_normalized_coordinates():
    assert Region(100, 200, 300, 400).point(0.5, 0.25) == (250, 300)


def test_publish_omits_region_when_returning_to_qr():
    helper = DouyinLoginV2.__new__(DouyinLoginV2)
    helper.region = None
    helper.area_version = 0
    helper.challenge_id = ""
    helper.last_snapshot = None
    helper.adapter = SimpleNamespace(
        identity="", page=SimpleNamespace(viewport_size={"width": 1000, "height": 760})
    )
    helper.emit = lambda *a, **kw: None
    helper.publish({"phase": "challenge_required", "region": Region(10, 10, 200, 200)})
    old = helper.challenge_id
    assert old and "pointer_batch" in helper.available_actions
    helper.publish({"phase": "qr_ready", "qr": {"revision": "new"}})
    assert helper.region is None and helper.challenge_id == ""
    assert "pointer_batch" not in helper.available_actions


def test_helper_rejects_stale_epoch_and_area_before_action():
    helper = DouyinLoginV2.__new__(DouyinLoginV2)
    helper.operation_id = "op"
    helper.epoch = 2
    helper.last_sequence = 0
    helper.available_actions = ["pointer_batch"]
    helper.region = Region(0, 0, 100, 100)
    helper.challenge_id = "challenge"
    helper.area_version = 3
    import pytest

    command = {
        "operation_id": "op",
        "controller_epoch": 1,
        "type": "pointer_batch",
        "sequence": 1,
        "challenge_id": "challenge",
        "area_version": 3,
    }
    with pytest.raises(ValueError, match="CONTROLLER_REPLACED"):
        helper.check_command(command)
    command["controller_epoch"] = 2
    command["area_version"] = 2
    with pytest.raises(ValueError, match="STALE_AREA"):
        helper.check_command(command)


def test_capture_uses_config_and_recovers_a_transient_failure(monkeypatch):
    import socket
    import time
    from backend.audit_agent.config import settings

    monkeypatch.setattr(settings, "crawler_login_v2_active_fps", 10)
    monkeypatch.setattr(settings, "crawler_login_v2_idle_fps", 3)
    monkeypatch.setattr(settings, "crawler_login_v2_jpeg_quality", 82)

    async def run():
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(operation_id="op", media_fd=left.detach())
        )
        emitted = []
        qualities = []

        class Page:
            viewport_size = {"width": 1000, "height": 760}

            async def screenshot(self, **kwargs):
                qualities.append(kwargs["quality"])
                if len(qualities) == 1:
                    raise RuntimeError("transient redirect")
                return b"jpeg"

        class Adapter:
            page = Page()
            identity = ""

            async def observe(self):
                return {"phase": "challenge_required", "region": region}

        class Media:
            async def send(self, *args, **kwargs):
                helper.stopped.set()

        region = Region(10, 10, 200, 100)
        helper.adapter = Adapter()
        helper.region = region
        helper.epoch = 1
        helper.controller_attached = True
        helper.phase = "challenge_required"
        helper.last_activity = time.monotonic()
        helper.emit = lambda kind, **payload: emitted.append((kind, payload))
        actual_media = helper.media
        helper.media = Media()
        try:
            assert helper.capture_period() == 0.1
            helper.last_activity = 0
            assert helper.capture_period() == 1 / 3
            helper.last_activity = time.monotonic()
            await helper.capture_frames()
            assert qualities == [82, 82]
            assert any(kind == "notice" for kind, _ in emitted)
            assert not any(kind == "error" for kind, _ in emitted)
        finally:
            actual_media.close()
            right.close()

    asyncio.run(run())


def test_capture_permanent_failure_stops_after_three_attempts():
    import socket

    async def run():
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(operation_id="op", media_fd=left.detach())
        )
        attempts = []
        emitted = []

        class Page:
            viewport_size = {"width": 1000, "height": 760}

            async def screenshot(self, **kwargs):
                attempts.append(kwargs)
                raise RuntimeError("permanently closed page")

        class Adapter:
            page = Page()
            identity = ""

            async def observe(self):
                raise RuntimeError("closed page cannot relocate")

        helper.adapter = Adapter()
        helper.region = Region(10, 10, 200, 100)
        helper.epoch = 1
        helper.controller_attached = True
        helper.phase = "challenge_required"
        helper.emit = lambda kind, **payload: emitted.append((kind, payload))
        try:
            await helper.capture_frames()
            assert len(attempts) == 3
            assert helper.stopped.is_set()
            assert emitted[-1][0] == "error"
            assert emitted[-1][1]["code"] == "CAPTURE_FAILED"
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def test_qr_expiration_without_image_preserves_real_revision_until_refresh():
    from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter
    from unittest.mock import AsyncMock

    async def run():
        page = SimpleNamespace(evaluate=AsyncMock())
        adapter = DouyinLoginAdapter(None, page)
        panel = SimpleNamespace(inner_text=AsyncMock(return_value="扫码登录"))
        adapter.region = AsyncMock(return_value=None)
        adapter.visible = AsyncMock(return_value=panel)
        adapter.qr = AsyncMock(
            return_value={
                "image_data_url": "data:image/png;base64,observed-first",
                "revision": "actual-first",
                "state": "valid",
                "expires_at": None,
            }
        )
        first = await adapter.observe()
        assert first["phase"] == "qr_ready"
        adapter.qr.return_value = None
        panel.inner_text.return_value = "二维码失效 点击刷新"
        expired = await adapter.observe()
        assert expired["phase"] == "qr_expired"
        assert expired["qr"]["revision"] == "actual-first"
        assert expired["qr"]["image_data_url"] == first["qr"]["image_data_url"]
        assert expired["qr"]["state"] == "expired"
        # No new platform image yet: observation cannot manufacture a revision.
        assert (await adapter.observe())["qr"] == expired["qr"]
        adapter.qr.return_value = {
            **first["qr"],
            "revision": "actual-next",
            "image_data_url": "data:image/png;base64,observed-next",
        }
        panel.inner_text.return_value = "扫码登录"
        refreshed = await adapter.observe()
        assert refreshed["phase"] == "qr_ready"
        assert refreshed["qr"]["revision"] == "actual-next"
        assert refreshed["qr"]["state"] == "valid"

    asyncio.run(run())


def test_qr_expiration_before_any_image_is_explicit_without_fake_qr():
    from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter
    from unittest.mock import AsyncMock

    async def run():
        adapter = DouyinLoginAdapter(None, SimpleNamespace(evaluate=AsyncMock()))
        adapter.region = AsyncMock(return_value=None)
        adapter.visible = AsyncMock(
            return_value=SimpleNamespace(
                inner_text=AsyncMock(return_value="二维码已失效 点击刷新"),
            )
        )
        adapter.qr = AsyncMock(return_value=None)
        observation = await adapter.observe()
        assert observation == {"phase": "qr_expired", "qr": None}

    asyncio.run(run())


def test_sms_resend_deadline_is_stable_and_real_button_reenables(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock
    from scripts import crawler_douyin_login_adapter as module

    class Clock(datetime):
        current = datetime(2026, 10, 9, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(module, "datetime", Clock)

    async def run():
        adapter = module.DouyinLoginAdapter(None, SimpleNamespace(evaluate=AsyncMock()))
        panel = SimpleNamespace(
            inner_text=AsyncMock(return_value="短信验证码 60秒后重新获取")
        )
        request = SimpleNamespace(
            is_visible=AsyncMock(return_value=True),
            inner_text=AsyncMock(return_value="60秒后重新获取"),
            is_enabled=AsyncMock(return_value=False),
            evaluate=AsyncMock(return_value=True),
        )
        adapter.visible = AsyncMock(return_value=panel)
        adapter.sms_controls = AsyncMock(return_value={"request": request})
        adapter.sms_result = "pending"
        assert await adapter.observe_sms_result() == "confirmed"
        deadline = adapter.sms_resend_available_at
        assert deadline == Clock.current + timedelta(seconds=60)
        Clock.current += timedelta(seconds=10)
        assert await adapter.observe_sms_result() == "confirmed"
        assert adapter.sms_resend_available_at == deadline
        Clock.current += timedelta(seconds=20)
        request.inner_text.return_value = "30秒后重新获取"
        await adapter.observe_sms_result()
        assert adapter.sms_resend_seconds == 30
        assert adapter.sms_resend_available_at == deadline
        assert not adapter.sms_resend_allowed
        Clock.current += timedelta(seconds=30)
        request.inner_text.return_value = "0秒后重新获取"
        await adapter.observe_sms_result()
        assert adapter.sms_resend_available_at is None
        assert (
            not adapter.sms_resend_allowed
        )  # Timer alone does not enable a disabled button.
        request.inner_text.return_value = "重新发送"
        request.is_enabled.return_value = True
        request.evaluate.return_value = False
        await adapter.observe_sms_result()
        assert adapter.sms_resend_allowed
        assert adapter.sms_resend_available_at is None
        assert adapter.sms_result == "confirmed"
        # Every snapshot reflects actual button state, driving frontend rerender.
        assert adapter.sms_resend_seconds is None

    asyncio.run(run())


def test_sms_countdown_does_not_override_rejection_or_unknown():
    from unittest.mock import AsyncMock
    from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter

    async def run():
        adapter = DouyinLoginAdapter(None, SimpleNamespace(evaluate=AsyncMock()))
        panel = SimpleNamespace(
            inner_text=AsyncMock(return_value="操作频繁 60秒后重新获取")
        )
        request = SimpleNamespace(
            is_visible=AsyncMock(return_value=True),
            inner_text=AsyncMock(return_value="60秒后重新获取"),
            is_enabled=AsyncMock(return_value=False),
            evaluate=AsyncMock(return_value=True),
        )
        adapter.visible = AsyncMock(return_value=panel)
        adapter.sms_controls = AsyncMock(return_value={"request": request})
        adapter.sms_result = "pending"
        assert await adapter.observe_sms_result() == "rejected"
        panel.inner_text.return_value = "60秒后重新获取"
        assert await adapter.observe_sms_result() == "rejected"
        adapter.sms_result = "unknown"
        assert await adapter.observe_sms_result() == "unknown"
        assert not adapter.sms_resend_allowed

    asyncio.run(run())


def test_stale_cookie_with_rendered_qr_enters_prepared_login(tmp_path):
    from unittest.mock import AsyncMock

    async def run():
        left, right = __import__("socket").socketpair()
        args = SimpleNamespace(
            operation_id="op",
            media_fd=left.detach(),
            intent="check_or_login",
            entry="home",
            expected_platform_account_id="",
        )
        helper = DouyinLoginV2(args)
        helper.profile = {"directory": tmp_path}
        adapter = SimpleNamespace(
            has_auth=AsyncMock(return_value=True),
            navigate=AsyncMock(),
            open_login=AsyncMock(),
            identity="",
            identity_source="",
        )
        observations = 0

        async def observe():
            nonlocal observations
            observations += 1
            if observations == 3:
                helper.stopped.set()
            return {
                "phase": "qr_ready",
                "qr": {"revision": "actual", "image_data_url": "observed"},
            }

        adapter.observe = observe
        adapter.trusted_identity = AsyncMock(return_value="")
        helper.adapter = adapter
        helper.open_context = AsyncMock()
        helper.handshake = AsyncMock()
        helper.abort_gesture = AsyncMock()
        helper.emit = lambda *args, **kwargs: None
        phases = []
        helper.publish = lambda snapshot: phases.append(snapshot["phase"])
        try:
            await helper.flow()
            assert phases == ["qr_ready", "qr_ready"]
            assert helper.credentials_changed
            helper.handshake.assert_any_await("prepare_requested", "prepare_ready")
            adapter.trusted_identity.assert_not_awaited()
            assert adapter.navigate.await_count == 1
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def test_recover_with_stale_cookie_and_qr_fails_without_reset(tmp_path):
    from unittest.mock import AsyncMock
    import socket

    async def run():
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(
                operation_id="op",
                media_fd=left.detach(),
                intent="recover",
                entry="self",
                expected_platform_account_id="",
            )
        )
        helper.profile = {"directory": tmp_path}
        helper.adapter = SimpleNamespace(
            navigate=AsyncMock(),
            has_auth=AsyncMock(return_value=True),
            observe=AsyncMock(return_value={"phase": "qr_ready"}),
        )
        helper.open_context = AsyncMock()
        helper.handshake = AsyncMock()
        try:
            with __import__("pytest").raises(ValueError, match="REUSE_AUTH_INVALID"):
                await helper.flow()
            helper.handshake.assert_not_awaited()
            assert not helper.credentials_changed
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def test_authentication_resuming_after_cookie_check_prepares_wrong_identity_cleanup(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock
    from scripts import crawler_douyin_login_v2 as module
    import socket

    async def run():
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(
                operation_id="op",
                media_fd=left.detach(),
                intent="check_or_login",
                entry="home",
                expected_platform_account_id="correct-person",
            )
        )
        helper.profile = {"directory": tmp_path}
        helper.context = object()
        adapter = SimpleNamespace(
            navigate=AsyncMock(),
            has_auth=AsyncMock(return_value=True),
            observe=AsyncMock(
                side_effect=[
                    {"phase": "preparing"},
                    {"phase": "qr_ready", "qr": {"revision": "observed"}},
                    {"phase": "preparing"},
                ]
            ),
            trusted_identity=AsyncMock(return_value="wrong-person"),
            close=AsyncMock(),
            identity="old-person",
            identity_source="old-source",
        )
        helper.adapter = adapter
        helper.open_context = AsyncMock()
        helper.handshake = AsyncMock()
        helper.abort_gesture = AsyncMock()
        helper.publish = lambda _: None
        events = []
        helper.emit = lambda kind, **kwargs: events.append(kind)
        reset = AsyncMock()
        monkeypatch.setattr(module, "reset_douyin_credentials_v2", reset)
        try:
            with __import__("pytest").raises(ValueError, match="IDENTITY_MISMATCH"):
                await helper.flow()
            assert helper.credentials_changed
            calls = [call.args for call in helper.handshake.await_args_list]
            assert ("prepare_requested", "prepare_ready") in calls
            assert ("cleanup_requested", "cleanup_ready") in calls
            assert calls.index(("prepare_requested", "prepare_ready")) < calls.index(
                ("cleanup_requested", "cleanup_ready")
            )
            reset.assert_awaited_once_with(helper.context, helper.profile)
            assert "cleanup_done" in events and "identity_rejected" not in events
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def test_real_sms_button_availability_changes_published_action_capabilities():
    import socket

    left, right = socket.socketpair()
    helper = DouyinLoginV2(SimpleNamespace(operation_id="op", media_fd=left.detach()))
    helper.adapter = SimpleNamespace(
        identity="", page=SimpleNamespace(viewport_size={"width": 1000, "height": 760})
    )
    snapshots = []
    helper.emit = lambda _, **payload: snapshots.append(payload)
    try:
        helper.publish(
            {
                "phase": "sms_required",
                "sms": {"send_status": "confirmed", "resend_allowed": False},
            }
        )
        assert "request_sms" not in snapshots[-1]["available_actions"]
        helper.publish(
            {
                "phase": "sms_required",
                "sms": {"send_status": "confirmed", "resend_allowed": True},
            }
        )
        assert len(snapshots) == 2
        assert "request_sms" in snapshots[-1]["available_actions"]
        assert snapshots[-1]["sms"]["send_status"] == "confirmed"
    finally:
        helper.media.close()
        right.close()


def test_late_sms_confirmation_after_unknown_is_observed_without_replay():
    import socket
    import time
    from unittest.mock import AsyncMock
    from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter

    async def run():
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(operation_id="op", media_fd=left.detach())
        )
        adapter = DouyinLoginAdapter(None, None)
        panel = SimpleNamespace(inner_text=AsyncMock(return_value="请先完成图形验证"))
        request = SimpleNamespace(
            is_visible=AsyncMock(return_value=False),
            inner_text=AsyncMock(return_value="60秒后重新发送"),
            is_enabled=AsyncMock(return_value=False),
            evaluate=AsyncMock(return_value=True),
        )
        adapter.visible = AsyncMock(return_value=panel)
        adapter.sms_controls = AsyncMock(return_value={"request": request})
        adapter.sms_result = "pending"
        adapter.sms_request_observation_pending = True
        helper.adapter = adapter
        helper.pending_sms = {
            "issued": {
                "command": {"command_id": "issued"},
                "action": "request_sms",
                "started": time.monotonic() - 20,
            }
        }
        results = []
        helper.emit = lambda kind, **payload: results.append((kind, payload))
        try:
            await helper.observe_pending_commands({"phase": "challenge_required"})
            assert results[-1][1]["platform_status"] == "unknown"
            assert "issued" in helper.pending_sms
            count = len(results)
            await helper.observe_pending_commands({"phase": "challenge_required"})
            assert len(results) == count
            request.is_visible.return_value = True
            panel.inner_text.return_value = "60秒后重新发送"
            await helper.observe_pending_commands({"phase": "sms_required"})
            assert results[-1][1]["platform_status"] == "confirmed"
            assert results[-1][1]["command_id"] == "issued"
            assert not helper.pending_sms
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def test_historical_backup_without_marker_requires_explicit_migration(tmp_path):
    from unittest.mock import AsyncMock
    import socket

    async def run():
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(
                operation_id="op",
                media_fd=left.detach(),
                intent="check_or_login",
                entry="self",
                expected_platform_account_id="",
                has_auth_backup=True,
            )
        )
        helper.profile = {"directory": tmp_path}
        helper.adapter = SimpleNamespace(
            navigate=AsyncMock(),
            has_auth=AsyncMock(return_value=True),
            observe=AsyncMock(return_value={"phase": "qr_ready"}),
        )
        helper.open_context = AsyncMock()
        helper.handshake = AsyncMock()
        try:
            with __import__("pytest").raises(
                ValueError, match="PROFILE_MIGRATION_REQUIRED"
            ):
                await helper.flow()
            helper.handshake.assert_awaited_once_with(
                "prepare_requested", "prepare_ready"
            )
            assert not (tmp_path / "auth-imported").exists()
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def _gesture_helper():
    import socket
    from unittest.mock import AsyncMock

    left, right = socket.socketpair()
    helper = DouyinLoginV2(SimpleNamespace(operation_id="op", media_fd=left.detach()))
    helper.adapter = SimpleNamespace(
        identity="",
        page=SimpleNamespace(
            viewport_size={"width": 1000, "height": 760},
            mouse=SimpleNamespace(up=AsyncMock()),
        ),
    )
    helper.region = Region(10, 10, 200, 100)
    helper.epoch = 1
    helper.challenge_id = "challenge"
    helper.area_version = 1
    helper.phase = "challenge_required"
    helper.available_actions = ["pointer_batch", "abort_gesture"]
    helper.pointer_down = True
    helper.emit = lambda *args, **kwargs: None
    return helper, right


def test_full_queue_cannot_leave_mouse_down_when_up_is_rejected():
    async def run():
        helper, right = _gesture_helper()
        try:
            for n in range(32):
                await helper.enqueue_command(
                    {"type": "pointer_batch", "command_id": str(n)}
                )
            await helper.enqueue_command({"type": "pointer_batch", "command_id": "up"})
            helper.adapter.page.mouse.up.assert_awaited_once()
            assert not helper.pointer_down
            assert helper.commands.empty() and not helper.pending_trajectories
            assert helper.results["up"]["platform_status"] == "not_applicable"
            assert helper.area_version > 1
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def test_abort_bypasses_full_queue_and_watchdog_only_expires_pending_work():
    import time

    async def run():
        helper, right = _gesture_helper()
        try:
            for n in range(32):
                await helper.enqueue_command(
                    {"type": "pointer_batch", "command_id": str(n)}
                )
            await helper.enqueue_command(
                {
                    "type": "abort_gesture",
                    "command_id": "abort",
                    "operation_id": "op",
                    "controller_epoch": 1,
                    "sequence": 99,
                    "challenge_id": "challenge",
                    "area_version": 1,
                }
            )
            assert helper.commands.empty()
            assert helper.results["abort"]["execution_status"] == "executed"
            helper.adapter.page.mouse.up.assert_awaited_once()
            helper.adapter.page.mouse.up.reset_mock()
            helper.pointer_down = True
            watchdog = asyncio.create_task(helper.gesture_watchdog())
            await asyncio.sleep(0.075)
            helper.adapter.page.mouse.up.assert_not_awaited()  # Legitimate idle hold has no queued work.
            helper.pending_trajectories["old"] = time.monotonic() - 1
            await asyncio.sleep(0.05)
            helper.adapter.page.mouse.up.assert_awaited_once()
            assert not helper.pointer_down and not helper.pending_trajectories
            helper.stopped.set()
            await watchdog
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def test_sms_button_disabled_before_execution_is_definitively_not_executed():
    import socket
    from unittest.mock import AsyncMock

    async def run():
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(operation_id="op", media_fd=left.detach())
        )
        phone = SimpleNamespace(fill=AsyncMock())
        request = SimpleNamespace(click=AsyncMock())
        helper.adapter = SimpleNamespace(
            page=object(),
            sms_controls=AsyncMock(return_value={"phone": phone, "request": request}),
            observe_sms_result=AsyncMock(return_value="unknown"),
            sms_resend_allowed=False,
        )
        helper.epoch = 1
        helper.available_actions = ["request_sms"]
        helper.emit = (
            lambda kind, **payload: helper.stopped.set()
            if kind == "command_result"
            else None
        )
        helper.commands.put_nowait(
            {
                "type": "action",
                "action": "request_sms",
                "command_id": "request",
                "operation_id": "op",
                "controller_epoch": 1,
                "sequence": 1,
            }
        )
        try:
            await helper.execute_commands()
            assert helper.results["request"]["execution_status"] == "rejected"
            assert helper.results["request"]["platform_status"] == "not_applicable"
            phone.fill.assert_not_awaited()
            request.click.assert_not_awaited()
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


def test_home_fallback_only_applies_before_any_actionable_login_step(
    tmp_path, monkeypatch
):
    import socket
    from unittest.mock import AsyncMock
    from scripts import crawler_douyin_login_v2 as module

    async def scenario(phases):
        clock = {"now": 0}
        monkeypatch.setattr(
            module, "time", SimpleNamespace(monotonic=lambda: clock["now"])
        )
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(
                operation_id="op",
                media_fd=left.detach(),
                intent="check_or_login",
                entry="self",
                expected_platform_account_id="",
            )
        )
        helper.profile = {"directory": tmp_path}
        index = 0

        async def observe():
            nonlocal index
            phase = phases[min(index, len(phases) - 1)]
            clock["now"] = index * 10
            index += 1
            if index == len(phases):
                helper.stopped.set()
            return {"phase": phase}

        adapter = SimpleNamespace(
            navigate=AsyncMock(),
            has_auth=AsyncMock(return_value=False),
            open_login=AsyncMock(),
            observe=observe,
            identity="",
            identity_source="",
        )
        helper.adapter = adapter
        helper.open_context = AsyncMock()
        helper.handshake = AsyncMock()
        helper.abort_gesture = AsyncMock()
        helper.emit = lambda *args, **kwargs: None
        helper.publish = lambda _: None
        try:
            await helper.flow()
            return [call.args[0] for call in adapter.navigate.await_args_list]
        finally:
            helper.media.close()
            right.close()

    assert asyncio.run(scenario(["preparing", "preparing", "preparing"])) == [
        "self",
        "home",
    ]
    assert asyncio.run(scenario(["qr_ready", "qr_ready", "preparing"])) == ["self"]


def test_personal_header_identity_requires_exact_individual_labels():
    from unittest.mock import AsyncMock
    from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter

    async def run():
        locator = SimpleNamespace(
            evaluate_all=AsyncMock(
                return_value=[
                    {
                        "href": "https://www.douyin.com/user/MS4wLjABAAAA-author",
                        "text": "作者的个人主页",
                        "label": "查看作者的个人主页",
                        "title": "作者的个人主页",
                        "visible": True,
                    }
                ]
            )
        )
        adapter = DouyinLoginAdapter(None, SimpleNamespace(locator=lambda _: locator))
        assert await adapter.trusted_identity() == ""
        for field in ("text", "label", "title"):
            locator.evaluate_all.return_value = [
                {
                    "href": "https://www.douyin.com/user/MS4wLjABAAAA-self",
                    "text": "",
                    "label": "",
                    "title": "",
                    "visible": True,
                    field: "我的主页",
                }
            ]
            assert await adapter.trusted_identity() == "MS4wLjABAAAA-self"
            assert adapter.identity_source == "personal_header_menu"
            adapter.identity = ""

    asyncio.run(run())


def test_personal_header_conflicting_ids_fail_closed_and_self_response_has_priority():
    from unittest.mock import AsyncMock
    from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter
    import pytest

    async def run():
        locator = SimpleNamespace(
            evaluate_all=AsyncMock(
                return_value=[
                    {
                        "href": "https://www.douyin.com/user/MS4wLjABAAAA-first",
                        "text": "我的主页",
                        "visible": True,
                    },
                    {
                        "href": "https://www.douyin.com/user/MS4wLjABAAAA-second",
                        "label": "个人主页",
                        "visible": True,
                    },
                ]
            )
        )
        adapter = DouyinLoginAdapter(None, SimpleNamespace(locator=lambda _: locator))
        with pytest.raises(ValueError, match="IDENTITY_UNCONFIRMED"):
            await adapter.trusted_identity()
        assert adapter.identity == ""
        locator.evaluate_all.reset_mock()
        adapter.identity = "MS4wLjABAAAA-response"
        adapter.identity_source = "douyin_self_response"
        assert await adapter.trusted_identity() == "MS4wLjABAAAA-response"
        locator.evaluate_all.assert_not_awaited()

    asyncio.run(run())


def test_capture_freezes_input_ack_and_sends_one_final_same_pixel_confirmation():
    import socket

    async def run():
        left, right = socket.socketpair()
        helper = DouyinLoginV2(
            SimpleNamespace(operation_id="op", media_fd=left.detach())
        )
        screenshots = []
        frames = []

        class Page:
            viewport_size = {"width": 1000, "height": 760}

            async def screenshot(self, **kwargs):
                screenshots.append(kwargs)
                if len(screenshots) == 1:
                    # The command finishes while screenshot is in flight. Its
                    # old-started capture must not claim the new execution ACK.
                    helper.applied_sequence = 5
                    await asyncio.sleep(0)
                elif len(screenshots) == 4:
                    helper.stopped.set()
                return b"same-final-pixels"

        class Media:
            async def send(self, payload, metadata, **kwargs):
                frames.append((payload, metadata))

        helper.adapter = SimpleNamespace(page=Page())
        helper.region = Region(10, 10, 200, 100)
        helper.epoch = 1
        helper.area_version = 2
        helper.controller_attached = True
        helper.phase = "challenge_required"
        helper.applied_sequence = 4
        helper.capture_period = lambda: 0
        actual_media = helper.media
        helper.media = Media()
        try:
            await helper.capture_frames()
            assert len(screenshots) == 4
            assert len(frames) == 2  # Following identical idle frames are deduplicated.
            assert [metadata["applied_input_sequence"] for _, metadata in frames] == [
                4,
                5,
            ]
            assert frames[0][0] == frames[1][0] == b"same-final-pixels"
        finally:
            actual_media.close()
            right.close()

    asyncio.run(run())
