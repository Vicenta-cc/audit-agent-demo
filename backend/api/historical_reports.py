from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import Field

from backend.api.contracts import (
    CreateInvestigationTurnRequest,
    InvestigationActivityEventResponse,
    InvestigationAnswerDraftResponse,
    InvestigationPublicStage,
    InvestigationTurnStatus,
    PublicApiModel,
)
from backend.api.investigation import (
    _raise_public_error,
    _turn_status_response,
    public_activity_events_for_turns,
    public_answer_draft_for_turn,
    turn_event_stream_response,
)
from backend.investigation_creation.principal import Principal, PrincipalProvider


class HistoricalDisplayMessageResponse(PublicApiModel):
    id: str
    kind: Literal[
        "user_request",
        "plan_recommendation",
        "user_confirmation",
        "processing_update",
        "report_ready",
    ]
    occurred_at: str
    content: str


class HistoricalConfigurationDetail(PublicApiModel):
    title: str
    text: str


class HistoricalDraftResponse(PublicApiModel):
    task_name: str
    subject: str
    platform: str
    search_terms: tuple[str, ...]
    analysis_plan: str
    analysis_description: str
    recall_lexicons: tuple[str, ...]
    history_notice: str = ""
    scope_description: str = ""
    configuration_details: tuple[HistoricalConfigurationDetail, ...] = ()


class HistoricalConversationMessageResponse(PublicApiModel):
    message_id: str
    turn_id: str
    role: Literal["user", "assistant"]
    content: str
    sequence: int = Field(ge=1)
    created_at: str


class HistoricalTurnStatusResponse(PublicApiModel):
    turn_id: str
    status: InvestigationTurnStatus
    stage: InvestigationPublicStage
    answer: str = ""
    safe_message: str = ""
    retryable: bool = False
    updated_at: str
    event_sequence: int = Field(default=0, ge=0)


class HistoricalTurnAcceptedResponse(PublicApiModel):
    turn_id: str
    status: Literal["running"] = "running"


class HistoricalWorkspaceResponse(PublicApiModel):
    workspace_id: str
    run_id: str
    title: str
    task_id: str
    report_version_id: str
    run_status: Literal["PUBLISHED"] = "PUBLISHED"
    import_semantics: Literal["historical"] = "historical"
    draft: HistoricalDraftResponse
    display_timeline: tuple[HistoricalDisplayMessageResponse, ...]
    conversation: tuple[HistoricalConversationMessageResponse, ...] = ()
    latest_turn: HistoricalTurnStatusResponse | None = None
    activity_events: tuple[InvestigationActivityEventResponse, ...] = ()
    answer_draft: InvestigationAnswerDraftResponse | None = None


class HistoricalWorkspaceListResponse(PublicApiModel):
    items: tuple[HistoricalWorkspaceResponse, ...]


