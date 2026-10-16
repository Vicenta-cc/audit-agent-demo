import asyncio

from scripts.crawler_login_pages import close_rendered_pages, prepare_headed_login


class HeadedContext:
    """Model Chromium exiting immediately when its final tab closes."""

    def __init__(self):
        self.pages = [Page(self)]
        self.closed = False
        self.cookies_cleared = False
        self.cleared_origins = []

    async def new_page(self):
        assert not self.closed
        page = Page(self)
        self.pages.append(page)
        return page

    async def storage_state(self, **kwargs):
        return {
            "origins": [{"origin": "https://saved.douyin.com"}],
            "cookies": [{"domain": ".cookie.douyin.com"}],
        }

    async def cookies(self):
        return [{"domain": ".cookie.douyin.com"}]

    async def clear_cookies(self, **kwargs):
        assert not self.closed
        self.cookies_cleared = True


class Page:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"

    async def close(self):
        self.context.pages.remove(self)
        if not self.context.pages:
            self.context.closed = True

    async def route(self, *_):
        pass

    async def goto(self, url, **_):
        assert not self.context.closed
        self.url = url

    async def evaluate(self, script):
        assert all(
            token in script
            for token in (
                "localStorage.clear",
                "sessionStorage.clear",
                "deleteDatabase",
                "unregister",
                "caches.delete",
            )
        )
        self.context.cleared_origins.append(self.url)


def test_headed_reset_clears_credentials_without_exiting_browser(tmp_path):
    async def run():
        context = HeadedContext()
        context.pages[0].url = "https://saved.douyin.com/"
        await prepare_headed_login(context, {"directory": tmp_path})
        assert (tmp_path / "auth-imported").exists()
        assert context.cookies_cleared
        assert {
            "https://saved.douyin.com",
            "https://cookie.douyin.com",
            "https://www.douyin.com",
        } <= set(context.cleared_origins)
        assert not context.closed
        assert [page.url for page in context.pages] == ["about:blank"]
        page = await context.new_page()
        await page.goto("https://www.douyin.com")
        await close_rendered_pages(context)
        assert not context.closed
        assert [page.url for page in context.pages] == ["about:blank"]

    asyncio.run(run())


def test_reset_does_not_export_incomplete_indexeddb_and_covers_disk_and_live_origins(
    tmp_path,
):
    async def run():
        context = HeadedContext()
        calls = []

        async def state(**kwargs):
            calls.append(kwargs)
            if kwargs.get("indexed_db"):
                raise RuntimeError(
                    "Unable to serialize IndexedDB: Database version is unset"
                )
            return {"origins": [], "cookies": []}

        context.storage_state = state
        context.pages[0].url = "https://visited.douyin.com/login"
        context.pages[0].frames = [
            type("Frame", (), {"url": "https://frame.douyin.com/challenge"})()
        ]
        storage = tmp_path / "storage" / "default"
        storage.mkdir(parents=True)
        for name in (
            "https+++idbonly.douyin.com",
            "https+++partition.douyin.com^partitionKey=%28https%2Cdouyin.com%29",
            "https+++unrelated.invalid",
            "https+++douyin.com.evil.invalid",
        ):
            (storage / name).mkdir()
        await prepare_headed_login(context, {"directory": tmp_path})
        assert calls == []
        assert {
            "https://visited.douyin.com",
            "https://frame.douyin.com",
            "https://idbonly.douyin.com",
            "https://partition.douyin.com",
        } <= set(context.cleared_origins)
        assert not any("invalid" in origin for origin in context.cleared_origins)
        assert context.cookies_cleared

    asyncio.run(run())


def test_reset_still_fails_closed_when_indexeddb_deletion_fails(tmp_path):
    import pytest

    async def run():
        context = HeadedContext()
        original = context.new_page

        async def new_page():
            page = await original()

            async def failed(_):
                raise RuntimeError("IDB_RESET_BLOCKED")

            page.evaluate = failed
            return page

        context.new_page = new_page
        with pytest.raises(RuntimeError, match="IDB_RESET_BLOCKED"):
            await prepare_headed_login(context, {"directory": tmp_path})
        assert not context.cookies_cleared
        assert not (tmp_path / "auth-imported").exists()

    asyncio.run(run())


def test_first_party_export_never_traverses_third_party_or_echo_storage():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from scripts.crawler_login_pages import export_douyin_auth_state

    async def run():
        first = SimpleNamespace(
            url="https://www.douyin.com/user/self",
            evaluate=AsyncMock(
                return_value={
                    "origin": "https://www.douyin.com",
                    "localStorage": [{"name": "account", "value": "fixture"}],
                }
            ),
        )
        third = SimpleNamespace(
            url="https://lf-rc1.yhgfb-cn-static.com/verification",
            evaluate=AsyncMock(
                side_effect=AssertionError("third-party storage must not be read")
            ),
        )
        echo = SimpleNamespace(
            url="https://api.ip.sb/ip",
            evaluate=AsyncMock(
                side_effect=AssertionError("echo storage must not be read")
            ),
        )
        page = SimpleNamespace(url=first.url, frames=[first, third])
        context = SimpleNamespace(
            pages=[page, echo],
            cookies=AsyncMock(
                return_value=[
                    {"domain": ".douyin.com", "name": "sessionid", "value": "fixture"},
                    {"domain": "api.ip.sb", "name": "echo", "value": "never-export"},
                    {
                        "domain": "douyin.com.invalid",
                        "name": "other",
                        "value": "never-export",
                    },
                ]
            ),
            storage_state=AsyncMock(
                side_effect=AssertionError("all-origin export must never run")
            ),
        )
        result = await export_douyin_auth_state(context)
        assert result == {
            "cookies": [
                {"domain": ".douyin.com", "name": "sessionid", "value": "fixture"}
            ],
            "origins": [
                {
                    "origin": "https://www.douyin.com",
                    "localStorage": [{"name": "account", "value": "fixture"}],
                }
            ],
        }
        context.storage_state.assert_not_awaited()
        third.evaluate.assert_not_awaited()
        echo.evaluate.assert_not_awaited()

    asyncio.run(run())


@__import__("pytest").mark.parametrize(
    "actual_origin", ["https://api.ip.sb", "https://different.douyin.com"]
)
def test_first_party_export_fails_closed_on_frame_navigation_race(actual_origin):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from scripts.crawler_login_pages import export_douyin_auth_state

    async def run():
        frame = SimpleNamespace(
            url="https://www.douyin.com/user/self",
            evaluate=AsyncMock(
                return_value={"origin": actual_origin, "localStorage": []}
            ),
        )
        context = SimpleNamespace(
            pages=[SimpleNamespace(frames=[frame])], cookies=AsyncMock(return_value=[])
        )
        with __import__("pytest").raises(
            ValueError, match="AUTH_EXPORT_ORIGIN_CHANGED"
        ):
            await export_douyin_auth_state(context)

    asyncio.run(run())
