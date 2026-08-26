import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.domain.contracts import FindingFilters
from backend.domain.identity import make_evidence_id, make_finding_id
from backend.domain.repository import DomainRepository


class DomainRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "audit.sqlite3"
        self.outputs_dir = self.root / "outputs"
        self.task_id = "task-1"
        self._create_database()
        self.repository = DomainRepository(self.db_path, outputs_dir=self.outputs_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_database(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE contents (
                    id INTEGER PRIMARY KEY,
                    content_key TEXT NOT NULL,
                    raw_item_path TEXT
                );
                CREATE TABLE task_contents (
                    id INTEGER PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    content_id INTEGER NOT NULL,
                    raw_item_path TEXT,
                    UNIQUE(task_id, content_id)
                );
                CREATE TABLE audit_results (
                    id INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    content_id INTEGER,
                    platform TEXT NOT NULL,
                    content_key TEXT NOT NULL,
                    decision TEXT,
                    risk_level TEXT,
                    categories_json TEXT,
                    summary TEXT,
                    result_json TEXT NOT NULL
                );
                """
            )
            connection.execute("INSERT INTO jobs (id, archived) VALUES (?, 0)", (self.task_id,))
            connection.execute("INSERT INTO jobs (id, archived) VALUES ('archived-task', 1)")
            for content_id in (10, 11):
                raw_path = self.outputs_dir / self.task_id / "raw_items" / f"{content_id}.json"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text("{}", encoding="utf-8")
                connection.execute(
                    "INSERT INTO contents (id, content_key, raw_item_path) VALUES (?, ?, ?)",
                    (content_id, f"content-{content_id}", str(raw_path)),
                )
                connection.execute(
                    "INSERT INTO task_contents (id, task_id, content_id, raw_item_path) VALUES (?, ?, ?, ?)",
                    (content_id, self.task_id, content_id, str(raw_path)),
                )

            asset_path = self.outputs_dir / self.task_id / "assets" / "image.jpg"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(b"image")
            first_result = {
                "risk_score": 60,
                "primary_risk": "违规引流",
                "categories": ["违规引流"],
                "summary": "存在联系方式",
                "evidence_items": [{
                    "evidence_id": "selected:1",
                    "primary_modality": "ocr",
                    "source": "image:0",
                    "ocr_text": "联系我",
                    "asset_rel": "assets/image.jpg",
                }],
                "evidence_index": {"evidence_catalog": [{
                    "evidence_id": "catalog:1",
                    "primary_modality": "ocr",
                    "source": "image:0",
                    "ocr_text": "联系我",
                    "asset_rel": "assets/image.jpg",
                }]},
                "rule_matches": [{
                    "rule_id": "ocr-contact",
                    "evidence_ids": ["selected:1"],
                }],
            }
            second_result = {
                "risk_score": 0,
                "categories": [],
                "summary": "正常内容",
            }
            connection.execute(
                """
                INSERT INTO audit_results
                    (id, job_id, content_id, platform, content_key, decision, risk_level, categories_json, summary, result_json)
                VALUES (1, ?, 10, 'xhs', 'content-10', 'review', 'medium', ?, '存在联系方式', ?)
                """,
                (self.task_id, json.dumps(["违规引流"]), json.dumps(first_result)),
            )
            connection.execute(
                """
                INSERT INTO audit_results
                    (id, job_id, content_id, platform, content_key, decision, risk_level, categories_json, summary, result_json)
                VALUES (2, ?, 11, 'xhs', 'content-11', 'pass', 'none', '[]', '正常内容', ?)
                """,
                (self.task_id, json.dumps(second_result)),
            )

    def test_get_finding_view_uses_stable_identity_and_top_level_fields(self):
        envelope = self.repository.get_finding_view(1)
        finding = envelope.data

        self.assertEqual(finding.finding_id, make_finding_id(1))
        self.assertEqual(finding.task_id, self.task_id)
        self.assertEqual(finding.content_id, 10)
        self.assertEqual(finding.decision, "review")
        self.assertEqual(finding.risk_level, "medium")
        self.assertEqual(finding.risk_score, 60)
        self.assertEqual(finding.matched_rule_ids, ("ocr-contact",))
        self.assertEqual(len(finding.evidence_ids), 1)
        self.assertEqual(envelope.sources[0].finding_id, finding.finding_id)

    def test_list_finding_views_filters_and_paginates(self):
        first_page = self.repository.list_finding_views(
            self.task_id,
            FindingFilters(risk_level="medium", category="违规引流"),
            offset=0,
            limit=1,
        )

        self.assertEqual(first_page.data.total, 1)
        self.assertEqual(len(first_page.data.items), 1)
        self.assertEqual(first_page.data.items[0].audit_result_id, 1)

    def test_get_evidence_view_accepts_merged_local_id_alias(self):
        alias_id = make_evidence_id(1, "catalog:1")

        envelope = self.repository.get_evidence_view(alias_id)

        self.assertEqual(envelope.data.local_evidence_id, "selected:1")
        self.assertIn("catalog:1", envelope.data.local_evidence_aliases)

    def test_validate_finding_evidence_link(self):
        finding_id = make_finding_id(1)
        evidence_id = self.repository.get_finding_view(1).data.evidence_ids[0]

        valid = self.repository.validate_finding_evidence_link(finding_id, evidence_id)
        cross_result = self.repository.validate_finding_evidence_link(
            finding_id,
            make_evidence_id(2, "selected:1"),
        )

        self.assertTrue(valid.data.valid)
        self.assertFalse(cross_result.data.valid)
        self.assertIn("different audit results", cross_result.data.reason)

    def test_pass_finding_has_no_synthetic_evidence(self):
        finding = self.repository.get_finding_view(2).data
        evidence = self.repository.get_evidence_views(2).data

        self.assertEqual(finding.evidence_ids, ())
        self.assertEqual(evidence, ())

    def test_database_connection_is_query_only(self):
        with self.repository._connect() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE audit_results SET summary = 'changed' WHERE id = 1")


if __name__ == "__main__":
    unittest.main()
