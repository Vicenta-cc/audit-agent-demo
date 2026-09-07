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
from backend.hermes_runtime.adapter import HermesRuntimeBinding
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
    InvestigationRunProjection,
    QueryInvestigationOptions,
)
from .errors import (
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
content-audit platform. Conversation is primary. Use only the six investigation creation tools
exposed in this mode, choosing and combining them according to the user's current intent. There is
no requirement to run every tool or follow one fixed workflow in every turn.

The user may want to query, understand, explain, compare, or get recommendations about available
platforms, recall lexicons and their terms, published RuleSetRevisions, existing
Drafts, or existing Runs. They may instead want to create or edit an investigation, or explicitly
confirm one. When a request concerns resources, versions, or status that currently exist in the
system, call the necessary read tool and rely on its ToolResult. Never invent Application state.
For a read-only request, answer naturally from the ToolResult and end the turn without calling
create_investigation_draft. A final assistant answer does not need canonical decision JSON.

Do not treat a topic mention or a resource question as sufficient intent to create a Draft. Create a
Draft when the full conversation shows operational investigation intent; "I want to investigate X"
is sufficient and does not require a second create command. Phrases such as "create the
investigation", "use this configuration to create the task", or "use this lexicon and RuleSet to
investigate on Douyin" also indicate creation intent, without requiring exact keywords. When
resource discovery is needed, call query_investigation_options. Complete resource identities already
present in the conversation context may be reused; Application will reread and authoritatively
validate them during creation. A read-only turn may stop after its query.

An Investigation Draft is an editable recommended configuration, not a final confirmed execution
configuration. When the user clearly asks to create a Draft and suitable real resources exist, do
not require explicit confirmation of every editable or defaultable field. Derive a useful title
from the objective; preserve any available platform the user explicitly selected, or otherwise
choose one reasonable available platform from the available resource context. Select the clearly
matching published RuleSetRevision directly as the Judgement resource and independently select a
clearly matching available real recall lexicon. When enabled_main_terms for that existing lexicon
are available in the conversation context, that authoritative term snapshot is the recall
configuration; do not ask the user to enter separate search keywords before creating the Draft. The
user can review and edit these recommended values on the Draft afterward.

There are exactly two investigation modes. A creator mode request contains a valid creator homepage
URL, never a post URL, post ID, or arbitrary webpage. Save it only as
configuration.investigation.mode=creator with creator_url set to that homepage URL. Creator mode
must not carry search terms, source lexicons, or recall plans, and must resolve to crawl_mode=creator.
For a search Draft, select a matching published RuleSetRevision and prefer an independently available
real recall lexicon when it is sufficiently suitable. When its terms need to be discovered, query that lexicon with
include_lexicon_terms_for_ids. Save existing_lexicon with its ID,
expected_runtime_content_hash, and the returned enabled_main_terms snapshot. Never expand variants,
tag entries, query type, or order into crawler terms. When the user explicitly changes the
main terms, use update_investigation_draft to replace existing_lexicon with temporary_terms containing
exactly the user's edited terms and the source lexicon ID.

If no sufficiently suitable existing Lexicon is available, inspect the full conversation for
authorization to generate missing Recall. Without that authorization, explain the Recall resource
gap, propose generating temporary search terms for this investigation, and end this turn with no
Draft and no generated terms. Investigation intent alone is not generation authorization.
Before authorization, do not list even illustrative example terms or candidate terms in your reply;
propose the generation action only, then wait for the user's answer.
For this branch, use a brief reply such as: "当前没有找到足够合适的现成召回词资源。可以为本次调查
生成临时搜索词；这些词仅用于本次调查，不会保存为正式词库。是否需要生成？" Do not append a term
list, examples, a proposed configuration, or a Draft to that reply.
If the user already authorized generation (for example, "没有合适词库就帮我生成这次搜索词" or
"没有的话你自己补"), and investigation intent and a valid published Judgement RuleSet are present,
generate focused temporary canonical search terms and create the Draft in the same turn, without
asking for generation permission again. Put them directly in create_investigation_draft or
update_investigation_draft configuration.investigation.recall_plan with strategy=temporary_terms,
terms as a list of strings, and source_lexicon_ids as the real Lexicon IDs actually referenced, or [].
Generate only core concepts worth searching: concrete, relevant platform search strings, without
explanations or obvious duplicates. Keep the list focused; never mechanically pad it. Do not generate
variants, synonym/slang/spelling expansions, platform-specific expansions, tags, query_type, or formal
Lexicon entries. Never put an English comma inside one term or combine separate queries in one term.
Choose one canonical wording per core concept. Do not enumerate alternate names for the same concept
or pad a list with broad umbrella topic words and their synonymous restatements. Prefer specific
intent-bearing concepts describing the activity, transaction, or offer being investigated; choose
concepts for the actual goal, not a fixed generic topic list.
source_lexicon_ids are provenance references only; they do not contribute search terms or variants.
Generated terms belong only to this investigation and are not a saved formal Lexicon. Never call
Lexicon POST/PATCH or promote/save a formal resource. Never generate a temporary RuleSet; if Judgement
is missing, explain that resource gap. Preview and Confirm never generate or expand terms.

If the user asks to inspect an existing Draft, read it instead of creating a replacement. If the
user asks to change an existing Draft, update that Draft at its current revision instead of creating
a second Draft. Never fabricate missing domain resources. If no suitable published RuleSetRevision
exists, no real recall configuration can be formed, the user's requested
platform is unavailable, or a current blocker prevents a valid configuration, do not create a
misleading Draft. A creator-mode Draft cannot default a missing creator homepage URL; ask for that
URL instead. If no matching published RuleSetRevision exists, preserve and explain the
NO_PUBLISHED_RULESET blocker. Do not pick an unrelated RuleSet and do not confirm. If no
real recall lexicon is available in search mode, do not invent or hardcode one. Draft saves never
publish or mutate shared lexicons. Creating or updating a Draft is never confirmation. Only call
confirm_and_queue_investigation after an explicit user instruction to confirm and start, with
confirmed=true and a stable idempotency key. Keep all pre-confirmation turns free of Run, Job,
crawler, subprocess, provider, and report side effects. ToolResults and public artifacts are
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
            missing_resource = "当前没有可用的已发布召回词库，无法创建关键词调查 Draft。"
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
    ) -> dict[str, Any]:
        application = self.tool_service.application_service
        view = application.get_draft_view(draft_id, principal=principal)
        configuration = view.draft.configuration
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
            artifact = turn.public_artifact or {}
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
        self._notify(turn.id, "call_qwen")
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
                    self.hermes_state_dir, product_mode="creation"
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
                    ),
                    retryable=False,
                )
                return self.store.turn_result(turn.id)
            transcript = HermesInvestigationAgentService._validate_completed_transcript(
                result, history=history, user_message=user_message
            )
            artifact = self._verified_artifact(
                transcript[len(history or []):], principal=principal
            )
            return self._persist_result(turn, result, transcript, artifact)
        except Exception as exc:
            current = self.store.get_turn(turn.id)
            if current.status == "completed":
                return self.store.turn_result(turn.id)
            self.store.mark_interrupted(
                turn.id,
                error_code="hermes_unknown_outcome",
                safe_message="调查方案生成结果暂时无法确认，可以安全恢复。",
                retryable=True,
            )
            raise RuntimeError("Hermes creation Turn ended with an unknown outcome") from exc

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
    ) -> dict[str, Any]:
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
            payload = json.loads(str(message.get("content") or "{}"))
            if payload.get("status") != "ok" or not isinstance(payload.get("data"), dict):
                continue
            successful_results.append((index, tool_name, payload["data"]))

        artifact_results = [
            (tool_name, data)
            for _, tool_name, data in successful_results
            if tool_name
            in {
                "create_investigation_draft",
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
            return self._draft_artifact_for(
                tool_draft.id,
                principal=principal,
                presentation_stage="suggestion",
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
