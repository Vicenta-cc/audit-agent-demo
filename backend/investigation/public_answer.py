from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import logging
from threading import RLock
from typing import Callable, Iterator

from backend.investigation.store import InvestigationStore


logger = logging.getLogger(__name__)


_SAFETY_HOLDBACK = 128
_MIN_LIVE_CHUNK = 24
_MAX_EVENT_DELTA = 4_096
_FLUSH_BOUNDARIES = frozenset("\n。！？；：.!?;:")
_SAFE_SENTENCE_BOUNDARIES = frozenset("\n。！？；.!?;")


@dataclass
class _AnswerState:
    turn_id: str
    message_id: str
    revision: int = 1
    iteration: int | None = None
    raw_text: str = ""
    projected_text: str = ""
    emitted_text: str = ""
    pending_text: str = ""
    chunk_index: int = 0
    suppressed: bool = False
    finalized: bool = False


def public_answer_message_id(turn_id: str) -> str:
    return "public-answer:" + _digest("answer", turn_id)[:32]


class PublicAnswerStreamer:
    """Fail-open, durable projection of safe model text into public SSE.

    Hermes may retry a provider request, execute tools, or continue into another
    model iteration after already producing visible text.  Those boundaries do
    not alter the canonical Turn result.  This projection therefore treats live
    text as a revisioned draft and always reconciles it with the server-cleaned
    final answer before the Turn becomes terminal.
    """

    def __init__(
        self,
        store: InvestigationStore,
        *,
        enabled: Callable[[], bool],
        sanitize: Callable[[str], tuple[str, bool]],
    ) -> None:
        self.store = store
        self.enabled = enabled
        self.sanitize = sanitize
        self._lock = RLock()
        self._turns_by_session: dict[str, list[str]] = {}
        self._states: dict[str, _AnswerState] = {}

    def agent_callbacks(self, session_id: str) -> dict[str, Callable[..., None]]:
        if not self.enabled():
            return {}
        return {
            "step_callback": (
                lambda iteration, _previous_tools: self.begin_iteration(
                    session_id, iteration
                )
            ),
            "stream_delta_callback": (
                lambda delta: self.stream_delta(session_id, delta)
            ),
            "interim_assistant_callback": (
                lambda _text, **_kwargs: self.discard_draft_for_session(session_id)
            ),
            "tool_start_callback": (
                lambda _tool_call_id, _tool_name, _arguments: (
                    self.discard_draft_for_session(session_id)
                )
            ),
        }

    @contextmanager
    def bind_turn(self, session_id: str, turn_id: str) -> Iterator[None]:
        if not self.enabled():
            yield
            return
        with self._lock:
            state = self._states.get(turn_id)
            if state is None:
                try:
                    state = self._restore_state(turn_id)
                except Exception:
                    logger.warning(
                        "Public answer state restoration failed for Turn %s",
                        turn_id,
                        exc_info=True,
                    )
                    state = _AnswerState(
                        turn_id=turn_id,
                        message_id=public_answer_message_id(turn_id),
                        suppressed=True,
                    )
                self._states[turn_id] = state
            bound_turns = self._turns_by_session.setdefault(session_id, [])
            bound_turns.append(turn_id)
            ambiguous = len(bound_turns) > 1
        if ambiguous:
            logger.warning(
                "Suppressing ambiguous public answer stream for concurrent Turns "
                "in Session %s",
                session_id,
            )
        elif state.emitted_text:
            # A prior process can die after durable deltas but before it writes
            # the interruption reset.  A resumed Turn must never append the new
            # provider attempt to that stale draft.
            self._discard_draft(state)
        try:
            yield
        finally:
            with self._lock:
                bound_turns = self._turns_by_session.get(session_id, [])
                if turn_id in bound_turns:
                    bound_turns.remove(turn_id)
                if not bound_turns:
                    self._turns_by_session.pop(session_id, None)

    def begin_iteration(self, session_id: str, iteration: int) -> None:
        state = self._active_state(session_id)
        if state is None:
            return
        normalized_iteration = max(1, int(iteration or 1))
        with self._lock:
            previous = state.iteration
            state.iteration = normalized_iteration
        if previous is not None:
            self._discard_draft(state)

    def stream_delta(self, session_id: str, delta: str) -> None:
        state = self._active_state(session_id)
        text = str(delta or "")
        if state is None or not text:
            return
        with self._lock:
            if state.finalized or state.suppressed:
                return
            state.raw_text += text
            sanitized = self.sanitize(state.raw_text)[0]
            holdback_end = max(0, len(sanitized) - _SAFETY_HOLDBACK)
            sentence_end = max(
                (
                    index + 1
                    for index, char in enumerate(sanitized)
                    if char in _SAFE_SENTENCE_BOUNDARIES
                ),
                default=0,
            )
            # Completed sentences cannot be extended into an internal account
            # token, so they can appear immediately. An unfinished tail keeps
            # enough text server-side for cumulative redaction to recognize a
            # token even when the provider splits it across chunks.
            stable = sanitized[: max(holdback_end, sentence_end)]
            if not stable.startswith(state.projected_text):
                self._discard_draft(state)
                return
            suffix = stable[len(state.projected_text):]
            if suffix:
                state.projected_text += suffix
                state.pending_text += suffix
            should_flush = (
                len(state.pending_text) >= _MIN_LIVE_CHUNK
                or any(char in state.pending_text for char in _FLUSH_BOUNDARIES)
            )
        if should_flush:
            self._flush_pending(state)

    def discard_draft_for_session(self, session_id: str) -> None:
        state = self._active_state(session_id)
        if state is not None:
            self._discard_draft(state)

    def interrupt(self, turn_id: str) -> None:
        if not self.enabled():
            return
        state = self._state(turn_id)
        if state is not None:
            self._discard_draft(state)

    def finalize(self, turn_id: str, canonical_answer: str) -> None:
        if not self.enabled():
            return
        state = self._state(turn_id)
        if state is None:
            return
        canonical = self.sanitize(str(canonical_answer or ""))[0]
        with self._lock:
            if state.finalized:
                return
            state.raw_text = ""
            state.pending_text = ""
            state.projected_text = state.emitted_text
            current = state.emitted_text
        if not canonical.startswith(current):
            self._discard_draft(state)
            with self._lock:
                current = state.emitted_text
        if canonical.startswith(current):
            self._emit_text(state, canonical[len(current):])
        with self._lock:
            state.finalized = True

    def release(self, turn_id: str) -> None:
        with self._lock:
            self._states.pop(turn_id, None)

    def _active_state(self, session_id: str) -> _AnswerState | None:
        if not self.enabled():
            return None
        with self._lock:
            bound_turns = self._turns_by_session.get(session_id, [])
            if len(bound_turns) != 1:
                return None
            return self._states.get(bound_turns[0])

    def _state(self, turn_id: str) -> _AnswerState | None:
        with self._lock:
            state = self._states.get(turn_id)
        if state is not None:
            return state
        try:
            state = self._restore_state(turn_id)
        except Exception:
            logger.warning(
                "Public answer state restoration failed for Turn %s",
                turn_id,
                exc_info=True,
            )
            return None
        with self._lock:
            return self._states.setdefault(turn_id, state)

    def _restore_state(self, turn_id: str) -> _AnswerState:
        message_id = public_answer_message_id(turn_id)
        state = _AnswerState(turn_id=turn_id, message_id=message_id)
        events = self.store.list_public_turn_events(
            turn_id,
            event_types=("answer_delta", "answer_reset"),
        )
        for event in events:
            if event.get("event_type") not in {"answer_delta", "answer_reset"}:
                continue
            # InvestigationStore exposes validated public payload fields at the
            # top level; tolerate the pre-projection nested shape for adapters.
            payload = event.get("payload") or event
            if payload.get("message_id") != message_id:
                continue
            revision = int(payload.get("revision") or 0)
            if event.get("event_type") == "answer_reset":
                if revision >= state.revision:
                    state.revision = revision
                    state.emitted_text = ""
                    state.projected_text = ""
                    state.chunk_index = 0
                continue
            if revision > state.revision:
                state.revision = revision
                state.emitted_text = ""
                state.projected_text = ""
                state.chunk_index = 0
            if revision == state.revision:
                delta = str(payload.get("delta") or "")
                state.emitted_text += delta
                state.projected_text += delta
                state.chunk_index += 1
        return state

    def _discard_draft(self, state: _AnswerState) -> None:
        with self._lock:
            state.raw_text = ""
            state.pending_text = ""
            state.projected_text = state.emitted_text
            if not state.emitted_text:
                state.projected_text = ""
                return
            next_revision = state.revision + 1
        payload = {
            "message_id": state.message_id,
            "revision": next_revision,
        }
        key = "public-event:" + _digest(
            "answer-reset", state.turn_id, str(next_revision)
        )[:32]
        if not self._append(state.turn_id, "answer_reset", payload, key):
            with self._lock:
                state.suppressed = True
            return
        with self._lock:
            state.revision = next_revision
            state.emitted_text = ""
            state.projected_text = ""
            state.pending_text = ""
            state.chunk_index = 0
            state.suppressed = False

    def _flush_pending(self, state: _AnswerState) -> None:
        while True:
            with self._lock:
                if state.suppressed or not state.pending_text:
                    return
                chunk = state.pending_text[:_MAX_EVENT_DELTA]
            if not self._emit_chunk(state, chunk):
                return

    def _emit_text(self, state: _AnswerState, text: str) -> None:
        remaining = str(text or "")
        while remaining:
            chunk = remaining[:_MAX_EVENT_DELTA]
            if not self._emit_chunk(state, chunk):
                return
            remaining = remaining[len(chunk):]

    def _emit_chunk(self, state: _AnswerState, chunk: str) -> bool:
        if not chunk:
            return True
        with self._lock:
            chunk_index = state.chunk_index
            revision = state.revision
        payload = {
            "message_id": state.message_id,
            "revision": revision,
            "delta": chunk,
        }
        key = "public-event:" + _digest(
            "answer-delta",
            state.turn_id,
            str(revision),
            str(chunk_index),
            chunk,
        )[:32]
        if not self._append(state.turn_id, "answer_delta", payload, key):
            return False
        with self._lock:
            state.emitted_text += chunk
            if state.pending_text.startswith(chunk):
                state.pending_text = state.pending_text[len(chunk):]
            state.projected_text = state.emitted_text + state.pending_text
            state.chunk_index += 1
        return True

    def _append(
        self,
        turn_id: str,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> bool:
        try:
            self.store.append_public_stream_event(
                turn_id,
                event_type=event_type,
                payload=payload,
                idempotency_key=idempotency_key,
            )
            return True
        except Exception:
            logger.warning(
                "Public answer projection failed for Turn %s",
                turn_id,
                exc_info=True,
            )
            return False


def _digest(*parts: str) -> str:
    value = "\x00".join(str(part or "") for part in parts)
    return sha256(value.encode("utf-8")).hexdigest()
