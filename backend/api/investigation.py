from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from backend.api.contracts import (
    CreateInvestigationTurnRequest,
    InvestigationActivityEventResponse,
    InvestigationAnswerDraftResponse,
    InvestigationAnswerDeltaEventResponse,
    InvestigationAnswerResetEventResponse,
    InvestigationMessageResponse,
    InvestigationPublicStage,
    InvestigationSessionResponse,
    InvestigationTurnAcceptedResponse,
    InvestigationTurnEventResponse,
    InvestigationTurnStatus,
    InvestigationTurnStatusResponse,
)
from backend.investigation.errors import (
    ClientMessageConflictError,
    ConcurrentTurnError,
    InvestigationError,
    InvestigationSessionNotFoundError,
    InvestigationTurnNotFoundError,
    ReportNotFoundError,
    ReportScopeError,
)
from backend.api.reporting import published_report_task_id
from backend.audit_agent.config import settings


logger = logging.getLogger(__name__)


def public_activity_events_for_turns(
    store: Any,
    turn_ids: Iterable[object],
    *,
    max_turns: int | None = None,
) -> tuple[InvestigationActivityEventResponse, ...]:
    """Return validated activity for a bounded window of recent Turns.

    Conversation messages remain authoritative and unbounded. Only the
    optional activity decoration is bounded so workspace recovery cannot grow
    by rereading every event from every historical Turn.
    """

    if not settings.activity_stream_enabled:
        return ()
    events: list[InvestigationActivityEventResponse] = []
    recovery_limit = max(
        1,
        min(
            100,
            int(
                settings.activity_recovery_turn_limit
                if max_turns is None
                else max_turns
            ),
        ),
    )
    normalized_turn_ids: list[str] = []
    seen_turn_ids: set[str] = set()
    for raw_turn_id in turn_ids:
        turn_id = str(raw_turn_id or "").strip()
        if not turn_id or turn_id in seen_turn_ids:
            continue
        seen_turn_ids.add(turn_id)
        normalized_turn_ids.append(turn_id)
    for turn_id in normalized_turn_ids[-recovery_limit:]:
        for raw_event in store.list_public_turn_events(
            turn_id,
            event_types=("activity",),
        ):
            payload = dict(raw_event)
            payload.pop("event_type", None)
            try:
                events.append(InvestigationActivityEventResponse.model_validate(payload))
            except (ValidationError, TypeError, ValueError):
                logger.warning(
                    "Skipping invalid public activity event %s for Turn %s",
                    raw_event.get("event_id", ""),
                    turn_id,
                    exc_info=True,
                )
    events.sort(key=lambda event: (event.occurred_at, event.turn_id, event.sequence))
    return tuple(events)


def public_answer_draft_for_turn(
    store: Any,
    turn_id: object,
    *,
    enabled: bool | None = None,
) -> InvestigationAnswerDraftResponse | None:
    """Collapse the current public answer revision for one pending Turn."""

    if not (settings.answer_stream_enabled if enabled is None else enabled):
        return None
    normalized_turn_id = str(turn_id or "").strip()
    if not normalized_turn_id:
        return None
    message_id = ""
    revision = 0
    text = ""
    event_sequence = 0
    for raw_event in store.list_public_turn_events(
        normalized_turn_id,
        event_types=("answer_delta", "answer_reset"),
    ):
        event_type = str(raw_event.get("event_type") or "")
        if event_type not in {"answer_delta", "answer_reset"}:
            continue
        try:
            _, event = _public_stream_event(raw_event)
        except (ValidationError, TypeError, ValueError):
            logger.warning(
                "Skipping invalid public answer event %s for Turn %s",
                raw_event.get("event_id", ""),
                normalized_turn_id,
                exc_info=True,
            )
            continue
        if message_id and event.message_id != message_id:
            logger.warning(
                "Skipping mismatched public answer message for Turn %s",
                normalized_turn_id,
            )
            continue
        message_id = event.message_id
        if event.revision < revision:
            continue
        if event_type == "answer_reset":
            revision = event.revision
            text = ""
        else:
            if event.revision > revision:
                revision = event.revision
                text = ""
            text += event.delta
        event_sequence = event.sequence
    if not message_id or not revision or not event_sequence:
        return None
    return InvestigationAnswerDraftResponse(
        message_id=message_id,
        revision=revision,
        text=text,
        event_sequence=event_sequence,
    )


