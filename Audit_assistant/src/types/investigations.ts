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
  session_id: string;
  turn_id: string;
  status: InvestigationTurnStatus;
  stage: InvestigationTurnStage;
  answer: string;
  safe_message: string;
  retryable: boolean;
  updated_at: string;
  event_sequence?: number;
}

export interface InvestigationTurnAcceptedResponse {
  session_id: string;
  turn_id: string;
  status: "running";
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
}
