from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator
from backend.resource_management.contracts import ResourceError

from backend.api.contracts import (
    PublicInvestigationDraft,
    PublicInvestigationRunProjection,
)
from backend.investigation_creation.public_projection import public_draft, public_run

from backend.investigation_creation.contracts import (
    ConfirmAndQueueCommand,
    ConfirmationPreview,
    CreateDraftCommand,
    InvestigationDraftConfiguration,
    InvestigationOptions,
    Platform,
    QueryInvestigationOptions,
    UpdateDraftCommand,
)
from backend.investigation_creation.errors import (
    ConfigurationValidationError,
    ConfirmationRequiredError,
    DraftAlreadyConfirmedError,
    DraftNotFoundError,
    DraftRevisionConflictError,
    IdempotencyConflictError,
    InvestigationCreationError,
    ResourceStaleError,
    RunNotFoundError,
    PrincipalAccessDeniedError,
)
from backend.investigation_creation.principal import (
    LocalPrincipalProvider,
    Principal,
    PrincipalProvider,
)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateDraftRequest(RequestModel):
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=4_000)
    configuration: InvestigationDraftConfiguration

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UpdateDraftRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, min_length=1, max_length=4_000)
    configuration: InvestigationDraftConfiguration | None = None

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ConfirmAndQueueRequest(RequestModel):
    expected_task_settings_revision: int | None = Field(default=None, ge=0)
    expected_revision: int = Field(ge=1)
    confirmed: StrictBool


class SaveDraftLexiconRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=200)


def create_investigation_creation_router(
    service: Any,
    *,
    principal_provider: PrincipalProvider | None = None,
) -> APIRouter:
    router = APIRouter(tags=["investigation-creation"])
    provide_principal = principal_provider or LocalPrincipalProvider()

    @router.get(
        "/api/investigation-options",
        response_model=InvestigationOptions,
    )
    def query_options(
        domain_hint: str = Query(default="", max_length=200),
        mode: Literal["search", "creator"] = Query(default="search"),
        platform: Platform | None = Query(default=None),
        ruleset_revision_ids: list[str] = Query(default=[]),
        lexicon_ids: list[str] = Query(default=[]),
        include_lexicon_terms_for_ids: list[str] = Query(default=[]),
        include_ruleset_details_for_revision_ids: list[str] = Query(default=[]),
        page_size: int = Query(default=20, ge=1, le=50),
        lexicon_term_limit: int = Query(default=50, ge=1, le=100),
        cursor: str = Query(default="", max_length=40),
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationOptions:
        try:
            return service.query_investigation_options(
                QueryInvestigationOptions(
                    domain_hint=domain_hint,
                    mode=mode,
                    platform=platform,
                    ruleset_revision_ids=ruleset_revision_ids,
                    lexicon_ids=lexicon_ids,
                    include_lexicon_terms_for_ids=include_lexicon_terms_for_ids,
                    include_ruleset_details_for_revision_ids=(
                        include_ruleset_details_for_revision_ids
                    ),
                    page_size=page_size,
                    lexicon_term_limit=lexicon_term_limit,
                    cursor=cursor,
                ),
                principal=principal,
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-drafts",
        response_model=PublicInvestigationDraft,
        status_code=201,
    )
    def create_draft(
        request: CreateDraftRequest,
        principal: Principal = Depends(provide_principal),
    ) -> PublicInvestigationDraft:
        try:
            return public_draft(
                service.create_draft(
                    CreateDraftCommand(**request.model_dump()), principal=principal
                )
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.patch(
        "/api/investigation-drafts/{draft_id}",
        response_model=PublicInvestigationDraft,
    )
    def update_draft(
        draft_id: str,
        request: UpdateDraftRequest,
        principal: Principal = Depends(provide_principal),
    ) -> PublicInvestigationDraft:
        try:
            return public_draft(
                service.update_draft(
                    UpdateDraftCommand(draft_id=draft_id, **request.model_dump()),
                    principal=principal,
                )
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-drafts/{draft_id}",
        response_model=PublicInvestigationDraft,
    )
    def get_draft(
        draft_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> PublicInvestigationDraft:
        try:
            return public_draft(service.get_draft(draft_id, principal=principal))
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-drafts/{draft_id}/confirmation-preview",
        response_model=ConfirmationPreview,
    )
    def get_confirmation_preview(
        draft_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> ConfirmationPreview:
        try:
            return service.get_confirmation_preview(
                draft_id, principal=principal
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-drafts/{draft_id}/save-lexicon",
    )
    def save_draft_lexicon(
        draft_id: str,
        request: SaveDraftLexiconRequest,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, Any]:
        try:
            return service.save_draft_lexicon(
                draft_id,
                expected_revision=request.expected_revision,
                operation_id=request.operation_id,
                principal=principal,
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-drafts/{draft_id}/confirm-and-queue",
        response_model=PublicInvestigationRunProjection,
        status_code=202,
    )
    def confirm_and_queue(
        draft_id: str,
        request: ConfirmAndQueueRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
        principal: Principal = Depends(provide_principal),
    ) -> PublicInvestigationRunProjection:
        try:
            run = service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=draft_id,
                    idempotency_key=idempotency_key,
                    **request.model_dump(),
                ),
                principal=principal,
            )
            return public_run(service.get_run(run.id, principal=principal))
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-runs/{run_id}",
        response_model=PublicInvestigationRunProjection,
    )
    def get_run(
        run_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> PublicInvestigationRunProjection:
        try:
            return public_run(service.get_run(run_id, principal=principal))
        except Exception as exc:
            _raise_public_error(exc)

    return router


def _raise_public_error(exc: Exception) -> None:
    from backend.task_admission.store import AdmissionError
    if isinstance(exc, AdmissionError):
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc), "details": exc.details}) from exc
    if isinstance(exc, ResourceError):
        detail = {"code": exc.code, "message": str(exc), "details": exc.details}
        if exc.code == "RESOURCE_FORBIDDEN":
            raise HTTPException(status_code=403, detail=detail) from exc
        if exc.code == "RESOURCE_NOT_FOUND":
            raise HTTPException(status_code=404, detail=detail) from exc
        if "CONFLICT" in exc.code:
            raise HTTPException(status_code=409, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    detail = (
        {
            "code": exc.code,
            "message": str(exc),
            "details": exc.details,
        }
        if isinstance(exc, InvestigationCreationError)
        else str(exc)
    )
    if isinstance(exc, (DraftNotFoundError, RunNotFoundError)):
        raise HTTPException(status_code=404, detail=detail) from exc
    if isinstance(
        exc,
        (
            DraftRevisionConflictError,
            DraftAlreadyConfirmedError,
            IdempotencyConflictError,
            ResourceStaleError,
        ),
    ):
        raise HTTPException(status_code=409, detail=detail) from exc
    if isinstance(exc, PrincipalAccessDeniedError):
        raise HTTPException(status_code=404, detail="resource was not found") from exc
    if isinstance(exc, ValidationError):
        # Validator contexts can contain ValueError objects, which JSONResponse
        # cannot serialize. The location/type/message remain sufficient to report it.
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc
    if isinstance(exc, (ConfirmationRequiredError, ConfigurationValidationError, ValueError)):
        raise HTTPException(status_code=400, detail=detail) from exc
    raise exc
