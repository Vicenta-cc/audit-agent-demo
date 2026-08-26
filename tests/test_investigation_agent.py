import hashlib
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import requests
from pydantic import ValidationError

from backend.audit_agent.config import settings
from backend.domain.identity import stable_hash
from backend.investigation.agent import InvestigationAgentService
from backend.investigation.artifacts import (
    EvidenceArtifactCompiler,
    EvidenceCanonicalCapture,
)
from backend.investigation.capabilities import (
    QueryCapabilityResolver,
    QueryCapabilityScope,
    QueryReceiptConflictError,
)
from backend.investigation.contracts import (
    BoundEvidenceCollectionRequirement,
    FindingEvidenceQueryDetails,
    PlannerInputSnapshot,
    QwenChatResult,
    QwenUsage,
    ReadySourceBundle,
    ResolvedReference,
    SubmitTurnPlanInput,
    SubmitTurnPlanToolInput,
    ToolCall,
    ToolResultEnvelope,
)
from backend.investigation.errors import (
    CheckpointScopeMismatchError,
    ConcurrentTurnError,
    QwenChatError,
    ReportNotFoundError,
    ReportScopeError,
    ToolProtocolError,
)
from backend.investigation.protocol import validate_model_request_messages
from backend.investigation.planner import (
    PLANNER_PROMPT_VERSION,
    PLANNER_SYSTEM_PROMPT,
    SUBMIT_TURN_PLAN_TOOL,
    TurnPlanner,
)
from backend.investigation.orchestrator import SourceOrchestrator, SubjectBinder
from backend.investigation.qwen_chat_tool_client import QwenChatToolClient
from backend.investigation.report_query import ReportQueryFacade
from backend.investigation.store import InvestigationStore
from backend.investigation.tools import InvestigationToolService
from backend.reporting.graph import ReportGenerationGraph
from backend.reporting.store import ReportStore
from tests import test_domain_query_service as domain_query_tests
from tests.test_report_graph import DeterministicReportModel


V3_REPORT_VERSION_ID = "report-version:0b44280b029d4504a27d1811954bd2b8"
V3_CASE_CLAIM_ID = "report-claim:ff7da6f12058fd5314cee4c2c150478b"
V3_FINDING_ID = "finding:audit_result:214"
V3_TEXT_EVIDENCE_ID = "evidence:audit_result:214:ev_text_001"
V3_COMMENT_EVIDENCE_ID = "evidence:audit_result:214:ev_comment_001"
REAL_AUDIT_DB_PATH = settings.data_dir / "audit_index.sqlite3"

PLANNER_GOLD_PATH = Path(__file__).parent / "fixtures" / "planner_shadow_gold.json"
PLANNER_V11_BLIND_PATH = (
    Path(__file__).parent / "fixtures" / "planner_shadow_v1_1_blind.json"
)
PLANNER_V12_REGRESSION_PATH = (
    Path(__file__).parent / "fixtures" / "planner_shadow_v1_2_regression.json"
)
PLANNER_V12_BLIND_PATH = (
    Path(__file__).parent / "fixtures" / "planner_shadow_v1_2_blind.json"
)


class ScriptedChatModel:
    model = "scripted-chat-test"

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def append(self, *responses):
        self.responses.extend(responses)

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("scripted response queue is empty")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(kwargs)
        return item


def result(
    request_id,
    *,
    content="",
    tool_calls=(),
    input_tokens=20,
    output_tokens=5,
):
    return QwenChatResult(
        content=content,
        tool_calls=tuple(tool_calls),
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=QwenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        model="scripted-chat-test",
        request_id=request_id,
    )


class InvestigationFixtureTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.domain_fixture = domain_query_tests.DomainQueryServiceTest(
            "test_task_snapshot_and_overview_share_statistics"
        )
        self.domain_fixture.setUp()
        self.addCleanup(self.domain_fixture.tearDown)
        self.report_store = ReportStore(self.domain_fixture.db_path)
        report_graph = ReportGenerationGraph(
            query_service=self.domain_fixture.service,
            store=self.report_store,
            model_client=DeterministicReportModel(),
            checkpoint_path=self.root / "report-checkpoints.sqlite3",
        )
        self.addCleanup(report_graph.close)
        generated = report_graph.generate(self.domain_fixture.task_id)
        self.report_version_id = generated.report_version_id
        self.facade = ReportQueryFacade(
            self.domain_fixture.db_path,
            query_service=self.domain_fixture.service,
        )
        self.context = self.facade.get_published_report_context(
            self.report_version_id
        )
        body = self.report_store.get_version(self.report_version_id)["body"]
        self.metrics = body["audit_model"]["metrics"]
        self.metric_key = self.metrics[0]["metric_key"]
        self.full_version = self.report_store.get_full_version(self.report_version_id)

    def service(self, model, **kwargs):
        if "shadow_planner" not in kwargs:
            kwargs.setdefault("enable_shadow_planner", False)
        service = InvestigationAgentService(
            report_facade=self.facade,
            store=InvestigationStore(self.root / f"business-{id(model)}.sqlite3"),
            model_client=model,
            checkpoint_path=self.root / f"checkpoint-{id(model)}.sqlite3",
            **kwargs,
        )
        self.addCleanup(service.close)
        return service

    def tool_call(self, call_id, name, arguments):
        return ToolCall(id=call_id, name=name, arguments=arguments)


class ReportQueryFacadeTest(InvestigationFixtureTest):
    def test_published_context_and_claim_support_are_version_scoped(self):
        self.assertEqual(self.context.task_id, self.domain_fixture.task_id)
        self.assertGreater(len(self.context.finding_ids), 0)
        self.assertGreater(len(self.context.evidence_ids), 0)
        presentation = self.facade.get_report_presentation(self.report_version_id)
        self.assertEqual(presentation["presentation_version"], "human-report-v1")
        metric = self.facade.lookup_metric(self.report_version_id, self.metric_key)
        self.assertEqual(metric["metric_key"], self.metric_key)

        claim = next(
            item for item in self.full_version["claims"] if item["claim_type"] == "domain_fact"
        )
        support = self.facade.get_claim_support(self.report_version_id, claim["id"])
        self.assertEqual(support["claim"]["claim_id"], claim["id"])
        self.assertTrue(set(support["finding_ids"]).issubset(self.context.finding_ids))
        self.assertTrue(
            {item["evidence_id"] for item in support["evidence"]}.issubset(
                self.context.evidence_ids
            )
        )

    def test_draft_report_and_out_of_snapshot_ids_fail_closed(self):
        generation = self.report_store.create_generation(
            self.domain_fixture.task_id,
            model="test",
            prompt_version="test",
        )
        with self.assertRaises(ReportNotFoundError):
            self.facade.get_published_report_context(generation["report_version_id"])
        with self.assertRaises(ReportScopeError):
            self.facade.get_finding_detail(
                self.report_version_id, "finding:audit_result:999999"
            )

    def test_facade_uses_read_only_sqlite_connection(self):
        with self.facade._connect() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE report_versions SET title = title")


class InvestigationToolServiceTest(InvestigationFixtureTest):
    def setUp(self):
        super().setUp()
        self.tools = InvestigationToolService(self.facade, max_result_size=8_000)

    def query_receipt(self, call, envelope, *, model_payload=None):
        return self.tools.query_receipt(
            session_id="investigation-session:test",
            turn_id="investigation-turn:test",
            report_version_id=self.report_version_id,
            snapshot_hash=self.context.snapshot_hash,
            call=call,
            result=envelope,
            model_payload=model_payload or self.tools.model_payload(envelope),
        )

    def test_schema_forbids_scope_arguments(self):
        envelope = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call(
                "call-1",
                "lookup_report_metric",
                {"metric_key": self.metric_key, "task_id": self.domain_fixture.task_id},
            ),
        )
        self.assertEqual(envelope.status, "error")
        self.assertEqual(envelope.error.error_code, "invalid_tool_arguments")
        serialized = json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)
        self.assertNotIn(str(self.domain_fixture.db_path), serialized)
        self.assertNotIn("traceback", serialized.lower())

    def test_metric_semantics_and_source_kind_are_explicit(self):
        percentage = next(
            item
            for item in self.metrics
            if item.get("percentage_basis") == "finding_count"
        )
        envelope = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call(
                "call-2", "lookup_report_metric", {"metric_key": percentage["metric_key"]}
            ),
        )
        self.assertEqual(envelope.status, "ok")
        self.assertEqual(envelope.data["returned"], 1)
        metric = envelope.data["metrics"][0]
        self.assertTrue(metric["semantic_definition"])
        self.assertEqual(metric["percentage"], percentage["value"])
        self.assertEqual(metric["percentage_basis"], "finding_count")
        self.assertEqual(envelope.provenance[0].source_kind.value, "frozen_metric")
        self.assertEqual(envelope.provenance[0].freshness, "frozen_snapshot")

    def test_metric_batch_supports_one_multiple_dedup_and_stable_fingerprint(self):
        keys = [item["metric_key"] for item in self.metrics[:2]]
        single = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call("single", "lookup_report_metric", {"metric_keys": [keys[0]]}),
        )
        legacy = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call("legacy", "lookup_report_metric", {"metric_key": keys[0]}),
        )
        duplicate = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call(
                "duplicate", "lookup_report_metric", {"metric_keys": [keys[0], keys[0]]}
            ),
        )
        batch = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call("batch", "lookup_report_metric", {"metric_keys": keys}),
        )
        self.assertEqual(single.status, "ok")
        self.assertEqual(single.data["metric_keys"], [keys[0]])
        self.assertEqual(single.result_fingerprint, legacy.result_fingerprint)
        self.assertEqual(single.result_fingerprint, duplicate.result_fingerprint)
        self.assertEqual(duplicate.data["returned"], 1)
        self.assertEqual(len(duplicate.provenance), 1)
        self.assertEqual(batch.data["metric_keys"], keys)
        self.assertEqual([item["metric_key"] for item in batch.data["metrics"]], keys)
        self.assertEqual(len(batch.provenance), 2)
        self.assertEqual(
            {item.metric_key for item in batch.provenance}, set(keys)
        )
        required = {
            "metric_key", "value", "denominator", "percentage",
            "percentage_basis", "semantic_definition", "dimension", "group",
        }
        self.assertTrue(all(required.issubset(item) for item in batch.data["metrics"]))
        self.assertTrue(all(item.query_fingerprint for item in batch.provenance))

    def test_metric_batch_rejects_empty_limit_unknown_and_extra_fields(self):
        calls = [
            self.tool_call("empty", "lookup_report_metric", {"metric_keys": []}),
            self.tool_call(
                "limit",
                "lookup_report_metric",
                {"metric_keys": [item["metric_key"] for item in self.metrics[:9]]},
            ),
            self.tool_call(
                "extra",
                "lookup_report_metric",
                {"metric_keys": [self.metric_key], "metric_key": self.metric_key},
            ),
        ]
        for call in calls:
            envelope = self.tools.execute(
                report_version_id=self.report_version_id, call=call
            )
            self.assertEqual(envelope.status, "error")
            self.assertEqual(envelope.error.error_code, "invalid_tool_arguments")
        unknown = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call(
                "unknown", "lookup_report_metric", {"metric_keys": ["metric:" + "f" * 64]}
            ),
        )
        self.assertEqual(unknown.status, "error")
        self.assertEqual(unknown.error.error_code, "report_not_found")
        self.assertEqual(unknown.provenance, ())

    def test_model_metric_schema_exposes_only_metric_keys(self):
        definition = next(
            item["function"]
            for item in self.tools.definitions()
            if item["function"]["name"] == "lookup_report_metric"
        )
        properties = definition["parameters"]["properties"]
        self.assertEqual(set(properties), {"metric_keys"})
        self.assertEqual(properties["metric_keys"]["minItems"], 1)
        self.assertEqual(properties["metric_keys"]["maxItems"], 8)
        description = definition["description"]
        self.assertIn("一次最多读取 8 个", description)
        self.assertIn("明确要求完整统计", description)
        self.assertIn("不要为了普通概览拆成多个 batch", description)

    def test_report_presentation_preserves_published_text(self):
        published = self.facade.get_report_presentation(self.report_version_id)
        envelope = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call("presentation", "read_report_presentation", {}),
        )

        def texts(value):
            if isinstance(value, dict):
                return [str(value["text"])] if "text" in value else sum(
                    (texts(item) for item in value.values()), []
                )
            if isinstance(value, list):
                return sum((texts(item) for item in value), [])
            return []

        self.assertEqual(sorted(texts(envelope.data)), sorted(texts(published)))
        self.assertNotIn("semantic_warnings", envelope.data)
        self.assertEqual(
            envelope.data["source_annotation"]["authority"],
            "published_report_expression",
        )

    def test_claim_support_is_compact_and_does_not_expand_all_evidence(self):
        claim = next(
            item
            for item in self.full_version["claims"]
            if self.facade.get_claim_support(self.report_version_id, item["id"])["evidence"]
        )
        raw = self.facade.get_claim_support(self.report_version_id, claim["id"])
        envelope = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call(
                "claim-support", "read_claim_support", {"claim_id": claim["id"]}
            ),
        )
        self.assertEqual(envelope.status, "ok")
        self.assertNotIn("evidence", envelope.data)
        overview = envelope.data["evidence_overview"]
        self.assertEqual(overview["total"], len(raw["evidence"]))
        self.assertLessEqual(overview["returned"], 3)
        self.assertTrue(all("original_text" not in item for item in overview["representative_previews"]))

    def test_list_tools_return_compact_page_metadata(self):
        findings = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call("finding-list", "list_report_findings", {"limit": 1}),
        )
        self.assertEqual(set(findings.data), {"items", "total", "returned", "has_more"})
        self.assertEqual(
            set(findings.data["items"][0]),
            {"finding_id", "label", "risk_level", "decision", "short_summary"},
        )
        evidence = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call(
                "evidence-list",
                "list_finding_evidence",
                {"finding_id": self.context.finding_ids[0], "limit": 1},
            ),
        )
        self.assertEqual(set(evidence.data), {"items", "total", "returned", "has_more"})
        self.assertEqual(
            set(evidence.data["items"][0]),
            {"evidence_id", "type", "preview", "label", "asset_status"},
        )

    def test_evidence_detail_does_not_expose_local_paths(self):
        evidence_id = self.context.evidence_ids[0]
        envelope = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call(
                "call-3", "read_evidence_detail", {"evidence_id": evidence_id}
            ),
        )
        self.assertEqual(envelope.status, "ok")
        serialized = json.dumps(envelope.data, ensure_ascii=False)
        self.assertNotIn("asset_path", serialized)
        self.assertNotIn(str(self.domain_fixture.outputs_dir), serialized)
        self.assertEqual(envelope.data["freshness"], "current_source")
        self.assertEqual(envelope.provenance[0].source_kind.value, "current_evidence")
        current = self.facade.get_evidence_detail(self.report_version_id, evidence_id)["data"]
        self.assertEqual(envelope.data["original_text"], current["original_text"])
        self.assertEqual(envelope.data["translated_text"], current["translated_text"])

    def test_evidence_capture_precedes_bounding_and_never_changes_model_payload(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        tool = InvestigationToolService(self.facade, max_result_size=4_000)
        original_dispatch = tool._dispatch

        def oversized_dispatch(*args, **kwargs):
            data, provenance = original_dispatch(*args, **kwargs)
            items = [dict(item) for item in data["items"]]
            items[0]["preview"] = "x" * 12_000
            return {**data, "items": items}, provenance

        call = self.tool_call(
            "canonical-before-bounding",
            "list_finding_evidence",
            {"finding_id": finding_id, "limit": 20},
        )
        with patch.object(tool, "_dispatch", side_effect=oversized_dispatch):
            envelope, capture = tool.execute_with_canonical(
                report_version_id=self.report_version_id,
                call=call,
            )

        self.assertIsNotNone(capture)
        self.assertEqual(len(capture.canonical_payload["items"][0]["preview"]), 12_000)
        self.assertTrue(envelope.truncated)
        self.assertNotEqual(envelope.data, capture.canonical_payload)
        self.assertEqual(envelope.result_fingerprint, capture.result_fingerprint)
        self.assertLessEqual(
            len(json.dumps(tool.model_payload(envelope), ensure_ascii=False)),
            tool.max_result_size,
        )

        non_evidence, non_evidence_capture = tool.execute_with_canonical(
            report_version_id=self.report_version_id,
            call=self.tool_call(
                "no-artifact-capture", "read_report_presentation", {}
            ),
        )
        self.assertEqual(non_evidence.status, "ok")
        self.assertIsNone(non_evidence_capture)

    def test_canonical_capture_failure_does_not_change_tool_result(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        call = self.tool_call(
            "capture-failure",
            "list_finding_evidence",
            {"finding_id": finding_id, "limit": 20},
        )
        expected = self.tools.execute(
            report_version_id=self.report_version_id,
            call=call,
        )

        with patch(
            "backend.investigation.tools.EvidenceCanonicalCapture",
            side_effect=ValueError("injected capture failure"),
        ):
            actual, capture = self.tools.execute_with_canonical(
                report_version_id=self.report_version_id,
                call=call,
            )

        self.assertEqual(actual, expected)
        self.assertIsNone(capture)

    def test_complete_model_tool_envelope_obeys_size_guard(self):
        claim = next(
            item
            for item in self.full_version["claims"]
            if self.facade.get_claim_support(self.report_version_id, item["id"])["evidence"]
        )
        result_envelope = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call("bounded", "read_claim_support", {"claim_id": claim["id"]}),
        )
        payload = self.tools.model_payload(result_envelope)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertLessEqual(len(serialized), self.tools.max_result_size)
        self.assertIn("result_fingerprint", payload)
        self.assertIn("has_more", payload["provenance"])

    def test_model_provenance_is_compact_but_server_provenance_is_full(self):
        envelope = self.tools.execute(
            report_version_id=self.report_version_id,
            call=self.tool_call("compact-provenance", "read_report_presentation", {}),
        )
        self.assertTrue(any(item.excerpt for item in envelope.provenance))
        payload = self.tools.model_payload(envelope)
        model_sources = payload["provenance"]["items"]
        self.assertTrue(model_sources)
        self.assertTrue(all(item["stable_ref"] for item in model_sources))
        self.assertTrue(all("excerpt" not in item for item in model_sources))

    def test_tool_definitions_are_strict_and_contain_only_allowlisted_tools(self):
        definitions = self.tools.definitions()
        self.assertEqual(
            {item["function"]["name"] for item in definitions},
            self.tools.allowed_tool_names,
        )
        self.assertTrue(all(item["function"]["strict"] for item in definitions))
        serialized = json.dumps(definitions)
        self.assertNotIn("task_id", serialized)
        self.assertNotIn("report_version_id", serialized)
        self.assertNotIn("snapshot_hash", serialized)
        evidence_schema = next(
            item["function"]["parameters"]
            for item in definitions
            if item["function"]["name"] == "list_finding_evidence"
        )
        evidence_types = evidence_schema["properties"]["evidence_types"]["items"]["enum"]
        self.assertIn("visual", evidence_types)
        self.assertNotIn("vision", evidence_types)

    def test_all_read_tools_emit_strongly_typed_query_receipts(self):
        claim = next(
            item
            for item in self.full_version["claims"]
            if self.facade.get_claim_support(self.report_version_id, item["id"])[
                "finding_ids"
            ]
        )
        support = self.facade.get_claim_support(self.report_version_id, claim["id"])
        finding_id = support["finding_ids"][0]
        evidence_page = self.facade.list_finding_evidence(
            self.report_version_id, finding_id, limit=20
        )
        evidence_id = evidence_page["items"][0]["evidence_id"]
        calls = (
            self.tool_call("receipt-presentation", "read_report_presentation", {}),
            self.tool_call(
                "receipt-metric",
                "lookup_report_metric",
                {"metric_keys": [self.metric_key]},
            ),
            self.tool_call(
                "receipt-claim", "read_claim_support", {"claim_id": claim["id"]}
            ),
            self.tool_call("receipt-findings", "list_report_findings", {"limit": 2}),
            self.tool_call(
                "receipt-finding-detail",
                "read_finding_detail",
                {"finding_id": finding_id},
            ),
            self.tool_call(
                "receipt-evidence-list",
                "list_finding_evidence",
                {"finding_id": finding_id, "limit": 20},
            ),
            self.tool_call(
                "receipt-evidence-detail",
                "read_evidence_detail",
                {"evidence_id": evidence_id},
            ),
        )
        receipts = []
        for call in calls:
            envelope = self.tools.execute(
                report_version_id=self.report_version_id, call=call
            )
            self.assertEqual(envelope.status, "ok")
            receipt = self.query_receipt(call, envelope)
            receipts.append(receipt)
            self.assertEqual(receipt.status, "ok")
            self.assertEqual(receipt.tool_call_id, call.id)
            self.assertEqual(receipt.report_version_id, self.report_version_id)
            self.assertTrue(receipt.arguments_fingerprint)
            self.assertTrue(receipt.query_fingerprint)
            self.assertTrue(receipt.result_fingerprint)
            self.assertNotIn(
                "query_receipt",
                json.dumps(self.tools.model_payload(envelope), ensure_ascii=False),
            )

        self.assertEqual(
            [item.operation for item in receipts],
            [
                "report_presentation_read",
                "report_metric_lookup",
                "claim_support_read",
                "report_findings_list",
                "finding_detail_read",
                "finding_evidence_list",
                "evidence_detail_read",
            ],
        )
        claim_receipt = receipts[2]
        self.assertEqual(claim_receipt.details.claim_ref, claim["id"])
        self.assertEqual(
            set(claim_receipt.details.finding_summary_refs), set(support["finding_ids"])
        )
        self.assertEqual(
            set(claim_receipt.details.frozen_citation_refs),
            {item["evidence_id"] for item in support["evidence"]},
        )
        self.assertNotEqual(claim_receipt.operation, "finding_evidence_list")

        evidence_receipt = receipts[5]
        self.assertEqual(evidence_receipt.details.finding_ref, finding_id)
        self.assertEqual(
            set(evidence_receipt.details.returned_evidence_refs),
            {item["evidence_id"] for item in evidence_page["items"]},
        )
        self.assertEqual(evidence_receipt.total, evidence_page["total"])
        self.assertEqual(evidence_receipt.has_more, evidence_page["has_more"])
        detail_receipt = receipts[6]
        self.assertEqual(detail_receipt.details.evidence_ref, evidence_id)
        self.assertEqual(detail_receipt.details.finding_ref, finding_id)
        self.assertNotEqual(detail_receipt.operation, "finding_evidence_list")

    def test_evidence_list_receipts_distinguish_filtered_and_unfiltered_queries(self):
        finding_id = next(
            finding_id
            for finding_id in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, finding_id, limit=20
            )["items"]
        )
        unfiltered_call = self.tool_call(
            "receipt-unfiltered",
            "list_finding_evidence",
            {"finding_id": finding_id, "limit": 20},
        )
        unfiltered_result = self.tools.execute(
            report_version_id=self.report_version_id, call=unfiltered_call
        )
        evidence_type = unfiltered_result.data["items"][0]["type"]
        filtered_call = self.tool_call(
            "receipt-filtered",
            "list_finding_evidence",
            {
                "finding_id": finding_id,
                "evidence_types": [evidence_type],
                "limit": 20,
            },
        )
        filtered_result = self.tools.execute(
            report_version_id=self.report_version_id, call=filtered_call
        )
        unfiltered = self.query_receipt(unfiltered_call, unfiltered_result)
        filtered = self.query_receipt(filtered_call, filtered_result)

        self.assertEqual(unfiltered.query_params["evidence_types"], [])
        self.assertEqual(filtered.query_params["evidence_types"], [evidence_type])
        self.assertNotEqual(
            unfiltered.arguments_fingerprint, filtered.arguments_fingerprint
        )
        self.assertNotEqual(unfiltered.query_fingerprint, filtered.query_fingerprint)
        self.assertEqual(unfiltered.details.evidence_types, ())
        self.assertEqual(filtered.details.evidence_types, (evidence_type,))

    def test_receipt_separates_backend_refs_from_model_visible_refs(self):
        finding_id = self.context.finding_ids[0]
        evidence_refs = tuple(
            f"evidence:audit_result:1:synthetic_{index:03d}" for index in range(12)
        )
        call = self.tool_call(
            "receipt-projection",
            "list_finding_evidence",
            {"finding_id": finding_id, "limit": 20},
        )
        envelope = ToolResultEnvelope(
            status="ok",
            data={"items": [], "total": 12, "returned": 12, "has_more": False},
            result_fingerprint="f" * 64,
            truncated=True,
            query_details=FindingEvidenceQueryDetails(
                operation="finding_evidence_list",
                finding_ref=finding_id,
                returned_evidence_refs=evidence_refs,
                returned_count=12,
                total=12,
                has_more=False,
            ),
        )
        model_payload = {
            "status": "ok",
            "data": {"items": [{"evidence_id": evidence_refs[0]}]},
            "provenance": {"items": [], "total": 12, "returned": 1, "has_more": True},
            "result_fingerprint": "f" * 64,
            "truncated": True,
        }
        receipt = self.query_receipt(
            call, envelope, model_payload=model_payload
        )

        self.assertEqual(receipt.returned_refs, evidence_refs)
        self.assertEqual(receipt.model_visible_refs, (evidence_refs[0],))
        self.assertTrue(receipt.model_output_truncated)

    def test_failed_tool_attempt_emits_non_capability_receipt(self):
        call = self.tool_call(
            "receipt-invalid",
            "list_finding_evidence",
            {"finding_id": "not-a-finding"},
        )
        envelope = self.tools.execute(
            report_version_id=self.report_version_id, call=call
        )
        receipt = self.query_receipt(call, envelope)

        self.assertEqual(receipt.status, "error")
        self.assertEqual(receipt.error_code, "invalid_tool_arguments")
        self.assertEqual(receipt.returned_refs, ())
        self.assertEqual(receipt.model_visible_refs, ())
        self.assertIsNone(receipt.result_count)
        self.assertEqual(receipt.details.operation, "failed_tool_query")


