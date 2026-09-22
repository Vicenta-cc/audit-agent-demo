from __future__ import annotations

import json
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

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
    InvestigationDraftView,
    QueryInvestigationOptions,
    StrictModel,
    UpdateDraftCommand,
)
from .errors import IdempotencyConflictError
from .principal import Principal
from .approval import UseRuleSetProposalInput
from backend.resource_management.tools import RESOURCE_TOOL_INPUTS, RESOURCE_MUTATIONS, RESOURCE_DESCRIPTIONS, execute_resource


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
    expected_task_settings_revision: int | None = Field(default=None, ge=0)
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
    "use_ruleset_proposal": UseRuleSetProposalInput,
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
        "use_ruleset_proposal",
        "create_ruleset_proposal",
        "update_ruleset_proposal",
        "create_investigation_draft",
        "update_investigation_draft",
        "confirm_and_queue_investigation",
    }
)


M3_TOOL_DESCRIPTIONS = {
    "use_ruleset_proposal": (
        "You decide whether the CURRENT user explicitly intends adoption; Application validates structural facts, "
        "not natural-language approval. Select presentation_id ONLY from the trusted completed presentation context. "
        "Questions, edits, negation, hesitation, saving, comparison/choice and ambiguous references are not adoption. "
        "For example '就用这套创建招聘诈骗调查，还是先暂停调查' asks a choice: clarify, do NOT call this tool. "
        "Provide draft_id + expected_revision, OR complete create_draft title/objective/configuration "
        "(platform and investigation/Recall, no judgement). Reuse established configuration; ask if missing. "
        "Never guess IDs or automatically select latest. On stale presentation, re-present and WAIT for a new user "
        "turn; never retry the newer snapshot in this turn. On Draft conflict, read and assess changes first; "
        "never just replace expected_revision to force a retry. This binds a temporary Draft, not formal save or execution."
    ),
    "create_ruleset_proposal": (
        "Author a temporary candidate 审核规则 directly as canonical 审核规则完整内容 JSON when "
        "the user asks to generate 审核规则 or another candidate. Application validates, compiles "
        "and persists it in this Session. Generation is not approval or use: it never binds "
        "Draft Judgement, publishes or saves a formal 审核规则. No prior options query is required. "
        "Temporary terms and a Proposal may both be generated in the same turn. "
        "Choose each rule's stages by where its risk can independently appear: image_evidence "
        "for image text/visual 审核规则, video_frame_evidence for video/OCR/ASR 审核规则, comment_audit "
        "for comments themselves, fusion_audit for judgment using existing evidence. 审核规则 supporting "
        "a final finding must explicitly include fusion_audit; the compiler does not add it. Do not default "
        "every rule to comment + fusion or to all four stages. Both exemption types remove risk; "
        "use only conditions that negate it, never identity/certification/reputation alone."
    ),
    "update_ruleset_proposal": (
        "Update a current Session Proposal using proposal_id, expected_version and the complete "
        "new canonical 审核规则完整内容. Application maintains content_hash. Same content is a no-op. "
        "On stale version, read current content before editing; never assume an automatic merge. "
        "Preserve category_id, rule_id and ordering for unchanged semantics. This never approves, "
        "uses or publishes the Proposal. Choose revised 审核规则' application_stages by the evidence "
        "modalities that can independently show their risk: image_evidence, video_frame_evidence "
        "(including OCR/ASR), comment_audit (comments themselves), fusion_audit (existing evidence). "
        "审核规则 supporting final findings must explicitly include fusion_audit; it is not added automatically. "
        "Do not mechanically assign comment + fusion or all four stages. general_exemptions and "
        "rule_exemptions remove risk, not just confidence; only use conditions that negate risk, "
        "never identity/certification/reputation alone."
    ),
    "get_ruleset_proposal": (
        "Read the full current temporary 审核规则 Proposal in this Session, including its version "
        "and canonical content, before inspection or editing. This is read-only."
    ),
    "query_investigation_options": (
        "Query currently available real platforms, published 审核规则已发布版本, independent "
        "recall 黑话库, and blockers. Use it to discover, explain, "
        "compare, recommend, or configure resources. For search, request actual enabled search terms "
        "only for explicit candidate 黑话库 IDs. Request 审核规则 categories, 审核规则, hit "
        "conditions, exemptions, adjudication notes, and application stages only for exact "
        "published revision IDs. This query never creates a Draft, Run, or Job."
    ),
    "create_investigation_draft": (
        "Create an editable Investigation Draft when the user wants the currently defined "
        "configuration captured as an investigation. Use current real resource candidates. "
        "Select a published 审核规则已发布版本 directly as judgement. "
        "Search Drafts prefer a suitable real existing_lexicon ID, hash, and the server-projected "
        "variant-first search snapshot (stored in the legacy enabled_main_terms field). If none is "
        "sufficiently suitable and the conversation authorizes generating missing Recall, supply "
        "focused, covert, platform-plausible search strings directly in temporary_terms.terms; "
        "otherwise explain the gap and propose generation without creating a Draft or listing "
        "any candidate/example search terms in the reply. Prefer hidden-language variants over "
        "explicit risk labels. source_lexicon_ids are write-time checked provenance, not runtime "
        "dependencies. Temporary terms are not saved as a formal 黑话库. "
        "Creator Drafts must contain only a validated creator homepage URL and no "
        "recall plan. This never confirms or starts a Run. A creation Session that already has "
        "a PUBLISHED Run is single-report scoped: do not create a second Draft there; direct the "
        "user to start a new Session instead."
    ),
    "update_investigation_draft": (
        "Update an editable Investigation Draft at its expected revision. User changes "
        "to platform, 审核规则 judgement, 黑话库, or terms are allowed before confirmation; edited "
        "黑话库 terms must be represented as temporary_terms. Authorized generation of missing "
        "Recall supplies actual search terms directly as a flat list, without formal save. "
        "Changed source_lexicon_ids must reference real 黑话库; unchanged provenance needs no "
        "resource refresh. Each temporary term must be comma-free. This command never confirms "
        "or starts an investigation. A PUBLISHED Run cannot be replaced or supplemented in the "
        "same creation Session; preserve it and direct a new investigation to a new Session."
    ),
    "get_investigation_draft": (
        "Read the current Draft and its dynamic confirmation preview. Show that preview "
        "to the user and do not treat it as confirmation."
    ),
    "confirm_and_queue_investigation": (
        "Freeze and queue one Investigation Run only after the user explicitly asks to "
        "confirm and start. Pass confirmed=true; never infer confirmation from a Draft edit. "
        "If this creation Session already has a PUBLISHED Run, do not confirm another Run; "
        "preserve the published report and direct the user to a new Session."
    ),
    "get_investigation_run": (
        "Read the existing Investigation Run projection. This query is read-only and "
        "never creates a Job, report, or Session."
    ),
}


