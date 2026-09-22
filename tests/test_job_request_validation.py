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

    def test_saved_global_parameters_override_diagnostic_entry_and_freeze(self):
        from fastapi import BackgroundTasks
        from backend.audit_agent.job_store import JobStore
        from backend.audit_agent.task_settings import TaskSettingsStore
        jobs = JobStore(self.store.db_path)
        settings_store = TaskSettingsStore(self.store.db_path)
        settings_store.save({'max_notes': 2, 'max_comments': 1, 'analyze_limit': 2,
                             'search_sort': 'most_liked',
                             'analysis_batch_size': 1, 'max_items_per_minute': 1}, 0)
        account = self.store.create(platform='dy', display_name='available')
        self.store.save_auth_state(account['id'], 'encrypted-state')
        with patch.object(main, 'job_store', jobs), \
             patch.object(main, 'AuditPipeline'), \
             patch.object(main, 'enrich_job', side_effect=lambda job: job), \
             patch.object(main, 'create_revision_from_payload', return_value={'version': 1}), \
             patch.object(main.settings, 'm3_posts_per_keyword', 5), \
             patch.object(main.settings, 'm3_comments_per_post', 3), \
             patch.object(main.settings, 'm3_analyze_limit', 10):
            background = BackgroundTasks()
            job = main.create_job(main.CrawlRequest(platform='dy', keyword='维汉夫妻',
                crawler_account_id='stale-per-task-account', max_notes=5), background)
        self.assertEqual(job['max_notes'], 2)
        self.assertEqual(job['crawler_account_id'], account['id'])
        self.assertEqual(job['effective_config']['max_comments'], 1)
        self.assertEqual(job['search_sort'], 'most_liked')
        self.assertEqual(job['effective_config']['search_sort'], 'most_liked')
        self.assertEqual(len(background.tasks), 1)
        settings_store.save({'max_notes': 1}, 1)
        self.assertEqual(jobs.get(job['id'])['max_notes'], 2)

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
        self.assertEqual(main.CrawlRequest().analyze_limit, 0)
        for value in (0, 6):
            with self.assertRaises(ValidationError):
                main.CrawlRequest(max_items_per_minute=value)

    def test_max_notes_accepts_only_strict_integers_from_one_to_thirty(self):
        for value in (1, 5, 30):
            self.assertEqual(main.CrawlRequest(max_notes=value).max_notes, value)
        for value in (0, 31, -1, 1.5, "5", True):
            with self.assertRaises(ValidationError):
                main.CrawlRequest(max_notes=value)

    def test_collection_feature_switches_are_strict_booleans(self):
        request = main.CrawlRequest(
            collect_comments=False,
            collect_media=False,
        )
        self.assertFalse(request.collect_comments)
        self.assertFalse(request.collect_media)
        for field in ("collect_comments", "collect_media"):
            with self.assertRaises(ValidationError):
                main.CrawlRequest(**{field: "false"})

    def test_authoritative_m3_public_job_redacts_internal_crawler_account(self):
        projected = main.redact_authoritative_m3_crawler_account(
            {
                "id": "m3-123456",
                "crawler_account_id": "internal-account-id",
                "crawler_account_display_name": "内部采集账号",
                "requested_config": {
                    "crawler_account_id": "internal-account-id",
                    "max_notes": 1,
                },
                "effective_config": {
                    "crawler_account_id": "internal-account-id",
                    "max_notes": 1,
                },
                "control": {
                    "execution_account": {
                        "id": "internal-account-id",
                        "display_name": "内部采集账号",
                    },
                    "crawl_epoch": 1,
                },
                "logs": [
                    {"time": "t1", "message": "执行账号：内部采集账号"},
                    {"time": "t2", "message": "审核完成 1/1"},
                ],
            }
        )
        self.assertNotIn("crawler_account_id", projected)
        self.assertNotIn("crawler_account_display_name", projected)
        self.assertNotIn("crawler_account_id", projected["requested_config"])
        self.assertNotIn("crawler_account_id", projected["effective_config"])
        self.assertNotIn("execution_account", projected["control"])
        self.assertEqual(projected["control"]["crawl_epoch"], 1)
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
