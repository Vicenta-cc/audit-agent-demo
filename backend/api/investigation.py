from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from backend.api.contracts import (
    CreateInvestigationTurnRequest,
    InvestigationMessageResponse,
    InvestigationPublicStage,
    InvestigationSessionResponse,
    InvestigationTurnAcceptedResponse,
    InvestigationTurnEventResponse,
    InvestigationTurnStatus,
    InvestigationTurnStatusResponse,
)
from backend.investigation.errors import (
    ConcurrentTurnError,
    InvestigationError,
    InvestigationSessionNotFoundError,
    InvestigationTurnNotFoundError,
    ReportNotFoundError,
    ReportScopeError,
)


def create_investigation_router(service: Any, executor: Any) -> APIRouter:
    """Expose the 3A Agent without leaking its internal execution contracts."""

    router = APIRouter(tags=["investigation"])

    @router.post(
        "/api/report-versions/{report_version_id}/investigation-sessions",
        response_model=InvestigationSessionResponse,
    )
    def create_session(report_version_id: str) -> InvestigationSessionResponse:
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
            service.store.get_turn(turn_id)
            cursor = int(after_sequence)
            if last_event_id:
                cursor = max(
                    cursor,
                    service.store.get_public_turn_event_sequence(
                        turn_id, last_event_id
                    ),
                )
        except Exception as exc:
            _raise_public_error(exc)

        async def event_stream():
            nonlocal cursor
            idle_ticks = 0
            while True:
                events = service.store.list_public_turn_events(
                    turn_id, after_sequence=cursor
                )
                for index, raw_event in enumerate(events):
                    event = InvestigationTurnEventResponse.model_validate(raw_event)
                    cursor = event.sequence
                    idle_ticks = 0
                    yield (
                        f"id: {event.event_id}\n"
                        "event: turn\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                    if event.stage in {
                        InvestigationPublicStage.COMPLETED,
                        InvestigationPublicStage.INTERRUPTED,
                        InvestigationPublicStage.FAILED,
                    } and _is_current_terminal_event(
                        service,
                        turn_id,
                        event.stage,
                        is_latest_in_batch=index == len(events) - 1,
                    ):
                        return
                if await request.is_disconnected():
                    return
                idle_ticks += 1
                if idle_ticks >= 60:
                    idle_ticks = 0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post(
        "/api/investigation-turns/{turn_id}/resume",
        response_model=InvestigationTurnAcceptedResponse,
        status_code=202,
    )
    def resume_turn(turn_id: str) -> InvestigationTurnAcceptedResponse:
        try:
            turn = executor.resume_turn(turn_id)
        except Exception as exc:
            _raise_public_error(exc)
        return InvestigationTurnAcceptedResponse(
            session_id=turn.session_id,
            turn_id=turn.id,
        )

    return router


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
        updated_at=turn.completed_at or turn.started_at or turn.created_at,
    )


def _turn_status_response(service: Any, turn: Any) -> InvestigationTurnStatusResponse:
    if turn.status in {"completed", "error"}:
        return _terminal_turn_response(service.store.turn_result(turn.id), turn)
    events = service.store.list_public_turn_events(turn.id)
    latest = events[-1] if events else None
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
        updated_at=(
            str(latest["occurred_at"])
            if latest
            else turn.completed_at or turn.started_at or turn.created_at
        ),
    )


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
    if isinstance(exc, (ConcurrentTurnError, ReportScopeError)):
        raise HTTPException(status_code=409, detail=exc.safe_message) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail="调查请求参数无效。") from exc
    if isinstance(exc, InvestigationError):
        raise HTTPException(status_code=503, detail=exc.safe_message) from exc
    raise HTTPException(status_code=500, detail="调查对话暂时无法完成。") from exc
