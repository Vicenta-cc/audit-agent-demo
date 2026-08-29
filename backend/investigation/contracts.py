from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.domain.identity import stable_hash


REPORT_ID_PATTERN = r"^report:[0-9a-f]{32}$"
REPORT_VERSION_ID_PATTERN = r"^report-version:[0-9a-f]{32}$"
CLAIM_ID_PATTERN = r"^report-claim:[0-9a-f]{32}$"
FINDING_ID_PATTERN = r"^finding:audit_result:[1-9][0-9]*$"
EVIDENCE_ID_PATTERN = r"^evidence:audit_result:[1-9][0-9]*:[A-Za-z0-9_.:-]{1,180}$"
METRIC_KEY_PATTERN = r"^metric:[0-9a-f]{64}$"
SNAPSHOT_HASH_PATTERN = r"^[0-9a-f]{64}$"
FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"
SOURCE_ARTIFACT_ID_PATTERN = r"^source-artifact:[0-9a-f]{64}$"
QUERY_RESULT_ARTIFACT_ID_PATTERN = r"^query-result-artifact:[0-9a-f]{64}$"

SourceArtifactId = Annotated[str, Field(pattern=SOURCE_ARTIFACT_ID_PATTERN)]
QueryResultArtifactId = Annotated[
    str, Field(pattern=QUERY_RESULT_ARTIFACT_ID_PATTERN)
]
Fingerprint = Annotated[str, Field(pattern=FINGERPRINT_PATTERN)]
EvidenceRef = Annotated[str, Field(pattern=EVIDENCE_ID_PATTERN)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceKind(str, Enum):
    REPORT_TEXT = "report_text"
    FROZEN_METRIC = "frozen_metric"
    REPORT_CLAIM = "report_claim"
    FROZEN_CITATION_EXCERPT = "frozen_citation_excerpt"
    CURRENT_FINDING = "current_finding"
    CURRENT_EVIDENCE = "current_evidence"


class PublishedReportContext(StrictModel):
    task_id: str = Field(min_length=1, max_length=128)
    report_id: str = Field(pattern=REPORT_ID_PATTERN)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    version_number: int = Field(ge=1)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    source_hash: str = Field(min_length=1, max_length=128)
    title: str = Field(max_length=500)
    content_hash: str = Field(min_length=1, max_length=128)
    published_at: str
    finding_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


class Referent(StrictModel):
    type: Literal["claim", "finding", "evidence", "case", "account"]
    target_id: str = Field(min_length=1, max_length=240)
    label: str = Field(min_length=1, max_length=300)
    source_message_id: str = Field(default="", max_length=160)
    list_position: int = Field(ge=1, le=100)
    ledger_ids: tuple[str, ...] = Field(default=(), max_length=20)


class ResolvedReference(StrictModel):
    expression: str = Field(max_length=80)
    status: Literal["resolved", "unresolved", "not_applicable"]
    target_type: str = Field(default="", max_length=40)
    target_id: str = Field(default="", max_length=240)
    label: str = Field(default="", max_length=300)


class CaseSelection(StrictModel):
    selected_case_ref: str = Field(pattern=CLAIM_ID_PATTERN)


class FocusedReportFact(StrictModel):
    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)
    claim_type: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2_000)
    source_block: Literal["summary", "conclusion"]


class FocusedCaseContext(StrictModel):
    case_ref: str = Field(pattern=CLAIM_ID_PATTERN)
    case_index: int = Field(ge=1, le=100)
    case_section_id: str = Field(pattern=r"^case-[1-9][0-9]*$")
    title: str = Field(min_length=1, max_length=500)
    case_text: str = Field(min_length=1, max_length=6_000)
    related_claim_refs: tuple[str, ...] = Field(min_length=1, max_length=40)
    related_finding_refs: tuple[str, ...] = Field(default=(), max_length=100)
    related_evidence_refs: tuple[str, ...] = Field(default=(), max_length=200)
    related_metric_refs: tuple[str, ...] = Field(default=(), max_length=100)
    validated_report_wide_facts: tuple[FocusedReportFact, ...] = Field(
        default=(), max_length=20
    )


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]


class QwenUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class QwenChatResult(StrictModel):
    content: str = Field(default="", max_length=100_000)
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = Field(default="", max_length=80)
    usage: QwenUsage = Field(default_factory=QwenUsage)
    model: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=240)


class ToolErrorDetail(StrictModel):
    tool_call_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=100)
    error_code: str = Field(min_length=1, max_length=100)
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class SourceLedgerCandidate(StrictModel):
    source_kind: SourceKind
    metric_key: str = Field(default="", max_length=100)
    section_id: str = Field(default="", max_length=160)
    claim_id: str = Field(default="", max_length=100)
    finding_id: str = Field(default="", max_length=100)
    evidence_id: str = Field(default="", max_length=240)
    source_hash: str = Field(min_length=1, max_length=128)
    excerpt: str = Field(default="", max_length=1600)
    asset_status: str = Field(default="", max_length=80)
    query_fingerprint: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(default="", max_length=200)
    query_receipt_id: str = Field(default="", max_length=160)
    warnings: tuple[str, ...] = Field(default=(), max_length=20)
    freshness: Literal["published_version", "frozen_snapshot", "current_source"]


class ReportPresentationQueryDetails(StrictModel):
    operation: Literal["report_presentation_read"]
    section_refs: tuple[str, ...] = Field(default=(), max_length=500)
    claim_refs: tuple[str, ...] = Field(default=(), max_length=500)
    finding_refs: tuple[str, ...] = Field(default=(), max_length=500)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=1000)
    metric_refs: tuple[str, ...] = Field(default=(), max_length=500)


class ReportMetricQueryDetails(StrictModel):
    operation: Literal["report_metric_lookup"]
    requested_metric_refs: tuple[str, ...] = Field(default=(), max_length=8)
    returned_metric_refs: tuple[str, ...] = Field(default=(), max_length=8)
    returned_count: int = Field(default=0, ge=0, le=8)


class ClaimSupportQueryDetails(StrictModel):
    operation: Literal["claim_support_read"]
    claim_ref: str = Field(default="", max_length=100)
    finding_summary_refs: tuple[str, ...] = Field(default=(), max_length=100)
    frozen_citation_refs: tuple[str, ...] = Field(default=(), max_length=200)
    metric_refs: tuple[str, ...] = Field(default=(), max_length=100)