class QueryCapabilityResolverTest(InvestigationFixtureTest):
    session_id = "investigation-session:capability-test"
    turn_id = "investigation-turn:capability-test"

    def setUp(self):
        super().setUp()
        self.tools = InvestigationToolService(self.facade, max_result_size=8_000)
        self.scope = QueryCapabilityScope(
            session_id=self.session_id,
            report_version_id=self.report_version_id,
            snapshot_hash=self.context.snapshot_hash,
        )

    def query_receipt(self, call, envelope, *, model_payload=None):
        return self.tools.query_receipt(
            session_id=self.session_id,
            turn_id=self.turn_id,
            report_version_id=self.report_version_id,
            snapshot_hash=self.context.snapshot_hash,
            call=call,
            result=envelope,
            model_payload=model_payload or self.tools.model_payload(envelope),
        )

    def execute_receipt(self, call, *, model_payload=None):
        envelope = self.tools.execute(
            report_version_id=self.report_version_id, call=call
        )
        return self.query_receipt(call, envelope, model_payload=model_payload)

    def resolver(self, *receipts, scope=None):
        return QueryCapabilityResolver(receipts, scope=scope or self.scope)

    def claim_with_finding_and_evidence(self):
        for claim in self.full_version["claims"]:
            support = self.facade.get_claim_support(
                self.report_version_id, claim["id"]
            )
            if support["finding_ids"] and support["evidence"]:
                return claim["id"], support
        self.fail("fixture has no claim with Finding and frozen Evidence")

    def test_report_and_claim_receipts_project_only_their_query_capabilities(self):
        presentation = self.execute_receipt(
            self.tool_call(
                "capability-presentation", "read_report_presentation", {}
            )
        )
        claim_ref, support = self.claim_with_finding_and_evidence()
        claim = self.execute_receipt(
            self.tool_call(
                "capability-claim", "read_claim_support", {"claim_id": claim_ref}
            )
        )
        resolver = self.resolver(presentation, claim)

        report = resolver.assess_report_presentation(self.report_version_id)
        self.assertTrue(report.backend_complete)
        self.assertEqual(
            report.status,
            "available" if report.tool_result_model_ref_complete else "partial",
        )
        self.assertTrue(resolver.has_claim_support(claim_ref))
        for finding_ref in support["finding_ids"]:
            self.assertTrue(resolver.has_finding_summary(finding_ref))
            self.assertEqual(
                resolver.assess_evidence_collection(finding_ref).status,
                "unavailable",
            )
        for evidence in support["evidence"]:
            self.assertTrue(
                resolver.has_frozen_citation(
                    evidence["evidence_id"], claim_ref=claim_ref
                )
            )

    def test_collection_requires_matching_filter_and_complete_backend_page(self):
        _claim_ref, support = self.claim_with_finding_and_evidence()
        finding_ref = support["finding_ids"][0]
        page = self.facade.list_finding_evidence(
            self.report_version_id, finding_ref, limit=20
        )
        evidence_type = page["items"][0]["evidence_type"]
        unfiltered = self.execute_receipt(
            self.tool_call(
                "capability-all-evidence",
                "list_finding_evidence",
                {"finding_id": finding_ref, "limit": 20},
            )
        )
        filtered = self.execute_receipt(
            self.tool_call(
                "capability-filtered-evidence",
                "list_finding_evidence",
                {
                    "finding_id": finding_ref,
                    "evidence_types": [evidence_type],
                    "limit": 20,
                },
            )
        )

        all_assessment = self.resolver(unfiltered).assess_evidence_collection(
            finding_ref
        )
        self.assertEqual(all_assessment.status, "available")
        self.assertTrue(all_assessment.backend_complete)
        self.assertTrue(all_assessment.tool_result_model_ref_complete)
        self.assertEqual(
            set(all_assessment.returned_refs),
            {item["evidence_id"] for item in page["items"]},
        )
        filtered_resolver = self.resolver(filtered)
        self.assertTrue(
            filtered_resolver.has_evidence_collection(
                finding_ref, evidence_types=[evidence_type, evidence_type]
            )
        )
        self.assertFalse(filtered_resolver.has_evidence_collection(finding_ref))

        partial_details = unfiltered.details.model_copy(
            update={
                "returned_count": len(unfiltered.returned_refs),
                "total": len(unfiltered.returned_refs) + 1,
                "has_more": True,
            }
        )
        partial = unfiltered.model_copy(
            update={
                "receipt_id": "tool-query-receipt:partial-page",
                "tool_call_id": "capability-partial-page",
                "total": len(unfiltered.returned_refs) + 1,
                "has_more": True,
                "details": partial_details,
            }
        )
        partial_assessment = self.resolver(partial).assess_evidence_collection(
            finding_ref
        )
        self.assertEqual(partial_assessment.status, "partial")
        self.assertFalse(partial_assessment.backend_complete)
        self.assertIn(
            "backend_collection_has_more", partial_assessment.reasons
        )

    def test_backend_complete_collection_can_have_incomplete_tool_result_refs(self):
        _claim_ref, support = self.claim_with_finding_and_evidence()
        finding_ref = support["finding_ids"][0]
        complete = self.execute_receipt(
            self.tool_call(
                "capability-model-partial",
                "list_finding_evidence",
                {"finding_id": finding_ref, "limit": 20},
            )
        )
        self.assertTrue(complete.returned_refs)
        synthetic_refs = (
            "evidence:audit_result:1:capability_visible",
            "evidence:audit_result:1:capability_hidden",
        )
        synthetic_details = complete.details.model_copy(
            update={
                "returned_evidence_refs": synthetic_refs,
                "returned_count": 2,
                "total": 2,
                "has_more": False,
            }
        )
        model_partial = complete.model_copy(
            update={
                "receipt_id": "tool-query-receipt:model-partial",
                "returned_refs": synthetic_refs,
                "model_visible_refs": synthetic_refs[:1],
                "result_count": 2,
                "total": 2,
                "has_more": False,
                "model_output_truncated": True,
                "details": synthetic_details,
            }
        )

        assessment = self.resolver(model_partial).assess_evidence_collection(
            finding_ref
        )
        self.assertTrue(assessment.backend_complete)
        self.assertFalse(assessment.tool_result_model_ref_complete)
        self.assertEqual(assessment.status, "partial")
        self.assertIn("tool_result_model_refs_incomplete", assessment.reasons)

    def test_evidence_detail_is_distinct_and_truncation_is_partial(self):
        _claim_ref, support = self.claim_with_finding_and_evidence()
        finding_ref = support["finding_ids"][0]
        evidence_ref = self.facade.list_finding_evidence(
            self.report_version_id, finding_ref, limit=20
        )["items"][0]["evidence_id"]
        detail = self.execute_receipt(
            self.tool_call(
                "capability-detail",
                "read_evidence_detail",
                {"evidence_id": evidence_ref},
            )
        )
        resolver = self.resolver(detail)

        detail_assessment = resolver.assess_evidence_detail(
            evidence_ref, finding_ref=finding_ref
        )
        self.assertEqual(detail_assessment.status, "available")
        self.assertTrue(detail_assessment.tool_result_content_complete)
        self.assertFalse(resolver.has_evidence_collection(finding_ref))

        truncated = detail.model_copy(
            update={
                "receipt_id": "tool-query-receipt:truncated-detail",
                "model_output_truncated": True,
            }
        )
        truncated_assessment = self.resolver(truncated).assess_evidence_detail(
            evidence_ref, finding_ref=finding_ref
        )
        self.assertEqual(truncated_assessment.status, "partial")
        self.assertFalse(truncated_assessment.tool_result_content_complete)
        self.assertIn(
            "tool_result_content_truncated", truncated_assessment.reasons
        )

    def test_failed_and_cross_scope_receipts_do_not_produce_capabilities(self):
        _claim_ref, support = self.claim_with_finding_and_evidence()
        finding_ref = support["finding_ids"][0]
        valid = self.execute_receipt(
            self.tool_call(
                "capability-scope",
                "list_finding_evidence",
                {"finding_id": finding_ref, "limit": 20},
            )
        )
        failed = self.execute_receipt(
            self.tool_call(
                "capability-failed",
                "list_finding_evidence",
                {"finding_id": "not-a-finding"},
            )
        )
        self.assertFalse(self.resolver(failed).has_evidence_collection(finding_ref))
        for error_code in ("not_found", "permission_denied", "tool_error"):
            with self.subTest(error_code=error_code):
                failed_success_shape = valid.model_copy(
                    update={
                        "receipt_id": f"tool-query-receipt:{error_code}",
                        "status": "error",
                        "error_code": error_code,
                    }
                )
                self.assertFalse(
                    self.resolver(failed_success_shape).has_evidence_collection(
                        finding_ref
                    )
                )

        self.assertFalse(
            self.resolver(valid).has_evidence_collection(
                "finding:audit_result:999999"
            )
        )
        inconsistent_subject = valid.model_copy(
            update={
                "receipt_id": "tool-query-receipt:inconsistent-subject",
                "subject_refs": ("finding:audit_result:999999",),
            }
        )
        self.assertFalse(
            self.resolver(inconsistent_subject).has_evidence_collection(
                finding_ref
            )
        )

        mismatched_scopes = (
            QueryCapabilityScope(
                session_id="investigation-session:other",
                report_version_id=self.report_version_id,
                snapshot_hash=self.context.snapshot_hash,
            ),
            QueryCapabilityScope(
                session_id=self.session_id,
                report_version_id="report-version:" + "f" * 32,
                snapshot_hash=self.context.snapshot_hash,
            ),
            QueryCapabilityScope(
                session_id=self.session_id,
                report_version_id=self.report_version_id,
                snapshot_hash="f" * 64,
            ),
        )
        for scope in mismatched_scopes:
            with self.subTest(scope=scope):
                self.assertFalse(
                    self.resolver(valid, scope=scope).has_evidence_collection(
                        finding_ref
                    )
                )

    def test_duplicate_receipts_are_deduplicated_and_conflicts_fail_closed(self):
        claim_ref, _support = self.claim_with_finding_and_evidence()
        receipt = self.execute_receipt(
            self.tool_call(
                "capability-duplicate",
                "read_claim_support",
                {"claim_id": claim_ref},
            )
        )
        assessment = self.resolver(receipt, receipt).assess_claim_support(claim_ref)
        self.assertEqual(assessment.matched_receipt_ids, (receipt.receipt_id,))

        conflicting = receipt.model_copy(update={"error_code": "conflict"})
        with self.assertRaises(QueryReceiptConflictError):
            self.resolver(receipt, conflicting)


@unittest.skipUnless(REAL_AUDIT_DB_PATH.is_file(), "real audit database is not available")
class InvestigationV3EvidenceCharacterizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facade = ReportQueryFacade(REAL_AUDIT_DB_PATH)
        try:
            cls.context = cls.facade.get_published_report_context(V3_REPORT_VERSION_ID)
        except ReportNotFoundError as exc:
            raise unittest.SkipTest("clean V3 published report is not available") from exc
        cls.tools = InvestigationToolService(cls.facade)

    @staticmethod
    def tool_call(call_id, name, arguments):
        return ToolCall(id=call_id, name=name, arguments=arguments)

    def query_receipt(self, call, envelope):
        return self.tools.query_receipt(
            session_id="investigation-session:v3-receipt",
            turn_id="investigation-turn:v3-receipt",
            report_version_id=V3_REPORT_VERSION_ID,
            snapshot_hash=self.context.snapshot_hash,
            call=call,
            result=envelope,
            model_payload=self.tools.model_payload(envelope),
        )

    def test_claim_support_previews_are_the_frozen_claim_relations_not_finding_inventory(self):
        raw_support = self.facade.get_claim_support(
            V3_REPORT_VERSION_ID, V3_CASE_CLAIM_ID
        )
        claim_call = self.tool_call(
            "v3-claim-support",
            "read_claim_support",
            {"claim_id": V3_CASE_CLAIM_ID},
        )
        claim_result = self.tools.execute(
            report_version_id=V3_REPORT_VERSION_ID,
            call=claim_call,
        )
        finding_page = self.facade.list_finding_evidence(
            V3_REPORT_VERSION_ID, V3_FINDING_ID, limit=20
        )

        claim_relation_refs = {
            item["evidence_id"] for item in raw_support["evidence"]
        }
        preview_refs = {
            item["evidence_id"]
            for item in claim_result.data["evidence_overview"][
                "representative_previews"
            ]
        }
        finding_inventory_refs = {
            item["evidence_id"] for item in finding_page["items"]
        }

        self.assertEqual(raw_support["finding_ids"], [V3_FINDING_ID])
        self.assertEqual(
            finding_inventory_refs,
            {V3_TEXT_EVIDENCE_ID, V3_COMMENT_EVIDENCE_ID},
        )
        self.assertEqual(preview_refs, claim_relation_refs)
        self.assertIn(V3_TEXT_EVIDENCE_ID, preview_refs)
        self.assertNotIn(V3_COMMENT_EVIDENCE_ID, preview_refs)
        overview = claim_result.data["evidence_overview"]
        self.assertEqual(overview["total"], len(claim_relation_refs))
        self.assertEqual(overview["returned"], len(preview_refs))
        self.assertEqual(
            overview["has_more"], len(preview_refs) < len(claim_relation_refs)
        )
        self.assertEqual(
            {item.source_kind.value for item in claim_result.provenance},
            {"report_claim", "frozen_citation_excerpt"},
        )
        self.assertNotIn(
            "current_evidence",
            {item.source_kind.value for item in claim_result.provenance},
        )
        receipt = self.query_receipt(claim_call, claim_result)
        self.assertEqual(receipt.operation, "claim_support_read")
        self.assertEqual(receipt.details.claim_ref, V3_CASE_CLAIM_ID)
        self.assertEqual(receipt.details.finding_summary_refs, (V3_FINDING_ID,))
        self.assertEqual(
            receipt.details.frozen_citation_refs, (V3_TEXT_EVIDENCE_ID,)
        )
        self.assertNotIn(V3_COMMENT_EVIDENCE_ID, receipt.returned_refs)

    def test_finding_214_evidence_list_is_complete_and_finding_local(self):
        call = self.tool_call(
            "v3-finding-evidence",
            "list_finding_evidence",
            {"finding_id": V3_FINDING_ID, "limit": 20},
        )
        result = self.tools.execute(
            report_version_id=V3_REPORT_VERSION_ID,
            call=call,
        )
        returned_refs = {item["evidence_id"] for item in result.data["items"]}

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            returned_refs,
            {V3_TEXT_EVIDENCE_ID, V3_COMMENT_EVIDENCE_ID},
        )
        self.assertEqual(result.data["total"], 2)
        self.assertEqual(result.data["returned"], 2)
        self.assertFalse(result.data["has_more"])
        self.assertEqual(
            {item.evidence_id for item in result.provenance}, returned_refs
        )
        self.assertEqual(
            {item.finding_id for item in result.provenance}, {V3_FINDING_ID}
        )
        self.assertEqual(
            {item.source_kind.value for item in result.provenance},
            {"current_evidence"},
        )
        receipt = self.query_receipt(call, result)
        self.assertEqual(receipt.operation, "finding_evidence_list")
        self.assertEqual(receipt.subject_refs, (V3_FINDING_ID,))
        self.assertEqual(set(receipt.returned_refs), returned_refs)
        self.assertEqual(receipt.total, 2)
        self.assertFalse(receipt.has_more)

    def test_v3_filtered_collection_and_detail_are_distinct_receipts(self):
        filtered_call = self.tool_call(
            "v3-comment-evidence",
            "list_finding_evidence",
            {
                "finding_id": V3_FINDING_ID,
                "evidence_types": ["comment"],
                "limit": 20,
            },
        )
        filtered_result = self.tools.execute(
            report_version_id=V3_REPORT_VERSION_ID, call=filtered_call
        )
        filtered = self.query_receipt(filtered_call, filtered_result)
        detail_call = self.tool_call(
            "v3-comment-detail",
            "read_evidence_detail",
            {"evidence_id": V3_COMMENT_EVIDENCE_ID},
        )
        detail_result = self.tools.execute(
            report_version_id=V3_REPORT_VERSION_ID, call=detail_call
        )
        detail = self.query_receipt(detail_call, detail_result)

        self.assertEqual(filtered.status, "ok")
        self.assertEqual(filtered.details.evidence_types, ("comment",))
        self.assertEqual(filtered.returned_refs, (V3_COMMENT_EVIDENCE_ID,))
        self.assertEqual(filtered.total, 1)
        self.assertFalse(filtered.has_more)
        self.assertEqual(detail.status, "ok")
        self.assertEqual(detail.operation, "evidence_detail_read")
        self.assertEqual(detail.details.evidence_ref, V3_COMMENT_EVIDENCE_ID)
        self.assertEqual(detail.details.finding_ref, V3_FINDING_ID)
        self.assertIsNone(detail.has_more)

    def test_v3_receipts_project_round3_then_collection_capabilities(self):
        claim_call = self.tool_call(
            "v3-capability-claim",
            "read_claim_support",
            {"claim_id": V3_CASE_CLAIM_ID},
        )
        claim_receipt = self.query_receipt(
            claim_call,
            self.tools.execute(
                report_version_id=V3_REPORT_VERSION_ID, call=claim_call
            ),
        )
        scope = QueryCapabilityScope(
            session_id="investigation-session:v3-receipt",
            report_version_id=V3_REPORT_VERSION_ID,
            snapshot_hash=self.context.snapshot_hash,
        )
        round3 = QueryCapabilityResolver((claim_receipt,), scope=scope)

        self.assertTrue(round3.has_claim_support(V3_CASE_CLAIM_ID))
        self.assertTrue(round3.has_finding_summary(V3_FINDING_ID))
        self.assertTrue(
            round3.has_frozen_citation(
                V3_TEXT_EVIDENCE_ID, claim_ref=V3_CASE_CLAIM_ID
            )
        )
        self.assertFalse(round3.has_evidence_collection(V3_FINDING_ID))
        self.assertFalse(round3.has_evidence_detail(V3_TEXT_EVIDENCE_ID))

        list_call = self.tool_call(
            "v3-capability-list",
            "list_finding_evidence",
            {"finding_id": V3_FINDING_ID, "limit": 20},
        )
        list_receipt = self.query_receipt(
            list_call,
            self.tools.execute(
                report_version_id=V3_REPORT_VERSION_ID, call=list_call
            ),
        )
        after_list = QueryCapabilityResolver(
            (claim_receipt, list_receipt), scope=scope
        ).assess_evidence_collection(V3_FINDING_ID)

        self.assertEqual(after_list.status, "available")
        self.assertTrue(after_list.backend_complete)
        self.assertTrue(after_list.tool_result_model_ref_complete)
        self.assertEqual(
            set(after_list.returned_refs),
            {V3_TEXT_EVIDENCE_ID, V3_COMMENT_EVIDENCE_ID},
        )


class InvestigationStoreTest(InvestigationFixtureTest):
    def test_database_enforces_one_running_turn_and_client_idempotency(self):
        store = InvestigationStore(self.root / "store.sqlite3")
        session = store.create_session(self.context)
        turn, replay = store.create_turn(
            session.id, client_message_id="client-1", user_input="第一轮"
        )
        self.assertFalse(replay)
        same, replay = store.create_turn(
            session.id, client_message_id="client-1", user_input="不会重复写入"
        )
        self.assertTrue(replay)
        self.assertEqual(same.id, turn.id)
        with self.assertRaises(ConcurrentTurnError):
            store.create_turn(
                session.id, client_message_id="client-2", user_input="并发轮次"
            )
        with sqlite3.connect(store.db_path) as connection:
            indexes = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'uq_investigation_running_turn'"
            ).fetchone()[0]
        self.assertIn("WHERE status = 'running'", indexes)

    def test_existing_business_database_receives_receipt_and_ledger_link_columns(self):
        db_path = self.root / "legacy-store.sqlite3"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TABLE investigation_source_ledger (
                    ledger_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    metric_key TEXT NOT NULL DEFAULT '',
                    section_id TEXT NOT NULL DEFAULT '',
                    claim_id TEXT NOT NULL DEFAULT '',
                    finding_id TEXT NOT NULL DEFAULT '',
                    evidence_id TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL,
                    excerpt TEXT NOT NULL DEFAULT '',
                    asset_status TEXT NOT NULL DEFAULT '',
                    query_fingerprint TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    freshness TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        store = InvestigationStore(db_path)
        with sqlite3.connect(store.db_path) as connection:
            ledger_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(investigation_source_ledger)"
                )
            }
            receipt_table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'investigation_tool_query_receipts'"
            ).fetchone()

        self.assertIn("tool_call_id", ledger_columns)
        self.assertIn("query_receipt_id", ledger_columns)
        self.assertIsNotNone(receipt_table)


