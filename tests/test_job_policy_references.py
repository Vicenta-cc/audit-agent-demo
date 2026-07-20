import tempfile
import unittest
from pathlib import Path

from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
from backend.audit_agent.job_store import JobStore


class JobPolicyReferencesTest(unittest.TestCase):
    def test_lists_only_active_job_reference_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            jobs = JobStore(db_path)
            revisions = TaskAuditConfigRevisionStore(db_path)
            job = jobs.create(display_name="测试监控任务")
            revision = revisions.create(
                job_id=job["id"],
                source_policy_id="policy_test",
                source_policy_name="测试方案",
                config_hash="hash",
            )
            jobs.update(job["id"], current_audit_config_revision_id=revision["id"])

            references = jobs.list_policy_references()

            self.assertEqual(len(references), 1)
            self.assertEqual(references[0]["id"], job["id"])
            self.assertEqual(references[0]["source_policy_id"], "policy_test")
            self.assertNotIn("items", references[0])
            self.assertNotIn("logs", references[0])


if __name__ == "__main__":
    unittest.main()
