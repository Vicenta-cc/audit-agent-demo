import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_login_manager import CrawlerAccountLoginManager, LoginSession


class CrawlerAccountLoginManagerTest(unittest.TestCase):
    def test_scanned_state_clears_qr_and_remains_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = CrawlerAccountStore(root / "audit.sqlite3")
            manager = CrawlerAccountLoginManager(
                store=store,
                cipher=AuthStateCipher(key_file=root / "auth.key"),
                python_path=Path(sys.executable),
                helper_path=root / "unused-login.py",
            )
            account = store.create(platform="dy", display_name="测试账号")
            now = datetime.now(timezone.utc).isoformat()
            manager._sessions["scan-session"] = LoginSession(
                id="scan-session", account_id=account["id"], platform="dy",
                status="waiting_scan", created_at=now, updated_at=now,
                expires_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
                qr_image_data_url="data:image/png;base64,SYNTHETIC",
            )

            manager._handle_event("scan-session", {"type": "scanned"})

            session = manager.get("scan-session")
            self.assertEqual(session["status"], "scanned")
            self.assertEqual(session["qr_image_data_url"], "")
            manager.cancel("scan-session")

    def test_expired_server_session_is_terminal_and_qr_is_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = CrawlerAccountStore(root / "audit.sqlite3")
            manager = CrawlerAccountLoginManager(
                store=store,
                cipher=AuthStateCipher(key_file=root / "auth.key"),
                python_path=Path(sys.executable),
                helper_path=root / "unused-login.py",
                timeout_seconds=60,
            )
            account = store.create(platform="xhs", display_name="测试账号")
            now = datetime.now(timezone.utc)
            manager._sessions["expired-session"] = LoginSession(
                id="expired-session",
                account_id=account["id"],
                platform="xhs",
                status="waiting_scan",
                created_at=(now - timedelta(minutes=2)).isoformat(),
                updated_at=(now - timedelta(minutes=2)).isoformat(),
                expires_at=(now - timedelta(seconds=1)).isoformat(),
                qr_image_data_url="data:image/png;base64,SYNTHETIC",
            )

            session = manager.get("expired-session")

            self.assertIsNotNone(session)
            self.assertEqual(session["status"], "expired")
            self.assertEqual(session["qr_image_data_url"], "")
            self.assertIn("超时", session["error"])
            self.assertEqual(store.get(account["id"])["status"], "expired")
            manager.shutdown()

    def test_login_process_can_import_project_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            helper = root / "import_backend.py"
            helper.write_text(
                "import json\n"
                "from backend.audit_agent.crawler_account_identity import extract_platform_account_id\n"
                "print(json.dumps({'type': 'success', 'auth_state': {'cookies': [], 'origins': []}, "
                "'platform_account_id': extract_platform_account_id('xhs', "
                "'https://www.xiaohongshu.com/user/profile/test-user')}), flush=True)\n",
                encoding="utf-8",
            )
            store = CrawlerAccountStore(root / "audit.sqlite3")
            manager = CrawlerAccountLoginManager(
                store=store,
                cipher=AuthStateCipher(key_file=root / "auth.key"),
                python_path=Path(sys.executable),
                helper_path=helper,
                timeout_seconds=60,
            )
            account = store.create(platform="xhs", display_name="测试账号")

            session = manager.start(account)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                session = manager.get(session["id"])
                if session and session["status"] == "success":
                    break
                time.sleep(0.02)

            self.assertEqual(session["status"], "success")
            self.assertEqual(session["platform_account_id"], "test-user")
            manager.shutdown()

    def test_missing_playwright_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            helper = root / "missing_playwright.py"
            helper.write_text(
                "raise ModuleNotFoundError(\"No module named 'playwright'\")\n",
                encoding="utf-8",
            )
            store = CrawlerAccountStore(root / "audit.sqlite3")
            manager = CrawlerAccountLoginManager(
                store=store,
                cipher=AuthStateCipher(key_file=root / "auth.key"),
                python_path=Path(sys.executable),
                helper_path=helper,
                timeout_seconds=60,
            )
            account = store.create(platform="xhs", display_name="测试账号")

            session = manager.start(account)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                session = manager.get(session["id"])
                if session and session["status"] == "failed":
                    break
                time.sleep(0.02)

            self.assertEqual(session["status"], "failed")
            self.assertEqual(
                session["error"],
                "登录运行环境缺少 Playwright，请检查 CRAWLER_LOGIN_PYTHON",
            )
            manager.shutdown()

    def test_qr_session_encrypts_successful_storage_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            helper = root / "fake_login.py"
            auth_state = {
                "cookies": [{"name": "web_session", "value": "login-secret"}],
                "origins": [],
            }
            helper.write_text(
                "import json, sys, time\n"
                "print(json.dumps({'type': 'qr', 'image_data_url': 'data:image/png;base64,AAAA', "
                "'expires_at': '2099-01-01T00:00:00+00:00'}), flush=True)\n"
                "sys.stderr.write('browser diagnostic without a trailing newline')\n"
                "sys.stderr.flush()\n"
                "print(json.dumps({'type': 'finalizing', 'duration_seconds': 3}), flush=True)\n"
                "time.sleep(0.08)\n"
                f"print(json.dumps({{'type': 'success', 'auth_state': {auth_state!r}, "
                "'platform_account_id': '5f58bd990000000001003753'}), flush=True)\n",
                encoding="utf-8",
            )
            store = CrawlerAccountStore(root / "audit.sqlite3")
            cipher = AuthStateCipher(key_file=root / "auth.key")
            manager = CrawlerAccountLoginManager(
                store=store,
                cipher=cipher,
                python_path=Path(sys.executable),
                helper_path=helper,
                timeout_seconds=60,
            )
            account = store.create(platform="xhs", display_name="测试账号")

            session = manager.start(account)
            deadline = time.monotonic() + 3
            saw_finalizing = False
            while time.monotonic() < deadline:
                session = manager.get(session["id"])
                saw_finalizing = saw_finalizing or bool(
                    session and session["status"] == "finalizing"
                )
                if session and session["status"] == "success":
                    break
                time.sleep(0.02)

            self.assertTrue(saw_finalizing)
            self.assertEqual(session["status"], "success")
            self.assertEqual(session["platform_account_id"], "5f58bd990000000001003753")
            saved = store.get_auth_state_ciphertext(account["id"])
            self.assertNotIn("login-secret", saved)
            self.assertEqual(cipher.decrypt(saved), auth_state)
            self.assertEqual(store.get(account["id"])["status"], "active")
            self.assertEqual(
                store.get(account["id"])["platform_account_id"],
                "5f58bd990000000001003753",
            )
            manager.shutdown()

    def test_only_one_browser_login_can_run_at_a_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            helper = root / "slow_login.py"
            helper.write_text(
                "import json, time\n"
                "print(json.dumps({'type': 'qr', 'image_data_url': 'data:image/png;base64,AAAA'}), flush=True)\n"
                "time.sleep(10)\n",
                encoding="utf-8",
            )
            store = CrawlerAccountStore(root / "audit.sqlite3")
            manager = CrawlerAccountLoginManager(
                store=store,
                cipher=AuthStateCipher(key_file=root / "auth.key"),
                python_path=Path(sys.executable),
                helper_path=helper,
                timeout_seconds=60,
            )
            first = store.create(platform="xhs", display_name="账号一")
            second = store.create(platform="dy", display_name="账号二")

            manager.start(first)
            with self.assertRaisesRegex(ValueError, "已有账号正在登录"):
                manager.start(second)

            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
