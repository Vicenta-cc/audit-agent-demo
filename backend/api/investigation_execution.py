from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable


logger = logging.getLogger(__name__)


class InvestigationTurnExecutor:
    """Application-scoped durable executor for public investigation Turns."""

    def __init__(self, service: Any, *, max_workers: int = 2):
        self.service = service
        self.store = service.store
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="investigation-turn",
        )
        self._lock = Lock()
        self._futures: dict[str, Future[Any]] = {}
        self.service.add_turn_node_observer(self._observe_node)

    def accept_turn(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
        **context: Any,
    ) -> Any:
        with self._lock:
            turn, replay = self.service.accept_message(
                session_id,
                client_message_id=client_message_id,
                content=content,
                **context,
            )
            if not self.store.list_public_turn_events(turn.id):
                self.store.append_public_turn_event(turn.id, stage="accepted")
            if turn.status == "running" and (
                not replay
                or (not turn.current_node and turn.id not in self._futures)
            ):
                self._submit_locked(turn.id, self.service.execute_turn)
            return turn

    def resume_turn(self, turn_id: str) -> Any:
        with self._lock:
            turn, replay = self.service.accept_resume(turn_id)
            if not replay:
                self.store.append_public_turn_event(turn.id, stage="accepted")
                self._submit_locked(turn.id, self.service.execute_resume)
            return turn

    def recover(self) -> dict[str, int]:
        recovered = 0
        interrupted = 0
        with self._lock:
            for turn in self.store.list_running_turns():
                owns_turn = getattr(self.service, "owns_turn", None)
                if callable(owns_turn) and not owns_turn(turn.id):
                    continue
                if not self.store.list_public_turn_events(turn.id):
                    self.store.append_public_turn_event(turn.id, stage="accepted")
                if turn.current_node:
                    self.store.mark_interrupted(
                        turn.id,
                        error_code="process_restarted",
                        safe_message="调查执行被中断，可以从检查点恢复。",
                        retryable=True,
                    )
                    self.store.append_public_turn_event(
                        turn.id,
                        stage="interrupted",
                        safe_message="调查执行被中断，可以从检查点恢复。",
                        retryable=True,
                    )
                    interrupted += 1
                    continue
                self._submit_locked(turn.id, self.service.execute_turn)
                recovered += 1
        return {"recovered": recovered, "interrupted": interrupted}

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=False)

    def _submit_locked(
        self, turn_id: str, operation: Callable[[str], Any]
    ) -> None:
        current = self._futures.get(turn_id)
        if current is not None and not current.done():
            return
        future = self._pool.submit(self._run, turn_id, operation)
        self._futures[turn_id] = future
        future.add_done_callback(
            lambda completed, current_turn_id=turn_id: self._forget(
                current_turn_id, completed
            )
        )

    def _run(self, turn_id: str, operation: Callable[[str], Any]) -> None:
        self.store.append_public_turn_event(turn_id, stage="planning")
        try:
            operation(turn_id)
        except Exception:
            logger.exception("investigation Turn execution failed", extra={"turn_id": turn_id})
        self._append_terminal_event(turn_id)

    def _append_terminal_event(self, turn_id: str) -> None:
        turn = self.store.get_turn(turn_id)
        if turn.status == "running":
            self.store.mark_interrupted(
                turn_id,
                error_code="execution_interrupted",
                safe_message="调查执行被中断，可以从检查点恢复。",
                retryable=True,
            )
            turn = self.store.get_turn(turn_id)
        if turn.status == "completed":
            result = self.store.turn_result(turn_id)
            self.store.append_public_turn_event(
                turn_id,
                stage="completed",
                answer=result.answer,
                artifact=turn.public_artifact,
            )
            return
        if turn.status == "interrupted":
            self.store.append_public_turn_event(
                turn_id,
                stage="interrupted",
                safe_message=turn.safe_message or "调查执行已中断。",
                retryable=bool(turn.retryable),
            )
            return
        if turn.status == "error":
            self.store.append_public_turn_event(
                turn_id,
                stage="failed",
                safe_message=turn.safe_message or "调查对话暂时无法完成。",
                retryable=bool(turn.retryable),
            )

    def _observe_node(self, turn_id: str, node_name: str) -> None:
        stage = _public_stage(node_name)
        if stage:
            self.store.append_public_turn_event(turn_id, stage=stage)

    def _forget(self, turn_id: str, future: Future[Any]) -> None:
        with self._lock:
            if self._futures.get(turn_id) is future:
                self._futures.pop(turn_id, None)


def _public_stage(node_name: str) -> str:
    node = str(node_name or "")
    if node == "prepare_context":
        return "planning"
    if node == "prepare_evidence_sources":
        return "preparing_sources"
    if node in {"execute_tools", "assess_progress"}:
        return "acquiring_source"
    if node in {
        "call_qwen",
        "prepare_case_answer",
        "prepare_final",
        "prepare_grounding_retry",
        "validate_grounding",
        "persist_turn",
        "persist_error",
    }:
        return "answering"
    return ""
