import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter


@pytest.mark.parametrize(
    "url,parent,allowed",
    [
        ("https://www.douyin.com/", None, True),
        ("https://blocked.invalid/", None, False),
        ("http://www.douyin.com/", None, False),
        ("https://lf-rc1.yhgfb-cn-static.com/", object(), True),
        ("https://lf-rc1.yhgfb-cn-static.com/", None, False),
        ("https://blocked.invalid/frame", object(), False),
    ],
)
def test_document_guard_preserves_top_level_iframe_policy(url, parent, allowed):
    async def run():
        page = SimpleNamespace()
        request = SimpleNamespace(
            url=url,
            frame=SimpleNamespace(page=page, parent_frame=parent),
            is_navigation_request=lambda: True,
            resource_type="document",
        )
        route = SimpleNamespace(
            request=request, fallback=AsyncMock(), abort=AsyncMock()
        )
        adapter = DouyinLoginAdapter(SimpleNamespace(), page)
        await adapter._guard_request(route)
        assert route.fallback.await_count == int(allowed)
        assert route.abort.await_count == int(not allowed)

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind,blocked",
    [("media", True), ("script", False), ("image", False), ("xhr", False)],
)
def test_only_video_media_is_filtered(kind, blocked):
    async def run():
        page = SimpleNamespace()
        request = SimpleNamespace(
            url="https://v.douyinvod.com/resource",
            frame=SimpleNamespace(page=page, parent_frame=None),
            is_navigation_request=lambda: False,
            resource_type=kind,
        )
        route = SimpleNamespace(
            request=request, fallback=AsyncMock(), abort=AsyncMock()
        )
        adapter = DouyinLoginAdapter(SimpleNamespace(), page, block_media=True)
        await adapter._guard_request(route)
        assert route.abort.await_count == int(blocked)
        assert route.fallback.await_count == int(not blocked)

    asyncio.run(run())


def test_context_policy_registered_before_popup_pages_and_existing_pages_closed():
    async def run():
        existing = SimpleNamespace(
            url="https://blocked.invalid/restored", close=AsyncMock()
        )
        context = SimpleNamespace(pages=[existing], route=AsyncMock())
        adapter = DouyinLoginAdapter(context, existing)
        await adapter.install_guard(existing)
        context.route.assert_awaited_once_with("**/*", adapter._guard_request)
        existing.close.assert_awaited_once()

    asyncio.run(run())


def test_popup_without_frame_is_treated_as_top_level():
    class PopupRequest:
        url = "https://blocked.invalid/popup"
        resource_type = "document"

        @property
        def frame(self):
            raise RuntimeError("Frame has not been created")

        def is_navigation_request(self):
            return True

    async def run():
        context = SimpleNamespace(close=AsyncMock())
        route = SimpleNamespace(
            request=PopupRequest(), abort=AsyncMock(), fallback=AsyncMock()
        )
        adapter = DouyinLoginAdapter(context, SimpleNamespace())
        await adapter._guard_request(route)
        route.abort.assert_awaited_once()
        context.close.assert_not_awaited()

    asyncio.run(run())


def test_navigation_addon_cannot_follow_profile_policy_symlink(tmp_path):
    from backend.audit_agent.crawler_browser import load_account_browser

    load_account_browser()
    from tools.navigation_guard_addon import build_navigation_guard

    profile = tmp_path / "profile"
    other = tmp_path / "unrelated"
    profile.mkdir()
    other.mkdir()
    (other / "background.js").write_text("keep-existing-data")
    (profile / "navigation-policy").symlink_to(other, target_is_directory=True)
    with pytest.raises(OSError):
        build_navigation_guard(profile)
    assert (other / "background.js").read_text() == "keep-existing-data"


def test_navigation_addon_pins_complete_asset_set_and_private_files(tmp_path):
    import json
    from backend.audit_agent.crawler_browser import load_account_browser

    load_account_browser()
    from tools.navigation_guard_addon import build_navigation_guard

    profile = tmp_path / "profile"
    profile.mkdir()
    first = build_navigation_guard(profile, "https://api.ip.sb/ip")
    second = build_navigation_guard(profile, "https://api.ip.sb/ip")
    assert first == second
    directory = profile / "navigation-policy"
    assert {item.name for item in directory.iterdir()} == {
        "manifest.json",
        "background.js",
        "ready.js",
        "policy.js",
    }
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(item.stat().st_mode & 0o777 == 0o600 for item in directory.iterdir())
    assert (
        json.loads((directory / "manifest.json").read_text())["manifest_version"] == 2
    )
