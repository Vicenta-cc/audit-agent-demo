from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.investigation.contracts import (
    Fingerprint,
    QueryResultArtifact,
    QueryResultArtifactLink,
    SourceArtifact,
    SourceLedgerCandidate,
    StrictModel,
    ToolQueryReceipt,
)


class EvidenceCanonicalCapture(StrictModel):
    """Server-only canonical Tool output captured before model-result bounding."""

    tool_name: Literal["list_finding_evidence", "read_evidence_detail"]
    normalized_query: dict[str, Any]
    canonical_payload: dict[str, Any]
    provenance: tuple[SourceLedgerCandidate, ...] = Field(max_length=10_000)
    query_fingerprint: Fingerprint
    result_fingerprint: Fingerprint

    @model_validator(mode="after")
    def validate_shape(self):
        if self.tool_name == "list_finding_evidence":
            if not isinstance(self.canonical_payload.get("items"), list):
                raise ValueError("Evidence collection capture requires items")
            if not self.normalized_query.get("finding_id"):
                raise ValueError("Evidence collection capture requires finding_id")
        else:
            evidence_id = self.normalized_query.get("evidence_id")
            if self.canonical_payload.get("evidence_id") != evidence_id:
                raise ValueError("Evidence detail capture does not match its query")
        return self


@dataclass(frozen=True)
class CompiledEvidenceArtifacts:
    query_result: QueryResultArtifact
    source_artifacts: tuple[SourceArtifact, ...]
    link: QueryResultArtifactLink