def create_investigation_router(
    service: Any,
    executor: Any,
    *,
    report_store: Any | None = None,
    m3_run_store: Any | None = None,
    historical_report_service: Any | None = None,
) -> APIRouter:
    """Expose the 3A Agent without leaking its internal execution contracts."""

    router = APIRouter(tags=["investigation"])

    @router.post(
        "/api/report-versions/{report_version_id}/investigation-sessions",
        response_model=InvestigationSessionResponse,
    )
    def create_session(report_version_id: str) -> InvestigationSessionResponse:
        _reject_legacy_m3_session_creation(
            report_store,
            m3_run_store,
            report_version_id,
            historical_report_service=historical_report_service,
        )
        try:
            session = service.create_session(report_version_id)
        except Exception as exc:
            _raise_public_error(exc)
        return _session_response(session)

    @router.get(
        "/api/investigation-sessions/{session_id}/messages",
        response_model=tuple[InvestigationMessageResponse, ...],
    )
    def list_messages(session_id: str) -> tuple[InvestigationMessageResponse, ...]:
        try:
            _reject_workspace_handoff_session(
                service,
                session_id,
                report_store,
                m3_run_store,
                historical_report_service=historical_report_service,
            )
            messages = service.get_messages(session_id, include_tool_messages=False)
            return tuple(
                InvestigationMessageResponse(
                    message_id=message.id,
                    turn_id=message.turn_id,
                    role=message.role,
                    content=message.content,
                    sequence=message.sequence,
                    created_at=message.created_at,
                )
                for message in messages
                if _is_public_conversation_message(service, message)
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-sessions/{session_id}/turns",
        response_model=InvestigationTurnAcceptedResponse,
        status_code=202,
    )
    def create_turn(
        session_id: str,
        request: CreateInvestigationTurnRequest,
    ) -> InvestigationTurnAcceptedResponse:
        try:
            _reject_workspace_handoff_session(
                service,
                session_id,
                report_store,
                m3_run_store,
                historical_report_service=historical_report_service,
            )
            turn = executor.accept_turn(
                session_id,
                client_message_id=request.client_message_id,
                content=request.content,
            )
        except Exception as exc:
            _raise_public_error(exc)
        return InvestigationTurnAcceptedResponse(
            session_id=turn.session_id,
            turn_id=turn.id,
        )

    @router.get(
        "/api/investigation-turns/{turn_id}",
        response_model=InvestigationTurnStatusResponse,
    )
    def get_turn(turn_id: str) -> InvestigationTurnStatusResponse:
        try:
            turn = service.store.get_turn(turn_id)
            _reject_workspace_handoff_session(
                service,
                turn.session_id,
                report_store,
                m3_run_store,
                historical_report_service=historical_report_service,
            )
            return _turn_status_response(service, turn)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get("/api/investigation-turns/{turn_id}/events")
    async def stream_turn_events(
        turn_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            turn = service.store.get_turn(turn_id)
            _reject_workspace_handoff_session(
                service,
                turn.session_id,
                report_store,
                m3_run_store,
                historical_report_service=historical_report_service,
            )
            return turn_event_stream_response(
                service,
                turn_id,
                request,
                after_sequence=after_sequence,
                last_event_id=last_event_id,
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-turns/{turn_id}/resume",
        response_model=InvestigationTurnAcceptedResponse,
        status_code=202,
    )
    def resume_turn(turn_id: str) -> InvestigationTurnAcceptedResponse:
        try:
            turn = service.store.get_turn(turn_id)
            _reject_workspace_handoff_session(
                service,
                turn.session_id,
                report_store,
                m3_run_store,
                historical_report_service=historical_report_service,
            )
            turn = executor.resume_turn(turn_id)
        except Exception as exc:
            _raise_public_error(exc)
        return InvestigationTurnAcceptedResponse(
            session_id=turn.session_id,
            turn_id=turn.id,
        )

    return router


def _reject_legacy_m3_session_creation(
    report_store: Any | None,
    m3_run_store: Any | None,
    report_version_id: str,
    *,
    historical_report_service: Any | None = None,
) -> None:
    if (
        historical_report_service is not None
        and historical_report_service.is_historical_report_version(report_version_id)
    ):
        raise HTTPException(status_code=404, detail="Published report version not found")
    if report_store is None or m3_run_store is None:
        return
    task_id = published_report_task_id(report_store, report_version_id)
    owners = m3_run_store.owner_principals_for_report(
        report_version_id=report_version_id, task_id=task_id
    )
    if owners:
        raise HTTPException(status_code=404, detail="Published report version not found")


def _reject_workspace_handoff_session(
    service: Any,
    session_id: str,
    report_store: Any | None = None,
    m3_run_store: Any | None = None,
    *,
    historical_report_service: Any | None = None,
) -> None:
    read_anchor = getattr(service.store, "session_anchor", None)
    if callable(read_anchor):
        anchor = read_anchor(session_id)
        if anchor.startswith("m3-run:") or anchor.startswith("historical-report:"):
            raise InvestigationSessionNotFoundError("investigation Session was not found")
    if report_store is None or m3_run_store is None:
        return
    session = service.store.get_session(session_id)
    task_id = published_report_task_id(report_store, session.report_version_id)
    owners = m3_run_store.owner_principals_for_report(
        report_version_id=session.report_version_id, task_id=task_id
    )
    if owners:
        raise InvestigationSessionNotFoundError("investigation Session was not found")


def _session_response(session: Any) -> InvestigationSessionResponse:
    return InvestigationSessionResponse(
        session_id=session.id,
        report_version_id=session.report_version_id,
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _is_public_conversation_message(service: Any, message: Any) -> bool:
    if message.role == "user":
        return True
    if message.role != "assistant" or not message.turn_id:
        return False
    turn = service.store.get_turn(message.turn_id)
    return bool(turn.assistant_message_id and turn.assistant_message_id == message.id)


def _terminal_turn_response(result: Any, turn: Any) -> InvestigationTurnStatusResponse:
    completed = result.status == "completed"
    return InvestigationTurnStatusResponse(
        session_id=result.session_id,
        turn_id=result.turn_id,
        status=(
            InvestigationTurnStatus.COMPLETED
            if completed
            else InvestigationTurnStatus.ERROR
        ),
        stage=(
            InvestigationPublicStage.COMPLETED
            if completed
            else InvestigationPublicStage.FAILED
        ),
        answer=result.answer if completed else "",
        safe_message="" if completed else (result.answer or "调查对话暂时无法完成。"),
        retryable=bool(turn.retryable),
        artifact=getattr(turn, "public_artifact", None) or None,
        updated_at=turn.completed_at or turn.started_at or turn.created_at,
    )


def _turn_status_response(service: Any, turn: Any) -> InvestigationTurnStatusResponse:
    if turn.status in {"completed", "error"}:
        return _terminal_turn_response(service.store.turn_result(turn.id), turn)
    turn_events = service.store.list_public_turn_events(
        turn.id,
        event_types=("turn",),
    )
    latest = turn_events[-1] if turn_events else None
    interrupted = turn.status == "interrupted"
    return InvestigationTurnStatusResponse(
        session_id=turn.session_id,
        turn_id=turn.id,
        status=(
            InvestigationTurnStatus.INTERRUPTED
            if interrupted
            else InvestigationTurnStatus.RUNNING
        ),
        stage=(
            InvestigationPublicStage.INTERRUPTED
            if interrupted
            else InvestigationPublicStage(
                str(latest["stage"]) if latest else _public_stage(turn.current_node)
            )
        ),
        safe_message=(
            (turn.safe_message or "调查执行已中断。") if interrupted else ""
        ),
        retryable=bool(turn.retryable),
        artifact=getattr(turn, "public_artifact", None) or None,
        updated_at=(
            str(latest["occurred_at"])
            if latest
            else turn.completed_at or turn.started_at or turn.created_at
        ),
    )


def turn_event_stream_response(
    service: Any,
    turn_id: str,
    request: Request,
    *,
    after_sequence: int = 0,
    last_event_id: str | None = None,
) -> StreamingResponse:
    service.store.get_turn(turn_id)
    cursor = int(after_sequence)
    if last_event_id:
        cursor = max(
            cursor,
            service.store.get_public_turn_event_sequence(turn_id, last_event_id),
        )

    async def event_stream():
        nonlocal cursor
        idle_ticks = 0
        batch_size = settings.stream_replay_batch_size
        while True:
            events = service.store.list_public_turn_events(
                turn_id,
                after_sequence=cursor,
                limit=batch_size,
            )
            terminal_stage: InvestigationPublicStage | None = None
            for raw_event in events:
                cursor = int(raw_event["sequence"])
                idle_ticks = 0
                try:
                    event_name, event = _public_stream_event(raw_event)
                except (ValidationError, TypeError, ValueError):
                    logger.warning(
                        "Skipping invalid public stream event %s for Turn %s",
                        raw_event.get("event_id", ""),
                        turn_id,
                        exc_info=True,
                    )
                    continue
                yield (
                    f"id: {event.event_id}\n"
                    f"event: {event_name}\n"
                    f"data: {event.model_dump_json()}\n\n"
                )
                if event_name == "turn":
                    if event.stage in {
                        InvestigationPublicStage.COMPLETED,
                        InvestigationPublicStage.INTERRUPTED,
                        InvestigationPublicStage.FAILED,
                    }:
                        terminal_stage = event.stage
                    else:
                        terminal_stage = None
            caught_up = len(events) < batch_size
            if not caught_up:
                caught_up = (
                    service.store.get_latest_public_turn_event_sequence(turn_id)
                    <= cursor
                )
            if terminal_stage is not None and _is_current_terminal_event(
                service,
                turn_id,
                terminal_stage,
                is_latest_in_batch=caught_up,
            ):
                return
            if await request.is_disconnected():
                return
            if not caught_up:
                continue
            idle_ticks += 1
            if idle_ticks >= 60:
                idle_ticks = 0
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _public_stream_event(raw_event: dict[str, Any]) -> tuple[str, Any]:
    event_type = str(raw_event.get("event_type", "turn"))
    payload = dict(raw_event)
    payload.pop("event_type", None)
    response_models = {
        "turn": InvestigationTurnEventResponse,
        "activity": InvestigationActivityEventResponse,
        "answer_delta": InvestigationAnswerDeltaEventResponse,
        "answer_reset": InvestigationAnswerResetEventResponse,
    }
    response_model = response_models.get(event_type)
    if response_model is None:
        raise ValueError("invalid public stream event type")
    return event_type, response_model.model_validate(payload)


def _public_stage(node_name: str) -> InvestigationPublicStage:
    node = str(node_name or "")
    if node == "prepare_context":
        return InvestigationPublicStage.PLANNING
    if node == "prepare_evidence_sources":
        return InvestigationPublicStage.PREPARING_SOURCES
    if node in {"execute_tools", "assess_progress"}:
        return InvestigationPublicStage.ACQUIRING_SOURCE
    if node in {
        "call_qwen",
        "prepare_case_answer",
        "prepare_final",
        "prepare_grounding_retry",
        "validate_grounding",
        "persist_turn",
    }:
        return InvestigationPublicStage.ANSWERING
    return InvestigationPublicStage.ACCEPTED


def _is_current_terminal_event(
    service: Any,
    turn_id: str,
    stage: InvestigationPublicStage,
    *,
    is_latest_in_batch: bool,
) -> bool:
    if not is_latest_in_batch:
        return False
    status = service.store.get_turn(turn_id).status
    return (
        (stage is InvestigationPublicStage.COMPLETED and status == "completed")
        or (stage is InvestigationPublicStage.INTERRUPTED and status == "interrupted")
        or (stage is InvestigationPublicStage.FAILED and status == "error")
    )


def _raise_public_error(exc: Exception) -> None:
    if isinstance(exc, (ReportNotFoundError, InvestigationSessionNotFoundError, InvestigationTurnNotFoundError)):
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc
    if isinstance(
        exc, (ClientMessageConflictError, ConcurrentTurnError, ReportScopeError)
    ):
        raise HTTPException(status_code=409, detail=exc.safe_message) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail="调查请求参数无效。") from exc
    if isinstance(exc, InvestigationError):
        raise HTTPException(status_code=503, detail=exc.safe_message) from exc
    raise HTTPException(status_code=500, detail="调查对话暂时无法完成。") from exc
