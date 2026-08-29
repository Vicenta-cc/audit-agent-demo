from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from backend.audit_agent.config import settings
from backend.domain.identity import stable_hash
from backend.investigation.artifacts import (
    EvidenceArtifactCompiler,
    EvidenceCanonicalCapture,
)
from backend.investigation.context import InvestigationContextBuilder, ReferenceResolver
from backend.investigation.contracts import (
    AcquisitionRequired,
    BoundEvidenceCollectionRequirement,
    BoundEvidenceDetailRequirement,
    CaseSelection,
    FocusedCaseContext,
    GroundingIssue,
    InvestigationAgentState,
    InvestigationMessage,
    InvestigationSession,
    InvestigationTurn,
    PlannerInputSnapshot,
    ReadySourceBundle,
    ResolvedReference,
    SourceLedgerCandidate,
    SourceFailure,
    ToolCall,
    ToolResultEnvelope,
    TurnResult,
    UnsupportedCapacity,
    UnsupportedComplete,
)
from backend.investigation.errors import (
    CheckpointScopeMismatchError,
    ContextBudgetError,
    InvestigationError,
    InvestigationTurnNotFoundError,
    QwenChatError,
    RunTokenBudgetError,
    ToolProtocolError,
)
from backend.investigation.protocol import (
    assistant_tool_message,
    validate_model_request_messages,
)
from backend.investigation.qwen_chat_tool_client import QwenChatToolClient
from backend.investigation.planner import TurnPlanner
from backend.investigation.orchestrator import SourceOrchestrator
from backend.investigation.report_query import ReportQueryFacade
from backend.investigation.store import InvestigationStore
from backend.investigation.tools import InvestigationToolService


logger = logging.getLogger(__name__)


_EVIDENCE_DISPLAY_LABELS = {
    "text": "文本证据",
    "comment": "评论证据",
    "ocr": "OCR 证据",
    "asr": "语音转写证据",
    "visual": "画面证据",
    "keyframe": "关键帧证据",
    "profile": "账号资料",
    "rule_reference": "规则依据",
    "other": "证据",
}