class EvidenceArtifactCompiler:
    """Compile canonical Evidence Tool output into immutable persistence contracts."""

    @classmethod
    def compile(
        cls,
        *,
        source_snapshot_id: str,
        receipt: ToolQueryReceipt,
        capture: EvidenceCanonicalCapture,
    ) -> CompiledEvidenceArtifacts:
        if receipt.status != "ok":
            raise ValueError("failed Tool results cannot produce Evidence artifacts")
        if receipt.tool_name != capture.tool_name:
            raise ValueError("capture and Receipt Tool names differ")
        if receipt.query_params != capture.normalized_query:
            raise ValueError("capture and Receipt normalized queries differ")
        if receipt.query_fingerprint != capture.query_fingerprint:
            raise ValueError("capture and Receipt query fingerprints differ")
        if receipt.result_fingerprint != capture.result_fingerprint:
            raise ValueError("capture and Receipt result fingerprints differ")

        if capture.tool_name == "list_finding_evidence":
            query_result, sources = cls._compile_collection(
                source_snapshot_id=source_snapshot_id,
                receipt=receipt,
                capture=capture,
            )
        else:
            query_result, sources = cls._compile_detail(
                source_snapshot_id=source_snapshot_id,
                receipt=receipt,
                capture=capture,
            )
        return CompiledEvidenceArtifacts(
            query_result=query_result,
            source_artifacts=sources,
            link=QueryResultArtifactLink(
                receipt_id=receipt.receipt_id,
                query_result_artifact_id=query_result.artifact_id,
                linked_at=receipt.executed_at,
            ),
        )

    @classmethod
    def _compile_collection(
        cls,
        *,
        source_snapshot_id: str,
        receipt: ToolQueryReceipt,
        capture: EvidenceCanonicalCapture,
    ) -> tuple[QueryResultArtifact, tuple[SourceArtifact, ...]]:
        payload = capture.canonical_payload
        items = payload["items"]
        if not all(isinstance(item, dict) for item in items):
            raise ValueError("Evidence collection items must be objects")
        if len(items) != len(capture.provenance):
            raise ValueError("Evidence collection and provenance sizes differ")
        sources = tuple(
            cls._source_artifact(
                source_snapshot_id=source_snapshot_id,
                receipt=receipt,
                capture=capture,
                canonical_content=dict(item),
                content_level="evidence_collection_item",
                ordinal=index,
            )
            for index, item in enumerate(items)
        )
        subject_ref = str(capture.normalized_query["finding_id"])
        normalized_filters = {
            "evidence_types": list(
                capture.normalized_query.get("evidence_types") or []
            )
        }
        returned_count = cls._required_int(payload, "returned")
        total = cls._required_int(payload, "total")
        has_more = payload.get("has_more")
        if not isinstance(has_more, bool):
            raise ValueError("Evidence collection has_more must be boolean")
        if returned_count != len(sources):
            raise ValueError("Evidence collection returned count is inconsistent")
        result = QueryResultArtifact.create(
            **cls._scope(source_snapshot_id, receipt),
            operation="finding_evidence_list",
            subject_ref=subject_ref,
            normalized_query=dict(capture.normalized_query),
            normalized_filters=normalized_filters,
            ordered_sources=sources,
            returned_count=returned_count,
            total=total,
            has_more=has_more,
            cursor=cls._optional_cursor(payload.get("cursor")),
            next_cursor=cls._optional_cursor(payload.get("next_cursor")),
            canonical_payload=dict(payload),
            query_fingerprint=capture.query_fingerprint,
            result_fingerprint=capture.result_fingerprint,
            observed_at=receipt.executed_at,
            created_at=receipt.executed_at,
            observation_metadata=cls._query_metadata(receipt),
        )
        return result, sources

    @classmethod
    def _compile_detail(
        cls,
        *,
        source_snapshot_id: str,
        receipt: ToolQueryReceipt,
        capture: EvidenceCanonicalCapture,
    ) -> tuple[QueryResultArtifact, tuple[SourceArtifact, ...]]:
        if len(capture.provenance) != 1:
            raise ValueError("Evidence detail requires exactly one provenance item")
        source = cls._source_artifact(
            source_snapshot_id=source_snapshot_id,
            receipt=receipt,
            capture=capture,
            canonical_content=dict(capture.canonical_payload),
            content_level="evidence_detail",
            ordinal=0,
        )
        subject_ref = str(capture.normalized_query["evidence_id"])
        result = QueryResultArtifact.create(
            **cls._scope(source_snapshot_id, receipt),
            operation="evidence_detail_read",
            subject_ref=subject_ref,
            normalized_query=dict(capture.normalized_query),
            normalized_filters={},
            ordered_sources=(source,),
            returned_count=1,
            total=1,
            has_more=None,
            cursor=None,
            next_cursor=None,
            canonical_payload=dict(capture.canonical_payload),
            query_fingerprint=capture.query_fingerprint,
            result_fingerprint=capture.result_fingerprint,
            observed_at=receipt.executed_at,
            created_at=receipt.executed_at,
            observation_metadata=cls._query_metadata(receipt),
        )
        return result, (source,)

    @classmethod
    def _source_artifact(
        cls,
        *,
        source_snapshot_id: str,
        receipt: ToolQueryReceipt,
        capture: EvidenceCanonicalCapture,
        canonical_content: dict[str, Any],
        content_level: Literal["evidence_collection_item", "evidence_detail"],
        ordinal: int,
    ) -> SourceArtifact:
        stable_ref = str(canonical_content.get("evidence_id") or "")
        source_type = str(
            canonical_content.get("evidence_type")
            or canonical_content.get("type")
            or ""
        )
        if ordinal >= len(capture.provenance):
            raise ValueError("canonical Evidence has no matching provenance")
        provenance = capture.provenance[ordinal]
        if provenance.evidence_id != stable_ref:
            raise ValueError("canonical Evidence order differs from provenance")
        return SourceArtifact.create(
            **cls._scope(source_snapshot_id, receipt),
            stable_source_ref=stable_ref,
            source_type=source_type,
            content_level=content_level,
            canonical_content=canonical_content,
            observed_at=receipt.executed_at,
            created_at=receipt.executed_at,
            observation_metadata={
                "tool_name": receipt.tool_name,
                "tool_call_id": receipt.tool_call_id,
                "receipt_id": receipt.receipt_id,
                "query_fingerprint": capture.query_fingerprint,
                "source_hash": provenance.source_hash,
                "asset_status": provenance.asset_status,
                "warnings": list(provenance.warnings),
                "freshness": provenance.freshness,
            },
        )

    @staticmethod
    def _scope(
        source_snapshot_id: str, receipt: ToolQueryReceipt
    ) -> dict[str, str]:
        return {
            "session_id": receipt.session_id,
            "report_version_id": receipt.report_version_id,
            "source_snapshot_id": source_snapshot_id,
            "snapshot_hash": receipt.snapshot_hash,
        }

    @staticmethod
    def _query_metadata(receipt: ToolQueryReceipt) -> dict[str, str]:
        return {
            "tool_name": receipt.tool_name,
            "tool_call_id": receipt.tool_call_id,
            "receipt_id": receipt.receipt_id,
        }

    @staticmethod
    def _required_int(payload: dict[str, Any], key: str) -> int:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Evidence collection {key} must be an integer")
        return value

    @staticmethod
    def _optional_cursor(value: Any) -> str | None:
        return str(value) if value not in (None, "") else None
