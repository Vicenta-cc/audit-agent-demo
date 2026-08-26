import json
import sqlite3
import unittest
from collections import Counter

from backend.reporting.contracts import ModelStepResult, OutlinePlan, SectionDraft
from backend.reporting.errors import PublishedReportImmutableError, ReportGenerationError
from backend.reporting.graph import NODE_ORDER, ReportGenerationGraph
from backend.reporting.store import ReportStore
from tests.test_domain_query_service import DomainQueryServiceTest


class DeterministicReportModel:
    model = "deterministic-report-test"
    prompt_version = "report-test-v1"

    def __init__(self):
        self.calls = []

    def generate_structured(self, *, messages, response_model, **kwargs):
        payload = self._payload(messages)
        if response_model is OutlinePlan:
            output = self._outline(payload)
            section_id = ""
        elif response_model is SectionDraft:
            output = self._section(payload)
            section_id = payload["section"]["section_id"]
        else:
            raise AssertionError(response_model)
        self.calls.append((response_model.__name__, section_id))
        validated = response_model.model_validate(output)
        return ModelStepResult(
            output=validated.model_dump(mode="json"),
            model=self.model,
            usage={"total_tokens": 1},
        )

    @staticmethod
    def _payload(messages):
        for message in reversed(messages):
            marker = "输入：\n"
            if marker in message["content"]:
                return json.loads(message["content"].split(marker, 1)[1])
        raise AssertionError("prompt payload not found")

    @staticmethod
    def _outline(payload):
        findings = [item["finding_id"] for item in payload["representative_findings"]]
        metrics = [item["metric_key"] for item in payload["statistics"]]
        definitions = (
            ("overview", "overview", "调查概况", "概括本次调查的整体发现"),
            ("risk", "risk_analysis", "整体风险特征", "分析风险等级与类别"),
            ("patterns", "risk_analysis", "主要问题表现", "解释突出的风险模式"),
            ("cases", "case_analysis", "重点风险案例", "综合重点内容及证据"),
            ("synthesis", "synthesis", "综合研判", "结合上下文形成综合判断"),
            ("conclusion", "conclusion", "结论", "形成调查结论和关注建议"),
        )
        sections = []
        for index, (section_id, section_kind, title, purpose) in enumerate(definitions):
            sections.append(
                {
                    "section_id": section_id,
                    "section_kind": section_kind,
                    "title": title,
                    "purpose": purpose,
                    "finding_ids": findings[:1] if findings else [],
                    "metric_refs": metrics[index : index + 1] if index < len(metrics) else [],
                }
            )
        return {
            "report_title": f"{payload['task']['task_name']}调查报告",
            "executive_summary_focus": "风险分布、重点内容与证据覆盖",
            "sections": sections,
        }

    @staticmethod
    def _section(payload):
        section = payload["section"]
        claims = []
        if payload["allowed_metrics"]:
            metric = payload["allowed_metrics"][0]
            claims.append(
                {
                    "claim_id": f"{section['section_id']}-metric",
                    "claim_type": "numeric",
                    "text": f"{metric['label']}为{metric['display_value']}。",
                    "finding_ids": [],
                    "evidence_ids": [],
                    "metric_refs": [metric["metric_key"]],
                    "support_type": "aggregate",
                }
            )
        if payload["allowed_findings_and_evidence"]:
            finding = payload["allowed_findings_and_evidence"][0]
            evidence = finding["evidence"]
            if evidence:
                claims.append(
                    {
                        "claim_id": f"{section['section_id']}-fact",
                        "claim_type": "domain_fact",
                        "text": "重点内容存在可回溯的规范化证据支持。",
                        "finding_ids": [finding["finding"]["finding_id"]],
                        "evidence_ids": [evidence[0]["evidence_id"]],
                        "metric_refs": [],
                        "support_type": "direct",
                    }
                )
        if not claims:
            claims.append(
                {
                    "claim_id": f"{section['section_id']}-method",
                    "claim_type": "methodology",
                    "text": "本节说明本次调查的范围和统计口径。",
                    "finding_ids": [],
                    "evidence_ids": [],
                    "metric_refs": [],
                    "support_type": "aggregate",
                }
            )
        case_blocks = []
        if section["section_kind"] == "case_analysis":
            domain_claim = next(
                item
                for item in claims
                if item["claim_type"] in {"domain_fact", "synthesis"}
            )
            case_blocks.append(
                {
                    "title": "评论互动风险案例",
                    "text": domain_claim["text"],
                    "claim_ids": [domain_claim["claim_id"]],
                }
            )
        paragraph_text = " ".join(item["text"] for item in claims)
        if section["section_kind"] == "conclusion":
            paragraph_text += " 后续应重点关注相关风险互动。"
        return {
            "section_id": section["section_id"],
            "title": section["title"],
            "paragraphs": [
                {
                    "text": paragraph_text,
                    "claim_ids": [item["claim_id"] for item in claims],
                }
            ],
            "claims": claims,
            "case_blocks": case_blocks,
        }


