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

    async def storage_state(self):
        return {"origins": [{"origin": "https://saved.douyin.com"}],
                "cookies": [{"domain": ".cookie.douyin.com"}]}

    async def clear_cookies(self):
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
        assert script == "localStorage.clear(); sessionStorage.clear()"
        self.context.cleared_origins.append(self.url)


def test_headed_reset_clears_credentials_without_exiting_browser(tmp_path):
    async def run():
        context = HeadedContext()
        await prepare_headed_login(context, {"directory": tmp_path})
        assert (tmp_path / "auth-imported").exists()
        assert context.cookies_cleared
        assert {"https://saved.douyin.com", "https://cookie.douyin.com",
                "https://www.douyin.com"} <= set(context.cleared_origins)
        assert not context.closed
        assert [page.url for page in context.pages] == ["about:blank"]
        page = await context.new_page()
        await page.goto("https://www.douyin.com")
        await close_rendered_pages(context)
        assert not context.closed
        assert [page.url for page in context.pages] == ["about:blank"]
    asyncio.run(run())
