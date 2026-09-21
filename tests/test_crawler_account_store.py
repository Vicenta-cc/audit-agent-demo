import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore


class CrawlerAccountStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = CrawlerAccountStore(Path(self.temp_dir.name) / "audit.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_account_lifecycle(self):
        account = self.store.create(
            platform="xhs",
            display_name="小红书采集号 A",
            platform_account_id="xhs-a",
        )

        self.assertEqual(account["status"], "login_required")
        self.assertEqual(self.store.list()[0]["display_name"], "小红书采集号 A")

        updated = self.store.update(
            account["id"],
            display_name="小红书主账号",
            status="disabled",
        )

        self.assertEqual(updated["display_name"], "小红书主账号")
        self.assertEqual(updated["status"], "disabled")
        self.assertTrue(self.store.delete(account["id"]))
        self.assertIsNone(self.store.get(account["id"]))

    def test_existing_accounts_migrate_to_private_scope(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript(
                """
                CREATE TABLE crawler_accounts (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    platform_account_id TEXT,
                    status TEXT NOT NULL DEFAULT 'login_required',
                    last_validated_at TEXT,
                    last_used_at TEXT,
                    last_error TEXT,
                    cooldown_until TEXT,
                    failure_kind TEXT,
                    auth_state_ciphertext TEXT,
                    auth_state_updated_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO crawler_accounts (
                    id, platform, display_name, status, created_at, updated_at
                ) VALUES (
                    'legacy-account', 'dy', '旧账号', 'login_required',
                    '2026-09-20T00:00:00', '2026-09-20T00:00:00'
                );
                """
            )

        migrated = CrawlerAccountStore(legacy_path)

        self.assertEqual(migrated.get("legacy-account")["access_scope"], "private")

    def test_available_accounts_prioritize_public_scope(self):
        private = self.store.create(
            platform="dy",
            display_name="私有账号",
            access_scope="private",
        )
        public = self.store.create(
            platform="dy",
            display_name="公共账号",
            access_scope="public",
        )
        self.store.save_auth_state(private["id"], "private-state")
        self.store.save_auth_state(public["id"], "public-state")

        available = self.store.available_accounts("dy")

        self.assertEqual([item["id"] for item in available], [public["id"], private["id"]])

    def test_rejects_duplicate_platform_account_id(self):
        self.store.create(
            platform="dy",
            display_name="抖音账号 A",
            platform_account_id="same-id",
        )

        with self.assertRaisesRegex(ValueError, "已存在"):
            self.store.create(
                platform="dy",
                display_name="抖音账号 B",
                platform_account_id="SAME-ID",
            )

        other_platform = self.store.create(
            platform="ks",
            display_name="快手账号",
            platform_account_id="same-id",
        )
        self.assertEqual(other_platform["platform"], "ks")

    def test_validates_platform_and_name(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.store.create(platform="unknown", display_name="未知账号")

        with self.assertRaisesRegex(ValueError, "不能为空"):
            self.store.create(platform="xhs", display_name="  ")

    def test_saves_only_encrypted_auth_state(self):
        cipher = AuthStateCipher(key_file=Path(self.temp_dir.name) / "auth.key")
        account = self.store.create(platform="xhs", display_name="登录账号")
        auth_state = {
            "cookies": [{"name": "web_session", "value": "plain-cookie-secret"}],
            "origins": [],
        }

        updated = self.store.save_auth_state(account["id"], cipher.encrypt(auth_state))

        self.assertEqual(updated["status"], "active")
        self.assertTrue(updated["has_auth_state"])
        self.assertNotIn("auth_state_ciphertext", updated)
        ciphertext = self.store.get_auth_state_ciphertext(account["id"])
        self.assertNotIn("plain-cookie-secret", ciphertext)
        self.assertEqual(cipher.decrypt(ciphertext), auth_state)

    def test_saves_recognized_platform_account_id_with_auth_state(self):
        account = self.store.create(platform="dy", display_name="自动识别账号")

        updated = self.store.save_auth_state(
            account["id"],
            "encrypted-state",
            platform_account_id="MS4wLjABAAAA-recognized-user",
        )

        self.assertEqual(
            updated["platform_account_id"],
            "MS4wLjABAAAA-recognized-user",
        )
        self.assertTrue(updated["has_auth_state"])

    def test_successful_login_clears_previous_cooldown(self):
        account = self.store.create(platform="dy", display_name="验证冷却账号")
        self.store.save_auth_state(account["id"], "first-encrypted-state")
        self.store.mark_cooldown(
            account["id"],
            "ACCOUNT_VERIFY",
            "9999-12-31T00:00:00",
            failure_kind="verify",
        )

        updated = self.store.save_auth_state(account["id"], "refreshed-encrypted-state")

        self.assertEqual(updated["status"], "active")
        self.assertEqual(updated["cooldown_until"], "")
        self.assertEqual(updated["failure_kind"], "")
        self.assertEqual(updated["last_error"], "")

    def test_duplicate_recognized_id_does_not_save_login_state(self):
        self.store.create(
            platform="xhs",
            display_name="已有账号",
            platform_account_id="5f58bd990000000001003753",
        )
        duplicate = self.store.create(platform="xhs", display_name="重复登录账号")

        with self.assertRaisesRegex(ValueError, "已存在"):
            self.store.save_auth_state(
                duplicate["id"],
                "encrypted-state",
                platform_account_id="5f58bd990000000001003753",
            )

        unchanged = self.store.get(duplicate["id"])
        self.assertEqual(unchanged["status"], "login_required")
        self.assertFalse(unchanged["has_auth_state"])

    def test_tracks_usage_and_marks_invalid_login_state_expired(self):
        account = self.store.create(platform="dy", display_name="任务执行账号")
        self.store.save_auth_state(account["id"], "encrypted-state")

        self.store.mark_used(account["id"])
        used = self.store.get(account["id"])
        self.assertTrue(used["last_used_at"])
        self.assertEqual(used["status"], "active")

        self.store.mark_expired(account["id"], "登录态失效")
        expired = self.store.get(account["id"])
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["last_error"], "登录态失效")


if __name__ == "__main__":
    unittest.main()
