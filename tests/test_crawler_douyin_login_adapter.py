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
