import hashlib
import json
import os
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, StrictInt

from .api.investigation import create_investigation_router
from .api.historical_reports import create_historical_report_router
from .api.investigation_conversation import create_investigation_conversation_router
from .api.investigation_creation import create_investigation_creation_router
from .api.investigation_execution import InvestigationTurnExecutor
from .api.reporting import create_reporting_router
from .rulesets.api import create_ruleset_router
from .rulesets.errors import RuleSetRevisionNotFoundError
from .rulesets.service import RuleSetService
from .rulesets.store import RuleSetStore
from .audit_agent.audit_policy_store import (
    AuditPolicyLibraryReferenceConflictError,
    AuditPolicyRevisionConflictError,
    AuditPolicyStore,
    TaskAuditConfigRevisionStore,
)
from .audit_agent.config import settings
from .audit_agent.crawler_account_store import account_is_cooling_down, crawler_account_store
from .audit_agent.creator_url import (
    CreatorUrlValidationError,
    validate_creator_url as validate_creator_url_contract,
)
from .audit_agent.crawler_login_manager import crawler_account_login_manager
from .audit_agent.runtime_boundary import runtime_data_directory_error
from .audit_agent.crawler_adapter import SUPPORTED_PLATFORMS, MediaCrawlerAdapter
from .audit_agent.evidence_groups import build_evidence_groups
from .audit_agent.ingestion import AuditResultStore, IngestionStore
from .audit_agent.job_store import job_store
from .audit_agent.job_state import (
    RECOVERABLE_CRAWL_FAILURE_CODES,
    available_job_actions,
    failure_from_job,
)
from .audit_agent.lexicon_store import (
    LexiconCategoryReferenceConflictError,
    LexiconStore,
)
from .audit_agent.pipeline import AuditPipeline, AUDIO_FILE_SIGNATURES, AUDIO_URL_EXTENSIONS
from .audit_agent.rule_compiler import (
    DEFAULT_CAPABILITIES,
    DEFAULT_THRESHOLDS,
    IMPORTANCE_SCORES,
    TEMPLATE_IMPORTANCE,
    compile_rule_profile,
    normalize_capabilities,
    normalize_library_ids,
)
from .investigation_creation.contracts import RunStatus
from .reporting.store import ReportStore
from .reporting.runtime import R31ReportRuntime
from .hermes_runtime.service import HermesInvestigationAgentService
from .hermes_runtime.turn_process import TurnProcessRunner
from .historical_reports import (
    HISTORICAL_REPORT_SPECS,
    HistoricalReportDemoService,
    HistoricalReportWorkspaceStore,
)
from .investigation_creation.adapters import (
    InvestigationConfigurationResolver,
    InvestigationRunProjector,
)
from .investigation_creation.service import InvestigationCreationService
from .investigation_creation.conversation import InvestigationCreationConversationService
from .investigation_creation.fake_runtime import (
    FakeInvestigationRunProjector,
    FakePublishedReportHermesAgent,
)
from .investigation_creation.resources import InvestigationResourceService
from .investigation_creation.tools import (
    InvestigationCreationToolService,
    configure_hermes_investigation_creation_tools,
)
from .investigation_creation.store import InvestigationCreationStore
from .investigation_creation.principal import LocalPrincipalProvider


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
FRONTEND_SAAS_DIR = ROOT / "frontend-saas"
FRONTEND_V2_DIST_DIR = ROOT / "frontend-v2" / "dist"

