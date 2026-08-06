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
