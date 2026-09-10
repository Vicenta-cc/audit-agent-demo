from __future__ import annotations

from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.warnings import DataQuality


class EvidenceType(str, Enum):
    TEXT = "text"
    COMMENT = "comment"
    OCR = "ocr"
    ASR = "asr"
    KEYFRAME = "keyframe"
    VISUAL = "visual"
    PROFILE = "profile"
    RULE_REFERENCE = "rule_reference"
    OTHER = "other"


class SupportType(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    COUNTER_EVIDENCE = "counter_evidence"
    AGGREGATE = "aggregate"


class SourceScope(str, Enum):
    CURRENT_TASK = "current_task"
    CROSS_TASK = "cross_task"


class SourceStatus(str, Enum):
    LIVE = "live"
    DEGRADED = "degraded"


class SourceFormat(str, Enum):
    AUDIT_RESULT = "audit_result"
    EVIDENCE_ITEMS = "evidence_items"
    EVIDENCE_CATALOG = "evidence_catalog"
    EXTERNAL_EVIDENCE_INDEX = "external_evidence_index"
    LEGACY_RISK_EVIDENCE = "legacy_risk_evidence"
    LEGACY_RISK_FRAME = "legacy_risk_frame"
    LEGACY_RISK_IMAGE = "legacy_risk_image"
    RULE_MATCH = "rule_match"
    SCORE_BREAKDOWN = "score_breakdown"
    UNKNOWN = "unknown"


class DomainWarning(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: DataQuality
    message: str
    task_id: str = ""
    audit_result_id: int | None = None
    evidence_id: str = ""
    source_json_path: str = ""


class EvidenceView(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    local_evidence_id: str
    local_evidence_aliases: tuple[str, ...] = ()
    audit_result_id: int
    task_id: str
    content_id: int | None = None
    evidence_type: EvidenceType
    original_text: str = ""
    translated_text: str = ""
    summary: str = ""
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    asset_path: str = ""
    source_json_path: str
    source_json_paths: tuple[str, ...] = ()
    support_type: SupportType = SupportType.DIRECT
    source_format: SourceFormat
    source_formats: tuple[SourceFormat, ...] = ()
    source_hash: str
    data_quality: tuple[DataQuality, ...] = (DataQuality.COMPLETE,)


class FindingView(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    finding_kind: str = "post_audit_result"
    finding_schema_version: int = 1
    audit_result_id: int
    task_id: str
    content_id: int | None = None
    content_key: str
    source_platform: str = ""
    content_title: str = ""
    content_url: str = ""
    author: str = ""
    analyzed_at: str = ""
    created_at: str = ""
    audit_config_revision_id: str = ""
    decision: str
    risk_level: str
    risk_score: float | None = None
    primary_risk: str = ""
    categories: tuple[str, ...] = ()
    matched_rule_ids: tuple[str, ...] = ()
    summary: str = ""
    evidence_ids: tuple[str, ...] = ()
    evidence_type_counts: dict[str, int] = Field(default_factory=dict)
    source_hash: str
    data_quality: tuple[DataQuality, ...] = (DataQuality.COMPLETE,)


class SourceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    audit_result_id: int
    finding_id: str
    content_id: int | None = None
    evidence_ids: tuple[str, ...] = ()
    source_scope: SourceScope = SourceScope.CURRENT_TASK
    support_type: SupportType = SupportType.DIRECT
    source_status: SourceStatus = SourceStatus.LIVE
    source_format: SourceFormat = SourceFormat.AUDIT_RESULT
    source_formats: tuple[SourceFormat, ...] = ()
    source_hash: str


T = TypeVar("T")


class SourceEnvelope(BaseModel, Generic[T]):
    data: T
    sources: tuple[SourceRecord, ...] = ()
    warnings: tuple[DomainWarning, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class FindingFilters(BaseModel):
    """Finding predicates are ANDed; values inside evidence_types are ORed."""

    decision: str = ""
    risk_level: str = ""
    primary_risk: str = ""
    category: str = ""
    author: str = ""
    content_id: int | None = None
    has_evidence: bool | None = None
    data_quality: tuple[DataQuality, ...] = ()
    rule_id: str = ""
    evidence_types: tuple[EvidenceType, ...] = ()
    source_platform: str = ""
    risk_score_min: float | None = None
    risk_score_max: float | None = None
    # Stage 0 compatibility. Query code canonicalizes this into risk_score_min.
    min_risk_score: float | None = None


class FindingPage(BaseModel):
    items: tuple[FindingView, ...]
    total: int
    offset: int
    limit: int


class FindingEvidenceLinkValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    evidence_id: str
    valid: bool
    reason: str
    same_audit_result: bool = False
    same_task: bool = False
    finding_exists: bool = False
    evidence_exists: bool = False
    support_type: SupportType | None = None
    data_quality: tuple[DataQuality, ...] = (DataQuality.COMPLETE,)
