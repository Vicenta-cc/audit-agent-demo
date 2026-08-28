from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from backend.investigation_creation.contracts import (
    ConfirmAndQueueCommand,
    ConfirmationPreview,
    CreateDraftCommand,
    DraftConfiguration,
    InvestigationDraft,
    InvestigationOptions,
    InvestigationRunProjection,
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
    configuration: DraftConfiguration

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UpdateDraftRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, min_length=1, max_length=4_000)
    configuration: DraftConfiguration | None = None

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ConfirmAndQueueRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    confirmed: StrictBool


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
        platform: Platform | None = Query(default=None),
        audit_policy_ids: list[str] = Query(default=[]),
        lexicon_ids: list[str] = Query(default=[]),
        include_lexicon_terms_for_ids: list[str] = Query(default=[]),
        page_size: int = Query(default=20, ge=1, le=50),
        lexicon_term_limit: int = Query(default=50, ge=1, le=100),
        cursor: str = Query(default="", max_length=40),
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationOptions:
        try:
            return service.query_investigation_options(
                QueryInvestigationOptions(
                    domain_hint=domain_hint,
                    platform=platform,
                    audit_policy_ids=audit_policy_ids,
                    lexicon_ids=lexicon_ids,
                    include_lexicon_terms_for_ids=include_lexicon_terms_for_ids,
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
        response_model=InvestigationDraft,
        status_code=201,
    )
    def create_draft(
        request: CreateDraftRequest,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationDraft:
        try:
            return service.create_draft(
                CreateDraftCommand(**request.model_dump()), principal=principal
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.patch(
        "/api/investigation-drafts/{draft_id}",
        response_model=InvestigationDraft,
    )
    def update_draft(
        draft_id: str,
        request: UpdateDraftRequest,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationDraft:
        try:
            return service.update_draft(
                UpdateDraftCommand(draft_id=draft_id, **request.model_dump()),
                principal=principal,
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-drafts/{draft_id}",
        response_model=InvestigationDraft,
    )
    def get_draft(
        draft_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationDraft:
        try:
            return service.get_draft(draft_id, principal=principal)
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
        "/api/investigation-drafts/{draft_id}/confirm-and-queue",
        response_model=InvestigationRunProjection,
        status_code=202,
    )
    def confirm_and_queue(
        draft_id: str,
        request: ConfirmAndQueueRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationRunProjection:
        try:
            run = service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=draft_id,
                    idempotency_key=idempotency_key,
                    **request.model_dump(),
                ),
                principal=principal,
            )
            return service.get_run(run.id, principal=principal)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-runs/{run_id}",
        response_model=InvestigationRunProjection,
    )
    def get_run(
        run_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationRunProjection:
        try:
            return service.get_run(run_id, principal=principal)
        except Exception as exc:
            _raise_public_error(exc)

    return router


def _raise_public_error(exc: Exception) -> None:
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
        raise HTTPException(status_code=403, detail="resource is not owned by principal") from exc
    if isinstance(exc, ValidationError):
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if isinstance(exc, (ConfirmationRequiredError, ConfigurationValidationError, ValueError)):
        raise HTTPException(status_code=400, detail=detail) from exc
    raise exc
