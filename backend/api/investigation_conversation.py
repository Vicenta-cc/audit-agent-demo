from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.api.contracts import (
    CreateInvestigationTurnRequest,
    GenerateInvestigationConfirmationPreviewRequest,
    InvestigationMessageResponse,
    InvestigationTurnAcceptedResponse,
    InvestigationTurnStatusResponse,
    InvestigationWorkspaceTurnStatusResponse,
    InvestigationWorkspaceStateResponse,
    InvestigationWorkspaceSessionResponse,
    InvestigationWorkspaceReportTurnAcceptedResponse,
    PublishedReportDetailResponse,
)
from backend.api.investigation import (
    _is_public_conversation_message,
    _raise_public_error,
    _turn_status_response,
    public_activity_events_for_turns,
    turn_event_stream_response,
)
from backend.api.investigation_creation import (
    _raise_public_error as _raise_creation_error,
)
from backend.investigation_creation.errors import InvestigationCreationError
from backend.investigation_creation.principal import (
    LocalPrincipalProvider,
    Principal,
    PrincipalProvider,
)
from backend.api.reporting import published_report_detail_response


class CreateInvestigationWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_key: str = Field(default="", max_length=200)


class InvestigationWorkspaceListItem(BaseModel):
    workspace_session_id: str
    title: str
    updated_at: str
    run_status: str
    presentation_stage: str


class InvestigationWorkspaceListResponse(BaseModel):
    items: list[InvestigationWorkspaceListItem]
    has_more: bool


