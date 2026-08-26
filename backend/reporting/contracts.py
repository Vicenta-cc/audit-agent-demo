from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ReportVersionStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    FAILED = "failed"


class ReportRunStatus(str, Enum):
    RUNNING = "running"
    RECOVERABLE = "recoverable"
    COMPLETED = "completed"
    FAILED = "failed"


class ClaimType(str, Enum):
    DOMAIN_FACT = "domain_fact"
    NUMERIC = "numeric"
    SYNTHESIS = "synthesis"
    METHODOLOGY = "methodology"


class SectionKind(str, Enum):
    OVERVIEW = "overview"
    RISK_ANALYSIS = "risk_analysis"
    CASE_ANALYSIS = "case_analysis"
    SYNTHESIS = "synthesis"
    CONCLUSION = "conclusion"


class OutlineSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str
    section_kind: SectionKind
    title: str
    purpose: str
    finding_ids: tuple[str, ...] = ()
    metric_refs: tuple[str, ...] = ()
    is_report_category: bool = False


class OutlinePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_title: str
    executive_summary_focus: str
    sections: tuple[OutlineSection, ...] = Field(min_length=4, max_length=6)


class ClaimDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str
    claim_type: ClaimType
    text: str
    finding_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    metric_refs: tuple[str, ...] = ()
    support_type: Literal["direct", "indirect", "counter_evidence", "aggregate"] = "direct"


class CaseBlockDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    text: str
    claim_ids: tuple[str, ...] = Field(min_length=1)


class SectionParagraphDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    claim_ids: tuple[str, ...] = Field(min_length=1)


class SectionDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str
    title: str
    paragraphs: tuple[SectionParagraphDraft, ...] = Field(min_length=1)
    claims: tuple[ClaimDraft, ...] = Field(min_length=1)
    case_blocks: tuple[CaseBlockDraft, ...] = ()
    is_report_category: bool = False


class HumanReportTextBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    claim_ids: tuple[str, ...] = ()


class HumanReportKeyMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    value: str
    detail: str = ""
    metric_refs: tuple[str, ...] = ()


class HumanReportCitationAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["evidence_drawer"] = "evidence_drawer"
    label: str = "查看相关证据"
    claim_id: str


class HumanReportCaseBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    text: str
    claim_ids: tuple[str, ...]
    citation_actions: tuple[HumanReportCitationAction, ...] = ()


class HumanReportSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str
    title: str
    paragraphs: tuple[HumanReportTextBlock, ...]


class HumanReportDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    presentation_version: str = "human-report-v1"
    title: str
    summary: HumanReportTextBlock
    key_metrics: tuple[HumanReportKeyMetric, ...]
    sections: tuple[HumanReportSection, ...]
    case_blocks: tuple[HumanReportCaseBlock, ...]
    conclusion: HumanReportTextBlock
    data_quality_note: HumanReportTextBlock


class ReportMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric_key: str
    metric_name: str
    label: str
    value: float
    denominator: int
    denominator_name: str
    group: dict[str, str] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    source_hash: str
    validation_kind: str
    validation_request: dict[str, Any]
    percentage_basis: str = ""


class FrozenSourceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    report_version_id: str
    task_id: str
    task_status: str
    source_hash: str
    configuration_revision_id: str
    finding_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    data_quality_warnings: tuple[dict[str, Any], ...]
    statistic_inputs: tuple[dict[str, Any], ...]
    generated_at: str
    snapshot_hash: str
    display_name: str = ""
    source_revision: str = ""
    relation_hash: str = ""
    statistics: dict[str, Any] = Field(default_factory=dict)


class ReportGenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    report_id: str
    report_version_id: str
    version_number: int
    task_id: str
    status: str
    title: str
    content_hash: str
    body_markdown: str
    warnings: tuple[dict[str, Any], ...] = ()


class ModelStepResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    output: dict[str, Any]
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)
    reused: bool = False


class ReportGraphState(TypedDict, total=False):
    run_id: str
    report_id: str
    report_version_id: str
    version_number: int
    task_id: str
    source_snapshot: dict[str, Any]
    statistics: dict[str, Any]
    selected_finding_ids: list[str]
    selected_finding_cards: list[dict[str, Any]]
    outline: dict[str, Any]
    section_drafts: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    number_validation_errors: list[str]
    claim_validation_errors: list[str]
    citation_validation_errors: list[str]
    citation_details: dict[str, dict[str, Any]]
    assembled_report: dict[str, Any]
    current_node: str
    retry_count: int
    warnings: list[dict[str, Any]]
    prompt_version: str
    model: str
    risk_inputs: list[dict[str, Any]]
    risk_alias_registry: dict[str, Any]
    investigation_findings: list[dict[str, Any]]
    standalone_risk_posts: list[dict[str, Any]]
    risk_post_coverage_complete: bool
    r2_configuration: dict[str, Any]
    report_account_projection: dict[str, Any]
