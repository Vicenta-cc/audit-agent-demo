from __future__ import annotations

import json
from dataclasses import dataclass
from threading import RLock
from typing import Any

from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)
from backend.rulesets.contracts import RuleSetContent

from .contracts import (
    ConfirmAndQueueCommand,
    CreateDraftCommand,
    InvestigationDraftConfiguration,
    QueryInvestigationOptions,
    StrictModel,
    UpdateDraftCommand,
)
from .errors import IdempotencyConflictError
from .principal import Principal


@dataclass(frozen=True)
class HermesToolExecutionIdentity:
    """Stable identity supplied by Hermes at the tool-execution boundary."""

    session_id: str
    turn_id: str
    tool_call_id: str

    @classmethod
    def require(
        cls,
        *,
        session_id: str,
        turn_id: str,
        tool_call_id: str,
    ) -> "HermesToolExecutionIdentity":
        identity = cls(
            session_id=str(session_id or "").strip(),
            turn_id=str(turn_id or "").strip(),
            tool_call_id=str(tool_call_id or "").strip(),
        )
        if not identity.session_id or not identity.turn_id or not identity.tool_call_id:
            raise IdempotencyConflictError(
                "durable tool execution identity is required",
                code="TOOL_EXECUTION_IDENTITY_REQUIRED",
            )
        return identity


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


class CreateRuleSetProposalInput(StrictModel):
    content: RuleSetContent


class GetRuleSetProposalInput(StrictModel):
    proposal_id: str = Field(min_length=1, max_length=160)


class UpdateRuleSetProposalInput(GetRuleSetProposalInput):
    expected_version: StrictInt = Field(ge=1)
    content: RuleSetContent


M3_TOOL_INPUTS: dict[str, type[StrictModel]] = {
    "query_investigation_options": QueryInvestigationOptions,
    "create_investigation_draft": CreateInvestigationDraftInput,
    "update_investigation_draft": UpdateInvestigationDraftInput,
    "get_investigation_draft": GetInvestigationDraftInput,
    "confirm_and_queue_investigation": ConfirmAndQueueInvestigationInput,
    "get_investigation_run": GetInvestigationRunInput,
    "create_ruleset_proposal": CreateRuleSetProposalInput,
    "update_ruleset_proposal": UpdateRuleSetProposalInput,
    "get_ruleset_proposal": GetRuleSetProposalInput,
}

M3_MUTATION_TOOL_NAMES = frozenset(
    {
        "create_ruleset_proposal",
        "update_ruleset_proposal",
        "create_investigation_draft",
        "update_investigation_draft",
        "confirm_and_queue_investigation",
    }
)


