from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.investigation_creation.principal import (
    LocalPrincipalProvider,
    Principal,
    PrincipalProvider,
)

from .compiler import COMPILER_VERSION, SYSTEM_TEMPLATE_VERSION
from .contracts import RuleSetContent
from .errors import (
    RuleSetError,
    RuleSetForbiddenError,
    RuleSetIdempotencyConflictError,
    RuleSetNotFoundError,
    RuleSetRevisionConflictError,
    RuleSetRevisionNotFoundError,
    RuleSetValidationError,
)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRuleSetRequest(RequestModel):
    id: str = Field(default="", max_length=160)
    content: RuleSetContent


class UpdateRuleSetDraftRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    content: RuleSetContent


class PublishRuleSetRequest(RequestModel):
    expected_revision: int = Field(ge=1)


class CompilePreviewRequest(RequestModel):
    policy_id: str = ""
    policy_name: str = "编译预览"
    policy_version: str = "preview"
    capabilities: list[str] = Field(
        default_factory=lambda: ["text", "ocr", "asr", "vision", "comment"]
    )
    scoring_template: str = "balanced"
    thresholds: dict[str, int] = Field(
        default_factory=lambda: {"high": 80, "medium": 60, "review": 40}
    )
    system_template_version: str = SYSTEM_TEMPLATE_VERSION
    compiler_version: str = COMPILER_VERSION


class ForkRuleSetRequest(RequestModel):
    id: str = Field(default="", max_length=160)


def create_ruleset_router(
    service: Any,
    *,
    principal_provider: PrincipalProvider | None = None,
) -> APIRouter:
    router = APIRouter(tags=["rulesets"])
    provide_principal = principal_provider or LocalPrincipalProvider()

    @router.post("/api/rulesets", status_code=201)
    def create_ruleset(
        request: CreateRuleSetRequest,
        principal: Principal = Depends(provide_principal),
    ) -> dict:
        try:
            return service.create_draft(
                request.content,
                principal=principal,
                ruleset_id=request.id,
            )
        except RuleSetError as exc:
            _raise_public_error(exc)

    @router.get("/api/rulesets")
    def list_rulesets(principal: Principal = Depends(provide_principal)) -> dict:
        try:
            return {"items": service.list(principal=principal)}
        except RuleSetError as exc:
            _raise_public_error(exc)

    @router.get("/api/rulesets/{ruleset_id}")
    def get_ruleset(
        ruleset_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> dict:
        try:
            return service.get(ruleset_id, principal=principal)
        except RuleSetError as exc:
            _raise_public_error(exc)

    @router.patch("/api/rulesets/{ruleset_id}/draft")
    def update_ruleset_draft(
        ruleset_id: str,
        request: UpdateRuleSetDraftRequest,
        principal: Principal = Depends(provide_principal),
    ) -> dict:
        try:
            return service.update_draft(
                ruleset_id,
                expected_revision=request.expected_revision,
                content=request.content,
                principal=principal,
            )
        except RuleSetError as exc:
            _raise_public_error(exc)

    @router.post("/api/rulesets/{ruleset_id}/publish")
    def publish_ruleset(
        ruleset_id: str,
        request: PublishRuleSetRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
        principal: Principal = Depends(provide_principal),
    ) -> dict:
        try:
            return service.publish(
                ruleset_id,
                expected_revision=request.expected_revision,
                idempotency_key=idempotency_key,
                principal=principal,
            )
        except RuleSetError as exc:
            _raise_public_error(exc)

    @router.get("/api/ruleset-revisions")
    def list_ruleset_revisions(
        ruleset_id: str = Query(default=""),
        principal: Principal = Depends(provide_principal),
    ) -> dict:
        try:
            return {
                "items": service.list_published(
                    principal=principal,
                    ruleset_id=ruleset_id,
                )
            }
        except RuleSetError as exc:
            _raise_public_error(exc)

    @router.get("/api/ruleset-revisions/{revision_id}")
    def get_ruleset_revision(
        revision_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> dict:
        try:
            return service.get_published(revision_id, principal=principal)
        except RuleSetError as exc:
            _raise_public_error(exc)

    @router.post("/api/ruleset-revisions/{revision_id}/fork", status_code=201)
    def fork_ruleset_revision(
        revision_id: str,
        request: ForkRuleSetRequest,
        principal: Principal = Depends(provide_principal),
    ) -> dict:
        try:
            return service.fork_published(
                revision_id,
                principal=principal,
                ruleset_id=request.id,
            )
        except RuleSetError as exc:
            _raise_public_error(exc)

    @router.post("/api/ruleset-revisions/{revision_id}/compile-preview")
    def compile_preview(
        revision_id: str,
        request: CompilePreviewRequest,
        principal: Principal = Depends(provide_principal),
    ) -> dict:
        try:
            return service.compile_preview(
                revision_id,
                audit_policy={
                    "policy_id": request.policy_id,
                    "policy_name": request.policy_name,
                    "policy_version": request.policy_version,
                    "ruleset_revision_id": revision_id,
                    "capabilities": request.capabilities,
                    "scoring_template": request.scoring_template,
                    "thresholds": request.thresholds,
                },
                principal=principal,
                system_template_version=request.system_template_version,
                compiler_version=request.compiler_version,
            )
        except RuleSetError as exc:
            _raise_public_error(exc)

    return router


def _raise_public_error(exc: Exception) -> None:
    if isinstance(exc, (RuleSetNotFoundError, RuleSetRevisionNotFoundError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(
        exc,
        (RuleSetRevisionConflictError, RuleSetIdempotencyConflictError),
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, RuleSetForbiddenError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, RuleSetValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc
