from __future__ import annotations

import json
from threading import RLock
from typing import Any

from pydantic import Field, StrictBool, field_validator, model_validator

from .contracts import (
    ConfirmAndQueueCommand,
    CreateDraftCommand,
    InvestigationDraftConfiguration,
    QueryInvestigationOptions,
    StrictModel,
    UpdateDraftCommand,
)
from .principal import Principal


class CreateInvestigationDraftInput(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=4_000)
    configuration: InvestigationDraftConfiguration

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UpdateInvestigationDraftInput(StrictModel):
    draft_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, min_length=1, max_length=4_000)
    configuration: InvestigationDraftConfiguration | None = None

    @field_validator("draft_id", "title", "objective", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_change(self) -> "UpdateInvestigationDraftInput":
        if self.title is None and self.objective is None and self.configuration is None:
            raise ValueError("at least one Draft field must be updated")
        return self


class GetInvestigationDraftInput(StrictModel):
    draft_id: str = Field(min_length=1, max_length=160)

    @field_validator("draft_id", mode="before")
    @classmethod
    def strip_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ConfirmAndQueueInvestigationInput(StrictModel):
    draft_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    confirmed: StrictBool
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("draft_id", "idempotency_key", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class GetInvestigationRunInput(StrictModel):
    run_id: str = Field(min_length=1, max_length=160)

    @field_validator("run_id", mode="before")
    @classmethod
    def strip_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


M3_TOOL_INPUTS: dict[str, type[StrictModel]] = {
    "query_investigation_options": QueryInvestigationOptions,
    "create_investigation_draft": CreateInvestigationDraftInput,
    "update_investigation_draft": UpdateInvestigationDraftInput,
    "get_investigation_draft": GetInvestigationDraftInput,
    "confirm_and_queue_investigation": ConfirmAndQueueInvestigationInput,
    "get_investigation_run": GetInvestigationRunInput,
}


M3_TOOL_DESCRIPTIONS = {
    "query_investigation_options": (
        "Query bounded real platform, published AuditPolicy/RuleSet, and recall lexicon "
        "options. Request enabled main terms only for explicit candidate lexicon IDs. "
        "This query never creates a Draft, Run, or Job."
    ),
    "create_investigation_draft": (
        "Create an editable Investigation Draft after discussing recommendations with "
        "the user. This command never confirms or starts an investigation."
    ),
    "update_investigation_draft": (
        "Update an editable Investigation Draft at its expected revision. User changes "
        "to lexicon terms must be represented as temporary_terms. This command never "
        "starts an investigation."
    ),
    "get_investigation_draft": (
        "Read the current Draft and its dynamic confirmation preview. Show that preview "
        "to the user and do not treat it as confirmation."
    ),
    "confirm_and_queue_investigation": (
        "Freeze and queue one Investigation Run only after the user explicitly asks to "
        "confirm and start. Pass confirmed=true; never infer confirmation from a Draft edit."
    ),
    "get_investigation_run": (
        "Read the existing Investigation Run projection. This query is read-only and "
        "never creates a Job, report, or Session."
    ),
}


HERMES_M3_TOOL_SCHEMAS = tuple(
    {
        "name": name,
        "description": M3_TOOL_DESCRIPTIONS[name],
        "parameters": schema.model_json_schema(),
    }
    for name, schema in M3_TOOL_INPUTS.items()
)


class InvestigationCreationToolService:
    """Hermes-facing adapter over stable M3 Application commands and queries."""

    def __init__(self, application_service: Any) -> None:
        self.application_service = application_service

    @property
    def allowed_tool_names(self) -> frozenset[str]:
        return frozenset(M3_TOOL_INPUTS)

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    **schema,
                    "strict": True,
                },
            }
            for schema in HERMES_M3_TOOL_SCHEMAS
        ]

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        principal: Principal,
    ) -> dict[str, Any]:
        schema = M3_TOOL_INPUTS.get(tool_name)
        if schema is None:
            raise ValueError("Hermes M3 tool name is not allowed")
        parsed = schema.model_validate(arguments)
        if tool_name == "query_investigation_options":
            result = self.application_service.query_investigation_options(
                parsed, principal=principal
            )
        elif tool_name == "create_investigation_draft":
            draft = self.application_service.create_draft(
                CreateDraftCommand.model_validate(parsed.model_dump(mode="json")),
                principal=principal,
            )
            result = self.application_service.get_draft_view(
                draft.id, principal=principal
            )
        elif tool_name == "update_investigation_draft":
            draft = self.application_service.update_draft(
                UpdateDraftCommand.model_validate(parsed.model_dump(mode="json")),
                principal=principal,
            )
            result = self.application_service.get_draft_view(
                draft.id, principal=principal
            )
        elif tool_name == "get_investigation_draft":
            result = self.application_service.get_draft_view(
                parsed.draft_id, principal=principal
            )
        elif tool_name == "confirm_and_queue_investigation":
            run = self.application_service.confirm_and_queue(
                ConfirmAndQueueCommand.model_validate(parsed.model_dump(mode="json")),
                principal=principal,
            )
            result = self.application_service.get_run(run.id, principal=principal)
        else:
            result = self.application_service.get_run(
                parsed.run_id, principal=principal
            )
        return result.model_dump(mode="json", warnings=False)


_binding_lock = RLock()
_tool_service: InvestigationCreationToolService | None = None
_principal_provider: Any = None


def configure_hermes_investigation_creation_tools(
    tool_service: InvestigationCreationToolService,
    *,
    principal_provider: Any,
) -> None:
    global _tool_service, _principal_provider
    with _binding_lock:
        _tool_service = tool_service
        _principal_provider = principal_provider


def dispatch_hermes_investigation_creation_tool(
    tool_name: str, arguments: dict[str, Any]
) -> str:
    with _binding_lock:
        tool_service = _tool_service
        principal_provider = _principal_provider
    if tool_service is None or principal_provider is None:
        return json.dumps(
            {
                "status": "error",
                "error": {
                    "code": "APPLICATION_TOOLS_UNBOUND",
                    "message": "Investigation creation tools are not bound to the Application service.",
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    try:
        payload = tool_service.execute(
            tool_name,
            dict(arguments or {}),
            principal=principal_provider(),
        )
        result = {"status": "ok", "data": payload}
    except Exception as exc:
        result = {
            "status": "error",
            "error": {
                "code": str(getattr(exc, "code", "TOOL_EXECUTION_FAILED")),
                "message": str(exc),
                "details": dict(getattr(exc, "details", {}) or {}),
            },
        }
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