M3_TOOL_DESCRIPTIONS = {
    "create_ruleset_proposal": (
        "Author a temporary candidate RuleSet directly as canonical RuleSetContent JSON when "
        "the user asks to generate rules or another candidate. Application validates, compiles "
        "and persists it in this Session. Generation is not approval or use: it never binds "
        "Draft Judgement, publishes or saves a formal RuleSet. No prior options query is required. "
        "Temporary terms and a Proposal may both be generated in the same turn. "
        "Choose each rule's stages by where its risk can independently appear: image_evidence "
        "for image text/visual rules, video_frame_evidence for video/OCR/ASR rules, comment_audit "
        "for comments themselves, fusion_audit for judgment using existing evidence. Rules supporting "
        "a final finding must explicitly include fusion_audit; the compiler does not add it. Do not default "
        "every rule to comment + fusion or to all four stages. Both exemption types remove risk; "
        "use only conditions that negate it, never identity/certification/reputation alone."
    ),
    "update_ruleset_proposal": (
        "Update a current Session Proposal using proposal_id, expected_version and the complete "
        "new canonical RuleSetContent. Application maintains content_hash. Same content is a no-op. "
        "On stale version, read current content before editing; never assume an automatic merge. "
        "Preserve category_id, rule_id and ordering for unchanged semantics. This never approves, "
        "uses or publishes the Proposal. Choose revised rules' application_stages by the evidence "
        "modalities that can independently show their risk: image_evidence, video_frame_evidence "
        "(including OCR/ASR), comment_audit (comments themselves), fusion_audit (existing evidence). "
        "Rules supporting final findings must explicitly include fusion_audit; it is not added automatically. "
        "Do not mechanically assign comment + fusion or all four stages. general_exemptions and "
        "rule_exemptions remove risk, not just confidence; only use conditions that negate risk, "
        "never identity/certification/reputation alone."
    ),
    "get_ruleset_proposal": (
        "Read the full current temporary RuleSet Proposal in this Session, including its version "
        "and canonical content, before inspection or editing. This is read-only."
    ),
    "query_investigation_options": (
        "Query currently available real platforms, published RuleSetRevisions, independent "
        "recall lexicons, and blockers. Use it to discover, explain, "
        "compare, recommend, or configure resources. For search, request enabled main terms "
        "only for explicit candidate lexicon IDs. Request RuleSet categories, rules, hit "
        "conditions, exemptions, adjudication notes, and application stages only for exact "
        "published revision IDs. This query never creates a Draft, Run, or Job."
    ),
    "create_investigation_draft": (
        "Create an editable Investigation Draft when the user wants the currently defined "
        "configuration captured as an investigation. Use current real resource candidates. "
        "Select a published RuleSetRevision directly as judgement. "
        "Search Drafts prefer a suitable real existing_lexicon ID, hash, and enabled_main_terms "
        "snapshot. If none is sufficiently suitable and the conversation authorizes generating "
        "missing Recall, supply focused canonical strings directly in temporary_terms.terms; "
        "otherwise explain the gap and propose generation without creating a Draft or listing "
        "any candidate/example search terms in the reply. Never generate "
        "variants or query_type. source_lexicon_ids are write-time checked provenance, not runtime "
        "dependencies. Temporary terms are not saved as a formal Lexicon. "
        "Creator Drafts must contain only a validated creator homepage URL and no "
        "recall plan. This never confirms or starts a Run."
    ),
    "update_investigation_draft": (
        "Update an editable Investigation Draft at its expected revision. User changes "
        "to platform, RuleSet judgement, lexicon, or terms are allowed before confirmation; edited "
        "lexicon terms must be represented as temporary_terms. Authorized generation of missing "
        "Recall supplies canonical terms directly, without variants, query_type, or formal save. "
        "Changed source_lexicon_ids must reference real Lexicons; unchanged provenance needs no "
        "resource refresh. Each temporary term must be comma-free. This command never confirms "
        "or starts an investigation."
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


def _creation_tool_schema(schema: type[StrictModel]) -> dict[str, Any]:
    parameters = schema.model_json_schema()

    def remove_legacy_platform(node: Any) -> None:
        if isinstance(node, dict):
            enum = node.get("enum")
            if isinstance(enum, list) and "wb" in enum:
                node["enum"] = [value for value in enum if value != "wb"]
            for value in node.values():
                remove_legacy_platform(value)
        elif isinstance(node, list):
            for value in node:
                remove_legacy_platform(value)

    remove_legacy_platform(parameters)
    return parameters


HERMES_M3_TOOL_SCHEMAS = tuple(
    {
        "name": name,
        "description": M3_TOOL_DESCRIPTIONS[name],
        "parameters": _creation_tool_schema(schema),
    }
    for name, schema in M3_TOOL_INPUTS.items()
)


class InvestigationCreationToolService:
    """Hermes-facing adapter over stable M3 Application commands and queries."""

    def __init__(self, application_service: Any) -> None:
        self.application_service = application_service
        self._conversation_turns: dict[str, str] = {}
        self._conversation_lock = RLock()

    def begin_conversation_turn(self, session_id: str, turn_id: str) -> None:
        with self._conversation_lock:
            if session_id in self._conversation_turns:
                raise RuntimeError("Session already has an executing conversation turn")
            self._conversation_turns[session_id] = turn_id

    def end_conversation_turn(self, session_id: str) -> None:
        with self._conversation_lock:
            self._conversation_turns.pop(session_id, None)

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
        session_id: str = "",
    ) -> dict[str, Any]:
        schema = M3_TOOL_INPUTS.get(tool_name)
        if schema is None:
            raise ValueError("Hermes M3 tool name is not allowed")
        parsed = schema.model_validate(arguments)
        if tool_name == "create_ruleset_proposal":
            result = self.application_service.create_ruleset_proposal(
                parsed.content, session_id=session_id
            )
        elif tool_name == "update_ruleset_proposal":
            result = self.application_service.update_ruleset_proposal(
                parsed.proposal_id, session_id=session_id,
                expected_version=parsed.expected_version, content=parsed.content,
            )
        elif tool_name == "get_ruleset_proposal":
            result = self.application_service.get_ruleset_proposal(
                parsed.proposal_id, session_id=session_id
            )
        elif tool_name == "query_investigation_options":
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

    def execute_with_identity(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        principal: Principal,
        identity: HermesToolExecutionIdentity,
    ) -> dict[str, Any]:
        identity = HermesToolExecutionIdentity.require(
            session_id=identity.session_id,
            turn_id=identity.turn_id,
            tool_call_id=identity.tool_call_id,
        )
        raw_arguments = dict(arguments or {})
        if tool_name in M3_MUTATION_TOOL_NAMES:
            try:
                M3_TOOL_INPUTS[tool_name].model_validate(raw_arguments)
            except ValidationError as exc:
                details: dict[str, Any] = {
                    "receipt_created": False,
                    "mutation_applied": False,
                    "retryable": True,
                    "validation_errors": [
                        {
                            "loc": list(error["loc"]),
                            "type": error["type"],
                            "message": error["msg"],
                        }
                        for error in exc.errors(
                            include_url=False,
                            include_context=False,
                            include_input=False,
                        )
                    ],
                    "recovery": (
                        "Correct the arguments using the Tool schema and retry "
                        f"{tool_name}."
                    ),
                }
                if tool_name == "create_investigation_draft":
                    details.update(
                        {
                            "draft_created": False,
                            "recovery": (
                                "Draft was not created. Correct the arguments using the "
                                "Tool schema and retry create_investigation_draft."
                            ),
                        }
                    )
                return {
                    "status": "error",
                    "error": {
                        "code": "INVALID_TOOL_ARGUMENTS",
                        "message": "Tool arguments do not match the required schema.",
                        "details": details,
                    },
                }
        receipt: dict[str, Any] | None = None
        try:
            if tool_name in M3_MUTATION_TOOL_NAMES:
                with self._conversation_lock:
                    application_turn_id = (
                        self._conversation_turns.get(identity.session_id, "")
                        if tool_name in {"create_ruleset_proposal", "update_ruleset_proposal"} else ""
                    )
                receipt = self.application_service.store.begin_tool_execution(
                    session_id=identity.session_id,
                    turn_id=identity.turn_id,
                    tool_call_id=identity.tool_call_id,
                    principal=principal.id,
                    tool_name=tool_name,
                    arguments=raw_arguments,
                    is_mutation=True,
                    application_turn_id=application_turn_id,
                )
                if receipt["replay"]:
                    if receipt["response"] is not None:
                        return dict(receipt["response"])
                    return {
                        "status": "error",
                        "error": {
                            "code": "MUTATION_RESULT_UNKNOWN",
                            "message": (
                                "The mutation may already have executed and was not replayed. "
                                "Query Application state before taking another action."
                            ),
                        },
                    }
            payload = self.execute(
                tool_name,
                raw_arguments,
                principal=principal,
                session_id=identity.session_id,
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
        if receipt is not None:
            self.application_service.store.complete_tool_execution(
                receipt["receipt_id"],
                response=result,
                succeeded=result["status"] == "ok",
            )
        return result


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
    tool_name: str,
    arguments: dict[str, Any],
    *,
    session_id: str = "",
    turn_id: str = "",
    tool_call_id: str = "",
) -> str:
    identity: HermesToolExecutionIdentity | None = None
    if tool_name in M3_MUTATION_TOOL_NAMES:
        try:
            identity = HermesToolExecutionIdentity.require(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
            )
        except Exception as exc:
            return _tool_error_json(exc)
    return dispatch_hermes_investigation_creation_tool_with_identity(
        tool_name,
        arguments,
        session_id=session_id,
        identity=identity,
    )


def dispatch_hermes_investigation_creation_tool_with_identity(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    session_id: str,
    identity: HermesToolExecutionIdentity | None,
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
        if tool_name in M3_MUTATION_TOOL_NAMES and identity is None:
            raise IdempotencyConflictError(
                "durable tool execution identity is required",
                code="TOOL_EXECUTION_IDENTITY_REQUIRED",
            )
        if (
            identity is not None
            and identity.session_id != str(session_id or "").strip()
        ):
            raise IdempotencyConflictError(
                "tool execution identity does not match the dispatched Session"
            )
        try:
            principal = principal_provider(session_id)
        except TypeError:
            principal = principal_provider()
        if identity is None:
            result = {
                "status": "ok",
                "data": tool_service.execute(
                    tool_name,
                    dict(arguments or {}),
                    principal=principal,
                    session_id=session_id,
                ),
            }
        else:
            result = tool_service.execute_with_identity(
                tool_name,
                dict(arguments or {}),
                principal=principal,
                identity=identity,
            )
    except Exception as exc:
        return _tool_error_json(exc)
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def _tool_error_json(exc: Exception) -> str:
    return json.dumps(
        {
            "status": "error",
            "error": {
                "code": str(getattr(exc, "code", "TOOL_EXECUTION_FAILED")),
                "message": str(exc),
                "details": dict(getattr(exc, "details", {}) or {}),
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )
