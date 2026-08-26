from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.contracts import (
    EvidenceType,
    FindingView,
    SupportType,
)
from backend.domain.warnings import DataQuality


class AccessContext(BaseModel):
    """Stage 1 is single-tenant; allowed_task_ids reserves the future RBAC boundary."""

    model_config = ConfigDict(frozen=True)

    principal_id: str = "internal"
    tenant_id: str = "single_tenant"
    allowed_task_ids: tuple[str, ...] = ()


class TaskSnapshotFindingInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    audit_result_id: int
    task_id: str
    content_id: int | None = None
    content_key: str
    decision: str
    risk_level: str
    risk_score: float | None = None
    primary_risk: str = ""
    categories: tuple[str, ...] = ()
    matched_rule_ids: tuple[str, ...] = ()
    audit_config_revision_id: str = ""
    evidence_ids: tuple[str, ...] = ()
    evidence_type_counts: dict[str, int] = Field(default_factory=dict)
    source_hash: str
    data_quality: tuple[DataQuality, ...] = (DataQuality.COMPLETE,)


class TaskSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    task_name: str
    task_status: str
    source_platform: str = ""
    created_at: str = ""
    completed_at: str = ""
    completed_at_basis: str = ""
    content_count: int
    audit_result_count: int
    decision_distribution: dict[str, int]
    risk_level_distribution: dict[str, int]
    category_distribution: dict[str, int]
    finding_ids: tuple[str, ...]
    configuration_revision_id: str = ""
    data_quality_summary: dict[str, int]
    statistic_inputs: tuple[TaskSnapshotFindingInput, ...]
    source_hash: str
    snapshot_kind: str = "recalculable_domain_query_snapshot"


class TaskOverview(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    task_name: str
    task_status: str
    source_platform: str = ""
    audit_result_count: int
    pass_count: int
    review_count: int
    reject_count: int
    risk_level_distribution: dict[str, int]
    primary_risk_distribution: dict[str, int]
    findings_with_evidence_count: int
    data_quality_summary: dict[str, int]
    source_hash: str
    derived_from: str = "task_snapshot"


class FindingCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    audit_result_id: int
    task_id: str
    content_id: int | None = None
    content_title: str = ""
    author: str = ""
    source_platform: str = ""
    decision: str
    risk_level: str
    risk_score: float | None = None
    primary_risk: str = ""
    summary: str = ""
    evidence_count: int
    evidence_type_summary: dict[str, int]
    data_quality: tuple[DataQuality, ...]
    source_hash: str


class FindingSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[FindingCard, ...]
    total: int
    page: int
    page_size: int
    applied_filters: dict[str, Any]
    filter_combination: str = "AND"
    evidence_types_combination: str = "OR"
    source_hash: str


class AggregationValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric_key: str
    metric: str
    value: float
    denominator: int
    denominator_name: str
    group: dict[str, str]
    filters: dict[str, Any]
    task_id: str
    source_hash: str
    percentage_basis: str = ""


class AggregationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    group_by: tuple[str, ...]
    metrics: tuple[str, ...]
    rows: tuple[AggregationValue, ...]
    filtered_finding_count: int
    filtered_evidence_count: int
    filters: dict[str, Any]
    source_hash: str


class PostInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_id: int | None = None
    content_key: str
    platform: str = ""
    note_id: str = ""
    title: str = ""
    url: str = ""
    author_key: str = ""
    author: dict[str, Any] = Field(default_factory=dict)
    analyzed_at: str = ""


class FindingDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding: FindingView
    post: PostInfo
    score_breakdown: tuple[dict[str, Any], ...] = ()
    rule_matches: tuple[dict[str, Any], ...] = ()
    exclusion_basis: tuple[str, ...] = ()
    risk_basis: str = ""
    review_status: str = ""
    review_note: str = ""


class EvidenceBrief(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    evidence_type: EvidenceType
    summary: str = ""
    original_text_preview: str = ""
    translated_text_preview: str = ""
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    asset_available: bool
    support_type: SupportType
    data_quality: tuple[DataQuality, ...]
    source_hash: str


class EvidenceContextOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    comment_neighbors: int = Field(default=0, ge=0, le=20)
    time_window_seconds: float = Field(default=0, ge=0, le=600)
    text_characters: int = Field(default=0, ge=0, le=4000)
    related_resource_limit: int = Field(default=0, ge=0, le=20)


class EvidenceContextItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_kind: str
    source_id: str = ""
    original_text: str = ""
    translated_text: str = ""
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    asset_path: str = ""


class EvidenceAvailability(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class EvidenceDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    finding_id: str
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
    asset_available: bool
    structured_content_available: bool
    availability: EvidenceAvailability
    context_semantics: str
    context: tuple[EvidenceContextItem, ...] = ()
    support_type: SupportType
    source_format: str
    source_hash: str
    data_quality: tuple[DataQuality, ...]