class ReportGraphTest(unittest.TestCase):
    def setUp(self):
        self.fixture = DomainQueryServiceTest("test_task_snapshot_and_overview_share_statistics")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.store = ReportStore(self.fixture.db_path)
        self.model = DeterministicReportModel()
        self.checkpoint_path = self.fixture.root / "checkpoints.sqlite3"

    def _graph(self, **kwargs):
        graph = ReportGenerationGraph(
            query_service=self.fixture.service,
            store=self.store,
            model_client=self.model,
            checkpoint_path=self.checkpoint_path,
            **kwargs,
        )
        self.addCleanup(graph.close)
        return graph

    def test_fixed_graph_publishes_traceable_version(self):
        self.assertEqual(
            NODE_ORDER,
            (
                "freeze_source_snapshot",
                "build_statistics",
                "select_findings",
                "plan_outline",
                "draft_sections",
                "validate_numbers",
                "validate_claim_support",
                "validate_citations",
                "assemble_report",
                "publish_report_version",
            ),
        )
        result = self._graph().generate(self.fixture.task_id)
        full = self.store.get_full_version(result.report_version_id)

        self.assertEqual(result.status, "published")
        self.assertEqual(full["source_snapshot"]["task_id"], self.fixture.task_id)
        self.assertEqual(len(full["source_snapshot"]["finding_ids"]), 3)
        self.assertGreater(len(full["source_snapshot"]["evidence_ids"]), 0)
        self.assertEqual(len(full["sections"]), 6)
        self.assertGreater(len(full["claims"]), 0)
        self.assertGreater(len(full["claim_findings"]), 0)
        self.assertGreater(len(full["claim_evidence"]), 0)
        human_report = self.store.get_human_report(result.report_version_id)
        self.assertIsNotNone(human_report)
        self.assertEqual(
            set(human_report),
            {
                "presentation_version",
                "title",
                "summary",
                "key_metrics",
                "sections",
                "case_blocks",
                "conclusion",
                "data_quality_note",
            },
        )
        markdown = result.body_markdown
        for forbidden in (
            "finding:audit_result:",
            "evidence:audit_result:",
            "metric:",
            "metric_ref",
            "可核验声明",
            "来源哈希",
            "来源快照",
        ):
            self.assertNotIn(forbidden, markdown)
        self.assertIn("【查看相关证据】", markdown)
        stored_claim_ids = {item["id"] for item in full["claims"]}
        action_claim_ids = {
            action["claim_id"]
            for case in human_report["case_blocks"]
            for action in case["citation_actions"]
        }
        self.assertTrue(action_claim_ids)
        self.assertTrue(action_claim_ids <= stored_claim_ids)
        self.assertIn("24.0", ReportGenerationGraph._metric_number_forms(24))

    def test_two_generations_create_immutable_versions(self):
        graph = self._graph()
        first = graph.generate(self.fixture.task_id)
        first_before = self.store.get_full_version(first.report_version_id)
        second = graph.generate(self.fixture.task_id)
        first_after = self.store.get_full_version(first.report_version_id)
        report = self.store.get_report_for_task(self.fixture.task_id)
        versions = self.store.list_versions(report["id"])

        self.assertEqual([item["version_number"] for item in versions], [1, 2])
        self.assertEqual([item["status"] for item in versions], ["published", "published"])
        self.assertEqual(first_before["content_hash"], first_after["content_hash"])
        self.assertNotEqual(first.report_version_id, second.report_version_id)
        with self.assertRaises(PublishedReportImmutableError):
            self.store.assert_published_immutable(first.report_version_id)
        full = self.store.get_full_version(first.report_version_id)
        mutations = (
            ("UPDATE report_source_snapshots SET source_hash = source_hash WHERE id = ?", full["source_snapshot"]["id"]),
            ("UPDATE report_sections SET title = title WHERE id = ?", full["sections"][0]["id"]),
            ("UPDATE report_claims SET text = text WHERE id = ?", full["claims"][0]["id"]),
            (
                "UPDATE report_claim_findings SET finding_id = finding_id WHERE claim_id = ?",
                full["claim_findings"][0]["claim_id"],
            ),
            (
                "UPDATE report_claim_evidence SET citation_excerpt = citation_excerpt WHERE claim_id = ?",
                full["claim_evidence"][0]["claim_id"],
            ),
        )
        for statement, identifier in mutations:
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.store._connect() as connection:
                        connection.execute(statement, (identifier,))

    def test_checkpoint_resume_reuses_successful_section_steps(self):
        graph = self._graph(fail_once_section_index=3)
        with self.assertRaises(ReportGenerationError):
            graph.generate(self.fixture.task_id)
        with self.store._connect() as connection:
            run_id = connection.execute(
                "SELECT id FROM report_generation_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()[0]
        calls_before_resume = list(self.model.calls)
        result = graph.resume(run_id)
        events = self.store.list_run_events(run_id)
        model_steps = self.store.list_model_steps(result.report_version_id)
        started = Counter(
            item["node_name"] for item in events if item["event"] == "started"
        )

        self.assertEqual(result.status, "published")
        self.assertEqual(started["freeze_source_snapshot"], 1)
        self.assertEqual(started["build_statistics"], 1)
        self.assertEqual(started["select_findings"], 1)
        self.assertEqual(started["plan_outline"], 1)
        self.assertEqual(started["draft_sections"], 2)
        self.assertEqual(len(calls_before_resume), 4)  # outline plus first three sections
        self.assertEqual(len(self.model.calls), 7)  # outline plus six sections, no duplicates
        self.assertEqual(sum(item["status"] == "succeeded" for item in model_steps), 7)
        reused_sections = {
            item["detail"]["section_id"]
            for item in events
            if item["event"] == "model_step_reused"
        }
        self.assertEqual(reused_sections, {"overview", "risk", "patterns"})


if __name__ == "__main__":
    unittest.main()
