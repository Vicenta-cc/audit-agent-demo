import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.domain.contracts import FindingFilters
from backend.domain.errors import (
    DomainAccessDeniedError,
    DomainObjectNotFoundError,
    DomainQueryParameterError,
    DomainUnsupportedFormatError,
    EvidenceTypeNotFoundError,
    FindingHasNoEvidenceError,
)
from backend.domain.pagination import Pagination
from backend.domain.query_contracts import AccessContext, EvidenceContextOptions
from backend.domain.query_service import DomainQueryService
from backend.domain.repository import DomainRepository
from backend.domain.warnings import DataQuality


class DomainQueryServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "audit.sqlite3"
        self.outputs_dir = self.root / "outputs"
        self.task_id = "task-query"
        self._create_database()
        repository = DomainRepository(self.db_path, outputs_dir=self.outputs_dir)
        self.service = DomainQueryService(repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_database(self):
        task_root = self.outputs_dir / self.task_id
        asset_dir = task_root / "assets"
        raw_dir = task_root / "raw"
        asset_dir.mkdir(parents=True)
        raw_dir.mkdir(parents=True)
        for name in ("frame.jpg", "ocr.jpg"):
            (asset_dir / name).write_bytes(b"asset")

        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    platform TEXT,
                    display_name TEXT,
                    current_audit_config_revision_id TEXT,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                    note_id TEXT,
                    url TEXT,
                    title TEXT,
                    author_key TEXT,
                    author_json TEXT,
                    decision TEXT,
                    risk_level TEXT,
                    categories_json TEXT,
                    summary TEXT,
                    result_json TEXT NOT NULL,
                    audit_config_revision_id TEXT,
                    review_status TEXT,
                    review_note TEXT,
                    analyzed_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO jobs VALUES (?, 'completed', 'xhs', '查询测试', 'rev-1', 0, '2026-01-01', '2026-01-02')",
                (self.task_id,),
            )
            connection.execute(
                "INSERT INTO jobs VALUES ('other-task', 'completed', 'xhs', '其他', '', 0, '2026-01-01', '2026-01-02')"
            )

            for content_id in (10, 11, 12):
                raw_path = raw_dir / f"{content_id}.json"
                raw_path.write_text("{}", encoding="utf-8")
                connection.execute(
                    "INSERT INTO contents VALUES (?, ?, ?)",
                    (content_id, f"content-{content_id}", str(raw_path)),
                )
                connection.execute(
                    "INSERT INTO task_contents VALUES (?, ?, ?, ?)",
                    (content_id, self.task_id, content_id, str(raw_path)),
                )

            comments = [
                {"comment_id": "c0", "content": "前一条"},
                {"comment_id": "c1", "content": "原始评论", "translation_zh": "评论译文"},
                {"comment_id": "c2", "content": "后一条"},
                {"comment_id": "c3", "content": "另一条证据"},
            ]
            first = {
                "risk_score": 85,
                "primary_risk": "违规引流",
                "categories": ["违规引流"],
                "risk_basis": "命中联系方式",
                "desc": "正文前缀 联系方式 正文后缀",
                "comments": comments,
                "evidence_items": [
                    {
                        "evidence_id": "comment-1",
                        "primary_modality": "comment",
                        "source": "comment:c1",
                        "comment_id": "c1",
                        "text": "原始评论",
                        "translation_zh": "评论译文",
                    },
                    {
                        "evidence_id": "comment-2",
                        "primary_modality": "comment",
                        "source": "comment:c3",
                        "comment_id": "c3",
                        "text": "另一条证据",
                    },
                    {
                        "evidence_id": "ocr-1",
                        "primary_modality": "ocr",
                        "source": "video_frame:10",
                        "ocr_text": "OCR原文",
                        "ocr_text_zh": "OCR译文",
                        "timestamp": 10,
                        "asset_rel": "assets/ocr.jpg",
                    },
                    {
                        "evidence_id": "asr-1",
                        "primary_modality": "asr",
                        "source": "video_audio:20-25",
                        "text": "ASR原文",
                        "start": 20,
                        "end": 25,
                    },
                    {
                        "evidence_id": "frame-1",
                        "primary_modality": "keyframe",
                        "timestamp": 30,
                        "frame_asset_rel": "assets/frame.jpg",
                        "evidence": "关键帧描述",
                    },
                ],
                "evidence_index": {
                    "ocr_items": [
                        {"id": "ocr-context", "ocr_text": "OCR上下文", "timestamp": 12}
                    ],
                    "asr_segments": [
                        {"id": "asr-context", "text": "ASR上下文", "start": 18, "end": 22}
                    ],
                    "timeline_frames": [
                        {"frame_id": "near-frame", "timestamp": 31, "asset_rel": "assets/frame.jpg"}
                    ],
                },
                "rule_matches": [{"rule_id": "rule-contact", "evidence_ids": ["comment-1"]}],
                "score_breakdown": [{"rule_id": "rule-contact", "score": 85}],
            }
            second = {"risk_score": 0, "categories": [], "summary": "正常内容"}
            third = {
                "risk_score": 20,
                "primary_risk": "视觉风险",
                "categories": ["视觉风险"],
                "evidence_index_path": str(task_root / "missing-index.json"),
                "evidence_items": [
                    {
                        "evidence_id": "visual-broken",
                        "primary_modality": "visual",
                        "source": "image:0",
                        "asset_rel": "../escape.jpg",
                        "evidence": "内嵌视觉描述",
                    }
                ],
                "evidence_index": {"evidence_catalog": []},
            }
            rows = (
                (1, 10, "review", "high", ["违规引流"], "高风险", first, "author-a", "第一条"),
                (2, 11, "pass", "none", [], "正常", second, "author-b", "第二条"),
                (3, 12, "review", "low", ["视觉风险"], "降级", third, "author-a", "第三条"),
            )
            for audit_id, content_id, decision, risk, categories, summary, result, author, title in rows:
                connection.execute(
                    """
                    INSERT INTO audit_results VALUES (
                        ?, ?, ?, 'xhs', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rev-1', '', '',
                        '2026-01-02T00:00:00', '2026-01-02T00:00:00', '2026-01-02T00:00:00'
                    )
                    """,
                    (
                        audit_id,
                        self.task_id,
                        content_id,
                        f"content-{content_id}",
                        f"note-{audit_id}",
                        f"https://example/{audit_id}",
                        title,
                        author,
                        json.dumps({"nickname": author}),
                        decision,
                        risk,
                        json.dumps(categories, ensure_ascii=False),
                        summary,
                        json.dumps(result, ensure_ascii=False),
                    ),
                )

    def test_task_snapshot_and_overview_share_statistics(self):
        snapshot = self.service.get_task_snapshot(self.task_id)
        overview = self.service.get_task_overview(self.task_id)

        self.assertEqual(snapshot.data.content_count, 3)
        self.assertEqual(snapshot.data.audit_result_count, 3)
        self.assertEqual(snapshot.data.decision_distribution, {"pass": 1, "review": 2})
        self.assertEqual(overview.data.audit_result_count, snapshot.data.audit_result_count)
        self.assertEqual(overview.metadata["derived_from"], snapshot.data.source_hash)
        self.assertEqual(snapshot.data.snapshot_kind, "recalculable_domain_query_snapshot")

    def test_missing_objects_and_malformed_ids_are_distinct_errors(self):
        with self.assertRaises(DomainObjectNotFoundError):
            self.service.get_task_snapshot("missing")
        with self.assertRaises(DomainObjectNotFoundError):
            self.service.get_finding_detail("finding:audit_result:999")
        with self.assertRaises(DomainObjectNotFoundError):
            self.service.get_evidence_detail("evidence:audit_result:1:missing")
        with self.assertRaises(DomainQueryParameterError):
            self.service.get_finding_detail("bad-id")
        with self.assertRaises(DomainQueryParameterError):
            self.service.get_evidence_detail("bad-id")

    def test_access_context_is_reserved_and_enforced(self):
        restricted = DomainQueryService(
            self.service.repository,
            access_context=AccessContext(allowed_task_ids=("other-task",)),
        )
        with self.assertRaises(DomainAccessDeniedError):
            restricted.get_task_overview(self.task_id)

    def test_search_uses_and_semantics_and_evidence_type_or_semantics(self):
        result = self.service.search_findings(
            self.task_id,
            FindingFilters(
                decision="review",
                risk_score_min=80,
                risk_score_max=90,
                rule_id="rule-contact",
                evidence_types=("comment", "asr"),
                source_platform="xhs",
                author="author-a",
            ),
            Pagination(page=1, page_size=10, sort_by="risk_score", sort_order="desc"),
        )

        self.assertEqual(result.data.total, 1)
        self.assertEqual(result.data.items[0].audit_result_id, 1)
        self.assertEqual(result.data.filter_combination, "AND")
        self.assertEqual(result.data.evidence_types_combination, "OR")

    def test_search_pagination_sort_and_empty_success(self):
        first = self.service.search_findings(
            self.task_id,
            FindingFilters(decision="review"),
            Pagination(page=1, page_size=1, sort_by="risk_score", sort_order="desc"),
        )
        second = self.service.search_findings(
            self.task_id,
            FindingFilters(decision="review"),
            Pagination(page=2, page_size=1, sort_by="risk_score", sort_order="desc"),
        )
        empty = self.service.search_findings(
            self.task_id,
            FindingFilters(risk_level="impossible"),
            Pagination(),
        )

        self.assertEqual(first.data.items[0].audit_result_id, 1)
        self.assertEqual(second.data.items[0].audit_result_id, 3)
        self.assertEqual(empty.data.items, ())
        self.assertEqual(empty.data.total, 0)
        self.assertEqual(empty.metadata["query_status"], "success")

    def test_invalid_filter_range_and_pagination(self):
        with self.assertRaises(DomainQueryParameterError):
            self.service.search_findings(
                self.task_id,
                FindingFilters(risk_score_min=90, risk_score_max=10),
                Pagination(),
            )
        with self.assertRaises(DomainQueryParameterError):
            self.service.search_findings(self.task_id, FindingFilters(), {"page": 0})

    def test_decision_and_evidence_aggregation_have_explicit_denominators(self):
        decision = self.service.aggregate_findings(
            self.task_id, FindingFilters(), ["decision"], ["count", "percentage"]
        )
        evidence = self.service.aggregate_findings(
            self.task_id,
            FindingFilters(),
            ["evidence_type"],
            ["finding_count", "evidence_count", "percentage"],
        )

        review_count = next(
            row for row in decision.data.rows if row.group == {"decision": "review"} and row.metric == "count"
        )
        comment_finding = next(
            row
            for row in evidence.data.rows
            if row.group == {"evidence_type": "comment"} and row.metric == "finding_count"
        )
        comment_evidence = next(
            row
            for row in evidence.data.rows
            if row.group == {"evidence_type": "comment"} and row.metric == "evidence_count"
        )
        comment_percentage = next(
            row
            for row in evidence.data.rows
            if row.group == {"evidence_type": "comment"} and row.metric == "percentage"
        )
        self.assertEqual((review_count.value, review_count.denominator), (2, 3))
        self.assertEqual(comment_finding.value, 1)
        self.assertEqual(comment_evidence.value, 2)
        self.assertEqual(comment_percentage.denominator, 3)
        self.assertEqual(comment_percentage.percentage_basis, "finding_count")

    def test_finding_detail_and_evidence_briefs(self):
        detail = self.service.get_finding_detail("finding:audit_result:1")
        briefs = self.service.get_finding_evidence(
            "finding:audit_result:1", ["comment"], limit=10
        )

        self.assertEqual(detail.data.post.title, "第一条")
        self.assertEqual(detail.data.finding.matched_rule_ids, ("rule-contact",))
        self.assertEqual(len(detail.data.score_breakdown), 1)
        self.assertEqual(len(briefs.data), 2)
        self.assertTrue(all(item.evidence_type.value == "comment" for item in briefs.data))

    def test_no_evidence_and_missing_type_are_not_empty_successes(self):
        with self.assertRaises(FindingHasNoEvidenceError):
            self.service.get_finding_evidence("finding:audit_result:2")
        with self.assertRaises(EvidenceTypeNotFoundError):
            self.service.get_finding_evidence("finding:audit_result:1", ["visual"])

    def test_unsupported_evidence_format_is_not_reported_as_no_evidence(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE audit_results SET result_json = ? WHERE id = 2",
                (json.dumps({"evidence_items": "invalid"}),),
            )
        with self.assertRaises(DomainUnsupportedFormatError):
            self.service.get_finding_evidence("finding:audit_result:2")

    def test_evidence_context_units_and_content(self):
        evidence = self.service.get_finding_evidence("finding:audit_result:1").data
        by_type = {item.evidence_type.value: item.evidence_id for item in evidence}
        by_type["comment"] = next(
            item.evidence_id for item in evidence if item.evidence_id.endswith(":comment-1")
        )

        comment = self.service.get_evidence_detail(by_type["comment"], 1).data
        ocr = self.service.get_evidence_detail(by_type["ocr"], 3).data
        asr = self.service.get_evidence_detail(
            by_type["asr"], EvidenceContextOptions(time_window_seconds=5)
        ).data
        frame = self.service.get_evidence_detail(
            by_type["keyframe"], EvidenceContextOptions(time_window_seconds=2, related_resource_limit=2)
        ).data

        self.assertEqual(comment.original_text, "原始评论")
        self.assertEqual(comment.translated_text, "评论译文")
        self.assertEqual([item.original_text for item in comment.context], ["前一条", "后一条"])
        self.assertIn("time_seconds", ocr.context_semantics)
        self.assertEqual(ocr.context[0].original_text, "OCR上下文")
        self.assertEqual(asr.context[0].original_text, "ASR上下文")
        self.assertEqual(frame.context[0].source_id, "near-frame")

    def test_degraded_embedded_content_and_illegal_asset_path(self):
        evidence = self.service.get_finding_evidence("finding:audit_result:3").data[0]
        detail = self.service.get_evidence_detail(evidence.evidence_id)

        warning_codes = {warning.code for warning in detail.warnings}
        self.assertIn(DataQuality.DEGRADED_EXTERNAL_FILE_MISSING, warning_codes)
        self.assertIn(DataQuality.BROKEN_REFERENCE, warning_codes)
        self.assertFalse(detail.data.asset_available)
        self.assertTrue(detail.data.structured_content_available)
        self.assertEqual(detail.data.availability.value, "degraded")
        self.assertEqual(detail.data.asset_path, "")

    def test_finding_evidence_link_validation(self):
        evidence = self.service.get_finding_evidence("finding:audit_result:1").data[0]
        valid = self.service.validate_finding_evidence_link(
            "finding:audit_result:1", evidence.evidence_id
        )
        cross = self.service.validate_finding_evidence_link(
            "finding:audit_result:2", evidence.evidence_id
        )

        self.assertTrue(valid.data.valid)
        self.assertTrue(valid.data.same_audit_result)
        self.assertTrue(valid.data.same_task)
        self.assertFalse(cross.data.valid)
        self.assertFalse(cross.data.same_audit_result)
        self.assertTrue(cross.data.same_task)


if __name__ == "__main__":
    unittest.main()
