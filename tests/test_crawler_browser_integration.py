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
from backend.audit_agent.crawler_browser import account_browser_env, load_account_browser
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_login_manager import CrawlerAccountLoginManager
try:
    from scripts import crawler_account_login as login
except ModuleNotFoundError as exc:
    if exc.name != 'playwright':
        raise
    login = None  # The backend env need not contain the separate browser runtime.


class BrowserBindingTest(unittest.TestCase):
    def test_search_and_creator_launch_headless_with_account_binding(self):
        captured = []
        class FinishedProcess:
            returncode = 0
            def __init__(self, command, **kwargs):
                captured.append((command, kwargs['env']))
            def poll(self):
                return 0
        with tempfile.TemporaryDirectory() as directory, \
             patch('backend.audit_agent.crawler_adapter.subprocess.Popen', FinishedProcess):
            adapter = MediaCrawlerAdapter(Path(directory))
            common = dict(platform='dy', account_id='fixture-a', max_notes=1,
                          max_comments=2, max_concurrency=1, max_items_per_minute=5,
                          get_sub_comment=False, save_root=Path(directory) / 'out')
            adapter.run_search(keyword='fixture', start_page=0, **common)
            adapter.run_creator(creator_id='fixture-creator', **common)
        self.assertEqual(len(captured), 2)
        for command, env in captured:
            self.assertEqual(command[-2:], ['--headless', 'true'])
            self.assertEqual(command[command.index('--request_scheduler_db') + 1], str(settings.request_scheduler_db))
            self.assertEqual(command[command.index('--request_concurrency') + 1], str(settings.request_concurrency))
            self.assertEqual(env['MEDIACRAWLER_REQUEST_SCHEDULER_DB'], str(settings.request_scheduler_db))
            self.assertEqual(env['MEDIACRAWLER_ACCOUNT_ID'], 'fixture-a')
            self.assertEqual(env['MEDIACRAWLER_CLOAK_PROFILE_ROOT'],
                             account_browser_env('fixture-a')['MEDIACRAWLER_CLOAK_PROFILE_ROOT'])

    def test_crawl_env_uses_selected_id_and_removes_inherited_account(self):
        adapter = MediaCrawlerAdapter(Path('/fixture/crawler'))
        with patch.dict(os.environ, {'MEDIACRAWLER_ACCOUNT_ID': 'wrong-account',
                                     'MEDIACRAWLER_CLOAK_PROFILE_ROOT': '/wrong-root',
                                     'MEDIACRAWLER_DY_BROWSER_ENGINE': 'playwright'}):
            env = adapter._subprocess_env(platform='dy', account_id='account-a')
            self.assertEqual(env['MEDIACRAWLER_ACCOUNT_ID'], 'account-a')
            self.assertEqual(env['MEDIACRAWLER_CLOAK_PROFILE_ROOT'],
                             str(settings.crawler_browser_profile_root))
            self.assertEqual(env['MEDIACRAWLER_DY_BROWSER_ENGINE'], 'cloakbrowser')
            self.assertNotIn('MEDIACRAWLER_ACCOUNT_ID', adapter._subprocess_env(platform='xhs'))
            with self.assertRaisesRegex(ValueError, '选择'):
                adapter._subprocess_env(platform='dy')

    def test_login_process_receives_same_binding_and_cancel_requires_login(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'login_fixture.py'
            helper.write_text(
                "import os,sys,json,time\n"
                "a=sys.argv[sys.argv.index('--account-id')+1]\n"
                "assert '--headed' not in sys.argv\n"
                "assert os.path.isabs(os.environ['MEDIACRAWLER_REQUEST_SCHEDULER_DB'])\n"
                "assert a == os.environ['MEDIACRAWLER_ACCOUNT_ID']\n"
                "assert os.environ['MEDIACRAWLER_DY_BROWSER_ENGINE']=='cloakbrowser'\n"
                "assert os.environ['CRAWLER_BROWSER_PROFILE_ROOT']==os.environ['MEDIACRAWLER_CLOAK_PROFILE_ROOT']\n"
                "print(json.dumps({'type':'reauth_started'}),flush=True)\n"
                "assert sys.stdin.readline().strip()=='reauth_ready'\n"
                "print(json.dumps({'type':'qr','image_data_url':'data:image/png;base64,AAAA'}),flush=True)\n"
                "time.sleep(15)\n"
            )
            store = CrawlerAccountStore(root / 'accounts.sqlite3')
            account = store.create(platform='dy', display_name='fixture')
            cipher = AuthStateCipher(key_file=root / 'key')
            store.save_auth_state(account['id'], cipher.encrypt({'cookies': [], 'origins': []}))
            manager = CrawlerAccountLoginManager(store=store, cipher=cipher,
                                                 python_path=Path(sys.executable), helper_path=helper)
            try:
                session = manager.start(store.get(account['id']))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    current = manager.get(session['id'])
                    if current['status'] == 'waiting_scan':
                        break
                    time.sleep(0.02)
                self.assertEqual(current['status'], 'waiting_scan')
                manager.cancel(session['id'])
                self.assertEqual(store.get(account['id'])['status'], 'login_required')
            finally:
                manager.shutdown()

    def test_missing_browser_adapter_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, '缺少账号浏览器'):
                load_account_browser(Path(directory))