class ReportFindingsQueryDetails(StrictModel):
    operation: Literal["report_findings_list"]
    returned_finding_refs: tuple[str, ...] = Field(default=(), max_length=20)
    returned_count: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    has_more: bool = False


class FindingDetailQueryDetails(StrictModel):
    operation: Literal["finding_detail_read"]
    finding_ref: str = Field(default="", max_length=100)
    representative_evidence_refs: tuple[str, ...] = Field(default=(), max_length=20)


class FindingEvidenceQueryDetails(StrictModel):
    operation: Literal["finding_evidence_list"]
    finding_ref: str = Field(default="", max_length=100)
    evidence_types: tuple[str, ...] = Field(default=(), max_length=20)
    returned_evidence_refs: tuple[str, ...] = Field(default=(), max_length=1000)
    returned_count: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    has_more: bool = False


class EvidenceDetailQueryDetails(StrictModel):
    operation: Literal["evidence_detail_read"]
    evidence_ref: str = Field(default="", max_length=240)
    finding_ref: str = Field(default="", max_length=100)
    evidence_type: str = Field(default="", max_length=80)


class FailedToolQueryDetails(StrictModel):
    operation: Literal["failed_tool_query"]
    requested_tool_name: str = Field(min_length=1, max_length=100)


ToolQueryDetails = Annotated[
    ReportPresentationQueryDetails
    | ReportMetricQueryDetails
    | ClaimSupportQueryDetails
    | ReportFindingsQueryDetails
    | FindingDetailQueryDetails
    | FindingEvidenceQueryDetails
    | EvidenceDetailQueryDetails
    | FailedToolQueryDetails,
    Field(discriminator="operation"),
]


class ToolQueryReceipt(StrictModel):
    schema_version: Literal["tool-query-receipt-v1"] = "tool-query-receipt-v1"
    receipt_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    tool_call_id: str = Field(min_length=1, max_length=200)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    tool_name: str = Field(min_length=1, max_length=100)
    operation: Literal[
        "report_presentation_read",
        "report_metric_lookup",
        "claim_support_read",
        "report_findings_list",
        "finding_detail_read",
        "finding_evidence_list",
        "evidence_detail_read",
        "failed_tool_query",
    ]
    subject_refs: tuple[str, ...] = Field(default=(), max_length=1000)
    query_params: dict[str, Any] = Field(default_factory=dict)
    arguments_fingerprint: str = Field(min_length=1, max_length=128)
    query_fingerprint: str = Field(min_length=1, max_length=128)
    status: Literal["ok", "error"]
    error_code: str = Field(default="", max_length=100)
    returned_refs: tuple[str, ...] = Field(default=(), max_length=2000)
    model_visible_refs: tuple[str, ...] = Field(default=(), max_length=2000)
    result_count: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    has_more: bool | None = None
    cursor: str = Field(default="", max_length=500)
    next_cursor: str = Field(default="", max_length=500)
    model_output_truncated: bool = False
    result_fingerprint: str = Field(min_length=1, max_length=128)
    details: ToolQueryDetails
    executed_at: str


class ToolResultEnvelope(StrictModel):
    status: Literal["ok", "error"]
    data: dict[str, Any] | list[Any] | None = None
    error: ToolErrorDetail | None = None
    provenance: tuple[SourceLedgerCandidate, ...] = ()
    result_fingerprint: str = Field(min_length=1, max_length=128)
    truncated: bool = False
    query_details: ToolQueryDetails | None = None


class GroundingValidation(StrictModel):
    status: Literal["passed", "failed"]
    source_count: int = Field(ge=0)
    inherited_sources: bool = False
    warnings: tuple[str, ...] = ()


class GroundingIssue(StrictModel):
    issue_type: Literal[
        "internal_id_leak",
        "missing_authoritative_source",
        "unsupported_derived_statistic",
        "ambiguous_numeric_fact",
        "missing_semantic_definition",
    ]
    unsupported_claim: str = Field(default="", max_length=2000)
    numeric_facts: tuple[str, ...] = Field(default=(), max_length=20)
    available_authoritative_facts: tuple[str, ...] = Field(default=(), max_length=40)
    source_refs: tuple[str, ...] = Field(default=(), max_length=40)
    candidate_metric_keys: tuple[str, ...] = Field(default=(), max_length=8)
    message: str = Field(min_length=1, max_length=1000)


class InvestigationSession(StrictModel):
    id: str
    scope_type: Literal["report", "creation"] = "report"
    owner_principal: str = Field(default="", exclude=True)
    task_id: str
    report_id: str
    report_version_id: str
    source_snapshot_id: str
    snapshot_hash: str
    status: Literal["active", "closed"]
    summary_text: str = ""
    active_focus: dict[str, Any] = Field(default_factory=dict)
    ordered_referents: tuple[Referent, ...] = ()
    last_claim_id: str = ""
    last_finding_id: str = ""
    last_evidence_id: str = ""
    last_answer_message_id: str = ""
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def validate_scope_anchor(self) -> "InvestigationSession":
        report_anchor = (
            self.task_id,
            self.report_id,
            self.report_version_id,
            self.source_snapshot_id,
            self.snapshot_hash,
        )
        if self.scope_type == "report" and not all(report_anchor):
            raise ValueError("report-scoped Session requires its complete immutable anchor")
        if self.scope_type == "creation" and any(report_anchor):
            raise ValueError("creation-scoped Session cannot carry a report anchor")
        if self.scope_type == "creation" and not self.owner_principal:
            raise ValueError("creation-scoped Session requires an owner Principal")
        return self


class InvestigationMessage(StrictModel):
    id: str
    session_id: str
    turn_id: str = ""
    role: Literal["user", "assistant", "tool"]
    content: str
    client_message_id: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    sequence: int = Field(ge=1)
    created_at: str


class InvestigationTurn(StrictModel):
    id: str
    session_id: str
    user_message_id: str
    assistant_message_id: str = ""
    client_message_id: str
    status: Literal["running", "completed", "interrupted", "error"]
    current_node: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    stop_reason: str = ""
    error_code: str = ""
    safe_message: str = ""
    retryable: bool = False
    public_artifact: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    started_at: str
    completed_at: str = ""


