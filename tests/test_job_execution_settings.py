import tempfile
import unittest
from pathlib import Path

from backend.audit_agent.job_store import JobStore


class JobExecutionSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.temp_dir.name) / "audit.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_persists_account_snapshot_and_rate_limit(self):
        job = self.store.create(
            platform="xhs",
            crawler_account_id="account-1",
            crawler_account_display_name="巡查账号",
            display_name="测试任务",
            max_concurrency=3,
            max_items_per_minute=4,
            get_sub_comment=False,
            run_crawler=True,
        )

        stored = self.store.get(job["id"])
        self.assertEqual(stored["crawler_account_id"], "account-1")
        self.assertEqual(stored["crawler_account_display_name"], "巡查账号")
        self.assertEqual(stored["max_items_per_minute"], 4)
        self.assertEqual(stored["max_concurrency"], 3)

    def test_legacy_job_defaults_rate_and_allows_no_account(self):
        job = self.store.create(
            platform="dy",
            display_name="旧客户端任务",
            max_concurrency=1,
            get_sub_comment=False,
            run_crawler=True,
        )

        stored = self.store.get(job["id"])
        self.assertIsNone(stored["crawler_account_id"])
        self.assertEqual(stored["max_items_per_minute"], 5)


if __name__ == "__main__":
    unittest.main()
