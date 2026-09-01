from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from .contracts import (
    AuditPolicySummary,
    ConfirmationPreview,
    DraftConfiguration,
    DraftStatus,
    InvestigationOptions,
    Platform,
    PlatformOption,
    RecallLexiconSummary,
    RuleSetRevisionSummary,
    RunStatus,
    StrictModel,
)


class PublicInvestigationDraft(StrictModel):
    id: str
    status: DraftStatus
    current_revision: int = Field(ge=1)
    title: str
    objective: str
    configuration: DraftConfiguration
    created_at: str
    updated_at: str
    confirmed_revision: int | None = None
    confirmed_at: str = ""


class PublicInvestigationRunProjection(StrictModel):
    run_id: str
    draft_id: str
    draft_revision: int = Field(ge=1)
    status: RunStatus
    job_id: str = ""
    crawl_status: str
    analysis_status: str
    task_stats: dict[str, Any]
    audit_results: list[dict[str, Any]] = Field(default_factory=list)
    report_status: str
    report_version_id: str = ""
    error_code: str = ""
    error_message: str = ""
    created_at: str
    updated_at: str
    started_at: str = ""
    completed_at: str = ""


class InvestigationDraftSuggestion(StrictModel):
    title: str
    objective: str
    mode: Literal["search", "creator"]
    creator_url: str = ""
    platform_options: list[PlatformOption]
    selected_platform: Platform
    search_terms: list[str] = Field(default_factory=list)
    audit_policy: AuditPolicySummary | None = None
    ruleset_revision: RuleSetRevisionSummary | None = None
    recall_lexicons: list[RecallLexiconSummary] = Field(default_factory=list)


class InvestigationDraftArtifact(StrictModel):
    artifact_type: Literal["investigation_draft"] = "investigation_draft"
    presentation_stage: Literal["suggestion", "confirmation"] = "suggestion"
    draft_id: str
    draft_revision: int = Field(ge=1)
    draft: PublicInvestigationDraft
    confirmation_preview: ConfirmationPreview
    suggestion: InvestigationDraftSuggestion | None = None


class InvestigationRunArtifact(StrictModel):
    artifact_type: Literal["investigation_run"] = "investigation_run"
    run_id: str
    run: PublicInvestigationRunProjection


InvestigationConversationArtifact = Annotated[
    InvestigationDraftArtifact | InvestigationRunArtifact,
    Field(discriminator="artifact_type"),
]


def public_draft(draft: Any) -> PublicInvestigationDraft:
    return PublicInvestigationDraft(
        id=draft.id if not isinstance(draft, dict) else draft.get("id"),
        status=draft.status if not isinstance(draft, dict) else draft.get("status"),
        current_revision=(
            draft.current_revision
            if not isinstance(draft, dict)
            else draft.get("current_revision")
        ),
        title=draft.title if not isinstance(draft, dict) else draft.get("title"),
        objective=(
            draft.objective if not isinstance(draft, dict) else draft.get("objective")
        ),
        configuration=(
            draft.configuration
            if not isinstance(draft, dict)
            else draft.get("configuration")
        ),
        created_at=(
            draft.created_at if not isinstance(draft, dict) else draft.get("created_at")
        ),
        updated_at=(
            draft.updated_at if not isinstance(draft, dict) else draft.get("updated_at")
        ),
        confirmed_revision=(
            draft.confirmed_revision
            if not isinstance(draft, dict)
            else draft.get("confirmed_revision")
        ),
        confirmed_at=(
            draft.confirmed_at
            if not isinstance(draft, dict)
            else draft.get("confirmed_at", "")
        ),
    )


def public_run(run: Any) -> PublicInvestigationRunProjection:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(run, dict):
            return run.get(name, default)
        return getattr(run, name, default)

    return PublicInvestigationRunProjection(
        run_id=value("run_id"),
        draft_id=value("draft_id"),
        draft_revision=value("draft_revision"),
        status=value("status"),
        job_id=value("job_id", ""),
        crawl_status=value("crawl_status"),
        analysis_status=value("analysis_status"),
        task_stats=value("task_stats", {}),
        audit_results=value("audit_results", []),
        report_status=value("report_status"),
        report_version_id=value("report_version_id", ""),
        error_code=value("error_code", ""),
        error_message=value("error_message", ""),
        created_at=value("created_at"),
        updated_at=value("updated_at"),
        started_at=value("started_at", ""),
        completed_at=value("completed_at", ""),
    )


def draft_artifact(
    view: Any,
    *,
    options: InvestigationOptions | None = None,
    recommended_lexicon_ids: set[str] | None = None,
    presentation_stage: Literal["suggestion", "confirmation"] = "suggestion",
) -> InvestigationDraftArtifact:
    draft = public_draft(view.draft)
    preview = ConfirmationPreview.model_validate(view.confirmation_preview)
    suggestion = None
    if options is not None:
        suggestion = InvestigationDraftSuggestion(
            title=draft.title,
            objective=draft.objective,
            mode=preview.mode,
            creator_url=preview.creator_url,
            platform_options=[
                option
                for option in options.platforms
                if option.available and option.id is not Platform.WEIBO
            ],
            selected_platform=preview.platform,
            search_terms=list(preview.resolved_search_terms),
            audit_policy=preview.audit_policy,
            ruleset_revision=preview.ruleset_revision,
            recall_lexicons=[
                lexicon
                for lexicon in options.recall_lexicons
                if lexicon.available
                and (
                    recommended_lexicon_ids is None
                    or lexicon.id in recommended_lexicon_ids
                )
            ],
        )
    return InvestigationDraftArtifact(
        presentation_stage=presentation_stage,
        draft_id=draft.id,
        draft_revision=draft.current_revision,
        draft=draft,
        confirmation_preview=preview,
        suggestion=suggestion,
    )


def run_artifact(run: Any) -> InvestigationRunArtifact:
    projection = public_run(run)
    return InvestigationRunArtifact(run_id=projection.run_id, run=projection)