def _creation_tool_schema(schema: type[StrictModel]) -> dict[str, Any]:
    parameters = schema.model_json_schema()
    # Per-run task parameters are part of the editable Draft contract. Keep them
    # visible to the dialogue tools so explicit user choices (for example
    # collect_media=false) survive preview and confirmation. Account selection
    # remains application-managed and is intentionally hidden below.
    task_schema = parameters.get("$defs", {}).get("InvestigationTaskParameters", {})
    task_schema.get("properties", {}).pop("crawler_account_id", None)

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


M3_TOOL_INPUTS.update(RESOURCE_TOOL_INPUTS)
M3_MUTATION_TOOL_NAMES = M3_MUTATION_TOOL_NAMES | RESOURCE_MUTATIONS
M3_RECORDED_TOOL_NAMES = M3_MUTATION_TOOL_NAMES | {"get_resource_edit", "get_ruleset_proposal"}
M3_TOOL_DESCRIPTIONS.update(RESOURCE_DESCRIPTIONS)

# Attach guidance before freezing the schemas used by deferred discovery.
M3_PARAMETER_GUIDANCE = {
    'use_ruleset_proposal': (
        '新建草案时 arguments 结构为 '
        '{"presentation_id":"展示记录返回的真实ID","create_draft":{"title":"任务标题","objective":"任务目标","configuration":{"platform":"dy","investigation":{"mode":"search","recall_plan":{"strategy":"temporary_terms","terms":["已展示的搜索词"]}}}}}。title/objective/configuration'
        ' 必须放在 create_draft 内；recall_plan 直接放 terms，不嵌套 temporary_terms。configuration 也可包含 task_parameters；'
        '用户明确指定执行参数时须写入并保留，不要传 crawler_account_id。已有草案则传 '
        'presentation_id、draft_id、expected_revision，不传 create_draft。'
    ),
    'query_investigation_options': (
        '最小查询参数 {"platform":"dy","mode":"search"}。平台字段叫 platform，抖音值为 dy；不要传 platform_id 或 '
        'include_ruleset_categories。详细规则用 include_ruleset_details_for_revision_ids 指定真实 revision '
        'ID。'
    ),
    'create_ruleset_proposal': (
        '参数只有 content，其值是完整 审核规则完整内容 对象；不要用 canonical_content。分类和规则的名称字段均叫 name；风险字段叫 '
        'suggested_risk_level；分类和规则均须有 order，规则还须有 adjudication_notes。具体必填项先读 '
        'schema。成功后展示规则及搜索词并结束本轮，等用户采用。'
    ),
    'update_ruleset_proposal': (
        '参数为 proposal_id、expected_version、content（完整新内容）。先读取当前提案，按 rule_id 修改目标并保持其他规则。不是局部 '
        'patch，也不传 ruleset_id。'
    ),
    'get_ruleset_proposal': (
        '参数只有 proposal_id，使用工具返回的真实 ID。'
    ),
    'get_investigation_draft': (
        '参数只有 draft_id，使用工具返回的真实 ID。'
    ),
    'create_investigation_draft': (
        'configuration 可包含 task_parameters。用户明确指定帖子数、每帖评论数、并发、是否采集媒体或是否自动审核时，必须把这些值写入 '
        'configuration.task_parameters；未明确指定时可省略并使用统一设置。生成临时召回时，temporary_terms 必须同时包含完整 '
        'lexicon_content，主词表示主题、启用变体表示实际搜索词，terms 必须与变体投影完全一致。不要传 crawler_account_id。'
    ),
    'update_investigation_draft': (
        '参数须有 draft_id、expected_revision，另传需要修改的 title、objective 或完整 configuration。先读当前草案；修改搜索词时保留 '
        'platform、judgement 和未要求改变的 task_parameters。结构化关键词编辑必须同时提交完整 lexicon_content 和与其启用变体投影完全一致的 '
        'investigation.recall_plan.terms，不得只改扁平 terms 而丢失主题归属。用户明确指定帖子数、评论数、并发、媒体采集等'
        '执行参数时，在 configuration.task_parameters 中写入并在后续完整配置更新中保留；不要传 crawler_account_id、patch 或 expected_version。'
    ),
    'confirm_and_queue_investigation': (
        '用户明确启动后调用。参数为 draft_id、expected_revision、confirmed:true、idempotency_key。revision '
        '取最新草案；只采用或只创建草案不等于启动授权。'
    ),
    'get_investigation_run': (
        '参数只有 run_id，使用确认工具返回的真实 ID。'
    ),
}
M3_ADOPTION_GUIDANCE = {
    'create_investigation_draft': (
        '本工具用于以已发布规则创建草案。采用本会话临时 审核规则 Proposal 时禁止调用本工具；必须直接调用 '
        'use_ruleset_proposal，由它一次性创建草案并绑定规则。不要把 Proposal 内容塞入本工具。 '
    ),
    'use_ruleset_proposal': (
        '本工具既能采用临时规则，也能直接创建 Draft，无需先调用 create_investigation_draft。用户明确采用已展示 Proposal 且尚无 Draft '
        '时，直接传 presentation_id 和 create_draft（title、objective、configuration；configuration 只有 '
        'platform、investigation 与可选 task_parameters，不传 judgement）。 '
    ),
}
for _name in M3_TOOL_DESCRIPTIONS:
    M3_TOOL_DESCRIPTIONS[_name] = (
        M3_PARAMETER_GUIDANCE.get(_name, "") + " "
        + M3_ADOPTION_GUIDANCE.get(_name, "")
        + M3_TOOL_DESCRIPTIONS[_name]
    ).strip()

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
        self._tool_start_observer: Callable[[str, str, str], None] | None = None

    def set_tool_start_observer(
        self,
        observer: Callable[[str, str, str], None] | None,
    ) -> None:
        """Install a fail-open projection observer at the application boundary."""

        self._tool_start_observer = observer

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
        runtime_identity: HermesToolExecutionIdentity | None = None,
    ) -> dict[str, Any]:
        schema = M3_TOOL_INPUTS.get(tool_name)
        if schema is None:
            raise ValueError("Hermes M3 tool name is not allowed")
        parsed = schema.model_validate(arguments)
        if tool_name in RESOURCE_TOOL_INPUTS:
            return execute_resource(self.application_service.resource_management, tool_name, parsed,
                                    session_id=session_id, principal=principal)
        if tool_name == "use_ruleset_proposal":
            with self._conversation_lock:
                turn_id = self._conversation_turns.get(session_id, "")
            draft = self.application_service.use_ruleset_proposal(
                parsed, session_id=session_id, turn_id=turn_id, principal=principal,
                runtime_turn_id=runtime_identity.turn_id if runtime_identity else "",
                tool_call_id=runtime_identity.tool_call_id if runtime_identity and runtime_identity.session_id == session_id else "",
            )
            result = InvestigationDraftView(
                draft=draft,
                confirmation_preview=self.application_service.resource_service.confirmation_preview(
                    draft, principal=principal,
                ),
            )
        elif tool_name == "create_ruleset_proposal":
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
        observer = self._tool_start_observer
        if observer is not None:
            try:
                observer(identity.session_id, identity.tool_call_id, tool_name)
            except Exception:
                # Public activity is a display projection and must never alter
                # validation, receipt fencing, replay, or mutation execution.
                pass
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
            with self._conversation_lock:
                application_turn_id = (
                    self._conversation_turns.get(identity.session_id, "")
                    if tool_name in M3_RECORDED_TOOL_NAMES else ""
                )
            # Reads only need a receipt when they can contribute to this turn's
            # durable public display. Standalone reads retain their read-only API.
            if tool_name in M3_MUTATION_TOOL_NAMES or (application_turn_id and tool_name in M3_RECORDED_TOOL_NAMES):
                receipt = self.application_service.store.begin_tool_execution(
                    session_id=identity.session_id,
                    turn_id=identity.turn_id,
                    tool_call_id=identity.tool_call_id,
                    principal=principal.id,
                    tool_name=tool_name,
                    arguments=raw_arguments,
                    is_mutation=tool_name in M3_MUTATION_TOOL_NAMES,
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
                **({"runtime_identity": identity} if tool_name == "use_ruleset_proposal" else {}),
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
    if tool_name in M3_MUTATION_TOOL_NAMES or (tool_name in M3_RECORDED_TOOL_NAMES and turn_id and tool_call_id):
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