class SourceLedgerEntry(StrictModel):
    ledger_id: str
    session_id: str
    turn_id: str
    message_id: str
    report_version_id: str
    snapshot_hash: str
    source_kind: SourceKind
    metric_key: str = ""
    section_id: str = ""
    claim_id: str = ""
    finding_id: str = ""
    evidence_id: str = ""
    source_hash: str
    excerpt: str = ""
    asset_status: str = ""
    query_fingerprint: str
    tool_call_id: str = ""
    query_receipt_id: str = ""
    warnings: tuple[str, ...] = ()
    freshness: str
    created_at: str


class NoSourceTurnPlan(StrictModel):
    requirement_kind: Literal["none"]


class ReportPresentationTurnPlan(StrictModel):
    requirement_kind: Literal["report_presentation"]


class FocusedClaimSupportTurnPlan(StrictModel):
    requirement_kind: Literal["focused_claim_support"]


PlannerEvidenceType = Literal["text", "comment", "ocr", "asr", "visual", "keyframe"]
MaterializedEvidenceType = Literal[
    "text",
    "comment",
    "ocr",
    "asr",
    "visual",
    "keyframe",
    "profile",
    "rule_reference",
    "other",
]


class FindingEvidenceCollectionTurnPlan(StrictModel):
    requirement_kind: Literal["finding_evidence_collection"]
    evidence_types: tuple[PlannerEvidenceType, ...] | None = Field(
        default=None, min_length=1, max_length=6
    )
    coverage: Literal["discovery", "complete"]

    @model_validator(mode="after")
    def reject_duplicate_evidence_types(self):
        values = self.evidence_types or ()
        if len(values) != len(set(values)):
            raise ValueError("evidence_types must be unique")
        return self


class EvidenceDetailTurnPlan(StrictModel):
    requirement_kind: Literal["evidence_detail"]


class UnsupportedTurnPlan(StrictModel):
    requirement_kind: Literal["unsupported"]
    reason: Literal[
        "multiple_source_requirements",
        "out_of_scope_source_requirement",
    ]


TurnPlan = Annotated[
    NoSourceTurnPlan
    | ReportPresentationTurnPlan
    | FocusedClaimSupportTurnPlan
    | FindingEvidenceCollectionTurnPlan
    | EvidenceDetailTurnPlan
    | UnsupportedTurnPlan,
    Field(discriminator="requirement_kind"),
]


class BoundEvidenceCollectionRequirement(StrictModel):
    schema_version: Literal["bound-requirement-v1"] = "bound-requirement-v1"
    requirement_kind: Literal["finding_evidence_collection"]
    session_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    finding_ref: str = Field(pattern=FINDING_ID_PATTERN)
    evidence_types: tuple[PlannerEvidenceType, ...] | None = Field(
        default=None, min_length=1, max_length=6
    )
    coverage: Literal["discovery", "complete"]

    @model_validator(mode="after")
    def validate_evidence_types(self):
        evidence_types = self.evidence_types or ()
        if len(evidence_types) != len(set(evidence_types)):
            raise ValueError("evidence_types must be unique")
        return self


class BoundEvidenceDetailRequirement(StrictModel):
    schema_version: Literal["bound-requirement-v1"] = "bound-requirement-v1"
    requirement_kind: Literal["evidence_detail"]
    session_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    evidence_ref: str = Field(pattern=EVIDENCE_ID_PATTERN)


BoundRequirement = Annotated[
    BoundEvidenceCollectionRequirement | BoundEvidenceDetailRequirement,
    Field(discriminator="requirement_kind"),
]


class QueryResultArtifactMember(StrictModel):
    ordinal: int = Field(ge=0, le=10_000)
    stable_source_ref: EvidenceRef
    source_artifact_id: SourceArtifactId
    content_level: Literal["evidence_collection_item", "evidence_detail"]


_FORBIDDEN_ARTIFACT_KEYS = {
    "access_token",
    "api_key",
    "asset_path",
    "file_path",
    "local_path",
    "password",
    "secret",
}


def _reject_sensitive_artifact_keys(value: Any) -> Any:
    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).lower() in _FORBIDDEN_ARTIFACT_KEYS:
                    raise ValueError(f"artifact payload contains forbidden key: {key}")
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return value


