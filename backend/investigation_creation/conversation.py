from __future__ import annotations

import json
import logging
import re
import sqlite3
import traceback
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from backend.audit_agent.config import settings
from backend.audit_agent.creator_url import CreatorUrlValidationError, validate_creator_url
from backend.hermes_runtime.adapter import HermesReportToolsUnavailable, HermesRuntimeBinding, prepare_creation_history, session_runtime_home
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.investigation.contracts import (
    InvestigationMessage,
    InvestigationSession,
    InvestigationTurn,
    TurnResult,
)
from backend.investigation.errors import (
    InvestigationSessionNotFoundError,
    InvestigationTurnNotFoundError,
    ReportScopeError,
)
from backend.investigation.public_activity import PublicActivityEmitter
from backend.investigation.public_answer import PublicAnswerStreamer
from backend.hermes_runtime.comment_delivery import compose as compose_comment_delivery, live_model_text_allowed, prepared_runtime
from backend.investigation.store import InvestigationStore

from .contracts import (
    ConfirmationPreview,
    InvestigationDraftConfiguration,
    InvestigationDraftView,
    InvestigationOptions,
    PlatformOption,
    InvestigationRunProjection,
    QueryInvestigationOptions,
)
from .errors import (
    ConfigurationValidationError,
    DraftAlreadyConfirmedError,
    DraftRevisionConflictError,
    PrincipalAccessDeniedError,
    RunNotFoundError,
)
from .principal import Principal
from .public_projection import draft_artifact, public_configuration, public_draft, run_artifact
from .public_answer import has_failed_ruleset_update, redact_creation_internal_references
from .tools import (
    HermesToolExecutionIdentity,
    InvestigationCreationToolService,
)


from backend.resource_management.tools import RESOURCE_PROMPT

PUBLISHED_SESSION_NOTICE = (
    "当前会话已生成并发布报告，后续可继续围绕该报告进行提问。\n"
    "如需基于当前配置发起新的调查，请新建会话。"
)

logger = logging.getLogger(__name__)

DRAFT_TERM_TOOLS = frozenset({"create_investigation_draft", "update_investigation_draft", "use_ruleset_proposal", "configure_investigation_draft"})

CREATION_SYSTEM_PROMPT = """用用户提问的语言自然回答，原名、原文引用和必要术语保持原样。
地域配置：本次调查对象、议题或事件名称包含地域时，默认将其作为调查地域，不要求用户另外强调某地的舆情或明确限定地域。
例如“调查克拉玛依大火”“调查深圳楼市”“调查新疆棉花争议”分别采用克拉玛依、深圳、新疆；事件议题和调查地域分别保存，不能仅把地点藏入标题、目标或词库。
调查地域指所讨论事件或议题的地域，不要求发帖人、评论者或 IP 来自该地；“关注全国网友对该事件的讨论”不等于取消事件地域。
同任务用户最新明确更换、取消或要求全国且不限制地域时，以最新要求为准。账号背景、资源名称、引用举例、否定、助手建议和仅修改标题不等于地域要求；用户未提地名时不靠事件常识补地点。
每次通过 configure_investigation_draft 创建草案必须传 scope_decision：有地域选 apply，并通过 relevance_scope_change 传 action=set、原始地名、selection_policy 和真实用户原话 source_quote；无地域或创建前已明确取消选 none，且省略 relevance_scope_change。缺失或矛盾会在写入前拒绝，应根据真实历史补全后重试，不要求用户重复。
明确只要当地、不补充用 local_only，其余有地域的调查用 local_first_with_supplement 并在方案中说明，不额外确认默认策略。
更新草案不传 create 专用 scope_decision；明确设置或换地域时传 set，明确取消地域时传 clear 和取消原话；只改标题、规则或黑话库时省略 relevance_scope_change 保留已有地域。不要从旧报告继承新一轮范围。
用户已明确要求创建、修改或取消，且必要信息、资源引用和采用条件满足时直接配置，成功后报告，不再询问是否应用。
资源缺失时不创建占位草案，保留真实用户消息中的要求，资源齐备后引用同任务此前原话；资源展示和后续采用授权边界继续适用。
资源齐备并获采用授权时，回看同一任务真实用户消息中的调查对象、事件及其地域，以及后续变更或取消；本轮仅说采用资源不表示放弃此前地域。创建草案时一并作出 scope_decision 判断，有地域时引用原要求并配置，不要求用户重复此前要求。
调查范围支持单一省级地域或地级市（含直辖市），最细到城市。地域简称先交给配置工具解析；解析成功直接报告结果。
仅在真实回执为 pending 时，根据原因和候选请用户核对地域名或明确指代；吉林这类省市同名简称不强行择一，完整省名或市名可正常解析；区县同名不阻断明确城市别名。查不到不代表用户一定写错，不自行猜测纠错。
已有草案须先通过同一配置工具保存原始目标的待澄清状态，再根据成功回执提问；无草案且资源不足时可直接澄清。
区县、县级市、街道和非市地级类型暂不支持，保留原始目标待澄清，不能自动扩大为其所属城市或省。
读取草案和预览作为状态依据；pending 表示最新要求待澄清，上次已解析范围不代表本次要求已生效。地域执行尚未接通，不能启动地域调查或声称采集已限定地域。
推荐现有资源时，说明真实名称、已发布版本、覆盖范围和缺口；已发布或主题相近不等于完整适配。
规则创建或修改成功后，应用会展示完整快照，回复只需说明关键内容和变化。
你的产品身份固定为“研判助手”。不要自称 Hermes、Hermes Agent，不能向用户透露底层代理框架、
模型运行时、供应商实现或产品改造来源。用户只是在打招呼（例如 hello、你好）时，简短回应并说明
你可以协助配置调查、查询审核规则与黑话库、跟进调查报告，不要擅自创建调查草稿或调用资源工具。
用户界面与回复统一使用“审核规则”和“黑话库”两个资源名称；理解用户旧称，但回复、标题和生成说明只用新名称。黑话库的主词用于表达主题，启用变体词是优先的实际平台搜索词；某个主词没有启用变体时，回退使用该主词；主词停用时整组不参与搜索。任务卡只展示实际搜索词摘要；关键词修改在结构化 Drawer 中按“主题主词→变体词”完成。用户明确“搜索主词 X”“只用 X”，或用自然语序说“想抓一条抖音 X”“抖音抓 N 条 X”“在抖音搜索 N 条 X”时，即使 X 没有引号，也必须剥离意图词、平台名、数量和“抓取/报告”等动作词，把剩余的用户原文 X 作为本次唯一实际搜索词。例如“想抓一条抖音 bc料”的平台是抖音、数量是一条、唯一实际搜索词是 bc料。保留 X 的原始大小写、字母数字、符号和简写，不把短词或黑话改写成解释性词语，也不要自行增加其他主词、变体或标签。若建立完整词库，可以把 X 作为某个主题主词下的启用变体，但 search_terms 必须仍严格只有 X。可以把“博彩”等领域解释用于调查标题和审核规则匹配，但必须明确说明实际搜索词仍是用户原文。临时词库先通过 create_lexicon_edit 建立会话编辑稿，采用时才绑定当前调查 Draft；生成本身不正式保存到黑话库数据库。

Conversation is primary. Use only the investigation creation tools
exposed in this mode, choosing and combining them according to the user's current intent. There is
no requirement to run every tool or follow one fixed workflow in every turn.

Application may restore successful ToolResults from a durable checkpoint after an earlier model Turn
failed. Those restored ToolResults are authoritative completed operations. Continue only the unfinished
work; do not repeat their generation, save, Draft mutation, or confirmation merely because the earlier
model Turn failed. Each successful checkpoint consumes only that completed part of the earlier request;
the same authorization may continue a genuinely unfinished later step, but it does not authorize repeating
the completed mutation. Repeat one only when the CURRENT user explicitly asks to regenerate, replace, or
save another resource. This recovery rule does not change normal suitability decisions: prefer a sufficiently
suitable existing resource, and generate a missing resource only with current authorization.

会话边界必须服从应用状态：一个创建会话最多对应一个已发布报告。若当前会话的真实 Run
状态为 PUBLISHED 且已有 report_version_id，这个会话只保留该报告的后续问答；不要再创建、
修改、采用或确认第二个 Draft/Run，也不要把已发布任务描述为“排队中”“执行中”或建议继续等待。
一旦 Draft 已确认并产生 Run，搜索词和任务配置即冻结。此后用户要求修改关键词时，不调用更新工具，
不声称原任务已修改；应说明原任务配置不能再改，如需使用新关键词请新建调查会话。
用户要求“照当前配置再做一次”、新建独立草稿或第二份报告时，原样回复：
“当前会话已生成并发布报告，后续可继续围绕该报告进行提问。
如需基于当前配置发起新的调查，请新建会话。”
该回复表示原报告和原任务均保留；新调查必须在新会话开始。

The user may want to query, understand, explain, compare, or get recommendations about available
platforms, recall 黑话库 and their terms, published 审核规则已发布版本, existing
Drafts, or existing Runs. They may instead want to create or edit an investigation, or explicitly
confirm one. When a request concerns resources, versions, or status that currently exist in the
system, call the necessary read tool and rely on its ToolResult. Never invent Application state.
For a read-only request, answer naturally from the ToolResult and end the turn without calling
configure_investigation_draft. A final assistant answer does not need canonical decision JSON.

Do not treat a topic mention or a resource question as sufficient intent to create a Draft. Create a
Draft when the full conversation shows operational investigation intent; "I want to investigate X"
is sufficient and does not require a second create command. Phrases such as "create the
investigation", "use this configuration to create the task", or "use this 黑话库 and 审核规则 to
investigate on Douyin" also indicate creation intent, without requiring exact keywords. When
resource discovery is needed, call query_investigation_options. Complete resource identities already
present in the conversation context may be reused; Application will reread and authoritatively
validate them during creation. A read-only turn may stop after its query.

An Investigation Draft is an editable recommended configuration, not a final confirmed execution
configuration. When the user clearly asks to create a Draft and suitable real resources exist, do
not require explicit confirmation of every editable or defaultable field. Derive a useful title
from the objective; preserve any available platform the user explicitly selected, or otherwise
choose one reasonable available platform from the available resource context. Select the clearly
matching published 审核规则已发布版本 directly as the Judgement resource and independently select a
clearly matching available real recall 黑话库. When the actual enabled search-term snapshot for that
existing 黑话库 is available in the conversation context (the legacy tool field may still be named
enabled_main_terms), that authoritative term snapshot is the recall
configuration; do not ask the user to enter separate search keywords before creating the Draft. The
user can review and edit these recommended values on the Draft afterward.

Execution parameters come only from 采集与分析设置 and cannot be changed in conversation;
omit task_parameters. crawler_account_id remains application-managed.
只在用户询问或要求修改执行参数时说明设置入口，不在普通资源回答末尾附带参数限制。

审核规则按实际条款判断覆盖范围；黑话库按实际启用搜索词判断能否找到目标讨论，两者分别判断。
已发布状态和主题名称不能代替内容适配。部分适配就说明缺口，不把涉及相同群体当作完整覆盖。
用户明确指定的资源不擅自替换；不合适时说明原因。缺少合适黑话库时遵循下面的生成授权边界。

There are exactly two investigation modes. A creator mode request contains a valid creator homepage
URL, never a post URL, post ID, or arbitrary webpage. Pass creator_url to configure_investigation_draft to select creator mode. Creator mode
must not carry search terms, source 黑话库, or recall plans, and must resolve to crawl_mode=creator.
For a search Draft, select a matching published 审核规则已发布版本 and prefer an independently available
real recall 黑话库 when it is sufficiently suitable. When its terms need to be discovered, query that 黑话库 with
include_lexicon_terms_for_ids. For a selected formal 黑话库, pass its resource_ref as lexicon_ref to configure_investigation_draft; if you only have discovery results,
read the selected resource first. The backend binds and validates the exact resource version,
runtime fingerprint, access permissions, and actual search-term snapshot; do not assemble hashes.
If the reference is stale, re-read and review the differences rather than silently adopting a newer version. The legacy
enabled_main_terms field already contains the server-projected variant-first search terms; do not
replace them with theme main terms or tag entries. When the user explicitly changes the
search terms, update the session lexicon edit first, then use configure_investigation_draft(operation=update) with its latest
lexicon_ref. The backend preserves complete lexicon_content, variant-first projection and
source 黑话库 ID. Never flatten a structured Drawer edit and discard theme-to-variant relationships.

If no sufficiently suitable existing 黑话库 is available, inspect the full conversation for
authorization to generate missing Recall. Without that authorization, explain the Recall resource
gap, propose generating temporary search terms for this investigation, and end this turn with no
Draft and no generated terms. Investigation intent alone is not generation authorization.
Before authorization, do not list even illustrative example terms or candidate terms in your reply;
propose the generation action only, then wait for the user's answer.
If the user already authorized generation (for example, "没有合适词库就帮我生成这次搜索词" or
"没有的话你自己补"), and investigation intent and a valid published Judgement 审核规则 are present,
call create_lexicon_edit to persist a structured temporary lexicon, then create the Draft in the same turn only when
using that valid published Judgement. Pass the edit's resource_ref as lexicon_ref; do not copy content.
When temporary 审核规则 and search terms are generated together, this takes priority: create the
审核规则 Proposal and a lexicon edit via create_lexicon_edit, then show their actual contents, and END this turn.
If the user explicitly requested formal saving too, finish that authorized save before ending;
this exception never authorizes adoption or execution.
Do not call configure_investigation_draft in that
generation turn. Generating or displaying temporary terms does not require a Draft. Wait for a
LATER explicit adoption; then configure_investigation_draft creates the Draft with the selected resource references.
Do not ask again for permission to generate terms that the user already requested.
Before generating, read create_lexicon_edit's complete schema. Send generation_request containing
the complete objective, platform and relevant user constraints; the dedicated author carries the
original “统一召回词生成要求”. Do not invent content in the coordinator.
Even for temporary-only use, persist the structured session edit through create_lexicon_edit.
The tool returns the variant-first search projection and recall_plan; use those exact results.
A list in a natural-language answer is only a preview, not a created or saved lexicon.
Preserve exact authoritative resource terms and explicit user edits. Before Draft adoption,
show proposed temporary terms in the conversation and preserve that list when later binding.
source_lexicon_ids are provenance references only; they do not contribute search terms or variants.
Generated terms are session edits, not saved formal 黑话库. Do not publish them automatically.
Only a user's explicit save request authorizes save_resource for the exact edit version. If published Judgement is missing, explain
that resource gap. Preview and Confirm never generate or expand terms.

Temporary rules are session candidates, not published resources. Generation/editing never adopt them.
First show the complete actual rule version, then wait for a LATER user message explicitly adopting it.
You interpret the CURRENT user's adoption intent; Application validates provenance, not user language.
Questions, edits, negation, hesitation, saving and ambiguous choices are not adoption. Clarify only real ambiguity.
Use configure_investigation_draft for both create and update, regardless of formal/temporary resources.
Read the selected rules through read_resource or get_resource_edit to obtain ruleset_ref = resource_ref;
read the selected lexicon the same way to obtain lexicon_ref = resource_ref. Do not use a proposal ID,
presentation ID, edit ID or library ID as a resource_ref. Copy exact returned references.
The backend resolves temporary rules to a completed public presentation of that exact version and
performs existing adoption checks. A reference does not grant consent. If not presented, show the
actual current rules and wait for later adoption; if changed, do not silently adopt the new version.
Create uses operation=create with title/objective/platform/ruleset_ref and lexicon_ref or creator_url.
Update uses operation=update with draft_id/expected_revision and ONLY changed fields; omitted fields
remain unchanged. Do not send null, rule content, hashes or complete configuration. Multiple changes
are committed together. Changing resource content still uses update_resource_edit, not configuration.
Do not use legacy create_investigation_draft/update_investigation_draft/use_ruleset_proposal names
from old history; they remain historical compatibility only, not current model tools.
Do not expose internal references in the answer. Never create placeholder drafts or substitute an
unrelated resource. On revision conflict read and assess changes before deciding whether to retry.
When the user asks to save the current Draft keywords as a formal 黑话库, first read the latest Draft.
If temporary_terms.lexicon_content exists, call save_draft_lexicon with draft_id, expected_revision and
operation_id; the backend saves that exact snapshot. Never re-copy content into create_lexicon_edit.
For an unadopted session edit, save_resource still saves its exact edit_id/expected_version.
Do not reconstruct, regroup, rename, or add terms from conversation memory. If the
latest Draft still has only legacy flat terms, explain that it must first be structured and confirmed in
the Drawer. Saving a lexicon never mutates the Draft and never changes an already-started task.
For viewing or re-presentation, read the requested resource version. Do not open or modify an edit merely to answer a read request. Explain mutations using the returned actual target and applied_diff.
Temporary Drafts use the same Preview/Confirm flow as formal Drafts, subject to fresh Application
readiness validation. Adoption alone never starts execution. Execution requires a separate explicit
confirmation and uses the bound Draft snapshot, never the live Proposal.
You may generate temporary terms and a Proposal in the same turn. When a valid published Judgement
is available, save the terms in Draft Recall and independently create a requested comparison Proposal.
When both formal resources are missing, generate the requested Proposal and wait for later approval
before binding. Do not invent a Judgement identity or use an unrelated published 审核规则.
Resource creation and editing follow the unified principles below. Application owns content_hash.
A request for another independent candidate leaves earlier candidates available.
Explain the actual result naturally after a successful tool call.

Detailed rule-authoring guidance is preserved in the dedicated generation step and the update tools.
Read the relevant tool description and complete schema before authoring; do not substitute prose for a resource.

If the user asks to inspect an existing Draft, read it instead of creating a replacement. If the
user asks to change an existing Draft, update that Draft at its current revision instead of creating
a second Draft. Never fabricate missing domain resources. If no suitable published 审核规则已发布版本
exists, no real recall configuration can be formed, the user's requested
platform is unavailable, or a current blocker prevents a valid configuration, do not create a
misleading Draft. A creator-mode Draft cannot default a missing creator homepage URL; ask for that
URL instead. Before a temporary Proposal has been explicitly approved, if no matching published
审核规则已发布版本 exists, preserve and explain the
NO_PUBLISHED_RULESET blocker. Do not pick an unrelated 审核规则 and do not confirm. If no
real recall 黑话库 is available in search mode, do not invent or hardcode one. Draft saves never
publish or mutate shared 黑话库. Creating or updating a Draft is never confirmation. Only call
confirm_and_queue_investigation after an explicit user instruction to confirm and start, with
confirmed=true and a stable idempotency key. Keep all pre-confirmation turns free of Run, Job,
crawler, audit-provider, and report side effects. Explicitly authorized resource generation may
call its dedicated authoring provider but never starts collection or auditing. ToolResults and public artifacts are
authoritative. When a Draft or Run tool succeeds, briefly explain its public artifact in the user's
language.


"""


