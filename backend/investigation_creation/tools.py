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
from backend.resource_management.authoring_guidance import RULESET_AUTHORING_GUIDANCE
from backend.resource_management.generation_contracts import ResourceGenerationRequest
from backend.audit_agent.search_terms_cap import lexicon_cap_note
from backend.resource_management.generation import ResourceGenerator
from backend.resource_management.keyword_profiles import keyword_profile_metadata

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
from .resource_ref_inputs import (
    ToolDraftConfiguration, ToolUseRuleSetProposalInput, resolve_arguments,
    hide_legacy_hash_input,
)
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
    configuration: ToolDraftConfiguration

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UpdateInvestigationDraftInput(StrictModel):
    draft_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, min_length=1, max_length=4_000)
    configuration: ToolDraftConfiguration | None = None

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
    expected_task_settings_revision: StrictInt = Field(
        ge=0,
        description=(
            "Copy confirmation_preview.task_settings_revision from the Draft preview "
            "reviewed by the user. Required even when it is 0; do not guess or substitute "
            "a newer revision without renewed user confirmation."
        ),
    )
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
    content: RuleSetContent | None = None
    generation_request: ResourceGenerationRequest | None = None

    @model_validator(mode="after")
    def one_input(self):
        if (self.content is None) == (self.generation_request is None):
            raise ValueError("provide exactly one of content or generation_request")
        return self


class GetRuleSetProposalInput(StrictModel):
    proposal_id: str = Field(min_length=1, max_length=160)


class SaveDraftLexiconInput(StrictModel):
    draft_id: str = Field(min_length=1, max_length=160)
    expected_revision: StrictInt = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=200)


class UpdateRuleSetProposalInput(GetRuleSetProposalInput):
    expected_version: StrictInt = Field(ge=1)
    content: RuleSetContent


M3_TOOL_INPUTS: dict[str, type[StrictModel]] = {
    "use_ruleset_proposal": ToolUseRuleSetProposalInput,
    "query_investigation_options": QueryInvestigationOptions,
    "create_investigation_draft": CreateInvestigationDraftInput,
    "update_investigation_draft": UpdateInvestigationDraftInput,
    "get_investigation_draft": GetInvestigationDraftInput,
    "save_draft_lexicon": SaveDraftLexiconInput,
    "save_draft_ruleset": SaveDraftLexiconInput,
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
        "save_draft_lexicon",
        "save_draft_ruleset",
    }
)


