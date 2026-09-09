from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from backend.audit_agent.config import settings
from backend.audit_agent.creator_url import CreatorUrlValidationError, validate_creator_url
from backend.hermes_runtime.adapter import HermesRuntimeBinding, session_runtime_home
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
from .public_projection import draft_artifact, public_draft, run_artifact
from .tools import (
    HermesToolExecutionIdentity,
    InvestigationCreationToolService,
)


CREATION_SYSTEM_PROMPT = """You are the investigation configuration and resource assistant for a
content-audit platform. Application appends the complete authoritative 审核规则 Proposal snapshot
to the public assistant message after successful Proposal creation or update. Your natural-language
response may explain the design or changes; it is not the authoritative rule presentation.
用户界面与回复统一使用“审核规则”和“黑话库”两个资源名称；理解用户旧称，但回复、标题和生成说明只用新名称。黑话库仍包含用于平台搜索和内容召回的词条，不仅限于隐语；不因改名改变搜索词生成标准、临时资源边界或采用流程。

Conversation is primary. Use only the investigation creation tools
exposed in this mode, choosing and combining them according to the user's current intent. There is
no requirement to run every tool or follow one fixed workflow in every turn.

The user may want to query, understand, explain, compare, or get recommendations about available
platforms, recall 黑话库 and their terms, published 审核规则已发布版本, existing
Drafts, or existing Runs. They may instead want to create or edit an investigation, or explicitly
confirm one. When a request concerns resources, versions, or status that currently exist in the
system, call the necessary read tool and rely on its ToolResult. Never invent Application state.
For a read-only request, answer naturally from the ToolResult and end the turn without calling
create_investigation_draft. A final assistant answer does not need canonical decision JSON.

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
clearly matching available real recall 黑话库. When enabled_main_terms for that existing 黑话库
are available in the conversation context, that authoritative term snapshot is the recall
configuration; do not ask the user to enter separate search keywords before creating the Draft. The
user can review and edit these recommended values on the Draft afterward.

There are exactly two investigation modes. A creator mode request contains a valid creator homepage
URL, never a post URL, post ID, or arbitrary webpage. Save it only as
configuration.investigation.mode=creator with creator_url set to that homepage URL. Creator mode
must not carry search terms, source 黑话库, or recall plans, and must resolve to crawl_mode=creator.
For a search Draft, select a matching published 审核规则已发布版本 and prefer an independently available
real recall 黑话库 when it is sufficiently suitable. When its terms need to be discovered, query that 黑话库 with
include_lexicon_terms_for_ids. Save existing_lexicon with its ID,
expected_runtime_content_hash, and the returned enabled_main_terms snapshot. Never expand variants,
tag entries, query type, or order into crawler terms. When the user explicitly changes the
main terms, use update_investigation_draft to replace existing_lexicon with temporary_terms containing
exactly the user's edited terms and the source 黑话库 ID.

If no sufficiently suitable existing 黑话库 is available, inspect the full conversation for
authorization to generate missing Recall. Without that authorization, explain the Recall resource
gap, propose generating temporary search terms for this investigation, and end this turn with no
Draft and no generated terms. Investigation intent alone is not generation authorization.
Before authorization, do not list even illustrative example terms or candidate terms in your reply;
propose the generation action only, then wait for the user's answer.
For this branch, use a brief reply such as: "当前没有找到足够合适的现成黑话库资源。可以为本次调查
生成临时搜索词；这些词仅用于本次调查，不会保存为正式词库。是否需要生成？" Do not append a term
list, examples, a proposed configuration, or a Draft to that reply.
If the user already authorized generation (for example, "没有合适词库就帮我生成这次搜索词" or
"没有的话你自己补"), and investigation intent and a valid published Judgement 审核规则 are present,
generate focused temporary canonical search terms and create the Draft in the same turn only when
using that valid published Judgement. Store terms in configuration.investigation.recall_plan with
strategy=temporary_terms and source_lexicon_ids as real referenced 黑话库 IDs, or [].
When temporary 审核规则 and search terms are generated together, this takes priority: create the
审核规则 Proposal, then show its 审核规则 and the complete search-term list, and END this turn.
Do not call create_investigation_draft, update_investigation_draft or use_ruleset_proposal in that
generation turn. Generating or displaying temporary terms does not require a Draft. Wait for a
LATER explicit adoption; then use_ruleset_proposal creates the Draft with the displayed terms.
Do not ask again for permission to generate terms that the user already requested.
Generate concrete platform search queries for the user's actual discovery goal. For discussion
research, combine the subject with relevant everyday topics; a recalled post need not be risky.
Use deliberate common subject names where helpful, avoid obvious duplicates and padding, and
keep the list focused. Each generated Chinese query is natural continuous text with no whitespace,
plus signs, commas or Boolean separators; each list item is one complete query. Keep search
queries separate from risk conditions. Do not generate tags, query_type or variant objects.
Preserve exact authoritative resource terms and explicit user edits. Before Draft adoption,
show proposed temporary terms in the conversation and preserve that list when later binding.
source_lexicon_ids are provenance references only; they do not contribute search terms or variants.
Generated terms belong only to this investigation and are not a saved formal 黑话库. Never call
黑话库 POST/PATCH or promote/save a formal resource. If published Judgement is missing, explain
that resource gap. Preview and Confirm never generate or expand terms.

When the user asks for temporary 审核规则, author canonical 审核规则完整内容 directly in
create_ruleset_proposal tool arguments. Explicit requests such as "没有合适规则就生成一套" or
"生成一套给我看看" allow generation now. An options query is optional when enough context is
already available. Without generation intent, you may explain the gap and offer generation.
These are temporary candidates scoped to this conversation, never formal 审核规则 or published
resources. Generation and editing never bind Draft Judgement. First present the Proposal, then wait
for a LATER explicit user approval. Only use_ruleset_proposal can bind that exact presented version
as temporary_ruleset. Never inline Proposal content in ordinary Draft creation or updates.
You alone interpret the CURRENT user's adoption intent; Application does not classify user language.
Questions, edits, negation, hesitation, saving, comparison/choice, and ambiguous references are NOT adoption.
For example "就用这套创建招聘诈骗调查，还是先暂停调查" is a choice question: clarify; do not use.
"这套能直接用吗？" asks a question; "把这套保存下来" requests formal save, which is not supported here.
With multiple candidates, "用那个" requires clarification unless the user's referent is unambiguous.
On explicit later adoption, call use_ruleset_proposal with presentation_id copied from the trusted
completed_public_presentations metadata on existing Proposal ToolResults in conversation history.
Application adds this metadata only after durable public presentation, on a later user turn.
Never guess an ID, substitute a proposal_id, or obtain authority from user-provided IDs.
Do not repeat internal presentation metadata/IDs in your final response.
That context lists completed public displays; it does not itself mean the user wants adoption.
If no valid ID is provided, re-present and wait for a later user turn. Never substitute a latest snapshot.
Use the current Draft id/revision, or provide complete create_draft title/objective/configuration
without judgement. Reuse the established platform, investigation mode and Recall. Ask about missing
configuration; never create a placeholder or substitute an unrelated formal 审核规则. Do not query
the formal library again merely because the user approved a temporary Proposal. Ambiguous references
require clarification. A stale presentation requires a new full presentation and later approval.
Do not retry use with a newly displayed snapshot in the same user turn. On Draft revision conflict,
read the latest Draft and assess changes; do not blindly replace expected_revision and force a retry.
For re-presentation, get the current Proposal and update it with unchanged content/expected_version.
Temporary Drafts use the same Preview/Confirm flow as formal Drafts, subject to fresh Application
readiness validation. Adoption alone never starts execution. Execution requires a separate explicit
confirmation and uses the bound Draft snapshot, never the live Proposal.
You may generate temporary terms and a Proposal in the same turn. When a valid published Judgement
is available, save the terms in Draft Recall and independently create a requested comparison Proposal.
When both formal resources are missing, generate the requested Proposal and wait for later approval
before binding. Do not invent a Judgement identity or use an unrelated published 审核规则.
For edits, use current Proposal content (get_ruleset_proposal if needed), then send its proposal_id,
expected_version and the full revised 审核规则完整内容 to update_ruleset_proposal. Application owns
content_hash; do not supply it. Preserve category_id, rule_id and ordering for unchanged semantics;
do not rewrite unrelated 审核规则 without reason. On stale version, read the current Proposal and
reconsider the edit. A request for another candidate creates a new Proposal, leaving earlier ones
available. Explain the proposed 审核规则 naturally in the user's language after a successful tool call.

When authoring or revising any domain's 审核规则完整内容, choose each rule's application_stages by
asking which evidence modalities can independently show the risk behavior. The only legal stages
are image_evidence, video_frame_evidence, comment_audit and fusion_audit. image_evidence extracts
image text and visual evidence AND applies business 审核规则 relevant to images. video_frame_evidence
extracts and assesses video frames, OCR, ASR and contextual evidence AND applies business 审核规则
relevant to video. comment_audit audits the comment's own content. fusion_audit performs final rule
matching and combined judgment using existing cross-modal evidence; it does not reread all raw
media or recover every OCR/ASR detail. Do not default all 审核规则 to comment_audit + fusion_audit.
A rule intended to support a final finding must also include fusion_audit, even when one modality
alone can establish that risk: the compiler does not automatically copy 审核规则 into fusion. Reserve
discovery-only stages for deliberate evidence prerequisites covered by an explicit final rule;
do not require a cross-modal closed loop before recognizing every independently sufficient risk.
For example, an explicit requirement to pay before starting a job can independently appear in a
recruitment poster, video subtitles/frames, spoken ASR or comments, so normally consider all four
stages. A rule about participation organized by a comment can use comment_audit + fusion_audit;
a rule that only combines existing evidence across sources can use fusion_audit alone. These are
examples of modality-based selection, not a requirement that every rule use all four stages.

Both general_exemptions and rule_exemptions are strong business exemptions: matching them removes
the corresponding risk, rather than providing background, a confidence hint or a small downgrade.
Author an exemption only when its condition negates the applicable risk. A general
exemption must be valid across the 审核规则 it can exempt; use rule_exemptions for narrower conditions.
Enterprise certification, a blue verification badge, an official account, matching registered
business scope, a well-known institution or real-name verification alone must never be a general
exemption. Identity or reputation does not negate an explicit risky act. For example, even a
verified employer's explicit requirement to pay a training fee before employment is not exempt
because the employer is verified. Do not encode mere background information as an exemption.


生成质量参考（只在民族关系讨论任务中适用）：搜索以相关主体和婚恋、家庭、文化语言、身份认同、交往等议题寻找讨论场；重点议题可分别使用维族、维吾尔族及维汉的自然完整查询，例如维吾尔族民族认同、维汉恋爱。正常民族认同、文化保护和个人婚恋选择不是风险，不推断任何人的民族身份或认同倾向。
规则识别具体攻击：群体负面泛化与先天优劣通常 medium；非人化、严重集体犯罪污名、权利剥夺或驱逐、明确暴力威胁通常 high，避免同义重复。通婚普遍排斥与血统纯洁分别覆盖，血统污染主张无需同时要求强制阻止婚姻。民族关系关联攻击通常 medium：对红娘、情侣或支持者的侮辱诅咒须与其民族相关身份、行为或立场有明显关联；普通售假等具体行为批评不能仅因账号背景而命中。可靠维语原文或译文中的明确恶意辱骂可独立列为 low 专项敌意线索，不要求民族对象或民族动机，不据此认定民族仇恨；单纯使用维语不命中。模糊敌意仅作 low 待复核兜底，有明确规则可用时不用它。
保持正常身份文化表达、具体行为正常批评、个人生活选择，以及不支持风险表达的新闻学术和批判性引用豁免；豁免不得抵消明确攻击。关键条件写入 hit_condition，通用原则写入 audit_goal，不能只写在不进入编译的 adjudication_notes；按实际证据模态分配阶段。以上是生成指导，不自行扩展到政治宗教或思想倾向评分，也不改变任务流程。

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
crawler, subprocess, provider, and report side effects. ToolResults and public artifacts are
authoritative. When a Draft or Run tool succeeds, briefly explain its public artifact in the user's
language.

结构化规则通用约束：正常、允许、无需处置的表达应写入 general_exemptions 或 rule_exemptions，不能创建启用的 low 风险规则来表示豁免。兜底规则仅在不能命中其他明确风险规则时使用；生成和修改后检查是否存在相反条件。民族主题中的模糊敌意另须具有明确民族关联。
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


class FakeCreationHermesAgent:
    """Deterministic local Hermes-shaped runtime; it never starts the investigation pipeline."""

    def __init__(
        self,
        *,
        session_id: str,
        tool_service: InvestigationCreationToolService,
        principal_resolver: Callable[[str], Principal],
        **_: Any,
    ) -> None:
        self.session_id = session_id
        self.tool_service = tool_service
        self.principal_resolver = principal_resolver

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
        options = self.tool_service.execute(
            "query_investigation_options", option_args, principal=principal
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
            lexicon_option_args = {
                "domain_hint": message.strip()[:200],
                "mode": "search",
                "lexicon_ids": [lexicon["id"]],
                "include_lexicon_terms_for_ids": [lexicon["id"]],
                "page_size": 20,
                "lexicon_term_limit": 100,
            }
            lexicon_options = self.tool_service.execute(
                "query_investigation_options",
                lexicon_option_args,
                principal=principal,
            )
            lexicon = (lexicon_options.get("recall_lexicons") or [None])[0]
        missing_resource = ""
        if ruleset is None:
            missing_resource = "当前没有适合本次调查的已发布研判规则，暂时无法创建 Draft。"
        elif mode == "search" and lexicon is None:
            missing_resource = "当前没有可用的已发布黑话库，无法创建关键词调查 Draft。"
        if missing_resource:
            final = missing_resource
            option_call_id = f"{task_id}:options"
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
        option_call_id = f"{task_id}:options"
        lexicon_option_call_id = f"{task_id}:lexicon-options"
        create_call_id = f"{task_id}:create-draft"
        create_result = self.tool_service.execute_with_identity(
            "create_investigation_draft",
            draft_args,
            principal=principal,
            identity=HermesToolExecutionIdentity.require(
                session_id=self.session_id,
                turn_id=task_id,
                tool_call_id=create_call_id,
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
            confirm_result = self.tool_service.execute_with_identity(
                "confirm_and_queue_investigation",
                confirm_args,
                principal=principal,
                identity=HermesToolExecutionIdentity.require(
                    session_id=self.session_id,
                    turn_id=task_id,
                    tool_call_id=confirm_call_id,
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
    ) -> None:
        self.tool_service = tool_service
        self.store = store or InvestigationStore()
        self.tool_service.application_service.conversation_store = self.store
        self.runtime_binding = runtime_binding or HermesRuntimeBinding()
        self.agent_factory = agent_factory
        self.fake_runtime = bool(fake_runtime)
        self.hermes_state_dir = (
            hermes_state_dir or settings.data_dir / "hermes-investigation-creation"
        ).resolve()
        self._agents: dict[str, Any] = {}
        self._agent_lock = RLock()
        self._turn_node_observers: list[Callable[[str, str], None]] = []

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
        for session in self.store.list_creation_sessions(principal=principal.id, limit=limit, offset=offset):
            state = self.get_workspace_state(session.id, principal=principal)
            first_question = next((message.content for message in state.messages if message.role == "user"), "")
            items.append({
                "workspace_session_id": session.id,
                "title": (state.draft_artifact.get("draft") or {}).get("title") or first_question[:48] or "新调查需求",
                "updated_at": session.updated_at,
                "run_status": state.run.status if state.run else "",
                "presentation_stage": state.draft_artifact.get("presentation_stage", ""),
            })
        return items

    def get_session(self, session_id: str, *, principal: Principal) -> InvestigationSession:
        session = self.store.get_session(session_id)
        self._authorize(session, principal)
        return session

    def principal_for_session(self, session_id: str) -> Principal:
        session = self.store.get_session(session_id)
        if session.scope_type != "creation" or not session.owner_principal:
            raise InvestigationSessionNotFoundError("creation Session was not found")
        return Principal(session.owner_principal)

    def accept_message(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
        principal: Principal,
    ) -> tuple[InvestigationTurn, bool]:
        user_input = str(content or "").strip()
        client_id = str(client_message_id or "").strip()
        if not user_input or len(user_input) > 4_000:
            raise ValueError("content must contain between 1 and 4000 characters")
        if not client_id or len(client_id) > 200:
            raise ValueError("client_message_id must contain between 1 and 200 characters")
        session = self.get_session(session_id, principal=principal)
        return self.store.create_turn(
            session.id,
            client_message_id=client_id,
            user_input=user_input,
        )

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
        if (isinstance(configuration, InvestigationDraftConfiguration)
                and configuration.judgement.strategy == "temporary_ruleset"):
            from .resources import _CREATION_PLATFORM_ORDER, _PLATFORM_NAMES

            return draft_artifact(
                view, options=InvestigationOptions(
                    platforms=[PlatformOption(id=key, name=_PLATFORM_NAMES[key]) for key in _CREATION_PLATFORM_ORDER],
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
        current_artifact: dict[str, Any] = {}
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
        )

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

    def execute_turn(self, turn_id: str) -> TurnResult:
        turn = self.store.get_turn(turn_id)
        if turn.status in {"completed", "error"}:
            return self.store.turn_result(turn.id, idempotent_replay=True)
        if turn.status != "running":
            raise InvestigationTurnNotFoundError("turn is not ready for execution")
        session = self.store.get_session(turn.session_id)
        principal = self.principal_for_session(session.id)
        history = self.store.latest_completed_hermes_transcript(session.id)
        user_message = self.store.get_user_message_for_turn(turn.id).content
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
                        records = [record for record in context if payload.get("data") == record["snapshot"]]
                        if records:
                            payload["completed_public_presentations"] = [
                                {key: record[key] for key in ("presentation_id", "proposal_id", "proposal_version", "assistant_message_id", "presented_at")}
                                for record in records]
                            replacement = {**message, "content": json.dumps(payload, ensure_ascii=False)}
                projected.append(replacement)
            history = projected
        self._notify(turn.id, "call_qwen")
        self.tool_service.begin_conversation_turn(session.id, turn.id)
        try:
            if self.fake_runtime:
                agent = self._agent(session.id)
                result = agent.run_conversation(
                    user_message,
                    system_message=CREATION_SYSTEM_PROMPT,
                    conversation_history=history,
                    task_id=turn.id,
                )
            else:
                with self.runtime_binding.product_mode_execution(
                    session_runtime_home(self.hermes_state_dir, session.id),
                    product_mode="creation"
                ):
                    agent = self._agent(session.id)
                    result = agent.run_conversation(
                        user_message,
                        system_message=CREATION_SYSTEM_PROMPT,
                        conversation_history=history,
                        task_id=turn.id,
                    )
            if not isinstance(result, dict):
                raise RuntimeError("Hermes returned a non-object Turn result")
            if bool(result.get("interrupted")):
                raise RuntimeError("Hermes creation Turn was interrupted")
            if bool(result.get("failed")) or not bool(result.get("completed", True)):
                self.store.fail_turn(
                    turn.id,
                    error_code="hermes_execution_failed",
                    safe_message=(
                        str(result.get("final_response") or "").strip()
                        or "调查方案生成暂时无法完成。"
                    ) + ("\n" + self._binding_failure_notice(turn) if self._binding_failure_notice(turn) else ""),
                    retryable=False,
                )
                return self.store.turn_result(turn.id)
            transcript = HermesInvestigationAgentService._validate_completed_transcript(
                result, history=history, user_message=user_message
            )
            artifact = self._verified_artifact(
                transcript[len(history or []):], principal=principal, adoption_turn=turn
            )
            return self._persist_result(turn, result, transcript, artifact)
        except Exception as exc:
            current = self.store.get_turn(turn.id)
            if current.status == "completed":
                return self.store.turn_result(turn.id)
            self.store.mark_interrupted(
                turn.id,
                error_code="hermes_unknown_outcome",
                safe_message="调查方案生成结果暂时无法确认，可以安全恢复。" + self._binding_failure_notice(turn),
                retryable=True,
            )
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

    def _binding_failure_notice(self, turn: InvestigationTurn, trace_messages: list[dict] | None = None) -> str:
        from .approval import BINDING_FAILURES

        if self.tool_service.application_service.store.successful_adoption_results(
            session_id=turn.session_id, turn_id=turn.id,
            principal=self.principal_for_session(turn.session_id).id,
        ):
            return ""

        # Receipt provenance, not model prose, determines this minimal public fallback.
        with self.tool_service.application_service.store._connect() as connection:
            failures = connection.execute(
                """SELECT r.response_json FROM investigation_creation_tool_receipts r
                   JOIN ruleset_proposal_conversation_bindings b ON b.receipt_id=r.receipt_id
                   WHERE r.session_id=? AND b.application_turn_id=?
                     AND r.tool_name='use_ruleset_proposal' AND r.status='FAILED'""",
                (turn.session_id, turn.id),
            ).fetchall()
        errors = [json.loads(row["response_json"]).get("error", {}) for row in failures]
        # Schema rejection happens before a mutation receipt exists. Preserve its
        # basic failure reason from the existing validated ToolResult transcript.
        use_ids = {call.get("id") for item in trace_messages or [] if item.get("role") == "assistant"
                   for call in item.get("tool_calls") or []
                   if (call.get("function") or {}).get("name") == "use_ruleset_proposal"}
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

    def _agent(self, session_id: str) -> Any:
        with self._agent_lock:
            agent = self._agents.get(session_id)
            if agent is None:
                if self.fake_runtime:
                    agent = FakeCreationHermesAgent(
                        session_id=session_id,
                        tool_service=self.tool_service,
                        principal_resolver=self.principal_for_session,
                    )
                else:
                    agent = self.runtime_binding.create_agent(
                        session_id=session_id,
                        agent_factory=self.agent_factory,
                        product_mode="creation",
                        base_url=settings.dashscope_base_url,
                        api_key=settings.dashscope_api_key,
                        stream_delta_callback=lambda _delta: None,
                    )
                self._agents[session_id] = agent
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
            if (tool_name == "use_ruleset_proposal"
                    or calls.get(str(message.get("tool_call_id") or "")) == "use_ruleset_proposal"):
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
                    and (verified.draft.configuration != tool_draft.configuration
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
    ) -> TurnResult:
        answer = str(result.get("final_response") or "").strip()
        history_count = len(self.store.latest_completed_hermes_transcript(turn.session_id) or [])
        trace_messages = [
            item
            for item in transcript[history_count:]
            if item.get("role") in {"assistant", "tool"}
        ]
        notice = self._binding_failure_notice(turn, trace_messages)
        if notice:
            answer = "\n\n".join(filter(None, [answer, notice]))
        tool_calls = HermesInvestigationAgentService._tool_calls(trace_messages)
        session = self.store.get_session(turn.session_id)
        self.store.complete_turn(
            turn.id,
            answer=answer,
            trace_messages=trace_messages,
            pending_sources=[],
            grounding_validation={"status": "passed", "source_count": 0, "warnings": []},
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
            proposal_snapshots=self.tool_service.application_service.store.proposal_presentation_snapshots(
                session_id=turn.session_id, turn_id=turn.id,
            ),
        )
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
