import unittest

from backend.audit_agent.crawler_account_identity import (
    candidate_is_personal_link,
    extract_platform_account_id,
)


class CrawlerAccountIdentityTest(unittest.TestCase):
    def test_extracts_supported_profile_ids(self):
        self.assertEqual(
            extract_platform_account_id(
                "xhs",
                "https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753?xsec_source=pc_feed",
            ),
            "5f58bd990000000001003753",
        )
        self.assertEqual(
            extract_platform_account_id(
                "dy",
                "https://www.douyin.com/user/MS4wLjABAAAA_test-user?from_tab_name=main",
            ),
            "MS4wLjABAAAA_test-user",
        )
        self.assertEqual(
            extract_platform_account_id(
                "ks",
                "https://www.kuaishou.com/profile/3x84qugg4ch9zhs",
            ),
            "3x84qugg4ch9zhs",
        )

    def test_rejects_non_profile_urls(self):
        self.assertEqual(
            extract_platform_account_id(
                "xhs",
                "https://www.xiaohongshu.com/explore/5f58bd990000000001003753",
            ),
            "",
        )
        self.assertEqual(
            extract_platform_account_id("dy", "https://www.douyin.com/video/123456789"),
            "",
        )

    def test_douyin_candidate_must_be_top_level_personal_link(self):
        header_avatar = {
            "visible": True,
            "top": 48,
            "hasImage": True,
            "inContent": False,
            "text": "",
            "label": "",
        }
        feed_author = {**header_avatar, "top": 420}
        content_header = {**header_avatar, "inContent": True}
        unlabeled_fallback = {**header_avatar, "hasImage": False}
        labeled_fallback = {**unlabeled_fallback, "label": "个人主页"}

        self.assertTrue(candidate_is_personal_link("dy", header_avatar, False))
        self.assertFalse(candidate_is_personal_link("dy", feed_author, False))
        self.assertFalse(candidate_is_personal_link("dy", content_header, False))
        self.assertFalse(candidate_is_personal_link("dy", unlabeled_fallback, True))
        self.assertTrue(candidate_is_personal_link("dy", labeled_fallback, True))


if __name__ == "__main__":
    unittest.main()


def test_douyin_open_author_url_and_unlabelled_avatar_are_not_self(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import pytest

    pytest.importorskip(
        "playwright", reason="login helper runs in pinned crawler Python"
    )
    from scripts import crawler_account_login as login

    author = SimpleNamespace(url="https://www.douyin.com/user/MS4wLjABAAAA_author")
    candidate = {
        "href": author.url,
        "visible": True,
        "top": 40,
        "hasImage": True,
        "inContent": False,
        "text": "",
        "label": "",
    }
    monkeypatch.setattr(
        login, "rendered_link_candidates", AsyncMock(return_value=[candidate])
    )
    assert (
        asyncio.run(
            login.identify_logged_in_account("dy", SimpleNamespace(pages=[author]))
        )
        == ""
    )


def test_douyin_explicit_personal_menu_is_self_even_on_author_page(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import pytest

    pytest.importorskip(
        "playwright", reason="login helper runs in pinned crawler Python"
    )
    from scripts import crawler_account_login as login

    author = SimpleNamespace(url="https://www.douyin.com/user/MS4wLjABAAAA_author")
    personal = {
        "href": "https://www.douyin.com/user/MS4wLjABAAAA_myself",
        "visible": True,
        "top": 40,
        "hasImage": True,
        "inContent": False,
        "text": "",
        "label": "我的主页",
    }
    content = {**personal, "href": author.url, "inContent": True}
    monkeypatch.setattr(
        login, "rendered_link_candidates", AsyncMock(return_value=[personal, content])
    )
    assert (
        asyncio.run(
            login.identify_logged_in_account("dy", SimpleNamespace(pages=[author]))
        )
        == "MS4wLjABAAAA_myself"
    )


def test_douyin_conflicting_personal_menus_across_pages_are_rejected(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import pytest

    pytest.importorskip(
        "playwright", reason="login helper runs in pinned crawler Python"
    )
    from scripts import crawler_account_login as login

    first, second = (
        SimpleNamespace(url="https://www.douyin.com"),
        SimpleNamespace(url="https://www.douyin.com/user/self"),
    )

    async def candidates(page, selector):
        identity = "MS4wLjABAAAA_first" if page is first else "MS4wLjABAAAA_second"
        return [
            {
                "href": "https://www.douyin.com/user/" + identity,
                "visible": True,
                "top": 40,
                "hasImage": True,
                "inContent": False,
                "text": "个人主页",
                "label": "",
            }
        ]

    monkeypatch.setattr(login, "rendered_link_candidates", candidates)
    assert (
        asyncio.run(
            login.identify_logged_in_account(
                "dy", SimpleNamespace(pages=[first, second])
            )
        )
        == ""
    )


def test_non_douyin_identity_keeps_existing_current_url_behavior(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import pytest

    pytest.importorskip(
        "playwright", reason="login helper runs in pinned crawler Python"
    )
    from scripts import crawler_account_login as login

    page = SimpleNamespace(
        url="https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753"
    )
    render = AsyncMock()
    monkeypatch.setattr(login, "rendered_link_candidates", render)
    assert (
        asyncio.run(
            login.identify_logged_in_account("xhs", SimpleNamespace(pages=[page]))
        )
        == "5f58bd990000000001003753"
    )
    render.assert_not_called()


def test_douyin_author_homepage_label_is_not_personal_identity(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest

    pytest.importorskip(
        "playwright", reason="login helper runs in pinned crawler Python"
    )
    from scripts import crawler_account_login as login

    page = SimpleNamespace(url="https://www.douyin.com/user/MS4wLjABAAAA_author")
    for label in ("作者主页", "我关注的作者主页", "作者的个人主页"):
        candidate = {
            "href": page.url,
            "visible": True,
            "top": 40,
            "hasImage": True,
            "inContent": False,
            "text": "",
            "label": label,
        }
        monkeypatch.setattr(
            login, "rendered_link_candidates", AsyncMock(return_value=[candidate])
        )
        assert (
            asyncio.run(
                login.identify_logged_in_account("dy", SimpleNamespace(pages=[page]))
            )
            == ""
        )
