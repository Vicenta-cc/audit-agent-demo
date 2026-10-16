import asyncio
import base64
import cv2
import json
import numpy as np
import os
import sys
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.audit_agent.config import settings
from backend.audit_agent.crawler_adapter import MediaCrawlerAdapter
from backend.audit_agent.crawler_browser import (
    account_browser_env,
    account_profile_directory,
    account_profile_owns_auth,
    load_account_browser,
)
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_login_manager import CrawlerAccountLoginManager

try:
    from scripts import crawler_account_login as login
except ModuleNotFoundError as exc:
    if exc.name != "playwright":
        raise
    login = None  # The backend env need not contain the separate browser runtime.


class BrowserBindingTest(unittest.TestCase):
    def test_unmanaged_search_and_creator_fail_closed_before_browser_launch(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("backend.audit_agent.crawler_adapter.subprocess.Popen") as launch,
        ):
            adapter = MediaCrawlerAdapter(Path(directory).resolve())
            common = dict(
                platform="dy",
                account_id="fixture-a",
                max_notes=1,
                max_comments=2,
                max_concurrency=1,
                max_items_per_minute=5,
                get_sub_comment=False,
                save_root=Path(directory).resolve() / "out",
            )
            with self.assertRaisesRegex(
                RuntimeError, "CRAWLER_AUTHORITY_ROUTE_REQUIRED"
            ):
                adapter.run_search(keyword="fixture", start_page=0, **common)
            with self.assertRaisesRegex(
                RuntimeError, "CRAWLER_AUTHORITY_ROUTE_REQUIRED"
            ):
                adapter.run_creator(creator_id="fixture-creator", **common)
            launch.assert_not_called()

    def test_crawl_env_uses_selected_id_and_removes_inherited_account(self):
        adapter = MediaCrawlerAdapter(Path("/fixture/crawler"))
        with patch.dict(
            os.environ,
            {
                "MEDIACRAWLER_ACCOUNT_ID": "wrong-account",
                "MEDIACRAWLER_CAMOUFOX_PROFILE_ROOT": "/wrong-root",
                "MEDIACRAWLER_DY_BROWSER_ENGINE": "playwright",
            },
        ):
            env = adapter._subprocess_env(platform="dy", account_id="account-a")
            self.assertEqual(env["MEDIACRAWLER_ACCOUNT_ID"], "account-a")
            self.assertEqual(
                env["MEDIACRAWLER_CAMOUFOX_PROFILE_ROOT"],
                str(settings.crawler_camoufox_state_root / "profiles"),
            )
            self.assertEqual(env["MEDIACRAWLER_DY_BROWSER_ENGINE"], "camoufox")
            self.assertNotIn(
                "MEDIACRAWLER_ACCOUNT_ID", adapter._subprocess_env(platform="xhs")
            )
            with self.assertRaisesRegex(ValueError, "选择"):
                adapter._subprocess_env(platform="dy")

    def test_persistent_douyin_profile_owns_auth_state(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                settings, "crawler_camoufox_state_root", Path(directory).resolve()
            ),
        ):
            account_id = "account-a"
            profile = account_profile_directory(account_id)
            self.assertFalse(account_profile_owns_auth(account_id))
            profile.mkdir(parents=True)
            (profile / "auth-imported").touch()
            self.assertTrue(account_profile_owns_auth(account_id))

            adapter = MediaCrawlerAdapter(Path("/fixture/crawler"))
            oversized = {
                "cookies": [],
                "origins": [
                    {
                        "origin": "https://www.douyin.com",
                        "localStorage": [
                            {"name": "cache", "value": "x" * 200_000},
                        ],
                    },
                ],
            }
            env = adapter._subprocess_env(
                oversized,
                platform="dy",
                account_id=account_id,
            )
            self.assertNotIn("MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64", env)

    def test_uninitialized_douyin_profile_still_receives_auth_state(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                settings, "crawler_camoufox_state_root", Path(directory).resolve()
            ),
        ):
            adapter = MediaCrawlerAdapter(Path("/fixture/crawler"))
            auth_state = {"cookies": [], "origins": []}
            env = adapter._subprocess_env(
                auth_state,
                platform="dy",
                account_id="account-a",
            )
            self.assertNotIn("MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64", env)

    def test_douyin_v1_login_is_disabled_before_child_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            store = CrawlerAccountStore(root / "accounts.sqlite3")
            account = store.create(platform="dy", display_name="fixture")
            cipher = AuthStateCipher(key_file=root / "key")
            manager = CrawlerAccountLoginManager(
                store=store, cipher=cipher, python_path=Path(sys.executable)
            )
            try:
                with self.assertRaisesRegex(ValueError, "Login V2"):
                    manager.start(account)
                self.assertFalse(manager._sessions)
            finally:
                manager.shutdown()

    def test_missing_browser_adapter_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "缺少账号浏览器"):
                load_account_browser(Path(directory).resolve())


