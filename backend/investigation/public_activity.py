from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
from threading import RLock
from typing import Any, Callable, Iterator

from backend.investigation.store import InvestigationStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublicActivitySpec:
    label: str
    succeeded_summary: str
    failed_summary: str = "该步骤未完成，系统会按原有流程继续处理或给出安全提示。"


PUBLIC_TOOL_ACTIVITIES: dict[str, PublicActivitySpec] = {
    # Published report A/B/C and unified-report tools.
    "read_report": PublicActivitySpec("读取报告概览", "已读取报告概览。"),
    "list_case_members": PublicActivitySpec("读取报告案例帖子", "已读取报告案例帖子。"),
    "list_category_posts": PublicActivitySpec("读取报告分类帖子", "已读取报告分类帖子。"),
    "list_finding_posts": PublicActivitySpec("读取报告风险帖子", "已读取报告风险帖子。"),
    "list_task_posts": PublicActivitySpec("读取任务帖子", "已读取任务帖子。"),
    "search_posts": PublicActivitySpec("搜索报告帖子", "已完成报告帖子搜索。"),
    "read_posts": PublicActivitySpec("读取帖子详情", "已读取帖子详情。"),
    "list_post_risk_comments": PublicActivitySpec("查看帖子风险评论", "已读取帖子风险评论。"),
    "list_post_comments": PublicActivitySpec("查看帖子评论", "已读取帖子评论。"),
    "list_evidence": PublicActivitySpec("查看证据目录", "已读取证据目录。"),
    "read_evidence": PublicActivitySpec("读取证据内容", "已读取证据内容。"),
    "search_accounts": PublicActivitySpec("查找报告中的账号", "已完成报告账号查找。"),
    "get_account_overview": PublicActivitySpec("读取账号概览", "已读取账号概览。"),
    "list_account_occurrences": PublicActivitySpec("查询账号活动记录", "已读取账号活动记录。"),
    "read_account_occurrence": PublicActivitySpec("读取账号活动详情", "已读取账号活动详情。"),
    "read_account_post": PublicActivitySpec("读取账号关联帖子", "已读取账号关联帖子。"),
    "compare_authorized_report_accounts": PublicActivitySpec(
        "查询账号跨报告活动", "已完成授权报告间的账号活动查询。"
    ),
    # Investigation creation tools.
    "query_investigation_options": PublicActivitySpec(
        "查询可用平台与审核资源", "已读取可用平台与审核资源。"
    ),
    "create_ruleset_proposal": PublicActivitySpec(
        "生成审核规则草案", "审核规则草案已生成，尚未正式保存。"
    ),
    "update_ruleset_proposal": PublicActivitySpec(
        "修改审核规则草案", "审核规则草案已修改，尚未正式保存。"
    ),
    "get_ruleset_proposal": PublicActivitySpec(
        "读取审核规则草案", "已读取审核规则草案。"
    ),
    "use_ruleset_proposal": PublicActivitySpec(
        "采用审核规则草案", "审核规则草案已用于任务草案，任务尚未启动。"
    ),
    "create_investigation_draft": PublicActivitySpec(
        "创建任务配置草案", "任务配置草案已创建，任务尚未启动。"
    ),
    "update_investigation_draft": PublicActivitySpec(
        "修改任务配置草案", "任务配置草案已修改，任务尚未启动。"
    ),
    "get_investigation_draft": PublicActivitySpec(
        "读取任务配置草案", "已读取任务配置草案。"
    ),
    "confirm_and_queue_investigation": PublicActivitySpec(
        "提交调查任务", "调查任务已确认并提交。"
    ),
    "get_investigation_run": PublicActivitySpec(
        "查询调查任务状态", "已读取调查任务状态。"
    ),
    # Resource-management tools.
    "read_resource": PublicActivitySpec(
        "查询正式审核资源", "已读取正式审核资源。"
    ),
    "create_lexicon_edit": PublicActivitySpec(
        "生成黑话库编辑稿", "黑话库编辑稿已生成，尚未正式保存。"
    ),
    "open_resource_edit": PublicActivitySpec(
        "建立审核资源编辑副本", "审核资源编辑副本已建立，正式版本未改变。"
    ),
    "get_resource_edit": PublicActivitySpec(
        "读取审核资源编辑稿", "已读取审核资源编辑稿。"
    ),
    "update_resource_edit": PublicActivitySpec(
        "修改审核资源编辑稿", "审核资源编辑稿已修改，尚未正式保存。"
    ),
    "save_resource": PublicActivitySpec(
        "正式保存审核资源", "审核资源已正式保存。"
    ),
    "get_resource_save": PublicActivitySpec(
        "查询审核资源保存结果", "已读取审核资源保存结果。"
    ),
}


