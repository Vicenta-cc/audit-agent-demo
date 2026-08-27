from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from backend.audit_agent.creator_url import validate_creator_url


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DraftStatus(str, Enum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    REPORT_GENERATING = "REPORT_GENERATING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class Platform(str, Enum):
    XHS = "xhs"
    DOUYIN = "dy"
    KUAISHOU = "ks"


class CrawlMode(str, Enum):
    SEARCH = "search"
    CREATOR = "creator"


class KeywordSource(str, Enum):
    KEYWORD = "keyword"
    LEXICON = "lexicon"


class ScoringTemplate(str, Enum):
    BALANCED = "balanced"
    STRICT = "strict"
    OCR_FIRST = "ocr_first"
    VISION_FIRST = "vision_first"


class AnalysisCapability(str, Enum):
    TEXT = "text"
    OCR = "ocr"
    ASR = "asr"
    VISION = "vision"
    COMMENT = "comment"


class CollectionConfiguration(StrictModel):
    crawl_mode: CrawlMode = CrawlMode.SEARCH
    keyword_source: KeywordSource = KeywordSource.KEYWORD
    keywords: list[StrictStr] = Field(default_factory=list)
    creator_url: StrictStr = ""
    display_name: StrictStr = ""
    start_page: StrictInt = Field(default=1, ge=1)
    max_notes: StrictInt = Field(default=10_000, ge=1)
    max_comments: StrictInt = Field(default=1_000, ge=0)
    max_concurrency: StrictInt = Field(default=1, ge=1)
    max_items_per_minute: StrictInt = Field(default=5, ge=1, le=5)
    crawler_account_id: StrictStr | None = None
    get_sub_comment: StrictBool = False
    run_crawler: StrictBool = True
    source_output_id: StrictStr | None = None

    @field_validator(
        "creator_url",
        "display_name",
        "crawler_account_id",
        "source_output_id",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("keywords must not contain blank values")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("keywords must not contain duplicates")
        return cleaned

    @model_validator(mode="after")
    def validate_mode(self) -> "CollectionConfiguration":
        if not self.run_crawler and not self.source_output_id:
            raise ValueError(
                "source_output_id is required when run_crawler is false"
            )
        if self.crawl_mode == CrawlMode.SEARCH:
            if self.creator_url:
                raise ValueError("creator_url is not allowed in search mode")
            if self.keyword_source == KeywordSource.KEYWORD and not self.keywords:
                raise ValueError("search keywords are required")
            if self.keyword_source == KeywordSource.LEXICON and self.keywords:
                raise ValueError(
                    "keywords must be empty when keyword_source is lexicon"
                )
        else:
            if not self.creator_url:
                raise ValueError("creator_url is required in creator mode")
            if self.keywords:
                raise ValueError("keywords are not allowed in creator mode")
            if self.keyword_source != KeywordSource.KEYWORD:
                raise ValueError("creator mode requires keyword_source=keyword")
        return self


class AnalysisConfiguration(StrictModel):
    policy_id: StrictStr = ""
    library_ids: list[StrictStr] = Field(default_factory=lambda: ["soft"])
    capabilities: list[AnalysisCapability] = Field(default_factory=list)
    scoring_template: ScoringTemplate = ScoringTemplate.BALANCED
    analyze_limit: StrictInt = Field(default=10_000, ge=0)
    analysis_batch_size: StrictInt = Field(default=5, ge=1)

    @field_validator("policy_id")
    @classmethod
    def strip_policy_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("library_ids")
    @classmethod
    def normalize_string_list(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("configuration lists must not contain blank values")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("configuration lists must not contain duplicates")
        return cleaned

    @field_validator("capabilities")
    @classmethod
    def require_unique_capabilities(
        cls, values: list[AnalysisCapability]
    ) -> list[AnalysisCapability]:
        if len(values) != len(set(values)):
            raise ValueError("capabilities must not contain duplicates")
        return values


class InvestigationConfiguration(StrictModel):
    platform: Platform
    collection: CollectionConfiguration
    analysis: AnalysisConfiguration = Field(default_factory=AnalysisConfiguration)

    @model_validator(mode="after")
    def validate_creator_scope(self) -> "InvestigationConfiguration":
        if self.collection.crawl_mode == CrawlMode.CREATOR:
            validate_creator_url(self.platform.value, self.collection.creator_url)
        return self


class AuditConfigRevisionSnapshot(StrictModel):
    source_policy_id: StrictStr = ""
    source_policy_name: StrictStr
    source_policy_version: StrictStr = ""
    audit_config: dict[str, Any]
    knowledge_package_snapshots: list[dict[str, Any]]
    rule_snapshot: dict[str, Any]
    prompt_profile_snapshot: dict[str, Any]
    config_hash: StrictStr


class ResolvedExecutionConfiguration(StrictModel):
    platform: Platform
    display_name: StrictStr = ""
    crawl_mode: CrawlMode
    keyword: StrictStr = ""
    keyword_source: KeywordSource
    lexicon_category: StrictStr
    library_ids: list[StrictStr]
    capabilities: list[StrictStr]
    scoring_template: ScoringTemplate
    rule_snapshot: dict[str, Any]
    lexicon_keywords: list[StrictStr]
    creator_url: StrictStr = ""
    creator_id: StrictStr = ""
    start_page: StrictInt = Field(ge=1)
    max_notes: StrictInt = Field(ge=1)
    max_comments: StrictInt = Field(ge=0)
    max_concurrency: StrictInt = Field(ge=1)
    max_items_per_minute: StrictInt = Field(ge=1, le=5)
    crawler_account_id: StrictStr | None = None
    crawler_account_display_name: StrictStr = ""
    get_sub_comment: StrictBool
    analyze_limit: StrictInt = Field(ge=0)
    run_crawler: StrictBool
    source_output_id: StrictStr | None = None
    analysis_batch_size: StrictInt = Field(ge=1)
    prompt_profile_snapshot: dict[str, Any]
    policy_id: StrictStr = ""
    audit_config_revision: AuditConfigRevisionSnapshot

    @model_validator(mode="after")
    def validate_execution_mode(self) -> "ResolvedExecutionConfiguration":
        if self.crawl_mode == CrawlMode.CREATOR:
            validated = validate_creator_url(self.platform.value, self.creator_url)
            if self.creator_id != validated:
                raise ValueError("creator_id must match the validated creator_url")
        elif self.creator_url or self.creator_id:
            raise ValueError("creator_url is not allowed in search mode")
        return self


class ConfirmedConfigurationSnapshot(StrictModel):
    schema_version: Literal["investigation-run-config-v2"]
    draft_id: StrictStr
    draft_revision: StrictInt = Field(ge=1)
    title: StrictStr
    objective: StrictStr
    draft_configuration: InvestigationConfiguration
    execution: ResolvedExecutionConfiguration

    @field_validator("draft_id", "title", "objective", mode="before")
    @classmethod
    def strip_required_snapshot_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_snapshot_text(self) -> "ConfirmedConfigurationSnapshot":
        if not self.draft_id:
            raise ValueError("draft_id must not be blank")
        if not self.title:
            raise ValueError("title must not be blank")
        if not self.objective:
            raise ValueError("objective must not be blank")
        return self


class CreateDraftCommand(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=4_000)
    configuration: InvestigationConfiguration

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UpdateDraftCommand(StrictModel):
    draft_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, min_length=1, max_length=4_000)
    configuration: InvestigationConfiguration | None = None

    @field_validator("draft_id", "title", "objective", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_change(self) -> "UpdateDraftCommand":
        if self.title is None and self.objective is None and self.configuration is None:
            raise ValueError("at least one draft field must be updated")
        return self


class ConfirmAndQueueCommand(StrictModel):
    draft_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    confirmed: StrictBool
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("draft_id", "idempotency_key", mode="before")
    @classmethod
    def strip_confirmation_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class InvestigationDraft(StrictModel):
    id: str
    owner_principal: str = Field(exclude=True)
    status: DraftStatus
    current_revision: int = Field(ge=1)
    title: str
    objective: str
    configuration: InvestigationConfiguration
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str
    confirmed_revision: int | None = None
    confirmed_by: str = ""
    confirmed_at: str = ""


class InvestigationRun(StrictModel):
    id: str
    owner_principal: str = Field(exclude=True)
    draft_id: str
    draft_revision: int = Field(ge=1)
    idempotency_key: str
    status: RunStatus
    confirmed_configuration: dict[str, Any]
    confirmed_by: str
    confirmed_at: str
    job_id: str = ""
    pipeline_started_at: str = ""
    pipeline_returned_at: str = ""
    report_version_id: str = ""
    report_session_id: str = ""
    error_code: str = ""
    error_message: str = ""
    claimed_by: str = ""
    claim_token: str = ""
    claimed_at: str = ""
    heartbeat_at: str = ""
    recovery_required: bool = False
    created_at: str
    updated_at: str
    started_at: str = ""
    completed_at: str = ""


class ReportGenerationBinding(StrictModel):
    run_id: str
    generation_key: str
    r31_run_id: str = ""
    report_version_id: str = ""
    state: Literal["RESERVED", "STARTED", "PUBLISHED"]
    created_at: str
    updated_at: str


class InvestigationRunProjection(StrictModel):
    run_id: str
    draft_id: str
    draft_revision: int
    status: RunStatus
    job_id: str = ""
    crawl_status: str
    analysis_status: str
    task_stats: dict[str, Any]
    report_status: str
    report_version_id: str = ""
    report_session_id: str = ""
    error_code: str = ""
    error_message: str = ""
    created_at: str
    updated_at: str
    started_at: str = ""
    completed_at: str = ""