@unittest.skipUnless(login is not None, 'run with CRAWLER_LOGIN_PYTHON')
class LoginCompletionTest(unittest.IsolatedAsyncioTestCase):
    def test_qr_image_validation_rejects_photo_and_accepts_qr(self):
        qr = cv2.QRCodeEncoder_create().encode("https://fixture.invalid/login")
        qr = cv2.resize(qr, (240, 240), interpolation=cv2.INTER_NEAREST)
        ok, qr_png = cv2.imencode('.png', qr)
        self.assertTrue(ok)
        self.assertTrue(login.is_qr_png(qr_png.tobytes()))

        photo = np.zeros((170, 171, 3), dtype=np.uint8)
        photo[:, :, 1] = np.linspace(15, 240, 171, dtype=np.uint8)
        ok, photo_png = cv2.imencode('.png', photo)
        self.assertTrue(ok)
        self.assertFalse(login.is_qr_png(photo_png.tobytes()))

    async def test_success_is_emitted_after_profile_is_closed(self):
        order = []
        page = SimpleNamespace(set_default_timeout=lambda *_: None, goto=AsyncMock())
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=[]),
                                  storage_state=AsyncMock(return_value={'cookies': [], 'origins': []}))
        @asynccontextmanager
        async def fixture_context(*args, **kwargs):
            try:
                yield context
            finally:
                order.append('closed')
        with patch.object(login, 'login_context', fixture_context), \
             patch.object(login, 'wait_for_qr', AsyncMock()), \
             patch.object(login, 'is_logged_in', AsyncMock(return_value=True)), \
             patch.object(login, 'identify_logged_in_account', AsyncMock(return_value='identity-a')), \
             patch.object(login, 'LOGIN_SETTLE_SECONDS', 0), \
             patch.object(login, 'emit', lambda kind, **kwargs: order.append(kind)):
            await login.run_login('dy', 30, 'account-a')
        self.assertLess(order.index('closed'), order.index('success'))


@unittest.skipUnless(login is not None and os.getenv('CLOAK_BROWSER_SMOKE') == '1', 'requires downloaded local CloakBrowser')
class RealLoginCrawlProfileTest(unittest.IsolatedAsyncioTestCase):
    async def test_relogin_then_actual_crawler_uses_fresh_state_and_same_profile(self):
        # All website responses are local fixtures; no real account or search.
        crawler_root = settings.media_crawler_dir.resolve()
        sys.path.insert(0, str(crawler_root))
        previous_cwd = Path.cwd()
        try:
            os.chdir(crawler_root)  # The crawler's JS assets are resolved from its cwd.
            from media_platform.douyin.core import DouYinCrawler
            module = load_account_browser()
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(settings, 'crawler_browser_profile_root', Path(directory)), \
                 patch.object(login, 'request_login_reset', AsyncMock()):
                async def fixture_page(context):
                    await context.set_offline(True)
                    await context.route('**/*', lambda route: route.fulfill(
                        content_type='text/html', body='<html>local fixture</html>'))
                    page = await context.new_page()
                    await page.goto('https://fixture.invalid/')
                    return page

                original = None
                for value in ('first-login', 'second-login'):
                    async with login.login_context('dy', 'account-a') as context:
                        page = await fixture_page(context)
                        self.assertIsNone(await page.evaluate("localStorage.getItem('session')"))
                        await page.evaluate("v => localStorage.setItem('session', v)", value)
                        await context.add_cookies([{'name': 'session', 'value': value,
                                                   'url': 'https://fixture.invalid/', 'expires': 2000000000}])
                    old = {'cookies': [{'name': 'session', 'value': 'old-database',
                                         'domain': 'fixture.invalid', 'path': '/'}], 'origins': []}
                    env = account_browser_env('account-a')
                    env['MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64'] = base64.b64encode(json.dumps(old).encode()).decode()
                    with patch.dict(os.environ, env):
                        crawler = DouYinCrawler()
                        await crawler._launch_browser_context(None, None, False, False)
                        try:
                            self.assertFalse(await crawler.apply_account_auth_state(crawler.browser_context))
                            page = await fixture_page(crawler.browser_context)
                            self.assertEqual(await page.evaluate("localStorage.getItem('session')"), value)
                            self.assertEqual([c['value'] for c in await crawler.browser_context.cookies()
                                              if c['name'] == 'session'], [value])
                            current = crawler._cloak_profile
                            if original is not None:
                                self.assertEqual(original, current)
                            original = current
                            with self.assertRaisesRegex(RuntimeError, 'already in use'):
                                async with login.login_context('dy', 'account-a'):
                                    pass
                        finally:
                            await crawler._close_current_browser()
                # Cancellation leaves the marker in place and cannot import old credentials.
                with self.assertRaises(asyncio.CancelledError):
                    async with login.login_context('dy', 'account-a'):
                        raise asyncio.CancelledError()
                with patch.dict(os.environ, env):
                    crawler = DouYinCrawler()
                    await crawler._launch_browser_context(None, None, False, False)
                    try:
                        self.assertFalse(await crawler.apply_account_auth_state(crawler.browser_context))
                        page = await fixture_page(crawler.browser_context)
                        self.assertIsNone(await page.evaluate("localStorage.getItem('session')"))
                        self.assertFalse(await crawler.browser_context.cookies())
                    finally:
                        await crawler._close_current_browser()
                b, profile_b = await module.launch_account_context('account-b', Path(directory), headless=True)
                try:
                    page = await fixture_page(b)
                    self.assertIsNone(await page.evaluate("localStorage.getItem('session')"))
                    self.assertNotEqual(profile_b['seed'], original['seed'])
                finally:
                    await b.close()
        finally:
            os.chdir(previous_cwd)
            sys.path.remove(str(crawler_root))


if __name__ == '__main__':
    unittest.main()