app = FastAPI(title="XHS Audit Agent Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
app.mount("/saas-static", StaticFiles(directory=str(FRONTEND_SAAS_DIR)), name="saas-static")
app.mount(
    "/saas-v2/assets",
    StaticFiles(directory=str(FRONTEND_V2_DIST_DIR / "assets"), check_dir=False),
    name="saas-v2-assets",
)
ingestion_store = IngestionStore()
audit_result_store = AuditResultStore()
lexicon_store = LexiconStore()
principal_provider = LocalPrincipalProvider()
ruleset_store = RuleSetStore()
ruleset_service = RuleSetService(ruleset_store)
audit_policy_store = AuditPolicyStore()
audit_config_revision_store = TaskAuditConfigRevisionStore()
report_store = ReportStore()
r31_report_runtime = R31ReportRuntime(report_store)
investigation_creation_store = InvestigationCreationStore()


def _authorized_m3_peer_report_versions(session, anchor: str) -> tuple[str, ...]:
    prefix = "m3-run:"
    if not str(anchor or "").startswith(prefix):
        return ()
    return investigation_creation_store.authorized_published_report_version_ids(
        run_id=str(anchor)[len(prefix):],
        current_report_version_id=session.report_version_id,
    )


investigation_agent_service = (
    HermesInvestigationAgentService(
        agent_factory=FakePublishedReportHermesAgent,
        bind_runtime=False,
        authorized_report_version_ids=tuple(
            item.report_version_id for item in HISTORICAL_REPORT_SPECS
            if item.workspace_id in {"historical-report-a", "historical-report-b", "historical-report-c"}
        ),
        authorized_context_anchor_prefixes=("historical-report:",),
        authorized_report_version_resolver=_authorized_m3_peer_report_versions,
    )
    if settings.hermes_creation_fake_runtime
    else HermesInvestigationAgentService(
        authorized_report_version_ids=tuple(
            item.report_version_id for item in HISTORICAL_REPORT_SPECS
            if item.workspace_id in {"historical-report-a", "historical-report-b", "historical-report-c"}
        ),
        authorized_context_anchor_prefixes=("historical-report:",),
        authorized_report_version_resolver=_authorized_m3_peer_report_versions,
    )
)
investigation_configuration_resolver = InvestigationConfigurationResolver(
    lexicon_store=lexicon_store,
    policy_store=audit_policy_store,
    crawler_account_store=crawler_account_store,
    ruleset_service=ruleset_service,
    principal_provider=principal_provider,
)
investigation_resource_service = InvestigationResourceService(
    lexicon_store=lexicon_store,
    ruleset_service=ruleset_service,
    configuration_resolver=investigation_configuration_resolver,
)
investigation_run_projector = (
    FakeInvestigationRunProjector(
        report_store=report_store,
        report_agent_service=investigation_agent_service,
        stage_seconds=settings.hermes_creation_fake_stage_seconds,
    )
    if settings.hermes_creation_fake_runtime
    else InvestigationRunProjector(
        job_store=job_store,
        ingestion_store=ingestion_store,
        report_store=report_store,
        crawler_account_store=crawler_account_store,
    )
)
investigation_creation_service = InvestigationCreationService(
    investigation_creation_store,
    configuration_resolver=investigation_configuration_resolver,
    resource_service=investigation_resource_service,
    run_projector=investigation_run_projector,
)
investigation_creation_tool_service = InvestigationCreationToolService(
    investigation_creation_service
)
investigation_creation_conversation_service = InvestigationCreationConversationService(
    tool_service=investigation_creation_tool_service,
    store=investigation_agent_service.store,
    fake_runtime=settings.hermes_creation_fake_runtime,
)
configure_hermes_investigation_creation_tools(
    investigation_creation_tool_service,
    principal_provider=investigation_creation_conversation_service.principal_for_session,
)
investigation_turn_executor = InvestigationTurnExecutor(
    investigation_agent_service,
    max_workers=settings.hermes_investigation_max_workers,
    process_runner=(None if settings.hermes_creation_fake_runtime else TurnProcessRunner(
        "report", timeout_seconds=settings.hermes_turn_timeout_seconds,
    )),
)
historical_report_workspace_store = HistoricalReportWorkspaceStore()
historical_report_demo_service = HistoricalReportDemoService(
    specs=HISTORICAL_REPORT_SPECS,
    workspace_store=historical_report_workspace_store,
    report_store=report_store,
    report_service=investigation_agent_service,
    executor=investigation_turn_executor,
)
from .investigation.deletion import WorkspaceDeletionService, create_workspace_deletion_router

workspace_deletion_service = WorkspaceDeletionService(
    conversations=investigation_creation_conversation_service,
    creation_store=investigation_creation_store,
    reports=report_store,
    historical=historical_report_demo_service,
    report_service=investigation_agent_service,
)
app.include_router(create_workspace_deletion_router(workspace_deletion_service, principal_provider))
investigation_creation_turn_executor = InvestigationTurnExecutor(
    investigation_creation_conversation_service,
    max_workers=settings.hermes_investigation_max_workers,
    process_runner=(None if settings.hermes_creation_fake_runtime else TurnProcessRunner(
        "creation", timeout_seconds=settings.hermes_turn_timeout_seconds,
    )),
)
app.include_router(
    create_reporting_router(
        report_store,
        r31_report_runtime,
        principal_provider=principal_provider,
        m3_run_store=investigation_creation_store,
        historical_report_service=historical_report_demo_service,
        outputs_dir=settings.outputs_dir,
    )
)
app.include_router(
    create_investigation_creation_router(
        investigation_creation_service,
        principal_provider=principal_provider,
    )
)
app.include_router(create_ruleset_router(ruleset_service, principal_provider=principal_provider))
from .resource_management.api import create_resource_router
app.include_router(create_resource_router(investigation_creation_service, investigation_creation_conversation_service, principal_provider))
app.include_router(
    create_historical_report_router(
        historical_report_demo_service,
        principal_provider=principal_provider,
    )
)
app.include_router(
    create_investigation_router(
        investigation_agent_service,
        investigation_turn_executor,
        report_store=report_store,
        m3_run_store=investigation_creation_store,
        historical_report_service=historical_report_demo_service,
    )
)
app.include_router(
    create_investigation_conversation_router(
        investigation_creation_conversation_service,
        investigation_creation_turn_executor,
        principal_provider=principal_provider,
        report_service=investigation_agent_service,
        report_executor=investigation_turn_executor,
        report_store=report_store,
    )
)


def _looks_like_audio_url(url: str) -> bool:
    clean = str(url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return any(clean.endswith(ext) for ext in AUDIO_URL_EXTENSIONS)


def _job_asset_path(item: dict, path_or_url: str | None) -> Path | None:
    value = str(path_or_url or "").strip()
    job_id = str(item.get("job_id") or "").strip()
    if not value or not job_id or value.startswith(("http://", "https://")):
        return None
    job_root = (settings.outputs_dir / job_id).resolve()
    marker = f"/outputs/{job_id}/"
    if marker in value:
        value = value.split(marker, 1)[1]
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = job_root / value
    target = target.resolve()
    try:
        target.relative_to(job_root)
    except ValueError:
        return None
    return target


def _is_probably_video_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(64)
    except OSError:
        return False
    if not header or header.startswith(AUDIO_FILE_SIGNATURES):
        return False
    if b"ftyp" in header[:16]:
        return True
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return True
    if header.startswith(b"RIFF") and b"AVI " in header[:16]:
        return True
    return False


def _is_valid_video_ref(item: dict, *refs: str | None) -> bool:
    saw_local_ref = False
    for ref in refs:
        value = str(ref or "").strip()
        if not value:
            continue
        if _looks_like_audio_url(value):
            return False
        asset_path = _job_asset_path(item, value)
        if asset_path:
            saw_local_ref = True
            if _is_probably_video_file(asset_path):
                return True
    return False if saw_local_ref else any(str(ref or "").startswith(("http://", "https://")) for ref in refs)


def _sanitize_audit_result_media(item: dict) -> dict:
    sanitized = dict(item)
    raw_index = sanitized.get("evidence_index")
    index = dict(raw_index) if isinstance(raw_index, dict) else {}
    if not index and not isinstance(sanitized.get("video_results"), list):
        return sanitized

    valid_video_units = []
    valid_video_sources: set[str] = set()
    for pos, unit in enumerate(index.get("video_units") or [], start=1):
        if not isinstance(unit, dict):
            continue
        if _is_valid_video_ref(
            sanitized,
            unit.get("asset_rel"),
            unit.get("local_path"),
            unit.get("path"),
            unit.get("url"),
            unit.get("video_url"),
            unit.get("video_play_url"),
            unit.get("video_download_url"),
        ):
            valid_video_units.append(unit)
            valid_video_sources.add(str(unit.get("source") or f"video:{pos}"))

    valid_video_results = []
    for result in sanitized.get("video_results") or []:
        if not isinstance(result, dict):
            continue
        if _is_valid_video_ref(
            sanitized,
            result.get("asset_rel"),
            result.get("local_path"),
            result.get("path"),
            result.get("url"),
            result.get("video_url"),
            result.get("video_play_url"),
            result.get("video_download_url"),
        ):
            valid_video_results.append(result)

    if isinstance(index, dict):
        index["video_units"] = valid_video_units
        if not valid_video_units and not valid_video_results:
            for key in (
                "timeline_frames",
                "review_sheets",
                "segment_reviews",
                "ocr_chunks",
                "asr_chunks",
                "moment_sheets",
                "moments",
                "precise_sheets",
                "asr_segments",
                "asr_raw",
                "ocr_items",
            ):
                index[key] = []
            index["evidence_catalog"] = [
                value
                for value in index.get("evidence_catalog") or []
                if not isinstance(value, dict)
                or not str(value.get("source") or "").startswith(("video:", "video_frame:", "video_audio:"))
            ]
        elif valid_video_sources:
            for key in (
                "timeline_frames",
                "review_sheets",
                "segment_reviews",
                "ocr_chunks",
                "asr_chunks",
                "moment_sheets",
                "moments",
                "precise_sheets",
                "asr_segments",
                "ocr_items",
                "evidence_catalog",
            ):
                values = index.get(key)
                if not isinstance(values, list):
                    continue
                index[key] = [
                    value
                    for value in values
                    if not isinstance(value, dict)
                    or not str(value.get("video_source") or value.get("source") or "").startswith(("video:", "video_frame:", "video_audio:"))
                    or any(
                        str(value.get("video_source") or value.get("source") or "").startswith(source)
                        for source in valid_video_sources
                    )
                ]
        valid_catalog_ids = {
            str(value.get("evidence_id") or "")
            for value in index.get("evidence_catalog") or []
            if isinstance(value, dict) and value.get("evidence_id")
        }
        if valid_catalog_ids and isinstance(index.get("final_evidence_refs"), list):
            index["final_evidence_refs"] = [
                value for value in index["final_evidence_refs"] if str(value) in valid_catalog_ids
            ]
        sanitized["evidence_index"] = index
    sanitized["video_results"] = valid_video_results
    return sanitized


class CrawlRequest(BaseModel):
    platform: str = "xhs"
    display_name: str = ""
    crawl_mode: str = "search"
    keyword: str = "泳装"
    keyword_source: str = "keyword"
    lexicon_category: str = ""
    library_ids: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    scoring_template: str = "balanced"
    rule_snapshot: dict = Field(default_factory=dict)
    lexicon_keywords: list[str] = []
    creator_url: str = ""
    creator_id: str = ""
    start_page: StrictInt = Field(default=0, ge=0)
    max_notes: StrictInt = Field(default=5, ge=1, le=5)
    max_comments: StrictInt = Field(default=100, ge=0, le=1000)
    max_concurrency: StrictInt = Field(default=1, ge=1, le=3)
    max_items_per_minute: StrictInt = Field(default=5, ge=1, le=5)
    crawler_account_id: Optional[str] = None
    collect_comments: StrictBool = True
    get_sub_comment: StrictBool = False
    collect_media: StrictBool = True
    auto_analyze: StrictBool = True
    analyze_limit: StrictInt = Field(default=0, ge=0)
    run_crawler: StrictBool = True
    source_output_id: Optional[str] = None
    analysis_batch_size: StrictInt = Field(default=5, ge=1, le=20)
    prompt_profile_snapshot: dict = Field(default_factory=dict)
    policy_id: str = ""
    relation_context: dict = Field(default_factory=dict)


class JobControlRequest(BaseModel):
    action: str
    analyze_limit: int = 0


def crawl_request_from_job(job: dict) -> CrawlRequest:
    """Rebuild the frozen crawl inputs when the same task is resumed."""
    model_fields = getattr(CrawlRequest, "model_fields", None) or getattr(
        CrawlRequest, "__fields__", {}
    )
    payload = {
        name: job[name]
        for name in model_fields
        if name in job and job[name] is not None
    }
    effective_config = dict(job.get("effective_config") or {})
    for name in ("collect_comments", "collect_media"):
        if name not in payload and name in effective_config:
            payload[name] = effective_config[name]
    return CrawlRequest(**payload)


def resume_job_and_requeue_run(job_id: str, operation, *args) -> None:
    """Finish a Job resume, then wake its interrupted Investigation Run."""
    try:
        operation(*args)
    finally:
        completed = job_store.get(job_id) or {}
    if str(completed.get("status") or "") != "completed":
        if str(completed.get("status") or "") == "failed":
            failure = failure_from_job(completed)
            error_message = str(completed.get("error") or "任务恢复失败")
            investigation_creation_store.fail_recovery_for_job(
                job_id,
                error_code=str(failure.get("code") or "crawl_resume_failed"),
                error_message=error_message,
            )
        return
    if investigation_creation_store.requeue_interrupted_run_for_job(job_id):
        job_store.log(
            job_id,
            "任务已从检查点完成，调查流程继续生成结果",
            stage="recovery",
        )


class ResumeJobTask:
    """Bound background callable that preserves the Job identity for diagnostics."""

    def __init__(self, job_id: str, operation) -> None:
        self.job_id = job_id
        self.operation = operation

    def run(self, *args) -> None:
        resume_job_and_requeue_run(self.job_id, self.operation, *args)


class AuditPolicyRequest(BaseModel):
    name: str = ""
    description: str = ""
    library_ids: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    scoring_template: str = "balanced"
    rule_snapshot: dict = Field(default_factory=dict)
    ruleset_revision_id: str = ""
    config: dict = Field(default_factory=dict)


class AuditConfigRevisionRequest(BaseModel):
    policy_id: str


class MonitoredUserFromAuditResultRequest(BaseModel):
    audit_result_id: int


class CrawlerAccountCreateRequest(BaseModel):
    platform: str
    display_name: str
    platform_account_id: str = ""


class CrawlerAccountUpdateRequest(BaseModel):
    display_name: str | None = None
    platform_account_id: str | None = None
    status: Literal["login_required", "disabled"] | None = None


class CommentUserRelationRequest(BaseModel):
    relation_context: dict = Field(default_factory=dict)


class AuditResultReviewRequest(BaseModel):
    status: str = "reviewed"
    note: str = ""
    reviewer: str = ""


class LexiconKeywordRequest(BaseModel):
    category_id: str = ""
    keyword: str = ""
    match_type: str = "模糊"
    platform: str = "全平台"
    risk_level: str = "中"
    enabled: bool = True
    note: str = ""


class LexiconCategoryRequest(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = None
    id: str = ""
    title: str = ""
    risk_label: str = ""
    terms: list[str] = Field(default_factory=list)
    platform_keywords: list[str] = Field(default_factory=list)
    platform_tags: list[str] = Field(default_factory=list)
    entries: list[dict] | None = None


class LexiconPromptProfileRequest(BaseModel):
    image_prompt: str = ""
    frame_prompt: str = ""
    fusion_prompt_template: str = ""


def prompt_profile_snapshot(category_id: str) -> dict:
    profile = lexicon_store.get_prompt_profile(category_id or "soft")
    return {
        key: value
        for key, value in profile.items()
        if key != "preview"
    }


def prepare_prompt_context(
    *,
    library_ids: list[str] | None,
    lexicon_category: str = "",
    capabilities: list[str] | None = None,
    scoring_template: str = "balanced",
    rule_snapshot: dict | None = None,
    force_composite: bool = False,
) -> dict:
    normalized_library_ids = normalize_library_ids(library_ids, lexicon_category or "soft")
    normalized_capabilities = normalize_capabilities(capabilities)
    if force_composite:
        libraries = lexicon_store.get_knowledge_packages(normalized_library_ids)
        compiled = compile_rule_profile(
            libraries=libraries,
            capabilities=normalized_capabilities,
            scoring_template=scoring_template,
            rule_snapshot=rule_snapshot or {},
        )
        normalized_rule_snapshot = compiled.get("rule_snapshot") or {}
        prompt_snapshot = {
            key: value
            for key, value in compiled.items()
            if key not in {"rule_snapshot"}
        }
        return {
            "library_ids": normalized_library_ids,
            "capabilities": normalized_capabilities,
            "scoring_template": scoring_template if scoring_template in TEMPLATE_IMPORTANCE else "balanced",
            "rule_snapshot": normalized_rule_snapshot,
            "prompt_profile_snapshot": prompt_snapshot,
        }
    primary_category = normalized_library_ids[0] if normalized_library_ids else (lexicon_category or "soft")
    return {
        "library_ids": normalized_library_ids,
        "capabilities": normalized_capabilities,
        "scoring_template": scoring_template if scoring_template in TEMPLATE_IMPORTANCE else "balanced",
        "rule_snapshot": rule_snapshot or {},
        "prompt_profile_snapshot": prompt_profile_snapshot(primary_category),
    }


def build_audit_config_revision_payload(
    *,
    source_policy_id: str = "",
    source_policy_name: str = "自定义审核配置",
    source_policy_version: str = "",
    library_ids: list[str] | None,
    lexicon_category: str = "soft",
    capabilities: list[str] | None,
    scoring_template: str = "balanced",
    rule_snapshot: dict | None = None,
    force_composite: bool = True,
) -> dict:
    context = prepare_prompt_context(
        library_ids=library_ids,
        lexicon_category=lexicon_category,
        capabilities=capabilities,
        scoring_template=scoring_template,
        rule_snapshot=rule_snapshot or {},
        force_composite=force_composite,
    )
    knowledge_packages = lexicon_store.get_knowledge_packages(context["library_ids"])
    rule = context.get("rule_snapshot") or {}
    prompt_snapshot = context.get("prompt_profile_snapshot") or {}
    audit_config = {
        "schema_version": "1.0",
        "source_policy_id": source_policy_id,
        "source_policy_name": source_policy_name,
        "source_policy_version": source_policy_version,
        "library_ids": context["library_ids"],
        "capabilities": context["capabilities"],
        "scoring_template": context["scoring_template"],
        "thresholds": rule.get("thresholds") or DEFAULT_THRESHOLDS,
        "scoring_rules": rule.get("scoring_rules") or [],
        "prompt_version": prompt_snapshot.get("prompt_version") or prompt_snapshot.get("version") or "",
    }
    hash_payload = {
        "audit_config": audit_config,
        "knowledge_package_snapshots": knowledge_packages,
        "rule_snapshot": rule,
        "prompt_profile_snapshot": prompt_snapshot,
    }
    config_hash = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "source_policy_id": source_policy_id,
        "source_policy_name": source_policy_name,
        "source_policy_version": source_policy_version,
        "audit_config": audit_config,
        "knowledge_package_snapshots": knowledge_packages,
        "rule_snapshot": rule,
        "prompt_profile_snapshot": prompt_snapshot,
        "config_hash": config_hash,
        "context": context,
    }


def build_revision_payload_from_policy(policy: dict) -> dict:
    config = audit_policy_store.config_for_use(policy)
    ruleset_revision_id = str(config.get("ruleset_revision_id") or "").strip()
    if ruleset_revision_id:
        if not policy.get("published_config") or not policy.get("published_version"):
            raise HTTPException(
                status_code=409,
                detail="RuleSet-backed policy must be published before it can be frozen",
            )
        validate_audit_policy_ruleset_reference(config)
        thresholds = config.get("rule_snapshot", {}).get("thresholds")
        if not isinstance(thresholds, dict):
            thresholds = DEFAULT_THRESHOLDS
        policy_version = audit_policy_store.version_for_use(policy)
        compiled = ruleset_service.compile_for_execution(
            ruleset_revision_id,
            audit_policy={
                "policy_id": str(policy.get("id") or ""),
                "policy_name": str(policy.get("name") or "研判方案"),
                "policy_version": policy_version,
                "ruleset_revision_id": ruleset_revision_id,
                "policy_config": config,
                "library_ids": config.get("library_ids") or [],
                "capabilities": config.get("capabilities") or [],
                "scoring_template": str(config.get("scoring_template") or "balanced"),
                "thresholds": thresholds,
            },
            principal=principal_provider(),
        )
        prompt_snapshot = compiled["prompt_profile_snapshot"]
        rule_snapshot = compiled["rule_snapshot"]
        library_ids = normalize_library_ids(
            config.get("library_ids") or [],
            str(config.get("lexicon_category") or "gambling"),
        )
        capabilities = normalize_capabilities(config.get("capabilities") or [])
        scoring_template = (
            str(config.get("scoring_template") or "balanced")
            if str(config.get("scoring_template") or "balanced") in TEMPLATE_IMPORTANCE
            else "balanced"
        )
        audit_config = {
            "schema_version": 2,
            "source_policy_id": str(policy.get("id") or ""),
            "source_policy_name": str(policy.get("name") or "研判方案"),
            "source_policy_version": policy_version,
            "ruleset_ref": rule_snapshot["ruleset_ref"],
            "library_ids": library_ids,
            "capabilities": capabilities,
            "scoring_template": scoring_template,
            "thresholds": rule_snapshot["thresholds"],
            "prompt_version": prompt_snapshot["prompt_version"],
            "system_template_version": prompt_snapshot["system_template_version"],
            "compiler_version": prompt_snapshot["compiler_version"],
        }
        return {
            "source_policy_id": audit_config["source_policy_id"],
            "source_policy_name": audit_config["source_policy_name"],
            "source_policy_version": audit_config["source_policy_version"],
            "audit_config": audit_config,
            "knowledge_package_snapshots": [],
            "rule_snapshot": rule_snapshot,
            "prompt_profile_snapshot": prompt_snapshot,
            "config_hash": compiled["config_hash"],
            "context": {
                "library_ids": library_ids,
                "capabilities": capabilities,
                "scoring_template": scoring_template,
                "rule_snapshot": rule_snapshot,
                "prompt_profile_snapshot": prompt_snapshot,
            },
        }
    return build_audit_config_revision_payload(
        source_policy_id=str(policy.get("id") or ""),
        source_policy_name=str(policy.get("name") or "研判方案"),
        source_policy_version=audit_policy_store.version_for_use(policy),
        library_ids=config.get("library_ids") or [],
        lexicon_category=str(config.get("lexicon_category") or "soft"),
        capabilities=config.get("capabilities") or [],
        scoring_template=str(config.get("scoring_template") or "balanced"),
        rule_snapshot=config.get("rule_snapshot") or {},
        force_composite=True,
    )


def activate_audit_config_revision(job_id: str, revision: dict) -> None:
    audit_config = revision.get("audit_config") or {}
    job_store.update(
        job_id,
        current_audit_config_revision_id=revision.get("id"),
        lexicon_category=(audit_config.get("library_ids") or ["soft"])[0],
        library_ids=audit_config.get("library_ids") or [],
        capabilities=audit_config.get("capabilities") or [],
        scoring_template=audit_config.get("scoring_template") or "balanced",
        rule_snapshot=revision.get("rule_snapshot") or {},
        prompt_profile_snapshot=revision.get("prompt_profile_snapshot") or {},
    )


def create_revision_from_payload(job_id: str, payload: dict, *, created_by: str = "") -> dict:
    revision = audit_config_revision_store.create(
        job_id=job_id,
        created_by=created_by or "system",
        source_policy_id=payload.get("source_policy_id") or "",
        source_policy_name=payload.get("source_policy_name") or "",
        source_policy_version=payload.get("source_policy_version") or "",
        audit_config=payload.get("audit_config") or {},
        knowledge_package_snapshots=payload.get("knowledge_package_snapshots") or [],
        rule_snapshot=payload.get("rule_snapshot") or {},
        prompt_profile_snapshot=payload.get("prompt_profile_snapshot") or {},
        config_hash=payload.get("config_hash") or "",
    )
    activate_audit_config_revision(job_id, revision)
    return revision


def validate_audit_policy_ruleset_reference(config: dict) -> None:
    revision_id = str((config or {}).get("ruleset_revision_id") or "").strip()
    if not revision_id:
        return
    try:
        compiled = ruleset_service.compile_for_execution(
            revision_id,
            audit_policy={
                "ruleset_revision_id": revision_id,
                "policy_config": config,
                "library_ids": config.get("library_ids") or [],
                "capabilities": config.get("capabilities") or [],
                "scoring_template": str(config.get("scoring_template") or "balanced"),
                "thresholds": (config.get("rule_snapshot") or {}).get("thresholds")
                if isinstance(config.get("rule_snapshot"), dict)
                else DEFAULT_THRESHOLDS,
            },
            principal=principal_provider(),
        )
    except RuleSetRevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Published RuleSetRevision not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if str(compiled["audit_policy_snapshot"].get("ruleset_revision_id") or "") != revision_id:
        raise HTTPException(status_code=400, detail="RuleSet Foundation pilot only supports gambling")


def enabled_keywords_for_categories(category_ids: list[str]) -> list[str]:
    seen = set()
    keywords = []
    for category_id in category_ids:
        for keyword in lexicon_store.enabled_search_keywords(category_id):
            cleaned = keyword.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                keywords.append(cleaned)
    return keywords


def parse_form_list(value: str) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return [str(item).strip() for item in loaded if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [item.strip() for item in text.split(",") if item.strip()]


def parse_form_dict(value: str) -> dict:
    text = (value or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def validate_creator_url(platform: str, creator_url: str, *, allow_legacy_id: bool = False) -> str:
    try:
        return validate_creator_url_contract(
            platform,
            creator_url,
            allow_legacy_id=allow_legacy_id,
        )
    except CreatorUrlValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def normalize_relation_context(value: dict | None) -> dict:
    context = dict(value or {})
    if not context:
        return {}
    source = str(context.get("source") or "").strip()
    if source != "comment_user_analysis":
        raise HTTPException(status_code=400, detail=f"Unsupported relation_context source: {source or 'unknown'}")
    try:
        parent_id = int(context.get("parent_audit_result_id") or 0)
    except (TypeError, ValueError):
        parent_id = 0
    if parent_id <= 0:
        raise HTTPException(status_code=400, detail="relation_context.parent_audit_result_id is required")
    context["parent_audit_result_id"] = parent_id
    return context


def relation_context_parent_result(context: dict) -> dict | None:
    if not context:
        return None
    result = audit_result_store.get_result(int(context.get("parent_audit_result_id") or 0))
    if not result:
        raise HTTPException(status_code=404, detail="Parent audit result not found")
    return result


def profile_url_identity(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return text.rstrip("/").lower()
    if not parsed.scheme or not parsed.netloc:
        return text.rstrip("/").lower()
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def ensure_relation_context_matches_creator(context: dict, creator_url: str) -> dict:
    if not context:
        return {}
    next_context = dict(context)
    context_url = str(next_context.get("suspect_profile_url") or "").strip()
    if context_url and profile_url_identity(context_url) != profile_url_identity(creator_url):
        raise HTTPException(status_code=400, detail="relation_context.suspect_profile_url must match creator_url")
    next_context["suspect_profile_url"] = creator_url
    return next_context


def enrich_job(job: dict) -> dict:
    from .investigation_creation.adapters import _public_job_logs

    enriched = dict(job)
    enriched["logs"] = _public_job_logs(enriched.get("logs") or [])
    control = enriched.get("control") or {}
    stats = ingestion_store.stats_for_task(enriched["id"])
    status = str(enriched.get("status") or "")
    if not enriched.get("display_name"):
        if enriched.get("input_type") == "local_video":
            enriched["display_name"] = enriched.get("input_filename") or enriched.get("keyword") or enriched["id"]
        elif enriched.get("crawl_mode") == "creator":
            enriched["display_name"] = enriched.get("creator_url") or enriched.get("creator_id") or enriched["id"]
        else:
            enriched["display_name"] = enriched.get("keyword") or enriched.get("lexicon_category") or enriched["id"]

    persisted_crawl_status = str(enriched.get("crawl_status") or "")
    persisted_analysis_status = str(enriched.get("analysis_status") or "")
    if not enriched.get("run_crawler"):
        crawl_status = "skipped"
    elif control.get("stop_all_requested"):
        crawl_status = "stopping"
    elif persisted_crawl_status and persisted_crawl_status != "pending":
        crawl_status = persisted_crawl_status
    elif status in {"crawl_paused", "stopped", "interrupted"} or control.get("crawl_stop_requested"):
        crawl_status = "stopped"
    elif status in {"completed", "analysis_paused", "analysis_stopped"}:
        crawl_status = "completed"
    elif status == "failed":
        crawl_status = "failed"
    elif status in {"running", "crawl_pausing", "queued", "analysis_stopping"}:
        crawl_status = "running"
    else:
        crawl_status = "unknown"

    if control.get("stop_all_requested") or status == "stopped":
        analysis_status = "stopping" if control.get("stop_all_requested") else "stopped"
    elif persisted_analysis_status and persisted_analysis_status != "pending":
        analysis_status = persisted_analysis_status
    elif status == "interrupted":
        analysis_status = "pending"
    elif status == "analysis_stopped":
        analysis_status = "stopped"
    elif control.get("analysis_stop_requested") or status == "analysis_stopping":
        analysis_status = "stopping"
    elif control.get("analysis_paused") or status == "analysis_paused":
        analysis_status = "paused"
    elif status in {"running", "analysis_running", "crawl_pausing"}:
        analysis_status = "running"
    elif status == "completed" and stats.get("failed_analysis_count", 0) > 0:
        analysis_status = "partial"
    elif stats["pending_analysis_count"] > 0:
        analysis_status = "pending"
    elif stats["completed_analysis_count"] > 0 or status == "completed":
        analysis_status = "completed"
    else:
        analysis_status = "idle"

    enriched["task_stats"] = stats
    if enriched.get("crawl_mode") == "creator" and not enriched.get("creator_nickname"):
        author = audit_result_store.representative_author_for_job(enriched["id"])
        nickname = str(author.get("nickname") or "").strip()
        if nickname:
            enriched["creator_nickname"] = nickname
            enriched["creator_author"] = author
    enriched["crawl_status"] = crawl_status
    enriched["analysis_status"] = analysis_status
    actions = available_job_actions(enriched, stats)
    preferred_account_id = str(
        ((control.get("execution_account") or {}).get("id"))
        or enriched.get("crawler_account_id")
        or ""
    ).strip()
    if (
        actions.get("resume_crawl")
        and preferred_account_id
        and not crawler_account_store.available_accounts(
            str(enriched.get("platform") or "")
        )
    ):
        # Keep the UI control in sync with the endpoint guard. A fresh GET will
        # reopen it when cooldown expires or a login refresh clears cooldown.
        actions["resume_crawl"] = False
    enriched["available_actions"] = actions
    revision_id = str(enriched.get("current_audit_config_revision_id") or "")
    if revision_id:
        revision = audit_config_revision_store.get(revision_id)
        if revision:
            enriched["current_audit_config_revision"] = {
                "id": revision.get("id"),
                "version": revision.get("version"),
                "source_policy_id": revision.get("source_policy_id"),
                "source_policy_name": revision.get("source_policy_name"),
                "source_policy_version": revision.get("source_policy_version"),
                "config_hash": revision.get("config_hash"),
                "effective_from": revision.get("effective_from"),
                "prompt_version": (revision.get("prompt_profile_snapshot") or {}).get("prompt_version", ""),
                "audit_config": revision.get("audit_config") or {},
            }
    return redact_authoritative_m3_crawler_account(enriched)


def redact_authoritative_m3_crawler_account(job: dict) -> dict:
    if not str(job.get("id") or "").startswith("m3-"):
        return job
    public_job = dict(job)
    public_job.pop("crawler_account_id", None)
    public_job.pop("crawler_account_display_name", None)
    public_job["logs"] = [
        dict(item)
        for item in public_job.get("logs") or []
        if not any(
            marker in str(item.get("message") or "")
            for marker in ("执行账号", "采集账号")
        )
    ]
    return public_job


def validate_crawler_account_for_job(account_id: str | None, platform: str) -> dict | None:
    normalized_id = str(account_id or "").strip()
    if not normalized_id:
        return None
    account = crawler_account_store.get(normalized_id)
    if not account:
        raise HTTPException(status_code=404, detail="采集账号不存在")
    if account["platform"] != platform:
        raise HTTPException(status_code=400, detail="采集账号与任务平台不匹配")
    if account["status"] != "active":
        raise HTTPException(status_code=409, detail="采集账号当前不可用，请重新登录或启用账号")
    if not account["has_auth_state"]:
        raise HTTPException(status_code=409, detail="采集账号尚未登录")
    if account_is_cooling_down(account):
        reason = "平台验证" if account.get("failure_kind") == "verify" else "平台限流"
        raise HTTPException(
            status_code=409,
            detail=f"采集账号因{reason}正在冷却，请在冷却结束后重试或选择其他账号",
        )
    return account


@app.on_event("startup")
def recover_interrupted_jobs():
    deleted_results = audit_result_store.delete_for_archived_jobs()
    if deleted_results:
        print(f"[startup] deleted results for archived jobs: {deleted_results}")
    recovered = job_store.recover_interrupted_jobs()
    requeued_analysis = ingestion_store.reset_all_analyzing()
    if recovered:
        print(f"[startup] recovered interrupted jobs: {recovered}")
    if requeued_analysis:
        print(f"[startup] requeued orphaned analysis items: {requeued_analysis}")
    investigation_recovery = investigation_turn_executor.recover()
    if any(investigation_recovery.values()):
        print(f"[startup] investigation Turn recovery: {investigation_recovery}")
    creation_recovery = investigation_creation_turn_executor.recover()
    if any(creation_recovery.values()):
        print(f"[startup] creation Turn recovery: {creation_recovery}")


@app.on_event("shutdown")
def stop_crawler_account_login_sessions():
    crawler_account_login_manager.shutdown()
    investigation_turn_executor.shutdown(wait=True)
    investigation_creation_turn_executor.shutdown(wait=True)
    investigation_agent_service.close()
    investigation_creation_conversation_service.close()


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/saas")
def saas_index():
    return FileResponse(ROOT / "saas-demo.html")


@app.get("/saas-v2")
@app.get("/saas-v2/")
@app.get("/saas-v2/{path:path}")
def saas_v2_index(path: str = ""):
    index_path = FRONTEND_V2_DIST_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="frontend-v2 has not been built")
    return FileResponse(index_path)


@app.get("/styles.css")
def styles():
    return FileResponse(FRONTEND_DIR / "styles.css", media_type="text/css")


@app.get("/app.js")
def app_js():
    return FileResponse(FRONTEND_DIR / "app.js", media_type="text/javascript")


@app.get("/config.js")
def config_js():
    return FileResponse(FRONTEND_DIR / "config.js", media_type="text/javascript")


@app.get("/api/config")
def get_config():
    runtime_boundary_error = runtime_data_directory_error(
        data_dir=settings.data_dir,
        account_db_path=crawler_account_store.db_path,
        outputs_dir=settings.outputs_dir,
    )
    return {
        "supported_platforms": sorted(SUPPORTED_PLATFORMS),
        "default_keyword": "泳装",
        "has_api_key": bool(settings.dashscope_api_key),
        "model_base_url": settings.dashscope_base_url,
        "qwen_text_model": settings.qwen_text_model,
        "qwen_vl_model": settings.qwen_vl_model,
        "qwen_image_audit_model": settings.qwen_image_audit_model,
        "qwen_contact_sheet_model": settings.qwen_contact_sheet_model,
        "asr_translate_model": settings.asr_translate_model,
        "qwen_use_response_format": settings.qwen_use_response_format,
        "vl_image_max_side": settings.vl_image_max_side,
        "vl_image_quality": settings.vl_image_quality,
        "asr_engine": settings.asr_engine,
        "asr_language": settings.asr_language,
        "asr_device": settings.asr_device,
        "whisper_model": settings.whisper_model,
        "whisper_device": settings.whisper_device,
        "whisper_compute_type": settings.whisper_compute_type,
        "whisper_cpu_fallback": settings.whisper_cpu_fallback,
        "dolphin_model": settings.dolphin_model,
        "dolphin_lang_sym": settings.dolphin_lang_sym,
        "dolphin_region_sym": settings.dolphin_region_sym,
        "dolphin_word_timestamp": settings.dolphin_word_timestamp,
        "dolphin_predict_time": settings.dolphin_predict_time,
        "use_remote_mms_asr": settings.use_remote_mms_asr,
        "remote_mms_asr_base_url": settings.remote_mms_asr_base_url,
        "mms_model": settings.mms_model,
        "mms_target_lang": settings.mms_target_lang,
        "asr_translate_engine": settings.asr_translate_engine,
        "hymt_model": settings.hymt_model,
        "use_remote_asr": settings.use_remote_asr,
        "use_remote_vlm": settings.use_remote_vlm,
        "use_remote_llm": settings.use_remote_llm,
        "use_remote_translation": settings.use_remote_translation,
        "use_remote_ocr": settings.use_remote_ocr,
        "ocr_enabled": settings.ocr_enabled,
        "ocr_engine": settings.ocr_engine,
        "ocr_concurrency": settings.ocr_concurrency,
        "ocr_language_hint": settings.ocr_language_hint,
        "ocr_sample_fps": settings.ocr_sample_fps,
        "ocr_paddle_prompt_preset": settings.ocr_paddle_prompt_preset,
        "ocr_paddle_prompt_compose": settings.ocr_paddle_prompt_compose,
        "max_video_frames": settings.max_video_frames,
        "video_review_max_frames": settings.video_review_max_frames,
        "video_ocr_max_frames": settings.video_ocr_max_frames,
        "video_moment_concurrency": settings.video_moment_concurrency,
        "video_scene_threshold": settings.video_scene_threshold,
        "video_fps_floor_seconds": settings.video_fps_floor_seconds,
        "remote_inference_base_url": settings.remote_inference_base_url,
        "remote_asr_base_url": settings.remote_asr_base_url,
        "remote_translation_base_url": settings.remote_translation_base_url,
        "remote_ocr_base_url": settings.remote_ocr_base_url,
        "media_crawler_dir": str(settings.media_crawler_dir),
        "crawler_max_concurrency": settings.crawler_max_concurrency,
        "crawler_sleep_seconds": settings.crawler_sleep_seconds,
        "outputs_dir": str(settings.outputs_dir),
        "runtime_data_dir": str(settings.data_dir),
        "runtime_backend_port": os.getenv("XHS_AUDIT_BACKEND_PORT", ""),
        "runtime_boundary_error": runtime_boundary_error,
        "activity_stream_enabled": settings.activity_stream_enabled,
        "answer_stream_enabled": settings.answer_stream_enabled,
        "creation_answer_stream_enabled": settings.creation_answer_stream_enabled,
        "risk_rule_defaults": {
            "capabilities": DEFAULT_CAPABILITIES,
            "thresholds": DEFAULT_THRESHOLDS,
            "importance_scores": IMPORTANCE_SCORES,
            "templates": TEMPLATE_IMPORTANCE,
        },
    }


@app.get("/api/outputs")
def list_outputs():
    return {"outputs": MediaCrawlerAdapter().list_existing_outputs()}


@app.get("/api/crawler-accounts")
def list_crawler_accounts():
    return {"items": crawler_account_store.list()}


@app.post("/api/crawler-accounts", status_code=201)
def create_crawler_account(request: CrawlerAccountCreateRequest):
    try:
        item = crawler_account_store.create(
            platform=request.platform,
            display_name=request.display_name,
            platform_account_id=request.platform_account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@app.patch("/api/crawler-accounts/{account_id}")
def update_crawler_account(account_id: str, request: CrawlerAccountUpdateRequest):
    if request.status == "disabled":
        crawler_account_login_manager.cancel_for_account(account_id)
    try:
        item = crawler_account_store.update(
            account_id,
            display_name=request.display_name,
            platform_account_id=request.platform_account_id,
            status=request.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Crawler account not found")
    return {"item": item}


@app.delete("/api/crawler-accounts/{account_id}", status_code=204)
def delete_crawler_account(account_id: str):
    crawler_account_login_manager.cancel_for_account(account_id)
    if not crawler_account_store.delete(account_id):
        raise HTTPException(status_code=404, detail="Crawler account not found")


@app.post("/api/crawler-accounts/{account_id}/login-sessions", status_code=201)
def start_crawler_account_login(account_id: str, response: Response):
    account = crawler_account_store.get(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Crawler account not found")
    try:
        session = crawler_account_login_manager.start(account)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"item": session}


@app.get("/api/crawler-account-login-sessions/{session_id}")
def get_crawler_account_login(session_id: str, response: Response):
    session = crawler_account_login_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Login session not found")
    response.headers["Cache-Control"] = "no-store"
    return {"item": session}


@app.delete("/api/crawler-account-login-sessions/{session_id}", status_code=204)
def cancel_crawler_account_login(session_id: str):
    if not crawler_account_login_manager.cancel(session_id):
        raise HTTPException(status_code=404, detail="Login session not found")


@app.get("/api/monitored-users")
def list_monitored_users():
    return {"items": job_store.list_monitored_users()}


@app.post("/api/monitored-users/from-audit-result")
def create_monitored_user_from_audit_result(request: MonitoredUserFromAuditResultRequest):
    result = audit_result_store.get_result(request.audit_result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Audit result not found")
    try:
        item = job_store.upsert_monitored_user_from_audit_result(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"item": item}


@app.post("/api/monitored-users/comment-relation")
def create_comment_user_relation(request: CommentUserRelationRequest):
    relation_context = normalize_relation_context(request.relation_context)
    parent_result = relation_context_parent_result(relation_context)
    if not parent_result:
        raise HTTPException(status_code=404, detail="Parent audit result not found")
    try:
        relation = job_store.upsert_comment_user_relation(
            analysis_job_id="",
            relation_context=relation_context,
            parent_audit_result=parent_result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"relation": relation}


@app.get("/api/audit-policies")
def list_audit_policies(include_drafts: bool = True):
    return {"items": audit_policy_store.list(include_drafts=include_drafts)}


@app.get("/api/audit-policies/{policy_id}")
def get_audit_policy(policy_id: str):
    policy = audit_policy_store.get(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Audit policy not found")
    return policy


@app.post("/api/audit-policies")
def create_audit_policy(request: AuditPolicyRequest):
    config = dict(request.config or {})
    if not config:
        config = {
            "library_ids": request.library_ids,
            "ruleset_revision_id": request.ruleset_revision_id,
            "capabilities": request.capabilities,
            "scoring_template": request.scoring_template,
            "rule_snapshot": request.rule_snapshot,
        }
    validate_audit_policy_ruleset_reference(config)
    try:
        return audit_policy_store.create(
            name=request.name,
            description=request.description,
            config=config,
        )
    except AuditPolicyLibraryReferenceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/audit-policies/{policy_id}")
def update_audit_policy(policy_id: str, request: AuditPolicyRequest):
    existing = audit_policy_store.get(policy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Audit policy not found")
    config = dict(request.config or {})
    if not config:
        config = {
            "library_ids": request.library_ids,
            "ruleset_revision_id": request.ruleset_revision_id,
            "capabilities": request.capabilities,
            "scoring_template": request.scoring_template,
            "rule_snapshot": request.rule_snapshot,
        }
    try:
        validate_audit_policy_ruleset_reference(config or existing.get("config") or {})
        return audit_policy_store.update(
            policy_id,
            name=request.name or existing.get("name"),
            description=request.description if request.description else existing.get("description", ""),
            config=config or existing.get("config") or {},
        )
    except AuditPolicyLibraryReferenceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="Audit policy not found")


@app.post("/api/audit-policies/{policy_id}/publish")
def publish_audit_policy(policy_id: str):
    policy = audit_policy_store.get(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Audit policy not found")
    expected_draft_hash = audit_policy_store.draft_hash(policy)
    validate_audit_policy_ruleset_reference(policy.get("config") or {})
    try:
        return audit_policy_store.publish(
            policy_id,
            expected_draft_hash=expected_draft_hash,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Audit policy not found")
    except AuditPolicyLibraryReferenceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AuditPolicyRevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/audit-policies/{policy_id}")
def delete_audit_policy(policy_id: str):
    try:
        policy = audit_policy_store.delete(policy_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Audit policy not found")
    return {"ok": True, "id": policy_id, "policy": policy}


@app.get("/api/lexicons")
def list_lexicons():
    return {"categories": lexicon_store.list_categories()}


@app.post("/api/lexicons")
def create_lexicon_category(request: LexiconCategoryRequest):
    try:
        category = lexicon_store.upsert_category(
            category_id=request.id,
            title=request.title,
            risk_label=request.risk_label,
            description=request.description,
            terms=request.terms,
            platform_keywords=request.platform_keywords,
            platform_tags=request.platform_tags,
            entries=request.entries,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"category": category, "categories": lexicon_store.list_categories()}


@app.patch("/api/lexicons/{category_id}")
def update_lexicon_category(category_id: str, request: LexiconCategoryRequest):
    try:
        from .resource_management.legacy_lexicon import save_editor
        from .resource_management.contracts import ResourceError
        if request.entries is None:
            raise ResourceError('请通过完整词库编辑接口提交内容。', code='RESOURCE_VERSION_REQUIRED')
        category = save_editor(lexicon_store, category_id, request.title, request.risk_label, request.entries, request.expected_version, description=request.description)

    except KeyError:
        raise HTTPException(status_code=404, detail="Lexicon category not found")
    except ValueError as exc:
        code = getattr(exc, 'code', '')
        raise HTTPException(status_code=428 if code == 'RESOURCE_VERSION_REQUIRED' else 409 if 'CONFLICT' in code else 400, detail={'code':code,'message':str(exc)})
    return {"category": category, "categories": lexicon_store.list_categories()}


@app.delete("/api/lexicons/{category_id}")
def delete_lexicon_category(category_id: str):
    try:
        category = lexicon_store.delete_category_atomically(category_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Lexicon category not found")
    except LexiconCategoryReferenceConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Lexicon category is referenced by Draft or Published AuditPolicy; "
                    "update and publish those policies before deleting it"
                ),
                "policy_references": exc.references,
            },
        ) from exc
    return {
        "ok": True,
        "id": category_id,
        "category": category,
        "affected_policy_count": 0,
        "categories": lexicon_store.list_categories(),
    }


@app.post("/api/lexicon-keywords")
def create_lexicon_keyword(request: LexiconKeywordRequest):
    try:
        keyword = lexicon_store.add_keyword(
            category_id=request.category_id,
            keyword=request.keyword,
            match_type=request.match_type,
            platform=request.platform,
            risk_level=request.risk_level,
            enabled=request.enabled,
            note=request.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Lexicon category not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"keyword": keyword, "categories": lexicon_store.list_categories()}


@app.patch("/api/lexicon-keywords/{keyword_id}")
def update_lexicon_keyword(keyword_id: int, request: LexiconKeywordRequest):
    payload = request.dict(exclude_unset=True)
    payload.pop("category_id", None)
    try:
        keyword = lexicon_store.update_keyword(keyword_id, **payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="Lexicon keyword not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"keyword": keyword, "categories": lexicon_store.list_categories()}


@app.delete("/api/lexicon-keywords/{keyword_id}")
def delete_lexicon_keyword(keyword_id: int):
    try:
        lexicon_store.delete_keyword(keyword_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Lexicon keyword not found")
    return {"ok": True, "categories": lexicon_store.list_categories()}


@app.put("/api/lexicons/{category_id}/prompt-profile")
def update_lexicon_prompt_profile(category_id: str, request: LexiconPromptProfileRequest):
    try:
        profile = lexicon_store.update_prompt_profile(
            category_id,
            image_prompt=request.image_prompt,
            frame_prompt=request.frame_prompt,
            fusion_prompt_template=request.fusion_prompt_template,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Lexicon category not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"prompt_profile": profile, "categories": lexicon_store.list_categories()}


@app.post("/api/lexicons/{category_id}/prompt-profile/reset")
def reset_lexicon_prompt_profile(category_id: str):
    try:
        profile = lexicon_store.reset_prompt_profile(category_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Lexicon category not found")
    return {"prompt_profile": profile, "categories": lexicon_store.list_categories()}


@app.get("/api/health/gpu")
def gpu_health():
    return {
        "qwen": {
            "base_url": settings.dashscope_base_url,
            "text_model": settings.qwen_text_model,
            "vl_model": settings.qwen_vl_model,
            "image_audit_model": settings.qwen_image_audit_model,
            "contact_sheet_model": settings.qwen_contact_sheet_model,
            "asr_translate_model": settings.asr_translate_model,
            "use_response_format": settings.qwen_use_response_format,
            "image_max_side": settings.vl_image_max_side,
            "image_quality": settings.vl_image_quality,
        },
        "asr": {
            "remote": settings.use_remote_asr,
            "remote_base_url": settings.remote_asr_base_url,
            "engine": settings.asr_engine,
            "language": settings.asr_language,
            "device": settings.asr_device,
            "compute_type": settings.asr_compute_type,
            "dolphin_model": settings.dolphin_model,
            "dolphin_lang_sym": settings.dolphin_lang_sym,
            "dolphin_region_sym": settings.dolphin_region_sym,
            "dolphin_word_timestamp": settings.dolphin_word_timestamp,
            "dolphin_predict_time": settings.dolphin_predict_time,
            "use_remote_mms_asr": settings.use_remote_mms_asr,
            "remote_mms_asr_base_url": settings.remote_mms_asr_base_url,
            "mms_model": settings.mms_model,
            "mms_target_lang": settings.mms_target_lang,
            "translate_engine": settings.asr_translate_engine,
        },
        "whisper": {
            "model": settings.whisper_model,
            "device": settings.whisper_device,
            "compute_type": settings.whisper_compute_type,
            "cpu_fallback": settings.whisper_cpu_fallback,
        },
        "translation": {
            "remote": settings.use_remote_translation,
            "remote_base_url": settings.remote_translation_base_url,
            "hymt_model": settings.hymt_model,
            "target_language": settings.hymt_target_language,
        },
        "ocr": {
            "enabled": settings.ocr_enabled,
            "remote": settings.use_remote_ocr,
            "remote_base_url": settings.remote_ocr_base_url,
            "engine": settings.ocr_engine,
            "concurrency": settings.ocr_concurrency,
            "language_hint": settings.ocr_language_hint,
            "sample_fps": settings.ocr_sample_fps,
            "video_max_frames": settings.video_ocr_max_frames,
            "paddle_device": settings.ocr_paddle_device,
            "paddle_pipeline_version": settings.ocr_paddle_pipeline_version,
            "paddle_prompt_label": settings.ocr_paddle_prompt_label,
            "paddle_prompt_preset": settings.ocr_paddle_prompt_preset,
            "paddle_prompt_compose": settings.ocr_paddle_prompt_compose,
        },
        "remote_inference": {
            "vlm": settings.use_remote_vlm,
            "llm": settings.use_remote_llm,
            "base_url": settings.remote_inference_base_url,
        },
    }


from backend.audit_agent.task_settings import TaskSettingsStore, TaskSettingsConflict, effective_parameters
from backend.investigation_creation.contracts import InvestigationTaskParameters


class TaskSettingsRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    parameters: InvestigationTaskParameters


@app.get("/api/task-settings")
def get_task_settings():
    saved = TaskSettingsStore(job_store.db_path).get()
    return {**saved, "effective_parameters": effective_parameters(saved["parameters"]).model_dump(mode="json")}


@app.put("/api/task-settings")
def save_task_settings(request: TaskSettingsRequest):
    try:
        saved = TaskSettingsStore(job_store.db_path).save(
            request.parameters.model_dump(mode="json"), request.expected_revision)
        return {**saved, "effective_parameters": effective_parameters(saved["parameters"]).model_dump(mode="json")}
    except TaskSettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/jobs")
def create_job(request: CrawlRequest, background_tasks: BackgroundTasks):
    saved_settings = TaskSettingsStore(job_store.db_path).get()
    if saved_settings["revision"] and request.run_crawler:
        effective = effective_parameters(saved_settings["parameters"])
        request = CrawlRequest.model_validate({**request.model_dump(), **effective.model_dump(mode="json"),
                                              "crawler_account_id": effective.crawler_account_id})
        if not request.crawler_account_id:
            account = crawler_account_store.next_available(request.platform)
            if not account:
                raise HTTPException(status_code=409, detail="当前没有可用采集账号，请检查登录状态或等待冷却结束。")
            request.crawler_account_id = account["id"]
    requested_config = request.model_dump(
        include={
            "platform",
            "crawl_mode",
            "keyword",
            "keyword_source",
            "crawler_account_id",
            "creator_url",
            "start_page",
            "max_notes",
            "max_comments",
            "max_concurrency",
            "max_items_per_minute",
            "collect_comments",
            "get_sub_comment",
            "collect_media",
            "auto_analyze",
            "analyze_limit",
            "analysis_batch_size",
            "run_crawler",
            "source_output_id",
        },
        mode="json",
    )
    relation_context = normalize_relation_context(request.relation_context)
    provided_library_ids = [item for item in request.library_ids if str(item).strip()]
    provided_capabilities = [item for item in request.capabilities if str(item).strip()]
    provided_rule_snapshot = bool(request.rule_snapshot)
    force_composite = bool(provided_library_ids or provided_capabilities or provided_rule_snapshot)
    if request.platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {request.platform}")
    if request.crawl_mode not in {"search", "creator"}:
        raise HTTPException(status_code=400, detail=f"Unsupported crawl_mode: {request.crawl_mode}")
    if request.keyword_source not in {"keyword", "lexicon"}:
        raise HTTPException(status_code=400, detail=f"Unsupported keyword_source: {request.keyword_source}")
    if relation_context and request.crawl_mode != "creator":
        raise HTTPException(status_code=400, detail="relation_context is only supported for creator jobs")
    request.lexicon_category = request.lexicon_category.strip() or "soft"
    relation_parent_result = relation_context_parent_result(relation_context)
    revision_payload = None
    if request.policy_id:
        policy = audit_policy_store.get(request.policy_id)
        if not policy:
            raise HTTPException(status_code=404, detail="Audit policy not found")
        try:
            revision_payload = build_revision_payload_from_policy(policy)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Lexicon category not found: {exc}")
        context = revision_payload["context"]
        force_composite = True
        request.keyword_source = "lexicon" if request.crawl_mode == "search" else "keyword"
    else:
        context = None
    try:
        context = context or prepare_prompt_context(
            library_ids=request.library_ids,
            lexicon_category=request.lexicon_category,
            capabilities=request.capabilities,
            scoring_template=request.scoring_template,
            rule_snapshot=request.rule_snapshot,
            force_composite=force_composite,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Lexicon category not found: {exc}")
    request.library_ids = context["library_ids"]
    request.capabilities = context["capabilities"]
    request.scoring_template = context["scoring_template"]
    request.rule_snapshot = context["rule_snapshot"]
    request.prompt_profile_snapshot = context["prompt_profile_snapshot"]
    request.lexicon_category = request.library_ids[0] if request.library_ids else request.lexicon_category
    if request.crawl_mode != "search":
        request.keyword_source = "keyword"
        request.lexicon_keywords = []
    if request.run_crawler and request.crawl_mode == "search":
        if request.keyword_source == "lexicon":
            seen_keywords = set()
            keywords = []
            source_keywords = enabled_keywords_for_categories(request.library_ids)
            for keyword in source_keywords:
                cleaned = keyword.strip()
                if cleaned and cleaned not in seen_keywords:
                    seen_keywords.add(cleaned)
                    keywords.append(cleaned)
            if not keywords:
                raise HTTPException(status_code=400, detail="platform search keywords are required when keyword_source is lexicon")
            request.lexicon_keywords = keywords
            request.keyword = ",".join(keywords)
        elif not request.keyword.strip():
            raise HTTPException(status_code=400, detail="keyword is required when crawl_mode is search")
    if request.run_crawler and request.crawl_mode == "creator":
        creator_ref = request.creator_url.strip() or request.creator_id.strip()
        request.creator_url = validate_creator_url(
            request.platform,
            creator_ref,
            allow_legacy_id=not bool(request.creator_url.strip()),
        )
        request.creator_id = request.creator_url
        relation_context = ensure_relation_context_matches_creator(relation_context, request.creator_url)
    if not request.run_crawler and not request.source_output_id:
        raise HTTPException(status_code=400, detail="source_output_id is required when run_crawler is false")

    request.crawler_account_id = str(request.crawler_account_id or "").strip() or None
    crawler_account = validate_crawler_account_for_job(request.crawler_account_id, request.platform)
    keyword_count = len(
        [item for item in str(request.keyword or "").split(",") if item.strip()]
    ) if request.crawl_mode == "search" else 1
    effective_max_concurrency = min(
        request.max_concurrency,
        max(1, settings.crawler_max_concurrency),
    )
    effective_auto_analyze = bool(
        request.auto_analyze
        and request.analyze_limit > 0
        and settings.auto_analyze_crawled_content
    )
    request.max_concurrency = effective_max_concurrency
    request.auto_analyze = effective_auto_analyze
    request.collect_comments = bool(request.collect_comments)
    if not request.collect_comments:
        request.max_comments = 0
        request.get_sub_comment = False
    effective_config = {
        **requested_config,
        "keyword": request.keyword,
        "keyword_source": request.keyword_source,
        "keyword_count": keyword_count,
        "max_notes": request.max_notes,
        "estimated_max_total": keyword_count * request.max_notes,
        "max_concurrency": effective_max_concurrency,
        "collect_comments": request.collect_comments,
        "max_comments": request.max_comments,
        "get_sub_comment": request.get_sub_comment,
        "collect_media": request.collect_media,
        "auto_analyze": effective_auto_analyze,
        "analyze_limit": request.analyze_limit,
    }

    job = job_store.create(
        platform=request.platform,
        crawler_account_id=request.crawler_account_id,
        crawler_account_display_name=(crawler_account or {}).get("display_name", ""),
        display_name=request.display_name.strip(),
        crawl_mode=request.crawl_mode,
        keyword=request.keyword,
        keyword_source=request.keyword_source,
        lexicon_category=request.lexicon_category,
        library_ids=request.library_ids,
        capabilities=request.capabilities,
        scoring_template=request.scoring_template,
        rule_snapshot=request.rule_snapshot,
        lexicon_keywords=request.lexicon_keywords,
        creator_url=request.creator_url,
        creator_id=request.creator_id,
        start_page=request.start_page,
        max_notes=request.max_notes,
        max_comments=request.max_comments,
        max_concurrency=request.max_concurrency,
        max_items_per_minute=request.max_items_per_minute,
        get_sub_comment=request.get_sub_comment,
        auto_analyze=request.auto_analyze,
        analyze_limit=request.analyze_limit,
        run_crawler=request.run_crawler,
        source_output_id=request.source_output_id,
        analysis_batch_size=request.analysis_batch_size,
        prompt_profile_snapshot=request.prompt_profile_snapshot,
        requested_config=requested_config,
        effective_config=effective_config,
    )
    if revision_payload is None:
        revision_payload = build_audit_config_revision_payload(
            source_policy_id="",
            source_policy_name="自定义审核配置",
            source_policy_version="",
            library_ids=request.library_ids,
            lexicon_category=request.lexicon_category,
            capabilities=request.capabilities,
            scoring_template=request.scoring_template,
            rule_snapshot=request.rule_snapshot,
            force_composite=force_composite,
        )
    revision = create_revision_from_payload(
        job["id"],
        revision_payload,
        created_by="api",
    )
    job_store.log(
        job["id"],
        f"生成任务审核配置 Revision {revision.get('version')}：{revision.get('source_policy_name') or '自定义审核配置'}",
        stage="configuration",
    )
    job_store.log(
        job["id"],
        (
            f"执行参数已冻结：每关键词最多 {request.max_notes} 条，"
            f"预计最大总量 {effective_config['estimated_max_total']} 条，"
            f"每帖评论 {request.max_comments} 条，并发 {request.max_concurrency}，"
            f"媒体采集 {'开启' if request.collect_media else '关闭'}，"
            f"自动分析 {'开启' if request.auto_analyze else '关闭'}"
        ),
        stage="configuration",
    )
    if crawler_account:
        job_store.log(
            job["id"],
            f"已选择可用的 {request.platform} 采集账号",
            stage="account",
        )
    if relation_context and relation_parent_result:
        relation = job_store.upsert_comment_user_relation(
            analysis_job_id=job["id"],
            relation_context=relation_context,
            parent_audit_result=relation_parent_result,
        )
        if relation:
            job_store.log(
                job["id"],
                "已写入重点用户与疑似关联账号关系，任务创建成功后标记为已创建分析任务。",
            )
    pipeline = AuditPipeline(job_id=job["id"])
    background_tasks.add_task(pipeline.run, request)
    return enrich_job(job_store.get(job["id"]) or job)


@app.post("/api/local-video-jobs")
async def create_local_video_job(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    title: str = Form(""),
    desc: str = Form(""),
    display_name: str = Form(""),
    library_ids: str = Form(""),
    capabilities: str = Form(""),
    scoring_template: str = Form("balanced"),
    rule_snapshot: str = Form(""),
    policy_id: str = Form(""),
):
    suffix = Path(video.filename or "video.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}:
        raise HTTPException(status_code=400, detail="Unsupported video file type")

    parsed_library_ids = parse_form_list(library_ids)
    parsed_capabilities = parse_form_list(capabilities)
    parsed_rule_snapshot = parse_form_dict(rule_snapshot)
    force_composite = bool(parsed_library_ids or parsed_capabilities or parsed_rule_snapshot)
    revision_payload = None
    try:
        if policy_id:
            policy = audit_policy_store.get(policy_id)
            if not policy:
                raise HTTPException(status_code=404, detail="Audit policy not found")
            revision_payload = build_revision_payload_from_policy(policy)
            context = revision_payload["context"]
            force_composite = True
        else:
            context = prepare_prompt_context(
                library_ids=parsed_library_ids,
                lexicon_category="soft",
                capabilities=parsed_capabilities,
                scoring_template=scoring_template,
                rule_snapshot=parsed_rule_snapshot,
                force_composite=force_composite,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Lexicon category not found: {exc}")
    primary_category = context["library_ids"][0] if context["library_ids"] else "soft"

    job = job_store.create(
        platform="local",
        display_name=display_name.strip() or title or video.filename or "本地视频审核",
        keyword=title or video.filename or "local video",
        lexicon_category=primary_category,
        library_ids=context["library_ids"],
        capabilities=context["capabilities"],
        scoring_template=context["scoring_template"],
        rule_snapshot=context["rule_snapshot"],
        prompt_profile_snapshot=context["prompt_profile_snapshot"],
        run_crawler=False,
        source_output_id=None,
        analyze_limit=1,
        analysis_batch_size=1,
        input_type="local_video",
        input_filename=video.filename,
    )
    if revision_payload is None:
        revision_payload = build_audit_config_revision_payload(
            source_policy_id="",
            source_policy_name="自定义审核配置",
            source_policy_version="",
            library_ids=context["library_ids"],
            lexicon_category=primary_category,
            capabilities=context["capabilities"],
            scoring_template=context["scoring_template"],
            rule_snapshot=context["rule_snapshot"],
            force_composite=force_composite,
        )
    revision = create_revision_from_payload(
        job["id"],
        revision_payload,
        created_by="api",
    )
    job_store.log(
        job["id"],
        f"生成任务审核配置 Revision {revision.get('version')}：{revision.get('source_policy_name') or '自定义审核配置'}",
    )
    upload_dir = settings.outputs_dir / job["id"] / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    video_path = upload_dir / f"input{suffix}"
    with video_path.open("wb") as f:
        while chunk := await video.read(1024 * 1024):
            f.write(chunk)

    pipeline = AuditPipeline(job_id=job["id"])
    background_tasks.add_task(pipeline.run_local_video, video_path, title, desc)
    return enrich_job(job_store.get(job["id"]) or job)


@app.get("/api/jobs")
def list_jobs():
    return [enrich_job(job) for job in job_store.list_summaries()]


@app.get("/api/job-policy-references")
def list_job_policy_references():
    return {"items": job_store.list_policy_references()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = job_store.get_summary(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return enrich_job(job)


@app.get("/api/jobs/{job_id}/audit-config-revisions")
def list_job_audit_config_revisions(job_id: str):
    if not job_store.exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"items": audit_config_revision_store.list_for_job(job_id)}


@app.get("/api/jobs/{job_id}/audit-config-revisions/{revision_id}")
def get_job_audit_config_revision(job_id: str, revision_id: str):
    if not job_store.exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    revision = audit_config_revision_store.get_for_job(job_id, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Audit config revision not found")
    return revision


@app.post("/api/jobs/{job_id}/audit-config-revisions")
def create_job_audit_config_revision(job_id: str, request: AuditConfigRevisionRequest):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    policy = audit_policy_store.get(request.policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Audit policy not found")
    try:
        payload = build_revision_payload_from_policy(policy)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Lexicon category not found: {exc}")
    revision = create_revision_from_payload(
        job_id,
        payload,
        created_by="api",
    )
    job_store.log(
        job_id,
        f"更新审核策略：Revision {revision.get('version')} · {revision.get('source_policy_name')} {revision.get('source_policy_version')}",
    )
    return {"job": enrich_job(job_store.get(job_id) or job), "revision": revision}


@app.post("/api/jobs/{job_id}/control")
def control_job(job_id: str, request: JobControlRequest, background_tasks: BackgroundTasks):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    stats = ingestion_store.stats_for_task(job_id)
    actions = available_job_actions(job, stats)

    if request.action == "pause_crawl":
        if not actions["pause_crawl"]:
            raise HTTPException(status_code=409, detail="当前任务状态不允许暂停采集")
        job_store.update_control(job_id, crawl_stop_requested=True)
        job_store.update(job_id, status="crawl_pausing", crawl_status="pausing")
        job_store.log(job_id, "收到控制指令：在安全检查点暂停采集", stage="control")
    elif request.action == "resume_crawl":
        if not actions["resume_crawl"]:
            raise HTTPException(status_code=409, detail="当前任务状态不允许继续采集")
        crawl_request = crawl_request_from_job(job)
        preferred_account_id = (
            ((job.get("control") or {}).get("execution_account") or {}).get("id")
            or crawl_request.crawler_account_id
        )
        if preferred_account_id and not crawler_account_store.available_accounts(crawl_request.platform):
            raise HTTPException(
                status_code=409,
                detail="当前没有结束冷却且已登录的可用采集账号，请稍后重试或补充账号。",
            )
        bound_run = investigation_creation_store.get_run_for_job(job_id)
        if bound_run and bound_run.status == RunStatus.FAILED:
            failure = failure_from_job(job)
            if not investigation_creation_store.begin_recoverable_crawl_for_job(
                job_id,
                failure_code=str(failure.get("code") or ""),
                allowed_error_codes=RECOVERABLE_CRAWL_FAILURE_CODES,
            ):
                raise HTTPException(status_code=409, detail="该调查失败原因不支持继续采集")
        crawl_epoch = int((job.get("control") or {}).get("crawl_epoch") or 0) + 1
        job_store.update_control(
            job_id,
            crawl_stop_requested=False,
            stop_all_requested=False,
            crawl_epoch=crawl_epoch,
            failure={},
        )
        job_store.update(job_id, status="queued", crawl_status="queued", error="")
        job_store.log(job_id, "收到控制指令：从采集检查点继续", stage="control")
        pipeline = AuditPipeline(job_id=job_id)
        background_tasks.add_task(
            ResumeJobTask(job_id, pipeline.run).run,
            crawl_request,
            crawl_epoch,
        )
    elif request.action == "pause_analysis":
        if not actions["pause_analysis"]:
            raise HTTPException(status_code=409, detail="当前任务状态不允许暂停分析")
        job_store.update_control(job_id, analysis_paused=True, analysis_stop_requested=False)
        job_store.update(job_id, status="analysis_pausing", analysis_status="pausing")
        job_store.log(job_id, "收到控制指令：在当前内容完成后暂停分析", stage="control")
    elif request.action == "stop_analysis":
        if not actions["stop_analysis"]:
            raise HTTPException(status_code=409, detail="当前任务状态不允许停止分析")
        job_store.update_control(job_id, analysis_paused=False, analysis_stop_requested=True)
        job_store.update(job_id, status="analysis_stopping", analysis_status="stopping")
        job_store.log(job_id, "收到控制指令：安全停止分析，已完成内容不会重复", stage="control")
    elif request.action == "resume_analysis":
        if not actions["resume_analysis"]:
            raise HTTPException(status_code=409, detail="当前任务状态不允许继续分析")
        job_store.update_control(
            job_id,
            analysis_paused=False,
            analysis_stop_requested=False,
            stop_all_requested=False,
        )
        crawl_is_active = str(job.get("crawl_status") or "") in {"queued", "running", "pausing"}
        job_store.update(
            job_id,
            status="running" if crawl_is_active else "analysis_running",
            analysis_status="running",
        )
        job_store.log(job_id, "收到控制指令：继续处理待分析内容", stage="control")
        if not crawl_is_active or not job.get("auto_analyze", True):
            pipeline = AuditPipeline(job_id=job_id)
            background_tasks.add_task(
                ResumeJobTask(job_id, pipeline.resume_pending_analysis).run,
                request.analyze_limit or int(job.get("analyze_limit") or 0),
                int(job.get("analysis_batch_size") or 5),
            )
    elif request.action == "stop_all":
        job_store.update_control(
            job_id,
            crawl_stop_requested=True,
            analysis_paused=False,
            analysis_stop_requested=True,
            stop_all_requested=True,
        )
        job_store.update(job_id, status="stopping", crawl_status="stopping", analysis_status="stopping")
        job_store.log(job_id, "收到控制指令：停止全部", stage="control")
    elif request.action == "backfill_analysis":
        job_store.log(job_id, "收到控制指令：继续分析")
        if job.get("status") == "analysis_stopping":
            job_store.update_control(
                job_id,
                analysis_paused=False,
                analysis_stop_requested=False,
                stop_all_requested=False,
            )
            job_store.update(job_id, status="running", analysis_status="running")
            job_store.log(job_id, "已取消停止分析请求，当前分析将继续")
            return enrich_job(job_store.get(job_id))
        if job.get("status") in {"queued", "running", "crawl_pausing", "stopping", "analysis_running"}:
            raise HTTPException(status_code=409, detail="任务仍在运行或停止中，暂不能启动继续分析")
        job_store.update_control(
            job_id,
            analysis_paused=False,
            analysis_stop_requested=False,
            stop_all_requested=False,
        )
        pipeline = AuditPipeline(job_id=job_id)
        background_tasks.add_task(
            pipeline.resume_pending_analysis,
            request.analyze_limit or int(job.get("analyze_limit") or 0),
            int(job.get("analysis_batch_size") or 5),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported action: {request.action}")

    return enrich_job(job_store.get(job_id))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job_store.update_control(
        job_id,
        crawl_stop_requested=True,
        analysis_paused=False,
        analysis_stop_requested=True,
        stop_all_requested=True,
    )
    if job.get("status") not in {"completed", "failed", "stopped", "interrupted"}:
        job_store.update(job_id, status="stopping")
    job_store.log(job_id, "收到控制指令：删除任务及关联分析帖子")
    job_store.archive(job_id)
    deleted_result_count = audit_result_store.delete_for_job(job_id)
    return {"ok": True, "id": job_id, "deleted_result_count": deleted_result_count}


@app.get("/api/jobs/{job_id}/items")
def get_job_items(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"items": job.get("items", [])}


@app.get("/api/jobs/{job_id}/audit-results")
def get_job_audit_results(
    job_id: str,
    after_id: int = 0,
    offset: int = 0,
    limit: int = 500,
    decision: str = "",
    risk_level: str = "",
    author_key: str = "",
    keyword: str = "",
    sort: str = "id",
    compact: bool = False,
):
    if not job_store.exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return audit_result_store.list_results(
        job_id,
        after_id=max(0, after_id),
        offset=max(0, offset),
        limit=limit,
        decision=decision,
        risk_level=risk_level,
        author_key=author_key,
        keyword=keyword,
        sort=sort,
        compact=compact,
    )


@app.get("/api/audit-results")
def list_audit_results(
    job_id: str = "",
    after_id: int = 0,
    offset: int = 0,
    limit: int = 500,
    decision: str = "",
    risk_level: str = "",
    author_key: str = "",
    keyword: str = "",
    sort: str = "latest",
    compact: bool = False,
):
    return audit_result_store.list_results(
        job_id,
        after_id=max(0, after_id),
        offset=max(0, offset),
        limit=limit,
        decision=decision,
        risk_level=risk_level,
        author_key=author_key,
        keyword=keyword,
        sort=sort,
        compact=compact,
    )


@app.get("/api/audit-results/{result_id}")
def get_audit_result_detail(result_id: int):
    item = audit_result_store.get_result(result_id)
    if not item:
        raise HTTPException(status_code=404, detail="Audit result not found")
    item = _sanitize_audit_result_media(item)
    revision_id = str(item.get("audit_config_revision_id") or "")
    revision = audit_config_revision_store.get(revision_id) if revision_id else None
    job = job_store.get_summary(str(item.get("job_id") or ""), log_limit=0) if item.get("job_id") else None
    evidence_groups = build_evidence_groups(item)
    current_revision_id = str((job or {}).get("current_audit_config_revision_id") or "")
    return {
        "audit_result": item,
        "audit_config_revision": revision or {},
        "evidence_groups": evidence_groups,
        "is_historical_config": bool(revision_id and current_revision_id and revision_id != current_revision_id),
    }


@app.patch("/api/audit-results/{result_id}/review")
def review_audit_result(result_id: int, request: AuditResultReviewRequest):
    try:
        item = audit_result_store.review_result(
            result_id,
            status=request.status,
            note=request.note,
            reviewer=request.reviewer,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Audit result not found")
    item = _sanitize_audit_result_media(item)
    evidence_groups = build_evidence_groups(item)
    revision_id = str(item.get("audit_config_revision_id") or "")
    revision = audit_config_revision_store.get(revision_id) if revision_id else None
    return {
        "audit_result": item,
        "audit_config_revision": revision or {},
        "evidence_groups": evidence_groups,
    }


@app.get("/api/jobs/{job_id}/assets")
def get_job_asset(job_id: str, path: str):
    if not job_store.exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    job_root = (settings.outputs_dir / job_id).resolve()
    target = (job_root / path).resolve()
    try:
        target.relative_to(job_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid asset path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(target)
