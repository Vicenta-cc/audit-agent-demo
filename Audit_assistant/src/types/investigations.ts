import type { InvestigationConversationArtifact } from "./investigationCreation";

export interface InvestigationApiSession {
  session_id: string;
  report_version_id: string;
  status: "active" | "closed";
  created_at: string;
  updated_at: string;
}

export type InvestigationTurnStatus = "running" | "completed" | "interrupted" | "error";

export type InvestigationTurnStage =
  | "accepted"
  | "planning"
  | "preparing_sources"
  | "acquiring_source"
  | "answering"
  | "completed"
  | "interrupted"
  | "failed";

export interface InvestigationTurnResponse {
  session_id?: string;
  turn_id: string;
  status: InvestigationTurnStatus;
  stage: InvestigationTurnStage;
  answer: string;
  safe_message: string;
  retryable: boolean;
  artifact?: InvestigationConversationArtifact | null;
  updated_at: string;
  event_sequence?: number;
}

export interface InvestigationTurnAcceptedResponse {
  session_id: string;
  turn_id: string;
  status: "running";
  updated_at: string;
}

export interface InvestigationTurnEvent {
  event_id: string;
  turn_id: string;
  sequence: number;
  stage: InvestigationTurnStage;
  occurred_at: string;
  answer: string;
  safe_message: string;
  retryable: boolean;
  artifact?: InvestigationConversationArtifact | null;
}

export type InvestigationActivityStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "interrupted";

export interface InvestigationActivityEvent {
  event_id: string;
  turn_id: string;
  sequence: number;
  occurred_at: string;
  activity_id: string;
  status: InvestigationActivityStatus;
  label: string;
  summary: string;
  result_count?: number | null;
}

export interface InvestigationAnswerDeltaEvent {
  event_id: string;
  turn_id: string;
  sequence: number;
  occurred_at: string;
  message_id: string;
  revision: number;
  delta: string;
}

export interface InvestigationAnswerResetEvent {
  event_id: string;
  turn_id: string;
  sequence: number;
  occurred_at: string;
  message_id: string;
  revision: number;
}

export interface InvestigationAnswerDraft {
  message_id: string;
  revision: number;
  text: string;
  event_sequence: number;
}

export type InvestigationStreamEvent =
  | InvestigationTurnEvent
  | InvestigationActivityEvent
  | InvestigationAnswerDeltaEvent
  | InvestigationAnswerResetEvent;