@unittest.skipUnless(login is not None, "run with CRAWLER_LOGIN_PYTHON")
class LoginCompletionTest(unittest.IsolatedAsyncioTestCase):
    def test_login_network_diagnostic_keeps_status_and_removes_secrets(self):
        payload = {
            "status_code": 0,
            "message": "waiting confirmation",
            "data": {
                "scan_status": "scanned",
                "confirm_required": True,
                "token": "secret-token",
                "redirect_url": "https://www.douyin.com/?ticket=secret",
                "user_name": "private-name",
            },
        }

        fields = login.safe_login_response_fields(payload)

        self.assertEqual(fields["status_code"], 0)
        self.assertEqual(fields["message"], "waiting confirmation")
        self.assertEqual(fields["data.scan_status"], "scanned")
        self.assertTrue(fields["data.confirm_required"])
        serialized = json.dumps(fields)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("private-name", serialized)
        self.assertNotIn("fixture.invalid", serialized)
        self.assertEqual(
            login.diagnostic_url("https://www.douyin.com/login?token=secret#fragment"),
            "https://www.douyin.com/login",
        )

    def test_qr_image_validation_rejects_photo_and_accepts_qr(self):
        qr = cv2.QRCodeEncoder_create().encode("https://www.douyin.com/login")
        qr = cv2.resize(qr, (240, 240), interpolation=cv2.INTER_NEAREST)
        ok, qr_png = cv2.imencode(".png", qr)
        self.assertTrue(ok)
        self.assertTrue(login.is_qr_png(qr_png.tobytes()))

        photo = np.zeros((170, 171, 3), dtype=np.uint8)
        photo[:, :, 1] = np.linspace(15, 240, 171, dtype=np.uint8)
        ok, photo_png = cv2.imencode(".png", photo)
        self.assertTrue(ok)
        self.assertFalse(login.is_qr_png(photo_png.tobytes()))

    async def test_success_is_emitted_after_profile_is_closed(self):
        order = []
        page = SimpleNamespace(
            set_default_timeout=lambda *_: None, goto=AsyncMock(), on=lambda *_: None
        )
        context = SimpleNamespace(
            new_page=AsyncMock(return_value=page),
            cookies=AsyncMock(return_value=[]),
            storage_state=AsyncMock(return_value={"cookies": [], "origins": []}),
        )

        @asynccontextmanager
        async def slot(*args, **kwargs):
            yield

        context._bound_proxy_session = SimpleNamespace(slot=slot)

        @asynccontextmanager
        async def fixture_context(*args, **kwargs):
            try:
                yield context
            finally:
                order.append("closed")

        with (
            patch.object(login, "login_context", fixture_context),
            patch.object(login, "wait_for_qr", AsyncMock()),
            patch.object(login, "report_douyin_challenge", AsyncMock()),
            patch.object(login, "is_logged_in", AsyncMock(return_value=True)),
            patch.object(
                login,
                "identify_logged_in_account",
                AsyncMock(return_value="identity-a"),
            ),
            patch.object(login, "LOGIN_SETTLE_SECONDS", 0),
            patch.object(login, "emit", lambda kind, **kwargs: order.append(kind)),
        ):
            await login.run_login("dy", 30, "account-a")
        self.assertLess(order.index("closed"), order.index("success"))