class SourceArtifact(StrictModel):
    """Immutable canonical content for one source observation, never a ledger excerpt."""

    schema_version: Literal["source-artifact-v1"] = "source-artifact-v1"
    artifact_id: SourceArtifactId
    session_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    stable_source_ref: EvidenceRef
    source_type: MaterializedEvidenceType
    content_level: Literal["evidence_collection_item", "evidence_detail"]
    canonical_content: dict[str, Any]
    canonical_content_hash: Fingerprint
    freshness: Literal["current_source"] = "current_source"
    observed_at: str = Field(min_length=1, max_length=80)
    observation_metadata: dict[str, Any] = Field(default_factory=dict)
    source_fingerprint: Fingerprint
    created_at: str = Field(min_length=1, max_length=80)

    @field_validator("canonical_content", "observation_metadata")
    @classmethod
    def reject_sensitive_keys(cls, value: dict[str, Any]):
        return _reject_sensitive_artifact_keys(value)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        report_version_id: str,
        source_snapshot_id: str,
        snapshot_hash: str,
        stable_source_ref: str,
        source_type: MaterializedEvidenceType,
        content_level: Literal["evidence_collection_item", "evidence_detail"],
        canonical_content: dict[str, Any],
        observed_at: str,
        created_at: str,
        observation_metadata: dict[str, Any] | None = None,
    ) -> SourceArtifact:
        content_hash = stable_hash(canonical_content)
        metadata = dict(observation_metadata or {})
        fingerprint = stable_hash(
            {
                "schema_version": "source-artifact-v1",
                "scope": {
                    "session_id": session_id,
                    "report_version_id": report_version_id,
                    "source_snapshot_id": source_snapshot_id,
                    "snapshot_hash": snapshot_hash,
                },
                "stable_source_ref": stable_source_ref,
                "source_type": source_type,
                "content_level": content_level,
                "canonical_content_hash": content_hash,
                "freshness": "current_source",
                "observed_at": observed_at,
                "observation_metadata": metadata,
            }
        )
        return cls(
            artifact_id=f"source-artifact:{fingerprint}",
            session_id=session_id,
            report_version_id=report_version_id,
            source_snapshot_id=source_snapshot_id,
            snapshot_hash=snapshot_hash,
            stable_source_ref=stable_source_ref,
            source_type=source_type,
            content_level=content_level,
            canonical_content=canonical_content,
            canonical_content_hash=content_hash,
            observed_at=observed_at,
            observation_metadata=metadata,
            source_fingerprint=fingerprint,
            created_at=created_at,
        )

    @model_validator(mode="after")
    def validate_fingerprints(self):
        if self.canonical_content.get("evidence_id") != self.stable_source_ref:
            raise ValueError("canonical content does not match stable_source_ref")
        payload_type = self.canonical_content.get("evidence_type")
        if payload_type is None:
            payload_type = self.canonical_content.get("type")
        if payload_type is not None and payload_type != self.source_type:
            raise ValueError("canonical content does not match source_type")
        if self.canonical_content_hash != stable_hash(self.canonical_content):
            raise ValueError("canonical_content_hash does not match canonical_content")
        expected = stable_hash(
            {
                "schema_version": self.schema_version,
                "scope": {
                    "session_id": self.session_id,
                    "report_version_id": self.report_version_id,
                    "source_snapshot_id": self.source_snapshot_id,
                    "snapshot_hash": self.snapshot_hash,
                },
                "stable_source_ref": self.stable_source_ref,
                "source_type": self.source_type,
                "content_level": self.content_level,
                "canonical_content_hash": self.canonical_content_hash,
                "freshness": self.freshness,
                "observed_at": self.observed_at,
                "observation_metadata": self.observation_metadata,
            }
        )
        if self.source_fingerprint != expected:
            raise ValueError("source_fingerprint does not match artifact identity")
        if self.artifact_id != f"source-artifact:{expected}":
            raise ValueError("artifact_id does not match source_fingerprint")
        return self


class QueryResultArtifact(StrictModel):
    """Canonical query response and its ordered source membership."""

    schema_version: Literal["query-result-artifact-v1"] = (
        "query-result-artifact-v1"
    )
    artifact_id: QueryResultArtifactId
    session_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    operation: Literal["finding_evidence_list", "evidence_detail_read"]
    subject_ref: str = Field(min_length=1, max_length=240)
    normalized_query: dict[str, Any]
    normalized_filters: dict[str, Any] = Field(default_factory=dict)
    ordered_members: tuple[QueryResultArtifactMember, ...] = Field(
        default=(), max_length=10_000
    )
    returned_count: int = Field(ge=0, le=10_000)
    total: int | None = Field(default=None, ge=0)
    has_more: bool | None = None
    cursor: str | None = Field(default=None, max_length=500)
    next_cursor: str | None = Field(default=None, max_length=500)
    canonical_payload: dict[str, Any]
    canonical_payload_hash: Fingerprint
    query_fingerprint: Fingerprint
    result_fingerprint: Fingerprint
    observed_at: str = Field(min_length=1, max_length=80)
    observation_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(min_length=1, max_length=80)

    @field_validator(
        "normalized_query",
        "normalized_filters",
        "canonical_payload",
        "observation_metadata",
    )
    @classmethod
    def reject_sensitive_keys(cls, value: dict[str, Any]):
        return _reject_sensitive_artifact_keys(value)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        report_version_id: str,
        source_snapshot_id: str,
        snapshot_hash: str,
        operation: Literal["finding_evidence_list", "evidence_detail_read"],
        subject_ref: str,
        normalized_query: dict[str, Any],
        normalized_filters: dict[str, Any],
        ordered_sources: tuple[SourceArtifact, ...],
        returned_count: int,
        total: int | None,
        has_more: bool | None,
        cursor: str | None,
        next_cursor: str | None,
        canonical_payload: dict[str, Any],
        query_fingerprint: str,
        result_fingerprint: str,
        observed_at: str,
        created_at: str,
        observation_metadata: dict[str, Any] | None = None,
    ) -> QueryResultArtifact:
        members = tuple(
            QueryResultArtifactMember(
                ordinal=index,
                stable_source_ref=source.stable_source_ref,
                source_artifact_id=source.artifact_id,
                content_level=source.content_level,
            )
            for index, source in enumerate(ordered_sources)
        )
        payload_hash = stable_hash(canonical_payload)
        metadata = dict(observation_metadata or {})
        identity = stable_hash(
            {
                "schema_version": "query-result-artifact-v1",
                "scope": {
                    "session_id": session_id,
                    "report_version_id": report_version_id,
                    "source_snapshot_id": source_snapshot_id,
                    "snapshot_hash": snapshot_hash,
                },
                "operation": operation,
                "subject_ref": subject_ref,
                "normalized_query": normalized_query,
                "normalized_filters": normalized_filters,
                "ordered_members": [item.model_dump(mode="json") for item in members],
                "returned_count": returned_count,
                "total": total,
                "has_more": has_more,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "canonical_payload_hash": payload_hash,
                "query_fingerprint": query_fingerprint,
                "result_fingerprint": result_fingerprint,
                "observed_at": observed_at,
                "observation_metadata": metadata,
            }
        )
        return cls(
            artifact_id=f"query-result-artifact:{identity}",
            session_id=session_id,
            report_version_id=report_version_id,
            source_snapshot_id=source_snapshot_id,
            snapshot_hash=snapshot_hash,
            operation=operation,
            subject_ref=subject_ref,
            normalized_query=normalized_query,
            normalized_filters=normalized_filters,
            ordered_members=members,
            returned_count=returned_count,
            total=total,
            has_more=has_more,
            cursor=cursor,
            next_cursor=next_cursor,
            canonical_payload=canonical_payload,
            canonical_payload_hash=payload_hash,
            query_fingerprint=query_fingerprint,
            result_fingerprint=result_fingerprint,
            observed_at=observed_at,
            observation_metadata=metadata,
            created_at=created_at,
        )

    @model_validator(mode="after")
    def validate_query_result(self):
        if self.canonical_payload_hash != stable_hash(self.canonical_payload):
            raise ValueError("canonical_payload_hash does not match canonical_payload")
        if self.returned_count != len(self.ordered_members):
            raise ValueError("returned_count must match ordered_members")
        if self.total is not None and self.total < self.returned_count:
            raise ValueError("total cannot be smaller than returned_count")
        ordinals = tuple(item.ordinal for item in self.ordered_members)
        if ordinals != tuple(range(len(self.ordered_members))):
            raise ValueError("ordered member ordinals must be contiguous from zero")
        source_refs = tuple(item.stable_source_ref for item in self.ordered_members)
        source_artifact_ids = tuple(
            item.source_artifact_id for item in self.ordered_members
        )
        if len(source_refs) != len(set(source_refs)):
            raise ValueError("ordered source refs must be unique")
        if len(source_artifact_ids) != len(set(source_artifact_ids)):
            raise ValueError("ordered source artifacts must be unique")
        if self.operation == "finding_evidence_list":
            if not re.fullmatch(FINDING_ID_PATTERN, self.subject_ref):
                raise ValueError("evidence collection subject must be a finding")
            if any(
                item.content_level != "evidence_collection_item"
                for item in self.ordered_members
            ):
                raise ValueError("evidence collection cannot contain detail artifacts")
            if self.normalized_query.get("finding_id") != self.subject_ref:
                raise ValueError("normalized query does not match collection subject")
            query_types = tuple(self.normalized_query.get("evidence_types") or ())
            filter_types = tuple(self.normalized_filters.get("evidence_types") or ())
            if query_types != filter_types or len(query_types) != len(set(query_types)):
                raise ValueError("normalized Evidence filters are inconsistent")
            if any(item not in get_args(PlannerEvidenceType) for item in query_types):
                raise ValueError("normalized query contains unsupported Evidence type")
            payload_items = self.canonical_payload.get("items")
            if not isinstance(payload_items, list):
                raise ValueError("collection canonical payload requires items")
            payload_refs = tuple(
                str(item.get("evidence_id") or "")
                for item in payload_items
                if isinstance(item, dict)
            )
            if payload_refs != source_refs:
                raise ValueError("canonical payload order does not match members")
            if (
                self.canonical_payload.get("returned") != self.returned_count
                or self.canonical_payload.get("total") != self.total
                or self.canonical_payload.get("has_more") != self.has_more
            ):
                raise ValueError("canonical payload cardinality is inconsistent")
        else:
            if not re.fullmatch(EVIDENCE_ID_PATTERN, self.subject_ref):
                raise ValueError("evidence detail subject must be an evidence ref")
            if len(self.ordered_members) != 1:
                raise ValueError("evidence detail must contain exactly one source")
            member = self.ordered_members[0]
            if (
                member.content_level != "evidence_detail"
                or member.stable_source_ref != self.subject_ref
            ):
                raise ValueError("evidence detail member must match its subject")
            if (
                self.normalized_query.get("evidence_id") != self.subject_ref
                or self.normalized_filters
            ):
                raise ValueError("normalized detail query is inconsistent")
            if self.canonical_payload.get("evidence_id") != self.subject_ref:
                raise ValueError("detail canonical payload does not match its subject")
        expected = stable_hash(
            {
                "schema_version": self.schema_version,
                "scope": {
                    "session_id": self.session_id,
                    "report_version_id": self.report_version_id,
                    "source_snapshot_id": self.source_snapshot_id,
                    "snapshot_hash": self.snapshot_hash,
                },
                "operation": self.operation,
                "subject_ref": self.subject_ref,
                "normalized_query": self.normalized_query,
                "normalized_filters": self.normalized_filters,
                "ordered_members": [
                    item.model_dump(mode="json") for item in self.ordered_members
                ],
                "returned_count": self.returned_count,
                "total": self.total,
                "has_more": self.has_more,
                "cursor": self.cursor,
                "next_cursor": self.next_cursor,
                "canonical_payload_hash": self.canonical_payload_hash,
                "query_fingerprint": self.query_fingerprint,
                "result_fingerprint": self.result_fingerprint,
                "observed_at": self.observed_at,
                "observation_metadata": self.observation_metadata,
            }
        )
        if self.artifact_id != f"query-result-artifact:{expected}":
            raise ValueError("artifact_id does not match query result identity")
        return self


