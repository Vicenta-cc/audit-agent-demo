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

    def test_list_results_supports_offset_pagination(self):
        job = self.jobs.create(display_name="分页任务")
        for index in range(5):
            self.create_result(job["id"], f"note-{index}")

        first_page = self.results.list_results(job["id"], limit=2, offset=0, sort="latest")
        second_page = self.results.list_results(job["id"], limit=2, offset=2, sort="latest")

        self.assertEqual(first_page["total"], 5)
        self.assertEqual([item["content_key"] for item in first_page["items"]], ["note-4", "note-3"])
        self.assertEqual([item["content_key"] for item in second_page["items"]], ["note-2", "note-1"])

    def test_job_summaries_omit_items_and_limit_logs(self):
        job = self.jobs.create(display_name="轻量任务", items=[{"payload": "large-value"}])
        for index in range(6):
            self.jobs.log(job["id"], f"日志 {index}")

        summary = self.jobs.get_summary(job["id"], log_limit=3)
        listed = self.jobs.list_summaries(log_limit=2)[0]

        self.assertEqual(summary["items"], [])
        self.assertEqual([log["message"] for log in summary["logs"]], ["日志 3", "日志 4", "日志 5"])
        self.assertEqual(listed["items"], [])
        self.assertEqual([log["message"] for log in listed["logs"]], ["日志 4", "日志 5"])
        self.assertEqual(self.jobs.get(job["id"])["items"], [{"payload": "large-value"}])
        self.assertTrue(self.jobs.exists(job["id"]))
        self.assertFalse(self.jobs.exists("missing-job"))

    def test_compact_list_results_omit_detail_payloads(self):
        job = self.jobs.create(display_name="轻量列表任务")
        self.results.upsert_result(
            job_id=job["id"],
            platform="dy",
            content_key="compact-note",
            result={
                "note_id": "compact-note",
                "content_title": "列表标题",
                "summary": "列表摘要",
                "decision": "review",
                "risk_level": "high",
                "author": {"nickname": "测试作者"},
                "comments": [{"content": "不应进入列表响应"}],
                "video_results": [{"duration": 12.5, "timeline_frames": [{"asset_rel": "frame.png"}]}],
                "evidence_groups": [{"id": "comment", "type": "comment", "count": 3, "items": [{"text": "详情"}]}],
                "evidence_items": [{"risk_library_id": "hate", "risk_library_label": "风险库", "text": "详情"}],
            },
        )

        item = self.results.list_results(job["id"], limit=1, compact=True)["items"][0]

        self.assertEqual(item["content_title"], "列表标题")
        self.assertEqual(item["author"]["nickname"], "测试作者")
        self.assertEqual(item["evidence_groups"], [{"id": "comment", "type": "comment", "count": 3}])
        self.assertEqual(item["evidence_items"], [{"risk_library_id": "hate", "risk_library_label": "风险库"}])
        self.assertEqual(item["thumbnail_asset"], {"asset_rel": "frame.png"})
        self.assertEqual(item["duration_seconds"], 12.5)
        self.assertNotIn("comments", item)
        self.assertNotIn("video_results", item)


if __name__ == "__main__":
    unittest.main()
