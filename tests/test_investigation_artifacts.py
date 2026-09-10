import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError

from backend.domain.identity import stable_hash
from backend.investigation.artifacts import (
    EvidenceArtifactCompiler,
    EvidenceCanonicalCapture,
)
from backend.investigation.contracts import (
    AcquisitionRequired,
    BoundEvidenceCollectionRequirement,
    BoundEvidenceDetailRequirement,
    BoundRequirement,
    EvidenceDetailQueryDetails,
    EvidenceDetailTurnPlan,
    FindingEvidenceQueryDetails,
    FindingEvidenceCollectionTurnPlan,
    NoSourceTurnPlan,
    PlannerShadowTrace,
    PreparationResult,
    ProjectionTokenAccounting,
    PublishedReportContext,
    QueryResultArtifact,
    QueryResultArtifactLink,
    ReadySourceBundle,
    ResolvedReference,
    SourceArtifact,
    SourceCoverageProof,
    SourceFailure,
    SourceKind,
    SourceLedgerCandidate,
    ToolQueryReceipt,
    UnsupportedCapacity,
    UnsupportedComplete,
)
from backend.investigation.errors import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
)
from backend.investigation.store import InvestigationStore
from backend.investigation.orchestrator import SourceOrchestrator, SubjectBinder


REPORT_ID = "report:" + "1" * 32
REPORT_VERSION_ID = "report-version:" + "2" * 32
SNAPSHOT_HASH = "3" * 64
FINDING_REF = "finding:audit_result:214"
COMMENT_REF = "evidence:audit_result:214:ev_comment_001"
TEXT_REF = "evidence:audit_result:214:ev_text_001"
NOW = "2026-08-10T12:00:00+00:00"