class QueryResultArtifactLink(StrictModel):
    schema_version: Literal["query-result-artifact-link-v1"] = (
        "query-result-artifact-link-v1"
    )
    receipt_id: str = Field(min_length=1, max_length=160)
    query_result_artifact_id: QueryResultArtifactId
    linked_at: str = Field(min_length=1, max_length=80)


class QueryArtifactIndexEntry(StrictModel):
    """Derived, rebuildable lookup entry; never an independent source of truth."""

    receipt_id: str = Field(min_length=1, max_length=160)
    query_result_artifact_id: QueryResultArtifactId
    session_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    operation: Literal["finding_evidence_list", "evidence_detail_read"]
    subject_ref: str = Field(min_length=1, max_length=240)
    normalized_query: dict[str, Any]
    normalized_filters: dict[str, Any]
    query_fingerprint: Fingerprint
    result_fingerprint: Fingerprint
    observed_at: str = Field(min_length=1, max_length=80)
    linked_at: str = Field(min_length=1, max_length=80)


class SourceCoverageProof(StrictModel):
    requested_coverage: Literal["discovery", "complete"]
    returned_count: int = Field(ge=0)
    total: int | None = Field(default=None, ge=0)
    has_more: bool | None = None
    acquisition_completeness: Literal["complete", "partial", "unknown"]
    proof_fingerprint: Fingerprint

    @model_validator(mode="after")
    def validate_complete_proof(self):
        if self.total is not None and self.total < self.returned_count:
            raise ValueError("total cannot be smaller than returned_count")
        if self.requested_coverage == "complete" and (
            self.acquisition_completeness != "complete"
            or self.has_more is not False
            or self.total is None
            or self.returned_count != self.total
        ):
            raise ValueError("complete coverage requires an exact complete acquisition")
        return self


class ProjectionTokenAccounting(StrictModel):
    source_tokens: int = Field(ge=0)
    source_token_budget: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_budget(self):
        if self.source_tokens > self.source_token_budget:
            raise ValueError("projected source tokens exceed their budget")
        return self