@dataclass(frozen=True)
class InvestigationWorkspaceState:
    session: InvestigationSession
    messages: tuple[InvestigationMessage, ...]
    latest_turn: InvestigationTurn | None
    draft_artifact: dict[str, Any]
    run: InvestigationRunProjection | None
    report_messages: tuple[InvestigationMessage, ...] = ()
    latest_report_turn: InvestigationTurn | None = None
    rounds: tuple[dict[str, Any], ...] = ()
    region_selection_history: tuple[dict[str, Any], ...] = ()


class FakeCreationHermesAgent:
    """Deterministic local Hermes-shaped runtime; it never starts the investigation pipeline."""

    def __init__(
        self,
        *,
        session_id: str,
        tool_service: InvestigationCreationToolService,
        principal_resolver: Callable[[str], Principal],
        tool_start_callback: Callable[..., None] | None = None,
        tool_complete_callback: Callable[..., None] | None = None,
        **_: Any,
    ) -> None:
        self.session_id = session_id
        self.tool_service = tool_service
        self.principal_resolver = principal_resolver
        self.tool_start_callback = tool_start_callback
        self.tool_complete_callback = tool_complete_callback

    def close(self) -> None:
        return None

    def run_conversation(
        self,
        message: str,
        *,
        conversation_history: list[dict[str, Any]] | None = None,
        task_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        principal = self.principal_resolver(self.session_id)
        history = list(conversation_history or [])
        creator_request = self._creator_request(message)
        mode = "creator" if creator_request is not None else "search"
        option_args = {
            "domain_hint": "" if mode == "creator" else message.strip()[:200],
            "mode": mode,
            "page_size": 20,
        }
        option_call_id = f"{task_id}:options"
        options = self._execute_tool(
            option_call_id,
            "query_investigation_options",
            option_args,
            lambda: self.tool_service.execute(
                "query_investigation_options", option_args, principal=principal
            ),
        )
        ruleset = (options.get("ruleset_revisions") or [None])[0]
        platform = (options.get("platforms") or [None])[0]
        lexicon = (options.get("recall_lexicons") or [None])[0]
        if platform is None:
            raise RuntimeError("fake Draft creation requires an available platform")
        if creator_request is not None:
            creator_platform, creator_url = creator_request
            matching_platform = next(
                (
                    item
                    for item in options.get("platforms") or []
                    if item.get("id") == creator_platform
                ),
                None,
            )
            platform = matching_platform or {"id": creator_platform}
        lexicon_options = options
        lexicon_option_args: dict[str, Any] | None = None
        if mode == "search" and lexicon is not None:
            lexicon_option_call_id = f"{task_id}:lexicon-options"
            lexicon_option_args = {
                "domain_hint": message.strip()[:200],
                "mode": "search",
                "lexicon_ids": [lexicon["id"]],
                "include_lexicon_terms_for_ids": [lexicon["id"]],
                "page_size": 20,
                "lexicon_term_limit": 100,
            }
            lexicon_options = self._execute_tool(
                lexicon_option_call_id,
                "query_investigation_options",
                lexicon_option_args,
                lambda: self.tool_service.execute(
                    "query_investigation_options",
                    lexicon_option_args,
                    principal=principal,
                ),
            )
            lexicon = (lexicon_options.get("recall_lexicons") or [None])[0]
        missing_resource = ""
        if ruleset is None:
            missing_resource = "当前没有适合本次调查的已发布研判规则，暂时无法创建 Draft。"
        elif mode == "search" and lexicon is None:
            missing_resource = "当前没有可用的已发布黑话库，无法创建关键词调查 Draft。"
        if missing_resource:
            final = missing_resource
            messages = [
                *history,
                {"role": "user", "content": message},
                self._tool_call(option_call_id, "query_investigation_options", option_args),
                self._tool_result(option_call_id, "query_investigation_options", options),
                {"role": "assistant", "content": final},
            ]
            return {
                "completed": True,
                "failed": False,
                "interrupted": False,
                "final_response": final,
                "messages": messages,
                "turn_exit_reason": "blocked_resource_gap",
                "api_calls": 0,
            }
        configuration: dict[str, Any] = {
            "schema_version": "investigation-draft-config-v4",
            "platform": platform["id"],
            "investigation": (
                {
                    "mode": "creator",
                    "creator_url": creator_request[1],
                }
                if creator_request is not None
                else {
                    "mode": "search",
                    "recall_plan": {
                        "strategy": "existing_lexicon",
                        "lexicon_id": lexicon["id"],
                        "expected_runtime_content_hash": lexicon[
                            "runtime_content_hash"
                        ],
                        "enabled_main_terms": lexicon["enabled_main_terms"],
                    },
                }
            ),
            "judgement": {
                "strategy": "existing_ruleset",
                "ruleset_revision_id": ruleset["id"],
                "expected_ruleset_version": ruleset["version"],
                "expected_ruleset_content_hash": ruleset["content_hash"],
            },
        }
        draft_args = {
            "title": (
                "博主主页调查"
                if mode == "creator"
                else "关键词风险调查"
            ),
            "objective": message.strip(),
            "configuration": configuration,
        }
        lexicon_option_call_id = f"{task_id}:lexicon-options"
        create_call_id = f"{task_id}:create-draft"
        create_result = self._execute_tool(
            create_call_id,
            "create_investigation_draft",
            draft_args,
            lambda: self.tool_service.execute_with_identity(
                "create_investigation_draft",
                draft_args,
                principal=principal,
                identity=HermesToolExecutionIdentity.require(
                    session_id=self.session_id,
                    turn_id=task_id,
                    tool_call_id=create_call_id,
                ),
            ),
        )
        if create_result.get("status") != "ok":
            error = create_result.get("error") or {}
            raise RuntimeError(
                str(error.get("message") or "fake Draft creation did not complete")
            )
        view = create_result["data"]
        explicit_confirm = (
            ("确认并开始" in message or "确认开始调查" in message)
            and "不要开始" not in message
        )
        confirm_call_id = f"{task_id}:confirm-run"
        confirm_args = {
            "expected_task_settings_revision": view["confirmation_preview"]["task_settings_revision"],
            "draft_id": str((view.get("draft") or {}).get("id") or ""),
            "expected_revision": int(
                (view.get("draft") or {}).get("current_revision") or 0
            ),
            "confirmed": True,
            "idempotency_key": f"fake-hermes-confirm:{task_id}",
        }
        confirm_result: dict[str, Any] | None = None
        can_confirm = bool(
            (view.get("confirmation_preview") or {}).get("can_confirm")
        )
        if explicit_confirm and can_confirm:
            confirm_result = self._execute_tool(
                confirm_call_id,
                "confirm_and_queue_investigation",
                confirm_args,
                lambda: self.tool_service.execute_with_identity(
                    "confirm_and_queue_investigation",
                    confirm_args,
                    principal=principal,
                    identity=HermesToolExecutionIdentity.require(
                        session_id=self.session_id,
                        turn_id=task_id,
                        tool_call_id=confirm_call_id,
                    ),
                ),
            )
            if confirm_result.get("status") != "ok":
                error = confirm_result.get("error") or {}
                raise RuntimeError(
                    str(error.get("message") or "fake Run confirmation did not complete")
                )
        final = (
            "调查方案已确认并进入调查队列。"
            if explicit_confirm and can_confirm
            else (
                "调查方案已生成，但当前 blocker 禁止确认。请先编辑或新增匹配的研判方案。"
                if not can_confirm
                else "调查方案已生成，尚未开始采集。请核对真实资源和确认预览；"
                "只有明确点击确认后才会排队执行。"
            )
        )
        messages = [
            *history,
            {"role": "user", "content": message},
            self._tool_call(option_call_id, "query_investigation_options", option_args),
            self._tool_result(option_call_id, "query_investigation_options", options),
            *(
                [
                    self._tool_call(
                        lexicon_option_call_id,
                        "query_investigation_options",
                        lexicon_option_args,
                    ),
                    self._tool_result(
                        lexicon_option_call_id,
                        "query_investigation_options",
                        lexicon_options,
                    ),
                ]
                if lexicon_option_args is not None
                else []
            ),
            self._tool_call(create_call_id, "create_investigation_draft", draft_args),
            {
                "role": "tool",
                "tool_call_id": create_call_id,
                "name": "create_investigation_draft",
                "content": json.dumps(
                    create_result, ensure_ascii=False, sort_keys=True
                ),
            },
            *(
                [
                    self._tool_call(
                        confirm_call_id,
                        "confirm_and_queue_investigation",
                        confirm_args,
                    ),
                    {
                        "role": "tool",
                        "tool_call_id": confirm_call_id,
                        "name": "confirm_and_queue_investigation",
                        "content": json.dumps(
                            confirm_result, ensure_ascii=False, sort_keys=True
                        ),
                    },
                ]
                if confirm_result is not None
                else []
            ),
            {"role": "assistant", "content": final},
        ]
        return {
            "completed": True,
            "failed": False,
            "interrupted": False,
            "final_response": final,
            "messages": messages,
            "turn_exit_reason": "completed",
            "api_calls": 0,
        }

    def _execute_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if self.tool_start_callback is not None:
            try:
                self.tool_start_callback(tool_call_id, tool_name, arguments)
            except Exception:
                pass
        try:
            result = operation()
        except Exception:
            if self.tool_complete_callback is not None:
                try:
                    self.tool_complete_callback(
                        tool_call_id,
                        tool_name,
                        arguments,
                        {"status": "error"},
                    )
                except Exception:
                    pass
            raise
        if self.tool_complete_callback is not None:
            callback_result = (
                result
                if result.get("status") in {"ok", "error"}
                else {"status": "ok", "data": result}
            )
            try:
                self.tool_complete_callback(
                    tool_call_id,
                    tool_name,
                    arguments,
                    callback_result,
                )
            except Exception:
                pass
        return result

    @staticmethod
    def _creator_request(message: str) -> tuple[str, str] | None:
        candidates = re.findall(r"https?://[^\s]+", str(message or ""))
        for candidate in candidates:
            url = candidate.rstrip("，。！？、,;；)]】}")
            for platform in ("xhs", "dy", "ks"):
                try:
                    return platform, validate_creator_url(platform, url)
                except CreatorUrlValidationError:
                    continue
            hostname = (urlsplit(url).hostname or "").lower()
            known_platform = next(
                (
                    platform
                    for platform, domain in (
                        ("xhs", "xiaohongshu.com"),
                        ("dy", "douyin.com"),
                        ("ks", "kuaishou.com"),
                    )
                    if hostname == domain or hostname.endswith(f".{domain}")
                ),
                None,
            )
            if known_platform is not None:
                return known_platform, url
        return None

    @staticmethod
    def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        }

    @staticmethod
    def _tool_result(call_id: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": json.dumps(
                {"status": "ok", "data": data}, ensure_ascii=False, sort_keys=True
            ),
        }