class InvestigationArtifactContractTest(unittest.TestCase):
    def scope(self):
        return {
            "session_id": "investigation-session:test",
            "report_version_id": REPORT_VERSION_ID,
            "source_snapshot_id": "source-snapshot:test",
            "snapshot_hash": SNAPSHOT_HASH,
        }

    def test_bound_requirement_is_a_strict_discriminated_union(self):
        adapter = TypeAdapter(BoundRequirement)
        collection = adapter.validate_python(
            {
                **self.scope(),
                "requirement_kind": "finding_evidence_collection",
                "finding_ref": FINDING_REF,
                "evidence_types": ["comment", "keyframe"],
                "coverage": "discovery",
            }
        )
        detail = adapter.validate_python(
            {
                **self.scope(),
                "requirement_kind": "evidence_detail",
                "evidence_ref": COMMENT_REF,
            }
        )

        self.assertIsInstance(collection, BoundEvidenceCollectionRequirement)
        self.assertIsInstance(detail, BoundEvidenceDetailRequirement)
        with self.assertRaises(ValidationError):
            adapter.validate_python(
                {
                    **self.scope(),
                    "requirement_kind": "finding_evidence_collection",
                    "finding_ref": FINDING_REF,
                    "evidence_types": ["comment", "comment"],
                    "coverage": "discovery",
                }
            )
        with self.assertRaises(ValidationError):
            adapter.validate_python(
                {
                    **self.scope(),
                    "requirement_kind": "finding_evidence_collection",
                    "finding_ref": FINDING_REF,
                    "evidence_types": ["other"],
                    "coverage": "discovery",
                }
            )
        with self.assertRaises(ValidationError):
            adapter.validate_python(
                {
                    **self.scope(),
                    "requirement_kind": "evidence_detail",
                    "evidence_ref": COMMENT_REF,
                    "coverage": "complete",
                }
            )

    def test_ready_bundle_and_preparation_result_keep_collection_detail_separate(self):
        requirement = BoundEvidenceCollectionRequirement(
            **self.scope(),
            requirement_kind="finding_evidence_collection",
            finding_ref=FINDING_REF,
            evidence_types=("comment",),
            coverage="complete",
        )
        coverage = SourceCoverageProof(
            requested_coverage="complete",
            returned_count=1,
            total=1,
            has_more=False,
            acquisition_completeness="complete",
            proof_fingerprint="4" * 64,
        )
        bundle = ReadySourceBundle(
            bound_requirement=requirement,
            query_result_artifact_ids=("query-result-artifact:" + "5" * 64,),
            source_artifact_ids=("source-artifact:" + "6" * 64,),
            ordered_projected_refs=(COMMENT_REF,),
            projected_source_message="canonical evidence payload",
            coverage_proof=coverage,
            acquisition_completeness="complete",
            answer_scope="collection_complete",
            projection_truncated=False,
            query_fingerprints=("7" * 64,),
            result_fingerprints=("8" * 64,),
            bundle_fingerprint="9" * 64,
            projection_policy_version="projection-v1",
            token_accounting=ProjectionTokenAccounting(
                source_tokens=10, source_token_budget=100
            ),
        )

        parsed = TypeAdapter(PreparationResult).validate_python(
            bundle.model_dump(mode="json")
        )
        self.assertEqual(parsed.status, "ready")
        with self.assertRaises(ValidationError):
            ReadySourceBundle.model_validate(
                {**bundle.model_dump(mode="json"), "answer_scope": "evidence_detail"}
            )
        with self.assertRaises(ValidationError):
            SourceCoverageProof(
                requested_coverage="complete",
                returned_count=1,
                total=2,
                has_more=True,
                acquisition_completeness="partial",
                proof_fingerprint="a" * 64,
            )

    def test_ready_bundle_v2_requires_strict_controlled_react_lineage(self):
        requirement = BoundEvidenceCollectionRequirement(
            **self.scope(),
            requirement_kind="finding_evidence_collection",
            finding_ref=FINDING_REF,
            evidence_types=("comment",),
            coverage="discovery",
        )
        detail = BoundEvidenceDetailRequirement(
            **self.scope(),
            requirement_kind="evidence_detail",
            evidence_ref=COMMENT_REF,
        )
        coverage = SourceCoverageProof(
            requested_coverage="discovery",
            returned_count=1,
            total=1,
            has_more=False,
            acquisition_completeness="complete",
            proof_fingerprint="4" * 64,
        )
        payload = {
            "schema_version": "ready-source-bundle-v2",
            "bound_requirement": requirement.model_dump(mode="json"),
            "supplemental_requirements": [detail.model_dump(mode="json")],
            "parent_bundle_fingerprint": "a" * 64,
            "react_iteration_count": 1,
            "query_result_artifact_ids": [
                "query-result-artifact:" + "5" * 64,
                "query-result-artifact:" + "6" * 64,
            ],
            "source_artifact_ids": ["source-artifact:" + "7" * 64],
            "ordered_projected_refs": [COMMENT_REF],
            "projected_source_message": "expanded canonical evidence payload",
            "coverage_proof": coverage.model_dump(mode="json"),
            "acquisition_completeness": "complete",
            "answer_scope": "collection_discovery",
            "projection_truncated": False,
            "query_fingerprints": ["8" * 64, "9" * 64],
            "result_fingerprints": ["b" * 64, "c" * 64],
            "bundle_fingerprint": "d" * 64,
            "projection_policy_version": "projection-v1",
            "token_accounting": {
                "source_tokens": 20,
                "source_token_budget": 100,
            },
        }

        bundle = ReadySourceBundle.model_validate(payload)

        self.assertEqual(bundle.schema_version, "ready-source-bundle-v2")
        self.assertEqual(bundle.react_iteration_count, 1)
        self.assertEqual(bundle.supplemental_requirements, (detail,))
        with self.assertRaises(ValidationError):
            ReadySourceBundle.model_validate(
                {**payload, "parent_bundle_fingerprint": None}
            )
        with self.assertRaises(ValidationError):
            ReadySourceBundle.model_validate(
                {**payload, "react_iteration_count": 0}
            )
        with self.assertRaises(ValidationError):
            ReadySourceBundle.model_validate(
                {
                    **payload,
                    "schema_version": "ready-source-bundle-v1",
                    "parent_bundle_fingerprint": None,
                }
            )

    def test_artifact_fingerprints_are_stable_and_reject_dirty_payloads(self):
        source = SourceArtifact.create(
            **self.scope(),
            stable_source_ref=COMMENT_REF,
            source_type="comment",
            content_level="evidence_collection_item",
            canonical_content={"evidence_id": COMMENT_REF, "summary": "评论摘要"},
            observed_at=NOW,
            created_at=NOW,
        )
        duplicate = SourceArtifact.create(
            **self.scope(),
            stable_source_ref=COMMENT_REF,
            source_type="comment",
            content_level="evidence_collection_item",
            canonical_content={"summary": "评论摘要", "evidence_id": COMMENT_REF},
            observed_at=NOW,
            created_at=NOW,
        )

        self.assertEqual(source, duplicate)
        later_materialization = SourceArtifact.create(
            **self.scope(),
            stable_source_ref=COMMENT_REF,
            source_type="comment",
            content_level="evidence_collection_item",
            canonical_content={"evidence_id": COMMENT_REF, "summary": "评论摘要"},
            observed_at=NOW,
            created_at="2026-08-10T12:05:00+00:00",
        )
        self.assertEqual(source.artifact_id, later_materialization.artifact_id)
        self.assertEqual(source.source_fingerprint, later_materialization.source_fingerprint)
        other = SourceArtifact.create(
            **self.scope(),
            stable_source_ref=TEXT_REF,
            source_type="other",
            content_level="evidence_collection_item",
            canonical_content={"evidence_id": TEXT_REF, "summary": "其他领域证据"},
            observed_at=NOW,
            created_at=NOW,
        )
        self.assertEqual(other.source_type, "other")
        corrupt = source.model_dump(mode="json")
        corrupt["canonical_content"]["summary"] = "changed"
        with self.assertRaises(ValidationError):
            SourceArtifact.model_validate(corrupt)
        with self.assertRaises(ValidationError):
            SourceArtifact.create(
                **self.scope(),
                stable_source_ref=COMMENT_REF,
                source_type="comment",
                content_level="evidence_detail",
                canonical_content={"asset_path": "/private/evidence.png"},
                observed_at=NOW,
                created_at=NOW,
            )


class InvestigationArtifactStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "investigation.sqlite3"
        self.store = InvestigationStore(self.db_path)
        self.context = PublishedReportContext(
            task_id="task:test",
            report_id=REPORT_ID,
            report_version_id=REPORT_VERSION_ID,
            version_number=1,
            source_snapshot_id="source-snapshot:test",
            snapshot_hash=SNAPSHOT_HASH,
            source_hash="source-hash",
            title="Test report",
            content_hash="content-hash",
            published_at=NOW,
            finding_ids=(FINDING_REF,),
            evidence_ids=(COMMENT_REF, TEXT_REF),
        )
        self.session = self.store.create_session(self.context)
        self.turn, _ = self.store.create_turn(
            self.session.id,
            client_message_id="artifact-test-turn",
            user_input="列出评论证据",
        )

    def scope(self, session=None):
        target = session or self.session
        return {
            "session_id": target.id,
            "report_version_id": target.report_version_id,
            "source_snapshot_id": target.source_snapshot_id,
            "snapshot_hash": target.snapshot_hash,
        }

    def source(
        self,
        source_ref=COMMENT_REF,
        *,
        source_type="comment",
        content_level="evidence_collection_item",
        session=None,
    ):
        return SourceArtifact.create(
            **self.scope(session),
            stable_source_ref=source_ref,
            source_type=source_type,
            content_level=content_level,
            canonical_content={
                "evidence_id": source_ref,
                "evidence_type": source_type,
                "summary": f"canonical:{source_ref}",
            },
            observed_at=NOW,
            created_at=NOW,
        )

    def collection_result(self, sources=(), *, session=None, total=None, has_more=False):
        target = session or self.session
        query = {
            "finding_id": FINDING_REF,
            "evidence_types": ["comment"],
            "limit": 20,
        }
        payload = {
            "items": [item.canonical_content for item in sources],
            "returned": len(sources),
            "total": len(sources) if total is None else total,
            "has_more": has_more,
        }
        return QueryResultArtifact.create(
            **self.scope(target),
            operation="finding_evidence_list",
            subject_ref=FINDING_REF,
            normalized_query=query,
            normalized_filters={"evidence_types": ["comment"]},
            ordered_sources=tuple(sources),
            returned_count=len(sources),
            total=len(sources) if total is None else total,
            has_more=has_more,
            cursor=None,
            next_cursor=None,
            canonical_payload=payload,
            query_fingerprint=stable_hash({"query": query}),
            result_fingerprint=stable_hash(
                {
                    "tool": "list_finding_evidence",
                    "query": query,
                    "payload": payload,
                }
            ),
            observed_at=NOW,
            created_at=NOW,
        )

    def receipt_for(self, result):
        return ToolQueryReceipt(
            receipt_id="tool-query-receipt:" + "b" * 32,
            session_id=self.session.id,
            turn_id=self.turn.id,
            tool_call_id="artifact-tool-call",
            report_version_id=self.session.report_version_id,
            snapshot_hash=self.session.snapshot_hash,
            tool_name="list_finding_evidence",
            operation="finding_evidence_list",
            subject_refs=(FINDING_REF,),
            query_params=result.normalized_query,
            arguments_fingerprint=stable_hash(
                {
                    "tool_name": "list_finding_evidence",
                    "arguments": result.normalized_query,
                }
            ),
            query_fingerprint=result.query_fingerprint,
            status="ok",
            returned_refs=tuple(
                item.stable_source_ref for item in result.ordered_members
            ),
            model_visible_refs=tuple(
                item.stable_source_ref for item in result.ordered_members
            ),
            result_count=result.returned_count,
            total=result.total,
            has_more=result.has_more,
            result_fingerprint=result.result_fingerprint,
            details=FindingEvidenceQueryDetails(
                operation="finding_evidence_list",
                finding_ref=FINDING_REF,
                evidence_types=("comment",),
                returned_evidence_refs=tuple(
                    item.stable_source_ref for item in result.ordered_members
                ),
                returned_count=result.returned_count,
                total=result.total or 0,
                has_more=bool(result.has_more),
            ),
            executed_at=NOW,
        )

    def persist_receipt(self, receipt):
        with self.store._connect() as connection:
            self.store._insert_query_receipts(
                connection,
                session_id=self.session.id,
                turn_id=self.turn.id,
                receipts=[receipt.model_dump(mode="json")],
            )

    @staticmethod
    def provenance(source_ref, source_type, query_fingerprint):
        return SourceLedgerCandidate(
            source_kind=SourceKind.CURRENT_EVIDENCE,
            finding_id=FINDING_REF,
            evidence_id=source_ref,
            source_hash=stable_hash(
                {"evidence_id": source_ref, "evidence_type": source_type}
            ),
            excerpt=f"canonical:{source_ref}",
            asset_status="available",
            query_fingerprint=query_fingerprint,
            freshness="current_source",
        )

    def compile_collection(self, sources=(), *, total=None, has_more=False):
        result = self.collection_result(
            tuple(sources), total=total, has_more=has_more
        )
        receipt = self.receipt_for(result)
        capture = EvidenceCanonicalCapture(
            tool_name="list_finding_evidence",
            normalized_query=result.normalized_query,
            canonical_payload=result.canonical_payload,
            provenance=tuple(
                self.provenance(
                    item.stable_source_ref,
                    item.source_type,
                    result.query_fingerprint,
                )
                for item in sources
            ),
            query_fingerprint=result.query_fingerprint,
            result_fingerprint=result.result_fingerprint,
        )
        return receipt, EvidenceArtifactCompiler.compile(
            source_snapshot_id=self.session.source_snapshot_id,
            receipt=receipt,
            capture=capture,
        )

    def compile_detail(self, *, original_text="完整评论原文"):
        query = {"evidence_id": COMMENT_REF}
        payload = {
            "evidence_id": COMMENT_REF,
            "finding_id": FINDING_REF,
            "evidence_type": "comment",
            "original_text": original_text,
            "translated_text": "",
            "summary": "评论摘要",
        }
        query_fingerprint = stable_hash({"query": query})
        result_fingerprint = stable_hash(
            {"tool": "read_evidence_detail", "query": query, "payload": payload}
        )
        receipt = ToolQueryReceipt(
            receipt_id="tool-query-receipt:" + "d" * 32,
            session_id=self.session.id,
            turn_id=self.turn.id,
            tool_call_id="detail-tool-call",
            report_version_id=self.session.report_version_id,
            snapshot_hash=self.session.snapshot_hash,
            tool_name="read_evidence_detail",
            operation="evidence_detail_read",
            subject_refs=(COMMENT_REF,),
            query_params=query,
            arguments_fingerprint=stable_hash(
                {"tool_name": "read_evidence_detail", "arguments": query}
            ),
            query_fingerprint=query_fingerprint,
            status="ok",
            returned_refs=(COMMENT_REF,),
            model_visible_refs=(COMMENT_REF,),
            result_count=1,
            total=1,
            has_more=None,
            result_fingerprint=result_fingerprint,
            details=EvidenceDetailQueryDetails(
                operation="evidence_detail_read",
                evidence_ref=COMMENT_REF,
                finding_ref=FINDING_REF,
                evidence_type="comment",
            ),
            executed_at=NOW,
        )
        capture = EvidenceCanonicalCapture(
            tool_name="read_evidence_detail",
            normalized_query=query,
            canonical_payload=payload,
            provenance=(
                self.provenance(COMMENT_REF, "comment", query_fingerprint),
            ),
            query_fingerprint=query_fingerprint,
            result_fingerprint=result_fingerprint,
        )
        return receipt, EvidenceArtifactCompiler.compile(
            source_snapshot_id=self.session.source_snapshot_id,
            receipt=receipt,
            capture=capture,
        ), payload

    def bound_collection(self, *, evidence_types=("comment",), coverage="discovery"):
        return BoundEvidenceCollectionRequirement(
            **self.scope(),
            requirement_kind="finding_evidence_collection",
            finding_ref=FINDING_REF,
            evidence_types=evidence_types,
            coverage=coverage,
        )

    def bound_detail(self):
        return BoundEvidenceDetailRequirement(
            **self.scope(),
            requirement_kind="evidence_detail",
            evidence_ref=COMMENT_REF,
        )

    def test_round_trip_preserves_order_and_content_levels(self):
        comment = self.source()
        text = self.source(TEXT_REF, source_type="text")
        result = self.collection_result((text, comment))

        stored = self.store.put_artifact_bundle(result, (comment, text))
        reopened = InvestigationStore(self.db_path)

        self.assertEqual(stored, result)
        self.assertEqual(reopened.get_query_result_artifact(result.artifact_id), result)
        self.assertEqual(
            tuple(item.stable_source_ref for item in stored.ordered_members),
            (TEXT_REF, COMMENT_REF),
        )
        self.assertEqual(reopened.get_source_artifact(comment.artifact_id), comment)
        self.assertIsNone(reopened.get_source_artifact("source-artifact:" + "0" * 64))

        later_comment = SourceArtifact.create(
            **self.scope(),
            stable_source_ref=COMMENT_REF,
            source_type="comment",
            content_level="evidence_collection_item",
            canonical_content=comment.canonical_content,
            observed_at=NOW,
            created_at="2026-08-10T12:05:00+00:00",
        )
        later_result_payload = result.model_dump(mode="json")
        later_result_payload["created_at"] = "2026-08-10T12:05:00+00:00"
        later_result = QueryResultArtifact.model_validate(later_result_payload)
        self.assertEqual(later_result.artifact_id, result.artifact_id)
        self.assertEqual(
            self.store.put_artifact_bundle(later_result, (later_comment, text)), result
        )

    def test_empty_collection_is_materialized_without_source_artifacts(self):
        result = self.collection_result(())

        stored = self.store.put_artifact_bundle(result, ())

        self.assertEqual(stored.returned_count, 0)
        self.assertEqual(stored.total, 0)
        self.assertEqual(stored.ordered_members, ())
        self.assertEqual(self.store.list_source_artifacts(session_id=self.session.id), ())

    def test_compiler_preserves_canonical_collection_and_atomic_write_is_idempotent(self):
        text = self.source(TEXT_REF, source_type="text")
        comment = self.source()
        receipt, compiled = self.compile_collection((text, comment))

        self.assertEqual(
            compiled.query_result.canonical_payload,
            self.collection_result((text, comment)).canonical_payload,
        )
        self.assertEqual(
            tuple(item.canonical_content for item in compiled.source_artifacts),
            (text.canonical_content, comment.canonical_content),
        )
        self.assertEqual(
            tuple(item.content_level for item in compiled.source_artifacts),
            ("evidence_collection_item", "evidence_collection_item"),
        )

        first = self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )
        second = self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(self.store.list_query_receipts(turn_id=self.turn.id)), 1)
        self.assertEqual(len(self.store.list_query_artifact_index()), 1)
        self.assertEqual(len(self.store.list_source_artifacts()), 2)

    def test_compiler_materializes_an_empty_collection_as_a_reusable_query_result(self):
        receipt, compiled = self.compile_collection(())

        self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )

        self.assertEqual(compiled.source_artifacts, ())
        self.assertEqual(compiled.query_result.returned_count, 0)
        self.assertEqual(compiled.query_result.total, 0)
        self.assertEqual(len(self.store.list_query_artifact_index()), 1)

    def test_receipt_and_artifact_graph_roll_back_together(self):
        source = self.source()
        receipt, compiled = self.compile_collection((source,))

        with patch.object(
            self.store,
            "_insert_query_result_artifact",
            side_effect=RuntimeError("injected atomic write failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected atomic write failure"):
                self.store.put_query_receipt_artifacts(
                    receipt,
                    compiled.query_result,
                    compiled.source_artifacts,
                    link=compiled.link,
                )

        self.assertEqual(self.store.list_query_receipts(turn_id=self.turn.id), ())
        self.assertEqual(self.store.list_source_artifacts(), ())
        self.assertEqual(self.store.list_query_result_artifacts(), ())
        self.assertEqual(self.store.list_query_artifact_index(), ())

    def test_receipt_result_mismatch_fails_closed_and_rolls_back(self):
        source = self.source()
        receipt, compiled = self.compile_collection((source,))
        inconsistent_receipt = receipt.model_copy(update={"returned_refs": ()})

        with self.assertRaisesRegex(
            ArtifactIntegrityError, "receipt and query result facts differ"
        ):
            self.store.put_query_receipt_artifacts(
                inconsistent_receipt,
                compiled.query_result,
                compiled.source_artifacts,
                link=compiled.link,
            )

        self.assertEqual(self.store.list_query_receipts(turn_id=self.turn.id), ())
        self.assertEqual(self.store.list_source_artifacts(), ())
        self.assertEqual(self.store.list_query_result_artifacts(), ())

    def test_compiler_keeps_detail_distinct_from_collection_preview(self):
        receipt, compiled, payload = self.compile_detail()
        self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )

        self.assertEqual(compiled.query_result.operation, "evidence_detail_read")
        self.assertEqual(compiled.query_result.canonical_payload, payload)
        self.assertEqual(compiled.source_artifacts[0].content_level, "evidence_detail")
        self.assertEqual(compiled.source_artifacts[0].canonical_content, payload)

    def test_subject_binder_consumes_existing_focus_without_parsing_language(self):
        binder = SubjectBinder()
        focused_session = self.session.model_copy(
            update={
                "active_focus": {
                    "type": "finding",
                    "target_id": FINDING_REF,
                    "label": "当前 Finding",
                },
                "last_finding_id": FINDING_REF,
            }
        )
        collection = binder.bind(
            plan=FindingEvidenceCollectionTurnPlan(
                requirement_kind="finding_evidence_collection",
                evidence_types=("comment",),
                coverage="discovery",
            ),
            session=focused_session,
            resolved_references=(),
        )
        detail = binder.bind(
            plan=EvidenceDetailTurnPlan(requirement_kind="evidence_detail"),
            session=self.session,
            resolved_references=(
                ResolvedReference(
                    expression="那条评论",
                    status="resolved",
                    target_type="evidence",
                    target_id=COMMENT_REF,
                    label="评论",
                ),
            ),
        )
        missing = binder.bind(
            plan=EvidenceDetailTurnPlan(requirement_kind="evidence_detail"),
            session=self.session,
            resolved_references=(),
        )
        ignored = binder.bind(
            plan=NoSourceTurnPlan(requirement_kind="none"),
            session=self.session,
            resolved_references=(),
        )
        incompatible = binder.bind(
            plan=FindingEvidenceCollectionTurnPlan(
                requirement_kind="finding_evidence_collection",
                evidence_types=None,
                coverage="discovery",
            ),
            session=focused_session,
            resolved_references=(
                ResolvedReference(
                    expression="那条评论",
                    status="resolved",
                    target_type="evidence",
                    target_id=COMMENT_REF,
                    label="评论",
                ),
            ),
        )
        unresolved = binder.bind(
            plan=FindingEvidenceCollectionTurnPlan(
                requirement_kind="finding_evidence_collection",
                evidence_types=None,
                coverage="discovery",
            ),
            session=focused_session,
            resolved_references=(
                ResolvedReference(
                    expression="第二个案例",
                    status="unresolved",
                ),
            ),
        )

        self.assertEqual(collection.status, "bound")
        self.assertEqual(collection.bound_requirement.finding_ref, FINDING_REF)
        self.assertEqual(detail.status, "bound")
        self.assertEqual(detail.bound_requirement.evidence_ref, COMMENT_REF)
        self.assertEqual(missing.status, "need_subject")
        self.assertEqual(ignored.status, "not_applicable")
        self.assertEqual(incompatible.status, "need_subject")
        self.assertEqual(unresolved.status, "need_subject")

    def test_orchestrator_requires_exact_filter_and_reuses_empty_collection(self):
        receipt, compiled = self.compile_collection(())
        self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )
        orchestrator = SourceOrchestrator(self.store)

        matched = orchestrator.prepare(self.bound_collection())
        unfiltered = orchestrator.prepare(
            self.bound_collection(evidence_types=None)
        )

        self.assertIsInstance(matched, ReadySourceBundle)
        self.assertEqual(matched.ordered_projected_refs, ())
        self.assertEqual(matched.source_artifact_ids, ())
        self.assertEqual(matched.coverage_proof.returned_count, 0)
        self.assertEqual(
            matched.query_result_artifact_ids,
            (compiled.query_result.artifact_id,),
        )
        self.assertIsInstance(unfiltered, AcquisitionRequired)

    def test_orchestrator_distinguishes_discovery_from_complete_coverage(self):
        source = self.source()
        receipt, compiled = self.compile_collection(
            (source,), total=2, has_more=True
        )
        self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )
        orchestrator = SourceOrchestrator(self.store)

        discovery = orchestrator.prepare(self.bound_collection())
        complete = orchestrator.prepare(
            self.bound_collection(coverage="complete")
        )

        self.assertIsInstance(discovery, ReadySourceBundle)
        self.assertEqual(discovery.acquisition_completeness, "partial")
        self.assertEqual(discovery.answer_scope, "collection_discovery")
        self.assertIsInstance(complete, UnsupportedComplete)
        self.assertEqual(complete.reason, "tool_not_pageable")

    def test_orchestrator_rejects_source_outside_exact_evidence_filter(self):
        text_source = self.source(
            source_ref=TEXT_REF,
            source_type="text",
        )
        receipt, compiled = self.compile_collection((text_source,))
        self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )

        observation = SourceOrchestrator(self.store).prepare(
            self.bound_collection(evidence_types=("comment",))
        )

        self.assertIsInstance(observation, SourceFailure)
        self.assertEqual(
            observation.error_code,
            "artifact_materialization_failure",
        )

    def test_orchestrator_materializes_detail_and_rejects_oversized_projection(self):
        receipt, compiled, payload = self.compile_detail()
        self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )
        ready = SourceOrchestrator(self.store).prepare(self.bound_detail())
        too_small = SourceOrchestrator(
            self.store, source_token_budget=1_000
        ).prepare(self.bound_detail())

        self.assertIsInstance(ready, ReadySourceBundle)
        self.assertEqual(ready.answer_scope, "evidence_detail")
        self.assertEqual(ready.ordered_projected_refs, (COMMENT_REF,))
        self.assertIn(payload["original_text"], ready.projected_source_message)
        self.assertIsInstance(too_small, ReadySourceBundle)

        large_store = InvestigationStore(Path(self.temp_dir.name) / "large.sqlite3")
        large_session = large_store.create_session(self.context)
        large_turn, _ = large_store.create_turn(
            large_session.id,
            client_message_id="large-detail",
            user_input="查看详情",
        )
        original_session, original_turn, original_store = (
            self.session,
            self.turn,
            self.store,
        )
        try:
            self.session = large_session
            self.turn = large_turn
            self.store = large_store
            large_receipt, large_compiled, _ = self.compile_detail(
                original_text="x" * 5_000
            )
            large_store.put_query_receipt_artifacts(
                large_receipt,
                large_compiled.query_result,
                large_compiled.source_artifacts,
                link=large_compiled.link,
            )
            capacity = SourceOrchestrator(
                large_store, source_token_budget=1_000
            ).prepare(self.bound_detail())
        finally:
            self.session, self.turn, self.store = (
                original_session,
                original_turn,
                original_store,
            )
        self.assertIsInstance(capacity, UnsupportedCapacity)

    def test_orchestrator_expands_detail_only_with_exact_parent_lineage(self):
        source = self.source()
        collection_receipt, collection = self.compile_collection((source,))
        self.store.put_query_receipt_artifacts(
            collection_receipt,
            collection.query_result,
            collection.source_artifacts,
            link=collection.link,
        )
        detail_receipt, detail, _payload = self.compile_detail()
        self.store.put_query_receipt_artifacts(
            detail_receipt,
            detail.query_result,
            detail.source_artifacts,
            link=detail.link,
        )
        orchestrator = SourceOrchestrator(self.store)
        requirement = self.bound_collection()
        detail_requirement = self.bound_detail()
        base = orchestrator.prepare(requirement)
        self.assertIsInstance(base, ReadySourceBundle)

        expanded = orchestrator.prepare(
            requirement,
            supplemental_requirements=(detail_requirement,),
            parent_bundle_fingerprint=base.bundle_fingerprint,
        )
        mismatched = orchestrator.prepare(
            requirement,
            supplemental_requirements=(detail_requirement,),
            parent_bundle_fingerprint="f" * 64,
        )

        self.assertIsInstance(expanded, ReadySourceBundle)
        self.assertEqual(expanded.schema_version, "ready-source-bundle-v2")
        self.assertEqual(expanded.parent_bundle_fingerprint, base.bundle_fingerprint)
        expanded_source = self.store.get_source_artifact(
            expanded.source_artifact_ids[0]
        )
        self.assertEqual(expanded_source.content_level, "evidence_detail")
        self.assertIsInstance(mismatched, SourceFailure)
        self.assertEqual(
            mismatched.error_code,
            "controlled_react_materialization_failure",
        )

    def test_source_preparation_shadow_trace_is_strict_and_idempotent(self):
        source = self.source()
        receipt, compiled = self.compile_collection((source,))
        self.store.put_query_receipt_artifacts(
            receipt,
            compiled.query_result,
            compiled.source_artifacts,
            link=compiled.link,
        )
        plan = FindingEvidenceCollectionTurnPlan(
            requirement_kind="finding_evidence_collection",
            evidence_types=("comment",),
            coverage="discovery",
        )
        planner_trace = PlannerShadowTrace(
            trace_id="planner-shadow-trace:" + "1" * 32,
            session_id=self.session.id,
            turn_id=self.turn.id,
            report_version_id=self.session.report_version_id,
            snapshot_hash=self.session.snapshot_hash,
            planner_prompt_version="test-planner-v1",
            input_fingerprint="2" * 64,
            active_focus_exists=True,
            active_focus_type="finding",
            resolved_referent_status="resolved",
            resolved_referent_type="finding",
            planning_status="ok",
            plan=plan,
            model="test",
            request_id="test-request",
            planner_llm_call_count=1,
            created_at=NOW,
        )
        self.store.put_planner_shadow_trace(planner_trace)
        focused_session = self.session.model_copy(
            update={
                "active_focus": {
                    "type": "finding",
                    "target_id": FINDING_REF,
                    "label": "Finding",
                }
            }
        )
        orchestrator = SourceOrchestrator(self.store)
        trace = orchestrator.observe_shadow(
            session=focused_session,
            turn_id=self.turn.id,
            planner_trace=planner_trace,
            resolved_references=(
                ResolvedReference(
                    expression="这个案例",
                    status="resolved",
                    target_type="finding",
                    target_id=FINDING_REF,
                    label="Finding",
                ),
            ),
        )

        first = self.store.put_source_preparation_shadow_trace(trace)
        second = self.store.put_source_preparation_shadow_trace(trace)

        self.assertEqual(first, second)
        self.assertEqual(first.status, "ready")
        self.assertEqual(first.binding_status, "bound")
        self.assertEqual(
            first.selected_query_result_artifact_id,
            compiled.query_result.artifact_id,
        )
        with self.assertRaises(ArtifactConflictError):
            self.store.put_source_preparation_shadow_trace(
                trace.model_copy(update={"reason": "conflicting replay"})
            )

    def test_detail_requires_one_detail_artifact(self):
        detail = self.source(content_level="evidence_detail")
        payload = {**detail.canonical_content, "original_text": "完整评论原文"}
        result = QueryResultArtifact.create(
            **self.scope(),
            operation="evidence_detail_read",
            subject_ref=COMMENT_REF,
            normalized_query={"evidence_id": COMMENT_REF},
            normalized_filters={},
            ordered_sources=(detail,),
            returned_count=1,
            total=1,
            has_more=None,
            cursor=None,
            next_cursor=None,
            canonical_payload=payload,
            query_fingerprint="c" * 64,
            result_fingerprint="d" * 64,
            observed_at=NOW,
            created_at=NOW,
        )

        self.assertEqual(
            self.store.put_artifact_bundle(result, (detail,)).ordered_members[0].content_level,
            "evidence_detail",
        )
        preview = self.source()
        invalid = result.model_dump(mode="json")
        invalid["ordered_members"][0] = {
            "ordinal": 0,
            "stable_source_ref": COMMENT_REF,
            "source_artifact_id": preview.artifact_id,
            "content_level": "evidence_collection_item",
        }
        with self.assertRaises(ValidationError):
            QueryResultArtifact.model_validate(invalid)

    def test_scope_mismatch_fails_before_any_artifact_is_committed(self):
        other_context = self.context.model_copy(
            update={
                "report_id": "report:" + "4" * 32,
                "report_version_id": "report-version:" + "5" * 32,
                "source_snapshot_id": "source-snapshot:other",
                "snapshot_hash": "6" * 64,
            }
        )
        other_session = self.store.create_session(other_context)
        source = self.source()
        result = self.collection_result((source,), session=other_session)

        with self.assertRaises(ArtifactIntegrityError):
            self.store.put_artifact_bundle(result, (source,))

        self.assertEqual(self.store.list_source_artifacts(), ())
        self.assertEqual(self.store.list_query_result_artifacts(), ())

    def test_transaction_rolls_back_sources_when_result_write_fails(self):
        source = self.source()
        result = self.collection_result((source,))

        with patch.object(
            self.store,
            "_insert_query_result_artifact",
            side_effect=RuntimeError("injected transaction failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
                self.store.put_artifact_bundle(result, (source,))

        self.assertEqual(self.store.list_source_artifacts(), ())
        self.assertEqual(self.store.list_query_result_artifacts(), ())

    def test_receipt_link_is_optional_but_validated_and_index_is_derived(self):
        source = self.source()
        result = self.collection_result((source,))

        self.store.put_artifact_bundle(result, (source,))
        self.assertEqual(self.store.list_query_artifact_index(), ())
        self.assertIsNone(
            self.store.get_query_result_for_receipt(
                "tool-query-receipt:" + "b" * 32
            )
        )

        receipt = self.receipt_for(result)
        self.persist_receipt(receipt)
        link = QueryResultArtifactLink(
            receipt_id=receipt.receipt_id,
            query_result_artifact_id=result.artifact_id,
            linked_at=NOW,
        )
        self.store.put_artifact_bundle(result, (source,), link=link)

        index = InvestigationStore(self.db_path).list_query_artifact_index(
            session_id=self.session.id,
            operation="finding_evidence_list",
            query_fingerprint=result.query_fingerprint,
        )
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0].receipt_id, receipt.receipt_id)
        self.assertEqual(
            self.store.get_query_result_for_receipt(receipt.receipt_id), result
        )
        with sqlite3.connect(self.db_path) as connection:
            materialized_index_table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'investigation_query_artifact_index'"
            ).fetchone()
        self.assertIsNone(materialized_index_table)

    def test_missing_receipt_rolls_back_and_corrupt_artifact_fails_closed(self):
        source = self.source()
        result = self.collection_result((source,))
        missing_link = QueryResultArtifactLink(
            receipt_id="tool-query-receipt:" + "e" * 32,
            query_result_artifact_id=result.artifact_id,
            linked_at=NOW,
        )
        with self.assertRaises(ArtifactNotFoundError):
            self.store.put_artifact_bundle(result, (source,), link=missing_link)
        self.assertEqual(self.store.list_source_artifacts(), ())

        self.store.put_artifact_bundle(result, (source,))
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE investigation_source_artifacts
                SET canonical_content_json = ? WHERE artifact_id = ?
                """,
                (json.dumps({"summary": "corrupt"}), source.artifact_id),
            )
        with self.assertRaises(ArtifactIntegrityError):
            self.store.get_source_artifact(source.artifact_id)

    def test_migration_is_idempotent_and_does_not_promote_legacy_ledger(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as connection:
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
            connection.execute(
                """
                INSERT INTO investigation_source_ledger (
                    ledger_id, session_id, turn_id, message_id, report_version_id,
                    snapshot_hash, source_kind, evidence_id, source_hash, excerpt,
                    query_fingerprint, freshness, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "source-ledger:legacy",
                    "legacy-session",
                    "legacy-turn",
                    "legacy-message",
                    REPORT_VERSION_ID,
                    SNAPSHOT_HASH,
                    "current_evidence",
                    COMMENT_REF,
                    "legacy-source-hash",
                    "preview only",
                    "f" * 64,
                    "current_source",
                    NOW,
                ),
            )

        migrated = InvestigationStore(legacy_path)
        migrated_again = InvestigationStore(legacy_path)

        self.assertEqual(migrated.list_source_artifacts(), ())
        self.assertEqual(migrated.list_query_result_artifacts(), ())
        self.assertEqual(migrated.list_query_artifact_index(), ())
        self.assertEqual(migrated_again.list_ledger(limit=10)[0].excerpt, "preview only")
        with sqlite3.connect(legacy_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue(
            {
                "investigation_source_artifacts",
                "investigation_query_result_artifacts",
                "investigation_query_result_members",
                "investigation_query_artifact_links",
                "investigation_source_preparation_shadow_traces",
            }.issubset(tables)
        )

    def test_artifact_migration_failure_does_not_damage_business_rows(self):
        session_id = self.session.id
        turn_id = self.turn.id
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE investigation_query_artifact_links")
            connection.execute("DROP TABLE investigation_query_result_members")
            connection.execute("DROP TABLE investigation_query_result_artifacts")
            connection.execute("DROP TABLE investigation_source_artifacts")
            connection.execute(
                "CREATE TABLE investigation_source_artifacts "
                "(artifact_id TEXT PRIMARY KEY)"
            )

        with self.assertRaises(sqlite3.OperationalError):
            InvestigationStore(self.db_path)

        with sqlite3.connect(self.db_path) as connection:
            session = connection.execute(
                "SELECT id FROM investigation_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            turn = connection.execute(
                "SELECT id FROM investigation_turns WHERE id = ?", (turn_id,)
            ).fetchone()
            query_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'investigation_query_result_artifacts'"
            ).fetchone()

        self.assertEqual(session[0], session_id)
        self.assertEqual(turn[0], turn_id)
        self.assertIsNone(query_table)


if __name__ == "__main__":
    unittest.main()