def create_investigation_conversation_router(
    service: Any,
    executor: Any,
    *,
    principal_provider: PrincipalProvider | None = None,
    report_service: Any | None = None,
    report_executor: Any | None = None,
    report_store: Any | None = None,
) -> APIRouter:
    router = APIRouter(tags=["investigation-creation-conversation"])
    provide_principal = principal_provider or LocalPrincipalProvider()

    @router.get("/api/investigation-workspaces", response_model=InvestigationWorkspaceListResponse)
    def list_workspaces(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationWorkspaceListResponse:
        try:
            items = service.list_workspaces(principal=principal, limit=limit + 1, offset=offset)
            return InvestigationWorkspaceListResponse(items=items[:limit], has_more=len(items) > limit)
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-workspaces",
        response_model=InvestigationWorkspaceSessionResponse,
        status_code=201,
    )
    def create_workspace(
        request: CreateInvestigationWorkspaceRequest,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationWorkspaceSessionResponse:
        try:
            session = service.create_session(
                principal=principal, workspace_key=request.workspace_key
            )
            return _workspace_response(session)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-workspaces/{workspace_session_id}/runs/{run_id}/published-report",
        response_model=PublishedReportDetailResponse,
    )
    def get_workspace_published_report(
        workspace_session_id: str,
        run_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> PublishedReportDetailResponse:
        if report_store is None:
            raise RuntimeError("published report handoff is not configured")
        try:
            run = service.authorize_report_handoff(
                workspace_session_id, run_id, principal=principal
            )
            return published_report_detail_response(
                report_store, run.report_version_id
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-workspaces/{workspace_session_id}",
        response_model=InvestigationWorkspaceSessionResponse,
    )
    def get_workspace(
        workspace_session_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationWorkspaceSessionResponse:
        try:
            return _workspace_response(
                service.get_session(workspace_session_id, principal=principal)
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-workspaces/{workspace_session_id}/state",
        response_model=InvestigationWorkspaceStateResponse,
    )
    def get_workspace_state(
        workspace_session_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationWorkspaceStateResponse:
        try:
            state = service.get_workspace_state(
                workspace_session_id, principal=principal
            )
            activity_turn_ids = [
                message.turn_id
                for message in (*state.messages, *state.report_messages)
            ]
            if state.latest_turn is not None:
                activity_turn_ids.append(state.latest_turn.id)
            if state.latest_report_turn is not None:
                activity_turn_ids.append(state.latest_report_turn.id)
            return InvestigationWorkspaceStateResponse(
                workspace=_workspace_response(state.session),
                messages=_public_messages(service, state.messages),
                latest_turn=(
                    _turn_status_response(service, state.latest_turn)
                    if state.latest_turn is not None
                    else None
                ),
                draft_artifact=state.draft_artifact or None,
                run=(
                    state.run.model_dump(
                        mode="json", exclude={"report_session_id"}
                    )
                    if state.run is not None
                    else None
                ),
                report_messages=_public_messages(service, state.report_messages),
                latest_report_turn=(
                    _workspace_turn_status_response(service, state.latest_report_turn)
                    if state.latest_report_turn is not None
                    else None
                ),
                activity_events=public_activity_events_for_turns(
                    service.store,
                    activity_turn_ids,
                ),
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-workspaces/{workspace_session_id}/messages",
        response_model=tuple[InvestigationMessageResponse, ...],
    )
    def list_messages(
        workspace_session_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> tuple[InvestigationMessageResponse, ...]:
        try:
            messages = service.get_messages(
                workspace_session_id,
                principal=principal,
                include_tool_messages=False,
            )
            return _public_messages(service, messages)
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-workspaces/{workspace_session_id}/turns",
        response_model=InvestigationTurnAcceptedResponse,
        status_code=202,
    )
    def create_turn(
        workspace_session_id: str,
        request: CreateInvestigationTurnRequest,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationTurnAcceptedResponse:
        try:
            turn = executor.accept_turn(
                workspace_session_id,
                client_message_id=request.client_message_id,
                content=request.content,
                principal=principal,
            )
            return InvestigationTurnAcceptedResponse(
                session_id=turn.session_id, turn_id=turn.id
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-workspaces/{workspace_session_id}/confirmation-preview",
        response_model=InvestigationTurnStatusResponse,
    )
    def generate_confirmation_preview(
        workspace_session_id: str,
        request: GenerateInvestigationConfirmationPreviewRequest,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationTurnStatusResponse:
        try:
            turn = service.generate_confirmation_preview(
                workspace_session_id,
                client_message_id=request.client_message_id,
                draft_id=request.draft_id,
                expected_revision=request.expected_revision,
                principal=principal,
            )
            return _turn_status_response(service, turn)
        except InvestigationCreationError as exc:
            _raise_creation_error(exc)
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-workspaces/{workspace_session_id}/runs/{run_id}/report-turns",
        response_model=InvestigationWorkspaceReportTurnAcceptedResponse,
        status_code=202,
    )
    def create_report_turn(
        workspace_session_id: str,
        run_id: str,
        request: CreateInvestigationTurnRequest,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationWorkspaceReportTurnAcceptedResponse:
        if report_service is None or report_executor is None:
            raise RuntimeError("report conversation handoff is not configured")
        try:
            run = service.authorize_report_handoff(
                workspace_session_id, run_id, principal=principal
            )
            report_session = report_service.create_session(
                run.report_version_id,
                anchor_key=f"m3-run:{run.run_id}",
            )
            turn = report_executor.accept_turn(
                report_session.id,
                client_message_id=request.client_message_id,
                content=request.content,
            )
            return InvestigationWorkspaceReportTurnAcceptedResponse(turn_id=turn.id)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-workspaces/{workspace_session_id}/runs/{run_id}/report-turns/{turn_id}",
        response_model=InvestigationWorkspaceTurnStatusResponse,
    )
    def get_report_turn(
        workspace_session_id: str,
        run_id: str,
        turn_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationWorkspaceTurnStatusResponse:
        if report_service is None:
            raise RuntimeError("report conversation handoff is not configured")
        try:
            turn = service.authorize_report_turn(
                workspace_session_id, run_id, turn_id, principal=principal
            )
            return _workspace_turn_status_response(report_service, turn)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-workspaces/{workspace_session_id}/runs/{run_id}/report-turns/{turn_id}/events"
    )
    async def stream_report_turn_events(
        workspace_session_id: str,
        run_id: str,
        turn_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        principal: Principal = Depends(provide_principal),
    ) -> StreamingResponse:
        if report_service is None:
            raise RuntimeError("report conversation handoff is not configured")
        try:
            service.authorize_report_turn(
                workspace_session_id, run_id, turn_id, principal=principal
            )
            return turn_event_stream_response(
                report_service,
                turn_id,
                request,
                after_sequence=after_sequence,
                last_event_id=last_event_id,
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/investigation-workspaces/{workspace_session_id}/runs/{run_id}/report-turns/{turn_id}/resume",
        response_model=InvestigationWorkspaceReportTurnAcceptedResponse,
        status_code=202,
    )
    def resume_report_turn(
        workspace_session_id: str,
        run_id: str,
        turn_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationWorkspaceReportTurnAcceptedResponse:
        if report_executor is None:
            raise RuntimeError("report conversation handoff is not configured")
        try:
            service.authorize_report_turn(
                workspace_session_id, run_id, turn_id, principal=principal
            )
            turn = report_executor.resume_turn(turn_id)
            return InvestigationWorkspaceReportTurnAcceptedResponse(turn_id=turn.id)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/investigation-workspace-turns/{turn_id}",
        response_model=InvestigationTurnStatusResponse,
    )
    def get_turn(
        turn_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationTurnStatusResponse:
        try:
            return _turn_status_response(
                service, service.authorize_turn(turn_id, principal=principal)
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.get("/api/investigation-workspace-turns/{turn_id}/events")
    async def stream_turn_events(
        turn_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        principal: Principal = Depends(provide_principal),
    ) -> StreamingResponse:
        try:
            service.authorize_turn(turn_id, principal=principal)
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
        "/api/investigation-workspace-turns/{turn_id}/resume",
        response_model=InvestigationTurnAcceptedResponse,
        status_code=202,
    )
    def resume_turn(
        turn_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> InvestigationTurnAcceptedResponse:
        try:
            service.authorize_turn(turn_id, principal=principal)
            turn = executor.resume_turn(turn_id)
            return InvestigationTurnAcceptedResponse(
                session_id=turn.session_id, turn_id=turn.id
            )
        except Exception as exc:
            _raise_public_error(exc)

    return router


def _workspace_response(session: Any) -> InvestigationWorkspaceSessionResponse:
    return InvestigationWorkspaceSessionResponse(
        workspace_session_id=session.id,
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _public_messages(
    service: Any, messages: Any
) -> tuple[InvestigationMessageResponse, ...]:
    output = []
    for message in messages:
        if not _is_public_conversation_message(service, message):
            continue
        artifact = None
        if message.role == "assistant" and message.turn_id:
            artifact = service.store.get_turn(message.turn_id).public_artifact or None
        output.append(
            InvestigationMessageResponse(
                message_id=message.id,
                turn_id=message.turn_id,
                role=message.role,
                content=message.content,
                artifact=artifact,
                sequence=message.sequence,
                created_at=message.created_at,
            )
        )
    return tuple(output)


def _workspace_turn_status_response(
    service: Any, turn: Any
) -> InvestigationWorkspaceTurnStatusResponse:
    status = _turn_status_response(service, turn)
    return InvestigationWorkspaceTurnStatusResponse.model_validate(
        status.model_dump(mode="json", exclude={"session_id"})
    )
