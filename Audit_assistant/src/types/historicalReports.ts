import type {
  InvestigationTurnStage,
  InvestigationTurnStatus
} from "./investigations";

export type HistoricalDisplayMessageKind =
  | "user_request"
  | "plan_recommendation"
  | "user_confirmation"
  | "processing_update"
  | "report_ready";

export interface HistoricalDisplayMessage {
  id: string;
  kind: HistoricalDisplayMessageKind;
  occurred_at: string;
  content: string;
}

export interface HistoricalReportDraft {
  task_name: string;
  subject: string;
  platform: string;
  search_terms: string[];
  analysis_plan: string;
  analysis_description: string;
  recall_lexicons: string[];
  history_notice?: string;
  scope_description?: string;
  configuration_details?: { title: string; text: string }[];
}

export interface HistoricalConversationMessage {
  message_id: string;
  turn_id: string;
  role: "user" | "assistant";
  content: string;
  sequence: number;
  created_at: string;
}

export interface HistoricalReportTurnStatus {
  turn_id: string;
  status: InvestigationTurnStatus;
  stage: InvestigationTurnStage;
  answer: string;
  safe_message: string;
  retryable: boolean;
  updated_at: string;
  event_sequence: number;
}

export interface HistoricalReportWorkspace {
  workspace_id: string;
  run_id: string;
  title: string;
  task_id: string;
  report_version_id: string;
  run_status: "PUBLISHED";
  import_semantics: "historical";
  draft: HistoricalReportDraft;
  display_timeline: HistoricalDisplayMessage[];
  conversation: HistoricalConversationMessage[];
  latest_turn: HistoricalReportTurnStatus | null;
}

export interface HistoricalReportWorkspaceList {
  items: HistoricalReportWorkspace[];
}

export interface HistoricalReportTurnAccepted {
  turn_id: string;
  status: "running";
}
