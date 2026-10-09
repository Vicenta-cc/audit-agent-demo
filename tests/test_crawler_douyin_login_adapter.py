import asyncio
import base64
from types import SimpleNamespace

from backend.audit_agent.crawler_account_identity import douyin_self_response_identity
from scripts.crawler_douyin_login_adapter import Region, document_allowed
from scripts.crawler_douyin_login_v2 import DouyinLoginV2


def test_self_identity_rejects_author_and_anonymous_responses():
    payload = {"status_code": 0, "user": {"sec_uid": "MS4wLjABAAAA-personal"}}
    assert douyin_self_response_identity("https://www.douyin.com/aweme/v1/web/user/profile/self/", payload)
    assert not douyin_self_response_identity("https://www.douyin.com/aweme/v1/web/user/profile/other/", payload)
    assert not douyin_self_response_identity("https://evil.invalid/aweme/v1/web/user/profile/self/", payload)
    assert not douyin_self_response_identity("https://www.douyin.com/aweme/v1/web/query/user/", {"user_uid": "1234"})
    assert not douyin_self_response_identity("https://www.douyin.com/aweme/v1/web/user/profile/self/", {"status_code": 0})


def test_document_allowlist_is_https_and_exact_host():
    assert document_allowed("https://www.douyin.com/user/self", top_level=True)
    assert not document_allowed("https://douyin.com.evil.invalid", top_level=True)
    assert not document_allowed("http://www.douyin.com", top_level=True)
    assert document_allowed("https://lf-rc1.yhgfb-cn-static.com/verify", top_level=False)
    assert not document_allowed("https://lf-rc1.yhgfb-cn-static.com/verify", top_level=True)


def test_region_translates_normalized_coordinates():
    assert Region(100, 200, 300, 400).point(.5, .25) == (250, 300)


def test_publish_omits_region_when_returning_to_qr():
    helper = DouyinLoginV2.__new__(DouyinLoginV2)
    helper.region = None
    helper.area_version = 0
    helper.challenge_id = ""
    helper.last_snapshot = None
    helper.adapter = SimpleNamespace(identity="", page=SimpleNamespace(viewport_size={"width": 1000, "height": 760}))
    helper.emit = lambda *a, **kw: None
    helper.publish({"phase": "challenge_required", "region": Region(10, 10, 200, 200)})
    old = helper.challenge_id
    assert old and "pointer_batch" in helper.available_actions
    helper.publish({"phase": "qr_ready", "qr": {"revision": "new"}})
    assert helper.region is None and helper.challenge_id == ""
    assert "pointer_batch" not in helper.available_actions


def test_helper_rejects_stale_epoch_and_area_before_action():
    helper = DouyinLoginV2.__new__(DouyinLoginV2)
    helper.operation_id = "op"; helper.epoch = 2; helper.last_sequence = 0
    helper.available_actions = ["pointer_batch"]; helper.region = Region(0,0,100,100)
    helper.challenge_id = "challenge"; helper.area_version = 3
    import pytest
    command = {"operation_id":"op","controller_epoch":1,"type":"pointer_batch","sequence":1,
               "challenge_id":"challenge","area_version":3}
    with pytest.raises(ValueError, match="CONTROLLER_REPLACED"): helper.check_command(command)
    command["controller_epoch"] = 2; command["area_version"] = 2
    with pytest.raises(ValueError, match="STALE_AREA"): helper.check_command(command)