class InvestigationCreationConversationService:
    """Creation-scoped facade over the shared durable Session/Turn transport."""

    def __init__(
        self,
        *,
        tool_service: InvestigationCreationToolService,
        store: InvestigationStore | None = None,
        runtime_binding: HermesRuntimeBinding | None = None,
        agent_factory: Callable[..., Any] | None = None,
        fake_runtime: bool = False,
        hermes_state_dir: Path | None = None,
        principal_resolver: Callable[[str], Principal] | None = None,
        report_service: HermesInvestigationAgentService | None = None,
    ) -> None:
        self.tool_service = tool_service
        self.report_service = report_service
        self.report_collection = None
        self.tool_service.session_run_resolver = lambda session_id, principal: self.get_workspace_state(session_id, principal=principal).run
        self.store = store or InvestigationStore()
        self.tool_service.application_service.conversation_store = self.store
        self.runtime_binding = runtime_binding or HermesRuntimeBinding()
        self.agent_factory = agent_factory
        self.fake_runtime = bool(fake_runtime)
        self.principal_resolver = principal_resolver
        self.hermes_state_dir = (
            hermes_state_dir or settings.data_dir / "hermes-investigation-creation"
        ).resolve()
        self._agents: dict[object, Any] = {}
        self._agent_lock = RLock()
        self._turn_node_observers: list[Callable[[str, str], None]] = []
        self._activity_emitter = PublicActivityEmitter(
            self.store,
            enabled=lambda: settings.activity_stream_enabled,
        )
        self._answer_streamer = PublicAnswerStreamer(
            self.store,
            enabled=lambda: settings.creation_answer_stream_enabled,
            sanitize=self._redact_answer,
            live_allowed=live_model_text_allowed,
        )
        set_tool_start_observer = getattr(
            self.tool_service, "set_tool_start_observer", None
        )
        if callable(set_tool_start_observer):
            set_tool_start_observer(self._activity_emitter.tool_started)

    @staticmethod
    def _redact_answer(value: str) -> tuple[str, bool]:
        original = value
        if settings.creation_answer_stream_enabled:
            value, _ = redact_creation_internal_references(value)
        if settings.continuous_resource_session_enabled:
            from backend.hermes_runtime.service import redact_internal_account_references
            value, _ = redact_internal_account_references(value)
        return value, value != original

    def close(self) -> None:
        with self._agent_lock:
            agents = tuple(self._agents.values())
            self._agents.clear()
        for agent in agents:
            close = getattr(agent, "close", None)
            if callable(close):
                close()

    def create_session(
        self, *, principal: Principal, workspace_key: str = ""
    ) -> InvestigationSession:
        return self.store.create_creation_session(
            principal=principal.id,
            anchor_key=str(workspace_key or "").strip(),
        )

    def list_workspaces(self, *, principal: Principal, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        items = []
        for session in self.store.list_creation_sessions(principal=principal.id, limit=None):
            try:
                state = self.get_workspace_state(session.id, principal=principal)
                first_question = next((message.content for message in state.messages if message.role == "user"), "")
                timestamp_candidates = [
                    session.updated_at,
                    state.run.updated_at if state.run else "",
                    str((state.draft_artifact.get("draft") or {}).get("updated_at") or ""),
                    state.latest_report_turn.completed_at if state.latest_report_turn else "",
                    *(message.created_at for message in state.messages),
                    *(message.created_at for message in state.report_messages),
                ]
                updated_at = max((value for value in timestamp_candidates if value), default=session.updated_at)
                items.append({
                    "workspace_session_id": session.id,
                    "run_id": state.run.run_id if state.run else "",
                    "title": (state.draft_artifact.get("draft") or {}).get("title") or first_question[:48] or "新调查需求",
                    "updated_at": updated_at,
                    "run_status": state.run.status if state.run else "",
                    "presentation_stage": state.draft_artifact.get("presentation_stage", ""),
                })
            except (sqlite3.Error, OSError):
                # A shared storage outage must remain a request failure.
                raise
            except Exception as exc:
                if isinstance(exc, InvestigationSessionNotFoundError):
                    # Skip only an actually removed session. A missing related
                    # record must still leave an explicit error row in the list.
                    try:
                        self.get_session(session.id, principal=principal)
                    except InvestigationSessionNotFoundError:
                        continue
                # Isolate projection failures without changing persisted history or
                # rereading potentially broken draft/run data for a fallback title.
                # Validation exceptions can contain private input: log locations
                # and exception type, never their message or input values.
                logger.error(
                    "Workspace list projection failed: session_id=%s error_type=%s\n%s",
                    session.id, type(exc).__name__,
                    "".join(traceback.format_tb(exc.__traceback__)),
                )
                items.append({
                    "workspace_session_id": session.id,
                    "run_id": "",
                    "title": "调查会话（暂时无法读取）",
                    "updated_at": session.updated_at,
                    "run_status": "",
                    "presentation_stage": "",
                    "read_error": "WORKSPACE_READ_FAILED",
                })
        items.sort(
            key=lambda item: (item["updated_at"], item["workspace_session_id"]),
            reverse=True,
        )
        return items[offset:offset + limit]

    def get_session(self, session_id: str, *, principal: Principal) -> InvestigationSession:
        session = self.store.get_session(session_id)
        self._authorize(session, principal)
        return session

    def principal_for_session(self, session_id: str) -> Principal:
        session = self.store.get_session(session_id)
        if session.scope_type != "creation" or not session.owner_principal:
            raise InvestigationSessionNotFoundError("creation Session was not found")
        if self.principal_resolver is not None:
            return self.principal_resolver(session.owner_principal)
        return Principal(session.owner_principal)

    def accept_message(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
        principal: Principal,
        resource_edit: dict | None = None,
    ) -> tuple[InvestigationTurn, bool]:
        user_input = str(content or "").strip()
        client_id = str(client_message_id or "").strip()
        if not user_input or len(user_input) > 4_000:
            raise ValueError("content must contain between 1 and 4000 characters")
        if not client_id or len(client_id) > 200:
            raise ValueError("client_message_id must contain between 1 and 200 characters")
        session = self.get_session(session_id, principal=principal)
        if resource_edit:
            from backend.resource_management.edit_entry import EditTarget
            from backend.resource_management.contracts import ResourceError
            resource_edit = EditTarget.model_validate(resource_edit).model_dump()
            replay = self.store.get_turn_by_client_message(session.id, client_id)
            if replay is None:
                selected = self.tool_service.application_service.resource_management.get_edit(
                    resource_edit['edit_id'], session_id=session.id, principal=principal)
                if selected['version'] != resource_edit['expected_version']:
                    raise ResourceError('编辑副本已经变化，请重新读取。', code='RESOURCE_VERSION_CONFLICT')
        sources = None
        if settings.investigation_multi_round_enabled:
            if self.report_collection is None or not self.report_collection.agent_driven:
                raise RuntimeError('多轮调查报告查询服务未配置。')
            from .rounds import project
            sources = self.report_collection.sync_rounds(session.id, principal,
                project(self.tool_service.application_service, session.id, principal))
        elif self.report_collection and self.report_collection.active(session.id):
            sources = self.report_collection.members(session.id, principal)
        accepted = self.store.create_turn(session.id, client_message_id=client_id, user_input=user_input,
            user_metadata={'resource_edit': resource_edit} if resource_edit else None)
        if sources is not None and not accepted[1]:
            self.report_collection.freeze(session.id, accepted[0].id, principal, sources=sources)
        return accepted

    def authorize_turn(self, turn_id: str, *, principal: Principal) -> InvestigationTurn:
        turn = self.store.get_turn(turn_id)
        self.get_session(turn.session_id, principal=principal)
        return turn

    def authorize_report_handoff(
        self,
        workspace_session_id: str,
        run_id: str,
        *,
        principal: Principal,
    ) -> Any:
        self.get_session(workspace_session_id, principal=principal)
        try:
            run = self.tool_service.application_service.get_run(
                run_id, principal=principal
            )
        except (PrincipalAccessDeniedError, RunNotFoundError) as exc:
            raise InvestigationSessionNotFoundError(
                "Run is not associated with this creation workspace"
            ) from exc
        _, _, turns = self.store.conversation_snapshot(
            workspace_session_id, include_tool_messages=False
        )
        associated = any(
            (
                artifact.get("artifact_type") == "investigation_draft"
                and str(artifact.get("draft_id") or "") == run.draft_id
            )
            or (
                artifact.get("artifact_type") == "investigation_run"
                and str(artifact.get("run_id") or "") == run.run_id
            )
            for turn in turns
            for artifact in (turn.public_artifact or {},)
        )
        if settings.investigation_multi_round_enabled:
            from .rounds import draft_ids
            with self.tool_service.application_service.store._connect() as connection:
                associated = run.draft_id in draft_ids(connection, workspace_session_id, principal.id)
        if not associated:
            raise InvestigationSessionNotFoundError(
                "Run is not associated with this creation workspace"
            )
        if run.status.value != "PUBLISHED" or not run.report_version_id:
            raise ReportScopeError("Run has no published report yet")
        return run

    def authorize_report_turn(
        self,
        workspace_session_id: str,
        run_id: str,
        turn_id: str,
        *,
        principal: Principal,
    ) -> InvestigationTurn:
        run = self.authorize_report_handoff(
            workspace_session_id, run_id, principal=principal
        )
        report_session = self.store.find_session_by_anchor(f"m3-run:{run.run_id}")
        if (
            report_session is None
            or report_session.scope_type != "report"
            or report_session.report_version_id != run.report_version_id
        ):
            raise InvestigationTurnNotFoundError("report Turn was not found")
        turn = self.store.get_turn(turn_id)
        if turn.session_id != report_session.id:
            raise InvestigationTurnNotFoundError("report Turn was not found")
        return turn

    def get_messages(
        self,
        session_id: str,
        *,
        principal: Principal,
        include_tool_messages: bool = False,
    ) -> tuple[InvestigationMessage, ...]:
        self.get_session(session_id, principal=principal)
        return self.store.list_messages(
            session_id, include_tool_messages=include_tool_messages
        )

    def _draft_artifact_for(
        self,
        draft_id: str,
        *,
        principal: Principal,
        presentation_stage: Literal["suggestion", "confirmation"],
        verified_view: InvestigationDraftView | None = None,
    ) -> dict[str, Any]:
        application = self.tool_service.application_service
        view = verified_view if verified_view is not None else application.get_draft_view(draft_id, principal=principal)
        configuration = view.draft.configuration
        # Confirmed previews already come from the run snapshot. Do not re-query
        # mutable resource options while presenting this read-only history.
        if (view.draft.confirmed_revision is not None
                or (isinstance(configuration, InvestigationDraftConfiguration)
                    and configuration.judgement.strategy == "temporary_ruleset")):
            from .resources import (
                _CREATION_PLATFORM_ORDER,
                _ENABLED_CREATION_PLATFORMS,
                _PLATFORM_NAMES,
            )

            return draft_artifact(
                view, options=InvestigationOptions(
                    platforms=[
                        PlatformOption(
                            id=key,
                            name=_PLATFORM_NAMES[key],
                            available=key in _ENABLED_CREATION_PLATFORMS,
                        )
                        for key in _CREATION_PLATFORM_ORDER
                    ],
                    ruleset_revisions=[], recall_lexicons=[],
                ), presentation_stage=presentation_stage,
            ).model_dump(mode="json")
        ruleset_revision_ids: list[str] = []
        lexicon_ids: list[str] = []
        if isinstance(configuration, InvestigationDraftConfiguration):
            ruleset_revision_ids = [
                configuration.judgement.ruleset_revision_id
            ]
            if configuration.investigation.mode == "search":
                plan = configuration.investigation.recall_plan
                lexicon_ids = (
                    [plan.lexicon_id]
                    if plan.strategy == "existing_lexicon"
                    else []
                )
        options = application.query_investigation_options(
            QueryInvestigationOptions(
                mode=(
                    configuration.investigation.mode
                    if isinstance(configuration, InvestigationDraftConfiguration)
                    else "search"
                ),
                platform=(
                    configuration.platform
                    if isinstance(configuration, InvestigationDraftConfiguration)
                    and configuration.investigation.mode == "creator"
                    else None
                ),
                ruleset_revision_ids=ruleset_revision_ids,
                lexicon_ids=lexicon_ids,
                page_size=50,
            ),
            principal=principal,
        )
        return draft_artifact(
            view,
            options=options,
            recommended_lexicon_ids=set(lexicon_ids),
            presentation_stage=presentation_stage,
        ).model_dump(mode="json")

    def get_workspace_state(
        self, session_id: str, *, principal: Principal
    ) -> InvestigationWorkspaceState:
        session, messages, turns = self.store.conversation_snapshot(
            session_id, include_tool_messages=False
        )
        self._authorize(session, principal)
        latest_turn = turns[-1] if turns else None
        draft_id = ""
        artifact_run_id = ""
        presentation_stage: Literal["suggestion", "confirmation"] = "suggestion"
        for turn in reversed(turns):
            adoption = (self._verified_artifact(
                [], principal=principal, adoption_turn=turn, allow_superseded_draft=True,
            ) if turn.status == "completed" else {})
            artifact = adoption or turn.public_artifact or {}
            if artifact.get("artifact_type") == "investigation_draft":
                draft_id = str(artifact.get("draft_id") or "")
                if draft_id:
                    if artifact.get("presentation_stage") == "confirmation":
                        presentation_stage = "confirmation"
                    break
            if artifact.get("artifact_type") == "investigation_run":
                artifact_run_id = str(artifact.get("run_id") or "")
                if artifact_run_id:
                    break
        if not draft_id and not artifact_run_id:
            transcript = self.store.latest_completed_hermes_transcript(session_id)
            if transcript is not None:
                recovered_artifact = self._verified_artifact(
                    transcript,
                    principal=principal,
                    allow_superseded_draft=True,
                )
                if recovered_artifact.get("artifact_type") == "investigation_draft":
                    draft_id = str(recovered_artifact.get("draft_id") or "")
                elif recovered_artifact.get("artifact_type") == "investigation_run":
                    artifact_run_id = str(recovered_artifact.get("run_id") or "")
        if not draft_id and not artifact_run_id:
            for failed_turn in reversed(turns):
                if failed_turn.status not in {"error", "interrupted"}:
                    continue
                recovered_artifact = self._verified_checkpoint_artifact(
                    failed_turn,
                    principal=principal,
                    allow_superseded_draft=True,
                )
                if recovered_artifact.get("artifact_type") == "investigation_draft":
                    draft_id = str(recovered_artifact.get("draft_id") or "")
                    if draft_id:
                        break
                if recovered_artifact.get("artifact_type") == "investigation_run":
                    artifact_run_id = str(recovered_artifact.get("run_id") or "")
                    if artifact_run_id:
                        break
        current_artifact: dict[str, Any] = {}
        rounds = ()
        if settings.investigation_multi_round_enabled:
            from .rounds import project
            rounds = project(self.tool_service.application_service, session_id, principal)
            if rounds:
                # An older draft read in chat must not replace the editable round.
                current_id = rounds[-1]['draft_id']
                if current_id != draft_id:
                    presentation_stage = 'suggestion'
                draft_id, artifact_run_id = current_id, ''
            if self.report_collection:
                self.report_collection.sync_rounds(session_id, principal, rounds)
        run = None
        report_messages: tuple[InvestigationMessage, ...] = ()
        latest_report_turn = None
        if artifact_run_id:
            run = self.tool_service.application_service.get_run(
                artifact_run_id, principal=principal
            )
            draft_id = run.draft_id
            presentation_stage = "confirmation"
        if draft_id:
            current_artifact = self._draft_artifact_for(
                draft_id,
                principal=principal,
                presentation_stage=presentation_stage,
            )
            if run is None:
                run = self.tool_service.application_service.find_run_for_draft(
                    draft_id, principal=principal
                )
            if run is not None and run.report_version_id:
                report_session = self.store.find_session_by_anchor(
                    f"m3-run:{run.run_id}"
                )
                if report_session is not None:
                    if (
                        report_session.scope_type != "report"
                        or report_session.report_version_id != run.report_version_id
                    ):
                        raise ReportScopeError(
                            "Run report Session anchor does not match its published report"
                        )
                    _, report_messages, report_turns = self.store.conversation_snapshot(
                        report_session.id, include_tool_messages=False
                    )
                    latest_report_turn = report_turns[-1] if report_turns else None
        return InvestigationWorkspaceState(
            session=session,
            messages=messages,
            latest_turn=latest_turn,
            draft_artifact=current_artifact,
            run=run,
            report_messages=report_messages,
            latest_report_turn=latest_report_turn,
            rounds=rounds,
            region_selection_history=self._region_selection_history(session_id, principal),
        )

    def _region_selection_history(self, session_id, principal):
        from .scope_selection import history
        return tuple(event.model_dump(mode='json') for event in history(
            self.tool_service.application_service, session_id=session_id, principal=principal))

    def generate_confirmation_preview(
        self,
        session_id: str,
        *,
        client_message_id: str,
        draft_id: str,
        expected_revision: int,
        principal: Principal,
    ) -> InvestigationTurn:
        state = self.get_workspace_state(session_id, principal=principal)
        artifact = state.draft_artifact or {}
        if str(artifact.get("draft_id") or "") != str(draft_id or "").strip():
            raise InvestigationSessionNotFoundError(
                "Draft is not associated with this creation workspace"
            )
        if state.run is not None:
            raise DraftAlreadyConfirmedError(draft_id)
        current_revision = int(artifact.get("draft_revision") or 0)
        if current_revision != expected_revision:
            raise DraftRevisionConflictError(
                f"expected revision {expected_revision}, current revision is {current_revision}"
            )
        current_artifact = dict(artifact)
        current_artifact["presentation_stage"] = "confirmation"
        turn, _ = self.store.create_turn(
            session_id,
            client_message_id=str(client_message_id or "").strip(),
            user_input="生成任务配置",
        )
        if turn.status == "completed":
            return turn
        if turn.status != "running":
            raise InvestigationTurnNotFoundError(
                "confirmation preview Turn is not writable"
            )
        answer = "已根据你的调查目标和平台选择生成任务配置，请确认。"
        session = state.session
        self.store.complete_turn(
            turn.id,
            answer=answer,
            trace_messages=[],
            pending_sources=[],
            grounding_validation={
                "status": "passed",
                "source_count": 0,
                "warnings": [],
            },
            resolved_references=[],
            all_tool_calls=[],
            query_receipts=[],
            summary_text=session.summary_text,
            active_focus=session.active_focus,
            ordered_referents=[
                item.model_dump(mode="json") for item in session.ordered_referents
            ],
            last_claim_id=session.last_claim_id,
            last_finding_id=session.last_finding_id,
            last_evidence_id=session.last_evidence_id,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            llm_call_count=0,
            stop_reason="deterministic_confirmation_preview",
            context_accounting=[],
            grounding_issues=[],
            grounding_repair_count=0,
            scope_repair_count=0,
            source_repair_count=0,
            semantic_rewrite_count=0,
            scope_initial_draft="",
            scope_repaired_draft="",
            scope_initial_issues=[],
            scope_remaining_issues=[],
            public_artifact=current_artifact,
        )
        completed = self.store.get_turn(turn.id)
        self.store.append_public_turn_event(
            completed.id,
            stage="completed",
            answer=answer,
            artifact=current_artifact,
        )
        return completed

    def accept_resume(self, turn_id: str) -> tuple[InvestigationTurn, bool]:
        turn = self.store.get_turn(turn_id)
        self.principal_for_session(turn.session_id)
        if turn.status in {"completed", "error"}:
            return turn, True
        if turn.status != "interrupted":
            raise InvestigationTurnNotFoundError("turn is not resumable")
        return self.store.begin_resume(turn.id), False

    def execute_resume(self, turn_id: str) -> TurnResult:
        return self.execute_turn(turn_id)

    def _execute_collection_turn(self, turn):
        from backend.report_collection.tools import PROMPT, AGENT_PROMPT, DIRECT_DELIVERY_PROMPT
        from backend.report_collection.context import project_history, restore_transcript
        from backend.hermes_runtime.adapter import turn_system_context
        try:
            self.report_collection.sources(turn.session_id, turn.id)
            self.report_collection.validate_history(turn.session_id)
            stored_history = self.store.hermes_conversation_history(turn.id)
            compact = self.report_collection.agent_driven and self.report_collection.compact_history
            boundary = self.store.hermes_history_compaction_boundary(turn.id, stored_history) if compact else 0
            history = project_history(stored_history, keep_from=boundary) if compact else stored_history
            user_message = self.store.get_user_message_for_turn(turn.id).content
            with ExitStack() as streams:
                streams.enter_context(self._activity_emitter.bind_turn(turn.session_id, turn.id))
                streams.enter_context(self.runtime_binding.product_mode_execution(
                    session_runtime_home(self.hermes_state_dir, turn.session_id), product_mode="collection"))
                agent = self._agent(turn.session_id, product_mode="collection")
                from backend.report_collection.delivery import direct_delivery_boundary
                delivery_state = streams.enter_context(direct_delivery_boundary(
                    agent, self.report_collection, turn.session_id, turn.id))
                agent_driven = self.report_collection.agent_driven
                collection_prompt = (DIRECT_DELIVERY_PROMPT if self.report_collection.direct_delivery
                    and self.report_collection.direct_delivery_guidance else AGENT_PROMPT) if agent_driven else PROMPT
                repair = "" if agent_driven else "本轮只能使用会话报告工具。答案只可引用本轮查询范围内的证据。"
                totals = {k: 0 for k in ("input_tokens", "output_tokens", "total_tokens", "api_calls")}
                for attempt in range(1 if agent_driven else 2):
                    with turn_system_context(agent, repair):
                        result = agent.run_conversation(user_message, system_message=collection_prompt,
                            conversation_history=deepcopy(history), task_id=turn.id)
                    for key in totals:
                        totals[key] += int(result.get(key) or 0)
                    try:
                        if not agent_driven:
                            self.report_collection.validate_answer(turn.session_id, turn.id, str(result.get("final_response") or ""))
                        break
                    except ValueError as exc:
                        self.report_collection.record_call("validate_answer", {}, turn.session_id, turn.id,
                            {"ok": False, "error": str(exc), "attempt": attempt + 1})
                        if attempt: raise
                        repair = str(exc)
                result.update(totals)
                result['api_calls'] = max(0, totals['api_calls'] - delivery_state['local_completions'])
            if not result.get("completed") or result.get("failed") or result.get("interrupted"):
                raise RuntimeError("报告会话模型未完成回答。")
            transcript = HermesInvestigationAgentService._validate_completed_transcript(
                result, history=history, user_message=user_message)
            transcript = restore_transcript(transcript, model_history=history, stored_history=stored_history)
            return self._persist_result(turn, result, transcript, {}, history_count=len(stored_history or []),
                include_proposal_presentations=False)
        except Exception:
            logger.exception("Collection turn failed: %s", turn.id)
            self.store.fail_turn(turn.id, error_code="collection_turn_failed",
                safe_message="统一报告查询未完成，请检查报告权限或稍后重试。", retryable=False)
            return self.store.turn_result(turn.id)

    def execute_turn(self, turn_id: str) -> TurnResult:
        turn = self.store.get_turn(turn_id)
        if turn.status in {"completed", "error"}:
            return self.store.turn_result(turn.id, idempotent_replay=True)
        if turn.status != "running":
            raise InvestigationTurnNotFoundError("turn is not ready for execution")
        session = self.store.get_session(turn.session_id)
        principal = self.principal_for_session(session.id)
        if (not settings.investigation_multi_round_enabled
                and self.report_collection and self.report_collection.active(session.id)):
            return self._execute_collection_turn(turn)
        history = self.store.hermes_conversation_history(turn.id)
        recovered_history = self._failed_turn_checkpoint_history(
            turn, principal=principal
        )
        if recovered_history:
            history = [*(history or []), *recovered_history]
        user_message = self.store.get_user_message_for_turn(turn.id).content
        # A creation session is single-report scoped. Once its run is published,
        # keep accidental creation-route turns deterministic and preserve the
        # published report. The UI normally sends report questions through the
        # report-turn endpoint; this guard protects retries and stale clients.
        workspace_state = self.get_workspace_state(session.id, principal=principal)
        if (not settings.continuous_resource_session_enabled and not settings.investigation_multi_round_enabled and workspace_state.run is not None
                and workspace_state.run.status.value == "PUBLISHED"):
            transcript = list(history or [])
            transcript.extend(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": PUBLISHED_SESSION_NOTICE},
                ]
            )
            return self._persist_result(
                turn,
                {
                    "final_response": PUBLISHED_SESSION_NOTICE,
                    "turn_exit_reason": "published_session_requires_new_session",
                    "completed": True,
                    "api_calls": 0,
                },
                transcript,
                {},
            )
        try:
            context = self._presentation_context_for_turn(turn)
        except ConfigurationValidationError as exc:
            self.store.fail_turn(turn.id, error_code=exc.code,
                                 safe_message=str(exc) + str(exc.details.get("recovery", "")), retryable=False)
            return self.store.turn_result(turn.id)
        if context and history:
            # Hermes caches its initial system prompt. Enrich existing Proposal
            # ToolResults with completed public artifact identity at read time.
            # This is trusted tool context, never user-visible assistant prose.
            projected = []
            for message in history:
                replacement = message
                if message.get("role") == "tool":
                    try:
                        payload = json.loads(message["content"])
                    except (ValueError, TypeError):
                        payload = {}
                    if isinstance(payload, dict) and payload.get("status") == "ok":
                        data = payload.get('data')
                        snapshot = data.get('proposal_snapshot') if isinstance(data, dict) and data.get('kind') == 'ruleset' else data
                        records = [record for record in context if snapshot == record["snapshot"]]
                        if records:
                            payload["completed_public_presentations"] = [
                                {key: record[key] for key in ("presentation_id", "proposal_id", "proposal_version", "assistant_message_id", "presented_at")}
                                for record in records]
                            replacement = {**message, "content": json.dumps(payload, ensure_ascii=False)}
                projected.append(replacement)
            history = projected
        self._notify(turn.id, "call_qwen")
        edit_target = self.store.get_user_message_for_turn(turn.id).metadata.get('resource_edit')
        if edit_target:
            try:
                selected = self.tool_service.application_service.resource_management.get_edit(
                    edit_target['edit_id'], session_id=session.id, principal=principal)
                if selected['version'] != edit_target['expected_version']:
                    from backend.resource_management.contracts import ResourceError
                    raise ResourceError('编辑副本已经变化，本轮未开始修改。', code='RESOURCE_VERSION_CONFLICT')
            except Exception as exc:
                self.store.fail_turn(turn.id, error_code=getattr(exc, 'code', 'RESOURCE_EDIT_UNAVAILABLE'),
                    safe_message='所选编辑副本已变化或不可访问，本轮未开始修改。请重新读取后再试。', retryable=False)
                return self.store.turn_result(turn.id)
        self.tool_service.begin_conversation_turn(session.id, turn.id, resource_edit=edit_target)
        provider_route = self._provider_route(user_message)
        runtime_started = False
        runtime_finished = False
        delivery_state = {'local_completions': 0}
        report_sources = None
        try:
            if settings.investigation_multi_round_enabled:
                report_sources = self.report_collection.sources(session.id, turn.id)
                self.report_collection.validate_history(session.id)
                from backend.report_collection.scopes import authorized_history_for_model
                history = authorized_history_for_model(self.report_collection, session.id, history)
            # Keep one immutable reference projection for the prefix fence and
            # this turn's slice. Hermes shallow-copies and may mutate its input.
            history = prepare_creation_history(history)
            with ExitStack() as public_streams:
                from backend.resource_management.edit_entry import editing_entry
                public_streams.enter_context(editing_entry(edit_target))
                public_streams.enter_context(
                    self._activity_emitter.bind_turn(session.id, turn.id)
                )
                public_streams.enter_context(
                    self._answer_streamer.bind_turn(session.id, turn.id)
                )
                if self.fake_runtime:
                    agent = self._agent(session.id, provider_route=provider_route)
                    from backend.hermes_runtime.adapter import turn_system_context
                    public_streams.enter_context(turn_system_context(agent, self._turn_context(workspace_state, report_sources=report_sources)))
                    runtime_started = True
                    result = agent.run_conversation(
                        user_message,
                        system_message=self._system_message(workspace_state),
                        conversation_history=deepcopy(history),
                        task_id=turn.id,
                    )
                else:
                    with self.runtime_binding.product_mode_execution(
                        session_runtime_home(self.hermes_state_dir, session.id),
                        product_mode="creation"
                    ):
                        report_error = ""
                        if settings.continuous_resource_session_enabled and not settings.investigation_multi_round_enabled:
                            from .continuity import prepare_report
                            try:
                                history = prepare_creation_history(prepare_report(self, workspace_state, principal, history))
                            except Exception:
                                self.runtime_binding.release_published_report_session(session.id)
                                report_error = "本轮报告读取服务不可用，不能声称已读取或报告不存在；仍可处理独立词库和规则。"
                                logger.exception("Report binding unavailable for workspace %s", session.id)
                        agent = self._agent(session.id, provider_route=provider_route)
                        if settings.investigation_multi_round_enabled:
                            from backend.report_collection.delivery import direct_delivery_boundary
                            delivery_state = public_streams.enter_context(direct_delivery_boundary(
                                agent, self.report_collection, session.id, turn.id))
                        from backend.hermes_runtime.adapter import turn_system_context
                        runtime_started = True
                        with turn_system_context(agent, self._turn_context(workspace_state, report_error, report_sources=report_sources)):
                            result = agent.run_conversation(
                                user_message,
                                system_message=self._system_message(workspace_state),
                                conversation_history=deepcopy(history),
                                task_id=turn.id,
                            )
            runtime_finished = True
            if not isinstance(result, dict):
                raise RuntimeError("Hermes returned a non-object Turn result")
            if delivery_state['local_completions']:
                result['api_calls'] = max(0, int(result.get('api_calls') or 0) - delivery_state['local_completions'])
            if bool(result.get("interrupted")):
                self._answer_streamer.interrupt(turn.id)
                recovered = self._complete_saved_checkpoints(turn, principal, history, user_message, result)
                if recovered is not None:
                    return recovered
                raise RuntimeError("Hermes creation Turn was interrupted")
            if bool(result.get("failed")) or not bool(result.get("completed", True)):
                self._answer_streamer.interrupt(turn.id)
                checkpoint_messages = self._checkpoint_messages_for_turn(
                    turn, principal=principal, include_recovery_instruction=False
                )
                from .save_recovery import completion_answer
                saved_answer = completion_answer(self._saved_checkpoints(checkpoint_messages))
                try:
                    artifact = self._verified_artifact(
                        checkpoint_messages, principal=principal, adoption_turn=turn)
                except Exception:
                    if not saved_answer:
                        raise
                    # A separate draft/preview failure must not erase a proven
                    # resource save. Report only the verified save in this case.
                    artifact = {}
                if artifact.get("artifact_type") in {
                    "investigation_draft",
                    "investigation_run",
                } or saved_answer:
                    answer = '\n\n'.join(filter(None, [
                        self._checkpoint_completion_answer(artifact) if artifact else '', saved_answer]))
                    transcript = [
                        *(history or []),
                        {"role": "user", "content": user_message},
                        *checkpoint_messages,
                        {"role": "assistant", "content": answer},
                    ]
                    result = {
                        **result,
                        "completed": True,
                        "failed": False,
                        "interrupted": False,
                        "final_response": answer,
                        "turn_exit_reason": ("recovered_successful_application_checkpoint" if artifact
                                             else "recovered_successful_resource_save"),
                    }
                    return self._persist_result(
                        turn,
                        result,
                        transcript,
                        artifact,
                        history_count=len(history or []),
                        include_proposal_presentations=False,
                    )
                self.store.fail_turn(
                    turn.id,
                    error_code=str(
                        result.get("error_code") or "hermes_execution_failed"
                    ),
                    safe_message=(
                        str(result.get("final_response") or "").strip()
                        or "调查方案生成暂时无法完成。"
                    ) + ("\n" + self._binding_failure_notice(turn) if self._binding_failure_notice(turn) else ""),
                    retryable=bool(result.get("retryable", False)),
                )
                self._answer_streamer.release(turn.id)
                return self.store.turn_result(turn.id)
            transcript = HermesInvestigationAgentService._validate_completed_transcript(
                result, history=history, user_message=user_message
            )
            artifact = self._verified_artifact(
                transcript[len(history or []):], principal=principal, adoption_turn=turn
            )
            return self._persist_result(
                turn,
                result,
                transcript,
                artifact,
                history_count=len(history or []),
            )
        except HermesReportToolsUnavailable:
            # The agent was rejected before inference, so this is a known
            # configuration failure, not an unknown business-write outcome.
            self._answer_streamer.interrupt(turn.id)
            self.store.fail_turn(
                turn.id,
                error_code="report_tools_unavailable",
                safe_message="报告问答工具未加载完整，本轮未执行操作，已有数据和交付进度保留。请检查启动目录和插件配置后再试。",
                retryable=False,
                stop_reason="environment_preflight_failed",
            )
            self._answer_streamer.release(turn.id)
            return self.store.turn_result(turn.id)
        except Exception as exc:
            current = self.store.get_turn(turn.id)
            if current.status == "completed":
                self._answer_streamer.release(turn.id)
                return self.store.turn_result(turn.id)
            self._answer_streamer.interrupt(turn.id)
            # Only runtime failure may use a save receipt as an alternative
            # answer. Never turn a history/protocol validation failure into success.
            if runtime_started and not runtime_finished:
                try:
                    recovered = self._complete_saved_checkpoints(turn, principal, history, user_message, {})
                    if recovered is not None:
                        return recovered
                except Exception:
                    logger.warning('Resource save recovery unavailable for Turn %s', turn.id, exc_info=True)
            self.store.mark_interrupted(
                turn.id,
                error_code="hermes_unknown_outcome",
                safe_message="调查方案生成结果暂时无法确认，可以安全恢复。" + self._binding_failure_notice(turn),
                retryable=True,
            )
            self._answer_streamer.release(turn.id)
            raise RuntimeError("Hermes creation Turn ended with an unknown outcome") from exc
        finally:
            self.tool_service.end_conversation_turn(session.id)

    def _presentation_context_for_turn(self, turn: InvestigationTurn) -> list[dict]:
        from .approval import published_presentations

        user = self.store.get_user_message_for_turn(turn.id)
        with self.store._connect() as connection:
            records, _ = published_presentations(
                connection, session_id=turn.session_id, before_sequence=user.sequence,
            )
        return records

    def _checkpoint_messages_for_turn(
        self,
        turn: InvestigationTurn,
        *,
        principal: Principal,
        include_recovery_instruction: bool = True,
    ) -> list[dict[str, Any]]:
        receipts = (
            self.tool_service.application_service.store
            .successful_conversation_tool_receipts(
                session_id=turn.session_id,
                turn_id=turn.id,
                principal=principal.id,
            )
        )
        from .save_recovery import conversation_saves
        from .store import RESOURCE_SAVE_TOOLS
        # Save success belongs to the resource transaction, even if the outer
        # tool log never finished. Verify saves rather than trusting its status.
        receipts = [r for r in receipts if r['tool_name'] not in RESOURCE_SAVE_TOOLS] + conversation_saves(
            self.tool_service.application_service, session_id=turn.session_id, turn_id=turn.id, principal=principal)
        if not receipts:
            return []
        calls = []
        results = []
        for receipt in receipts:
            call_id = f"recovered-checkpoint:{receipt['receipt_id']}"
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": receipt["tool_name"],
                        "arguments": {},
                    },
                }
            )
            results.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": receipt["tool_name"],
                    "content": json.dumps(
                        {
                            **receipt["response"],
                            "recovered_from_durable_checkpoint": True,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
        messages = [
            {"role": "assistant", "content": "", "tool_calls": calls},
            *results,
        ]
        if include_recovery_instruction:
            messages.append({
                "role": "assistant",
                "content": (
                    "系统已从持久化检查点恢复以上成功操作。它们已经完成，不要因原回合失败而重复执行；"
                    "只继续尚未完成的后续步骤，原请求对这些未完成步骤仍然有效。只有当前用户明确要求"
                    "重新生成、替换或另存时，才重复已经完成的写操作。"
                ),
            })
        return messages

    @staticmethod
    def _saved_checkpoints(messages):
        from .store import RESOURCE_SAVE_TOOLS
        return [{'response': json.loads(message['content'])} for message in messages
                if message.get('role') == 'tool' and message.get('name') in RESOURCE_SAVE_TOOLS]

    def _complete_saved_checkpoints(self, turn, principal, history, user_message, result):
        from .save_recovery import completion_answer
        messages = self._checkpoint_messages_for_turn(turn, principal=principal, include_recovery_instruction=False)
        answer = completion_answer(self._saved_checkpoints(messages))
        if not answer:
            return None
        transcript = [*(history or []), {'role': 'user', 'content': user_message},
                      *messages, {'role': 'assistant', 'content': answer}]
        return self._persist_result(turn, {**result, 'completed': True, 'failed': False, 'interrupted': False,
            'final_response': answer, 'turn_exit_reason': 'recovered_successful_resource_save'},
            transcript, {}, history_count=len(history or []), include_proposal_presentations=False)

    def _failed_turn_checkpoint_history(
        self,
        current_turn: InvestigationTurn,
        *,
        principal: Principal,
    ) -> list[dict[str, Any]]:
        turns = self.store.list_turns(current_turn.session_id)
        current_index = next(
            index for index, item in enumerate(turns) if item.id == current_turn.id
        )
        previous = list(turns[:current_index])
        last_completed = max(
            (
                index
                for index, item in enumerate(previous)
                if item.status == "completed"
            ),
            default=-1,
        )
        recovered: list[dict[str, Any]] = []
        for failed_turn in previous[last_completed + 1 :]:
            if failed_turn.status not in {"error", "interrupted"}:
                continue
            checkpoint_messages = self._checkpoint_messages_for_turn(
                failed_turn, principal=principal
            )
            if not checkpoint_messages:
                continue
            # ``hermes_conversation_history`` closes every failed user turn
            # with a synthetic assistant failure notice.  A recovered
            # checkpoint starts with an assistant tool-call message, so
            # appending it directly creates ``assistant, assistant``.  Hermes
            # repairs that alternation in place before the provider call,
            # which makes the completed transcript differ from the exact
            # history we supplied and trips the fail-closed transcript fence.
            #
            # Keep each durable checkpoint as its own protocol-valid recovery
            # turn.  The bridge is deliberately explicit that it is system
            # recovery context, not a fresh request to repeat the mutation.
            recovered.append(
                {
                    "role": "user",
                    "content": (
                        "系统恢复上下文：请读取下面从上一回合持久化收据恢复的成功操作。"
                        "这些操作已经完成，不要重复执行；只据此继续当前用户尚未完成的步骤。"
                    ),
                }
            )
            recovered.extend(checkpoint_messages)
        return recovered

    def _verified_checkpoint_artifact(
        self,
        turn: InvestigationTurn,
        *,
        principal: Principal,
        allow_superseded_draft: bool = False,
    ) -> dict[str, Any]:
        messages = self._checkpoint_messages_for_turn(turn, principal=principal)
        if not messages:
            return {}
        return self._verified_artifact(
            messages,
            principal=principal,
            allow_superseded_draft=allow_superseded_draft,
            adoption_turn=turn,
        )

    @staticmethod
    def _checkpoint_completion_answer(artifact: dict[str, Any]) -> str:
        if artifact.get("artifact_type") == "investigation_run":
            status = str((artifact.get("run") or {}).get("status") or "QUEUED")
            return (
                "调查任务已经确认，成功写入的执行状态已恢复。\n\n"
                f"- 当前状态：{status}\n"
                "- 后续操作：可在当前页面继续查看采集、分析和报告进度。"
            )

        draft = artifact.get("draft") or {}
        preview = artifact.get("confirmation_preview") or {}
        configuration = draft.get("configuration") or {}
        platform_names = {"dy": "抖音", "xhs": "小红书", "ks": "快手", "wb": "微博"}
        platform = str(preview.get("platform") or configuration.get("platform") or "")
        terms = [str(item) for item in preview.get("resolved_search_terms") or []]
        term_summary = "、".join(terms[:10])
        if len(terms) > 10:
            term_summary += f"等 {len(terms)} 个"
        lines = [
            "调查草案已成功保存，尚未开始采集。",
            "",
            f"- 标题：{draft.get('title') or '未命名调查'}",
            f"- 平台：{platform_names.get(platform, platform or '未设置')}",
        ]
        if term_summary:
            lines.append(f"- 实际搜索词：{term_summary}")
        if preview.get("search_terms_notice"):
            lines.append(f"- {preview['search_terms_notice']}")
        # 数量取预览里的生效值（来自采集与分析设置），不取草案里的请求值
        lines.extend(
            [
                (f"- 本任务上限：{preview.get('max_posts_per_keyword', 1)} 条"
                 if preview.get("mode") == "creator"
                 else f"- 每关键词上限：{preview.get('max_posts_per_keyword', 1)} 条"),
                f"- 单任务总量：{preview.get('max_notes', 1)} 条",
            ]
        )
        if preview.get('relevance_scope_state') or preview.get('relevance_scope'):
            state = preview.get('relevance_scope_state') or {}
            scope = preview.get('relevance_scope') or {}
            if state.get('status') == 'pending':
                lines.append(f"- 调查地域待澄清：{state.get('target_text', '')}；{state.get('reason', '')}")
            elif scope:
                lines.append(f"- 调查地域：{scope['target']['name']}（{scope['province']['name']}）")
        if preview.get('blockers'):
            lines.extend('- ' + item['message'] for item in preview['blockers'])
            lines.append("\n当前配置不能启动，可继续修改草案或澄清要求。")
        else:
            lines.append("\n可以继续确认并启动任务，或先修改草案。")
        return "\n".join(lines)

    def _binding_failure_notice(self, turn: InvestigationTurn, trace_messages: list[dict] | None = None) -> str:
        from .approval import BINDING_FAILURES

        if self.tool_service.application_service.store.successful_adoption_results(
            session_id=turn.session_id, turn_id=turn.id,
            principal=self.principal_for_session(turn.session_id).id,
        ):
            return ""

        # Receipt provenance, not model prose, determines this minimal public fallback.
        with self.tool_service.application_service.store._connect() as connection:
            committed = connection.execute(
                """SELECT 1 FROM draft_creation_operations o
                   JOIN investigation_creation_tool_receipts r ON r.receipt_id=o.active_receipt_id
                   JOIN ruleset_proposal_conversation_bindings b ON b.receipt_id=r.receipt_id
                   JOIN ruleset_proposal_approvals a ON a.draft_id=o.draft_id
                     AND a.session_id=r.session_id AND a.runtime_turn_id=r.turn_id
                     AND a.tool_call_id=r.tool_call_id AND a.draft_revision=1
                   WHERE r.session_id=? AND b.application_turn_id=?
                     AND o.principal=? AND o.state='CREATED'
                     AND r.tool_name IN ('use_ruleset_proposal','configure_investigation_draft') LIMIT 1""",
                (turn.session_id, turn.id, self.principal_for_session(turn.session_id).id),
            ).fetchone()
            if committed:
                return "调查草案和规则采用已保存，但确认预览暂时未能恢复。请读取现有草案后继续，不要重复创建。"
            failures = connection.execute(
                """SELECT r.response_json FROM investigation_creation_tool_receipts r
                   JOIN ruleset_proposal_conversation_bindings b ON b.receipt_id=r.receipt_id
                   WHERE r.session_id=? AND b.application_turn_id=?
                     AND r.tool_name IN ('use_ruleset_proposal','configure_investigation_draft') AND r.status='FAILED'""",
                (turn.session_id, turn.id),
            ).fetchall()
        errors = [json.loads(row["response_json"]).get("error", {}) for row in failures]
        # Schema rejection happens before a mutation receipt exists. Preserve its
        # basic failure reason from the existing validated ToolResult transcript.
        use_ids = {call.get("id") for item in trace_messages or [] if item.get("role") == "assistant"
                   for call in item.get("tool_calls") or []
                   if (call.get("function") or {}).get("name") in {"use_ruleset_proposal", "configure_investigation_draft"}}
        last_use_status = ""
        for item in trace_messages or []:
            if item.get("role") != "tool" or item.get("tool_call_id") not in use_ids:
                continue
            try:
                payload = json.loads(item.get("content") or "{}")
            except (ValueError, TypeError):
                # Hermes can append diagnostic prose to a rejected tool result.
                # It is not an authoritative envelope; keep a basic failure notice.
                last_use_status = "error"
                errors.append({"code": "CONFIGURATION_INVALID"})
                continue
            if isinstance(payload, dict):
                last_use_status = payload.get("status", "")
                if last_use_status == "error":
                    errors.append(payload.get("error") or {})
        notices = []
        for error in errors:
            reason, recovery = BINDING_FAILURES.get(error.get("code"),
                ("配置或操作条件未满足。", "请检查规则展示和草案配置后继续。"))
            notices.append("本次规则采用操作未成功：" + reason + recovery)
        return "\n".join(dict.fromkeys(notices))

    def add_turn_node_observer(self, observer: Callable[[str, str], None]) -> None:
        self._turn_node_observers.append(observer)

    def owns_turn(self, turn_id: str) -> bool:
        turn = self.store.get_turn(turn_id)
        return self.store.get_session(turn.session_id).scope_type == "creation"

    def _system_message(self, workspace):
        prompt = CREATION_SYSTEM_PROMPT + RESOURCE_PROMPT
        if settings.investigation_multi_round_enabled:
            from .rounds import GUIDANCE
            from backend.report_collection.tools import DIRECT_DELIVERY_PROMPT, AGENT_PROMPT
            start = prompt.index('会话边界必须服从应用状态：')
            end = prompt.index('The user may want to query', start)
            prompt = prompt[:start] + GUIDANCE + prompt[end:]
            prompt = prompt.replace('若当前会话已进入执行或报告阶段，沿用现有“新建调查”入口在新会话准备新 Draft，不替换原会话的已启动任务。', '')
            report_prompt = (DIRECT_DELIVERY_PROMPT if self.report_collection.direct_delivery
                and self.report_collection.direct_delivery_guidance else AGENT_PROMPT)
            prompt += '\n以下约定仅适用于报告问答：\n' + report_prompt
        elif settings.continuous_resource_session_enabled:
            from .continuity import GUIDANCE
            start = prompt.index('会话边界必须服从应用状态：')
            end = prompt.index('The user may want to query', start)
            prompt = prompt[:start] + GUIDANCE + prompt[end:]
        return prompt

    def _turn_context(self, workspace, report_error="", *, report_sources=None):
        from backend.resource_management.edit_entry import active_edit, context_text
        context = context_text(active_edit.get())
        # get_workspace_state authorizes this session and refreshes its active
        # draft from storage. UI selections need not fabricate chat/tool history
        # to become visible to a cached Agent on the next turn.
        draft = workspace.draft_artifact.get("draft")
        if draft:
            configuration = public_configuration(draft.get("configuration") or {})
            state = configuration.get("relevance_scope_state")
            scope = configuration.get("relevance_scope")
            if state and state.get("status") in {"pending", "cleared"}:
                # A pending edit retains the previous stored scope for recovery;
                # that previous value is not the effective investigation scope.
                scope = None
            current = {
                "draft_id": draft["id"],
                "current_revision": draft["current_revision"],
                "status": draft["status"],
                "relevance_scope": scope,
                "relevance_scope_state": state,
                "selection_policy": (state or {}).get("selection_policy")
                    or (scope or {}).get("selection_policy"),
            }
            context += (
                "\n服务端当前调查草案（本轮最新状态；历史对话及旧工具回执不代表当前配置；"
                "地域待澄清时不得把保留的旧地域当作有效范围；除非用户明确要求，保留现有规则与词库）："
                + json.dumps(current, ensure_ascii=False)
            )
        if settings.investigation_multi_round_enabled:
            directory = [{**{k: r[k] for k in ('round_number', 'label', 'draft_id', 'title', 'draft_revision', 'draft_status')},
                'run': {k: r['run'][k] for k in ('run_id', 'status', 'report_version_id')} if r['run'] else None}
                for r in workspace.rounds]
            if report_sources is not None:
                reports = [{'label': s['label'], 'report_ref': s['ref'], 'report_version_id': s['version']}
                           for s in report_sources]
                context += '\n本轮已授权的会话报告目录（名称与引用，不是正文证据；不代表全部历史报告）：' + json.dumps(reports, ensure_ascii=False)
            return context + '\n本会话调查轮次（只表示关联和状态，不是报告证据）：' + json.dumps(directory, ensure_ascii=False)
        if settings.continuous_resource_session_enabled:
            from .continuity import report_context
            context += report_context(workspace.session.id)
            if report_error:
                context += "\n" + report_error
            if workspace.run:
                context += "\n服务端当前任务状态：" + workspace.run.status.value + "，原任务配置已冻结。"
            # Old report-only turns are context for references, not evidence. Do
            # not copy their tools/opaque aliases into this workspace namespace.
            if workspace.report_messages:
                context += "\n此前报告对话（仅帮助理解指代；具体证据重新查询）：" + json.dumps(
                    [{'role': m.role, 'content': m.content[:2000]} for m in workspace.report_messages[-6:]], ensure_ascii=False)
        return context

    @staticmethod
    def _provider_route(user_message: str) -> str:
        # The coordinator always uses the normal provider. Resource generation
        # is routed by the invoked business tool, not by matching user language.
        return "default"

    def _agent(self, session_id: str, *, provider_route: str = "default", product_mode: str = "creation") -> Any:
        if provider_route != "default":
            raise ValueError("resource generation is routed inside its business tool")
        with self._agent_lock:
            cache_key: object = (
                session_id
                if provider_route == "default"
                else (session_id, provider_route)
            )
            if product_mode != "creation":
                cache_key = (session_id, product_mode)
            agent = self._agents.get(cache_key)
            from backend.resource_management.edit_entry import active_edit
            edit_binding = dict(active_edit.get()) if product_mode == 'creation' and active_edit.get() else None
            if agent is not None and getattr(agent, '_resource_edit_binding', None) != edit_binding:
                # Native tool schemas are cached on the Agent. A new explicit
                # entry must not reuse the previous target or restricted catalog.
                self._agents.pop(cache_key)
                close = getattr(agent, 'close', None)
                if callable(close):
                    close()
                agent = None
            if agent is None:
                callbacks = HermesInvestigationAgentService._compose_agent_callbacks(
                    self._answer_streamer.agent_callbacks(session_id),
                    self._activity_emitter.agent_callbacks(session_id),
                )
                callbacks.setdefault("stream_delta_callback", lambda _delta: None)
                if self.fake_runtime:
                    agent = FakeCreationHermesAgent(
                        session_id=session_id,
                        tool_service=self.tool_service,
                        principal_resolver=self.principal_for_session,
                        **callbacks,
                    )
                else:
                    provider_options = {
                        "base_url": settings.conversation_base_url,
                        "api_key": settings.conversation_api_key,
                        "model": settings.conversation_model,
                    }
                    if settings.conversation_model.lower().startswith("deepseek"):
                        provider_options["request_overrides"] = {
                            "extra_body": {"thinking": {"type": "disabled"}},
                        }
                    agent = self.runtime_binding.create_agent(
                        session_id=session_id,
                        agent_factory=self.agent_factory,
                        product_mode=product_mode,
                        **provider_options,
                        **callbacks,
                    )
                agent._resource_edit_binding = edit_binding
                self._agents[cache_key] = agent
            return agent

    def _verified_artifact(
        self,
        messages: list[dict[str, Any]],
        *,
        principal: Principal,
        allow_superseded_draft: bool = False,
        adoption_turn: InvestigationTurn | None = None,
    ) -> dict[str, Any]:
        adoption_results = []
        if adoption_turn is not None:
            self._authorize(self.store.get_session(adoption_turn.session_id), principal)
            adoption_results = self.tool_service.application_service.store.successful_adoption_results(
                session_id=adoption_turn.session_id, turn_id=adoption_turn.id, principal=principal.id,
            )
        adoption_position = len(messages)
        calls: dict[str, str] = {}
        successful_results: list[tuple[int, str, dict[str, Any]]] = []
        for index, message in enumerate(messages):
            if message.get("role") == "assistant":
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    calls[str(call.get("id") or "")] = str(function.get("name") or "")
                continue
            if message.get("role") != "tool":
                continue
            reported_name = str(message.get("name") or "")
            tool_name = (
                reported_name
                if reported_name in self.tool_service.allowed_tool_names
                else calls.get(str(message.get("tool_call_id") or ""), "")
            )
            if tool_name not in self.tool_service.allowed_tool_names:
                continue
            if (tool_name in {"use_ruleset_proposal", "configure_investigation_draft"}
                    or calls.get(str(message.get("tool_call_id") or "")) in {"use_ruleset_proposal", "configure_investigation_draft"}):
                # Transcript locates a candidate in the turn, but never supplies
                # its success status, Draft identity, or content (even valid JSON).
                adoption_position = index
                continue
            try:
                payload = json.loads(str(message.get("content") or "{}"))
            except (ValueError, TypeError):
                # Runtime diagnostics (including JSON followed by guardrail text)
                # are not resource results. Never extract a success from a prefix.
                continue
            if not isinstance(payload, dict) or payload.get("status") != "ok" or not isinstance(payload.get("data"), dict):
                continue
            successful_results.append((index, tool_name, payload["data"]))

        if adoption_results:
            successful_results.append((adoption_position, "use_ruleset_proposal", adoption_results[-1]))
            successful_results.sort(key=lambda item: item[0])

        artifact_results = [
            (tool_name, data)
            for _, tool_name, data in successful_results
            if tool_name
            in {
                "copy_investigation_draft",
                "create_investigation_draft",
                "use_ruleset_proposal",
                "update_investigation_draft",
                "get_investigation_draft",
                "confirm_and_queue_investigation",
                "get_investigation_run",
            }
        ]
        if not artifact_results:
            return {}
        tool_name, data = artifact_results[-1]
        if tool_name in {
            "copy_investigation_draft",
            "create_investigation_draft",
            "use_ruleset_proposal",
            "update_investigation_draft",
            "get_investigation_draft",
        }:
            tool_draft = public_draft(data.get("draft") or {})
            preview = ConfirmationPreview.model_validate(
                data.get("confirmation_preview")
            )
            if (
                preview.draft_id != tool_draft.id
                or preview.draft_revision != tool_draft.current_revision
            ):
                raise RuntimeError("Draft ToolResult projection identity is inconsistent")
            verified = self.tool_service.application_service.get_draft_view(
                tool_draft.id, principal=principal
            )
            if (
                verified.draft.current_revision != tool_draft.current_revision
                and not (
                    allow_superseded_draft
                    and verified.draft.current_revision > tool_draft.current_revision
                )
            ):
                raise RuntimeError("Draft ToolResult no longer matches Application state")
            if (tool_name == "use_ruleset_proposal"
                    and verified.draft.current_revision == tool_draft.current_revision
                    and (public_configuration(verified.draft.configuration)
                         != public_configuration(tool_draft.configuration)
                         or verified.draft.title != tool_draft.title
                         or verified.draft.objective != tool_draft.objective)):
                raise RuntimeError("Adoption Draft content no longer matches its receipt")
            return self._draft_artifact_for(
                tool_draft.id,
                principal=principal,
                presentation_stage="suggestion",
                **({"verified_view": verified} if tool_name == "use_ruleset_proposal" else {}),
            )
        projection = InvestigationRunProjection.model_validate(data)
        verified = self.tool_service.application_service.get_run(
            projection.run_id, principal=principal
        )
        return run_artifact(verified).model_dump(mode="json")

    def _persist_result(
        self,
        turn: InvestigationTurn,
        result: dict[str, Any],
        transcript: list[dict[str, Any]],
        artifact: dict[str, Any],
        *,
        history_count: int | None = None,
        include_proposal_presentations: bool = True,
    ) -> TurnResult:
        answer = str(result.get("final_response") or "").strip()
        answer_was_redacted = False
        transcript_was_redacted = False
        if settings.creation_answer_stream_enabled or settings.continuous_resource_session_enabled:
            answer, answer_was_redacted = self._redact_answer(answer)
        if history_count is None:
            history_count = len(self.store.hermes_conversation_history(turn.id) or [])
        trace_messages = [
            item
            for item in transcript[history_count:]
            if item.get("role") in {"assistant", "tool"}
        ]
        if has_failed_ruleset_update(trace_messages):
            answer = ("本次规则修改未成功，不能按已修改处理。"
                      "请以实际编辑稿及规则卡片显示的版本为准。")
            # Keep the failed-call evidence, but do not preserve the model's
            # false final success claim as conversation history for the next turn.
            transcript = [dict(item) for item in transcript]
            if (transcript and transcript[-1].get("role") == "assistant"
                    and not transcript[-1].get("tool_calls")):
                transcript[-1]["content"] = answer
            trace_messages = [item for item in transcript[history_count:]
                              if item.get("role") in {"assistant", "tool"}]
        notice = self._binding_failure_notice(turn, trace_messages)
        if notice:
            answer = "\n\n".join(filter(None, [answer, notice]))
        # The cap notice is authoritative preview data; never rely on the model to relay it.
        # Only a turn that set the draft's terms repeats it; reads rely on the card.
        cap_notice = str((artifact.get("confirmation_preview") or {}).get("search_terms_notice") or "")
        sets_terms = any(call["name"] in DRAFT_TERM_TOOLS
                         for call in HermesInvestigationAgentService._tool_calls(trace_messages))
        if cap_notice and sets_terms and cap_notice not in answer:
            answer = "\n\n".join(filter(None, [answer, cap_notice]))
        if settings.creation_answer_stream_enabled or settings.continuous_resource_session_enabled:
            public_transcript = []
            for message in transcript:
                projected = dict(message)
                if projected.get("role") == "assistant":
                    projected["content"], changed = self._redact_answer(
                        str(projected.get("content") or "")
                    )
                    transcript_was_redacted = transcript_was_redacted or changed
                public_transcript.append(projected)
            transcript = public_transcript
            trace_messages = [
                item
                for item in transcript[history_count:]
                if item.get("role") in {"assistant", "tool"}
            ]
        comment_runtime = None
        if settings.continuous_resource_session_enabled:
            from backend.hermes_runtime.service import redact_internal_account_references
            comment_runtime = prepared_runtime(turn.session_id, turn.id)
            if comment_runtime is not None:
                # Recover committed save results independently of the model's
                # explanatory draft. Never replay a write to recover its reply.
                from .save_recovery import completion_answer
                receipts = ""
                try:
                    checkpoints = self._checkpoint_messages_for_turn(
                        turn, principal=self.principal_for_session(turn.session_id),
                        include_recovery_instruction=False)
                    receipts = completion_answer(self._saved_checkpoints(checkpoints))
                except Exception:
                    logger.exception("Could not verify save receipts for comment delivery %s", turn.id)
                    receipts = "本轮资源保存结果暂时无法核实，请先查询操作结果，不要重复保存。"
                answer, transcript, rejected = compose_comment_delivery(
                    comment_runtime, turn.session_id, turn.id, answer, transcript, history_count,
                    verified_receipts=receipts)
                if rejected:
                    answer = "\n\n".join(filter(None, [answer, notice, cap_notice]))
                answer, _ = redact_internal_account_references(answer)
                trace_messages = [item for item in transcript[history_count:]
                                  if item.get("role") in {"assistant", "tool"}]
        from backend.product_help.delivery import complete_report_links
        answer, transcript = complete_report_links(answer, transcript, history_count)
        trace_messages = [item for item in transcript[history_count:]
                          if item.get("role") in {"assistant", "tool"}]
        tool_calls = HermesInvestigationAgentService._tool_calls(trace_messages)
        session = self.store.get_session(turn.session_id)
        try:
            self._answer_streamer.finalize(turn.id, answer)
        except Exception:
            # The public projection is fail-open and cannot change a successful
            # creation/resource operation or its authoritative receipt.
            logger.warning(
                "Creation answer finalization failed open for Turn %s",
                turn.id,
                exc_info=True,
            )
        collection_source_count = 0
        collection_warnings = []
        if self.report_collection and self.report_collection.active(turn.session_id):
            with self.report_collection.connect() as db:
                pinned = db.execute('SELECT selected FROM collection_turns WHERE turn=?', (turn.id,)).fetchone()
            collection_source_count = len(json.loads(pinned['selected'])) if pinned and pinned['selected'] else 0
            collection_warnings = (["collection_tool_access_verified; answer_citations_and_claims_not_independently_verified"]
                if self.report_collection.agent_driven else
                ["collection_source_scope_verified; semantic_claims_not_independently_verified"])
        self.store.complete_turn(
            turn.id,
            answer=answer,
            trace_messages=trace_messages,
            pending_sources=[],
            grounding_validation={
                "status": "passed",
                "source_count": collection_source_count,
                "warnings": collection_warnings + (
                    ["internal_creation_reference_redacted"]
                    if answer_was_redacted
                    or (
                        (settings.creation_answer_stream_enabled or settings.continuous_resource_session_enabled)
                        and transcript_was_redacted
                    )
                    else []
                ),
            },
            resolved_references=[],
            all_tool_calls=tool_calls,
            query_receipts=[],
            summary_text=answer[-2_000:],
            active_focus=session.active_focus,
            ordered_referents=[],
            last_claim_id="",
            last_finding_id="",
            last_evidence_id="",
            input_tokens=int(result.get("input_tokens") or 0),
            output_tokens=int(result.get("output_tokens") or 0),
            total_tokens=int(result.get("total_tokens") or 0),
            llm_call_count=int(result.get("api_calls") or 0),
            stop_reason=str(result.get("turn_exit_reason") or "completed"),
            context_accounting=[],
            grounding_issues=[],
            grounding_repair_count=0,
            scope_repair_count=0,
            source_repair_count=0,
            semantic_rewrite_count=0,
            scope_initial_draft="",
            scope_repaired_draft="",
            scope_initial_issues=[],
            scope_remaining_issues=[],
            hermes_transcript=transcript,
            public_artifact=artifact,
            proposal_snapshots=(
                self.tool_service.application_service.store.proposal_presentation_snapshots(
                    session_id=turn.session_id, turn_id=turn.id,
                )
                if include_proposal_presentations
                else []
            ),
        )
        if self.report_collection and self.report_collection.active(turn.session_id):
            from backend.report_collection.batches import acknowledge as acknowledge_collection
            try:
                acknowledge_collection(self.report_collection, turn.session_id, turn.id)
            except Exception:
                logger.exception('Collection delivery acknowledgment deferred for %s', turn.id)
        if comment_runtime is not None:
            from hermes_m0.comment_batches import acknowledge
            try:
                acknowledge(comment_runtime, turn.session_id, turn.id)
            except Exception:
                logger.exception("Comment delivery acknowledgment deferred for %s", turn.id)
        self._answer_streamer.release(turn.id)
        self._notify(turn.id, "persist_turn")
        return self.store.turn_result(turn.id)

    def _authorize(self, session: InvestigationSession, principal: Principal) -> None:
        if (
            session.scope_type != "creation"
            or session.owner_principal != principal.id
            or session.status != "active"
        ):
            raise InvestigationSessionNotFoundError("creation Session was not found")

    def _notify(self, turn_id: str, node_name: str) -> None:
        for observer in tuple(self._turn_node_observers):
            observer(turn_id, node_name)
