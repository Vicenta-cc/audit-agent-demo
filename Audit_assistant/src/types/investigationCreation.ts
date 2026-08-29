import type { InvestigationTurnResponse } from "./investigations";

export type InvestigationPlatform = "xhs" | "dy" | "ks";
export type InvestigationRunStatus =
  | "QUEUED"
  | "RUNNING"
  | "REPORT_GENERATING"
  | "PUBLISHED"
  | "FAILED"
  | "INTERRUPTED";

export interface InvestigationBlocker {
  code: string;
  message: string;
  resource_type: string;
  resource_id: string;
  latest_safe_summary: Record<string, unknown>;
  management_url: string;
}

export interface AuditPolicySummary {
  id: string;
  name: string;
  description: string;
  published_version: string;
  ruleset_revision_id: string;
  ruleset_version: number;
  domain: string;
}

export interface RuleSetRevisionSummary {
  id: string;
  ruleset_id: string;
  name: string;
  domain: string;
  version: number;
  enabled_rule_count: number;
}

export interface InvestigationDraftConfiguration {
  schema_version: "investigation-draft-config-v3";
  platform: InvestigationPlatform;
  investigation:
    | {
        mode: "search";
        recall_plan:
          | {
              strategy: "temporary_terms";
              terms: string[];
              source_lexicon_ids: string[];
            }
          | {
              strategy: "existing_lexicon";
              lexicon_id: string;
              expected_runtime_content_hash: string;
            };
      }
    | { mode: "creator"; creator_url: string };
  audit_policy: null | {
    id: string;
    expected_published_version: string;
    expected_published_config_hash: string;
    expected_ruleset_revision_id: string;
    expected_ruleset_version: number;
    expected_ruleset_content_hash: string;
  };
}

export interface PublicInvestigationDraft {
  id: string;
  status: "DRAFT" | "QUEUED";
  current_revision: number;
  title: string;
  objective: string;
  configuration: InvestigationDraftConfiguration;
  created_at: string;
  updated_at: string;
  confirmed_revision: number | null;
  confirmed_at: string;
}

export interface ConfirmationPreview {
  draft_id: string;
  draft_revision: number;
  title: string;
  objective: string;
  mode: "search" | "creator";
  platform: InvestigationPlatform;
  resolved_search_terms: string[];
  creator_url: string;
  recall_plan: {
    strategy: "existing_lexicon" | "temporary_terms" | "none";
    lexicon_id: string;
    lexicon_title: string;
    enabled_main_term_count: number;
    temporary_terms: string[];
    source_lexicon_ids: string[];
  };
  audit_policy: AuditPolicySummary | null;
  ruleset_revision: RuleSetRevisionSummary | null;
  max_notes: 1;
  blockers: InvestigationBlocker[];
  can_confirm: boolean;
}

export interface InvestigationDraftArtifact {
  artifact_type: "investigation_draft";
  draft_id: string;
  draft_revision: number;
  draft: PublicInvestigationDraft;
  confirmation_preview: ConfirmationPreview;
}

export interface InvestigationRunProjection {
  run_id: string;
  draft_id: string;
  draft_revision: number;
  status: InvestigationRunStatus;
  job_id: string;
  crawl_status: string;
  analysis_status: string;
  task_stats: Record<string, unknown>;
  report_status: string;
  report_version_id: string;
  error_code: string;
  error_message: string;
  created_at: string;
  updated_at: string;
  started_at: string;
  completed_at: string;
}

export interface InvestigationRunArtifact {
  artifact_type: "investigation_run";
  run_id: string;
  run: InvestigationRunProjection;
}

export type InvestigationConversationArtifact =
  | InvestigationDraftArtifact
  | InvestigationRunArtifact;

export interface InvestigationWorkspaceSession {
  workspace_session_id: string;
  status: "active" | "closed";
  created_at: string;
  updated_at: string;
}

export interface InvestigationWorkspaceMessage {
  message_id: string;
  turn_id: string;
  role: "user" | "assistant";
  content: string;
  artifact?: InvestigationConversationArtifact | null;
  sequence: number;
  created_at: string;
}

export interface InvestigationWorkspaceState {
  workspace: InvestigationWorkspaceSession;
  messages: InvestigationWorkspaceMessage[];
  latest_turn: InvestigationTurnResponse | null;
  draft_artifact: InvestigationDraftArtifact | null;
  run: InvestigationRunProjection | null;
  report_messages: InvestigationWorkspaceMessage[];
  latest_report_turn: InvestigationTurnResponse | null;
}