class InvestigationAgentService:
    """Phase 3A read-only chat service with an explicit LangGraph tool loop."""

    def __init__(
        self,
        *,
        report_facade: ReportQueryFacade | None = None,
        store: InvestigationStore | None = None,
        model_client: QwenChatToolClient | Any | None = None,
        checkpoint_path: Path | None = None,
        max_tool_iterations: int = 6,
        max_tool_result_size: int = 24_000,
        max_context_tokens: int = 48_000,
        max_run_tokens: int = 96_000,
        token_warning_ratio: float = 0.8,
        no_progress_limit: int = 2,
        max_grounding_retries: int = 3,
        reserved_output_tokens: int | None = None,
        shadow_planner: TurnPlanner | None = None,
        enable_shadow_planner: bool = True,
        source_orchestrator: SourceOrchestrator | None = None,
        enable_source_orchestrator_shadow: bool = True,
        enable_evidence_source_control: bool = True,
        enable_controlled_evidence_react: bool = True,
        max_evidence_react_iterations: int = 2,
        source_bundle_token_budget: int = 12_000,
    ):
        self.report_facade = report_facade or ReportQueryFacade()
        self.store = store or InvestigationStore()
        self.tool_service = InvestigationToolService(
            self.report_facade, max_result_size=max_tool_result_size
        )
        self.model_client = model_client or QwenChatToolClient(
            allowed_tool_names=self.tool_service.allowed_tool_names
        )
        self.reserved_output_tokens = max(
            1,
            int(
                reserved_output_tokens
                if reserved_output_tokens is not None
                else getattr(self.model_client, "max_tokens", 2_000)
            ),
        )
        self.reference_resolver = ReferenceResolver()
        self.shadow_planner = (
            shadow_planner or TurnPlanner() if enable_shadow_planner else None
        )
        self.enable_source_orchestrator_shadow = bool(
            enable_source_orchestrator_shadow and self.shadow_planner is not None
        )
        self.enable_evidence_source_control = bool(
            enable_evidence_source_control and self.shadow_planner is not None
        )
        self.enable_controlled_evidence_react = bool(
            enable_controlled_evidence_react
            and self.enable_evidence_source_control
        )
        self.max_evidence_react_iterations = max(
            0, min(int(max_evidence_react_iterations), 2)
        )
        if (
            self.enable_source_orchestrator_shadow
            or self.enable_evidence_source_control
        ):
            self.source_orchestrator = source_orchestrator or SourceOrchestrator(
                self.store,
                source_token_budget=source_bundle_token_budget,
            )
        else:
            self.source_orchestrator = None
        self.context_builder = InvestigationContextBuilder(
            max_context_tokens=max_context_tokens
        )
        self.max_tool_iterations = max(1, int(max_tool_iterations))
        self.max_run_tokens = max(1_000, int(max_run_tokens))
        self.token_warning_ratio = min(max(float(token_warning_ratio), 0.1), 0.99)
        self.no_progress_limit = max(1, int(no_progress_limit))
        self.max_grounding_retries = max(0, min(int(max_grounding_retries), 3))
        self.recursion_limit = max(24, self.max_tool_iterations * 4 + 12)
        checkpoint_file = (
            checkpoint_path
            or (settings.data_dir / "investigation_checkpoints.sqlite3")
        ).resolve()
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_connection = sqlite3.connect(
            checkpoint_file, check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        self.checkpointer.setup()
        self._turn_node_observers: list[Callable[[str, str], None]] = []
        self.app = self._build_graph().compile(checkpointer=self.checkpointer)

    def close(self) -> None:
        self._checkpoint_connection.close()

    def create_session(
        self,
        report_version_id: str,
        *,
        anchor_key: str = "",
    ) -> InvestigationSession:
        context = self.report_facade.get_published_report_context(report_version_id)
        return self.store.create_session(context, anchor_key=anchor_key)

    def send_message(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
    ) -> TurnResult:
        turn, replay = self.accept_message(
            session_id,
            client_message_id=client_message_id,
            content=content,
        )
        if replay:
            if turn.status in {"completed", "error"}:
                return self.store.turn_result(turn.id, idempotent_replay=True)
            raise InvestigationTurnNotFoundError("idempotent turn is still active")
        return self.execute_turn(turn.id)

    def accept_message(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
    ) -> tuple[InvestigationTurn, bool]:
        """Persist a Turn before execution and report idempotent replay separately."""
        user_input = str(content or "").strip()
        client_id = str(client_message_id or "").strip()
        if not user_input or len(user_input) > 4_000:
            raise ValueError("content must contain between 1 and 4000 characters")
        if not client_id or len(client_id) > 200:
            raise ValueError("client_message_id must contain between 1 and 200 characters")
        session = self.store.get_session(session_id)
        self._validate_business_scope(session)
        config = self._graph_config(session.id)
        self._validate_checkpoint_scope(session, config, required=False)
        turn, replay = self.store.create_turn(
            session.id, client_message_id=client_id, user_input=user_input
        )
        return turn, replay

    def execute_turn(self, turn_id: str) -> TurnResult:
        """Execute a previously accepted Turn without creating another message."""
        turn = self.store.get_turn(turn_id)
        if turn.status in {"completed", "error"}:
            return self.store.turn_result(turn.id, idempotent_replay=True)
        if turn.status != "running":
            raise InvestigationTurnNotFoundError("turn is not ready for execution")
        session = self.store.get_session(turn.session_id)
        self._validate_business_scope(session)
        config = self._graph_config(session.id)
        self._validate_checkpoint_scope(session, config, required=False)
        user_input = self.store.get_user_message_for_turn(turn.id).content
        self._observe_planner_shadow(session, turn.id, user_input)
        self._observe_source_preparation_shadow(session, turn.id, user_input)
        initial = self._initial_state(session, turn.id, user_input)
        try:
            self.app.invoke(initial, config=config)
        except Exception as exc:
            terminal = self._handle_execution_error(turn.id, exc)
            if terminal is not None:
                return terminal
            raise
        return self.store.turn_result(turn.id)

    def resume_turn(self, turn_id: str) -> TurnResult:
        turn, replay = self.accept_resume(turn_id)
        if replay:
            return self.store.turn_result(turn.id, idempotent_replay=True)
        return self.execute_resume(turn.id)

    def accept_resume(self, turn_id: str) -> tuple[InvestigationTurn, bool]:
        """Move an interrupted Turn back to running after validating its checkpoint."""
        turn = self.store.get_turn(turn_id)
        if turn.status in {"completed", "error"}:
            return turn, True
        session = self.store.get_session(turn.session_id)
        self._validate_business_scope(session)
        config = self._graph_config(session.id)
        self._validate_checkpoint_scope(
            session, config, required=True, expected_turn_id=turn.id
        )
        return self.store.begin_resume(turn.id), False

    def execute_resume(self, turn_id: str) -> TurnResult:
        """Resume a Turn that has already passed checkpoint validation."""
        turn = self.store.get_turn(turn_id)
        if turn.status in {"completed", "error"}:
            return self.store.turn_result(turn.id, idempotent_replay=True)
        if turn.status != "running":
            raise InvestigationTurnNotFoundError("turn is not ready to resume")
        session = self.store.get_session(turn.session_id)
        self._validate_business_scope(session)
        config = self._graph_config(session.id)
        self._validate_checkpoint_scope(
            session, config, required=True, expected_turn_id=turn.id
        )
        try:
            self.app.invoke(None, config=config)
        except Exception as exc:
            terminal = self._handle_execution_error(turn.id, exc)
            if terminal is not None:
                return terminal
            raise
        return self.store.turn_result(turn.id)

    def add_turn_node_observer(self, observer: Callable[[str, str], None]) -> None:
        self._turn_node_observers.append(observer)

    def get_messages(
        self, session_id: str, *, include_tool_messages: bool = False
    ) -> tuple[InvestigationMessage, ...]:
        self.store.get_session(session_id)
        return self.store.list_messages(
            session_id, include_tool_messages=include_tool_messages
        )

    def open_source(self, ledger_id: str) -> dict[str, Any]:
        entries = self.store.list_ledger(limit=1_000)
        for entry in entries:
            if entry.ledger_id == ledger_id:
                return entry.model_dump(mode="json")
        raise InvestigationTurnNotFoundError("source ledger entry not found")

    def _observe_planner_shadow(
        self, session: InvestigationSession, turn_id: str, user_input: str
    ) -> None:
        planner = self.shadow_planner
        if planner is None:
            return
        try:
            existing = self.store.get_planner_shadow_trace(
                turn_id, planner_prompt_version=planner.prompt_version
            )
            if existing is not None:
                return
        except Exception:
            return
        try:
            resolved, _, _ = self.reference_resolver.resolve(user_input, session)
            input_snapshot = planner.input_snapshot(
                current_user_message=user_input,
                active_focus=session.active_focus,
                resolved_references=resolved,
            )
        except Exception:
            focus_exists = bool(session.active_focus.get("target_id"))
            input_snapshot = PlannerInputSnapshot(
                current_user_message=user_input,
                active_focus_exists=focus_exists,
                active_focus_type=(
                    str(session.active_focus.get("type") or "unknown")
                    if focus_exists
                    else ""
                ),
                resolved_referent_status="not_applicable",
                resolved_referent_type="",
            )
            trace = planner.execution_error_trace(
                session=session,
                turn_id=turn_id,
                input_snapshot=input_snapshot,
                error_code="planner_snapshot_error",
                error_message="Planner pre-turn context snapshot failed.",
            )
        else:
            try:
                trace = planner.plan(
                    session=session,
                    turn_id=turn_id,
                    input_snapshot=input_snapshot,
                )
            except Exception:
                trace = planner.execution_error_trace(
                    session=session,
                    turn_id=turn_id,
                    input_snapshot=input_snapshot,
                )
        try:
            self.store.put_planner_shadow_trace(trace)
        except Exception:
            # Storage failures are also isolated from the Agent execution path.
            return

    def _observe_source_preparation_shadow(
        self, session: InvestigationSession, turn_id: str, user_input: str
    ) -> None:
        if not self.enable_source_orchestrator_shadow:
            return
        orchestrator = self.source_orchestrator
        planner = self.shadow_planner
        if orchestrator is None or planner is None:
            return
        try:
            existing = self.store.get_source_preparation_shadow_trace(
                turn_id,
                orchestrator_version=orchestrator.version,
            )
            if existing is not None:
                return
            planner_trace = self.store.get_planner_shadow_trace(
                turn_id,
                planner_prompt_version=planner.prompt_version,
            )
            if planner_trace is None:
                return
            resolved, _, _ = self.reference_resolver.resolve(user_input, session)
            trace = orchestrator.observe_shadow(
                session=session,
                turn_id=turn_id,
                planner_trace=planner_trace,
                resolved_references=resolved,
            )
            self.store.put_source_preparation_shadow_trace(trace)
        except Exception:
            # Source preparation remains an observer until Phase 4.
            return

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(InvestigationAgentState)
        graph.add_node("prepare_context", self._wrap("prepare_context", self._prepare_context))
        graph.add_node(
            "prepare_evidence_sources",
            self._wrap("prepare_evidence_sources", self._prepare_evidence_sources),
        )
        graph.add_node("call_qwen", self._wrap("call_qwen", self._call_qwen))
        graph.add_node("execute_tools", self._wrap("execute_tools", self._execute_tools))
        graph.add_node(
            "prepare_case_answer",
            self._wrap("prepare_case_answer", self._prepare_case_answer),
        )
        graph.add_node("assess_progress", self._wrap("assess_progress", self._assess_progress))
        graph.add_node("prepare_final", self._wrap("prepare_final", self._prepare_final))
        graph.add_node(
            "prepare_grounding_retry",
            self._wrap("prepare_grounding_retry", self._prepare_grounding_retry),
        )
        graph.add_node(
            "validate_grounding",
            self._wrap("validate_grounding", self._validate_grounding),
        )
        graph.add_node("persist_turn", self._wrap("persist_turn", self._persist_turn))
        graph.add_node("persist_error", self._wrap("persist_error", self._persist_error))
        graph.add_edge(START, "prepare_context")
        graph.add_edge("prepare_context", "prepare_evidence_sources")
        graph.add_conditional_edges(
            "prepare_evidence_sources",
            self._route_after_source_preparation,
            {
                "answer": "call_qwen",
                "acquire": "execute_tools",
                "safe_final": "validate_grounding",
                "error": "persist_error",
            },
        )
        graph.add_conditional_edges(
            "call_qwen",
            self._route_after_qwen,
            {
                "tools": "execute_tools",
                "case_selected": "prepare_case_answer",
                "final": "validate_grounding",
                "retry_final": "prepare_final",
                "retry_grounding": "prepare_grounding_retry",
            },
        )
        graph.add_conditional_edges(
            "execute_tools",
            self._route_after_tool_execution,
            {
                "source_preparation": "prepare_evidence_sources",
                "agent_progress": "assess_progress",
                "error": "persist_error",
            },
        )
        graph.add_edge("prepare_case_answer", "call_qwen")
        graph.add_conditional_edges(
            "assess_progress",
            lambda state: "final" if state.get("stop_reason") else "continue",
            {"final": "prepare_final", "continue": "call_qwen"},
        )
        graph.add_edge("prepare_final", "call_qwen")
        graph.add_edge("prepare_grounding_retry", "call_qwen")
        graph.add_conditional_edges(
            "validate_grounding",
            lambda state: "error" if state.get("error") else "success",
            {"error": "persist_error", "success": "persist_turn"},
        )
        graph.add_edge("persist_turn", END)
        graph.add_edge("persist_error", END)
        return graph

    def _prepare_evidence_sources(
        self, state: InvestigationAgentState
    ) -> dict[str, Any]:
        orchestrator = self.source_orchestrator
        planner = self.shadow_planner
        if (
            not self.enable_evidence_source_control
            or orchestrator is None
            or planner is None
        ):
            return {
                "evidence_source_control_active": False,
                "evidence_source_control_status": "not_applicable",
            }

        bound_data = dict(state.get("evidence_source_bound_requirement") or {})
        if bound_data:
            if bound_data.get("requirement_kind") == "finding_evidence_collection":
                bound_requirement = BoundEvidenceCollectionRequirement.model_validate(
                    bound_data
                )
            else:
                bound_requirement = BoundEvidenceDetailRequirement.model_validate(
                    bound_data
                )
        else:
            planner_trace = self.store.get_planner_shadow_trace(
                state["turn_id"], planner_prompt_version=planner.prompt_version
            )
            if planner_trace is None:
                return {
                    "evidence_source_control_active": True,
                    "evidence_source_control_status": "error",
                    "error": "planner_trace_missing",
                    "safe_message": "无法安全判断本轮所需资料，请重试。",
                    "retryable": True,
                    "stop_reason": "planner_control_unavailable",
                }
            if planner_trace.planning_status != "ok" or planner_trace.plan is None:
                return {
                    "evidence_source_control_active": True,
                    "evidence_source_control_status": "error",
                    "error": planner_trace.error_code or "planner_execution_error",
                    "safe_message": "无法安全判断本轮所需资料，请重试。",
                    "retryable": True,
                    "stop_reason": "planner_control_unavailable",
                }
            if planner_trace.plan.requirement_kind == "unsupported":
                return {
                    "evidence_source_control_active": True,
                    "evidence_source_control_status": "safe_final",
                    "answer": "当前请求包含多个或暂不支持的资料需求，请拆分为一次一个资料需求后重试。",
                    "tools_disabled": True,
                    "stop_reason": "unsupported_requirement",
                }
            if planner_trace.plan.requirement_kind not in {
                "finding_evidence_collection",
                "evidence_detail",
            }:
                return {
                    "evidence_source_control_active": False,
                    "evidence_source_control_status": "not_applicable",
                }
            session = self.store.get_session(state["session_id"])
            try:
                binding = orchestrator.subject_binder.bind(
                    plan=planner_trace.plan,
                    session=session,
                    resolved_references=(
                        ResolvedReference.model_validate(item)
                        for item in state.get("resolved_references") or []
                    ),
                )
            except Exception:
                return {
                    "evidence_source_control_active": True,
                    "evidence_source_control_status": "error",
                    "error": "source_subject_binding_failure",
                    "safe_message": "Evidence 对象绑定失败，无法安全准备资料。",
                    "retryable": False,
                    "stop_reason": "source_preparation_failure",
                }
            if binding.status == "need_subject":
                noun = (
                    "Finding"
                    if planner_trace.plan.requirement_kind
                    == "finding_evidence_collection"
                    else "Evidence"
                )
                return {
                    "evidence_source_control_active": True,
                    "evidence_source_control_status": "safe_final",
                    "answer": f"我还无法唯一确定你指的是哪条 {noun}，请先明确对象。",
                    "tools_disabled": True,
                    "stop_reason": "source_subject_required",
                }
            if binding.status != "bound" or binding.bound_requirement is None:
                return {
                    "evidence_source_control_active": True,
                    "evidence_source_control_status": "error",
                    "error": "source_subject_binding_invalid",
                    "safe_message": "Evidence 对象绑定结果无效，无法安全准备资料。",
                    "retryable": False,
                    "stop_reason": "source_preparation_failure",
                }
            bound_requirement = binding.bound_requirement

        supplemental_requirements = tuple(
            BoundEvidenceDetailRequirement.model_validate(item)
            for item in state.get("evidence_react_supplemental_requirements") or []
        )
        current_bundle_data = dict(state.get("ready_source_bundle") or {})
        parent_bundle_fingerprint = str(
            current_bundle_data.get("bundle_fingerprint") or ""
        )
        try:
            decision = orchestrator.prepare(
                bound_requirement,
                supplemental_requirements=supplemental_requirements,
                parent_bundle_fingerprint=(
                    parent_bundle_fingerprint
                    if supplemental_requirements
                    else None
                ),
            )
        except Exception:
            return {
                "evidence_source_control_active": True,
                "evidence_source_control_status": "error",
                "evidence_source_bound_requirement": bound_requirement.model_dump(
                    mode="json"
                ),
                "error": "source_orchestrator_failure",
                "safe_message": "Evidence 资料准备失败，无法安全回答。",
                "retryable": False,
                "stop_reason": "source_preparation_failure",
            }
        history = [
            *(state.get("evidence_source_history") or []),
            {
                "status": decision.status,
                "bound_requirement": bound_requirement.model_dump(mode="json"),
            },
        ]
        base = {
            "evidence_source_control_active": True,
            "evidence_source_bound_requirement": bound_requirement.model_dump(
                mode="json"
            ),
            "evidence_source_history": history,
        }
        if isinstance(decision, ReadySourceBundle):
            try:
                pending = self._merge_source_candidates(
                    list(state.get("pending_ledger_entries") or []),
                    [
                        item.model_dump(mode="json")
                        for item in orchestrator.ledger_candidates(decision)
                    ],
                )
            except Exception:
                return {
                    **base,
                    "evidence_source_control_status": "error",
                    "error": "source_bundle_audit_failure",
                    "safe_message": "ReadySourceBundle 的来源审计信息无法恢复。",
                    "retryable": False,
                    "stop_reason": "source_preparation_failure",
                }
            allowed_react_refs = self._controlled_react_allowed_refs(
                decision, supplemental_requirements
            )
            controlled_react_available = bool(
                self.enable_controlled_evidence_react
                and allowed_react_refs
                and len(supplemental_requirements)
                < self.max_evidence_react_iterations
                and int(state.get("tool_iteration_count") or 0)
                < self.max_tool_iterations
            )
            bundle_history = list(
                state.get("evidence_react_bundle_fingerprints") or []
            )
            if (
                not bundle_history
                or bundle_history[-1] != decision.bundle_fingerprint
            ):
                bundle_history.append(decision.bundle_fingerprint)
            return {
                **base,
                "evidence_source_control_status": "ready",
                "ready_source_bundle": decision.model_dump(mode="json"),
                "pending_ledger_entries": pending,
                "last_finding_id": (
                    bound_requirement.finding_ref
                    if isinstance(
                        bound_requirement, BoundEvidenceCollectionRequirement
                    )
                    else state.get("last_finding_id") or ""
                ),
                "last_evidence_id": (
                    bound_requirement.evidence_ref
                    if isinstance(bound_requirement, BoundEvidenceDetailRequirement)
                    else state.get("last_evidence_id") or ""
                ),
                "latest_model_response": {},
                "evidence_react_enabled": controlled_react_available,
                "evidence_react_allowed_refs": list(allowed_react_refs),
                "evidence_react_bundle_fingerprints": bundle_history,
                "tools_disabled": not controlled_react_available,
                "force_tool_choice": False,
            }
        if isinstance(decision, AcquisitionRequired):
            attempts = int(state.get("evidence_source_attempt_count") or 0)
            if attempts >= 1:
                return {
                    **base,
                    "evidence_source_control_status": "error",
                    "error": "source_acquisition_unsatisfied",
                    "safe_message": (
                        "所需 Evidence 查询没有生成可复用的 Source Artifact。"
                    ),
                    "retryable": False,
                    "stop_reason": "source_acquisition_unsatisfied",
                }
            tool_identity = stable_hash(
                {
                    "turn_id": state["turn_id"],
                    "bound_requirement": bound_requirement.model_dump(mode="json"),
                    "required_tool": decision.required_tool,
                    "required_arguments": decision.required_arguments,
                }
            )
            call = ToolCall(
                id=f"source-orchestrator:{tool_identity[:32]}",
                name=decision.required_tool,
                arguments=decision.required_arguments,
            )
            return {
                **base,
                "evidence_source_control_status": "acquiring",
                "evidence_source_attempt_count": attempts + 1,
                "latest_model_response": {
                    "content": "",
                    "tool_calls": [call.model_dump(mode="json")],
                },
                "tools_disabled": False,
                "force_tool_choice": False,
            }
        if isinstance(decision, UnsupportedComplete):
            return {
                **base,
                "evidence_source_control_status": "safe_final",
                "answer": (
                    "当前查询能力无法证明已经取得全部 Evidence，"
                    "因此不能按完整集合要求作答。"
                ),
                "tools_disabled": True,
                "stop_reason": "unsupported_complete",
            }
        if isinstance(decision, UnsupportedCapacity):
            return {
                **base,
                "evidence_source_control_status": "safe_final",
                "answer": "所需 Evidence 超出本轮可可靠处理的容量，无法完整作答。",
                "tools_disabled": True,
                "stop_reason": "unsupported_source_capacity",
            }
        if isinstance(decision, SourceFailure):
            return {
                **base,
                "evidence_source_control_status": "error",
                "error": decision.error_code,
                "safe_message": decision.safe_message,
                "retryable": decision.retryable,
                "stop_reason": "source_preparation_failure",
            }
        raise ToolProtocolError("unknown SourceOrchestrator decision")

    @staticmethod
    def _merge_source_candidates(
        current: list[dict[str, Any]], additions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        output = list(current)
        keys = {
            (
                str(item.get("source_kind") or ""),
                str(item.get("evidence_id") or ""),
                str(item.get("query_fingerprint") or ""),
            )
            for item in output
        }
        for item in additions:
            key = (
                str(item.get("source_kind") or ""),
                str(item.get("evidence_id") or ""),
                str(item.get("query_fingerprint") or ""),
            )
            if key not in keys:
                keys.add(key)
                output.append(item)
        return output

    @staticmethod
    def _route_after_source_preparation(state: InvestigationAgentState) -> str:
        status = str(state.get("evidence_source_control_status") or "")
        if status == "acquiring":
            return "acquire"
        if status == "safe_final":
            return "safe_final"
        if status == "error" or state.get("error"):
            return "error"
        return "answer"

    @staticmethod
    def _route_after_tool_execution(state: InvestigationAgentState) -> str:
        if state.get("error"):
            return "error"
        if (
            state.get("evidence_source_control_active")
            and state.get("evidence_source_control_status")
            in {"acquiring", "react_acquired"}
        ):
            return "source_preparation"
        return "agent_progress"

    def _wrap(
        self,
        name: str,
        function: Callable[[InvestigationAgentState], dict[str, Any]],
    ) -> Callable[[InvestigationAgentState], dict[str, Any]]:
        def wrapped(state: InvestigationAgentState) -> dict[str, Any]:
            turn_id = str(state.get("turn_id") or "")
            if turn_id:
                self.store.set_turn_node(turn_id, name)
                for observer in tuple(self._turn_node_observers):
                    try:
                        observer(turn_id, name)
                    except Exception:
                        logger.exception("investigation Turn node observer failed")
            return {**function(state), "current_node": name}

        return wrapped

    def _prepare_context(self, state: InvestigationAgentState) -> dict[str, Any]:
        session = self.store.get_session(state["session_id"])
        self._assert_state_scope(state, session)
        resolved, response_style, inherit = self.reference_resolver.resolve(
            state["user_input"], session
        )
        recent_messages = self.store.recent_conversation(session.id)
        previous_ledger = (
            list(
                self.store.list_ledger(
                    message_id=session.last_answer_message_id, limit=100
                )
            )
            if session.last_answer_message_id
            else []
        )
        case_catalog = []
        if any(item.type == "case" for item in session.ordered_referents):
            case_catalog = self.report_facade.get_case_catalog(session.report_version_id)

        resolved_target = next(
            (
                item.target_id
                for item in resolved
                if item.status == "resolved" and item.target_id
            ),
            "",
        )
        if not resolved_target and str(session.active_focus.get("type") or "") in {
            "claim",
            "finding",
            "evidence",
        }:
            resolved_target = str(session.active_focus.get("target_id") or "")
        focused_case = (
            self.report_facade.find_focused_case_context(
                session.report_version_id, resolved_target
            )
            if resolved_target
            else None
        )
        authority = self._focused_authority_from_ledger(
            focused_case, previous_ledger
        )
        reused = self._reused_focused_sources(
            focused_case=focused_case,
            case_catalog=case_catalog,
            ledger=previous_ledger,
        )
        inherited = (
            [self._candidate_from_ledger(item.model_dump(mode="json")) for item in previous_ledger]
            if inherit
            else [self._candidate_from_ledger(item.model_dump(mode="json")) for item in reused]
        )
        focused_authority_required = bool(
            focused_case
            and resolved
            and not authority["validated_claim_refs"]
            and not authority["validated_finding_refs"]
        )
        working = state.get("working_messages") or [
            {"role": "user", "content": state["user_input"]}
        ]
        return {
            "summary_text": session.summary_text,
            "recent_messages": recent_messages,
            "working_messages": working,
            "response_style": response_style,
            "active_focus": session.active_focus,
            "ordered_referents": [
                item.model_dump(mode="json") for item in session.ordered_referents
            ],
            "last_claim_id": session.last_claim_id,
            "last_finding_id": session.last_finding_id,
            "last_evidence_id": session.last_evidence_id,
            "last_answer_message_id": session.last_answer_message_id,
            "resolved_references": [item.model_dump(mode="json") for item in resolved],
            "case_catalog": case_catalog,
            "case_selection_available": bool(case_catalog and not focused_case),
            "case_selection_completed": False,
            "case_selection_trace": {},
            "case_selection_membership_validated": False,
            "focused_case": (
                focused_case.model_dump(mode="json") if focused_case else {}
            ),
            "focused_authoritative_facts": authority["facts"],
            "focused_validated_claim_refs": authority["validated_claim_refs"],
            "focused_validated_finding_refs": authority["validated_finding_refs"],
            "focused_validated_evidence_refs": authority["validated_evidence_refs"],
            "focused_authority_required": focused_authority_required,
            "conversation_continuity": [
                item for item in recent_messages if item.get("role") == "user"
            ][-4:],
            "inherit_last_sources": bool(inherit or inherited),
            "inherited_ledger_entries": inherited,
            "pending_ledger_entries": list(state.get("pending_ledger_entries") or inherited),
            "recent_ledger_refs": self.store.recent_ledger_refs(session.id),
            "tools_disabled": bool(state.get("tools_disabled")) or inherit,
            "force_tool_choice": bool(state.get("force_tool_choice"))
            or focused_authority_required,
        }

    def _call_qwen(self, state: InvestigationAgentState) -> dict[str, Any]:
        tools = self._model_tool_definitions(state)
        messages, accounting = self.context_builder.build_request(
            state=state, tool_definitions=tools
        )
        validate_model_request_messages(messages)
        estimated = self.context_builder.estimate_tokens([messages, tools])
        if estimated > self.context_builder.max_context_tokens:
            raise ContextBudgetError()
        projected_run_tokens = (
            int(state.get("total_tokens") or 0)
            + estimated
            + self.reserved_output_tokens
        )
        if projected_run_tokens > self.max_run_tokens:
            raise RunTokenBudgetError(
                f"projected run tokens {projected_run_tokens} exceed {self.max_run_tokens}"
            )
        result = self.model_client.complete(
            messages=messages,
            tools=tools,
            tool_choice=(
                "none"
                if state.get("tools_disabled")
                else "required"
                if state.get("force_tool_choice")
                else "auto"
            ),
        )
        call_ids = list(state.get("model_call_ids") or [])
        input_tokens = int(state.get("input_tokens") or 0)
        output_tokens = int(state.get("output_tokens") or 0)
        total_tokens = int(state.get("total_tokens") or 0)
        llm_call_count = int(state.get("llm_call_count") or 0)
        if result.request_id not in call_ids:
            call_ids.append(result.request_id)
            input_tokens += result.usage.input_tokens
            output_tokens += result.usage.output_tokens
            total_tokens += result.usage.total_tokens
            llm_call_count += 1
        token_warning = total_tokens >= int(
            self.max_run_tokens * self.token_warning_ratio
        )
        disabled_tool_call = bool(state.get("tools_disabled") and result.tool_calls)
        accounting.update(
            {
                "call_index": llm_call_count,
                "actual_input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            }
        )
        scope_update: dict[str, Any] = {}
        if (
            state.get("repair_mode") == "scope_repair"
            and result.content.strip()
            and not result.tool_calls
        ):
            scope_update = {
                "scope_repaired_draft": result.content.strip(),
                "scope_remaining_issues": [
                    item.model_dump(mode="json")
                    for item in self._grounding_issues(state, result.content)
                ],
            }
        return {
            "latest_model_response": result.model_dump(mode="json"),
            "answer": result.content.strip() if not result.tool_calls else "",
            "model_call_ids": call_ids,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "llm_call_count": llm_call_count,
            "context_accounting": [
                *(state.get("context_accounting") or []),
                accounting,
            ],
            "token_warning": token_warning,
            "error": "tool_call_while_disabled" if disabled_tool_call else "",
            "retryable": False,
            **scope_update,
        }

    def _route_after_qwen(self, state: InvestigationAgentState) -> str:
        response = state.get("latest_model_response") or {}
        calls = response.get("tool_calls") or []
        content = str(response.get("content") or "").strip()
        if calls:
            if state.get("tools_disabled"):
                return "final"
            return "tools"
        if content and self._parse_case_selection(content) is not None:
            if state.get("case_selection_completed"):
                raise ToolProtocolError("case selection was repeated after focus was established")
            return "case_selected"
        if content:
            issues = self._grounding_issues(state, content)
            missing_source = any(
                item.issue_type == "missing_authoritative_source" for item in issues
            )
            can_scope_repair = (
                missing_source
                and int(state.get("scope_repair_count") or 0) < 1
            )
            can_source_repair = (
                missing_source
                and int(state.get("scope_repair_count") or 0) >= 1
                and bool(state.get("scope_source_tools_allowed"))
                and int(state.get("source_repair_count") or 0) < 1
            )
            can_semantic_rewrite = (
                int(state.get("semantic_rewrite_count") or 0) < 1
            )
            if (
                issues
                and int(state.get("grounding_retry_count") or 0)
                < self.max_grounding_retries
                and (
                    can_scope_repair
                    or can_source_repair
                    or can_semantic_rewrite
                )
            ):
                return "retry_grounding"
            return "final"
        if (
            int(state.get("tool_iteration_count") or 0) > 0
            and not state.get("tools_disabled")
            and int(state.get("final_response_retry_count") or 0) < 1
        ):
            return "retry_final"
        return "final"

    def _prepare_case_answer(
        self, state: InvestigationAgentState
    ) -> dict[str, Any]:
        content = str((state.get("latest_model_response") or {}).get("content") or "")
        selection = self._parse_case_selection(content)
        if selection is None:
            raise ToolProtocolError("case selection response is malformed")
        try:
            focused = self.report_facade.get_focused_case_context(
                state["report_version_id"], selection.selected_case_ref
            )
        except InvestigationError as exc:
            raise ToolProtocolError(
                "selected case ref is not a member of the locked report version"
            ) from exc
        focused_data = focused.model_dump(mode="json")
        selected_referent = {
            "type": "case",
            "target_id": focused.case_ref,
            "label": focused.title,
            "source_message_id": "",
            "list_position": focused.case_index,
            "ledger_ids": [],
        }
        ordered = [
            selected_referent,
            *(
                item
                for item in state.get("ordered_referents") or []
                if not (
                    str(item.get("type") or "") == "case"
                    and str(item.get("target_id") or "") == focused.case_ref
                )
            ),
        ]
        selection_trace = {
            "stage": "case_selection",
            "selected_case_ref": focused.case_ref,
            "selected_case_title": focused.title,
            "related_claim_refs": list(focused.related_claim_refs),
            "related_finding_refs": list(focused.related_finding_refs),
            "membership_validation": "passed",
        }
        return {
            "working_messages": [
                *(state.get("working_messages") or []),
                {
                    "role": "assistant",
                    "content": content.strip(),
                    "internal_stage": "case_selection",
                },
            ],
            "latest_model_response": {},
            "answer": "",
            "case_selection_available": False,
            "case_selection_completed": True,
            "case_selection_trace": selection_trace,
            "case_selection_membership_validated": True,
            "focused_case": focused_data,
            "focused_authoritative_facts": [],
            "focused_validated_claim_refs": [],
            "focused_validated_finding_refs": [],
            "focused_validated_evidence_refs": [],
            "focused_authority_required": False,
            "active_focus": {
                "type": "case",
                "target_id": focused.case_ref,
                "label": focused.title,
            },
            "ordered_referents": ordered,
            "last_claim_id": focused.case_ref,
            "force_tool_choice": False,
            "tools_disabled": False,
        }

    def _execute_tools(self, state: InvestigationAgentState) -> dict[str, Any]:
        response = state.get("latest_model_response") or {}
        calls = [ToolCall.model_validate(item) for item in response.get("tool_calls") or []]
        prior_calls = [
            ToolCall.model_validate(item) for item in state.get("all_tool_calls") or []
        ]
        used_ids = {item.id for item in prior_calls}
        if any(call.id in used_ids for call in calls):
            raise ToolProtocolError("tool_call_id was reused across model iterations")
        fingerprints = list(state.get("tool_call_fingerprints") or [])
        controlled_react = self._is_controlled_react_tool_request(state)
        if controlled_react:
            error = self._controlled_react_call_error(
                state=state,
                calls=calls,
                prior_fingerprints=fingerprints,
            )
            if error is not None:
                error_code, safe_message = error
                if error_code == "controlled_react_no_progress":
                    return {
                        "tool_calls": [
                            item.model_dump(mode="json") for item in calls
                        ],
                        "all_tool_calls": [
                            *(state.get("all_tool_calls") or []),
                            *(item.model_dump(mode="json") for item in calls),
                        ],
                        "evidence_source_control_status": "ready",
                        "evidence_react_enabled": False,
                        "tools_disabled": True,
                        "force_tool_choice": False,
                        "error": "",
                        "safe_message": "",
                        "retryable": False,
                        "stop_reason": "controlled_react_no_progress",
                    }
                return {
                    "tool_calls": [
                        item.model_dump(mode="json") for item in calls
                    ],
                    "all_tool_calls": [
                        *(state.get("all_tool_calls") or []),
                        *(item.model_dump(mode="json") for item in calls),
                    ],
                    "evidence_source_control_status": "error",
                    "evidence_react_enabled": False,
                    "tools_disabled": True,
                    "error": error_code,
                    "safe_message": safe_message,
                    "retryable": False,
                    "stop_reason": "controlled_react_rejected",
                }
        results: list[ToolResultEnvelope] = []
        captures: list[EvidenceCanonicalCapture | None] = []
        artifact_persisted: list[bool] = []
        batch_fingerprints: list[str] = []
        for call in calls:
            capture = None
            try:
                fingerprint = self.tool_service.query_fingerprint(call)
            except ValueError:
                fingerprint = self.report_facade.query_fingerprint(
                    call.name, call.arguments
                )
            batch_fingerprints.append(fingerprint)
            if fingerprint in fingerprints:
                result = self.tool_service.error_result(
                    call,
                    "repeated_tool_call",
                    "相同工具和参数已经执行过，请使用已有结果直接回答。",
                )
            elif membership_error := self._focused_call_membership_error(state, call):
                result = self.tool_service.error_result(
                    call,
                    "focused_scope_mismatch",
                    membership_error,
                )
            else:
                result, capture = self.tool_service.execute_with_canonical(
                    report_version_id=state["report_version_id"], call=call
                )
                if result.status == "ok" and (
                    result_error := self._focused_result_membership_error(
                        state, call, result
                    )
                ):
                    result = self.tool_service.error_result(
                        call,
                        "focused_scope_mismatch",
                        result_error,
                    )
                    capture = None
            results.append(result)
            captures.append(capture)
            artifact_persisted.append(False)
        model_payloads = [self._model_tool_payload(result) for result in results]
        receipts = [
            self.tool_service.query_receipt(
                session_id=state["session_id"],
                turn_id=state["turn_id"],
                report_version_id=state["report_version_id"],
                snapshot_hash=state["snapshot_hash"],
                call=call,
                result=result,
                model_payload=payload,
            )
            for call, result, payload in zip(calls, results, model_payloads)
        ]
        results = [
            result.model_copy(
                update={
                    "provenance": tuple(
                        source.model_copy(
                            update={
                                "tool_call_id": call.id,
                                "query_receipt_id": receipt.receipt_id,
                            }
                        )
                        for source in result.provenance
                    )
                }
            )
            for call, result, receipt in zip(calls, results, receipts)
        ]
        for index, (result, receipt, capture) in enumerate(
            zip(results, receipts, captures)
        ):
            if result.status != "ok" or capture is None:
                continue
            try:
                compiled = EvidenceArtifactCompiler.compile(
                    source_snapshot_id=state["source_snapshot_id"],
                    receipt=receipt,
                    capture=capture,
                )
                self.store.put_query_receipt_artifacts(
                    receipt,
                    compiled.query_result,
                    compiled.source_artifacts,
                    link=compiled.link,
                )
                artifact_persisted[index] = True
            except Exception:
                logger.exception(
                    "Evidence Artifact persistence failed for Receipt %s",
                    receipt.receipt_id,
                )
        assistant_message = assistant_tool_message(
            [item.model_dump(mode="json") for item in calls],
            str(response.get("content") or ""),
        )
        tool_messages = [
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
            for call, payload in zip(calls, model_payloads)
        ]
        working = [
            *(state.get("working_messages") or []),
            assistant_message,
            *tool_messages,
        ]
        validate_model_request_messages(
            [{"role": "system", "content": "protocol validation"}, *working]
        )
        pending = list(state.get("pending_ledger_entries") or [])
        warnings = list(state.get("source_warnings") or [])
        for result in results:
            pending.extend(item.model_dump(mode="json") for item in result.provenance)
            if result.error:
                warnings.append(result.error.error_code)
        context_update = self._referent_updates(
            calls, results, state.get("active_focus") or {}, state.get("ordered_referents") or []
        )
        focused_update = self._focused_updates_from_results(state, calls, results)
        update = {
            "working_messages": working,
            "tool_calls": [item.model_dump(mode="json") for item in calls],
            "all_tool_calls": [
                *(state.get("all_tool_calls") or []),
                *(item.model_dump(mode="json") for item in calls),
            ],
            "tool_results": [item.model_dump(mode="json") for item in results],
            "all_tool_results": [
                *(state.get("all_tool_results") or []),
                *(item.model_dump(mode="json") for item in results),
            ],
            "query_receipts": [
                *(state.get("query_receipts") or []),
                *(item.model_dump(mode="json") for item in receipts),
            ],
            "tool_call_fingerprints": [*fingerprints, *batch_fingerprints],
            "tool_result_fingerprints": [
                *(state.get("tool_result_fingerprints") or []),
                *(item.result_fingerprint for item in results),
            ],
            "pending_ledger_entries": pending,
            "source_warnings": list(dict.fromkeys(warnings)),
            "force_tool_choice": False,
            "tool_iteration_count": int(state.get("tool_iteration_count") or 0) + 1,
            **context_update,
            **focused_update,
        }
        if controlled_react:
            if any(
                result.status != "ok" or not persisted
                for result, persisted in zip(results, artifact_persisted)
            ):
                return {
                    **update,
                    "evidence_source_control_status": "error",
                    "evidence_react_enabled": False,
                    "tools_disabled": True,
                    "error": "controlled_react_acquisition_failure",
                    "safe_message": "受控 Evidence 补充查询失败，无法安全继续回答。",
                    "retryable": False,
                    "stop_reason": "controlled_react_acquisition_failure",
                }
            supplemental = list(
                state.get("evidence_react_supplemental_requirements") or []
            )
            for call in calls:
                supplemental.append(
                    BoundEvidenceDetailRequirement(
                        requirement_kind="evidence_detail",
                        session_id=state["session_id"],
                        report_version_id=state["report_version_id"],
                        source_snapshot_id=state["source_snapshot_id"],
                        snapshot_hash=state["snapshot_hash"],
                        evidence_ref=str(call.arguments["evidence_id"]),
                    ).model_dump(mode="json")
                )
            return {
                **update,
                "evidence_source_control_status": "react_acquired",
                "evidence_react_enabled": False,
                "evidence_react_supplemental_requirements": supplemental,
                "evidence_react_iteration_count": len(supplemental),
                "tools_disabled": True,
                "force_tool_choice": False,
            }
        return update

    def _model_tool_definitions(
        self, state: InvestigationAgentState
    ) -> list[dict[str, Any]]:
        if state.get("tools_disabled"):
            return []
        if self._is_controlled_react_tool_request(state):
            return [
                item
                for item in self.tool_service.definitions()
                if item.get("function", {}).get("name")
                == "read_evidence_detail"
            ]
        return self.tool_service.definitions()

    @staticmethod
    def _controlled_react_allowed_refs(
        bundle: ReadySourceBundle,
        supplemental_requirements: tuple[BoundEvidenceDetailRequirement, ...],
    ) -> tuple[str, ...]:
        if not isinstance(
            bundle.bound_requirement, BoundEvidenceCollectionRequirement
        ):
            return ()
        expanded = {item.evidence_ref for item in supplemental_requirements}
        return tuple(
            ref for ref in bundle.ordered_projected_refs if ref not in expanded
        )

    @staticmethod
    def _is_controlled_react_tool_request(
        state: InvestigationAgentState,
    ) -> bool:
        return bool(
            state.get("evidence_source_control_active")
            and state.get("evidence_source_control_status") == "ready"
            and state.get("ready_source_bundle")
            and state.get("evidence_react_enabled")
        )

    def _controlled_react_call_error(
        self,
        *,
        state: InvestigationAgentState,
        calls: list[ToolCall],
        prior_fingerprints: list[str],
    ) -> tuple[str, str] | None:
        if len(calls) != 1:
            return (
                "controlled_react_batch_not_allowed",
                "受控 Evidence 调查每轮只允许一个补充查询。",
            )
        if int(state.get("evidence_react_iteration_count") or 0) >= (
            self.max_evidence_react_iterations
        ) or int(state.get("tool_iteration_count") or 0) >= self.max_tool_iterations:
            return (
                "controlled_react_budget_exhausted",
                "受控 Evidence 调查已达到本轮最大补充次数。",
            )
        call = calls[0]
        if call.name != "read_evidence_detail":
            return (
                "controlled_react_tool_not_allowed",
                "当前 Evidence Bundle 只允许补充读取其中一条 Evidence 详情。",
            )
        try:
            arguments = self.tool_service.canonical_arguments(call)
            fingerprint = self.tool_service.query_fingerprint(call)
        except ValueError:
            return (
                "controlled_react_arguments_invalid",
                "受控 Evidence 补充查询参数无效。",
            )
        evidence_ref = str(arguments.get("evidence_id") or "")
        if fingerprint in prior_fingerprints:
            return (
                "controlled_react_no_progress",
                "相同 Evidence 详情已经查询，已停止重复调查。",
            )
        if evidence_ref not in set(state.get("evidence_react_allowed_refs") or []):
            return (
                "controlled_react_scope_mismatch",
                "请求的 Evidence 不属于当前 ReadySourceBundle 的可扩展范围。",
            )
        return None

    def _model_tool_payload(self, result: ToolResultEnvelope) -> dict[str, Any]:
        return self.tool_service.model_payload(result)

    @staticmethod
    def _parse_case_selection(content: str) -> CaseSelection | None:
        match = re.fullmatch(
            r"\s*<case_selection>\s*(.*?)\s*</case_selection>\s*",
            str(content or ""),
            flags=re.DOTALL,
        )
        if match is None:
            return None
        payload = match.group(1).strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", payload, flags=re.DOTALL)
        if fenced:
            payload = fenced.group(1).strip()
        try:
            return CaseSelection.model_validate_json(payload)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _focused_authority_from_ledger(
        focused_case: FocusedCaseContext | None,
        ledger: list[Any],
    ) -> dict[str, list[Any]]:
        if focused_case is None:
            return {
                "facts": [],
                "validated_claim_refs": [],
                "validated_finding_refs": [],
                "validated_evidence_refs": [],
            }
        allowed_claims = set(focused_case.related_claim_refs)
        allowed_findings = set(focused_case.related_finding_refs)
        allowed_evidence = set(focused_case.related_evidence_refs)
        facts = []
        claims = []
        findings = []
        evidence = []
        for item in ledger:
            kind = item.source_kind.value
            if kind == "report_claim" and item.claim_id in allowed_claims:
                claims.append(item.claim_id)
            elif kind == "current_finding" and item.finding_id in allowed_findings:
                findings.append(item.finding_id)
            elif kind == "current_evidence" and item.evidence_id in allowed_evidence:
                evidence.append(item.evidence_id)
            elif (
                kind == "frozen_citation_excerpt"
                and item.evidence_id in allowed_evidence
            ):
                # Preserve the verified Claim-to-Evidence citation fact without
                # promoting its excerpt to current materialized Evidence.
                pass
            else:
                continue
            facts.append(
                {
                    "source_kind": kind,
                    "claim_id": item.claim_id,
                    "finding_id": item.finding_id,
                    "evidence_id": item.evidence_id,
                    "excerpt": item.excerpt,
                    "freshness": item.freshness,
                }
            )
        return {
            "facts": facts[-20:],
            "validated_claim_refs": list(dict.fromkeys(claims)),
            "validated_finding_refs": list(dict.fromkeys(findings)),
            "validated_evidence_refs": list(dict.fromkeys(evidence)),
        }

    @staticmethod
    def _reused_focused_sources(
        *,
        focused_case: FocusedCaseContext | None,
        case_catalog: list[dict[str, Any]],
        ledger: list[Any],
    ) -> list[Any]:
        if focused_case is None:
            if not case_catalog:
                return []
            return [
                item
                for item in ledger
                if item.source_kind.value == "report_text"
                and str(item.section_id).startswith("case-")
            ]
        allowed = {
            *focused_case.related_claim_refs,
            *focused_case.related_finding_refs,
            *focused_case.related_evidence_refs,
            *focused_case.related_metric_refs,
        }
        return [
            item
            for item in ledger
            if (
                item.source_kind.value == "report_text"
                and item.section_id
                in {
                    focused_case.case_section_id,
                    *(fact.source_block for fact in focused_case.validated_report_wide_facts),
                }
            )
            or bool(
                allowed.intersection(
                    {
                        item.claim_id,
                        item.finding_id,
                        item.evidence_id,
                        item.metric_key,
                    }
                )
            )
        ]

    @staticmethod
    def _focused_call_membership_error(
        state: InvestigationAgentState, call: ToolCall
    ) -> str:
        focused = state.get("focused_case") or {}
        if not focused:
            return ""
        claims = set(focused.get("related_claim_refs") or [])
        findings = set(focused.get("related_finding_refs") or [])
        evidence = set(focused.get("related_evidence_refs") or [])
        metrics = set(focused.get("related_metric_refs") or [])
        if call.name in {"read_report_presentation", "list_report_findings"}:
            return "当前问题已聚焦到一个案例，不能重新把整份报告或无关 Finding 作为同级事实池。"
        if call.name == "read_claim_support":
            return "" if str(call.arguments.get("claim_id") or "") in claims else (
                "该 Claim 不属于当前聚焦案例，已拒绝读取。"
            )
        if call.name in {"read_finding_detail", "list_finding_evidence"}:
            return "" if str(call.arguments.get("finding_id") or "") in findings else (
                "该 Finding 不属于当前聚焦案例，已拒绝读取。"
            )
        if call.name == "read_evidence_detail":
            return "" if str(call.arguments.get("evidence_id") or "") in evidence else (
                "该 Evidence 不属于当前聚焦案例的 Finding，已拒绝读取。"
            )
        if call.name == "lookup_report_metric":
            supplied = call.arguments.get("metric_keys") or [
                call.arguments.get("metric_key")
            ]
            return "" if supplied and set(supplied).issubset(metrics) else (
                "该 Metric 不属于当前聚焦案例的 Claim，已拒绝读取。"
            )
        return ""

    @staticmethod
    def _focused_result_membership_error(
        state: InvestigationAgentState,
        call: ToolCall,
        result: ToolResultEnvelope,
    ) -> str:
        focused = state.get("focused_case") or {}
        if not focused or not isinstance(result.data, dict):
            return ""
        data = result.data
        claims = set(focused.get("related_claim_refs") or [])
        findings = set(focused.get("related_finding_refs") or [])
        evidence = set(focused.get("related_evidence_refs") or [])
        if call.name == "read_claim_support":
            claim_id = str((data.get("claim") or {}).get("claim_id") or "")
            returned_findings = set(data.get("finding_ids") or [])
            previews = (data.get("evidence_overview") or {}).get(
                "representative_previews"
            ) or []
            returned_evidence = {
                str(item.get("evidence_id") or "") for item in previews
            }
            if (
                claim_id not in claims
                or not returned_findings.issubset(findings)
                or not returned_evidence.issubset(evidence)
            ):
                return "Claim 支持关系超出当前聚焦案例，已拒绝该结果。"
        elif call.name == "read_finding_detail":
            finding_id = str((data.get("finding") or {}).get("finding_id") or "")
            returned_evidence = set(
                (data.get("evidence_overview") or {}).get("representative_refs") or []
            )
            if finding_id not in findings or not returned_evidence.issubset(evidence):
                return "Finding 详情超出当前聚焦案例，已拒绝该结果。"
        elif call.name == "list_finding_evidence":
            returned_evidence = {
                str(item.get("evidence_id") or "") for item in data.get("items") or []
            }
            if not returned_evidence.issubset(evidence):
                return "Evidence 列表超出当前聚焦 Finding，已拒绝该结果。"
        elif call.name == "read_evidence_detail":
            if (
                str(data.get("finding_id") or "") not in findings
                or str(data.get("evidence_id") or "") not in evidence
            ):
                return "Evidence 详情超出当前聚焦 Finding，已拒绝该结果。"
        return ""

    def _focused_updates_from_results(
        self,
        state: InvestigationAgentState,
        calls: list[ToolCall],
        results: list[ToolResultEnvelope],
    ) -> dict[str, Any]:
        focused_data = dict(state.get("focused_case") or {})
        claims = list(state.get("focused_validated_claim_refs") or [])
        findings = list(state.get("focused_validated_finding_refs") or [])
        evidence = list(state.get("focused_validated_evidence_refs") or [])
        facts = list(state.get("focused_authoritative_facts") or [])
        selection_trace = dict(state.get("case_selection_trace") or {})
        selected_by_tool = False
        for call, result in zip(calls, results):
            if result.status != "ok" or not isinstance(result.data, dict):
                continue
            data = result.data
            if not focused_data and call.name == "read_claim_support":
                claim_id = str((data.get("claim") or {}).get("claim_id") or "")
                focused = self.report_facade.find_focused_case_context(
                    state["report_version_id"], claim_id
                )
                if focused:
                    focused_data = focused.model_dump(mode="json")
                    selected_by_tool = True
                    selection_trace = {
                        "stage": "existing_tool_call_selection",
                        "selected_case_ref": focused.case_ref,
                        "selected_case_title": focused.title,
                        "related_claim_refs": list(focused.related_claim_refs),
                        "related_finding_refs": list(focused.related_finding_refs),
                        "membership_validation": "passed",
                    }
            if call.name == "read_claim_support":
                claim_id = str((data.get("claim") or {}).get("claim_id") or "")
                if claim_id:
                    claims.append(claim_id)
            elif call.name == "read_finding_detail":
                finding_id = str((data.get("finding") or {}).get("finding_id") or "")
                if finding_id:
                    findings.append(finding_id)
            elif call.name in {"list_finding_evidence", "read_evidence_detail"}:
                evidence.extend(
                    str(item.get("evidence_id") or "")
                    for item in (
                        data.get("items") or [data]
                    )
                    if item.get("evidence_id")
                )
            for source in result.provenance:
                facts.append(
                    {
                        "source_kind": source.source_kind.value,
                        "claim_id": source.claim_id,
                        "finding_id": source.finding_id,
                        "evidence_id": source.evidence_id,
                        "excerpt": source.excerpt,
                        "freshness": source.freshness,
                    }
                )
        validated_claims = list(dict.fromkeys(claims))
        validated_findings = list(dict.fromkeys(findings))
        update = {
            "focused_case": focused_data,
            "focused_authoritative_facts": facts[-20:],
            "focused_validated_claim_refs": validated_claims,
            "focused_validated_finding_refs": validated_findings,
            "focused_validated_evidence_refs": list(dict.fromkeys(evidence)),
            "focused_authority_required": bool(
                focused_data and not (validated_claims or validated_findings)
            ),
        }
        if selected_by_tool:
            update.update(
                {
                    "case_selection_available": False,
                    "case_selection_completed": True,
                    "case_selection_trace": selection_trace,
                    "case_selection_membership_validated": True,
                }
            )
        return update

    def _assess_progress(self, state: InvestigationAgentState) -> dict[str, Any]:
        results = [
            ToolResultEnvelope.model_validate(item) for item in state.get("tool_results") or []
        ]
        batch_sources = sorted(
            stable_hash(
                item.model_dump(
                    mode="json", exclude={"tool_call_id", "query_receipt_id"}
                )
            )
            for result in results
            for item in result.provenance
        )
        signature = stable_hash(
            {
                "result_fingerprints": [item.result_fingerprint for item in results],
                "source_ids": batch_sources,
                "active_focus": state.get("active_focus") or {},
                "ordered_referents": state.get("ordered_referents") or [],
            }
        )
        previous = str(state.get("previous_progress_signature") or "")
        no_progress = (
            int(state.get("consecutive_no_progress") or 0) + 1
            if previous and previous == signature
            else 0
        )
        counts = Counter(state.get("tool_call_fingerprints") or [])
        stop_reason = str(state.get("stop_reason") or "")
        if any(value >= 2 for value in counts.values()):
            stop_reason = "repeated_tool_call"
        elif no_progress >= self.no_progress_limit:
            stop_reason = "no_progress"
        elif int(state.get("tool_iteration_count") or 0) >= self.max_tool_iterations:
            stop_reason = "max_tool_iterations"
        elif int(state.get("total_tokens") or 0) >= self.max_run_tokens:
            stop_reason = "token_budget"
        return {
            "previous_progress_signature": signature,
            "consecutive_no_progress": no_progress,
            "stop_reason": stop_reason,
        }

    def _prepare_final(self, state: InvestigationAgentState) -> dict[str, Any]:
        already_disabled = bool(state.get("tools_disabled"))
        current_reason = str(state.get("stop_reason") or "")
        is_empty_response_retry = already_disabled or not current_reason
        return {
            "tools_disabled": True,
            "final_response_retry_count": int(
                state.get("final_response_retry_count") or 0
            )
            + (1 if is_empty_response_retry else 0),
            "stop_reason": current_reason
            or ("empty_final_response" if is_empty_response_retry else "tool_loop_complete"),
        }

    def _prepare_grounding_retry(
        self, state: InvestigationAgentState
    ) -> dict[str, Any]:
        answer = str((state.get("latest_model_response") or {}).get("content") or "")
        issues = self._grounding_issues(state, answer)
        missing_source = any(
            item.issue_type == "missing_authoritative_source" for item in issues
        )
        scope_repair = (
            missing_source
            and int(state.get("scope_repair_count") or 0) < 1
        )
        source_repair = (
            missing_source
            and not scope_repair
            and int(state.get("scope_repair_count") or 0) >= 1
            and bool(state.get("scope_source_tools_allowed"))
            and int(state.get("source_repair_count") or 0) < 1
        )
        repair_mode = (
            "scope_repair"
            if scope_repair
            else "source_repair"
            if source_repair
            else "semantic_rewrite"
        )
        serialized_issues = [item.model_dump(mode="json") for item in issues]
        return {
            "answer": "",
            "grounding_draft": answer,
            "repair_working_start": (
                len(state.get("working_messages") or [])
                if source_repair
                else int(state.get("repair_working_start") or 0)
            ),
            "grounding_retry_count": int(state.get("grounding_retry_count") or 0) + 1,
            "scope_repair_count": int(state.get("scope_repair_count") or 0)
            + (1 if scope_repair else 0),
            "source_repair_count": int(state.get("source_repair_count") or 0)
            + (1 if source_repair else 0),
            "semantic_rewrite_count": int(state.get("semantic_rewrite_count") or 0)
            + (1 if repair_mode == "semantic_rewrite" else 0),
            "repair_mode": repair_mode,
            "scope_source_tools_allowed": (
                not bool(state.get("tools_disabled"))
                if scope_repair
                else bool(state.get("scope_source_tools_allowed"))
            ),
            "scope_initial_draft": (
                answer if scope_repair else str(state.get("scope_initial_draft") or "")
            ),
            "scope_initial_issues": (
                serialized_issues
                if scope_repair
                else list(state.get("scope_initial_issues") or [])
            ),
            "scope_repaired_draft": (
                answer
                if source_repair and state.get("repair_mode") == "scope_repair"
                else str(state.get("scope_repaired_draft") or "")
            ),
            "scope_remaining_issues": (
                serialized_issues
                if source_repair and state.get("repair_mode") == "scope_repair"
                else list(state.get("scope_remaining_issues") or [])
            ),
            "grounding_issues": serialized_issues,
            "numeric_validation_errors": [item.message for item in issues],
            "tools_disabled": not source_repair,
            "force_tool_choice": source_repair,
        }

    def _validate_grounding(self, state: InvestigationAgentState) -> dict[str, Any]:
        answer = self._sanitize_user_visible_answer(
            state, str(state.get("answer") or "").strip()
        )
        pending = [
            SourceLedgerCandidate.model_validate(item)
            for item in state.get("pending_ledger_entries") or []
        ]
        unique = {
            stable_hash(item.model_dump(mode="json")): item for item in pending
        }
        inherited = bool(state.get("inherit_last_sources"))
        if not answer:
            existing_error = str(state.get("error") or "")
            return {
                "safe_message": (
                    "模型在工具已禁用时仍请求了新工具，已安全终止本轮。"
                    if existing_error == "tool_call_while_disabled"
                    else "模型未能生成有效的最终回答。"
                ),
                "error": existing_error or "empty_final_response",
                "retryable": False,
                "grounding_validation": {
                    "status": "failed",
                    "source_count": len(unique),
                    "inherited_sources": inherited,
                    "warnings": [existing_error or "empty_final_response"],
                },
            }
        issues = self._grounding_issues(state, answer)
        if issues:
            messages = [item.message for item in issues]
            return {
                "safe_message": "最终回答中的数字或统计口径未通过来源感知 Grounding 校验。",
                "error": "numeric_grounding_failed",
                "retryable": False,
                "grounding_issues": [
                    item.model_dump(mode="json") for item in issues
                ],
                "numeric_validation_errors": messages,
                "grounding_validation": {
                    "status": "failed",
                    "source_count": len(unique),
                    "inherited_sources": inherited,
                    "warnings": messages,
                },
            }
        return {
            "answer": answer,
            "pending_ledger_entries": [
                item.model_dump(mode="json") for item in unique.values()
            ],
            "grounding_validation": {
                "status": "passed",
                "source_count": len(unique),
                "inherited_sources": inherited,
                "warnings": list(state.get("source_warnings") or []),
            },
        }

    def _sanitize_user_visible_answer(
        self, state: InvestigationAgentState, answer: str
    ) -> str:
        """Remove implementation IDs only at the persisted user-answer boundary."""
        output = str(answer or "")
        labels = self._bundle_evidence_display_labels(state)
        for stable_ref, label in sorted(
            labels.items(), key=lambda item: len(item[0]), reverse=True
        ):
            tokens = (stable_ref, stable_ref.rsplit(":", 1)[-1])
            for token in dict.fromkeys(tokens):
                output = re.sub(
                    rf"\s*[（(]\s*(?:\*\*)?{re.escape(token)}(?:\*\*)?\s*[）)]",
                    "",
                    output,
                )
                output = output.replace(token, label)

        generic_replacements = (
            (r"report-version:[A-Za-z0-9_.:-]+", "当前报告版本"),
            (r"report-claim:[A-Za-z0-9_.:-]+", "当前报告结论"),
            (r"report:[A-Za-z0-9_.:-]+", "当前报告"),
            (r"finding:[A-Za-z0-9_.:-]+", "当前案例"),
            (r"evidence:[A-Za-z0-9_.:-]+", "该证据"),
            (r"metric:[A-Za-z0-9_.:-]+", "该指标"),
        )
        for pattern, replacement in generic_replacements:
            output = re.sub(pattern, replacement, output)
        output = re.sub(
            r"\bev_[A-Za-z0-9_]+\b",
            "该证据",
            output,
        )
        return re.sub(r"\s*[（(]\s*[）)]", "", output).strip()

    def _bundle_evidence_display_labels(
        self, state: InvestigationAgentState
    ) -> dict[str, str]:
        raw_bundle = state.get("ready_source_bundle") or {}
        if not raw_bundle:
            return {}
        try:
            bundle = ReadySourceBundle.model_validate(raw_bundle)
        except (TypeError, ValueError):
            return {}
        source_types: dict[str, str] = {}
        for artifact_id in bundle.source_artifact_ids:
            source = self.store.get_source_artifact(artifact_id)
            if source is not None:
                source_types[source.stable_source_ref] = source.source_type
        ordered = [
            ref for ref in bundle.ordered_projected_refs if ref in source_types
        ]
        type_counts = Counter(source_types[ref] for ref in ordered)
        type_ordinals: Counter[str] = Counter()
        labels: dict[str, str] = {}
        for ref in ordered:
            source_type = source_types[ref]
            type_ordinals[source_type] += 1
            base = _EVIDENCE_DISPLAY_LABELS.get(source_type, "证据")
            labels[ref] = (
                f"{base} {type_ordinals[source_type]}"
                if type_counts[source_type] > 1
                else base
            )
        return labels

    def _persist_turn(self, state: InvestigationAgentState) -> dict[str, Any]:
        _, _, ledger = self.store.complete_turn(
            state["turn_id"],
            answer=state["answer"],
            trace_messages=list((state.get("working_messages") or [])[1:]),
            pending_sources=list(state.get("pending_ledger_entries") or []),
            grounding_validation=dict(state.get("grounding_validation") or {}),
            resolved_references=list(state.get("resolved_references") or []),
            all_tool_calls=list(state.get("all_tool_calls") or []),
            query_receipts=list(state.get("query_receipts") or []),
            summary_text=str(state.get("summary_text") or ""),
            active_focus=dict(state.get("active_focus") or {}),
            ordered_referents=list(state.get("ordered_referents") or []),
            last_claim_id=str(state.get("last_claim_id") or ""),
            last_finding_id=str(state.get("last_finding_id") or ""),
            last_evidence_id=str(state.get("last_evidence_id") or ""),
            input_tokens=int(state.get("input_tokens") or 0),
            output_tokens=int(state.get("output_tokens") or 0),
            total_tokens=int(state.get("total_tokens") or 0),
            llm_call_count=int(state.get("llm_call_count") or 0),
            stop_reason=str(state.get("stop_reason") or "completed"),
            context_accounting=list(state.get("context_accounting") or []),
            grounding_issues=list(state.get("grounding_issues") or []),
            grounding_repair_count=int(state.get("grounding_retry_count") or 0),
            scope_repair_count=int(state.get("scope_repair_count") or 0),
            source_repair_count=int(state.get("source_repair_count") or 0),
            semantic_rewrite_count=int(state.get("semantic_rewrite_count") or 0),
            scope_initial_draft=str(state.get("scope_initial_draft") or ""),
            scope_repaired_draft=str(state.get("scope_repaired_draft") or ""),
            scope_initial_issues=list(state.get("scope_initial_issues") or []),
            scope_remaining_issues=list(state.get("scope_remaining_issues") or []),
        )
        return {"citations": [item.model_dump(mode="json") for item in ledger]}

    def _persist_error(self, state: InvestigationAgentState) -> dict[str, Any]:
        self.store.fail_turn(
            state["turn_id"],
            error_code=str(state.get("error") or "agent_error"),
            safe_message=str(state.get("safe_message") or "调查对话未能完成。"),
            retryable=bool(state.get("retryable")),
            input_tokens=int(state.get("input_tokens") or 0),
            output_tokens=int(state.get("output_tokens") or 0),
            total_tokens=int(state.get("total_tokens") or 0),
            llm_call_count=int(state.get("llm_call_count") or 0),
            stop_reason=str(state.get("stop_reason") or "error"),
            trace_messages=list((state.get("working_messages") or [])[1:]),
            pending_sources=list(state.get("pending_ledger_entries") or []),
            grounding_validation=dict(state.get("grounding_validation") or {}),
            resolved_references=list(state.get("resolved_references") or []),
            all_tool_calls=list(state.get("all_tool_calls") or []),
            all_tool_results=list(state.get("all_tool_results") or []),
            query_receipts=list(state.get("query_receipts") or []),
            grounding_errors=list(state.get("numeric_validation_errors") or []),
            grounding_issues=list(state.get("grounding_issues") or []),
            context_accounting=list(state.get("context_accounting") or []),
            grounding_repair_count=int(state.get("grounding_retry_count") or 0),
            scope_repair_count=int(state.get("scope_repair_count") or 0),
            source_repair_count=int(state.get("source_repair_count") or 0),
            semantic_rewrite_count=int(state.get("semantic_rewrite_count") or 0),
            scope_initial_draft=str(state.get("scope_initial_draft") or ""),
            scope_repaired_draft=str(state.get("scope_repaired_draft") or ""),
            scope_initial_issues=list(state.get("scope_initial_issues") or []),
            scope_remaining_issues=list(state.get("scope_remaining_issues") or []),
        )
        return {}

    def _initial_state(
        self, session: InvestigationSession, turn_id: str, user_input: str
    ) -> InvestigationAgentState:
        return {
            "session_id": session.id,
            "turn_id": turn_id,
            "task_id": session.task_id,
            "report_id": session.report_id,
            "report_version_id": session.report_version_id,
            "source_snapshot_id": session.source_snapshot_id,
            "snapshot_hash": session.snapshot_hash,
            "summary_text": session.summary_text,
            "recent_messages": [],
            "working_messages": [],
            "user_input": user_input,
            "response_style": "normal",
            "active_focus": session.active_focus,
            "ordered_referents": [
                item.model_dump(mode="json") for item in session.ordered_referents
            ],
            "last_claim_id": session.last_claim_id,
            "last_finding_id": session.last_finding_id,
            "last_evidence_id": session.last_evidence_id,
            "last_answer_message_id": session.last_answer_message_id,
            "resolved_references": [],
            "case_catalog": [],
            "case_selection_available": False,
            "case_selection_completed": False,
            "case_selection_trace": {},
            "case_selection_membership_validated": False,
            "focused_case": {},
            "focused_authoritative_facts": [],
            "focused_validated_claim_refs": [],
            "focused_validated_finding_refs": [],
            "focused_validated_evidence_refs": [],
            "focused_authority_required": False,
            "conversation_continuity": [],
            "inherit_last_sources": False,
            "inherited_ledger_entries": [],
            "tool_calls": [],
            "all_tool_calls": [],
            "tool_results": [],
            "all_tool_results": [],
            "query_receipts": [],
            "pending_ledger_entries": [],
            "recent_ledger_refs": [],
            "source_warnings": [],
            "evidence_source_control_active": False,
            "evidence_source_control_status": "",
            "evidence_source_bound_requirement": {},
            "evidence_source_attempt_count": 0,
            "evidence_source_history": [],
            "ready_source_bundle": {},
            "evidence_react_enabled": False,
            "evidence_react_allowed_refs": [],
            "evidence_react_supplemental_requirements": [],
            "evidence_react_iteration_count": 0,
            "evidence_react_bundle_fingerprints": [],
            "answer": "",
            "citations": [],
            "grounding_validation": {},
            "tool_iteration_count": 0,
            "tool_call_fingerprints": [],
            "tool_result_fingerprints": [],
            "previous_progress_signature": "",
            "consecutive_no_progress": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "llm_call_count": 0,
            "context_accounting": [],
            "model_call_ids": [],
            "token_warning": False,
            "tools_disabled": False,
            "force_tool_choice": False,
            "stop_reason": "",
            "final_response_retry_count": 0,
            "grounding_retry_count": 0,
            "scope_repair_count": 0,
            "source_repair_count": 0,
            "semantic_rewrite_count": 0,
            "repair_mode": "",
            "scope_source_tools_allowed": False,
            "scope_initial_draft": "",
            "scope_repaired_draft": "",
            "scope_initial_issues": [],
            "scope_remaining_issues": [],
            "numeric_validation_errors": [],
            "grounding_issues": [],
            "grounding_draft": "",
            "repair_working_start": 0,
            "latest_model_response": {},
            "current_node": "",
            "error": "",
            "retryable": False,
            "safe_message": "",
        }

    def _numeric_grounding_errors(
        self, state: InvestigationAgentState, answer: str
    ) -> list[str]:
        return [item.message for item in self._grounding_issues(state, answer)]

    def _grounding_issues(
        self, state: InvestigationAgentState, answer: str
    ) -> list[GroundingIssue]:
        issues: list[GroundingIssue] = []
        if re.search(
            r"(?:report(?:-version|-claim)?:|finding:|evidence:|metric:)[A-Za-z0-9_.:-]+",
            answer,
        ) and "技术审计" not in str(state.get("user_input") or ""):
            issues.append(
                GroundingIssue(
                    issue_type="internal_id_leak",
                    unsupported_claim=self._sentence_for_span(answer, 0, len(answer)),
                    message="最终回答泄漏了内部稳定 ID，必须重写包含该 ID 的完整句子。",
                )
            )
        facts = self._structured_numeric_facts(state)
        loaded_metric_keys = {
            str(item.get("source_ref") or "")
            for item in facts
            if item.get("source_kind") == "frozen_metric"
        }
        for mention in self._numeric_mentions(answer):
            matching = [item for item in facts if self._fact_matches(mention, item)]
            if matching:
                continue
            candidate_keys = self._candidate_metric_keys(
                state, mention["value"], mention["unit"]
            )
            missing_keys = [
                item for item in candidate_keys if item not in loaded_metric_keys
            ]
            sentence = self._sentence_for_span(
                answer, int(mention["start"]), int(mention["end"])
            )
            if missing_keys:
                issues.append(
                    GroundingIssue(
                        issue_type="missing_authoritative_source",
                        unsupported_claim=sentence,
                        numeric_facts=(str(mention["text"]),),
                        candidate_metric_keys=tuple(missing_keys[:8]),
                        message=(
                            f"语义单元“{sentence}”中的 {mention['text']} 缺少权威来源；"
                            "按 candidate_metric_keys 批量补查后重写完整语义单元。"
                        ),
                    )
                )
                continue
            relevant = self._derived_component_facts(mention, facts, candidate_keys)
            if candidate_keys or relevant:
                issues.append(
                    GroundingIssue(
                        issue_type="unsupported_derived_statistic",
                        unsupported_claim=sentence,
                        numeric_facts=(str(mention["text"]),),
                        available_authoritative_facts=tuple(
                            str(item["description"]) for item in relevant[:20]
                        ),
                        source_refs=tuple(
                            dict.fromkeys(
                                str(item["source_ref"])
                                for item in relevant
                                if item.get("source_ref")
                            )
                        )[:40],
                        message=(
                            f"语义单元“{sentence}”包含没有独立 Metric 或 derivation contract 的"
                            f"派生统计 {mention['text']}；不得再次查询 Metric，必须用已有 component facts"
                            "重写完整句子。"
                        ),
                    )
                )
                continue
            issues.append(
                GroundingIssue(
                    issue_type="ambiguous_numeric_fact",
                    unsupported_claim=sentence,
                    numeric_facts=(str(mention["text"]),),
                    available_authoritative_facts=tuple(
                        str(item["description"]) for item in facts[-20:]
                    ),
                    source_refs=tuple(
                        dict.fromkeys(
                            str(item["source_ref"])
                            for item in facts
                            if item.get("source_ref")
                        )
                    )[-40:],
                    message=(
                        f"无法从已返回的结构化 Metric、Finding 或 Evidence 确定语义单元"
                        f"“{sentence}”中的 {mention['text']}；不得猜测来源或继续盲查 Metric，"
                        "必须重写该完整语义单元。"
                    ),
                )
            )
        unique: dict[str, GroundingIssue] = {}
        for issue in issues:
            key = stable_hash(issue.model_dump(mode="json"))
            unique.setdefault(key, issue)
        return list(unique.values())

    @staticmethod
    def _numeric_mentions(text: str) -> list[dict[str, Any]]:
        pattern = re.compile(
            r"(?<![A-Za-z0-9])(?P<value>[0-9]+(?:\.[0-9]+)?)\s*"
            r"(?P<unit>%|分钟|小时|条|个|项|分|秒|次|元|年|月|天)"
        )
        return [
            {
                "value": float(match.group("value")),
                "unit": match.group("unit"),
                "text": match.group(0).strip(),
                "start": match.start(),
                "end": match.end(),
            }
            for match in pattern.finditer(text)
        ]

    @staticmethod
    def _sentence_for_span(text: str, start: int, end: int) -> str:
        left = max(text.rfind(mark, 0, start) for mark in ("。", "！", "？", "\n"))
        right_candidates = [
            position
            for mark in ("。", "！", "？", "\n")
            if (position := text.find(mark, end)) >= 0
        ]
        right = min(right_candidates) + 1 if right_candidates else len(text)
        return text[left + 1 : right].strip()[:2000]

    def _structured_numeric_facts(
        self, state: InvestigationAgentState
    ) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        calls = list(state.get("all_tool_calls") or [])
        results = list(state.get("all_tool_results") or [])

        def add(
            value: Any,
            units: tuple[str, ...],
            source_kind: str,
            source_ref: str,
            description: str,
            role: str,
        ) -> None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return
            facts.append(
                {
                    "value": number,
                    "units": units,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "description": description,
                    "role": role,
                }
            )

        for index, result in enumerate(results):
            if not isinstance(result, dict) or result.get("status") != "ok":
                continue
            call = calls[index] if index < len(calls) else {}
            name = str(call.get("name") or "")
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            provenance = result.get("provenance") or []
            source_ref = ""
            if provenance:
                first = provenance[0]
                source_ref = str(
                    first.get("metric_key")
                    or first.get("evidence_id")
                    or first.get("finding_id")
                    or first.get("claim_id")
                    or ""
                )
            if name == "lookup_report_metric":
                metrics = data.get("metrics") or (
                    [data] if data.get("metric_key") else []
                )
                for metric in metrics:
                    ref = str(metric.get("metric_key") or "")
                    label = str(metric.get("label") or ref)
                    if metric.get("percentage") is not None:
                        add(
                            metric.get("percentage"),
                            ("%",),
                            "frozen_metric",
                            ref,
                            f"{label} percentage={metric.get('percentage')}",
                            "metric_percentage",
                        )
                    if str(metric.get("metric_name") or "") != "percentage":
                        add(
                            metric.get("value"),
                            ("条", "个", "项", "次"),
                            "frozen_metric",
                            ref,
                            f"{label} value={metric.get('value')}",
                            "metric_value",
                        )
                    add(
                        metric.get("denominator"),
                        ("条", "个", "项"),
                        "frozen_metric",
                        ref,
                        f"{label} denominator={metric.get('denominator')}",
                        "metric_denominator",
                    )
            elif name == "read_finding_detail":
                finding = data.get("finding") or {}
                ref = str(finding.get("finding_id") or source_ref)
                add(
                    finding.get("risk_score"),
                    ("分",),
                    "current_finding",
                    ref,
                    f"risk_score={finding.get('risk_score')}",
                    "finding_risk_score",
                )
                rule_count = len(finding.get("matched_rule_ids") or [])
                add(
                    rule_count,
                    ("条", "个", "项", "次"),
                    "current_finding",
                    ref,
                    f"matched_rule_count={rule_count}",
                    "finding_rule_count",
                )
                overview = data.get("evidence_overview") or {}
                add(
                    overview.get("total"),
                    ("条", "个", "项"),
                    "current_finding",
                    ref,
                    f"evidence_count={overview.get('total')}",
                    "finding_evidence_count",
                )
            elif name in {"list_finding_evidence", "read_evidence_detail"}:
                ref = str(data.get("evidence_id") or source_ref)
                if name == "list_finding_evidence":
                    add(
                        data.get("total"),
                        ("条", "个", "项"),
                        "current_evidence",
                        ref,
                        f"returned evidence total={data.get('total')}",
                        "evidence_count",
                    )
                else:
                    add(
                        data.get("timestamp_start"),
                        ("秒", "分钟", "小时"),
                        "current_evidence",
                        ref,
                        f"timestamp_start={data.get('timestamp_start')}",
                        "evidence_timestamp",
                    )
                    add(
                        data.get("timestamp_end"),
                        ("秒", "分钟", "小时"),
                        "current_evidence",
                        ref,
                        f"timestamp_end={data.get('timestamp_end')}",
                        "evidence_timestamp",
                    )
                    for field in ("original_text", "translated_text", "summary"):
                        for mention in self._numeric_mentions(str(data.get(field) or "")):
                            add(
                                mention["value"],
                                (str(mention["unit"]),),
                                "current_evidence",
                                ref,
                                f"{field} contains {mention['text']}",
                                "evidence_text",
                            )
        return facts

    @staticmethod
    def _fact_matches(mention: dict[str, Any], fact: dict[str, Any]) -> bool:
        tolerance = 0.11 if mention["unit"] == "%" else 0.001
        return (
            mention["unit"] in fact.get("units", ())
            and abs(float(mention["value"]) - float(fact["value"])) <= tolerance
        )

    def _candidate_metric_keys(
        self, state: InvestigationAgentState, value: float, unit: str
    ) -> list[str]:
        keys: list[str] = []

        def block_matches(block: dict[str, Any]) -> bool:
            local = " ".join(
                str(block.get(field) or "")
                for field in ("text", "value", "detail")
            )
            return any(
                abs(float(item["value"]) - value)
                <= (0.11 if unit == "%" else 0.001)
                and item["unit"] == unit
                for item in self._numeric_mentions(local)
            )

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                if block_matches(item):
                    keys.extend(str(ref) for ref in item.get("metric_refs") or [])
                for child in item.values():
                    if isinstance(child, (dict, list)):
                        visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        calls = list(state.get("all_tool_calls") or [])
        results = list(state.get("all_tool_results") or [])
        for index, result in enumerate(results):
            call = calls[index] if index < len(calls) else {}
            if call.get("name") == "read_report_presentation" and isinstance(result, dict):
                visit(result.get("data") or {})
        return list(dict.fromkeys(keys))[:8]

    @staticmethod
    def _derived_component_facts(
        mention: dict[str, Any],
        facts: list[dict[str, Any]],
        candidate_keys: list[str],
    ) -> list[dict[str, Any]]:
        metric_facts = [
            item
            for item in facts
            if item.get("source_kind") == "frozen_metric"
            and item.get("role") in {"metric_value", "metric_percentage"}
            and mention["unit"] in item.get("units", ())
        ]
        if candidate_keys:
            selected = [
                item for item in metric_facts if item.get("source_ref") in candidate_keys
            ]
            if selected:
                return selected
        tolerance = 0.11 if mention["unit"] == "%" else 0.001
        for index, left in enumerate(metric_facts):
            for right in metric_facts[index + 1 :]:
                if abs(
                    float(left["value"])
                    + float(right["value"])
                    - float(mention["value"])
                ) <= tolerance:
                    return [left, right]
        return []

    def _validate_business_scope(self, session: InvestigationSession) -> None:
        if session.scope_type != "report":
            raise CheckpointScopeMismatchError(
                "business session is not bound to a published report"
            )
        current = self.report_facade.get_published_report_context(
            session.report_version_id
        )
        if (
            current.task_id != session.task_id
            or current.report_id != session.report_id
            or current.report_version_id != session.report_version_id
            or current.source_snapshot_id != session.source_snapshot_id
            or current.snapshot_hash != session.snapshot_hash
        ):
            raise CheckpointScopeMismatchError("business session report lock changed")

    @staticmethod
    def _assert_state_scope(
        state: InvestigationAgentState, session: InvestigationSession
    ) -> None:
        if (
            state.get("session_id") != session.id
            or state.get("report_version_id") != session.report_version_id
            or state.get("snapshot_hash") != session.snapshot_hash
        ):
            raise CheckpointScopeMismatchError("agent state scope does not match session")

    def _validate_checkpoint_scope(
        self,
        session: InvestigationSession,
        config: dict[str, Any],
        *,
        required: bool,
        expected_turn_id: str = "",
    ) -> None:
        # LangGraph root graphs persist in the root namespace even when a logical
        # namespace is supplied to invoke; get_state treats non-root namespaces as
        # subgraph selectors. The dedicated DB remains the physical phase boundary.
        lookup_config = {
            "configurable": {
                "thread_id": config["configurable"]["thread_id"],
            }
        }
        snapshot = self.app.get_state(lookup_config)
        values = snapshot.values if snapshot is not None else {}
        if not values:
            if required:
                raise CheckpointScopeMismatchError("checkpoint is missing")
            return
        if (
            values.get("session_id") != session.id
            or values.get("report_version_id") != session.report_version_id
            or values.get("snapshot_hash") != session.snapshot_hash
        ):
            raise CheckpointScopeMismatchError("checkpoint scope does not match session")
        if expected_turn_id and values.get("turn_id") != expected_turn_id:
            raise CheckpointScopeMismatchError("checkpoint belongs to another turn")

    def _handle_execution_error(
        self, turn_id: str, exc: Exception
    ) -> TurnResult | None:
        turn = self.store.get_turn(turn_id)
        if turn.status in {"completed", "error"}:
            return self.store.turn_result(turn.id)
        if isinstance(exc, QwenChatError) and exc.retryable:
            self.store.mark_interrupted(
                turn_id,
                error_code=exc.code,
                safe_message=exc.safe_message,
                retryable=True,
            )
            return None
        if isinstance(exc, InvestigationError):
            audit = self._checkpoint_state(turn.session_id)
            self.store.fail_turn(
                turn_id,
                error_code=exc.code,
                safe_message=exc.safe_message,
                retryable=exc.retryable,
                input_tokens=int(audit.get("input_tokens") or 0),
                output_tokens=int(audit.get("output_tokens") or 0),
                total_tokens=int(audit.get("total_tokens") or 0),
                llm_call_count=int(audit.get("llm_call_count") or 0),
                stop_reason=str(getattr(exc, "stop_reason", "error")),
                trace_messages=list((audit.get("working_messages") or [])[1:]),
                pending_sources=list(audit.get("pending_ledger_entries") or []),
                grounding_validation=dict(audit.get("grounding_validation") or {}),
                resolved_references=list(audit.get("resolved_references") or []),
                all_tool_calls=list(audit.get("all_tool_calls") or []),
                all_tool_results=list(audit.get("all_tool_results") or []),
                query_receipts=list(audit.get("query_receipts") or []),
                grounding_errors=list(audit.get("numeric_validation_errors") or []),
                grounding_issues=list(audit.get("grounding_issues") or []),
                context_accounting=list(audit.get("context_accounting") or []),
                grounding_repair_count=int(audit.get("grounding_retry_count") or 0),
                scope_repair_count=int(audit.get("scope_repair_count") or 0),
                source_repair_count=int(audit.get("source_repair_count") or 0),
                semantic_rewrite_count=int(audit.get("semantic_rewrite_count") or 0),
                scope_initial_draft=str(audit.get("scope_initial_draft") or ""),
                scope_repaired_draft=str(audit.get("scope_repaired_draft") or ""),
                scope_initial_issues=list(audit.get("scope_initial_issues") or []),
                scope_remaining_issues=list(audit.get("scope_remaining_issues") or []),
            )
            return self.store.turn_result(turn_id)
        self.store.mark_interrupted(
            turn_id,
            error_code="execution_interrupted",
            safe_message="调查执行被中断，可以从检查点恢复。",
            retryable=True,
        )
        return None

    def _checkpoint_state(self, session_id: str) -> dict[str, Any]:
        snapshot = self.app.get_state(
            {"configurable": {"thread_id": session_id}}
        )
        values = snapshot.values if snapshot is not None else {}
        return dict(values or {})

    def _checkpoint_usage(self, session_id: str) -> dict[str, int]:
        values = self._checkpoint_state(session_id)
        return {
            key: int(values.get(key) or 0)
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "llm_call_count",
            )
        }

    @staticmethod
    def _candidate_from_ledger(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            key: entry[key]
            for key in (
                "source_kind",
                "metric_key",
                "section_id",
                "claim_id",
                "finding_id",
                "evidence_id",
                "source_hash",
                "excerpt",
                "asset_status",
                "query_fingerprint",
                "tool_call_id",
                "query_receipt_id",
                "warnings",
                "freshness",
            )
        }

    @staticmethod
    def _referent_updates(
        calls: list[ToolCall],
        results: list[ToolResultEnvelope],
        current_focus: dict[str, Any],
        current_referents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        active = dict(current_focus)
        ordered = list(current_referents)
        last_claim = ""
        last_finding = ""
        last_evidence = ""
        for call, result in zip(calls, results):
            if result.status != "ok" or not isinstance(result.data, dict):
                continue
            data = result.data
            next_referents: list[dict[str, Any]] = []
            if call.name == "read_report_presentation":
                for index, item in enumerate(data.get("case_blocks") or [], start=1):
                    claim_ids = item.get("claim_ids") or []
                    if not claim_ids:
                        continue
                    next_referents.append(
                        {
                            "type": "case",
                            "target_id": str(claim_ids[0]),
                            "label": str(item.get("title") or f"案例{index}")[:300],
                            "source_message_id": "",
                            "list_position": index,
                            "ledger_ids": [],
                        }
                    )
            elif call.name == "list_report_findings":
                for index, item in enumerate(data.get("items") or [], start=1):
                    next_referents.append(
                        {
                            "type": "finding",
                            "target_id": str(item["finding_id"]),
                            "label": str(
                                item.get("label")
                                or item.get("short_summary")
                                or f"案例{index}"
                            )[:300],
                            "source_message_id": "",
                            "list_position": index,
                            "ledger_ids": [],
                        }
                    )
            elif call.name == "list_finding_evidence":
                for index, item in enumerate(data.get("items") or [], start=1):
                    next_referents.append(
                        {
                            "type": "evidence",
                            "target_id": str(item["evidence_id"]),
                            "label": str(
                                item.get("summary")
                                or item.get("label")
                                or item.get("preview")
                                or item.get("original_text_preview")
                                or f"证据{index}"
                            )[:300],
                            "source_message_id": "",
                            "list_position": index,
                            "ledger_ids": [],
                        }
                    )
            elif call.name == "read_claim_support":
                claim = data.get("claim") or {}
                if claim.get("claim_id"):
                    last_claim = str(claim["claim_id"])
                    active = {
                        "type": "claim",
                        "target_id": last_claim,
                        "label": str(claim.get("text") or "当前结论")[:300],
                    }
                for index, finding_id in enumerate(data.get("finding_ids") or [], start=1):
                    next_referents.append(
                        {
                            "type": "finding",
                            "target_id": str(finding_id),
                            "label": f"结论支持案例 {index}",
                            "source_message_id": "",
                            "list_position": index,
                            "ledger_ids": [],
                        }
                    )
            elif call.name == "read_finding_detail":
                finding = data.get("finding") or {}
                if finding.get("finding_id"):
                    last_finding = str(finding["finding_id"])
                    active = {
                        "type": "finding",
                        "target_id": last_finding,
                        "label": str(finding.get("summary") or "当前案例")[:300],
                    }
            elif call.name == "read_evidence_detail":
                if data.get("evidence_id"):
                    last_evidence = str(data["evidence_id"])
                    last_finding = str(data.get("finding_id") or "")
                    active = {
                        "type": "evidence",
                        "target_id": last_evidence,
                        "label": str(
                            data.get("summary")
                            or data.get("original_text")
                            or "当前证据"
                        )[:300],
                    }
            if next_referents:
                family = InvestigationAgentService._referent_family(
                    next_referents[0]["type"]
                )
                ordered = [
                    *next_referents,
                    *(
                        item
                        for item in ordered
                        if InvestigationAgentService._referent_family(
                            str(item.get("type") or "")
                        )
                        != family
                    ),
                ]
                active = {
                    "type": next_referents[0]["type"],
                    "target_id": next_referents[0]["target_id"],
                    "label": next_referents[0]["label"],
                }
                if active["type"] == "finding":
                    last_finding = active["target_id"]
                elif active["type"] == "evidence":
                    last_evidence = active["target_id"]
                elif active["type"] in {"claim", "case"}:
                    last_claim = active["target_id"]
        return {
            "active_focus": active,
            "ordered_referents": ordered,
            "last_claim_id": last_claim,
            "last_finding_id": last_finding,
            "last_evidence_id": last_evidence,
        }

    @staticmethod
    def _referent_family(referent_type: str) -> str:
        if referent_type in {"case", "finding", "claim"}:
            return "case"
        return referent_type

    def _graph_config(self, session_id: str) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": session_id,
                "checkpoint_ns": "phase3a",
            },
            "recursion_limit": self.recursion_limit,
        }