def public_activity_id(turn_id: str, tool_call_id: str, tool_name: str) -> str:
    digest = _digest("activity", turn_id, tool_call_id, tool_name)
    return f"public-activity:{digest[:32]}"


def public_activity_event(
    *,
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    phase: str,
    result: Any = None,
    arguments: Any = None,
) -> tuple[dict[str, Any], str] | None:
    spec = PUBLIC_TOOL_ACTIVITIES.get(str(tool_name or ""))
    if spec is None or not str(turn_id or "") or not str(tool_call_id or ""):
        return None
    activity_id = public_activity_id(turn_id, tool_call_id, tool_name)
    if phase == "started":
        status = "running"
        summary = "正在执行该步骤。"
    elif phase == "completed":
        succeeded = _tool_result_succeeded(result)
        status = "succeeded" if succeeded else "failed"
        summary = spec.succeeded_summary if succeeded else spec.failed_summary
    elif phase == "interrupted":
        status = "interrupted"
        summary = "该步骤因本轮执行中断而停止。"
    else:
        raise ValueError("invalid public activity phase")
    label = spec.label
    succeeded_summary = spec.succeeded_summary
    if tool_name == "query_investigation_options":
        query_arguments = arguments if isinstance(arguments, dict) else {}
        reads_lexicon_terms = bool(
            query_arguments.get("include_lexicon_terms_for_ids")
        )
        reads_ruleset_details = bool(
            query_arguments.get("include_ruleset_details_for_revision_ids")
        )
        if reads_lexicon_terms and reads_ruleset_details:
            label = "读取所选审核资源详情"
            succeeded_summary = "已读取所选审核资源详情。"
        elif reads_lexicon_terms:
            label = "读取所选黑话库词条"
            succeeded_summary = "已读取所选黑话库词条。"
        elif reads_ruleset_details:
            label = "读取所选审核规则详情"
            succeeded_summary = "已读取所选审核规则详情。"
        else:
            label = "查询可用平台与审核资源"
            succeeded_summary = "已读取可用平台与审核资源。"
    if phase == "completed" and status == "succeeded":
        summary = succeeded_summary
    payload = {
        "activity_id": activity_id,
        "status": status,
        "label": label,
        "summary": summary,
        "result_count": None,
    }
    idempotency_key = "public-event:" + _digest(
        "activity-event", turn_id, tool_call_id, tool_name, phase
    )[:32]
    return payload, idempotency_key


