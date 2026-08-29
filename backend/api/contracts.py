from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.investigation_creation.public_projection import (
    InvestigationConversationArtifact,
    InvestigationDraftArtifact,
    InvestigationRunArtifact,
    PublicInvestigationDraft,
    PublicInvestigationRunProjection,
)


class PublicApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportVersionSummaryResponse(PublicApiModel):
    report_version_id: str
    report_id: str
    task_id: str
    version_number: int = Field(ge=1)
    status: Literal["published"] = "published"
    title: str
    presentation_version: str
    published_at: str


class ReportVersionListResponse(PublicApiModel):
    task_id: str
    items: tuple[ReportVersionSummaryResponse, ...] = ()
    latest_report_version_id: str | None = None


class ReportTextBlockResponse(PublicApiModel):
    text: str


class ReportKeyMetricResponse(PublicApiModel):
    label: str
    value: str
    detail: str = ""


class ReportCitationActionResponse(PublicApiModel):
    type: Literal["evidence_drawer"] = "evidence_drawer"
    label: str
    claim_ref: str


class ReportCaseBlockResponse(PublicApiModel):
    title: str
    text: str
    citation_actions: tuple[ReportCitationActionResponse, ...] = ()


class ReportSectionResponse(PublicApiModel):
    section_id: str
    title: str
    paragraphs: tuple[ReportTextBlockResponse, ...]


class PublishedReportPresentationResponse(PublicApiModel):
    presentation_version: str
    title: str
    summary: ReportTextBlockResponse
    key_metrics: tuple[ReportKeyMetricResponse, ...]
    sections: tuple[ReportSectionResponse, ...]
    case_blocks: tuple[ReportCaseBlockResponse, ...]
    conclusion: ReportTextBlockResponse
    data_quality_note: ReportTextBlockResponse


class PublishedReportDetailResponse(PublicApiModel):
    report_version_id: str
    report_id: str
    task_id: str
    version_number: int = Field(ge=1)
    status: Literal["published"] = "published"
    title: str
    published_at: str
    presentation: PublishedReportPresentationResponse


class CreateInvestigationTurnRequest(PublicApiModel):
    client_message_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4_000)


class InvestigationSessionResponse(PublicApiModel):
    session_id: str
    report_version_id: str
    status: Literal["active", "closed"]
    created_at: str
    updated_at: str


class InvestigationWorkspaceSessionResponse(PublicApiModel):
    workspace_session_id: str
    status: Literal["active", "closed"]
    created_at: str
    updated_at: str


class InvestigationMessageResponse(PublicApiModel):
    message_id: str
    turn_id: str = ""
    role: Literal["user", "assistant"]
    content: str
    artifact: InvestigationConversationArtifact | None = None
    sequence: int = Field(ge=1)
    created_at: str


class InvestigationTurnStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ERROR = "error"


class InvestigationPublicStage(str, Enum):
    ACCEPTED = "accepted"
    PLANNING = "planning"
    PREPARING_SOURCES = "preparing_sources"
    ACQUIRING_SOURCE = "acquiring_source"
    ANSWERING = "answering"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class InvestigationTurnAcceptedResponse(PublicApiModel):
    session_id: str
    turn_id: str
    status: Literal[InvestigationTurnStatus.RUNNING] = InvestigationTurnStatus.RUNNING


class InvestigationWorkspaceReportTurnAcceptedResponse(PublicApiModel):
    turn_id: str
    status: Literal[InvestigationTurnStatus.RUNNING] = InvestigationTurnStatus.RUNNING


class InvestigationTurnStatusResponse(PublicApiModel):
    session_id: str
    turn_id: str
    status: InvestigationTurnStatus
    stage: InvestigationPublicStage
    answer: str = ""
    safe_message: str = ""
    retryable: bool = False
    artifact: InvestigationConversationArtifact | None = None
    updated_at: str

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "InvestigationTurnStatusResponse":
        if self.status is InvestigationTurnStatus.COMPLETED and not self.answer.strip():
            raise ValueError("completed turn requires an answer")
        if self.status in {
            InvestigationTurnStatus.INTERRUPTED,
            InvestigationTurnStatus.ERROR,
        } and not self.safe_message.strip():
            raise ValueError("failed turn requires a safe message")
        return self


class InvestigationWorkspaceTurnStatusResponse(PublicApiModel):
    turn_id: str
    status: InvestigationTurnStatus
    stage: InvestigationPublicStage
    answer: str = ""
    safe_message: str = ""
    retryable: bool = False
    artifact: InvestigationConversationArtifact | None = None
    updated_at: str

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "InvestigationWorkspaceTurnStatusResponse":
        if self.status is InvestigationTurnStatus.COMPLETED and not self.answer.strip():
            raise ValueError("completed turn requires an answer")
        if self.status in {
            InvestigationTurnStatus.INTERRUPTED,
            InvestigationTurnStatus.ERROR,
        } and not self.safe_message.strip():
            raise ValueError("failed turn requires a safe message")
        return self


class InvestigationWorkspaceStateResponse(PublicApiModel):
    workspace: InvestigationWorkspaceSessionResponse
    messages: tuple[InvestigationMessageResponse, ...] = ()
    latest_turn: InvestigationTurnStatusResponse | None = None
    draft_artifact: InvestigationDraftArtifact | None = None
    run: PublicInvestigationRunProjection | None = None
    report_messages: tuple[InvestigationMessageResponse, ...] = ()
    latest_report_turn: InvestigationWorkspaceTurnStatusResponse | None = None


class InvestigationTurnEventResponse(PublicApiModel):
    event_id: str
    turn_id: str
    sequence: int = Field(ge=1)
    stage: InvestigationPublicStage
    occurred_at: str
    answer: str = ""
    safe_message: str = ""
    retryable: bool = False
    artifact: InvestigationConversationArtifact | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "InvestigationTurnEventResponse":
        if self.stage is InvestigationPublicStage.COMPLETED and not self.answer.strip():
            raise ValueError("completed event requires an answer")
        if self.stage in {
            InvestigationPublicStage.INTERRUPTED,
            InvestigationPublicStage.FAILED,
        } and not self.safe_message.strip():
            raise ValueError("failed event requires a safe message")
        return self