class ReadySourceBundle(StrictModel):
    schema_version: Literal["ready-source-bundle-v1", "ready-source-bundle-v2"] = (
        "ready-source-bundle-v1"
    )
    status: Literal["ready"] = "ready"
    bound_requirement: BoundRequirement
    supplemental_requirements: tuple[BoundEvidenceDetailRequirement, ...] = Field(
        default=(), max_length=2
    )
    parent_bundle_fingerprint: Fingerprint | None = None
    react_iteration_count: int = Field(default=0, ge=0, le=2)
    query_result_artifact_ids: tuple[QueryResultArtifactId, ...] = Field(
        min_length=1, max_length=100
    )
    source_artifact_ids: tuple[SourceArtifactId, ...] = Field(
        default=(), max_length=10_000
    )
    ordered_projected_refs: tuple[EvidenceRef, ...] = Field(
        default=(), max_length=10_000
    )
    projected_source_message: str = Field(min_length=1, max_length=100_000)
    coverage_proof: SourceCoverageProof | None = None
    acquisition_completeness: Literal["complete", "partial", "unknown"]
    projection_sufficiency: Literal["sufficient"] = "sufficient"
    answer_scope: Literal[
        "collection_discovery", "collection_complete", "evidence_detail"
    ]
    projection_truncated: bool
    query_fingerprints: tuple[Fingerprint, ...] = Field(min_length=1, max_length=100)
    result_fingerprints: tuple[Fingerprint, ...] = Field(min_length=1, max_length=100)
    bundle_fingerprint: Fingerprint
    projection_policy_version: str = Field(min_length=1, max_length=100)
    token_accounting: ProjectionTokenAccounting

    @model_validator(mode="after")
    def validate_requirement_scope(self):
        if isinstance(self.bound_requirement, BoundEvidenceCollectionRequirement):
            expected_scope = (
                "collection_complete"
                if self.bound_requirement.coverage == "complete"
                else "collection_discovery"
            )
            if self.answer_scope != expected_scope or self.coverage_proof is None:
                raise ValueError("collection bundle requires matching coverage proof")
            if (
                self.coverage_proof.requested_coverage
                != self.bound_requirement.coverage
            ):
                raise ValueError("coverage proof does not match bound requirement")
            if (
                self.coverage_proof.acquisition_completeness
                != self.acquisition_completeness
            ):
                raise ValueError("coverage proof and bundle acquisition differ")
        elif self.answer_scope != "evidence_detail" or self.coverage_proof is not None:
            raise ValueError("detail bundle cannot contain collection coverage proof")
        if len(self.query_result_artifact_ids) != len(
            set(self.query_result_artifact_ids)
        ):
            raise ValueError("query result artifact IDs must be unique")
        if len(self.source_artifact_ids) != len(set(self.source_artifact_ids)):
            raise ValueError("source artifact IDs must be unique")
        if len(self.ordered_projected_refs) != len(
            set(self.ordered_projected_refs)
        ):
            raise ValueError("projected Evidence refs must be unique")
        if len(self.query_fingerprints) != len(self.query_result_artifact_ids) or len(
            self.result_fingerprints
        ) != len(self.query_result_artifact_ids):
            raise ValueError("Bundle query identities must match query result artifacts")
        supplemental_refs = tuple(
            item.evidence_ref for item in self.supplemental_requirements
        )
        if len(supplemental_refs) != len(set(supplemental_refs)):
            raise ValueError("supplemental Evidence detail requirements must be unique")
        if self.schema_version == "ready-source-bundle-v1":
            if (
                self.supplemental_requirements
                or self.parent_bundle_fingerprint is not None
                or self.react_iteration_count != 0
            ):
                raise ValueError("v1 Bundle cannot contain Controlled ReAct lineage")
        elif (
            not isinstance(
                self.bound_requirement, BoundEvidenceCollectionRequirement
            )
            or not self.supplemental_requirements
            or self.parent_bundle_fingerprint is None
            or self.react_iteration_count != len(self.supplemental_requirements)
            or len(self.query_result_artifact_ids)
            != 1 + len(self.supplemental_requirements)
            or not set(supplemental_refs).issubset(self.ordered_projected_refs)
        ):
            raise ValueError("v2 Bundle requires valid Controlled ReAct lineage")
        return self


class UnsupportedComplete(StrictModel):
    status: Literal["unsupported_complete"]
    bound_requirement: BoundEvidenceCollectionRequirement
    reason: Literal[
        "acquisition_incomplete", "tool_not_pageable", "projection_incomplete"
    ]

    @model_validator(mode="after")
    def require_complete(self):
        if self.bound_requirement.coverage != "complete":
            raise ValueError("UnsupportedComplete requires complete coverage")
        return self


class UnsupportedCapacity(StrictModel):
    status: Literal["unsupported_capacity"]
    bound_requirement: BoundRequirement
    capacity_kind: Literal["context_capacity", "output_capacity"]
    reason: str = Field(min_length=1, max_length=500)


class SourceFailure(StrictModel):
    status: Literal["source_failure"]
    bound_requirement: BoundRequirement
    error_code: str = Field(min_length=1, max_length=100)
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class AcquisitionRequired(StrictModel):
    status: Literal["acquisition_required"]
    bound_requirement: BoundRequirement
    required_tool: Literal["list_finding_evidence", "read_evidence_detail"]
    required_arguments: dict[str, Any]
    reason: Literal["no_reusable_artifact"] = "no_reusable_artifact"

    @model_validator(mode="after")
    def validate_required_tool(self):
        if isinstance(self.bound_requirement, BoundEvidenceCollectionRequirement):
            expected_tool = "list_finding_evidence"
            expected_arguments = {
                "finding_id": self.bound_requirement.finding_ref,
                "evidence_types": list(self.bound_requirement.evidence_types or ()),
                "limit": 20,
            }
        else:
            expected_tool = "read_evidence_detail"
            expected_arguments = {
                "evidence_id": self.bound_requirement.evidence_ref,
            }
        if (
            self.required_tool != expected_tool
            or self.required_arguments != expected_arguments
        ):
            raise ValueError("required Tool does not match bound requirement")
        return self


PreparationResult = Annotated[
    ReadySourceBundle | UnsupportedComplete | UnsupportedCapacity | SourceFailure,
    Field(discriminator="status"),
]


