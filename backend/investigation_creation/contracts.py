from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Annotated, Any, Literal

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
    WEIBO = "wb"


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


InvestigationBlockerCode = Literal[
    "NO_PUBLISHED_AUDIT_POLICY",
    "NO_PUBLISHED_RECALL_LEXICON",
    "INVALID_RULESET_REFERENCE",
    "RESOURCE_STALE",
    "NO_SEARCH_TERMS",
    "PLATFORM_MISMATCH",
    "INVALID_CREATOR_URL",
    "CONFIRMATION_REQUIRED",
    "IDEMPOTENCY_CONFLICT",
]


class ExistingLexiconRecallPlan(StrictModel):
    strategy: Literal["existing_lexicon"]
    lexicon_id: StrictStr = Field(min_length=1, max_length=160)
    expected_runtime_content_hash: StrictStr = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    enabled_main_terms: list[StrictStr] = Field(default_factory=list, max_length=100)

    @field_validator("lexicon_id", "expected_runtime_content_hash", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("enabled_main_terms", mode="before")
    @classmethod
    def normalize_enabled_main_terms(cls, values: object) -> object:
        return TemporaryTermsRecallPlan.normalize_terms(values)


class TemporaryTermsRecallPlan(StrictModel):
    strategy: Literal["temporary_terms"]
    terms: list[StrictStr] = Field(default_factory=list, max_length=100)
    source_lexicon_ids: list[StrictStr] = Field(default_factory=list, max_length=20)

    @field_validator("terms", "source_lexicon_ids", mode="before")
    @classmethod
    def normalize_terms(cls, values: object) -> object:
        if not isinstance(values, (list, tuple)):
            return values
        seen: set[str] = set()
        normalized: list[object] = []
        for value in values:
            if not isinstance(value, str):
                normalized.append(value)
                continue
            text = value.strip()
            if text and text not in seen:
                seen.add(text)
                normalized.append(text)
        return normalized


RecallPlan = Annotated[
    ExistingLexiconRecallPlan | TemporaryTermsRecallPlan,
    Field(discriminator="strategy"),
]


class SearchInvestigationMode(StrictModel):
    mode: Literal["search"]
    recall_plan: RecallPlan


class CreatorInvestigationMode(StrictModel):
    mode: Literal["creator"]
    creator_url: StrictStr = Field(max_length=2_000)

    @field_validator("creator_url", mode="before")
    @classmethod
    def strip_creator_url(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


InvestigationMode = Annotated[
    SearchInvestigationMode | CreatorInvestigationMode,
    Field(discriminator="mode"),
]


class AuditPolicySelection(StrictModel):
    id: StrictStr = Field(min_length=1, max_length=160)
    expected_published_version: StrictStr = Field(min_length=1, max_length=80)
    expected_published_config_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    expected_ruleset_revision_id: StrictStr = Field(min_length=1, max_length=240)
    expected_ruleset_version: StrictInt = Field(ge=1)
    expected_ruleset_content_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "id",
        "expected_published_version",
        "expected_published_config_hash",
        "expected_ruleset_revision_id",
        "expected_ruleset_content_hash",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class InvestigationDraftConfiguration(StrictModel):
    schema_version: Literal["investigation-draft-config-v3"] = (
        "investigation-draft-config-v3"
    )
    platform: Platform
    investigation: InvestigationMode
    audit_policy: AuditPolicySelection | None = None


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


DraftConfiguration = InvestigationDraftConfiguration | InvestigationConfiguration


class InvestigationBlocker(StrictModel):
    code: InvestigationBlockerCode
    message: StrictStr
    resource_type: StrictStr = ""
    resource_id: StrictStr = ""
    latest_safe_summary: dict[str, Any] = Field(default_factory=dict)
    management_url: StrictStr = ""


class PlatformOption(StrictModel):
    id: Platform
    name: StrictStr
    available: StrictBool = True


class RuleSetRevisionSummary(StrictModel):
    id: StrictStr
    ruleset_id: StrictStr
    name: StrictStr
    domain: StrictStr
    version: StrictInt = Field(ge=1)
    content_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    enabled_rule_count: StrictInt = Field(ge=0)
    available: StrictBool = True


class AuditPolicySummary(StrictModel):
    id: StrictStr
    name: StrictStr
    description: StrictStr = ""
    published_version: StrictStr
    published_config_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    ruleset_revision_id: StrictStr
    ruleset_version: StrictInt = Field(ge=1)
    ruleset_content_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    domain: StrictStr
    available: StrictBool = True


class RecallLexiconSummary(StrictModel):
    id: StrictStr
    title: StrictStr = ""
    risk_label: StrictStr = ""
    enabled_main_term_count: StrictInt = Field(ge=0)
    runtime_content_hash: StrictStr = ""
    enabled_main_terms: list[StrictStr] = Field(default_factory=list)
    enabled_main_terms_returned: StrictInt = Field(default=0, ge=0)
    terms_included: StrictBool = False
    terms_truncated: StrictBool = False
    available: StrictBool = True


class QueryInvestigationOptions(StrictModel):
    domain_hint: StrictStr = Field(default="", max_length=200)
    mode: Literal["search", "creator"] = "search"
    platform: Platform | None = None
    audit_policy_ids: list[StrictStr] = Field(default_factory=list, max_length=20)
    lexicon_ids: list[StrictStr] = Field(default_factory=list, max_length=20)
    include_lexicon_terms_for_ids: list[StrictStr] = Field(
        default_factory=list, max_length=10
    )
    page_size: StrictInt = Field(default=20, ge=1, le=50)
    lexicon_term_limit: StrictInt = Field(default=50, ge=1, le=100)
    cursor: StrictStr = Field(default="", max_length=40)

    @field_validator(
        "audit_policy_ids",
        "lexicon_ids",
        "include_lexicon_terms_for_ids",
        mode="before",
    )
    @classmethod
    def normalize_ids(cls, values: object) -> object:
        if not isinstance(values, (list, tuple)):
            return values
        seen: set[str] = set()
        normalized: list[object] = []
        for value in values:
            if not isinstance(value, str):
                normalized.append(value)
                continue
            text = value.strip()
            if text and text not in seen:
                seen.add(text)
                normalized.append(text)
        return normalized

    @field_validator("domain_hint", "cursor", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class InvestigationOptions(StrictModel):
    platforms: list[PlatformOption]
    audit_policies: list[AuditPolicySummary]
    ruleset_revisions: list[RuleSetRevisionSummary]
    recall_lexicons: list[RecallLexiconSummary]
    blockers: list[InvestigationBlocker] = Field(default_factory=list)
    next_cursor: StrictStr = ""
    management_url: StrictStr = "/rule-assistant/rulesets?return_to=/investigation"


class RecallPlanPreview(StrictModel):
    strategy: Literal["existing_lexicon", "temporary_terms", "none"]
    lexicon_id: StrictStr = ""
    lexicon_title: StrictStr = ""
    runtime_content_hash: StrictStr = ""
    enabled_main_term_count: StrictInt = Field(default=0, ge=0)
    enabled_main_terms: list[StrictStr] = Field(default_factory=list)
    temporary_terms: list[StrictStr] = Field(default_factory=list)
    source_lexicon_ids: list[StrictStr] = Field(default_factory=list)


class ConfirmationPreview(StrictModel):
    draft_id: StrictStr
    draft_revision: StrictInt = Field(ge=1)
    title: StrictStr
    objective: StrictStr
    mode: Literal["search", "creator"]
    platform: Platform
    resolved_search_terms: list[StrictStr] = Field(default_factory=list)
    creator_url: StrictStr = ""
    recall_plan: RecallPlanPreview
    audit_policy: AuditPolicySummary | None = None
    ruleset_revision: RuleSetRevisionSummary | None = None
    max_notes: Literal[1] = 1
    blockers: list[InvestigationBlocker] = Field(default_factory=list)
    can_confirm: StrictBool


class InvestigationDraftView(StrictModel):
    draft: "InvestigationDraft"
    confirmation_preview: ConfirmationPreview


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


class ConfirmedRecallPlanSnapshot(StrictModel):
    strategy: Literal["existing_lexicon", "temporary_terms"]
    lexicon_id: StrictStr = ""
    runtime_content_hash: StrictStr = ""
    enabled_main_terms: list[StrictStr] = Field(default_factory=list)
    temporary_terms: list[StrictStr] = Field(default_factory=list)
    source_lexicon_ids: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_strategy(self) -> "ConfirmedRecallPlanSnapshot":
        if self.strategy == "existing_lexicon":
            if not self.lexicon_id or not self.runtime_content_hash:
                raise ValueError("existing_lexicon snapshot requires id and hash")
            if not self.enabled_main_terms:
                raise ValueError("existing_lexicon snapshot requires enabled main terms")
            if self.temporary_terms:
                raise ValueError("existing_lexicon snapshot cannot contain temporary terms")
        else:
            if self.lexicon_id or self.runtime_content_hash or self.enabled_main_terms:
                raise ValueError("temporary_terms snapshot cannot contain a lexicon snapshot")
            if not self.temporary_terms:
                raise ValueError("temporary_terms snapshot requires confirmed terms")
        return self


class ConfirmationResolution(StrictModel):
    mode: Literal["search", "creator"]
    platform: Platform
    resolved_search_terms: list[StrictStr] = Field(default_factory=list)
    creator_url: StrictStr = ""
    recall_plan: ConfirmedRecallPlanSnapshot | None = None
    audit_policy: AuditPolicySummary
    ruleset_revision: RuleSetRevisionSummary
    execution: ResolvedExecutionConfiguration
    config_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_resolution(self) -> "ConfirmationResolution":
        if self.mode == "search":
            if not self.resolved_search_terms or self.creator_url or self.recall_plan is None:
                raise ValueError("search resolution requires terms and a recall plan")
        elif self.resolved_search_terms or self.recall_plan is not None or not self.creator_url:
            raise ValueError("creator resolution requires only a creator URL")
        expected = confirmed_configuration_hash(
            {
                "mode": self.mode,
                "platform": self.platform.value,
                "resolved_search_terms": self.resolved_search_terms,
                "creator_url": self.creator_url,
                "recall_plan": (
                    self.recall_plan.model_dump(mode="json")
                    if self.recall_plan is not None
                    else None
                ),
                "audit_policy": self.audit_policy.model_dump(mode="json"),
                "ruleset_revision": self.ruleset_revision.model_dump(mode="json"),
                "execution": self.execution.model_dump(mode="json"),
            }
        )
        if self.config_hash != expected:
            raise ValueError("confirmation resolution config_hash does not match")
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


class ConfirmedConfigurationSnapshotV3(StrictModel):
    schema_version: Literal["investigation-run-config-v3"]
    draft_id: StrictStr
    draft_revision: StrictInt = Field(ge=1)
    title: StrictStr
    objective: StrictStr
    mode: Literal["search", "creator"]
    platform: Platform
    resolved_search_terms: list[StrictStr] = Field(default_factory=list)
    creator_url: StrictStr = ""
    recall_plan: ConfirmedRecallPlanSnapshot | None = None
    audit_policy: AuditPolicySummary
    ruleset_revision: RuleSetRevisionSummary
    max_notes: Literal[1]
    execution: ResolvedExecutionConfiguration
    config_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_by: StrictStr
    confirmed_at: StrictStr

    @model_validator(mode="after")
    def validate_snapshot(self) -> "ConfirmedConfigurationSnapshotV3":
        ConfirmationResolution.model_validate(
            {
                key: value
                for key, value in self.model_dump(mode="json").items()
                if key
                in {
                    "mode",
                    "platform",
                    "resolved_search_terms",
                    "creator_url",
                    "recall_plan",
                    "audit_policy",
                    "ruleset_revision",
                    "execution",
                    "config_hash",
                }
            }
        )
        if not all(
            value.strip()
            for value in (
                self.draft_id,
                self.title,
                self.objective,
                self.confirmed_by,
                self.confirmed_at,
            )
        ):
            raise ValueError("confirmed snapshot identity fields must not be blank")
        if self.execution.max_notes != 1:
            raise ValueError("confirmed execution max_notes must be 1")
        return self


def confirmed_configuration_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def parse_confirmed_configuration_snapshot(
    value: dict[str, Any],
) -> ConfirmedConfigurationSnapshot | ConfirmedConfigurationSnapshotV3:
    if value.get("schema_version") == "investigation-run-config-v3":
        return ConfirmedConfigurationSnapshotV3.model_validate(value)
    return ConfirmedConfigurationSnapshot.model_validate(value)


class CreateDraftCommand(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=4_000)
    configuration: DraftConfiguration

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UpdateDraftCommand(StrictModel):
    draft_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, min_length=1, max_length=4_000)
    configuration: DraftConfiguration | None = None

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
    configuration: DraftConfiguration
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