class PublicActivityEmitter:
    """Fail-open projection of Hermes callbacks into the durable public stream."""

    def __init__(
        self,
        store: InvestigationStore,
        *,
        enabled: Callable[[], bool],
    ) -> None:
        self.store = store
        self.enabled = enabled
        self._lock = RLock()
        self._turns_by_session: dict[str, list[str]] = {}
        self._active_calls: dict[str, dict[str, tuple[str, Any]]] = {}

    def agent_callbacks(self, session_id: str) -> dict[str, Callable[..., None]]:
        if not self.enabled():
            return {}
        return {
            "tool_start_callback": (
                lambda tool_call_id, tool_name, arguments: self.tool_started(
                    session_id, tool_call_id, tool_name, arguments
                )
            ),
            "tool_complete_callback": (
                lambda tool_call_id, tool_name, arguments, result: self.tool_completed(
                    session_id, tool_call_id, tool_name, result, arguments
                )
            ),
        }

    @contextmanager
    def bind_turn(self, session_id: str, turn_id: str) -> Iterator[None]:
        if not self.enabled():
            yield
            return
        with self._lock:
            bound_turns = self._turns_by_session.setdefault(session_id, [])
            bound_turns.append(turn_id)
            if len(bound_turns) > 1:
                logger.warning(
                    "Suppressing ambiguous public activity for concurrent Turns in "
                    "Session %s",
                    session_id,
                )
        try:
            yield
        finally:
            self._interrupt_active_calls(turn_id)
            with self._lock:
                bound_turns = self._turns_by_session.get(session_id, [])
                if turn_id in bound_turns:
                    bound_turns.remove(turn_id)
                if not bound_turns:
                    self._turns_by_session.pop(session_id, None)

    def tool_started(
        self,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any = None,
    ) -> None:
        turn_id = self._active_turn(session_id)
        if not turn_id or tool_name not in PUBLIC_TOOL_ACTIVITIES:
            return
        projected = public_activity_event(
            turn_id=turn_id,
            tool_call_id=str(tool_call_id or ""),
            tool_name=tool_name,
            phase="started",
            arguments=arguments,
        )
        if projected is None or not self._append(turn_id, projected):
            return
        with self._lock:
            calls = self._active_calls.setdefault(turn_id, {})
            normalized_call_id = str(tool_call_id or "")
            previous = calls.get(normalized_call_id)
            calls[normalized_call_id] = (
                tool_name,
                arguments if arguments is not None else previous[1] if previous else None,
            )

    def tool_completed(
        self,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result: Any,
        arguments: Any = None,
    ) -> None:
        turn_id = self._active_turn(session_id)
        if not turn_id or tool_name not in PUBLIC_TOOL_ACTIVITIES:
            return
        normalized_call_id = str(tool_call_id or "")
        with self._lock:
            start_was_observed = (
                normalized_call_id in self._active_calls.get(turn_id, {})
            )
        if not start_was_observed:
            # Some runtime paths can surface the canonical completion callback
            # without their best-effort start callback. Backfill the public
            # lifecycle at the projection boundary only. The durable event
            # idempotency key makes this harmless when a start was already
            # persisted before a process restart or callback-state loss.
            started = public_activity_event(
                turn_id=turn_id,
                tool_call_id=normalized_call_id,
                tool_name=tool_name,
                phase="started",
                arguments=arguments,
            )
            if started is not None:
                self._append(turn_id, started)
        projected = public_activity_event(
            turn_id=turn_id,
            tool_call_id=normalized_call_id,
            tool_name=tool_name,
            phase="completed",
            result=result,
            arguments=arguments,
        )
        if projected is not None:
            self._append(turn_id, projected)
        with self._lock:
            self._active_calls.get(turn_id, {}).pop(normalized_call_id, None)

    def _active_turn(self, session_id: str) -> str:
        if not self.enabled():
            return ""
        with self._lock:
            bound_turns = self._turns_by_session.get(session_id, [])
            return bound_turns[0] if len(bound_turns) == 1 else ""

    def _interrupt_active_calls(self, turn_id: str) -> None:
        with self._lock:
            active = tuple(self._active_calls.pop(turn_id, {}).items())
        for tool_call_id, (tool_name, arguments) in active:
            projected = public_activity_event(
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="interrupted",
                arguments=arguments,
            )
            if projected is not None:
                self._append(turn_id, projected)

    def _append(
        self,
        turn_id: str,
        projected: tuple[dict[str, Any], str],
    ) -> bool:
        payload, idempotency_key = projected
        try:
            self.store.append_public_stream_event(
                turn_id,
                event_type="activity",
                payload=payload,
                idempotency_key=idempotency_key,
            )
            return True
        except Exception:
            logger.warning(
                "Public activity projection failed for Turn %s",
                turn_id,
                exc_info=True,
            )
            return False


def _tool_result_succeeded(result: Any) -> bool:
    payload = result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return False
    if not isinstance(payload, dict):
        return False
    if payload.get("status") in {"ok", "success", "succeeded"}:
        return True
    if payload.get("status") in {"error", "failed"}:
        return False
    return payload.get("ok") is True


def _digest(*parts: str) -> str:
    value = "\x00".join(str(part or "") for part in parts)
    return sha256(value.encode("utf-8")).hexdigest()
