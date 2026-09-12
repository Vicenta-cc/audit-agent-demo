"""Formal Investigation Session/Turn facade backed by Hermes AIAgent."""

from __future__ import annotations

from contextlib import closing, nullcontext
import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from backend.audit_agent.config import settings
from backend.hermes_runtime.adapter import HermesRuntimeBinding, session_runtime_home
from backend.investigation.contracts import (
    InvestigationMessage,
    InvestigationSession,
    InvestigationTurn,
    TurnResult,
)
from backend.investigation.errors import InvestigationTurnNotFoundError
from backend.investigation.protocol import validate_hermes_transcript_messages
from backend.investigation.report_query import ReportQueryFacade
from backend.investigation.store import InvestigationStore


class HermesInvestigationAgentService:
    """Preserve the public compatibility facade while executing with Hermes."""

    def __init__(
        self,
        *,
        report_facade: ReportQueryFacade | None = None,
        store: InvestigationStore | None = None,
        runtime_binding: HermesRuntimeBinding | None = None,
        agent_factory: Callable[..., Any] | None = None,
        bind_runtime: bool = True,
        hermes_state_dir: Path | None = None,
        authorized_report_version_ids: tuple[str, ...] | None = None,
        authorized_context_anchor_prefixes: tuple[str, ...] | None = None,
    ) -> None:
        self.report_facade = report_facade or ReportQueryFacade()
        self.store = store or InvestigationStore()
        self.runtime_binding = runtime_binding or HermesRuntimeBinding()
        self.agent_factory = agent_factory
        self.bind_runtime = bool(bind_runtime)
        self.hermes_state_dir = (
            hermes_state_dir or settings.data_dir / "hermes-investigation"
        ).resolve()
        self.authorized_report_version_ids = (
            tuple(authorized_report_version_ids)
            if authorized_report_version_ids is not None
            else settings.hermes_authorized_report_version_ids
        )
        self.authorized_context_anchor_prefixes = (
            tuple(authorized_context_anchor_prefixes)
            if authorized_context_anchor_prefixes is not None
            else None
        )
        self._agents: dict[str, Any] = {}
        self._bound_sessions: set[str] = set()
        self._agent_lock = RLock()
        self._turn_node_observers: list[Callable[[str, str], None]] = []

    def close(self) -> None:
        with self._agent_lock:
            agents = tuple(self._agents.items())
            self._agents.clear()
        released_sessions = set()
        for session_id, agent in agents:
            close = getattr(agent, "close", None)
            if callable(close):
                close()
            if self.bind_runtime:
                self.runtime_binding.release_published_report_session(session_id)
                released_sessions.add(session_id)
        if self.bind_runtime:
            for session_id in self._bound_sessions - released_sessions:
                self.runtime_binding.release_published_report_session(session_id)
        self._bound_sessions.clear()

    def create_session(
        self,
        report_version_id: str,
        *,
        anchor_key: str = "",
    ) -> InvestigationSession:
        context = self.report_facade.get_published_report_context(report_version_id)
        return self.store.create_session(context, anchor_key=anchor_key)

    def close_session(self, session_id: str) -> InvestigationSession:
        session = self.store.close_session(session_id)
        with self._agent_lock:
            agent = self._agents.pop(session_id, None)
        if agent is not None:
            close = getattr(agent, "close", None)
            if callable(close):
                close()
        self._bound_sessions.discard(session_id)
        if self.bind_runtime:
            self.runtime_binding.release_published_report_session(session_id)
        return session

    def accept_message(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
    ) -> tuple[InvestigationTurn, bool]:
        user_input = str(content or "").strip()
        client_id = str(client_message_id or "").strip()
        if not user_input or len(user_input) > 4_000:
            raise ValueError("content must contain between 1 and 4000 characters")
        if not client_id or len(client_id) > 200:
            raise ValueError(
                "client_message_id must contain between 1 and 200 characters"
            )
        session = self.store.get_session(session_id)
        self._validate_business_scope(session)
        return self.store.create_turn(
            session.id,
            client_message_id=client_id,
            user_input=user_input,
        )

    def execute_turn(self, turn_id: str) -> TurnResult:
        turn = self.store.get_turn(turn_id)
        if turn.status in {"completed", "error"}:
            return self.store.turn_result(turn.id, idempotent_replay=True)
        if turn.status != "running":
            raise InvestigationTurnNotFoundError("turn is not ready for execution")
        session = self.store.get_session(turn.session_id)
        self._validate_business_scope(session)
        self._notify(turn.id, "call_qwen")
        try:
            mode_scope = (
                self.runtime_binding.product_mode_execution(
                    session_runtime_home(self.hermes_state_dir, session.id),
                    product_mode=self._product_mode(session.id),
                )
                if self.bind_runtime
                else nullcontext()
            )
            with mode_scope:
                self._bind_session(session)
                agent = self._agent(session.id)
                history = self.store.hermes_conversation_history(turn.id)
                if self.bind_runtime and history:
                    from hermes_m0.runtime import report_task_runtime_for_session
                    from hermes_m0.reference_state import (
                        prepare_conversation_history,
                    )
                    tool_service = report_task_runtime_for_session(session.id)
                    history = prepare_conversation_history(tool_service, session.id, history)
                user_message = self.store.get_user_message_for_turn(turn.id).content
                system_message = (
                    self.runtime_binding.product_system_prompt("pass-report")
                    if self._product_mode(session.id) == "pass-report"
                    else self.runtime_binding.product_system_prompt()
                )
                if self.bind_runtime:
                    from hermes_m0.runtime import report_task_runtime_for_session
                    activity = report_task_runtime_for_session(session.id).account_activity
                    if activity is not None:
                        titles = [repo.report.title for repo in activity.authorized_report_repositories]
                        system_message += (
                            "\n服务器确认本会话账号活动查询已授权以下报告："
                            + json.dumps(titles, ensure_ascii=False)
                            + "。当前报告绑定只限制报告发现与证据导航，不限制上述账号活动查询。"
                            "不得因为用户本轮没有重述授权，就声称只能查询当前报告。"
                            "这个名单不是账号出现记录；具体数量与评论必须读取账号工具。"
                            "查询跨报告账号活动时，须在本轮重新读取证据或账号工具；"
                            "不得仅据历史回答中的空账号字段，判定当前工具仍无法识别该账号。"
                        )
                result = agent.run_conversation(
                    user_message,
                    system_message=system_message,
                    conversation_history=history,
                    task_id=turn.id,
                )
            if not isinstance(result, dict):
                raise RuntimeError("Hermes returned a non-object Turn result")
        except Exception as exc:
            self.store.mark_interrupted(
                turn.id,
                error_code="hermes_unknown_outcome",
                safe_message="调查执行结果暂时无法确认，可以安全恢复。",
                retryable=True,
            )
            raise RuntimeError("Hermes Turn ended with an unknown outcome") from exc
        if bool(result.get("interrupted")):
            self.store.mark_interrupted(
                turn.id,
                error_code="hermes_interrupted",
                safe_message="调查执行被中断，可以安全恢复。",
                retryable=True,
            )
            raise RuntimeError("Hermes Turn was interrupted")
        if bool(result.get("failed")) or not bool(result.get("completed", True)):
            return self._persist_result(
                turn.id,
                result,
                previous_message_count=len(history or []),
                transcript=None,
            )
        try:
            transcript = self._validate_completed_transcript(
                result,
                history=history,
                user_message=user_message,
            )
            return self._persist_result(
                turn.id,
                result,
                previous_message_count=len(history or []),
                transcript=transcript,
            )
        except Exception as exc:
            current = self.store.get_turn(turn.id)
            if current.status == "completed":
                return self.store.turn_result(turn.id)
            self.store.mark_interrupted(
                turn.id,
                error_code="hermes_unknown_outcome",
                safe_message="调查执行结果暂时无法确认，可以安全恢复。",
                retryable=True,
            )
            raise RuntimeError("Hermes Turn ended with an unknown outcome") from exc

    def accept_resume(self, turn_id: str) -> tuple[InvestigationTurn, bool]:
        turn = self.store.get_turn(turn_id)
        if turn.status in {"completed", "error"}:
            return turn, True
        if turn.status != "interrupted":
            raise InvestigationTurnNotFoundError("turn is not resumable")
        session = self.store.get_session(turn.session_id)
        self._validate_business_scope(session)
        return self.store.begin_resume(turn.id), False

    def execute_resume(self, turn_id: str) -> TurnResult:
        return self.execute_turn(turn_id)

    def add_turn_node_observer(self, observer: Callable[[str, str], None]) -> None:
        self._turn_node_observers.append(observer)

    def owns_turn(self, turn_id: str) -> bool:
        turn = self.store.get_turn(turn_id)
        return self.store.get_session(turn.session_id).scope_type == "report"

    def get_messages(
        self, session_id: str, *, include_tool_messages: bool = False
    ) -> tuple[InvestigationMessage, ...]:
        self.store.get_session(session_id)
        return self.store.list_messages(
            session_id, include_tool_messages=include_tool_messages
        )

    def _product_mode(self, session_id: str) -> str:
        if not self.bind_runtime:
            return "account-activity"
        session = self.store.get_session(session_id)
        uri = self.report_facade.db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            row = connection.execute(
                "SELECT body_json FROM report_versions WHERE id=? AND status='published'",
                (session.report_version_id,),
            ).fetchone()
        document = (json.loads(row[0]).get("report_document") or {}) if row else {}
        if document.get("template_kind") == "all_pass":
            return "pass-report"
        return "account-activity"

    def _agent(self, session_id: str) -> Any:
        with self._agent_lock:
            agent = self._agents.get(session_id)
            if agent is None:
                agent = self.runtime_binding.create_agent(
                    session_id=session_id,
                    agent_factory=self.agent_factory,
                    product_mode=self._product_mode(session_id),
                    base_url=settings.dashscope_base_url,
                    api_key=settings.dashscope_api_key,
                    stream_delta_callback=lambda _delta: None,
                )
                self._agents[session_id] = agent
            return agent

    def _bind_session(self, session: InvestigationSession) -> None:
        if not self.bind_runtime:
            return
        is_bound = getattr(
            self.runtime_binding, "is_published_report_session_bound", None
        )
        if session.id in self._bound_sessions and not callable(is_bound):
            return
        with self._agent_lock:
            self.runtime_binding.configure_product_home(
                session_runtime_home(self.hermes_state_dir, session.id)
            )
            self.runtime_binding.discover_plugins(
                force=not self._bound_sessions, product_mode=self._product_mode(session.id)
            )
        additional_contexts = self._authorized_report_contexts(session)
        self.runtime_binding.bind_published_report_session(
            session_id=session.id,
            database_path=self.report_facade.db_path,
            report_version_id=session.report_version_id,
            content_hash=self.report_facade.get_published_report_context(
                session.report_version_id
            ).content_hash,
            snapshot_hash=session.snapshot_hash,
            ledger_path=self.hermes_state_dir / f"{session.id}.sqlite3",
            additional_report_contexts=additional_contexts,
        )
        self._bound_sessions.add(session.id)

    def _authorized_report_contexts(
        self, session: InvestigationSession
    ) -> tuple[Any, ...]:
        if self.authorized_context_anchor_prefixes is not None:
            anchor = self.store.session_anchor(session.id)
            if anchor.startswith("historical-report:") and session.report_version_id not in self.authorized_report_version_ids:
                # Only explicitly authorized historical versions can use the
                # cross-report context; unknown imports keep their own scope.
                return ()
            allowed_anchor = any(
                anchor.startswith(prefix)
                for prefix in self.authorized_context_anchor_prefixes
            )
            # Published M3 pass reports reuse the server's explicitly authorized
            # account sources. Report navigation remains bound to their own snapshot.
            pass_report_anchor = (
                anchor.startswith("m3-run:")
                and self._product_mode(session.id) == "pass-report"
            )
            if not allowed_anchor and not pass_report_anchor:
                return ()

        additional_contexts = []
        seen_tasks = {session.task_id}
        for report_version_id in self.authorized_report_version_ids:
            if report_version_id == session.report_version_id:
                continue
            context = self.report_facade.get_published_report_context(report_version_id)
            if context.task_id in seen_tasks:
                raise InvestigationTurnNotFoundError(
                    "authorized Account report sources must have unique tasks"
                )
            seen_tasks.add(context.task_id)
            additional_contexts.append(context)
        return tuple(additional_contexts)

    def _validate_business_scope(self, session: InvestigationSession) -> None:
        if session.scope_type != "report":
            raise InvestigationTurnNotFoundError(
                "session is not a published-report investigation Session"
            )
        if session.status != "active":
            if self.bind_runtime:
                self.runtime_binding.release_published_report_session(session.id)
            self._bound_sessions.discard(session.id)
            raise InvestigationTurnNotFoundError("session is not active")
        current = self.report_facade.get_published_report_context(
            session.report_version_id
        )
        if (
            current.task_id != session.task_id
            or current.report_id != session.report_id
            or current.source_snapshot_id != session.source_snapshot_id
            or current.snapshot_hash != session.snapshot_hash
        ):
            raise InvestigationTurnNotFoundError(
                "business session report lock changed"
            )

    def _conversation_history(
        self, session_id: str
    ) -> list[dict[str, Any]] | None:
        return self.store.latest_completed_hermes_transcript(session_id)

    def _persist_result(
        self,
        turn_id: str,
        result: dict[str, Any],
        *,
        previous_message_count: int,
        transcript: list[dict[str, Any]] | None,
    ) -> TurnResult:
        answer = str(result.get("final_response") or "").strip()
        if bool(result.get("failed")) or not bool(result.get("completed", True)):
            self.store.fail_turn(
                turn_id,
                error_code="hermes_execution_failed",
                safe_message=answer or "调查对话暂时无法完成。",
                retryable=False,
                input_tokens=int(result.get("input_tokens") or 0),
                output_tokens=int(result.get("output_tokens") or 0),
                total_tokens=int(result.get("total_tokens") or 0),
                llm_call_count=int(result.get("api_calls") or 0),
                stop_reason=str(result.get("turn_exit_reason") or "failed"),
            )
            return self.store.turn_result(turn_id)
        if transcript is None:
            raise RuntimeError("completed Hermes result requires a validated transcript")
        new_messages = transcript[previous_message_count:]
        trace_messages = [
            item
            for item in new_messages
            if item.get("role") in {"assistant", "tool"}
        ]
        tool_calls = self._tool_calls(trace_messages)
        session = self.store.get_session(self.store.get_turn(turn_id).session_id)
        self.store.complete_turn(
            turn_id,
            answer=answer,
            trace_messages=trace_messages,
            pending_sources=[],
            grounding_validation={
                "status": "passed",
                "source_count": sum(
                    item.get("role") == "tool" for item in trace_messages
                ),
                "warnings": [],
            },
            resolved_references=[],
            all_tool_calls=tool_calls,
            query_receipts=[],
            summary_text=answer[-2_000:],
            active_focus=session.active_focus,
            ordered_referents=[item.model_dump(mode="json") for item in session.ordered_referents],
            last_claim_id=session.last_claim_id,
            last_finding_id=session.last_finding_id,
            last_evidence_id=session.last_evidence_id,
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
        )
        self._notify(turn_id, "persist_turn")
        return self.store.turn_result(turn_id)

    @staticmethod
    def _validate_completed_transcript(
        result: dict[str, Any],
        *,
        history: list[dict[str, Any]] | None,
        user_message: str,
    ) -> list[dict[str, Any]]:
        transcript = result.get("messages")
        if not isinstance(transcript, list):
            raise RuntimeError("Hermes did not return a complete message transcript")
        validate_hermes_transcript_messages(transcript)
        expected_history = history or []
        if transcript[: len(expected_history)] != expected_history:
            raise RuntimeError("Hermes transcript did not preserve its input history")
        if len(transcript) <= len(expected_history):
            raise RuntimeError("Hermes transcript omitted the current user message")
        current_user = transcript[len(expected_history)]
        if (
            not isinstance(current_user, dict)
            or current_user.get("role") != "user"
            or current_user.get("content") != user_message
        ):
            raise RuntimeError("Hermes transcript changed the current user message")
        final_content = transcript[-1].get("content")
        if final_content != result.get("final_response"):
            raise RuntimeError(
                "Hermes final assistant does not match the completed response"
            )
        return [dict(item) for item in transcript]

    @staticmethod
    def _tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for message in messages:
            for raw in message.get("tool_calls") or []:
                if not isinstance(raw, dict):
                    continue
                function = raw.get("function") or {}
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    arguments = {}
                output.append(
                    {
                        "id": str(raw.get("id") or "hermes-tool-call"),
                        "name": str(function.get("name") or raw.get("name") or "unknown"),
                        "arguments": dict(arguments),
                    }
                )
        return output

    def _notify(self, turn_id: str, node_name: str) -> None:
        for observer in tuple(self._turn_node_observers):
            observer(turn_id, node_name)