SourcePreparationDecision = Annotated[
    ReadySourceBundle
    | AcquisitionRequired
    | UnsupportedComplete
    | UnsupportedCapacity
    | SourceFailure,
    Field(discriminator="status"),
]


class SubmitTurnPlanInput(StrictModel):
    plan: TurnPlan


class SubmitTurnPlanToolInput(StrictModel):
    """Flat function-call transport; converted into the strict TurnPlan union."""

    requirement_kind: Literal[
        "none",
        "report_presentation",
        "focused_claim_support",
        "finding_evidence_collection",
        "evidence_detail",
        "unsupported",
    ]
    evidence_types: tuple[PlannerEvidenceType, ...] | None = Field(
        min_length=1,
        max_length=6,
        description=(
            "Evidence type restrictions for one finding evidence collection; "
            "null when no type was requested or the requirement is not a collection."
        ),
    )
    coverage: Literal["discovery", "complete"] | None = Field(
        description=(
            "Coverage for a finding evidence collection; null for every other "
            "requirement kind."
        )
    )
    reason: Literal[
        "multiple_source_requirements",
        "out_of_scope_source_requirement",
    ] | None = Field(
        description="Unsupported reason; null unless requirement_kind is unsupported."
    )

    @model_validator(mode="after")
    def validate_variant_fields(self):
        if self.requirement_kind == "finding_evidence_collection":
            if self.coverage is None or self.reason is not None:
                raise ValueError("evidence collection requires only coverage and types")
        elif self.requirement_kind == "unsupported":
            if (
                self.reason is None
                or self.coverage is not None
                or self.evidence_types is not None
            ):
                raise ValueError("unsupported requires only a reason")
        elif self.requirement_kind == "evidence_detail":
            if (
                self.evidence_types is not None
                or self.coverage is not None
                or self.reason is not None
            ):
                raise ValueError("evidence detail does not accept variant fields")
        elif self.evidence_types is not None or self.coverage is not None or self.reason is not None:
            raise ValueError("this requirement kind does not accept variant fields")
        if self.evidence_types and len(self.evidence_types) != len(set(self.evidence_types)):
            raise ValueError("evidence_types must be unique")
        return self

    def as_submission(self) -> SubmitTurnPlanInput:
        plan: dict[str, Any] = {"requirement_kind": self.requirement_kind}
        if self.requirement_kind == "finding_evidence_collection":
            plan["evidence_types"] = self.evidence_types
            plan["coverage"] = self.coverage
        elif self.requirement_kind == "unsupported":
            plan["reason"] = self.reason
        return SubmitTurnPlanInput.model_validate({"plan": plan})


class PlannerInputSnapshot(StrictModel):
    current_user_message: str = Field(min_length=1, max_length=4_000)
    active_focus_exists: bool
    active_focus_type: str = Field(default="", max_length=40)
    resolved_referent_status: Literal[
        "resolved", "unresolved", "not_applicable", "mixed"
    ]
    resolved_referent_type: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def validate_focus_and_referent_shape(self):
        if self.active_focus_exists != bool(self.active_focus_type):
            raise ValueError("active focus existence and type must agree")
        if self.resolved_referent_status != "resolved" and self.resolved_referent_type:
            raise ValueError("only a resolved referent can expose a type")
        return self


class PlannerShadowTrace(StrictModel):
    schema_version: Literal["planner-shadow-trace-v1"] = "planner-shadow-trace-v1"
    trace_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    planner_prompt_version: str = Field(min_length=1, max_length=100)
    input_fingerprint: str = Field(min_length=1, max_length=128)
    active_focus_exists: bool
    active_focus_type: str = Field(default="", max_length=40)
    resolved_referent_status: Literal[
        "resolved", "unresolved", "not_applicable", "mixed"
    ]
    resolved_referent_type: str = Field(default="", max_length=40)
    planning_status: Literal["ok", "error"]
    plan: TurnPlan | None = None
    error_code: str = Field(default="", max_length=100)
    error_message: str = Field(default="", max_length=500)
    planner_error: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=200)
    request_id: str = Field(default="", max_length=240)
    planner_input_tokens: int = Field(default=0, ge=0)
    planner_output_tokens: int = Field(default=0, ge=0)
    planner_total_tokens: int = Field(default=0, ge=0)
    planner_llm_call_count: int = Field(default=0, ge=0, le=4)
    planner_latency_ms: int = Field(default=0, ge=0)
    planner_retry_count: int = Field(default=0, ge=0, le=3)
    created_at: str

    @model_validator(mode="after")
    def validate_status_payload(self):
        if self.planning_status == "ok":
            if (
                self.plan is None
                or self.error_code
                or self.error_message
                or self.planner_error
            ):
                raise ValueError("successful Planner trace must contain only a plan")
        elif (
            self.plan is not None
            or not self.error_code
            or not self.planner_error
            or self.planner_error != self.error_code
        ):
            raise ValueError("failed Planner trace must contain an error and no plan")
        return self