def create_historical_report_router(
    service: Any, *, principal_provider: PrincipalProvider
) -> APIRouter:
    router = APIRouter(tags=["historical-reports"])

    @router.get(
        "/api/historical-report-workspaces",
        response_model=HistoricalWorkspaceListResponse,
    )
    def list_workspaces(
        principal: Principal = Depends(principal_provider),
    ) -> HistoricalWorkspaceListResponse:
        try:
            items = tuple(
                _workspace_response(service, item)
                for item in service.list_workspaces(principal_id=principal.id)
            )
            return HistoricalWorkspaceListResponse(items=items)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/historical-report-workspaces/{workspace_id}",
        response_model=HistoricalWorkspaceResponse,
    )
    def get_workspace(
        workspace_id: str,
        principal: Principal = Depends(principal_provider),
    ) -> HistoricalWorkspaceResponse:
        try:
            return _workspace_response(
                service,
                service.get_workspace(workspace_id, principal_id=principal.id),
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.get("/api/historical-report-workspaces/{workspace_id}/analysis-records")
    def get_analysis_records(
        workspace_id: str,
        principal: Principal = Depends(principal_provider),
    ) -> dict[str, Any]:
        try:
            return service.analysis_records(workspace_id, principal_id=principal.id)
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/historical-report-workspaces/{workspace_id}/turns",
        response_model=HistoricalTurnAcceptedResponse,
        status_code=202,
    )
    def create_turn(
        workspace_id: str,
        request: CreateInvestigationTurnRequest,
        principal: Principal = Depends(principal_provider),
    ) -> HistoricalTurnAcceptedResponse:
        try:
            turn = service.accept_turn(
                workspace_id,
                principal_id=principal.id,
                client_message_id=request.client_message_id,
                content=request.content,
            )
            return HistoricalTurnAcceptedResponse(turn_id=turn.id)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/historical-report-workspaces/{workspace_id}/turns/{turn_id}",
        response_model=HistoricalTurnStatusResponse,
    )
    def get_turn(
        workspace_id: str,
        turn_id: str,
        principal: Principal = Depends(principal_provider),
    ) -> HistoricalTurnStatusResponse:
        try:
            turn = service.require_turn(
                workspace_id, turn_id, principal_id=principal.id
            )
            return _turn_response(service, turn)
        except Exception as exc:
            _raise_public_error(exc)

    @router.get(
        "/api/historical-report-workspaces/{workspace_id}/turns/{turn_id}/events"
    )
    async def stream_turn_events(
        workspace_id: str,
        turn_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        principal: Principal = Depends(principal_provider),
    ):
        try:
            service.require_turn(workspace_id, turn_id, principal_id=principal.id)
            return turn_event_stream_response(
                service.report_service,
                turn_id,
                request,
                after_sequence=after_sequence,
                last_event_id=last_event_id,
            )
        except Exception as exc:
            _raise_public_error(exc)

    @router.post(
        "/api/historical-report-workspaces/{workspace_id}/turns/{turn_id}/resume",
        response_model=HistoricalTurnAcceptedResponse,
        status_code=202,
    )
    def resume_turn(
        workspace_id: str,
        turn_id: str,
        principal: Principal = Depends(principal_provider),
    ) -> HistoricalTurnAcceptedResponse:
        try:
            turn = service.resume_turn(
                workspace_id, turn_id, principal_id=principal.id
            )
            return HistoricalTurnAcceptedResponse(turn_id=turn.id)
        except Exception as exc:
            _raise_public_error(exc)

    return router


def _workspace_response(service: Any, item: dict[str, Any]) -> HistoricalWorkspaceResponse:
    latest_turn = item.get("latest_turn")
    activity_turn_ids = [
        str(message.get("turn_id") or "")
        for message in item.get("conversation") or ()
    ]
    if latest_turn is not None:
        activity_turn_ids.append(latest_turn.id)
    return HistoricalWorkspaceResponse(
        workspace_id=str(item["id"]),
        run_id=str(item["run_id"]),
        title=str(item["title"]),
        task_id=str(item["task_id"]),
        report_version_id=str(item["report_version_id"]),
        draft=HistoricalDraftResponse.model_validate(item["draft"]),
        display_timeline=tuple(
            HistoricalDisplayMessageResponse.model_validate(message)
            for message in item["display_timeline"]
        ),
        conversation=tuple(
            HistoricalConversationMessageResponse.model_validate(message)
            for message in item.get("conversation") or ()
        ),
        latest_turn=(
            _turn_response(service, latest_turn) if latest_turn is not None else None
        ),
        activity_events=public_activity_events_for_turns(
            service.report_service.store,
            activity_turn_ids,
        ),
        answer_draft=(
            public_answer_draft_for_turn(
                service.report_service.store,
                latest_turn.id,
            )
            if latest_turn is not None and latest_turn.status == "running"
            else None
        ),
    )


def _turn_response(service: Any, turn: Any) -> HistoricalTurnStatusResponse:
    response = _turn_status_response(service.report_service, turn)
    events = service.report_service.store.list_public_turn_events(turn.id)
    return HistoricalTurnStatusResponse(
        **response.model_dump(exclude={"session_id", "artifact"}),
        event_sequence=int(events[-1]["sequence"]) if events else 0,
    )
