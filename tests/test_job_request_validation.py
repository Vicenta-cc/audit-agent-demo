import atexit
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_JOB_VALIDATION_TEST_ROOT = Path(
    tempfile.mkdtemp(prefix="job-request-validation-tests-", dir="/tmp")
)
os.environ["XHS_AUDIT_DATA_DIR"] = str(_JOB_VALIDATION_TEST_ROOT / "data")
os.environ["XHS_AUDIT_OUTPUTS_DIR"] = str(_JOB_VALIDATION_TEST_ROOT / "outputs")
os.environ["HERMES_HOME"] = str(_JOB_VALIDATION_TEST_ROOT / "hermes")
os.environ["PYTHONPYCACHEPREFIX"] = str(_JOB_VALIDATION_TEST_ROOT / "pycache")
sys.pycache_prefix = os.environ["PYTHONPYCACHEPREFIX"]
os.environ["TMPDIR"] = str(_JOB_VALIDATION_TEST_ROOT / "tmp")
(_JOB_VALIDATION_TEST_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
atexit.register(shutil.rmtree, _JOB_VALIDATION_TEST_ROOT, True)

from fastapi import HTTPException
from pydantic import ValidationError

from backend import main
from backend.audit_agent.crawler_account_store import CrawlerAccountStore


class JobRequestValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = CrawlerAccountStore(Path(self.temp_dir.name) / "audit.sqlite3")
        self.store_patch = patch.object(main, "crawler_account_store", self.store)
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.temp_dir.cleanup()

    def assert_http_error(self, account_id: str, platform: str, status_code: int):
        with self.assertRaises(HTTPException) as raised:
            main.validate_crawler_account_for_job(account_id, platform)
        self.assertEqual(raised.exception.status_code, status_code)

    def test_legacy_request_without_account_remains_valid(self):
        self.assertIsNone(main.validate_crawler_account_for_job(None, "xhs"))

    def test_rejects_missing_and_platform_mismatched_accounts(self):
        self.assert_http_error("missing", "xhs", 404)
        account = self.store.create(platform="dy", display_name="抖音账号")
        self.store.save_auth_state(account["id"], "encrypted-state")
        self.assert_http_error(account["id"], "xhs", 400)

    def test_rejects_unavailable_or_missing_login_state(self):
        no_login = self.store.create(platform="xhs", display_name="未登录账号")
        self.store.update(no_login["id"], status="active")
        self.assert_http_error(no_login["id"], "xhs", 409)

        disabled = self.store.create(platform="xhs", display_name="停用账号")
        self.store.save_auth_state(disabled["id"], "encrypted-state")
        self.store.update(disabled["id"], status="disabled")
        self.assert_http_error(disabled["id"], "xhs", 409)

    def test_accepts_active_matching_account_with_login_state(self):
        account = self.store.create(platform="ks", display_name="快手账号")
        self.store.save_auth_state(account["id"], "encrypted-state")
        selected = main.validate_crawler_account_for_job(account["id"], "ks")
        self.assertEqual(selected["id"], account["id"])

    def test_rejects_account_during_verify_cooldown(self):
        account = self.store.create(platform="dy", display_name="抖音冷却账号")
        self.store.save_auth_state(account["id"], "encrypted-state")
        self.store.mark_cooldown(
            account["id"],
            "ACCOUNT_VERIFY",
            "9999-12-31T00:00:00",
            failure_kind="verify",
        )

        with self.assertRaises(HTTPException) as raised:
            main.validate_crawler_account_for_job(account["id"], "dy")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("平台验证", str(raised.exception.detail))

    def test_frequency_defaults_to_five_and_rejects_out_of_range(self):
        self.assertEqual(main.CrawlRequest().max_items_per_minute, 5)
        self.assertEqual(main.CrawlRequest().analyze_limit, 10000)
        for value in (0, 6):
            with self.assertRaises(ValidationError):
                main.CrawlRequest(max_items_per_minute=value)

    def test_authoritative_m3_public_job_redacts_internal_crawler_account(self):
        projected = main.redact_authoritative_m3_crawler_account(
            {
                "id": "m3-123456",
                "crawler_account_id": "internal-account-id",
                "crawler_account_display_name": "内部采集账号",
                "logs": [
                    {"time": "t1", "message": "执行账号：内部采集账号"},
                    {"time": "t2", "message": "审核完成 1/1"},
                ],
            }
        )
        self.assertNotIn("crawler_account_id", projected)
        self.assertNotIn("crawler_account_display_name", projected)
        self.assertEqual(projected["logs"], [{"time": "t2", "message": "审核完成 1/1"}])

    def test_legacy_public_job_keeps_existing_account_contract(self):
        job = {
            "id": "legacy-job",
            "crawler_account_id": "legacy-account",
            "crawler_account_display_name": "旧任务账号",
            "logs": [],
        }
        self.assertIs(main.redact_authoritative_m3_crawler_account(job), job)


if __name__ == "__main__":
    unittest.main()