class SourcePreparationShadowTrace(StrictModel):
    schema_version: Literal["source-preparation-shadow-trace-v1"] = (
        "source-preparation-shadow-trace-v1"
    )
    trace_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    planner_trace_id: str = Field(min_length=1, max_length=160)
    planner_prompt_version: str = Field(min_length=1, max_length=100)
    subject_binder_version: str = Field(min_length=1, max_length=100)
    orchestrator_version: str = Field(min_length=1, max_length=100)
    input_fingerprint: Fingerprint
    requirement_kind: Literal[
        "none",
        "report_presentation",
        "focused_claim_support",
        "finding_evidence_collection",
        "evidence_detail",
        "unsupported",
    ]
    binding_status: Literal["not_applicable", "need_subject", "bound", "error"]
    status: Literal[
        "not_applicable",
        "planner_error",
        "need_subject",
        "acquisition_required",
        "ready",
        "unsupported_complete",
        "unsupported_capacity",
        "source_failure",
        "error",
    ]
    bound_requirement: BoundRequirement | None = None
    preparation_result: SourcePreparationDecision | None = None
    matched_query_result_artifact_ids: tuple[QueryResultArtifactId, ...] = Field(
        default=(), max_length=100
    )
    selected_query_result_artifact_id: QueryResultArtifactId | None = None
    reason: str = Field(default="", max_length=500)
    error_code: str = Field(default="", max_length=100)
    error_message: str = Field(default="", max_length=500)
    created_at: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_shadow_outcome(self):
        preparation_statuses = {
            "acquisition_required",
            "ready",
            "unsupported_complete",
            "unsupported_capacity",
            "source_failure",
        }
        if self.status in preparation_statuses:
            if (
                self.binding_status != "bound"
                or self.bound_requirement is None
                or self.preparation_result is None
                or self.preparation_result.status != self.status
                or self.preparation_result.bound_requirement != self.bound_requirement
                or self.error_code
                or self.error_message
            ):
                raise ValueError("prepared shadow trace is internally inconsistent")
        elif self.status == "need_subject":
            if (
                self.binding_status != "need_subject"
                or self.bound_requirement is not None
                or self.preparation_result is not None
                or not self.reason
                or self.error_code
                or self.error_message
            ):
                raise ValueError("need-subject trace is internally inconsistent")
        elif self.status == "not_applicable":
            if (
                self.binding_status != "not_applicable"
                or self.bound_requirement is not None
                or self.preparation_result is not None
                or self.error_code
                or self.error_message
            ):
                raise ValueError("not-applicable trace is internally inconsistent")
        elif (
            self.status not in {"planner_error", "error"}
            or not self.error_code
            or not self.error_message
            or self.preparation_result is not None
        ):
            raise ValueError("failed shadow trace requires an explicit error")
        if self.selected_query_result_artifact_id and (
            self.selected_query_result_artifact_id
            not in self.matched_query_result_artifact_ids
        ):
            raise ValueError("selected query result must be one of the matched results")
        if len(self.matched_query_result_artifact_ids) != len(
            set(self.matched_query_result_artifact_ids)
        ):
            raise ValueError("matched query result IDs must be unique")
        return self


class TurnResult(StrictModel):
    session_id: str
    turn_id: str
    message_id: str
    answer: str
    status: Literal["completed", "error"]
    tool_calls: tuple[ToolCall, ...] = ()
    tool_names: tuple[str, ...] = ()
    query_receipts: tuple[ToolQueryReceipt, ...] = ()
    planner_shadow_trace: PlannerShadowTrace | None = None
    source_preparation_shadow_trace: SourcePreparationShadowTrace | None = None
    accessed_sources: tuple[SourceLedgerEntry, ...] = ()
    # Compatibility alias. These are accessed-source audit entries, not a claim
    # that the final answer used every source.
    citations: tuple[SourceLedgerEntry, ...] = ()
    resolved_references: tuple[ResolvedReference, ...] = ()
    grounding_validation: GroundingValidation
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    context_accounting: tuple[dict[str, Any], ...] = ()
    grounding_issues: tuple[dict[str, Any], ...] = ()
    grounding_repair_count: int = 0
    scope_repair_count: int = 0
    source_repair_count: int = 0
    semantic_rewrite_count: int = 0
    scope_initial_draft: str = ""
    scope_repaired_draft: str = ""
    scope_initial_issues: tuple[dict[str, Any], ...] = ()
    scope_remaining_issues: tuple[dict[str, Any], ...] = ()
    stop_reason: str = ""
    idempotent_replay: bool = False


class InvestigationAgentState(TypedDict, total=False):
    session_id: str
    turn_id: str
    task_id: str
    report_id: str
    report_version_id: str
    source_snapshot_id: str
    snapshot_hash: str
    summary_text: str
    recent_messages: list[dict[str, Any]]
    working_messages: list[dict[str, Any]]
    user_input: str
    response_style: str
    active_focus: dict[str, Any]
    ordered_referents: list[dict[str, Any]]
    last_claim_id: str
    last_finding_id: str
    last_evidence_id: str
    last_answer_message_id: str
    resolved_references: list[dict[str, Any]]
    case_catalog: list[dict[str, Any]]
    case_selection_available: bool
    case_selection_completed: bool
    case_selection_trace: dict[str, Any]
    case_selection_membership_validated: bool
    focused_case: dict[str, Any]
    focused_authoritative_facts: list[dict[str, Any]]
    focused_validated_claim_refs: list[str]
    focused_validated_finding_refs: list[str]
    focused_validated_evidence_refs: list[str]
    focused_authority_required: bool
    conversation_continuity: list[dict[str, Any]]
    inherit_last_sources: bool
    inherited_ledger_entries: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    all_tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    all_tool_results: list[dict[str, Any]]
    query_receipts: list[dict[str, Any]]
    pending_ledger_entries: list[dict[str, Any]]
    recent_ledger_refs: list[dict[str, Any]]
    source_warnings: list[str]
    evidence_source_control_active: bool
    evidence_source_control_status: str
    evidence_source_bound_requirement: dict[str, Any]
    evidence_source_attempt_count: int
    evidence_source_history: list[dict[str, Any]]
    ready_source_bundle: dict[str, Any]
    evidence_react_enabled: bool
    evidence_react_allowed_refs: list[str]
    evidence_react_supplemental_requirements: list[dict[str, Any]]
    evidence_react_iteration_count: int
    evidence_react_bundle_fingerprints: list[str]
    answer: str
    citations: list[dict[str, Any]]
    grounding_validation: dict[str, Any]
    tool_iteration_count: int
    tool_call_fingerprints: list[str]
    tool_result_fingerprints: list[str]
    previous_progress_signature: str
    consecutive_no_progress: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    llm_call_count: int
    model_call_ids: list[str]
    token_warning: bool
    tools_disabled: bool
    force_tool_choice: bool
    stop_reason: str
    final_response_retry_count: int
    grounding_retry_count: int
    scope_repair_count: int
    source_repair_count: int
    semantic_rewrite_count: int
    repair_mode: str
    scope_source_tools_allowed: bool
    scope_initial_draft: str
    scope_repaired_draft: str
    scope_initial_issues: list[dict[str, Any]]
    scope_remaining_issues: list[dict[str, Any]]
    numeric_validation_errors: list[str]
    grounding_issues: list[dict[str, Any]]
    grounding_draft: str
    repair_working_start: int
    context_accounting: list[dict[str, Any]]
    latest_model_response: dict[str, Any]
    current_node: str
    error: str
    retryable: bool
    safe_message: str