M3_TOOL_DESCRIPTIONS = {
    "save_draft_ruleset": (
        "用户明确要求保存当前调查中使用的规则时，先读取当前 Draft，再传 draft_id、expected_revision、operation_id。"
        "后端直接将该版本规则快照保存到当前用户的私有规则库，不传 content 或 hash，不重新生成。"
        "已存在的正式规则无需再次保存；仅用户明确要求另存任务快照时复制。临时规则在任务启动后仍可保存。"
        "保存不修改草案、运行任务或报告，不启动采集；响应丢失查询 get_resource_save，不重复生成。"
    ),
    "save_draft_lexicon": (
        "用户明确要求保存当前调查/任务/卡片中的临时关键词为正式黑话库时使用。先 get_investigation_draft，"
        "只传该草案的 draft_id、expected_revision 和 operation_id。后端提取这一版本的完整结构化词库，"
        "包括卡片修改过的词、启停、风险等级及备注；不需要 content、hash 或重新 create_lexicon_edit。"
        "任务排队、运行或失败后仍可保存，保存不会改变草案或任务冻结快照，也不会启动采集。"
        "operation_id 标识本次逻辑保存；响应丢失后用完全相同的参数重试，不能换 operation_id 重复另存。"
        "版本冲突先重新读取并核对，不强行采用新版。旧版仅扁平词表需先在卡片中结构化确认，不能猜测生成。"
        "已是正式词库时先 read_resource 确认保存状态，不自动复制。只有会话编辑稿且未用于草案时，"
        "仍用 save_resource(edit_id, expected_version)。保存到当前用户的私有词库，普通用户也可保存自己的资源。"
    ),
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
        "Generate a temporary candidate 审核规则 using generation_request (objective/platform/requirements, "
        "requested_count only when explicitly specified). The dedicated author receives the complete "
        "rule-authoring instructions and returns validated content. Use content instead only to import "
        "an already supplied exact canonical resource, never to bypass a generation failure. "
        "Supply exactly one of generation_request or content. Use this tool when "
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
        "Search Drafts prefer a suitable formal lexicon: use the resource_ref recall_plan returned "
        "by read_resource or save_resource; the backend binds its exact search snapshot. If none is "
        "sufficiently suitable and the conversation authorizes generating missing Recall, first call "
        "create_lexicon_edit and use its resource_ref recall_plan unchanged, without copying content; "
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
        "黑话库 edits use the latest edit's resource_ref recall_plan; the backend captures temporary_terms. Authorized generation of missing "
        "Recall first creates a session lexicon edit and uses its exact structured recall_plan, without formal save. "
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
        "Copy expected_task_settings_revision from confirmation_preview.task_settings_revision "
        "and expected_revision from that Draft. If settings changed since user review, "
        "show the refreshed preview and obtain confirmation again. "
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
    hide_legacy_hash_input(parameters)
    # task_parameters stays in the Draft contract for stored drafts, but preview and
    # confirmation always use 采集与分析设置; chat values are ignored. Account selection
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
for _authoring_tool in ("update_ruleset_proposal",):
    M3_TOOL_DESCRIPTIONS[_authoring_tool] += "\n" + RULESET_AUTHORING_GUIDANCE

M3_TOOL_DESCRIPTIONS.update(RESOURCE_DESCRIPTIONS)

# Attach guidance before freezing the schemas used by deferred discovery.
M3_PARAMETER_GUIDANCE = {
    'use_ruleset_proposal': (
        '新建草案时 arguments 结构为 '
        '{"presentation_id":"展示记录返回的真实ID","create_draft":{"title":"任务标题","objective":"任务目标","configuration":{"platform":"dy","investigation":{"mode":"search","recall_plan":{"strategy":"resource_ref","resource_ref":"词库回执返回的真实引用"}}}}}。title/objective/configuration'
        ' 必须放在 create_draft 内；临时或正式词库均直接使用回执中的 recall_plan，不重抄内容。执行参数只来自采集与分析设置，'
        '不传 task_parameters，不要传 crawler_account_id。已有草案则传 '
        'presentation_id、draft_id、expected_revision，不传 create_draft。'
    ),
    'query_investigation_options': (
        '最小查询参数 {"platform":"dy","mode":"search"}。平台字段叫 platform，抖音值为 dy；不要传 platform_id 或 '
        'include_ruleset_categories。详细规则用 include_ruleset_details_for_revision_ids 指定真实 revision '
        'ID。'
    ),
    'create_ruleset_proposal': (
        '新生成传 generation_request，包含 objective、platform、requirements 和用户明确要求的 requested_count。'
        '不要把完整历史传入专用生成步骤。仅原样导入已给定内容才传 content；两者不能同时提供。'
        'content 是完整 审核规则完整内容 对象；不要用 canonical_content。分类和规则的名称字段均叫 name；风险字段叫 '
        'suggested_risk_level；分类和规则均须有 order，规则还须有 adjudication_notes。具体必填项先读 '
        'schema。成功后展示规则及搜索词，等后续明确采用；本轮已明确授权保存时可以先完成保存，不自动采用或启动。'
    ),
    'update_ruleset_proposal': (
        '参数为 proposal_id、expected_version、content（完整新内容）。先读取当前提案，按 rule_id 修改目标并保持其他规则。不是局部 '
        'patch，也不传 ruleset_id。expected_version 必须逐字取自 get_ruleset_proposal 返回的 version，不可省略或自行加一。'
        '只有修改工具返回 status=ok 才能宣称修改完成，版本以返回结果为准；报错时如实说明未完成，不把拟修改文字当作已写入。'
    ),
    'get_ruleset_proposal': (
        '参数只有 proposal_id，使用工具返回的真实 ID。'
    ),
    'get_investigation_draft': (
        '参数只有 draft_id，使用工具返回的真实 ID。'
    ),
    'create_investigation_draft': (
        '执行参数（帖子数、评论数、媒体采集等）只来自采集与分析设置，聊天不能修改，不传 task_parameters。'
        '已有临时或正式词库时直接使用其 resource_ref recall_plan，'
        '不重新填写内容、词条或 hash；主词归属、启用变体与搜索投影由后端保留。不要传 crawler_account_id。'
    ),
    'update_investigation_draft': (
        '参数须有 draft_id、expected_revision，另传需要修改的 title、objective 或完整 configuration。先读当前草案；修改搜索词时保留 '
        'platform 和 judgement。词库编辑后使用最新编辑稿的 resource_ref recall_plan，'
        '不得只改扁平 terms 而丢失主题归属；完整内容与启用变体投影由后端绑定。执行参数只来自采集与分析设置，'
        '不传 task_parameters；不要传 crawler_account_id、patch 或 expected_version。'
    ),
    'confirm_and_queue_investigation': (
        '用户明确启动后调用。参数为 draft_id、expected_revision、expected_task_settings_revision、confirmed:true、idempotency_key。'
        '版本取用户已核对草案及 confirmation_preview.task_settings_revision（0 也是有效值，不能猜测）。'
        '缺参数且未创建回执时，补齐后保留原 idempotency_key 重试；设置已变化则展示新预览并请用户重新确认。'
        '只采用或只创建草案不等于启动授权。'
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
        'platform 与 investigation，不传 judgement）。 '
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

    def __init__(self, application_service: Any, *, resource_generator=None) -> None:
        self.application_service = application_service
        self.resource_generator = resource_generator or ResourceGenerator()
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
        resource_ref = None
        generation_profile = None
        if tool_name in {'create_investigation_draft', 'update_investigation_draft', 'use_ruleset_proposal'}:
            resolved, resource_ref = resolve_arguments(tool_name, parsed.model_dump(mode='json'),
                                         lambda: self.application_service.resource_management,
                                         principal=principal, session_id=session_id)
            parsed = (UseRuleSetProposalInput if tool_name == 'use_ruleset_proposal' else schema).model_validate(resolved)
        if tool_name in {"create_ruleset_proposal", "create_lexicon_edit"} and parsed.generation_request is not None:
            kind = "ruleset" if tool_name == "create_ruleset_proposal" else "lexicon"
            if kind == "lexicon":
                generation_profile = keyword_profile_metadata(parsed.generation_request)
            content = self.resource_generator.generate(kind, parsed.generation_request)
            parsed = schema.model_validate({"content": content.model_dump(mode="json")})
        if tool_name in RESOURCE_TOOL_INPUTS:
            result = execute_resource(self.application_service.resource_management, tool_name, parsed,
                                      session_id=session_id, principal=principal)
            if generation_profile is not None:
                result = {**result, "generation_profile": generation_profile}
            # Saved lexicons keep every term; only task creation caps the search.
            # generation_profile is set exactly for generated lexicons.
            cap_note = (lexicon_cap_note(result.get("search_terms") or [])
                        if generation_profile is not None else None)
            if cap_note is not None:
                result = {**result, "search_terms_cap": cap_note}
            return result
        if tool_name == "use_ruleset_proposal":
            with self._conversation_lock:
                turn_id = self._conversation_turns.get(session_id, "")
            draft = self.application_service.use_ruleset_proposal(
                parsed, session_id=session_id, turn_id=turn_id, principal=principal,
                runtime_turn_id=runtime_identity.turn_id if runtime_identity else "",
                tool_call_id=runtime_identity.tool_call_id if runtime_identity and runtime_identity.session_id == session_id else "",
                **({'resource_ref': resource_ref} if resource_ref else {}),
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
                **({'resource_ref': resource_ref, 'session_id': session_id} if resource_ref else {}),
            )
            result = self.application_service.get_draft_view(
                draft.id, principal=principal
            )
        elif tool_name == "update_investigation_draft":
            draft = self.application_service.update_draft(
                UpdateDraftCommand.model_validate(parsed.model_dump(mode="json")),
                principal=principal,
                **({'resource_ref': resource_ref, 'session_id': session_id} if resource_ref else {}),
            )
            result = self.application_service.get_draft_view(
                draft.id, principal=principal
            )
        elif tool_name == "get_investigation_draft":
            result = self.application_service.get_draft_view(
                parsed.draft_id, principal=principal
            )
        elif tool_name == "save_draft_ruleset":
            saved = self.application_service.save_draft_ruleset(
                **parsed.model_dump(mode='json'), principal=principal, session_id=session_id,
            )
            return {key: saved[key] for key in (
                'status', 'kind', 'resource_id', 'version', 'revision_id', 'operation_id')}
        elif tool_name == "save_draft_lexicon":
            saved = self.application_service.save_draft_lexicon(
                **parsed.model_dump(mode="json"), principal=principal,
                session_id=session_id,
            )
            # Save the exact server snapshot, then return a compact factual receipt.
            # Echoing the full notes would recreate the large model payload we avoid.
            from backend.resource_management.contracts import LexiconContent
            content = LexiconContent.model_validate(saved['content'])
            return {
                'status': 'saved', 'kind': 'lexicon', 'resource_id': saved['resource_id'],
                'version': saved['version'], 'title': content.title,
                'search_terms': content.search_terms(), 'operation_id': parsed.operation_id,
                'draft_id': parsed.draft_id, 'draft_revision': parsed.expected_revision,
                'resource_ref': saved['resource_ref'],
                'recall_plan': {'strategy': 'resource_ref', 'resource_ref': saved['resource_ref']},
            }
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
                elif tool_name == "confirm_and_queue_investigation":
                    details.update(
                        {
                            "run_created": False,
                            "recovery": (
                                "Run was not created. Correct the arguments and retry with "
                                "the same idempotency_key. Copy expected_task_settings_revision "
                                "from the user-reviewed confirmation_preview.task_settings_revision; "
                                "do not guess. If the preview is unavailable, read get_investigation_draft. "
                                "If settings changed since user review, show the new preview "
                                "and obtain user confirmation again before starting."
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