class InvestigationAgentGraphTest(InvestigationFixtureTest):
    def _seed_report_cases(self, service, model):
        model.append(
            result(
                "focused-seed-1",
                tool_calls=(
                    self.tool_call(
                        "focused-seed-presentation",
                        "read_report_presentation",
                        {},
                    ),
                ),
            ),
            result("focused-seed-2", content="报告概览已经按当前范围说明。"),
        )
        session = service.create_session(self.report_version_id)
        seeded = service.send_message(
            session.id,
            client_message_id="focused-seed",
            content="这份报告主要发现了什么？",
        )
        self.assertEqual(seeded.status, "completed")
        self.assertTrue(
            any(item.type == "case" for item in service.store.get_session(session.id).ordered_referents)
        )
        return session

    @staticmethod
    def _selection_response(request_id, case_ref):
        return result(
            request_id,
            content=(
                '<case_selection>{"selected_case_ref":"'
                f"{case_ref}"
                '"}</case_selection>'
            ),
        )

    def _run_focused_chain_through_round3(
        self,
        *,
        shadow_planner=None,
        controlled_react=False,
    ):
        model = ScriptedChatModel()
        service = self.service(
            model,
            enable_controlled_evidence_react=controlled_react,
            **({"shadow_planner": shadow_planner} if shadow_planner else {}),
        )
        session = service.create_session(self.report_version_id)

        model.append(
            result(
                "evidence-chain-overview-tool",
                tool_calls=(
                    self.tool_call(
                        "evidence-chain-presentation",
                        "read_report_presentation",
                        {},
                    ),
                ),
            ),
            result(
                "evidence-chain-overview-final",
                content="报告概览已经按当前范围说明。",
            ),
        )
        overview = service.send_message(
            session.id,
            client_message_id="evidence-chain-round-1",
            content="这份报告主要发现了什么？",
        )
        self.assertEqual(overview.status, "completed")
        self.assertEqual(overview.tool_names, ("read_report_presentation",))
        self.assertNotIn("lookup_report_metric", overview.tool_names)

        selected = next(
            focused
            for item in self.facade.get_case_catalog(self.report_version_id)
            for focused in (
                self.facade.get_focused_case_context(
                    self.report_version_id, item["case_ref"]
                ),
            )
            if any(
                self.facade.get_claim_support(self.report_version_id, claim_id)[
                    "evidence"
                ]
                for claim_id in focused.related_claim_refs
            )
        )
        claim_id = next(
            claim_id
            for claim_id in selected.related_claim_refs
            if self.facade.get_claim_support(self.report_version_id, claim_id)[
                "evidence"
            ]
        )

        def focused_case_answer(request):
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertIn(selected.case_text, serialized)
            self.assertIn("focused_case_answer", serialized)
            return result(
                "evidence-chain-case-final",
                content=(
                    "这是当前选中的典型案例。"
                    "round2 unsupported assistant prose marker"
                ),
            )

        model.append(
            self._selection_response(
                "evidence-chain-case-selection", selected.case_ref
            ),
            focused_case_answer,
        )
        case_answer = service.send_message(
            session.id,
            client_message_id="evidence-chain-round-2",
            content="给我一个典型案例。",
        )
        self.assertEqual(case_answer.status, "completed")
        self.assertEqual(case_answer.tool_calls, ())
        self.assertEqual(
            service.store.get_session(session.id).active_focus["target_id"],
            selected.case_ref,
        )

        def focused_why_request(request):
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertEqual(request["tool_choice"], "required")
            self.assertIn("authority_required_before_answer", serialized)
            self.assertNotIn("round2 unsupported assistant prose marker", serialized)
            return result(
                "evidence-chain-claim-tool",
                tool_calls=(
                    self.tool_call(
                        "evidence-chain-claim-support",
                        "read_claim_support",
                        {"claim_id": claim_id},
                    ),
                ),
            )

        model.append(
            focused_why_request,
            result(
                "evidence-chain-why-final",
                content="风险依据来自当前 Claim 与 Finding 摘要。",
            ),
        )
        why_answer = service.send_message(
            session.id,
            client_message_id="evidence-chain-round-3",
            content="为什么这个案例有风险？",
        )
        self.assertEqual(why_answer.status, "completed")
        self.assertEqual(why_answer.tool_names, ("read_claim_support",))
        self.assertEqual(why_answer.resolved_references[0].target_id, selected.case_ref)
        self.assertFalse(
            {"list_finding_evidence", "read_evidence_detail"}.intersection(
                why_answer.tool_names
            )
        )
        source_kinds = {item.source_kind.value for item in why_answer.accessed_sources}
        self.assertIn("report_claim", source_kinds)
        self.assertIn("frozen_citation_excerpt", source_kinds)
        self.assertNotIn("current_evidence", source_kinds)
        self.assertTrue(
            {
                item.finding_id
                for item in why_answer.accessed_sources
                if item.finding_id
            }.issubset(set(selected.related_finding_refs))
        )
        return model, service, session, selected, claim_id, why_answer

    def test_round1_to_round3_focus_chain_preserves_existing_behavior(self):
        self._run_focused_chain_through_round3()

    def test_evidence_artifact_side_path_is_persisted_and_failure_is_behavior_neutral(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )

        def run(*, fail_artifacts):
            model = ScriptedChatModel(
                [
                    result(
                        f"artifact-side-tool-{fail_artifacts}",
                        tool_calls=(
                            self.tool_call(
                                "artifact-side-list",
                                "list_finding_evidence",
                                {"finding_id": finding_id, "limit": 20},
                            ),
                        ),
                    ),
                    result(
                        f"artifact-side-final-{fail_artifacts}",
                        content="已根据当前 Finding 的 Evidence 摘要作答。",
                    ),
                ]
            )
            service = self.service(model)
            session = service.create_session(self.report_version_id)
            if fail_artifacts:
                persistence = patch.object(
                    service.store,
                    "put_query_receipt_artifacts",
                    side_effect=RuntimeError("injected Artifact failure"),
                )
                log = patch("backend.investigation.agent.logger.exception")
            else:
                persistence = patch.object(
                    service.store,
                    "put_query_receipt_artifacts",
                    wraps=service.store.put_query_receipt_artifacts,
                )
                log = patch("backend.investigation.agent.logger.exception")
            with persistence, log:
                answer = service.send_message(
                    session.id,
                    client_message_id=f"artifact-side-{fail_artifacts}",
                    content="列出当前 Finding 的证据。",
                )
            tool_payload = next(
                message["content"]
                for message in model.calls[1]["messages"]
                if message.get("role") == "tool"
            )
            return service, session, answer, tool_payload

        success_service, success_session, success, success_payload = run(
            fail_artifacts=False
        )
        failed_service, failed_session, failed, failed_payload = run(
            fail_artifacts=True
        )

        self.assertEqual(success.status, "completed")
        self.assertEqual(failed.status, "completed")
        self.assertEqual(success.tool_names, failed.tool_names)
        self.assertEqual(success_payload, failed_payload)
        self.assertEqual(
            len(
                success_service.store.list_query_artifact_index(
                    session_id=success_session.id
                )
            ),
            1,
        )
        self.assertEqual(
            failed_service.store.list_query_artifact_index(
                session_id=failed_session.id
            ),
            (),
        )
        self.assertEqual(
            len(
                failed_service.store.list_query_receipts(
                    session_id=failed_session.id
                )
            ),
            1,
        )

    def test_frozen_claim_preview_is_not_the_complete_finding_evidence_set(self):
        model, service, _session, selected, claim_id, why_answer = (
            self._run_focused_chain_through_round3()
        )
        del model
        support = self.facade.get_claim_support(self.report_version_id, claim_id)
        finding_id = support["finding_ids"][0]
        full_page = self.facade.list_finding_evidence(
            self.report_version_id, finding_id, limit=20
        )
        frozen_refs = {
            item.evidence_id
            for item in why_answer.accessed_sources
            if item.source_kind.value == "frozen_citation_excerpt"
        }
        full_refs = {item["evidence_id"] for item in full_page["items"]}
        authority = service._focused_authority_from_ledger(
            selected,
            [
                item
                for item in why_answer.accessed_sources
                if item.source_kind.value == "frozen_citation_excerpt"
            ],
        )

        self.assertEqual(
            frozen_refs,
            {item["evidence_id"] for item in support["evidence"]},
        )
        self.assertEqual(
            {
                item["evidence_id"]
                for item in authority["facts"]
                if item["source_kind"] == "frozen_citation_excerpt"
            },
            frozen_refs,
        )
        self.assertEqual(authority["validated_evidence_refs"], [])
        self.assertTrue(frozen_refs < full_refs)

    def test_frozen_claim_preview_is_not_promoted_to_current_evidence_authority(self):
        _model, service, _session, selected, _claim_id, why_answer = (
            self._run_focused_chain_through_round3()
        )
        frozen_sources = [
            item
            for item in why_answer.accessed_sources
            if item.source_kind.value == "frozen_citation_excerpt"
        ]
        self.assertTrue(frozen_sources)

        authority = service._focused_authority_from_ledger(
            selected, frozen_sources
        )

        self.assertEqual(authority["validated_evidence_refs"], [])
        self.assertEqual(
            {
                (item["claim_id"], item["evidence_id"], item["source_kind"])
                for item in authority["facts"]
            },
            {
                (item.claim_id, item.evidence_id, "frozen_citation_excerpt")
                for item in frozen_sources
            },
        )

    def test_round4_evidence_request_cannot_finish_with_only_frozen_preview(self):
        planner_model = ScriptedChatModel(
            [
                TurnPlannerShadowTest.planner_result(
                    "round4-overview-plan",
                    {"requirement_kind": "report_presentation"},
                ),
                TurnPlannerShadowTest.planner_result(
                    "round4-case-plan",
                    {"requirement_kind": "report_presentation"},
                ),
                TurnPlannerShadowTest.planner_result(
                    "round4-claim-plan",
                    {"requirement_kind": "focused_claim_support"},
                ),
                TurnPlannerShadowTest.planner_result(
                    "round4-evidence-plan",
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    },
                ),
            ]
        )
        model, service, session, selected, _claim_id, why_answer = (
            self._run_focused_chain_through_round3(
                shadow_planner=TurnPlanner(model_client=planner_model)
            )
        )
        frozen_ref = next(
            item.evidence_id
            for item in why_answer.accessed_sources
            if item.source_kind.value == "frozen_citation_excerpt"
        )
        call_number = {"value": 0}

        def evidence_request(request):
            call_number["value"] += 1
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertIn(frozen_ref, serialized)
            self.assertNotIn("frozen_citation_excerpt", serialized)
            self.assertIn("ready_source_bundle", serialized)
            self.assertIn("current_evidence", serialized)
            self.assertEqual(request["tool_choice"], "none")
            self.assertEqual(request["tools"], [])
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            return result(
                f"evidence-chain-zero-tool-{call_number['value']}",
                content="已根据当前 Finding 的真实 Evidence 作答。",
            )

        model.append(evidence_request)
        evidence_answer = service.send_message(
            session.id,
            client_message_id="evidence-chain-round-4",
            content="具体有哪些证据？",
        )

        self.assertEqual(evidence_answer.status, "completed")
        self.assertTrue(
            {"list_finding_evidence", "read_evidence_detail"}.intersection(
                evidence_answer.tool_names
            )
        )
        self.assertEqual(
            evidence_answer.context_accounting[-1]["context_projection"],
            "bundle_only_answer",
        )

    def test_focused_case_projection_isolates_cases_and_keeps_only_validated_global_fact(self):
        model = ScriptedChatModel()
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        catalog = self.facade.get_case_catalog(self.report_version_id)
        selected = self.facade.get_focused_case_context(
            self.report_version_id, catalog[0]["case_ref"]
        )
        selected_data = selected.model_dump(mode="json")
        selected_data["validated_report_wide_facts"] = [
            {
                "claim_id": "report-claim:" + "e" * 32,
                "claim_type": "methodology",
                "text": "全局结论 S",
                "source_block": "conclusion",
            }
        ]
        state = service._initial_state(session, "focused-projection", "介绍这个案例")
        state.update(
            {
                "focused_case": selected_data,
                "case_catalog": [
                    {"case_ref": selected.case_ref, "title": selected.title, "summary": "A1"},
                    {
                        "case_ref": "report-claim:" + "d" * 32,
                        "title": "case B",
                        "summary": "B-specific fact B1",
                    },
                ],
                "case_selection_completed": True,
                "case_selection_available": False,
                "active_focus": {
                    "type": "case",
                    "target_id": selected.case_ref,
                    "label": selected.title,
                },
                "recent_messages": [
                    {"role": "user", "content": "上一问"},
                    {"role": "assistant", "content": "unsupported X from conversation"},
                ],
                "conversation_continuity": [{"role": "user", "content": "上一问"}],
                "working_messages": [{"role": "user", "content": "介绍这个案例"}],
            }
        )
        messages, accounting = service.context_builder.build_request(
            state=state,
            tool_definitions=service.tool_service.definitions(),
        )
        serialized = json.dumps(messages, ensure_ascii=False)
        self.assertEqual(accounting["context_projection"], "focused_case_answer")
        self.assertIn(selected.case_text, serialized)
        self.assertNotIn("B-specific fact B1", serialized)
        self.assertNotIn("unsupported X", serialized)
        self.assertIn("全局结论 S", serialized)
        presentation = self.facade.get_report_presentation(self.report_version_id)
        self.assertNotIn(presentation["summary"]["text"], serialized)
        self.assertNotIn(presentation["conclusion"]["text"], serialized)

    def test_case_selection_and_answer_generation_are_separate_and_membership_validated(self):
        model = ScriptedChatModel()
        service = self.service(model)
        session = self._seed_report_cases(service, model)
        catalog = self.facade.get_case_catalog(self.report_version_id)
        selected = self.facade.get_focused_case_context(
            self.report_version_id, catalog[0]["case_ref"]
        )

        def focused_answer(request):
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertIn("focused_case_answer", serialized)
            self.assertIn(selected.case_text, serialized)
            self.assertNotIn("报告概览已经按当前范围说明", serialized)
            self.assertEqual([item["role"] for item in request["messages"]], ["system", "user"])
            return result("focused-case-answer", content="这个案例展示了视频内容与评论互动之间的风险错位。")

        model.append(
            self._selection_response("focused-selection", selected.case_ref),
            focused_answer,
        )
        answer = service.send_message(
            session.id,
            client_message_id="focused-case",
            content="给我一个典型案例。",
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.llm_call_count, 2)
        self.assertEqual(answer.tool_calls, ())
        checkpoint = service._checkpoint_state(session.id)
        self.assertTrue(checkpoint["case_selection_membership_validated"])
        self.assertEqual(checkpoint["case_selection_trace"]["stage"], "case_selection")
        self.assertEqual(checkpoint["focused_case"]["case_ref"], selected.case_ref)
        locked = service.store.get_session(session.id)
        self.assertEqual(locked.active_focus["target_id"], selected.case_ref)

    def test_case_selection_rejects_ref_outside_current_report(self):
        model = ScriptedChatModel()
        service = self.service(model)
        session = self._seed_report_cases(service, model)
        model.append(
            self._selection_response(
                "invalid-focused-selection", "report-claim:" + "f" * 32
            )
        )
        answer = service.send_message(
            session.id,
            client_message_id="invalid-focused-case",
            content="给我一个典型案例。",
        )
        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.stop_reason, "error")
        self.assertEqual(len(model.calls), 3)

    def test_focused_explanation_excludes_conversation_and_reads_missing_claim_authority(self):
        model = ScriptedChatModel()
        service = self.service(model)
        session = self._seed_report_cases(service, model)
        selected = self.facade.get_focused_case_context(
            self.report_version_id,
            self.facade.get_case_catalog(self.report_version_id)[0]["case_ref"],
        )
        model.append(
            self._selection_response("authority-selection", selected.case_ref),
            result(
                "authority-case-answer",
                content="这是一个典型案例，但 previous unsupported X。",
            ),
        )
        case_answer = service.send_message(
            session.id,
            client_message_id="authority-case",
            content="给我一个典型案例。",
        )
        self.assertEqual(case_answer.status, "completed")

        def missing_authority_request(request):
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertEqual(request["tool_choice"], "required")
            self.assertIn("authority_required_before_answer", serialized)
            self.assertNotIn("previous unsupported X", serialized)
            return result(
                "authority-claim-call",
                tool_calls=(
                    self.tool_call(
                        "authority-claim",
                        "read_claim_support",
                        {"claim_id": selected.related_claim_refs[0]},
                    ),
                ),
            )

        model.append(
            missing_authority_request,
            result(
                "authority-final",
                content="风险来自视频的普法表述与评论区资金结算意图之间的错位。",
            ),
        )
        answer = service.send_message(
            session.id,
            client_message_id="authority-why",
            content="为什么这个案例有风险？",
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.tool_names, ("read_claim_support",))
        self.assertEqual(answer.resolved_references[0].target_id, selected.case_ref)
        self.assertIn(
            "report_claim", {item.source_kind.value for item in answer.accessed_sources}
        )

    def test_matching_claim_authority_is_reused_without_duplicate_tool(self):
        model = ScriptedChatModel()
        service = self.service(model)
        session = self._seed_report_cases(service, model)
        selected = self.facade.get_focused_case_context(
            self.report_version_id,
            self.facade.get_case_catalog(self.report_version_id)[0]["case_ref"],
        )
        model.append(
            self._selection_response("reuse-selection", selected.case_ref),
            result(
                "reuse-claim-call",
                tool_calls=(
                    self.tool_call(
                        "reuse-claim",
                        "read_claim_support",
                        {"claim_id": selected.related_claim_refs[0]},
                    ),
                ),
            ),
            result("reuse-case-final", content="该案例的报告 Claim 已核验。"),
        )
        first = service.send_message(
            session.id,
            client_message_id="reuse-case",
            content="给我一个典型案例。",
        )
        self.assertEqual(first.tool_names, ("read_claim_support",))

        def reused_request(request):
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertEqual(request["tool_choice"], "auto")
            self.assertIn("focused_authoritative_facts", serialized)
            self.assertIn(selected.related_claim_refs[0], serialized)
            return result(
                "reuse-final",
                content="风险来自该 Claim 已记录的视频警示与评论区违规意图错位。",
            )

        model.append(reused_request)
        answer = service.send_message(
            session.id,
            client_message_id="reuse-why",
            content="为什么这个案例有风险？",
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.tool_calls, ())
        self.assertTrue(answer.grounding_validation.inherited_sources)

    def test_focused_finding_membership_rejects_other_case(self):
        model = ScriptedChatModel()
        service = self.service(model)
        session = self._seed_report_cases(service, model)
        selected = self.facade.get_focused_case_context(
            self.report_version_id,
            self.facade.get_case_catalog(self.report_version_id)[0]["case_ref"],
        )
        other_finding = next(
            finding_id
            for finding_id in self.context.finding_ids
            if finding_id not in set(selected.related_finding_refs)
        )
        model.append(
            self._selection_response("membership-selection", selected.case_ref),
            result("membership-case", content="已选择当前案例。"),
        )
        service.send_message(
            session.id,
            client_message_id="membership-case",
            content="给我一个典型案例。",
        )
        model.append(
            result(
                "membership-wrong",
                tool_calls=(
                    self.tool_call(
                        "membership-wrong-finding",
                        "read_finding_detail",
                        {"finding_id": other_finding},
                    ),
                ),
            ),
            result(
                "membership-right",
                tool_calls=(
                    self.tool_call(
                        "membership-right-claim",
                        "read_claim_support",
                        {"claim_id": selected.related_claim_refs[0]},
                    ),
                ),
            ),
            result("membership-final", content="当前案例风险依据已经按所属 Claim 核验。"),
        )
        answer = service.send_message(
            session.id,
            client_message_id="membership-why",
            content="为什么这个案例有风险？",
        )
        self.assertEqual(
            answer.tool_names, ("read_finding_detail", "read_claim_support")
        )
        self.assertNotIn(
            other_finding,
            {item.finding_id for item in answer.accessed_sources},
        )
        messages = service.get_messages(session.id, include_tool_messages=True)
        wrong = next(
            item
            for item in messages
            if item.tool_call_id == "membership-wrong-finding"
        )
        self.assertIn("focused_scope_mismatch", wrong.content)

    def test_minimal_sufficient_guidance_is_generic_and_not_zero_metric_rule(self):
        model = ScriptedChatModel([result("scope-guidance", content="这是按当前问题范围作答。")])
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="scope-guidance", content="请概括当前材料"
        )
        self.assertEqual(answer.status, "completed")
        system = model.calls[0]["messages"][0]["content"]
        self.assertIn("最小充分回答原则", system)
        self.assertIn("最小充分不等于空泛", system)
        self.assertIn("不是必须使用 0 个 Metric", system)
        self.assertIn("普通概览不得为了完整感自动拆分多个 batch", system)
        self.assertNotIn("这份报告主要发现了什么", system)
        self.assertNotIn("V3", system)
        self.assertNotIn("intent classifier", system)

    def test_explicit_complete_statistics_can_use_controlled_multiple_batches(self):
        keys = [item["metric_key"] for item in self.metrics[:9]]
        model = ScriptedChatModel(
            [
                result(
                    "complete-statistics-1",
                    tool_calls=(
                        self.tool_call(
                            "complete-statistics-batch-1",
                            "lookup_report_metric",
                            {"metric_keys": keys[:8]},
                        ),
                    ),
                ),
                result(
                    "complete-statistics-2",
                    tool_calls=(
                        self.tool_call(
                            "complete-statistics-batch-2",
                            "lookup_report_metric",
                            {"metric_keys": keys[8:]},
                        ),
                    ),
                ),
                result("complete-statistics-3", content="已按要求读取完整统计。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id,
            client_message_id="complete-statistics",
            content="请列出全部统计指标",
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.tool_names.count("lookup_report_metric"), 2)
        self.assertEqual(answer.tool_calls[0].arguments["metric_keys"], keys[:8])
        self.assertEqual(answer.tool_calls[1].arguments["metric_keys"], keys[8:])

    def test_tool_loop_persists_trace_and_source_ledger(self):
        model = ScriptedChatModel(
            [
                result(
                    "request-1",
                    tool_calls=(
                        self.tool_call(
                            "call-1",
                            "lookup_report_metric",
                            {"metric_key": self.metric_key},
                        ),
                    ),
                ),
                result("request-2", content="冻结统计已经核验。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="client-1", content="报告统计是多少？"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.tool_names, ("lookup_report_metric",))
        self.assertEqual(len(answer.query_receipts), 1)
        receipt = answer.query_receipts[0]
        self.assertEqual(receipt.tool_call_id, "call-1")
        self.assertEqual(receipt.operation, "report_metric_lookup")
        self.assertEqual(receipt.status, "ok")
        self.assertEqual(receipt.returned_refs, (self.metric_key,))
        self.assertEqual(
            service.store.list_query_receipts(turn_id=answer.turn_id),
            answer.query_receipts,
        )
        reopened = InvestigationStore(service.store.db_path)
        self.assertEqual(
            reopened.list_query_receipts(turn_id=answer.turn_id),
            answer.query_receipts,
        )
        self.assertEqual(answer.citations[0].source_kind.value, "frozen_metric")
        self.assertEqual(answer.citations[0].tool_call_id, "call-1")
        self.assertEqual(answer.citations[0].query_receipt_id, receipt.receipt_id)
        self.assertEqual(answer.llm_call_count, 2)
        self.assertEqual(answer.total_tokens, 50)
        roles = [
            item.role
            for item in service.get_messages(session.id, include_tool_messages=True)
        ]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        for call in model.calls:
            self.assertEqual(call["messages"][0]["role"], "system")
            self.assertEqual(
                sum(item["role"] == "system" for item in call["messages"]), 1
            )
            validate_model_request_messages(call["messages"])
            self.assertNotIn("query_receipt", json.dumps(call["messages"]))

    def test_capability_projection_is_stable_after_receipt_store_reopen(self):
        finding_ref = next(
            finding_id
            for finding_id in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, finding_id, limit=20
            )["items"]
        )
        model = ScriptedChatModel(
            [
                result(
                    "capability-persist-1",
                    tool_calls=(
                        self.tool_call(
                            "capability-persist-call",
                            "list_finding_evidence",
                            {"finding_id": finding_ref, "limit": 20},
                        ),
                    ),
                ),
                result(
                    "capability-persist-2",
                    content="已经读取当前 Finding 的证据列表。",
                ),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id,
            client_message_id="capability-persist",
            content="读取当前 Finding 的证据列表",
        )
        scope = QueryCapabilityScope(
            session_id=session.id,
            report_version_id=session.report_version_id,
            snapshot_hash=session.snapshot_hash,
        )
        live = QueryCapabilityResolver(
            answer.query_receipts, scope=scope
        ).assess_evidence_collection(finding_ref)

        reopened = InvestigationStore(service.store.db_path)
        restored = QueryCapabilityResolver(
            reopened.list_query_receipts(session_id=session.id), scope=scope
        ).assess_evidence_collection(finding_ref)

        self.assertEqual(answer.status, "completed")
        self.assertEqual(live, restored)
        self.assertEqual(restored.status, "available")
        self.assertTrue(restored.backend_complete)

    def test_failed_tool_query_receipt_persists_without_source_authority(self):
        model = ScriptedChatModel(
            [
                result(
                    "failed-tool-receipt-1",
                    tool_calls=(
                        self.tool_call(
                            "failed-tool-call",
                            "list_finding_evidence",
                            {"finding_id": "not-a-finding"},
                        ),
                    ),
                ),
                result(
                    "failed-tool-receipt-2",
                    content="工具参数无效，本轮没有取得新的业务资料。",
                ),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id,
            client_message_id="failed-tool-receipt",
            content="读取不存在的证据范围",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(len(answer.query_receipts), 1)
        receipt = answer.query_receipts[0]
        self.assertEqual(receipt.status, "error")
        self.assertEqual(receipt.error_code, "invalid_tool_arguments")
        self.assertEqual(receipt.returned_refs, ())
        self.assertEqual(receipt.model_visible_refs, ())
        self.assertEqual(answer.accessed_sources, ())
        self.assertEqual(
            service.store.list_query_receipts(session_id=session.id),
            answer.query_receipts,
        )

    def test_normal_chat_does_not_call_domain_tools(self):
        model = ScriptedChatModel([result("hello-1", content="你好，我可以回答当前报告问题。")])
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="hello", content="你好"
        )
        self.assertEqual(answer.tool_calls, ())
        self.assertEqual(answer.citations, ())
        self.assertTrue(model.calls[0]["tools"])

    def test_simple_explanation_inherits_sources_without_new_tool_call(self):
        model = ScriptedChatModel(
            [
                result(
                    "metric-1",
                    tool_calls=(
                        self.tool_call(
                            "metric-call",
                            "lookup_report_metric",
                            {"metric_key": self.metric_key},
                        ),
                    ),
                ),
                result("metric-2", content="冻结值已经核验。"),
                result("simple-1", content="简单说，就是报告里的固定统计值。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        first = service.send_message(
            session.id, client_message_id="metric", content="这个数字是什么？"
        )
        second = service.send_message(
            session.id, client_message_id="simple", content="简单一点解释"
        )
        self.assertGreater(len(first.citations), 0)
        self.assertEqual(second.tool_calls, ())
        self.assertGreater(len(second.citations), 0)
        self.assertTrue(second.grounding_validation.inherited_sources)
        self.assertEqual(model.calls[-1]["tools"], [])
        self.assertEqual(model.calls[-1]["tool_choice"], "none")

    def test_second_referent_resolves_to_previous_ordered_finding(self):
        model = ScriptedChatModel(
            [
                result(
                    "list-1",
                    tool_calls=(
                        self.tool_call(
                            "list-call", "list_report_findings", {"limit": 3}
                        ),
                    ),
                ),
                result("list-2", content="我列出了三个案例。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        service.send_message(
            session.id, client_message_id="list", content="给我三个典型案例"
        )
        locked = service.store.get_session(session.id)
        self.assertGreaterEqual(len(locked.ordered_referents), 2)
        second_id = locked.ordered_referents[1].target_id
        model.append(
            result(
                "detail-1",
                tool_calls=(
                    self.tool_call(
                        "detail-call",
                        "read_finding_detail",
                        {"finding_id": second_id},
                    ),
                ),
            ),
            result("detail-2", content="第二个案例的详情已经核验。"),
        )
        answer = service.send_message(
            session.id, client_message_id="second", content="第二个案例呢？"
        )
        self.assertEqual(answer.resolved_references[0].status, "resolved")
        self.assertEqual(answer.resolved_references[0].target_id, second_id)
        self.assertEqual(answer.tool_names, ("read_finding_detail",))

    def test_typed_ordinal_keeps_case_list_when_evidence_list_is_newer(self):
        model = ScriptedChatModel(
            [
                result(
                    "case-list-1",
                    tool_calls=(
                        self.tool_call(
                            "case-list-call", "list_report_findings", {"limit": 3}
                        ),
                    ),
                ),
                result("case-list-2", content="我列出了三个案例。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        service.send_message(
            session.id, client_message_id="case-list", content="给我三个案例"
        )
        case_session = service.store.get_session(session.id)
        second_case = next(
            item
            for item in case_session.ordered_referents
            if item.type == "finding" and item.list_position == 2
        )
        first_case = next(
            item
            for item in case_session.ordered_referents
            if item.type == "finding" and item.list_position == 1
        )
        evidence = self.facade.list_finding_evidence(
            self.report_version_id, first_case.target_id, limit=2
        )["items"]
        model.append(
            result(
                "evidence-list-1",
                tool_calls=(
                    self.tool_call(
                        "evidence-list-call",
                        "list_finding_evidence",
                        {"finding_id": first_case.target_id, "limit": 2},
                    ),
                ),
            ),
            result("evidence-list-2", content="我列出了相关证据。"),
        )
        service.send_message(
            session.id, client_message_id="evidence-list", content="给我相关证据"
        )
        mixed_session = service.store.get_session(session.id)
        self.assertTrue(
            any(item.type == "evidence" for item in mixed_session.ordered_referents)
        )
        self.assertTrue(
            any(item.type == "finding" for item in mixed_session.ordered_referents)
        )
        model.append(
            result(
                "case-detail-1",
                tool_calls=(
                    self.tool_call(
                        "case-detail-call",
                        "read_finding_detail",
                        {"finding_id": second_case.target_id},
                    ),
                ),
            ),
            result("case-detail-2", content="第二个案例已经核验。"),
        )
        answer = service.send_message(
            session.id, client_message_id="typed-second", content="第二个案例呢？"
        )
        self.assertEqual(answer.resolved_references[0].target_id, second_case.target_id)
        self.assertNotEqual(answer.resolved_references[0].target_id, evidence[1]["evidence_id"])

    def test_repeated_tool_call_stops_loop_and_forces_final_answer(self):
        repeated = {"metric_key": self.metric_key}
        model = ScriptedChatModel(
            [
                result(
                    "repeat-1",
                    tool_calls=(
                        self.tool_call("repeat-call-1", "lookup_report_metric", repeated),
                    ),
                ),
                result(
                    "repeat-2",
                    tool_calls=(
                        self.tool_call("repeat-call-2", "lookup_report_metric", repeated),
                    ),
                ),
                result("repeat-3", content="使用第一次取得的冻结统计回答。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="repeat", content="读取这个统计"
        )
        self.assertEqual(answer.stop_reason, "repeated_tool_call")
        self.assertEqual(len(answer.tool_calls), 2)
        self.assertEqual(model.calls[-1]["tools"], [])

    def test_numeric_draft_without_metric_is_rejected_and_repaired(self):
        percentage = next(
            item
            for item in self.metrics
            if item.get("percentage_basis") == "finding_count"
        )
        display = round(float(percentage["value"]), 1)
        denominator = int(percentage["denominator"])

        def scope_repair_request(request):
            self.assertEqual(request["tool_choice"], "none")
            self.assertEqual(request["tools"], [])
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            system = request["messages"][0]["content"]
            self.assertIn("scope_constrained_repair", system)
            self.assertIn(f"报告中的比例是{display}%", system)
            self.assertIn("missing_authoritative_source", system)
            return result(
                "grounding-3-scope",
                content=f"报告中的比例是{display}%。",
            )

        def source_repair_request(request):
            self.assertEqual(request["tool_choice"], "required")
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            system = request["messages"][0]["content"]
            self.assertIn("missing_source_repair", system)
            self.assertIn(f"报告中的比例是{display}%", system)
            self.assertIn("missing_authoritative_source", system)
            self.assertIn(percentage["metric_key"], system)
            return result(
                "grounding-4",
                tool_calls=(
                    self.tool_call(
                        "grounding-metric",
                        "lookup_report_metric",
                        {"metric_key": percentage["metric_key"]},
                    ),
                ),
            )

        def repaired_final_request(request):
            validate_model_request_messages(request["messages"])
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user", "assistant", "tool"],
            )
            self.assertEqual(request["messages"][-1]["name"], "lookup_report_metric")
            self.assertIn(percentage["metric_key"], request["messages"][-1]["content"])
            return result(
                "grounding-5",
                content=(
                    f"冻结口径是：在{denominator}条 Finding 中，该分组占{display}%。"
                ),
            )

        model = ScriptedChatModel(
            [
                result(
                    "grounding-1",
                    tool_calls=(
                        self.tool_call(
                            "presentation-call", "read_report_presentation", {}
                        ),
                    ),
                ),
                result(
                    "grounding-2",
                    content=f"报告中的比例是{display}%。",
                ),
                scope_repair_request,
                source_repair_request,
                repaired_final_request,
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="grounding", content="解释报告数字"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(
            answer.tool_names,
            ("read_report_presentation", "lookup_report_metric"),
        )
        self.assertEqual(model.calls[2]["tool_choice"], "none")
        self.assertEqual(model.calls[3]["tool_choice"], "required")
        self.assertIn(f"{denominator}条 Finding", answer.answer)
        self.assertIn("frozen_metric", {item.source_kind.value for item in answer.citations})
        self.assertEqual(answer.grounding_repair_count, 2)
        self.assertEqual(answer.scope_repair_count, 1)
        self.assertEqual(answer.source_repair_count, 1)
        self.assertEqual(answer.semantic_rewrite_count, 0)
        self.assertEqual(
            answer.context_accounting[2]["context_projection"],
            "scope_constrained_repair",
        )
        self.assertEqual(
            answer.context_accounting[3]["context_projection"],
            "missing_source_repair",
        )
        self.assertEqual(
            answer.context_accounting[4]["context_projection"],
            "missing_source_repair",
        )
        self.assertGreater(answer.context_accounting[2]["pruned_working_tokens"], 0)

    def test_multiple_missing_metrics_are_repaired_with_one_batch_call(self):
        counts = [
            item for item in self.metrics
            if item.get("metric_name") == "count"
        ][:2]
        model = ScriptedChatModel(
            [
                result(
                    "batch-grounding-1",
                    tool_calls=(
                        self.tool_call("batch-presentation", "read_report_presentation", {}),
                    ),
                ),
                result(
                    "batch-grounding-2",
                    content=(
                        f"两个分组分别为{int(counts[0]['value'])}条和"
                        f"{int(counts[1]['value'])}条。"
                    ),
                ),
                result(
                    "batch-grounding-3-scope",
                    content=(
                        f"两个分组分别为{int(counts[0]['value'])}条和"
                        f"{int(counts[1]['value'])}条。"
                    ),
                ),
                result(
                    "batch-grounding-4",
                    tool_calls=(
                        self.tool_call(
                            "batch-metrics",
                            "lookup_report_metric",
                            {"metric_keys": [item["metric_key"] for item in counts]},
                        ),
                    ),
                ),
                result(
                    "batch-grounding-5",
                    content=(
                        f"两个分组分别为{int(counts[0]['value'])}条和"
                        f"{int(counts[1]['value'])}条。"
                    ),
                ),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="batch-grounding", content="比较两个统计分组"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.tool_names.count("lookup_report_metric"), 1)
        self.assertEqual(
            answer.tool_calls[-1].arguments["metric_keys"],
            [item["metric_key"] for item in counts],
        )
        self.assertEqual(service._checkpoint_state(session.id)["source_repair_count"], 1)
        audit = service.store.get_turn_audit(answer.turn_id)
        self.assertEqual(audit["grounding_repair_count"], 2)
        self.assertEqual(audit["scope_repair_count"], 1)
        self.assertEqual(audit["source_repair_count"], 1)
        self.assertEqual(audit["semantic_rewrite_count"], 0)

    def test_optional_missing_statistics_are_removed_by_scope_repair_without_metric(self):
        percentage = next(
            item
            for item in self.metrics
            if item.get("percentage_basis") == "finding_count"
        )
        display = round(float(percentage["value"]), 1)
        total = int(percentage["denominator"])
        initial_draft = (
            f"本次共分析{total}条内容，其中相关分组占{display}%。"
            "报告最值得关注的是内容主体与互动区呈现不同风险信号。"
        )
        repaired_draft = (
            "报告最值得关注的是内容主体与互动区呈现不同风险信号，"
            "需要结合两部分信息理解其风险模式。"
        )

        def scope_request(request):
            self.assertEqual(request["tool_choice"], "none")
            self.assertEqual(request["tools"], [])
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            system = request["messages"][0]["content"]
            self.assertIn("scope_constrained_repair", system)
            self.assertIn(initial_draft, system)
            self.assertIn("report_expression_digest", system)
            self.assertNotIn("result_fingerprint", system)
            return result("optional-scope-3", content=repaired_draft)

        model = ScriptedChatModel(
            [
                result(
                    "optional-scope-1",
                    tool_calls=(
                        self.tool_call("optional-report", "read_report_presentation", {}),
                    ),
                ),
                result("optional-scope-2", content=initial_draft),
                scope_request,
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="optional-scope", content="概括报告核心发现"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.answer, repaired_draft)
        self.assertEqual(answer.tool_names, ("read_report_presentation",))
        self.assertEqual(answer.scope_repair_count, 1)
        self.assertEqual(answer.source_repair_count, 0)
        self.assertEqual(answer.semantic_rewrite_count, 0)
        self.assertEqual(answer.scope_initial_draft, initial_draft)
        self.assertEqual(answer.scope_repaired_draft, repaired_draft)
        self.assertTrue(answer.scope_initial_issues)
        self.assertEqual(answer.scope_remaining_issues, ())
        self.assertEqual(
            answer.context_accounting[-1]["context_projection"],
            "scope_constrained_repair",
        )
        self.assertEqual(answer.context_accounting[-1]["current_turn_tool_result_tokens"], 0)
        audit = service.store.get_turn_audit(answer.turn_id)
        self.assertEqual(audit["scope_initial_draft"], initial_draft)
        self.assertEqual(audit["scope_repaired_draft"], repaired_draft)

    def test_mixed_scope_repair_queries_only_the_essential_metric(self):
        percentage = next(
            item
            for item in self.metrics
            if item.get("percentage_basis") == "finding_count"
        )
        optional = next(
            item
            for item in self.metrics
            if item.get("metric_name") == "count"
            and int(float(item["value"])) != int(percentage["denominator"])
        )
        display = round(float(percentage["value"]), 1)
        optional_value = int(float(optional["value"]))
        initial_draft = (
            f"该比例为{display}%，另外还有{optional_value}条其他统计信息。"
        )
        scope_draft = f"用户询问的比例为{display}%。"

        def source_request(request):
            self.assertEqual(request["tool_choice"], "required")
            system = request["messages"][0]["content"]
            self.assertIn(scope_draft, system)
            self.assertNotIn(f"{optional_value}条其他统计信息", system)
            return result(
                "mixed-scope-4",
                tool_calls=(
                    self.tool_call(
                        "mixed-essential-metric",
                        "lookup_report_metric",
                        {"metric_keys": [percentage["metric_key"]]},
                    ),
                ),
            )

        model = ScriptedChatModel(
            [
                result(
                    "mixed-scope-1",
                    tool_calls=(
                        self.tool_call("mixed-report", "read_report_presentation", {}),
                    ),
                ),
                result("mixed-scope-2", content=initial_draft),
                result("mixed-scope-3", content=scope_draft),
                source_request,
                result("mixed-scope-5", content=scope_draft),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id,
            client_message_id="mixed-scope",
            content="报告中的这个比例是多少？",
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(
            answer.tool_names,
            ("read_report_presentation", "lookup_report_metric"),
        )
        self.assertEqual(
            answer.tool_calls[-1].arguments["metric_keys"],
            [percentage["metric_key"]],
        )
        self.assertNotIn(str(optional_value), answer.scope_repaired_draft)
        remaining_facts = {
            fact
            for issue in answer.scope_remaining_issues
            for fact in issue.get("numeric_facts") or []
        }
        self.assertTrue(any("%" in fact for fact in remaining_facts))
        self.assertFalse(any(fact == f"{optional_value}条" for fact in remaining_facts))
        self.assertEqual(answer.scope_repair_count, 1)
        self.assertEqual(answer.source_repair_count, 1)

    def test_scope_source_and_semantic_repairs_are_each_bounded_once(self):
        percentage = next(
            item
            for item in self.metrics
            if item.get("percentage_basis") == "finding_count"
        )
        display = round(float(percentage["value"]), 1)
        draft = f"该比例为{display}%。"
        model = ScriptedChatModel(
            [
                result(
                    "repair-bound-1",
                    tool_calls=(
                        self.tool_call("repair-bound-report", "read_report_presentation", {}),
                    ),
                ),
                result("repair-bound-2", content=draft),
                result("repair-bound-3", content=draft),
                result(
                    "repair-bound-4",
                    tool_calls=(
                        self.tool_call(
                            "repair-bound-metric",
                            "lookup_report_metric",
                            {"metric_keys": [percentage["metric_key"]]},
                        ),
                    ),
                ),
                result(
                    "repair-bound-5",
                    content=f"该比例为{display}%，另有999条。",
                ),
                result("repair-bound-6", content="仍然有998条。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="repair-bound", content="这个比例是多少？"
        )
        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.llm_call_count, 6)
        self.assertEqual(answer.grounding_repair_count, 3)
        self.assertEqual(answer.scope_repair_count, 1)
        self.assertEqual(answer.source_repair_count, 1)
        self.assertEqual(answer.semantic_rewrite_count, 1)
        self.assertEqual(len(model.calls), 6)
        self.assertEqual(
            [item["context_projection"] for item in answer.context_accounting[-3:]],
            ["missing_source_repair", "missing_source_repair", "semantic_rewrite"],
        )

    def test_finding_local_numbers_are_supported_without_metric(self):
        finding_id = self.context.finding_ids[0]
        detail = self.facade.get_finding_detail(self.report_version_id, finding_id)["data"]
        finding = detail["finding"]
        score = float(finding["risk_score"])
        hit_count = len(finding.get("matched_rule_ids") or [])
        model = ScriptedChatModel(
            [
                result(
                    "finding-number-1",
                    tool_calls=(
                        self.tool_call(
                            "finding-number-call",
                            "read_finding_detail",
                            {"finding_id": finding_id},
                        ),
                    ),
                ),
                result(
                    "finding-number-2",
                    content=f"该案例风险评分为{score:g}分，命中{hit_count}条规则。",
                ),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="finding-number", content="这个案例评分如何？"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.tool_names, ("read_finding_detail",))
        self.assertEqual(answer.grounding_issues, ())
        self.assertNotIn("frozen_metric", {item.source_kind.value for item in answer.accessed_sources})

    def test_evidence_local_timestamp_is_supported_without_metric(self):
        evidence_id = next(
            item
            for item in self.context.evidence_ids
            if self.facade.get_evidence_detail(self.report_version_id, item)["data"].get(
                "timestamp_start"
            ) is not None
        )
        detail = self.facade.get_evidence_detail(self.report_version_id, evidence_id)["data"]
        timestamp = float(detail["timestamp_start"])
        model = ScriptedChatModel(
            [
                result(
                    "evidence-number-1",
                    tool_calls=(
                        self.tool_call(
                            "evidence-number-call",
                            "read_evidence_detail",
                            {"evidence_id": evidence_id},
                        ),
                    ),
                ),
                result(
                    "evidence-number-2",
                    content=f"这条证据从{timestamp:g}秒开始。",
                ),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="evidence-number", content="证据从何时开始？"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.tool_names, ("read_evidence_detail",))
        self.assertEqual(answer.grounding_issues, ())
        index = service.store.list_query_artifact_index(
            session_id=session.id,
            operation="evidence_detail_read",
        )
        self.assertEqual(len(index), 1)
        query_result = service.store.get_query_result_artifact(
            index[0].query_result_artifact_id
        )
        self.assertIsNotNone(query_result)
        self.assertEqual(query_result.canonical_payload["original_text"], detail["original_text"])
        source = service.store.get_source_artifact(
            query_result.ordered_members[0].source_artifact_id
        )
        self.assertIsNotNone(source)
        self.assertEqual(source.content_level, "evidence_detail")
        self.assertEqual(source.canonical_content, query_result.canonical_payload)

    def test_context_accounting_excludes_historical_tool_payloads(self):
        model = ScriptedChatModel(
            [
                result(
                    "context-1",
                    tool_calls=(
                        self.tool_call(
                            "context-metric",
                            "lookup_report_metric",
                            {"metric_keys": [self.metric_key]},
                        ),
                    ),
                ),
                result("context-2", content="冻结统计已核验。"),
                result("context-3", content="上一轮已经完成。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        first = service.send_message(
            session.id, client_message_id="context-first", content="读取这个统计"
        )
        second = service.send_message(
            session.id, client_message_id="context-second", content="继续说明"
        )
        followup_request = model.calls[1]
        self.assertNotIn(self.metric_key, followup_request["messages"][0]["content"])
        validate_model_request_messages(followup_request["messages"])
        next_turn_request = model.calls[2]
        self.assertFalse(any(item["role"] == "tool" for item in next_turn_request["messages"]))
        self.assertTrue(first.context_accounting)
        self.assertTrue(second.context_accounting)
        for call in (*first.context_accounting, *second.context_accounting):
            self.assertEqual(call["historical_tool_result_tokens"], 0)
            self.assertIn("system_prompt_tokens", call)
            self.assertIn("dynamic_context_tokens", call)
            self.assertIn("current_turn_tool_result_tokens", call)
            self.assertEqual(call["actual_input_tokens"], 20)
        audit = service.store.get_turn_audit(first.turn_id)
        self.assertEqual(audit["context_accounting"], list(first.context_accounting))

    def test_normal_projection_prunes_old_payload_but_preserves_audit_and_protocol(self):
        first_key, second_key = [item["metric_key"] for item in self.metrics[:2]]

        def final_request(request):
            validate_model_request_messages(request["messages"])
            tool_names = [
                item.get("name")
                for item in request["messages"]
                if item.get("role") == "tool"
            ]
            self.assertEqual(tool_names, ["lookup_report_metric", "lookup_report_metric"])
            system = request["messages"][0]["content"]
            self.assertIn("report_expression_digest", system)
            return result("projection-4", content="报告结论与所需统计已经核验。")

        model = ScriptedChatModel(
            [
                result(
                    "projection-1",
                    tool_calls=(
                        self.tool_call("projection-report", "read_report_presentation", {}),
                    ),
                ),
                result(
                    "projection-2",
                    tool_calls=(
                        self.tool_call(
                            "projection-metric-1",
                            "lookup_report_metric",
                            {"metric_keys": [first_key]},
                        ),
                    ),
                ),
                result(
                    "projection-3",
                    tool_calls=(
                        self.tool_call(
                            "projection-metric-2",
                            "lookup_report_metric",
                            {"metric_keys": [second_key]},
                        ),
                    ),
                ),
                final_request,
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="projection", content="按需核验报告与两个统计"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(len(answer.tool_calls), 3)
        self.assertEqual(answer.context_accounting[-1]["context_projection"], "normal_tool_loop")
        self.assertGreater(answer.context_accounting[-1]["pruned_working_tokens"], 0)
        checkpoint = service._checkpoint_state(session.id)
        self.assertEqual(len(checkpoint["all_tool_results"]), 3)
        self.assertEqual(
            len(
                [
                    item
                    for item in checkpoint["working_messages"]
                    if item.get("role") == "assistant" and item.get("tool_calls")
                ]
            ),
            3,
        )
        audit = service.store.get_turn_audit(answer.turn_id)
        self.assertGreater(len(audit["source_refs"]), 0)
        persisted = service.get_messages(session.id, include_tool_messages=True)
        self.assertEqual(
            len([item for item in persisted if item.turn_id == answer.turn_id and item.role == "tool"]),
            3,
        )

    def test_accessed_sources_are_audit_not_answer_support_claims(self):
        model = ScriptedChatModel(
            [
                result(
                    "source-audit-1",
                    tool_calls=(
                        self.tool_call(
                            "source-audit-call",
                            "lookup_report_metric",
                            {"metric_keys": [self.metric_key]},
                        ),
                    ),
                ),
                result("source-audit-2", content="已读取权威来源。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="source-audit", content="读取统计来源"
        )
        self.assertEqual(answer.accessed_sources, answer.citations)
        self.assertGreater(len(answer.accessed_sources), 0)
        audit = service.store.get_turn_audit(answer.turn_id)
        self.assertEqual(audit["source_semantics"], "retrieved_accessed_source_audit")
        self.assertEqual(audit["answer_support_sources"], [])

    def test_metrics_are_not_implicitly_added_into_a_new_fact(self):
        numeric_metrics = [
            item for item in self.metrics if item.get("metric_name") != "percentage"
        ]
        first = numeric_metrics[0]
        second = next(
            item
            for item in numeric_metrics[1:]
            if float(first["value"]) + float(item["value"])
            not in {
                float(first["value"]),
                float(first["denominator"]),
                float(item["value"]),
                float(item["denominator"]),
            }
        )
        total = int(float(first["value"]) + float(second["value"]))

        def semantic_rewrite(request):
            self.assertEqual(request["tool_choice"], "none")
            self.assertEqual(request["tools"], [])
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            system = request["messages"][0]["content"]
            self.assertIn(f"合计为{total}条。", system)
            self.assertIn("unsupported_derived_statistic", system)
            self.assertIn("semantic_rewrite", system)
            self.assertIn(first["metric_key"], system)
            self.assertIn(second["metric_key"], system)
            self.assertIn("semantic_definition", system)
            self.assertNotIn("result_fingerprint", system)
            return result(
                "sum-3",
                content=(
                    f"第一个冻结指标为{int(float(first['value']))}条，"
                    f"第二个冻结指标为{int(float(second['value']))}条。"
                ),
            )

        model = ScriptedChatModel(
            [
                result(
                    "sum-1",
                    tool_calls=(
                        self.tool_call("sum-first", "lookup_report_metric", {"metric_key": first["metric_key"]}),
                        self.tool_call("sum-second", "lookup_report_metric", {"metric_key": second["metric_key"]}),
                    ),
                ),
                result("sum-2", content=f"合计为{total}条。"),
                semantic_rewrite,
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="sum", content="把这两个统计相加"
        )
        self.assertEqual(answer.status, "completed")
        self.assertNotIn(f"{total}条", answer.answer)
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(answer.tool_names.count("lookup_report_metric"), 2)
        self.assertEqual(service._checkpoint_state(session.id)["semantic_rewrite_count"], 1)
        self.assertEqual(answer.grounding_repair_count, 1)
        self.assertEqual(answer.source_repair_count, 0)
        self.assertEqual(answer.semantic_rewrite_count, 1)
        rewrite_accounting = answer.context_accounting[-1]
        self.assertEqual(rewrite_accounting["context_projection"], "semantic_rewrite")
        self.assertEqual(rewrite_accounting["current_turn_tool_result_tokens"], 0)
        self.assertEqual(rewrite_accounting["provenance_tokens"], 0)
        self.assertGreater(rewrite_accounting["pruned_working_tokens"], 0)
        audit = service.store.get_turn_audit(answer.turn_id)
        self.assertGreater(len(audit["source_refs"]), 0)
        persisted = service.get_messages(session.id, include_tool_messages=True)
        self.assertEqual(
            len([item for item in persisted if item.turn_id == answer.turn_id and item.role == "tool"]),
            2,
        )

    def test_grounding_validator_does_not_rewrite_valid_model_answer(self):
        percentage = next(
            item
            for item in self.metrics
            if item.get("percentage_basis") == "finding_count"
        )
        display = round(float(percentage["value"]), 1)
        model_answer = f"冻结统计显示该分组比例为{display}%。"
        model = ScriptedChatModel(
            [
                result(
                    "metric-answer-1",
                    tool_calls=(
                        self.tool_call(
                            "metric-answer-call",
                            "lookup_report_metric",
                            {"metric_key": percentage["metric_key"]},
                        ),
                    ),
                ),
                result("metric-answer-2", content=model_answer),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="metric-answer", content="这个比例是什么意思？"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.answer, model_answer)
        self.assertEqual(len(model.calls), 2)

    def test_hard_token_budget_stops_before_model_call(self):
        model = ScriptedChatModel(
            [
                result("budget-should-not-run", content="不应执行"),
            ]
        )
        service = self.service(
            model, max_run_tokens=1_000, reserved_output_tokens=128
        )
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="budget", content="读取统计"
        )
        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.stop_reason, "token_budget")
        self.assertEqual(answer.llm_call_count, 0)
        self.assertEqual(model.calls, [])

    def test_hard_token_budget_is_checked_again_before_followup_call(self):
        model = ScriptedChatModel(
            [
                result(
                    "budget-first",
                    tool_calls=(
                        self.tool_call(
                            "budget-tool",
                            "lookup_report_metric",
                            {"metric_key": self.metric_key},
                        ),
                    ),
                    input_tokens=18_000,
                    output_tokens=100,
                ),
                result("budget-second-should-not-run", content="不应执行"),
            ]
        )
        service = self.service(
            model, max_run_tokens=20_000, reserved_output_tokens=100
        )
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="budget-followup", content="读取统计"
        )
        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.stop_reason, "token_budget")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(answer.llm_call_count, 1)
        audit = service.store.get_turn_audit(answer.turn_id)
        self.assertEqual(audit["tool_audit"][0]["tool_name"], "lookup_report_metric")
        self.assertGreater(len(audit["source_refs"]), 0)

    def test_empty_post_tool_response_retries_once_without_tools(self):
        model = ScriptedChatModel(
            [
                result(
                    "empty-1",
                    tool_calls=(
                        self.tool_call(
                            "empty-call",
                            "lookup_report_metric",
                            {"metric_key": self.metric_key},
                        ),
                    ),
                ),
                result("empty-2", content=""),
                result("empty-3", content="恢复后的最终回答。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="empty", content="读取统计"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.llm_call_count, 3)
        self.assertEqual(model.calls[-1]["tools"], [])

    def test_second_empty_final_response_persists_turn_error(self):
        model = ScriptedChatModel(
            [
                result(
                    "fail-empty-1",
                    tool_calls=(
                        self.tool_call(
                            "fail-empty-call",
                            "lookup_report_metric",
                            {"metric_key": self.metric_key},
                        ),
                    ),
                ),
                result("fail-empty-2", content=""),
                result("fail-empty-3", content=""),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="fail-empty", content="读取统计"
        )
        self.assertEqual(answer.status, "error")
        self.assertIn("未能生成", answer.answer)
        self.assertEqual(answer.llm_call_count, 3)
        self.assertEqual(answer.citations, ())
        audit = service.store.get_turn_audit(answer.turn_id)
        self.assertEqual(audit["tool_audit"][0]["tool_name"], "lookup_report_metric")
        self.assertEqual(audit["tool_audit"][0]["result_status"], "ok")
        self.assertTrue(audit["tool_audit"][0]["arguments_fingerprint"])
        self.assertTrue(audit["tool_audit"][0]["result_fingerprint"])
        self.assertEqual(len(audit["query_receipts"]), 1)
        self.assertEqual(audit["query_receipts"][0]["status"], "ok")
        self.assertGreater(len(audit["source_refs"]), 0)
        self.assertEqual(audit["llm_call_count"], 3)

    def test_failed_grounding_audit_is_in_business_store(self):
        numeric_metrics = [
            item for item in self.metrics if item.get("metric_name") != "percentage"
        ]
        first = numeric_metrics[0]
        model = ScriptedChatModel(
            [
                result(
                    "failed-grounding-1",
                    tool_calls=(
                        self.tool_call(
                            "failed-grounding-first",
                            "lookup_report_metric",
                            {"metric_key": first["metric_key"]},
                        ),
                    ),
                ),
                result("failed-grounding-2", content="统计是999条。"),
                result("failed-grounding-3", content="统计仍是998条。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="failed-grounding", content="给出统计"
        )
        self.assertEqual(answer.status, "error")
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(model.calls[-1]["tools"], [])
        audit = service.store.get_turn_audit(answer.turn_id)
        self.assertEqual(len(audit["tool_audit"]), 1)
        self.assertTrue(audit["grounding_errors"])
        self.assertEqual(
            audit["grounding_issues"][0]["issue_type"], "ambiguous_numeric_fact"
        )
        self.assertGreater(len(audit["source_refs"]), 0)

    def test_model_text_cannot_create_source_ledger_entries(self):
        model = ScriptedChatModel(
            [result("no-source", content="我不能自行创建服务端来源账本。")]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="no-source", content="你好"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.citations, ())
        self.assertEqual(service.store.list_ledger(turn_id=answer.turn_id), ())

    def test_interrupted_turn_resumes_from_checkpoint(self):
        model = ScriptedChatModel(
            [
                result(
                    "resume-1",
                    tool_calls=(
                        self.tool_call(
                            "resume-call",
                            "lookup_report_metric",
                            {"metric_key": self.metric_key},
                        ),
                    ),
                ),
                RuntimeError("injected interruption"),
                result("resume-2", content="恢复后使用已经取得的统计回答。"),
            ]
        )
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        with self.assertRaisesRegex(RuntimeError, "injected interruption"):
            service.send_message(
                session.id, client_message_id="resume", content="读取统计"
            )
        turn = service.store.get_turn_by_client_message(session.id, "resume")
        self.assertEqual(turn.status, "interrupted")
        checkpoint_receipts = service._checkpoint_state(session.id)["query_receipts"]
        self.assertEqual(len(checkpoint_receipts), 1)
        self.assertEqual(checkpoint_receipts[0]["tool_call_id"], "resume-call")
        self.assertEqual(service.store.list_query_receipts(turn_id=turn.id), ())
        answer = service.resume_turn(turn.id)
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.llm_call_count, 2)
        self.assertEqual(answer.tool_names, ("lookup_report_metric",))
        self.assertEqual(len(answer.query_receipts), 1)
        self.assertEqual(answer.query_receipts[0].tool_call_id, "resume-call")
        self.assertGreater(len(answer.citations), 0)

    def test_checkpoint_scope_mismatch_fails_closed(self):
        model = ScriptedChatModel([RuntimeError("pause")])
        service = self.service(model)
        session = service.create_session(self.report_version_id)
        with self.assertRaises(RuntimeError):
            service.send_message(
                session.id, client_message_id="mismatch", content="暂停"
            )
        turn = service.store.get_turn_by_client_message(session.id, "mismatch")
        service.app.update_state(
            {"configurable": {"thread_id": session.id}},
            {"snapshot_hash": "0" * 64},
        )
        with self.assertRaises(CheckpointScopeMismatchError):
            service.resume_turn(turn.id)
        self.assertEqual(service.store.get_turn(turn.id).status, "interrupted")


class ToolProtocolTest(unittest.TestCase):
    def test_dangling_or_out_of_order_tool_results_are_rejected(self):
        dangling = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "tool", "arguments": "{}"},
                    }
                ],
            },
        ]
        with self.assertRaises(ToolProtocolError):
            validate_model_request_messages(dangling)
        orphan = [
            {"role": "system", "content": "system"},
            {"role": "tool", "tool_call_id": "call-1", "content": "{}"},
        ]
        with self.assertRaises(ToolProtocolError):
            validate_model_request_messages(orphan)


class TurnPlannerShadowTest(InvestigationFixtureTest):
    @staticmethod
    def planner_result(request_id, plan, *, input_tokens=30, output_tokens=5):
        transport = {
            "requirement_kind": "none",
            "evidence_types": None,
            "coverage": None,
            "reason": None,
        }
        transport.update(plan)
        return result(
            request_id,
            tool_calls=(
                ToolCall(
                    id=f"{request_id}-call",
                    name=SUBMIT_TURN_PLAN_TOOL,
                    arguments=transport,
                ),
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def test_turn_plan_discriminated_schema_accepts_only_coherent_variants(self):
        valid = (
            {"requirement_kind": "none"},
            {"requirement_kind": "report_presentation"},
            {"requirement_kind": "focused_claim_support"},
            {
                "requirement_kind": "finding_evidence_collection",
                "evidence_types": None,
                "coverage": "discovery",
            },
            {
                "requirement_kind": "finding_evidence_collection",
                "evidence_types": ["comment"],
                "coverage": "complete",
            },
            {"requirement_kind": "evidence_detail"},
            {
                "requirement_kind": "unsupported",
                "reason": "multiple_source_requirements",
            },
        )
        for plan in valid:
            parsed = SubmitTurnPlanInput.model_validate({"plan": plan})
            self.assertEqual(parsed.plan.requirement_kind, plan["requirement_kind"])

        invalid = (
            {
                "requirement_kind": "focused_claim_support",
                "coverage": "complete",
            },
            {
                "requirement_kind": "finding_evidence_collection",
                "evidence_types": [],
                "coverage": "discovery",
            },
            {
                "requirement_kind": "finding_evidence_collection",
                "evidence_types": ["comment", "comment"],
                "coverage": "complete",
            },
            {"requirement_kind": "unsupported"},
            {"requirement_kind": "unknown"},
        )
        for plan in invalid:
            with self.assertRaises(ValidationError):
                SubmitTurnPlanInput.model_validate({"plan": plan})

        plan_schema = SubmitTurnPlanInput.model_json_schema()
        discriminator = plan_schema["properties"]["plan"]["discriminator"]
        self.assertEqual(discriminator["propertyName"], "requirement_kind")
        tool_schema = TurnPlanner.tool_definition()["function"]["parameters"]
        self.assertEqual(
            set(tool_schema["required"]),
            {"requirement_kind", "evidence_types", "coverage", "reason"},
        )
        multi_type = SubmitTurnPlanToolInput.model_validate(
            {
                "requirement_kind": "finding_evidence_collection",
                "evidence_types": ["comment", "keyframe"],
                "coverage": "discovery",
                "reason": None,
            }
        ).as_submission()
        self.assertEqual(multi_type.plan.evidence_types, ("comment", "keyframe"))
        with self.assertRaises(ValidationError):
            SubmitTurnPlanToolInput.model_validate(
                {
                    "requirement_kind": "none",
                    "evidence_types": None,
                    "coverage": "complete",
                    "reason": None,
                }
            )

    def test_v12_prompt_defines_collection_detail_and_cross_requirement_precedence(self):
        self.assertEqual(PLANNER_PROMPT_VERSION, "turn-planner-shadow-v1.2")
        self.assertIn("本身绝不表示 evidence_detail", PLANNER_SYSTEM_PROMPT)
        self.assertIn("明确单条指代才是 evidence_detail", PLANNER_SYSTEM_PROMPT)
        self.assertIn("coverage=complete", PLANNER_SYSTEM_PROMPT)
        self.assertIn(
            "report_presentation 和 finding_evidence_collection",
            PLANNER_SYSTEM_PROMPT,
        )
        self.assertIn(
            "focused_claim_support 和 evidence_detail",
            PLANNER_SYSTEM_PROMPT,
        )

    def test_planner_uses_minimal_input_and_dedicated_output_channel(self):
        model = ScriptedChatModel()
        planner = TurnPlanner(model_client=model)
        service = self.service(ScriptedChatModel())
        session = service.create_session(self.report_version_id).model_copy(
            update={
                "active_focus": {
                    "type": "case",
                    "target_id": "report-claim:" + "a" * 32,
                    "label": "private case title",
                }
            }
        )
        resolved = (
            ResolvedReference(
                expression="这个案例",
                status="resolved",
                target_type="case",
                target_id="report-claim:" + "a" * 32,
                label="private case title",
            ),
        )
        snapshot = planner.input_snapshot(
            current_user_message="为什么这个案例有风险？",
            active_focus=session.active_focus,
            resolved_references=resolved,
        )

        def inspect_request(request):
            self.assertEqual(request["tool_choice"], "required")
            self.assertEqual(
                [item["function"]["name"] for item in request["tools"]],
                [SUBMIT_TURN_PLAN_TOOL],
            )
            payload = json.loads(request["messages"][1]["content"])
            self.assertEqual(
                set(payload),
                {
                    "current_user_message",
                    "active_focus_exists",
                    "active_focus_type",
                    "resolved_referent_status",
                    "resolved_referent_type",
                },
            )
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertNotIn(session.report_version_id, serialized)
            self.assertNotIn("private case title", serialized)
            self.assertNotIn("report-claim:", serialized)
            return self.planner_result(
                "minimal-plan", {"requirement_kind": "focused_claim_support"}
            )

        model.append(inspect_request)
        trace = planner.plan(
            session=session,
            turn_id="investigation-turn:minimal-plan",
            input_snapshot=snapshot,
        )
        self.assertEqual(trace.planning_status, "ok")
        self.assertEqual(trace.plan.requirement_kind, "focused_claim_support")

    def test_planner_protocol_and_schema_errors_fail_closed(self):
        service = self.service(ScriptedChatModel())
        session = service.create_session(self.report_version_id)
        snapshot = PlannerInputSnapshot(
            current_user_message="有哪些证据？",
            active_focus_exists=False,
            active_focus_type="",
            resolved_referent_status="not_applicable",
            resolved_referent_type="",
        )
        bad_responses = (
            result("planner-prose", content="none"),
            result(
                "planner-extra-prose",
                content="这是计划",
                tool_calls=(
                    ToolCall(
                        id="planner-extra-prose-call",
                        name=SUBMIT_TURN_PLAN_TOOL,
                        arguments={"requirement_kind": "none"},
                    ),
                ),
            ),
            self.planner_result(
                "planner-invalid",
                {
                    "requirement_kind": "finding_evidence_collection",
                    "coverage": "sometimes",
                    "evidence_types": None,
                },
            ),
            result(
                "planner-repeated",
                tool_calls=(
                    ToolCall(
                        id="planner-repeated-1",
                        name=SUBMIT_TURN_PLAN_TOOL,
                        arguments={"requirement_kind": "none"},
                    ),
                    ToolCall(
                        id="planner-repeated-2",
                        name=SUBMIT_TURN_PLAN_TOOL,
                        arguments={"requirement_kind": "none"},
                    ),
                ),
            ),
        )
        for index, response in enumerate(bad_responses):
            planner = TurnPlanner(
                model_client=ScriptedChatModel([response]),
                max_schema_retries=0,
            )
            trace = planner.plan(
                session=session,
                turn_id=f"investigation-turn:bad-plan-{index}",
                input_snapshot=snapshot,
            )
            self.assertEqual(trace.planning_status, "error")
            self.assertIsNone(trace.plan)
            self.assertTrue(trace.error_code)
            self.assertEqual(trace.planner_error, trace.error_code)

    def test_planner_transport_normalizes_json_encoding_not_semantic_conflicts(self):
        collection = result(
            "planner-string-array",
            tool_calls=(
                ToolCall(
                    id="planner-string-array-call",
                    name=SUBMIT_TURN_PLAN_TOOL,
                    arguments={
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": '["comment"]',
                        "coverage": "discovery",
                        "reason": None,
                    },
                ),
            ),
        )
        parsed = TurnPlanner._parse_result(collection)
        self.assertEqual(parsed.plan.evidence_types, ("comment",))

        detail = result(
            "planner-null-strings",
            tool_calls=(
                ToolCall(
                    id="planner-null-strings-call",
                    name=SUBMIT_TURN_PLAN_TOOL,
                    arguments={
                        "requirement_kind": "evidence_detail",
                        "evidence_types": "null",
                        "coverage": "null",
                        "reason": "null",
                    },
                ),
            ),
        )
        detail_plan = TurnPlanner._parse_result(detail).plan
        self.assertEqual(detail_plan.requirement_kind, "evidence_detail")
        self.assertFalse(hasattr(detail_plan, "evidence_types"))

        conflicting = result(
            "planner-semantic-conflict",
            tool_calls=(
                ToolCall(
                    id="planner-semantic-conflict-call",
                    name=SUBMIT_TURN_PLAN_TOOL,
                    arguments={
                        "requirement_kind": "evidence_detail",
                        "evidence_types": '["comment"]',
                        "coverage": None,
                        "reason": None,
                    },
                ),
            ),
        )
        with self.assertRaises(ValidationError):
            TurnPlanner._parse_result(conflicting)

        malformed = result(
            "planner-malformed-array",
            tool_calls=(
                ToolCall(
                    id="planner-malformed-array-call",
                    name=SUBMIT_TURN_PLAN_TOOL,
                    arguments={
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": "comment",
                        "coverage": "discovery",
                        "reason": None,
                    },
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "JSON array"):
            TurnPlanner._parse_result(malformed)

    def test_planner_repairs_one_invalid_schema_submission(self):
        model = ScriptedChatModel(
            [
                self.planner_result(
                    "repair-invalid",
                    {
                        "requirement_kind": "unsupported",
                        "evidence_types": ["comment"],
                        "reason": "multiple_source_requirements",
                    },
                    input_tokens=40,
                    output_tokens=8,
                ),
                self.planner_result(
                    "repair-valid",
                    {
                        "requirement_kind": "unsupported",
                        "reason": "multiple_source_requirements",
                    },
                    input_tokens=45,
                    output_tokens=7,
                ),
            ]
        )
        planner = TurnPlanner(model_client=model)
        service = self.service(ScriptedChatModel())
        session = service.create_session(self.report_version_id)
        snapshot = PlannerInputSnapshot(
            current_user_message="概括报告并查看评论材料",
            active_focus_exists=False,
            active_focus_type="",
            resolved_referent_status="not_applicable",
            resolved_referent_type="",
        )

        trace = planner.plan(
            session=session,
            turn_id="investigation-turn:planner-schema-repair",
            input_snapshot=snapshot,
        )

        self.assertEqual(trace.planning_status, "ok")
        self.assertEqual(trace.plan.requirement_kind, "unsupported")
        self.assertEqual(trace.planner_llm_call_count, 2)
        self.assertEqual(trace.planner_retry_count, 1)
        self.assertEqual(trace.planner_input_tokens, 85)
        self.assertEqual(trace.planner_output_tokens, 15)
        self.assertEqual(trace.planner_total_tokens, 100)
        self.assertEqual(len(model.calls), 2)
        repair_payload = json.loads(model.calls[1]["messages"][-1]["content"])
        self.assertIn("repair_request", repair_payload)
        self.assertIn("validation_error", repair_payload)

    def test_planner_second_invalid_schema_submission_fails_closed(self):
        invalid = {
            "requirement_kind": "unsupported",
            "evidence_types": ["comment"],
            "reason": "multiple_source_requirements",
        }
        model = ScriptedChatModel(
            [
                self.planner_result("repair-invalid-first", invalid),
                self.planner_result("repair-invalid-second", invalid),
            ]
        )
        planner = TurnPlanner(model_client=model)
        service = self.service(ScriptedChatModel())
        session = service.create_session(self.report_version_id)
        snapshot = PlannerInputSnapshot(
            current_user_message="概括报告并查看评论材料",
            active_focus_exists=False,
            active_focus_type="",
            resolved_referent_status="not_applicable",
            resolved_referent_type="",
        )

        trace = planner.plan(
            session=session,
            turn_id="investigation-turn:planner-schema-repair-failed",
            input_snapshot=snapshot,
        )

        self.assertEqual(trace.planning_status, "error")
        self.assertIsNone(trace.plan)
        self.assertEqual(trace.error_code, "planner_output_schema_error")
        self.assertEqual(trace.planner_llm_call_count, 2)
        self.assertEqual(trace.planner_retry_count, 1)

    def test_shadow_trace_is_persisted_idempotent_and_budget_separated(self):
        planner_model = ScriptedChatModel(
            [
                self.planner_result(
                    "shadow-none",
                    {"requirement_kind": "none"},
                    input_tokens=300,
                    output_tokens=10,
                )
            ]
        )
        main_model = ScriptedChatModel(
            [result("shadow-main", content="你好。", input_tokens=20, output_tokens=5)]
        )
        planner = TurnPlanner(model_client=planner_model)
        service = self.service(main_model, shadow_planner=planner)
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="shadow-idempotent", content="你好"
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.total_tokens, 25)
        self.assertEqual(answer.llm_call_count, 1)
        self.assertEqual(answer.tool_calls, ())
        self.assertEqual(answer.query_receipts, ())
        self.assertIsNotNone(answer.planner_shadow_trace)
        trace = answer.planner_shadow_trace
        self.assertEqual(trace.planner_total_tokens, 310)
        self.assertEqual(trace.planner_llm_call_count, 1)
        self.assertEqual(trace.plan.requirement_kind, "none")
        self.assertEqual(len(planner_model.calls), 1)
        self.assertIsNotNone(answer.source_preparation_shadow_trace)
        source_trace = answer.source_preparation_shadow_trace
        self.assertEqual(source_trace.status, "not_applicable")

        replay = service.send_message(
            session.id, client_message_id="shadow-idempotent", content="你好"
        )
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(len(planner_model.calls), 1)
        self.assertEqual(replay.planner_shadow_trace.trace_id, trace.trace_id)
        self.assertEqual(
            replay.source_preparation_shadow_trace.trace_id,
            source_trace.trace_id,
        )
        reopened = InvestigationStore(service.store.db_path)
        restored = reopened.get_planner_shadow_trace(answer.turn_id)
        self.assertEqual(restored, trace)
        self.assertEqual(
            service.store.get_turn_audit(answer.turn_id)["planner_shadow_trace"]
            ["trace_id"],
            trace.trace_id,
        )
        self.assertEqual(
            service.store.get_turn_audit(answer.turn_id)[
                "source_preparation_shadow_trace"
            ]["trace_id"],
            source_trace.trace_id,
        )
        with service.store._connect() as connection:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM investigation_source_preparation_shadow_traces
                WHERE turn_id = ?
                """,
                (answer.turn_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_source_orchestrator_shadow_observes_acquisition_then_reuse(self):
        finding_id = next(
            finding_ref
            for finding_ref in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, finding_ref, limit=20
            )["items"]
        )
        planner_model = ScriptedChatModel(
            [
                self.planner_result(
                    "source-warmup-plan", {"requirement_kind": "none"}
                ),
                self.planner_result(
                    "source-acquire-plan",
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    },
                ),
                self.planner_result(
                    "source-reuse-plan",
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    },
                ),
            ]
        )
        main_model = ScriptedChatModel(
            [
                result(
                    "source-warmup-tool",
                    tool_calls=(
                        self.tool_call(
                            "source-warmup-finding",
                            "read_finding_detail",
                            {"finding_id": finding_id},
                        ),
                    ),
                ),
                result("source-warmup-final", content="已定位当前案例。"),
                result(
                    "source-acquire-tool",
                    tool_calls=(
                        self.tool_call(
                            "source-acquire-evidence",
                            "list_finding_evidence",
                            {"finding_id": finding_id, "limit": 20},
                        ),
                        self.tool_call(
                            "source-restore-finding",
                            "read_finding_detail",
                            {"finding_id": finding_id},
                        ),
                    ),
                ),
                result("source-acquire-final", content="已查看相关证据。"),
                result("source-reuse-main", content="已收到。"),
            ]
        )
        service = self.service(
            main_model,
            shadow_planner=TurnPlanner(model_client=planner_model),
            enable_evidence_source_control=False,
        )
        session = service.create_session(self.report_version_id)

        service.send_message(
            session.id,
            client_message_id="source-warmup",
            content="查看这个案例。",
        )
        acquisition = service.send_message(
            session.id,
            client_message_id="source-acquire",
            content="有哪些证据？",
        )
        reuse = service.send_message(
            session.id,
            client_message_id="source-reuse",
            content="再看一下这些证据。",
        )

        acquisition_trace = acquisition.source_preparation_shadow_trace
        self.assertEqual(acquisition_trace.status, "acquisition_required")
        self.assertEqual(
            acquisition_trace.preparation_result.required_tool,
            "list_finding_evidence",
        )
        self.assertEqual(
            acquisition.tool_names,
            ("list_finding_evidence", "read_finding_detail"),
        )
        reuse_trace = reuse.source_preparation_shadow_trace
        self.assertEqual(reuse_trace.status, "ready")
        bundle = reuse_trace.preparation_result
        self.assertEqual(bundle.bound_requirement.finding_ref, finding_id)
        self.assertTrue(bundle.source_artifact_ids)
        self.assertNotIn("source-artifact:", bundle.projected_source_message)
        self.assertEqual(reuse.tool_calls, ())
        self.assertEqual(reuse.llm_call_count, 1)
        self.assertEqual(reuse.total_tokens, 25)
        serialized_request = json.dumps(
            main_model.calls[-1]["messages"], ensure_ascii=False
        )
        self.assertNotIn("ready-source-projection-v1", serialized_request)
        self.assertNotIn("source-preparation-shadow-trace", serialized_request)

    def test_source_orchestrator_shadow_failure_is_traced_and_isolated(self):
        class ExplodingBinder(SubjectBinder):
            def bind(self, **kwargs):
                raise RuntimeError("injected SubjectBinder failure")

        planner_model = ScriptedChatModel(
            [
                self.planner_result(
                    "source-failure-plan",
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    },
                )
            ]
        )
        main_model = ScriptedChatModel(
            [result("source-failure-main", content="主流程仍然正常回答。")]
        )
        planner = TurnPlanner(model_client=planner_model)
        service = self.service(
            main_model,
            shadow_planner=planner,
            enable_evidence_source_control=False,
        )
        service.source_orchestrator = SourceOrchestrator(
            service.store,
            subject_binder=ExplodingBinder(),
        )
        session = service.create_session(self.report_version_id)
        before_focus = session.active_focus

        answer = service.send_message(
            session.id,
            client_message_id="source-orchestrator-failure",
            content="有哪些证据？",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.answer, "主流程仍然正常回答。")
        self.assertEqual(answer.llm_call_count, 1)
        self.assertEqual(answer.total_tokens, 25)
        self.assertEqual(answer.tool_calls, ())
        self.assertEqual(answer.source_preparation_shadow_trace.status, "error")
        self.assertEqual(
            answer.source_preparation_shadow_trace.error_code,
            "source_orchestrator_unexpected_error",
        )
        self.assertEqual(
            service.store.get_session(session.id).active_focus,
            before_focus,
        )

    def test_shadow_plan_does_not_change_focus_tool_path_receipt_or_grounding(self):
        def main_model(request_prefix):
            return ScriptedChatModel(
                [
                    result(
                        f"{request_prefix}-tool",
                        tool_calls=(
                            self.tool_call(
                                f"{request_prefix}-metric",
                                "lookup_report_metric",
                                {"metric_key": self.metric_key},
                            ),
                        ),
                    ),
                    result(
                        f"{request_prefix}-final",
                        content="冻结统计已经核验。",
                    ),
                ]
            )

        baseline_model = main_model("baseline")
        shadow_main_model = main_model("shadow")
        baseline_service = self.service(baseline_model)
        shadow_service = self.service(
            shadow_main_model,
            shadow_planner=TurnPlanner(
                model_client=ScriptedChatModel(
                    [
                        self.planner_result(
                            "tool-path-plan",
                            {"requirement_kind": "report_presentation"},
                        )
                    ]
                )
            ),
        )
        baseline_session = baseline_service.create_session(self.report_version_id)
        shadow_session = shadow_service.create_session(self.report_version_id)

        baseline = baseline_service.send_message(
            baseline_session.id,
            client_message_id="tool-path-baseline",
            content="报告统计是多少？",
        )
        shadow = shadow_service.send_message(
            shadow_session.id,
            client_message_id="tool-path-shadow",
            content="报告统计是多少？",
        )

        self.assertEqual(shadow.answer, baseline.answer)
        self.assertEqual(shadow.tool_names, baseline.tool_names)
        self.assertEqual(
            [call.arguments for call in shadow.tool_calls],
            [call.arguments for call in baseline.tool_calls],
        )
        self.assertEqual(
            [
                (receipt.operation, receipt.status, receipt.returned_refs)
                for receipt in shadow.query_receipts
            ],
            [
                (receipt.operation, receipt.status, receipt.returned_refs)
                for receipt in baseline.query_receipts
            ],
        )
        self.assertEqual(shadow.grounding_validation, baseline.grounding_validation)
        self.assertEqual(shadow.llm_call_count, baseline.llm_call_count)
        self.assertEqual(shadow.total_tokens, baseline.total_tokens)
        self.assertEqual(
            shadow_service.store.get_session(shadow_session.id).active_focus,
            baseline_service.store.get_session(baseline_session.id).active_focus,
        )
        for request in shadow_main_model.calls:
            self.assertNotIn(
                SUBMIT_TURN_PLAN_TOOL,
                json.dumps(request, ensure_ascii=False),
            )

    def test_shadow_failure_does_not_enter_agent_error_path(self):
        planner = TurnPlanner(
            model_client=ScriptedChatModel([result("planner-bad", content="not-json")])
        )
        main_model = ScriptedChatModel([result("main-ok", content="主流程正常回答。")])
        service = self.service(
            main_model,
            shadow_planner=planner,
            enable_evidence_source_control=False,
        )
        session = service.create_session(self.report_version_id)
        before_focus = session.active_focus
        answer = service.send_message(
            session.id, client_message_id="shadow-failure", content="你好"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.answer, "主流程正常回答。")
        self.assertEqual(answer.stop_reason, "completed")
        self.assertEqual(answer.planner_shadow_trace.planning_status, "error")
        self.assertEqual(
            answer.source_preparation_shadow_trace.status,
            "planner_error",
        )
        self.assertEqual(service.store.get_session(session.id).active_focus, before_focus)
        self.assertEqual(answer.grounding_validation.status, "passed")

    def test_unexpected_planner_execution_failure_is_traced_and_isolated(self):
        class ExplodingPlanner(TurnPlanner):
            def plan(self, **kwargs):
                raise RuntimeError("injected Planner failure")

        main_model = ScriptedChatModel(
            [result("planner-exception-main", content="主流程仍然正常回答。")]
        )
        service = self.service(
            main_model,
            shadow_planner=ExplodingPlanner(model_client=ScriptedChatModel()),
            enable_evidence_source_control=False,
        )
        session = service.create_session(self.report_version_id)
        before_focus = session.active_focus
        answer = service.send_message(
            session.id,
            client_message_id="planner-execution-failure",
            content="你好",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.answer, "主流程仍然正常回答。")
        self.assertEqual(answer.llm_call_count, 1)
        self.assertEqual(answer.total_tokens, 25)
        self.assertEqual(answer.planner_shadow_trace.planning_status, "error")
        self.assertEqual(
            answer.planner_shadow_trace.error_code, "planner_execution_error"
        )
        self.assertEqual(
            answer.planner_shadow_trace.planner_error, "planner_execution_error"
        )
        self.assertEqual(
            service.store.get_session(session.id).active_focus, before_focus
        )

    def test_disabled_planner_client_records_configuration_error(self):
        planner = TurnPlanner(
            model_client=QwenChatToolClient(
                api_key="",
                model="planner-disabled-test",
                max_retries=0,
                allowed_tool_names=frozenset({SUBMIT_TURN_PLAN_TOOL}),
            )
        )
        main_model = ScriptedChatModel([result("disabled-planner-main", content="你好。")])
        service = self.service(
            main_model,
            shadow_planner=planner,
            enable_evidence_source_control=False,
        )
        session = service.create_session(self.report_version_id)
        answer = service.send_message(
            session.id, client_message_id="disabled-planner", content="你好"
        )
        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.planner_shadow_trace.planning_status, "error")
        self.assertEqual(answer.planner_shadow_trace.error_code, "qwen_configuration")
        self.assertEqual(answer.planner_shadow_trace.planner_llm_call_count, 1)

    def test_phase4_planner_error_fails_closed_before_answer_qwen(self):
        planner = TurnPlanner(
            model_client=ScriptedChatModel([result("phase4-planner-bad", content="not-json")])
        )
        main_model = ScriptedChatModel()
        service = self.service(main_model, shadow_planner=planner)
        session = service.create_session(self.report_version_id)

        answer = service.send_message(
            session.id,
            client_message_id="phase4-planner-error",
            content="有哪些证据？",
        )

        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.stop_reason, "planner_control_unavailable")
        self.assertEqual(answer.answer, "无法安全判断本轮所需资料，请重试。")
        self.assertEqual(answer.llm_call_count, 0)
        self.assertEqual(main_model.calls, [])
        self.assertEqual(answer.planner_shadow_trace.planning_status, "error")

    def test_phase4_missing_planner_trace_fails_closed_before_answer_qwen(self):
        planner = TurnPlanner(
            model_client=ScriptedChatModel(
                [self.planner_result("phase4-missing-trace", {"requirement_kind": "none"})]
            )
        )
        main_model = ScriptedChatModel()
        service = self.service(main_model, shadow_planner=planner)
        session = service.create_session(self.report_version_id)

        with patch.object(
            service.store,
            "put_planner_shadow_trace",
            side_effect=RuntimeError("injected Planner trace persistence failure"),
        ):
            answer = service.send_message(
                session.id,
                client_message_id="phase4-planner-trace-missing",
                content="有哪些证据？",
            )

        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.stop_reason, "planner_control_unavailable")
        self.assertEqual(answer.llm_call_count, 0)
        self.assertEqual(main_model.calls, [])
        self.assertIsNone(answer.planner_shadow_trace)

    def test_phase4_unsupported_plan_is_a_deterministic_safe_final(self):
        planner = TurnPlanner(
            model_client=ScriptedChatModel(
                [
                    self.planner_result(
                        "phase4-unsupported-plan",
                        {
                            "requirement_kind": "unsupported",
                            "reason": "multiple_source_requirements",
                        },
                    )
                ]
            )
        )
        main_model = ScriptedChatModel()
        service = self.service(main_model, shadow_planner=planner)
        session = service.create_session(self.report_version_id)

        answer = service.send_message(
            session.id,
            client_message_id="phase4-unsupported",
            content="概括报告并列出全部证据。",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.stop_reason, "unsupported_requirement")
        self.assertIn("请拆分", answer.answer)
        self.assertEqual(answer.llm_call_count, 0)
        self.assertEqual(main_model.calls, [])

    def test_planner_pre_call_budget_is_independent_and_fail_closed(self):
        model = ScriptedChatModel()
        planner = TurnPlanner(
            model_client=model,
            max_input_tokens=500,
            max_total_tokens=700,
        )
        service = self.service(ScriptedChatModel())
        session = service.create_session(self.report_version_id)
        snapshot = PlannerInputSnapshot(
            current_user_message="证据" * 1_000,
            active_focus_exists=False,
            active_focus_type="",
            resolved_referent_status="not_applicable",
            resolved_referent_type="",
        )
        trace = planner.plan(
            session=session,
            turn_id="investigation-turn:planner-budget",
            input_snapshot=snapshot,
        )
        self.assertEqual(trace.planning_status, "error")
        self.assertEqual(trace.error_code, "planner_input_budget_exceeded")
        self.assertEqual(trace.planner_llm_call_count, 0)
        self.assertEqual(model.calls, [])

    def test_planner_timeout_retry_is_bounded_and_audited(self):
        model = ScriptedChatModel(
            [
                QwenChatError("timeout", "planner timeout", retryable=True),
                self.planner_result(
                    "planner-after-timeout", {"requirement_kind": "none"}
                ),
            ]
        )
        planner = TurnPlanner(model_client=model, max_retries=1)
        service = self.service(ScriptedChatModel())
        session = service.create_session(self.report_version_id)
        snapshot = PlannerInputSnapshot(
            current_user_message="谢谢",
            active_focus_exists=False,
            active_focus_type="",
            resolved_referent_status="not_applicable",
            resolved_referent_type="",
        )
        trace = planner.plan(
            session=session,
            turn_id="investigation-turn:planner-retry",
            input_snapshot=snapshot,
        )
        self.assertEqual(trace.planning_status, "ok")
        self.assertEqual(trace.planner_llm_call_count, 2)
        self.assertEqual(trace.planner_retry_count, 1)
        self.assertEqual(len(model.calls), 2)

    def test_shadow_planner_is_not_recalled_when_interrupted_turn_resumes(self):
        planner_model = ScriptedChatModel(
            [self.planner_result("resume-plan", {"requirement_kind": "none"})]
        )
        main_model = ScriptedChatModel(
            [
                result(
                    "resume-shadow-tool",
                    tool_calls=(
                        self.tool_call(
                            "resume-shadow-metric",
                            "lookup_report_metric",
                            {"metric_key": self.metric_key},
                        ),
                    ),
                ),
                RuntimeError("shadow resume interruption"),
                result("resume-shadow-final", content="恢复后完成回答。"),
            ]
        )
        service = self.service(
            main_model,
            shadow_planner=TurnPlanner(model_client=planner_model),
        )
        session = service.create_session(self.report_version_id)
        with self.assertRaisesRegex(RuntimeError, "shadow resume interruption"):
            service.send_message(
                session.id,
                client_message_id="shadow-resume",
                content="读取统计",
            )
        turn = service.store.get_turn_by_client_message(session.id, "shadow-resume")
        self.assertEqual(turn.status, "interrupted")
        self.assertEqual(len(planner_model.calls), 1)
        self.assertIsNotNone(service.store.get_planner_shadow_trace(turn.id))
        source_trace = service.store.get_source_preparation_shadow_trace(turn.id)
        self.assertIsNotNone(source_trace)
        answer = service.resume_turn(turn.id)
        self.assertEqual(answer.status, "completed")
        self.assertEqual(len(planner_model.calls), 1)
        self.assertEqual(answer.planner_shadow_trace.plan.requirement_kind, "none")
        self.assertEqual(
            answer.source_preparation_shadow_trace.trace_id,
            source_trace.trace_id,
        )

    def _phase4_focused_service(
        self,
        finding_id,
        plans,
        *main_responses,
        controlled_react=False,
    ):
        planner_model = ScriptedChatModel(
            [
                self.planner_result(
                    "phase4-warmup-plan",
                    {"requirement_kind": "focused_claim_support"},
                ),
                *[
                    self.planner_result(f"phase4-plan-{index}", plan)
                    for index, plan in enumerate(plans, start=1)
                ],
            ]
        )
        main_model = ScriptedChatModel(
            [
                result(
                    "phase4-warmup-tool",
                    tool_calls=(
                        self.tool_call(
                            "phase4-warmup-finding",
                            "read_finding_detail",
                            {"finding_id": finding_id},
                        ),
                    ),
                ),
                result("phase4-warmup-final", content="已锁定当前 Finding。"),
                *main_responses,
            ]
        )
        service = self.service(
            main_model,
            shadow_planner=TurnPlanner(model_client=planner_model),
            enable_controlled_evidence_react=controlled_react,
        )
        session = service.create_session(self.report_version_id)
        warmup = service.send_message(
            session.id,
            client_message_id="phase4-warmup",
            content="查看这条 Finding。",
        )
        self.assertEqual(warmup.status, "completed")
        self.assertEqual(warmup.tool_names, ("read_finding_detail",))
        self.assertEqual(
            service.store.get_session(session.id).active_focus["target_id"],
            finding_id,
        )
        return service, session, main_model, planner_model

    def _finding_with_evidence(self, minimum=1):
        for finding_id in self.context.finding_ids:
            items = self.facade.list_finding_evidence(
                self.report_version_id,
                finding_id,
                limit=20,
            )["items"]
            if len(items) >= minimum:
                return finding_id, items
        self.fail(f"fixture has no Finding with at least {minimum} Evidence items")

    def test_phase4_collection_acquires_projects_and_then_reuses_bundle(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        projected = []

        def answer_from_bundle(request):
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertEqual(request["tool_choice"], "none")
            self.assertEqual(request["tools"], [])
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            self.assertIn("ready_source_bundle", serialized)
            self.assertIn("ready-source-projection-v1", serialized)
            projected.append(serialized)
            return result(
                f"phase4-bundle-answer-{len(projected)}",
                content="已根据当前 Evidence 集合作答。",
            )

        plan = {
            "requirement_kind": "finding_evidence_collection",
            "evidence_types": None,
            "coverage": "discovery",
        }
        service, session, main_model, planner_model = self._phase4_focused_service(
            finding_id,
            [plan, plan],
            answer_from_bundle,
            answer_from_bundle,
        )
        first = service.send_message(
            session.id,
            client_message_id="phase4-collection-first",
            content="有哪些证据？",
        )
        second = service.send_message(
            session.id,
            client_message_id="phase4-collection-reuse",
            content="再列几条证据。",
        )

        self.assertEqual(first.tool_names, ("list_finding_evidence",))
        self.assertEqual(first.tool_calls[0].arguments["finding_id"], finding_id)
        self.assertEqual(first.tool_calls[0].arguments["evidence_types"], [])
        self.assertEqual(second.tool_calls, ())
        self.assertEqual(second.llm_call_count, 1)
        self.assertEqual(
            first.context_accounting[-1]["evidence_source_decision_history"],
            ["acquisition_required", "ready"],
        )
        self.assertEqual(
            first.context_accounting[-1]["evidence_source_attempt_count"], 1
        )
        self.assertTrue(
            any(
                item.source_kind.value == "current_evidence"
                for item in second.accessed_sources
            )
        )
        accounting = second.context_accounting[-1]
        self.assertEqual(accounting["context_projection"], "bundle_only_answer")
        self.assertTrue(accounting["bundle_only_context"])
        self.assertFalse(accounting["legacy_evidence_context_projected"])
        self.assertEqual(accounting["current_turn_tool_result_tokens"], 0)
        trace_bundle = second.source_preparation_shadow_trace.preparation_result
        self.assertTrue(accounting["ready_source_bundle_projected"])
        self.assertEqual(
            accounting["evidence_source_decision_history"], ["ready"]
        )
        self.assertEqual(
            accounting["ready_source_bundle_fingerprint"],
            trace_bundle.bundle_fingerprint,
        )
        self.assertNotIn("source-artifact:", projected[-1])
        self.assertEqual(len(projected), 2)
        self.assertEqual(len(planner_model.calls), 3)
        self.assertEqual(len(main_model.calls), 4)

        replay = service.send_message(
            session.id,
            client_message_id="phase4-collection-reuse",
            content="再列几条证据。",
        )
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(len(planner_model.calls), 3)
        self.assertEqual(
            len(service.store.list_query_receipts(session_id=session.id)),
            2,
        )

    def test_user_visible_answer_hides_bundle_evidence_ids(self):
        finding_id, items = self._finding_with_evidence()
        evidence_ref = items[0]["evidence_id"]
        short_ref = evidence_ref.rsplit(":", 1)[-1]
        source_type = items[0]["evidence_type"]
        expected_label = {
            "text": "文本证据",
            "comment": "评论证据",
            "ocr": "OCR 证据",
            "asr": "语音转写证据",
            "visual": "画面证据",
            "keyframe": "关键帧证据",
        }.get(source_type, "证据")
        plan = {
            "requirement_kind": "finding_evidence_collection",
            "evidence_types": None,
            "coverage": "discovery",
        }
        service, session, _main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [plan],
            result(
                "phase5-user-visible-id-answer",
                content=f"**证据（{short_ref}）** 可以支撑该判断，另见 {short_ref}。",
            ),
        )

        answer = service.send_message(
            session.id,
            client_message_id="phase5-user-visible-id",
            content="有哪些证据？",
        )

        self.assertEqual(answer.status, "completed")
        self.assertNotIn(short_ref, answer.answer)
        self.assertNotIn(evidence_ref, answer.answer)
        self.assertIn(expected_label, answer.answer)
        bundle = ReadySourceBundle.model_validate(
            service._checkpoint_state(session.id)["ready_source_bundle"]
        )
        self.assertIn(evidence_ref, bundle.ordered_projected_refs)

    def test_user_visible_answer_sanitizer_covers_stable_id_namespaces(self):
        service = self.service(ScriptedChatModel())
        answer = service._sanitize_user_visible_answer(
            {},
            (
                "report-version:0123456789abcdef0123456789abcdef "
                "report-claim:0123456789abcdef0123456789abcdef "
                "finding:audit_result:214 evidence:audit_result:214:ev_text_001 "
                "metric:report.total ev_comment_001"
            ),
        )

        for leaked in (
            "report-version:",
            "report-claim:",
            "finding:",
            "evidence:",
            "metric:",
            "ev_text_001",
            "ev_comment_001",
        ):
            self.assertNotIn(leaked, answer)
        self.assertIn("当前报告版本", answer)
        self.assertIn("当前报告结论", answer)
        self.assertIn("当前案例", answer)
        self.assertIn("该证据", answer)
        self.assertIn("该指标", answer)

    def test_phase5a_bundle_only_projection_excludes_legacy_evidence_channels(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        plan = {
            "requirement_kind": "finding_evidence_collection",
            "evidence_types": None,
            "coverage": "discovery",
        }
        service, session, _main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [plan],
            result("phase5a-seed-answer", content="已读取 Bundle。"),
        )
        completed = service.send_message(
            session.id,
            client_message_id="phase5a-decoy-seed",
            content="有哪些证据？",
        )
        self.assertEqual(completed.status, "completed")
        state = service._checkpoint_state(session.id)
        evidence_id = ReadySourceBundle.model_validate(
            state["ready_source_bundle"]
        ).ordered_projected_refs[0]
        legacy_markers = {
            "PHASE5_LEGACY_SUMMARY",
            "PHASE5_LEGACY_TOOL_PAYLOAD",
            "PHASE5_LEGACY_COMPACT_FACT",
            "PHASE5_LEGACY_FOCUSED_CASE",
            "PHASE5_LEGACY_FOCUSED_AUTHORITY",
            "PHASE5_LEGACY_ORDERED_REFERENT",
            "PHASE5_LEGACY_RECENT_LEDGER",
            "PHASE5_LEGACY_INHERITED_METRIC",
            "PHASE5_LEGACY_PREVIOUS_ASSISTANT",
            "PHASE5_LEGACY_FOCUS_LABEL",
            "PHASE5_LEGACY_RESOLVED_LABEL",
            "PHASE5_LEGACY_GROUNDING_FACT",
        }
        state.update(
            {
                "user_input": "PHASE5_CURRENT_USER",
                "summary_text": "PHASE5_LEGACY_SUMMARY",
                "active_focus": {
                    "type": "evidence",
                    "target_id": evidence_id,
                    "label": "PHASE5_LEGACY_FOCUS_LABEL",
                },
                "resolved_references": [
                    {
                        "expression": "这条证据",
                        "status": "resolved",
                        "target_type": "evidence",
                        "target_id": evidence_id,
                        "label": "PHASE5_LEGACY_RESOLVED_LABEL",
                    }
                ],
                "ordered_referents": [
                    {
                        "type": "evidence",
                        "target_id": evidence_id,
                        "label": "PHASE5_LEGACY_ORDERED_REFERENT",
                    }
                ],
                "recent_ledger_refs": [
                    {"excerpt": "PHASE5_LEGACY_RECENT_LEDGER"}
                ],
                "inherited_ledger_entries": [
                    {
                        "source_kind": "frozen_metric",
                        "metric_key": "metric:phase5-decoy",
                        "excerpt": "PHASE5_LEGACY_INHERITED_METRIC",
                    }
                ],
                "focused_case": {
                    "case_ref": "report-claim:" + ("a" * 32),
                    "case_text": "PHASE5_LEGACY_FOCUSED_CASE",
                },
                "focused_authoritative_facts": [
                    {"excerpt": "PHASE5_LEGACY_FOCUSED_AUTHORITY"}
                ],
                "recent_messages": [
                    {"role": "user", "content": "PHASE5_OLD_USER"},
                    {
                        "role": "assistant",
                        "content": "PHASE5_LEGACY_PREVIOUS_ASSISTANT",
                    },
                ],
                "conversation_continuity": [
                    {"role": "user", "content": "PHASE5_CONTINUITY_USER"}
                ],
                "working_messages": [
                    {"role": "user", "content": "PHASE5_CURRENT_USER"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "phase5-legacy-call",
                                "type": "function",
                                "function": {
                                    "name": "list_finding_evidence",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "phase5-legacy-call",
                        "name": "list_finding_evidence",
                        "content": json.dumps(
                            {
                                "status": "ok",
                                "data": {
                                    "preview": "PHASE5_LEGACY_TOOL_PAYLOAD"
                                },
                            }
                        ),
                    },
                ],
                "all_tool_calls": [
                    {
                        "id": "phase5-legacy-call",
                        "name": "read_evidence_detail",
                        "arguments": {"evidence_id": evidence_id},
                    }
                ],
                "all_tool_results": [
                    {
                        "status": "ok",
                        "data": {
                            "evidence_id": evidence_id,
                            "finding_id": finding_id,
                            "evidence_type": "comment",
                            "original_text": "PHASE5_LEGACY_COMPACT_FACT",
                        },
                        "provenance": [],
                    }
                ],
                "grounding_draft": "",
                "repair_mode": "",
            }
        )

        messages, accounting = service.context_builder.build_request(
            state=state,
            tool_definitions=[],
        )
        serialized = json.dumps(messages, ensure_ascii=False)

        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertEqual(messages[-1]["content"], "PHASE5_CURRENT_USER")
        self.assertIn("PHASE5_CONTINUITY_USER", serialized)
        self.assertIn("ready_source_bundle", serialized)
        self.assertIn("ready-source-projection-v1", serialized)
        for marker in legacy_markers:
            self.assertNotIn(marker, serialized)
        self.assertEqual(accounting["context_projection"], "bundle_only_answer")
        self.assertTrue(accounting["bundle_only_context"])
        self.assertFalse(accounting["legacy_evidence_context_projected"])
        self.assertEqual(accounting["current_turn_tool_result_tokens"], 0)
        self.assertGreater(accounting["pruned_working_tokens"], 0)

        for repair_mode, expected_projection in (
            ("scope_repair", "bundle_only_scope_repair"),
            ("semantic_rewrite", "bundle_only_semantic_rewrite"),
        ):
            repair_state = {
                **state,
                "repair_mode": repair_mode,
                "grounding_draft": "PHASE5_CURRENT_DRAFT",
                "grounding_issues": [
                    {
                        "issue_type": "ambiguous_numeric_fact",
                        "unsupported_claim": "PHASE5_CURRENT_DRAFT",
                        "numeric_facts": ["9条"],
                        "available_authoritative_facts": [
                            "PHASE5_LEGACY_GROUNDING_FACT"
                        ],
                        "source_refs": [evidence_id],
                        "candidate_metric_keys": ["metric:phase5-decoy"],
                        "message": "当前草稿中的数字需要重写。",
                    }
                ],
            }
            repair_messages, repair_accounting = (
                service.context_builder.build_request(
                    state=repair_state,
                    tool_definitions=[],
                )
            )
            repair_serialized = json.dumps(repair_messages, ensure_ascii=False)
            self.assertEqual(
                [item["role"] for item in repair_messages],
                ["system", "user"],
            )
            self.assertIn("PHASE5_CURRENT_DRAFT", repair_serialized)
            self.assertIn("ready_source_bundle", repair_serialized)
            for marker in legacy_markers:
                self.assertNotIn(marker, repair_serialized)
            self.assertEqual(
                repair_accounting["context_projection"],
                expected_projection,
            )
            self.assertTrue(repair_accounting["bundle_only_context"])
            self.assertEqual(
                repair_accounting["current_turn_tool_result_tokens"],
                0,
            )

    def test_phase5a_empty_collection_is_answered_from_an_empty_bundle(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        evidence_type = "visual"

        def answer_empty_bundle(request):
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            self.assertEqual(request["tools"], [])
            self.assertEqual(request["tool_choice"], "none")
            self.assertIn('"sources": []', request["messages"][0]["content"])
            return result(
                "phase5a-empty-answer",
                content="当前筛选条件下没有 Evidence。",
            )

        service, session, _main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [
                {
                    "requirement_kind": "finding_evidence_collection",
                    "evidence_types": [evidence_type],
                    "coverage": "discovery",
                }
            ],
            answer_empty_bundle,
        )
        call = self.tool_call(
            "phase5a-seeded-empty",
            "list_finding_evidence",
            {
                "finding_id": finding_id,
                "evidence_types": [evidence_type],
                "limit": 20,
            },
        )
        query_arguments = service.tool_service.canonical_arguments(call)
        empty_data = {
            "items": [],
            "total": 0,
            "returned": 0,
            "has_more": False,
        }
        query_fingerprint = service.report_facade.query_fingerprint(
            call.name, query_arguments
        )
        result_fingerprint = stable_hash(
            {
                "tool_name": call.name,
                "arguments": query_arguments,
                "data": empty_data,
                "sources": [],
            }
        )
        empty_result = ToolResultEnvelope(
            status="ok",
            data=empty_data,
            provenance=(),
            result_fingerprint=result_fingerprint,
            query_details=FindingEvidenceQueryDetails(
                operation="finding_evidence_list",
                finding_ref=finding_id,
                evidence_types=(evidence_type,),
                returned_evidence_refs=(),
                returned_count=0,
                total=0,
                has_more=False,
            ),
        )
        payload = service.tool_service.model_payload(empty_result)
        warmup_turn = service.store.get_turn_by_client_message(
            session.id, "phase4-warmup"
        )
        receipt = service.tool_service.query_receipt(
            session_id=session.id,
            turn_id=warmup_turn.id,
            report_version_id=session.report_version_id,
            snapshot_hash=session.snapshot_hash,
            call=call,
            result=empty_result,
            model_payload=payload,
        )
        capture = EvidenceCanonicalCapture(
            tool_name="list_finding_evidence",
            normalized_query=query_arguments,
            canonical_payload=empty_data,
            provenance=(),
            query_fingerprint=query_fingerprint,
            result_fingerprint=result_fingerprint,
        )
        compiled = EvidenceArtifactCompiler.compile(
            source_snapshot_id=session.source_snapshot_id,
            receipt=receipt,
            capture=capture,
        )
        service.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )
        answer = service.send_message(
            session.id,
            client_message_id="phase5a-empty-collection",
            content=f"只看 {evidence_type} Evidence。",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.tool_names, ())
        bundle = ReadySourceBundle.model_validate(
            service._checkpoint_state(session.id)["ready_source_bundle"]
        )
        self.assertEqual(bundle.source_artifact_ids, ())
        self.assertEqual(bundle.ordered_projected_refs, ())
        self.assertEqual(
            answer.context_accounting[-1]["context_projection"],
            "bundle_only_answer",
        )

    def test_phase5a_answer_tool_call_is_rejected_without_reentry(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        evidence_id = self.facade.list_finding_evidence(
            self.report_version_id, finding_id, limit=20
        )["items"][0]["evidence_id"]
        plan = {
            "requirement_kind": "finding_evidence_collection",
            "evidence_types": None,
            "coverage": "discovery",
        }
        service, session, main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [plan],
            result(
                "phase5a-illegal-tool-call",
                tool_calls=(
                    self.tool_call(
                        "phase5a-illegal-detail",
                        "read_evidence_detail",
                        {"evidence_id": evidence_id},
                    ),
                ),
            ),
        )

        answer = service.send_message(
            session.id,
            client_message_id="phase5a-reject-tool-call",
            content="有哪些证据？",
        )

        self.assertEqual(answer.status, "error")
        self.assertIn("工具已禁用", answer.answer)
        self.assertEqual(answer.tool_names, ("list_finding_evidence",))
        self.assertNotIn(
            "read_evidence_detail",
            [item.tool_name for item in answer.query_receipts],
        )
        self.assertEqual(len(main_model.calls), 3)
        self.assertEqual(main_model.calls[-1]["tools"], [])
        self.assertEqual(main_model.calls[-1]["tool_choice"], "none")

    def test_phase5b_detail_reentry_recompiles_bundle_without_raw_tool_context(self):
        finding_id, items = self._finding_with_evidence()
        evidence_id = items[0]["evidence_id"]
        observed_requests = []

        def request_detail(request):
            observed_requests.append(request)
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            self.assertEqual(request["tool_choice"], "auto")
            self.assertEqual(
                [item["function"]["name"] for item in request["tools"]],
                ["read_evidence_detail"],
            )
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertIn("ready-source-projection-v1", serialized)
            self.assertIn(evidence_id, serialized)
            return result(
                "phase5b-detail-request",
                tool_calls=(
                    self.tool_call(
                        "phase5b-detail-call",
                        "read_evidence_detail",
                        {"evidence_id": evidence_id},
                    ),
                ),
            )

        def answer_expanded_bundle(request):
            observed_requests.append(request)
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertIn("ready-source-projection-v2", serialized)
            self.assertIn('evidence_detail', serialized)
            self.assertNotIn('"role": "tool"', serialized)
            return result(
                "phase5b-expanded-answer",
                content="已根据重新编译后的详细 Evidence 作答。",
            )

        service, session, _main_model, _planner_model = (
            self._phase4_focused_service(
                finding_id,
                [
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    }
                ],
                request_detail,
                answer_expanded_bundle,
                controlled_react=True,
            )
        )
        answer = service.send_message(
            session.id,
            client_message_id="phase5b-detail-reentry",
            content="有哪些证据？需要时读取一条详情。",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(
            answer.tool_names,
            ("list_finding_evidence", "read_evidence_detail"),
        )
        self.assertEqual(
            tuple(item.tool_name for item in answer.query_receipts),
            ("list_finding_evidence", "read_evidence_detail"),
        )
        state = service._checkpoint_state(session.id)
        bundle = ReadySourceBundle.model_validate(state["ready_source_bundle"])
        self.assertEqual(bundle.schema_version, "ready-source-bundle-v2")
        self.assertEqual(bundle.react_iteration_count, 1)
        self.assertEqual(
            tuple(item.evidence_ref for item in bundle.supplemental_requirements),
            (evidence_id,),
        )
        self.assertEqual(len(bundle.query_result_artifact_ids), 2)
        self.assertEqual(len(bundle.source_artifact_ids), len(bundle.ordered_projected_refs))
        detail_source = next(
            service.store.get_source_artifact(artifact_id)
            for artifact_id in bundle.source_artifact_ids
            if service.store.get_source_artifact(artifact_id).stable_source_ref
            == evidence_id
        )
        self.assertEqual(detail_source.content_level, "evidence_detail")
        fingerprints = state["evidence_react_bundle_fingerprints"]
        self.assertEqual(len(fingerprints), 2)
        self.assertEqual(bundle.parent_bundle_fingerprint, fingerprints[0])
        self.assertEqual(bundle.bundle_fingerprint, fingerprints[1])
        self.assertNotEqual(fingerprints[0], fingerprints[1])
        accounting = answer.context_accounting[-1]
        self.assertEqual(accounting["context_projection"], "bundle_only_answer")
        self.assertEqual(accounting["current_turn_tool_result_tokens"], 0)
        self.assertEqual(accounting["controlled_react_iteration_count"], 1)
        self.assertEqual(len(observed_requests), 2)

    def test_phase5b_rejects_cross_bundle_and_disallowed_tool_calls(self):
        finding_id, items = self._finding_with_evidence()
        current_refs = {item["evidence_id"] for item in items}
        outside_ref = next(
            evidence_id
            for evidence_id in self.context.evidence_ids
            if evidence_id not in current_refs
        )
        scenarios = (
            (
                "cross-bundle",
                "read_evidence_detail",
                {"evidence_id": outside_ref},
                "controlled_react_scope_mismatch",
            ),
            (
                "collection-requery",
                "list_finding_evidence",
                {"finding_id": finding_id, "evidence_types": [], "limit": 20},
                "controlled_react_tool_not_allowed",
            ),
        )
        for label, tool_name, arguments, expected_error in scenarios:
            with self.subTest(label=label):
                service, session, main_model, _planner_model = (
                    self._phase4_focused_service(
                        finding_id,
                        [
                            {
                                "requirement_kind": "finding_evidence_collection",
                                "evidence_types": None,
                                "coverage": "discovery",
                            }
                        ],
                        result(
                            f"phase5b-{label}-request",
                            tool_calls=(
                                self.tool_call(
                                    f"phase5b-{label}-call",
                                    tool_name,
                                    arguments,
                                ),
                            ),
                        ),
                        controlled_react=True,
                    )
                )
                answer = service.send_message(
                    session.id,
                    client_message_id=f"phase5b-{label}",
                    content="有哪些证据？",
                )

                self.assertEqual(answer.status, "error")
                self.assertEqual(answer.stop_reason, "controlled_react_rejected")
                self.assertEqual(
                    service._checkpoint_state(session.id)["error"], expected_error
                )
                self.assertEqual(
                    tuple(item.tool_name for item in answer.query_receipts),
                    ("list_finding_evidence",),
                )
                self.assertEqual(len(main_model.calls), 3)
                self.assertEqual(
                    [
                        item["function"]["name"]
                        for item in main_model.calls[-1]["tools"]
                    ],
                    ["read_evidence_detail"],
                )

    def test_phase5b_repeated_detail_query_stops_without_new_receipt(self):
        finding_id, items = self._finding_with_evidence()
        evidence_id = items[0]["evidence_id"]

        def detail_call(request_id, call_id):
            return result(
                request_id,
                tool_calls=(
                    self.tool_call(
                        call_id,
                        "read_evidence_detail",
                        {"evidence_id": evidence_id},
                    ),
                ),
            )

        service, session, _main_model, _planner_model = (
            self._phase4_focused_service(
                finding_id,
                [
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    }
                ],
                detail_call("phase5b-first-detail", "phase5b-first-detail-call"),
                detail_call("phase5b-repeat-detail", "phase5b-repeat-detail-call"),
                result(
                    "phase5b-repeat-forced-final",
                    content="已停止重复查询，并根据当前 Bundle 完成回答。",
                ),
                controlled_react=True,
            )
        )
        answer = service.send_message(
            session.id,
            client_message_id="phase5b-repeat-detail",
            content="有哪些证据？",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.stop_reason, "controlled_react_no_progress")
        state = service._checkpoint_state(session.id)
        self.assertEqual(state["error"], "")
        self.assertTrue(state["tools_disabled"])
        self.assertFalse(state["evidence_react_enabled"])
        self.assertEqual(
            tuple(item.tool_name for item in answer.query_receipts),
            ("list_finding_evidence", "read_evidence_detail"),
        )
        self.assertEqual(
            len(
                service.store.list_query_artifact_index(session_id=session.id)
            ),
            2,
        )
        self.assertEqual(
            answer.context_accounting[-1]["context_projection"],
            "bundle_only_answer",
        )
        self.assertEqual(
            answer.context_accounting[-1]["current_turn_tool_result_tokens"],
            0,
        )

    def test_phase5b_stops_after_two_detail_expansions(self):
        finding_id, items = self._finding_with_evidence(minimum=2)
        evidence_refs = tuple(item["evidence_id"] for item in items[:2])

        def request_detail(index):
            def callback(request):
                self.assertEqual(request["tool_choice"], "auto")
                self.assertEqual(
                    [item["function"]["name"] for item in request["tools"]],
                    ["read_evidence_detail"],
                )
                return result(
                    f"phase5b-budget-detail-{index}",
                    tool_calls=(
                        self.tool_call(
                            f"phase5b-budget-call-{index}",
                            "read_evidence_detail",
                            {"evidence_id": evidence_refs[index]},
                        ),
                    ),
                )

            return callback

        def final_answer(request):
            self.assertEqual(request["tool_choice"], "none")
            self.assertEqual(request["tools"], [])
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertIn("ready-source-projection-v2", serialized)
            for evidence_ref in evidence_refs:
                self.assertIn(evidence_ref, serialized)
            return result(
                "phase5b-budget-final",
                content="已在两次受控扩展预算内完成回答。",
            )

        service, session, _main_model, _planner_model = (
            self._phase4_focused_service(
                finding_id,
                [
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    }
                ],
                request_detail(0),
                request_detail(1),
                final_answer,
                controlled_react=True,
            )
        )
        answer = service.send_message(
            session.id,
            client_message_id="phase5b-two-expansions",
            content="有哪些证据？",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(
            answer.tool_names,
            (
                "list_finding_evidence",
                "read_evidence_detail",
                "read_evidence_detail",
            ),
        )
        state = service._checkpoint_state(session.id)
        bundle = ReadySourceBundle.model_validate(state["ready_source_bundle"])
        self.assertEqual(bundle.react_iteration_count, 2)
        self.assertEqual(
            tuple(item.evidence_ref for item in bundle.supplemental_requirements),
            evidence_refs,
        )
        self.assertEqual(len(bundle.query_result_artifact_ids), 3)
        self.assertEqual(len(state["evidence_react_bundle_fingerprints"]), 3)
        self.assertFalse(state["evidence_react_enabled"])
        self.assertTrue(state["tools_disabled"])

    def test_phase5b_resume_after_bundle_two_does_not_repeat_acquisition(self):
        finding_id, items = self._finding_with_evidence()
        evidence_id = items[0]["evidence_id"]
        service, session, main_model, planner_model = (
            self._phase4_focused_service(
                finding_id,
                [
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    }
                ],
                result(
                    "phase5b-resume-detail-request",
                    tool_calls=(
                        self.tool_call(
                            "phase5b-resume-detail-call",
                            "read_evidence_detail",
                            {"evidence_id": evidence_id},
                        ),
                    ),
                ),
                RuntimeError("injected Phase 5B answer interruption"),
                result(
                    "phase5b-resume-final",
                    content="恢复后从 Bundle #2 完成回答。",
                ),
                controlled_react=True,
            )
        )
        with self.assertRaisesRegex(
            RuntimeError, "injected Phase 5B answer interruption"
        ):
            service.send_message(
                session.id,
                client_message_id="phase5b-resume",
                content="有哪些证据？",
            )

        turn = service.store.get_turn_by_client_message(session.id, "phase5b-resume")
        interrupted_state = service._checkpoint_state(session.id)
        interrupted_bundle = ReadySourceBundle.model_validate(
            interrupted_state["ready_source_bundle"]
        )
        interrupted_receipts = service.store.list_query_receipts(turn_id=turn.id)
        interrupted_artifacts = service.store.list_query_artifact_index(
            session_id=session.id
        )
        planner_calls = len(planner_model.calls)

        answer = service.resume_turn(turn.id)

        self.assertEqual(answer.status, "completed")
        self.assertEqual(len(planner_model.calls), planner_calls)
        self.assertEqual(
            service.store.list_query_receipts(turn_id=turn.id),
            interrupted_receipts,
        )
        self.assertEqual(
            service.store.list_query_artifact_index(session_id=session.id),
            interrupted_artifacts,
        )
        resumed_bundle = ReadySourceBundle.model_validate(
            service._checkpoint_state(session.id)["ready_source_bundle"]
        )
        self.assertEqual(
            resumed_bundle.bundle_fingerprint,
            interrupted_bundle.bundle_fingerprint,
        )
        self.assertEqual(len(main_model.calls), 5)

    def test_phase5b_token_guard_stops_before_answering_from_bundle_two(self):
        finding_id, items = self._finding_with_evidence()
        evidence_id = items[0]["evidence_id"]
        service, session, main_model, _planner_model = (
            self._phase4_focused_service(
                finding_id,
                [
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    }
                ],
                result(
                    "phase5b-token-detail-request",
                    tool_calls=(
                        self.tool_call(
                            "phase5b-token-detail-call",
                            "read_evidence_detail",
                            {"evidence_id": evidence_id},
                        ),
                    ),
                    input_tokens=95_000,
                    output_tokens=1,
                ),
                controlled_react=True,
            )
        )
        answer = service.send_message(
            session.id,
            client_message_id="phase5b-token-guard",
            content="有哪些证据？",
        )

        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.stop_reason, "token_budget")
        self.assertEqual(len(main_model.calls), 3)
        self.assertEqual(
            answer.tool_names,
            ("list_finding_evidence", "read_evidence_detail"),
        )
        bundle = ReadySourceBundle.model_validate(
            service._checkpoint_state(session.id)["ready_source_bundle"]
        )
        self.assertEqual(bundle.schema_version, "ready-source-bundle-v2")
        self.assertEqual(bundle.react_iteration_count, 1)

    def test_phase5b_detail_artifact_failure_fails_closed(self):
        finding_id, items = self._finding_with_evidence()
        evidence_id = items[0]["evidence_id"]
        service, session, main_model, _planner_model = (
            self._phase4_focused_service(
                finding_id,
                [
                    {
                        "requirement_kind": "finding_evidence_collection",
                        "evidence_types": None,
                        "coverage": "discovery",
                    }
                ],
                result(
                    "phase5b-artifact-detail-request",
                    tool_calls=(
                        self.tool_call(
                            "phase5b-artifact-detail-call",
                            "read_evidence_detail",
                            {"evidence_id": evidence_id},
                        ),
                    ),
                ),
                controlled_react=True,
            )
        )
        original_persist = service.store.put_query_receipt_artifacts

        def fail_detail_persistence(receipt, *args, **kwargs):
            if receipt.tool_name == "read_evidence_detail":
                raise RuntimeError("injected Phase 5B detail Artifact failure")
            return original_persist(receipt, *args, **kwargs)

        with patch.object(
            service.store,
            "put_query_receipt_artifacts",
            side_effect=fail_detail_persistence,
        ), patch("backend.investigation.agent.logger.exception"):
            answer = service.send_message(
                session.id,
                client_message_id="phase5b-artifact-failure",
                content="有哪些证据？",
            )

        self.assertEqual(answer.status, "error")
        self.assertEqual(
            answer.stop_reason,
            "controlled_react_acquisition_failure",
        )
        self.assertEqual(len(main_model.calls), 3)
        self.assertEqual(
            answer.tool_names,
            ("list_finding_evidence", "read_evidence_detail"),
        )
        artifact_index = service.store.list_query_artifact_index(
            session_id=session.id
        )
        self.assertEqual(len(artifact_index), 1)
        self.assertEqual(
            service.store.get_query_result_for_receipt(
                answer.query_receipts[-1].receipt_id
            ),
            None,
        )

    def test_phase4_oversized_canonical_result_reopens_and_reprojects_without_loss(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        marker = "phase4-canonical-start:" + ("x" * 6_000) + ":canonical-end"

        def answer_from_bundle(request):
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertEqual(request["tool_choice"], "none")
            self.assertEqual(request["tools"], [])
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            self.assertIn(":canonical-end", serialized)
            return result(
                "phase4-oversized-answer",
                content="已根据重新投影的 Evidence 集合作答。",
            )

        plan = {
            "requirement_kind": "finding_evidence_collection",
            "evidence_types": None,
            "coverage": "discovery",
        }
        service, session, _main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [plan],
            answer_from_bundle,
        )
        service.tool_service.max_result_size = 4_000
        original_dispatch = service.tool_service._dispatch

        def oversized_dispatch(report_version_id, name, arguments, query_fingerprint):
            data, provenance = original_dispatch(
                report_version_id, name, arguments, query_fingerprint
            )
            if name == "list_finding_evidence":
                data = dict(data)
                data["items"] = [dict(item) for item in data["items"]]
                data["items"][0]["original_text_preview"] = marker
            return data, provenance

        with patch.object(service.tool_service, "_dispatch", oversized_dispatch):
            answer = service.send_message(
                session.id,
                client_message_id="phase4-oversized-canonical",
                content="有哪些证据？",
            )

        self.assertEqual(answer.status, "completed")
        state = service._checkpoint_state(session.id)
        tool_messages = [
            item
            for item in state["working_messages"]
            if item.get("role") == "tool"
            and item.get("name") == "list_finding_evidence"
        ]
        self.assertEqual(len(tool_messages), 1)
        bounded_payload = json.loads(tool_messages[0]["content"])
        self.assertTrue(bounded_payload["truncated"])
        self.assertLessEqual(len(tool_messages[0]["content"]), 4_000)
        self.assertNotIn(":canonical-end", tool_messages[0]["content"])

        reopened = InvestigationStore(service.store.db_path)
        sources = reopened.list_source_artifacts(session_id=session.id)
        canonical_source = next(
            item
            for item in sources
            if item.canonical_content.get("original_text_preview") == marker
        )
        self.assertEqual(canonical_source.canonical_content["original_text_preview"], marker)
        requirement = BoundEvidenceCollectionRequirement(
            requirement_kind="finding_evidence_collection",
            session_id=session.id,
            report_version_id=session.report_version_id,
            source_snapshot_id=session.source_snapshot_id,
            snapshot_hash=session.snapshot_hash,
            finding_ref=finding_id,
            evidence_types=None,
            coverage="discovery",
        )
        reopened_bundle = SourceOrchestrator(reopened).prepare(requirement)
        self.assertIsInstance(reopened_bundle, ReadySourceBundle)
        self.assertIn(":canonical-end", reopened_bundle.projected_source_message)
        self.assertEqual(
            reopened_bundle.bundle_fingerprint,
            ReadySourceBundle.model_validate(
                state["ready_source_bundle"]
            ).bundle_fingerprint,
        )

    def test_phase4_answer_interruption_resumes_without_reacquisition(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )

        def resumed_answer(request):
            serialized = json.dumps(request["messages"], ensure_ascii=False)
            self.assertEqual(request["tool_choice"], "none")
            self.assertEqual(request["tools"], [])
            self.assertEqual(
                [item["role"] for item in request["messages"]],
                ["system", "user"],
            )
            self.assertIn("ready_source_bundle", serialized)
            return result(
                "phase4-resumed-answer",
                content="恢复后根据已准备的 Evidence 作答。",
            )

        plan = {
            "requirement_kind": "finding_evidence_collection",
            "evidence_types": None,
            "coverage": "discovery",
        }
        service, session, main_model, planner_model = self._phase4_focused_service(
            finding_id,
            [plan],
            RuntimeError("injected interruption after source preparation"),
            resumed_answer,
        )
        with patch.object(
            service.tool_service,
            "execute_with_canonical",
            wraps=service.tool_service.execute_with_canonical,
        ) as execute, patch.object(
            service.store,
            "put_query_receipt_artifacts",
            wraps=service.store.put_query_receipt_artifacts,
        ) as persist:
            with self.assertRaisesRegex(
                RuntimeError, "interruption after source preparation"
            ):
                service.send_message(
                    session.id,
                    client_message_id="phase4-answer-interruption",
                    content="有哪些证据？",
                )

            turn = service.store.get_turn_by_client_message(
                session.id, "phase4-answer-interruption"
            )
            self.assertEqual(turn.status, "interrupted")
            state = service._checkpoint_state(session.id)
            prepared_bundle = ReadySourceBundle.model_validate(
                state["ready_source_bundle"]
            )
            before_receipts = service.store.list_query_receipts(turn_id=turn.id)
            before_results = service.store.list_query_result_artifacts(
                session_id=session.id
            )
            before_sources = service.store.list_source_artifacts(
                session_id=session.id
            )
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(persist.call_count, 1)
            self.assertEqual(len(before_receipts), 1)
            self.assertEqual(len(planner_model.calls), 2)

            answer = service.resume_turn(turn.id)

            self.assertEqual(answer.status, "completed")
            self.assertEqual(answer.tool_names, ("list_finding_evidence",))
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(persist.call_count, 1)
            self.assertEqual(len(planner_model.calls), 2)
            self.assertEqual(len(main_model.calls), 4)
            self.assertEqual(
                service.store.list_query_receipts(turn_id=turn.id),
                before_receipts,
            )
            self.assertEqual(
                service.store.list_query_result_artifacts(session_id=session.id),
                before_results,
            )
            self.assertEqual(
                service.store.list_source_artifacts(session_id=session.id),
                before_sources,
            )
            resumed_bundle = ReadySourceBundle.model_validate(
                service._checkpoint_state(session.id)["ready_source_bundle"]
            )
            self.assertEqual(
                resumed_bundle.bundle_fingerprint,
                prepared_bundle.bundle_fingerprint,
            )
            self.assertEqual(
                answer.context_accounting[-1]["ready_source_bundle_fingerprint"],
                prepared_bundle.bundle_fingerprint,
            )
            self.assertEqual(
                answer.context_accounting[-1]["context_projection"],
                "bundle_only_answer",
            )
            self.assertEqual(
                answer.context_accounting[-1]["current_turn_tool_result_tokens"],
                0,
            )

    def test_phase4_exact_evidence_filter_does_not_satisfy_unfiltered_collection(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id,
                item,
                evidence_types=["comment"],
                limit=20,
            )["items"]
        )
        service, session, _main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [
                {
                    "requirement_kind": "finding_evidence_collection",
                    "evidence_types": ["comment"],
                    "coverage": "discovery",
                },
                {
                    "requirement_kind": "finding_evidence_collection",
                    "evidence_types": None,
                    "coverage": "discovery",
                },
            ],
            result("phase4-comment-final", content="已列出评论 Evidence。"),
            result("phase4-all-final", content="已列出当前 Evidence。"),
        )
        comments = service.send_message(
            session.id,
            client_message_id="phase4-comments",
            content="只看评论证据。",
        )
        all_types = service.send_message(
            session.id,
            client_message_id="phase4-all-types",
            content="还有哪些证据？",
        )

        self.assertEqual(comments.tool_calls[0].arguments["evidence_types"], ["comment"])
        self.assertEqual(all_types.tool_calls[0].arguments["evidence_types"], [])
        self.assertEqual(comments.tool_names, ("list_finding_evidence",))
        self.assertEqual(all_types.tool_names, ("list_finding_evidence",))

    def test_phase4_collection_preview_cannot_satisfy_evidence_detail(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        service, session, main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [
                {
                    "requirement_kind": "finding_evidence_collection",
                    "evidence_types": None,
                    "coverage": "discovery",
                },
                {"requirement_kind": "evidence_detail"},
            ],
            result("phase4-collection-final", content="已列出 Evidence。"),
            result("phase4-detail-final", content="这是该条 Evidence 的完整内容。"),
        )
        collection = service.send_message(
            session.id,
            client_message_id="phase4-before-detail",
            content="有哪些证据？",
        )
        focused_evidence = service.store.get_session(session.id).last_evidence_id
        detail = service.send_message(
            session.id,
            client_message_id="phase4-detail",
            content="刚才那条证据的原文是什么？",
        )

        self.assertEqual(collection.tool_names, ("list_finding_evidence",))
        self.assertTrue(focused_evidence)
        self.assertEqual(detail.tool_names, ("read_evidence_detail",))
        self.assertEqual(
            detail.tool_calls[0].arguments,
            {"evidence_id": focused_evidence},
        )
        serialized = json.dumps(main_model.calls[-1]["messages"], ensure_ascii=False)
        self.assertEqual(
            [item["role"] for item in main_model.calls[-1]["messages"]],
            ["system", "user"],
        )
        self.assertIn("evidence_detail", serialized)
        self.assertIn("content_level", serialized)
        self.assertEqual(
            detail.context_accounting[-1]["context_projection"],
            "bundle_only_answer",
        )
        self.assertEqual(
            detail.context_accounting[-1]["current_turn_tool_result_tokens"],
            0,
        )

    def test_phase4_complete_with_has_more_stops_without_repeating_tool(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        service, session, main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [
                {
                    "requirement_kind": "finding_evidence_collection",
                    "evidence_types": None,
                    "coverage": "complete",
                }
            ],
        )
        partial_call = self.tool_call(
            "phase4-seeded-partial",
            "list_finding_evidence",
            {"finding_id": finding_id, "evidence_types": [], "limit": 1},
        )
        partial_result, capture = service.tool_service.execute_with_canonical(
            report_version_id=self.report_version_id,
            call=partial_call,
        )
        self.assertTrue(partial_result.data["has_more"])
        warmup_turn = service.store.get_turn_by_client_message(
            session.id, "phase4-warmup"
        )
        payload = service.tool_service.model_payload(partial_result)
        receipt = service.tool_service.query_receipt(
            session_id=session.id,
            turn_id=warmup_turn.id,
            report_version_id=self.report_version_id,
            snapshot_hash=session.snapshot_hash,
            call=partial_call,
            result=partial_result,
            model_payload=payload,
        )
        compiled = EvidenceArtifactCompiler.compile(
            source_snapshot_id=session.source_snapshot_id,
            receipt=receipt,
            capture=capture,
        )
        service.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )
        answer = service.send_message(
            session.id,
            client_message_id="phase4-complete-has-more",
            content="全部证据一条不要漏。",
        )

        self.assertEqual(answer.status, "completed")
        self.assertEqual(answer.stop_reason, "unsupported_complete")
        self.assertEqual(answer.tool_names, ())
        self.assertEqual(answer.llm_call_count, 0)
        self.assertIn("无法证明已经取得全部", answer.answer)
        self.assertEqual(len(main_model.calls), 2)

    def test_phase4_artifact_failure_fails_closed_after_one_acquisition(self):
        finding_id = next(
            item
            for item in self.context.finding_ids
            if self.facade.list_finding_evidence(
                self.report_version_id, item, limit=20
            )["items"]
        )
        service, session, main_model, _planner_model = self._phase4_focused_service(
            finding_id,
            [
                {
                    "requirement_kind": "finding_evidence_collection",
                    "evidence_types": None,
                    "coverage": "discovery",
                }
            ],
        )
        with patch.object(
            service.store,
            "put_query_receipt_artifacts",
            side_effect=RuntimeError("injected Phase 4 Artifact failure"),
        ), patch("backend.investigation.agent.logger.exception"):
            answer = service.send_message(
                session.id,
                client_message_id="phase4-artifact-failure",
                content="有哪些证据？",
            )

        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.stop_reason, "source_acquisition_unsatisfied")
        self.assertEqual(answer.tool_names, ("list_finding_evidence",))
        self.assertEqual(answer.llm_call_count, 0)
        self.assertEqual(len(main_model.calls), 2)
        self.assertEqual(
            service.store.list_query_artifact_index(session_id=session.id), ()
        )

    def test_gold_evaluation_labels_conform_to_turn_plan_contract(self):
        fixture = json.loads(PLANNER_GOLD_PATH.read_text(encoding="utf-8"))
        self.assertEqual(fixture["frozen_prompt_version"], "turn-planner-shadow-v1.1")
        all_cases = [
            case
            for cases in fixture["suites"].values()
            for case in cases
        ]
        self.assertEqual(len(fixture["suites"]["smoke"]), 14)
        self.assertGreaterEqual(len(fixture["suites"]["blind"]), 30)
        self.assertEqual(len({case["id"] for case in all_cases}), len(all_cases))
        self.assertEqual(
            len({case["message"] for case in all_cases}), len(all_cases)
        )

        observed_evidence_types = set()
        observed_coverages = set()
        for case in all_cases:
            expected = case["expected"]
            requirement = expected["requirement_kind"]
            PlannerInputSnapshot.model_validate(
                {
                    "current_user_message": case["message"],
                    **case["context"],
                }
            )
            plan = {"requirement_kind": requirement}
            if requirement == "finding_evidence_collection":
                plan.update(
                    {
                        "evidence_types": expected["evidence_types"],
                        "coverage": expected["coverage"],
                    }
                )
                observed_evidence_types.update(expected["evidence_types"] or [])
                observed_coverages.add(expected["coverage"])
            elif requirement == "unsupported":
                plan["reason"] = expected["reason"]
            parsed = SubmitTurnPlanInput.model_validate({"plan": plan})
            self.assertEqual(
                parsed.plan.requirement_kind, requirement, case["message"]
            )

        self.assertEqual(
            observed_evidence_types,
            {"text", "comment", "ocr", "asr", "visual", "keyframe"},
        )
        self.assertEqual(observed_coverages, {"discovery", "complete"})
        for case in fixture["suites"]["blind"]:
            self.assertNotIn(case["message"], PLANNER_SYSTEM_PROMPT)

    def test_v11_blind_evaluation_set_is_frozen_balanced_and_schema_valid(self):
        fixture = json.loads(PLANNER_V11_BLIND_PATH.read_text(encoding="utf-8"))
        self.assertEqual(fixture["frozen_prompt_version"], "turn-planner-shadow-v1.1")
        groups = fixture["suites"]["blind"]["groups"]
        cases = []
        for group in groups:
            for item in group["cases"]:
                cases.append(
                    {
                        "id": item["id"],
                        "message": item["message"],
                        "slice": group["slice"],
                        "context": {
                            **group["context"],
                            **item.get("context", {}),
                        },
                        "expected": {
                            **group["expected"],
                            **item.get("expected", {}),
                        },
                    }
                )

        self.assertEqual(len(cases), 120)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertEqual(len({case["message"] for case in cases}), len(cases))
        self.assertTrue(all(case["message"] not in PLANNER_SYSTEM_PROMPT for case in cases))

        kind_counts = {}
        coverage_counts = {}
        type_counts = {
            evidence_type: 0
            for evidence_type in ("comment", "text", "ocr", "asr", "visual", "keyframe")
        }
        for case in cases:
            PlannerInputSnapshot.model_validate(
                {"current_user_message": case["message"], **case["context"]}
            )
            transport = SubmitTurnPlanToolInput.model_validate(case["expected"])
            transport.as_submission()
            kind = case["expected"]["requirement_kind"]
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            coverage = case["expected"]["coverage"]
            if coverage is not None:
                coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1
            for evidence_type in case["expected"]["evidence_types"] or []:
                type_counts[evidence_type] += 1

        self.assertEqual(
            kind_counts,
            {
                "report_presentation": 15,
                "focused_claim_support": 15,
                "finding_evidence_collection": 60,
                "evidence_detail": 10,
                "none": 10,
                "unsupported": 10,
            },
        )
        self.assertEqual(coverage_counts, {"discovery": 44, "complete": 16})
        self.assertEqual(
            type_counts,
            {
                "comment": 12,
                "text": 12,
                "ocr": 12,
                "asr": 8,
                "visual": 10,
                "keyframe": 8,
            },
        )
        self.assertTrue(any(case["context"]["active_focus_exists"] for case in cases))
        self.assertTrue(any(not case["context"]["active_focus_exists"] for case in cases))

    def test_v12_regression_set_is_separate_tagged_and_schema_valid(self):
        fixture = json.loads(PLANNER_V12_REGRESSION_PATH.read_text(encoding="utf-8"))
        self.assertEqual(fixture["frozen_prompt_version"], PLANNER_PROMPT_VERSION)
        groups = fixture["suites"]["regression"]["groups"]
        cases = []
        for group in groups:
            for item in group["cases"]:
                expected = {**group["expected"], **item.get("expected", {})}
                context = {**group["context"], **item.get("context", {})}
                cases.append((item, expected, context, set(group.get("tags", []))))
                PlannerInputSnapshot.model_validate(
                    {"current_user_message": item["message"], **context}
                )
                SubmitTurnPlanToolInput.model_validate(expected).as_submission()

        self.assertEqual(len(cases), 30)
        self.assertEqual(len({item["id"] for item, _, _, _ in cases}), 30)
        self.assertEqual(len({item["message"] for item, _, _, _ in cases}), 30)
        self.assertEqual(
            sum("collection_detail" in tags for _, _, _, tags in cases), 18
        )
        self.assertEqual(sum("asr" in tags for _, _, _, tags in cases), 9)
        self.assertEqual(
            sum("cross_requirement" in tags for _, _, _, tags in cases), 7
        )
        self.assertTrue(
            all(item["message"] not in PLANNER_SYSTEM_PROMPT for item, _, _, _ in cases)
        )

    def test_v12_blind_set_is_held_out_balanced_and_frozen(self):
        from tests.planner_fixture_support import _expanded_suites

        fixture = json.loads(PLANNER_V12_BLIND_PATH.read_text(encoding="utf-8"))
        self.assertEqual(fixture["frozen_prompt_version"], PLANNER_PROMPT_VERSION)
        cases = _expanded_suites(fixture)["blind"]
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({case["id"] for case in cases}), 100)
        self.assertEqual(len({case["message"] for case in cases}), 100)

        prior_messages = set()
        for path in (
            PLANNER_GOLD_PATH,
            PLANNER_V11_BLIND_PATH,
            PLANNER_V12_REGRESSION_PATH,
        ):
            prior_fixture = json.loads(path.read_text(encoding="utf-8"))
            prior_messages.update(
                case["message"]
                for suite in _expanded_suites(prior_fixture).values()
                for case in suite
            )
        self.assertTrue(
            all(case["message"] not in prior_messages for case in cases)
        )
        self.assertTrue(
            all(case["message"] not in PLANNER_SYSTEM_PROMPT for case in cases)
        )

        for case in cases:
            PlannerInputSnapshot.model_validate(
                {"current_user_message": case["message"], **case["context"]}
            )
            SubmitTurnPlanToolInput.model_validate(case["expected"]).as_submission()

        self.assertEqual(
            Counter(case["expected"]["requirement_kind"] for case in cases),
            Counter(
                {
                    "report_presentation": 10,
                    "focused_claim_support": 10,
                    "finding_evidence_collection": 40,
                    "evidence_detail": 20,
                    "none": 10,
                    "unsupported": 10,
                }
            ),
        )
        self.assertEqual(
            Counter(
                case["expected"]["coverage"]
                for case in cases
                if case["expected"]["coverage"] is not None
            ),
            Counter({"discovery": 20, "complete": 20}),
        )
        tag_counts = Counter(tag for case in cases for tag in case["tags"])
        self.assertEqual(tag_counts["collection_detail"], 60)
        self.assertEqual(tag_counts["asr"], 15)
        self.assertEqual(tag_counts["cross_requirement"], 10)
        self.assertEqual(tag_counts["typed_evidence"], 36)
        self.assertEqual(
            hashlib.sha256(PLANNER_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            "53af8bfa233eca92c352982827071710f052c7e691a6b3b24426c47f18214d98",
        )


class QwenChatToolClientTest(unittest.TestCase):
    def test_missing_api_key_fails_without_mock_fallback(self):
        client = QwenChatToolClient(api_key="", model="qwen-test")
        with self.assertRaises(QwenChatError) as captured:
            client.complete(
                messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "hello"},
                ],
                tools=[],
                tool_choice="none",
            )
        self.assertEqual(captured.exception.kind, "configuration")
        self.assertFalse(captured.exception.retryable)

    def test_parses_structured_tool_calls_usage_and_request_id(self):
        response = requests.Response()
        response.status_code = 200
        response.headers["x-request-id"] = "header-request"
        response._content = json.dumps(
            {
                "id": "completion-1",
                "model": "qwen-test",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_report_presentation",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "total_tokens": 13,
                },
            }
        ).encode()
        client = QwenChatToolClient(
            api_key="test",
            model="qwen-test",
            allowed_tool_names=frozenset({"read_report_presentation"}),
        )
        with patch("backend.investigation.qwen_chat_tool_client.requests.post", return_value=response):
            output = client.complete(
                messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "report"},
                ],
                tools=[{"type": "function", "function": {"name": "read_report_presentation"}}],
            )
        self.assertEqual(output.request_id, "completion-1")
        self.assertEqual(output.tool_calls[0].arguments, {})
        self.assertEqual(output.usage.total_tokens, 13)


if __name__ == "__main__":
    unittest.main()