@unittest.skipUnless(
    login is not None and os.getenv("CAMOUFOX_BROWSER_SMOKE") == "1",
    "requires downloaded local Camoufox",
)
class RealLoginProfileTest(unittest.IsolatedAsyncioTestCase):
    async def test_reauthentication_reuses_identity_without_importing_old_database_auth(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                settings, "crawler_camoufox_state_root", Path(directory).resolve()
            ),
            patch.object(
                settings, "crawler_camoufox_browser_version", "156.0.1-beta.36"
            ),
            patch.object(login, "request_login_reset", AsyncMock()),
            patch.dict(
                os.environ,
                {"MEDIACRAWLER_CAMOUFOX_PROFILE_GENERATION": "fixture-generation"},
            ),
        ):
            browser = load_account_browser()

            async def fixture_page(context):
                await context.route(
                    "https://www.douyin.com/**",
                    lambda route: route.fulfill(
                        content_type="text/html", body="<html>local fixture</html>"
                    ),
                )
                page = await context.new_page()
                await page.goto("https://www.douyin.com/")
                return page

            original_identity = None
            for value in ("first-login", "second-login"):
                async with login.login_context("dy", "account-a") as context:
                    page = await fixture_page(context)
                    self.assertIsNone(
                        await page.evaluate("localStorage.getItem('session')")
                    )
                    await page.evaluate(
                        "value => localStorage.setItem('session', value)", value
                    )
                    await context.add_cookies(
                        [
                            {
                                "name": "session",
                                "value": value,
                                "url": "https://www.douyin.com/",
                                "expires": int(time.time()) + 86400,
                            }
                        ]
                    )
                env = account_browser_env("account-a")
                env["MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64"] = base64.b64encode(
                    json.dumps(
                        {
                            "cookies": [
                                {
                                    "name": "session",
                                    "value": "old-database",
                                    "domain": ".douyin.com",
                                    "path": "/",
                                }
                            ],
                            "origins": [],
                        }
                    ).encode()
                ).decode()
                with patch.dict(os.environ, env):
                    context, profile = await browser.launch_account_context(
                        "account-a", settings.crawler_camoufox_state_root / "profiles"
                    )
                    try:
                        page = await fixture_page(context)
                        self.assertEqual(
                            await page.evaluate("localStorage.getItem('session')"),
                            value,
                        )
                        self.assertEqual(
                            [
                                cookie["value"]
                                for cookie in await context.cookies()
                                if cookie["name"] == "session"
                            ],
                            [value],
                        )
                        if original_identity:
                            self.assertEqual(
                                original_identity, profile["identity_hash"]
                            )
                        original_identity = profile["identity_hash"]
                        with self.assertRaisesRegex(RuntimeError, "already in use"):
                            await browser.launch_account_context(
                                "account-a",
                                settings.crawler_camoufox_state_root / "profiles",
                            )
                    finally:
                        await context.close()
            with self.assertRaises(asyncio.CancelledError):
                async with login.login_context("dy", "account-a"):
                    raise asyncio.CancelledError()
            with patch.dict(os.environ, env):
                context, _ = await browser.launch_account_context(
                    "account-a", settings.crawler_camoufox_state_root / "profiles"
                )
                try:
                    page = await fixture_page(context)
                    self.assertIsNone(
                        await page.evaluate("localStorage.getItem('session')")
                    )
                    self.assertFalse(await context.cookies())
                finally:
                    await context.close()
            context, other = await browser.launch_account_context(
                "account-b", settings.crawler_camoufox_state_root / "profiles"
            )
            try:
                page = await fixture_page(context)
                self.assertIsNone(
                    await page.evaluate("localStorage.getItem('session')")
                )
                self.assertNotEqual(other["identity_hash"], original_identity)
            finally:
                await context.close()


if __name__ == "__main__":
    unittest.main()
