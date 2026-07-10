import tempfile
import unittest
from pathlib import Path

from backend.audit_agent.ingestion import AuditResultStore
from backend.audit_agent.job_store import JobStore


class AuditResultDeletionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.sqlite3"
        self.jobs = JobStore(self.db_path)
        self.results = AuditResultStore(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_result(self, job_id: str, content_key: str = "note-1") -> dict:
        return self.results.upsert_result(
            job_id=job_id,
            platform="dy",
            content_key=content_key,
            result={
                "note_id": content_key,
                "title": "测试帖子",
                "decision": "review",
                "risk_level": "medium",
            },
        )

    def test_archived_job_results_are_hidden_and_can_be_deleted(self):
        job = self.jobs.create(display_name="待删除任务")
        result = self.create_result(job["id"])
        self.assertTrue(result.get("id"))
        self.assertEqual(self.results.list_results()["total"], 1)

        self.jobs.archive(job["id"])

        self.assertEqual(self.results.list_results()["items"], [])
        self.assertIsNone(self.results.get_result(result["id"]))
        with self.assertRaises(KeyError):
            self.results.review_result(result["id"], status="confirmed")
        self.assertEqual(self.results.delete_for_archived_jobs(), 1)
        self.assertEqual(self.results.delete_for_archived_jobs(), 0)

    def test_delete_for_job_only_removes_that_jobs_results(self):
        deleted_job = self.jobs.create(display_name="待删除任务")
        retained_job = self.jobs.create(display_name="保留任务")
        self.create_result(deleted_job["id"], "delete-note")
        retained = self.create_result(retained_job["id"], "retain-note")

        self.assertEqual(self.results.delete_for_job(deleted_job["id"]), 1)
        self.assertEqual(self.results.list_results()["total"], 1)
        self.assertEqual(self.results.get_result(retained["id"])["job_id"], retained_job["id"])

    def test_late_result_is_not_written_after_job_is_archived(self):
        job = self.jobs.create(display_name="分析中的任务")
        self.jobs.archive(job["id"])

        persisted = self.create_result(job["id"], "late-note")

        self.assertEqual(persisted, {})
        self.assertEqual(self.results.list_results()["total"], 0)


if __name__ == "__main__":
    unittest.main()
