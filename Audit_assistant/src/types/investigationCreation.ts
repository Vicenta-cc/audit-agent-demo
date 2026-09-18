import type {
  InvestigationActivityEvent,
  InvestigationTurnResponse
} from "./investigations";

export type InvestigationPlatform = "xhs" | "dy" | "ks" | "wb";
export type InvestigationCreationPlatform = InvestigationPlatform;
export type InvestigationRunStatus =
  | "QUEUED"
  | "RUNNING"
  | "AUDIT_COMPLETED"
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

export interface RuleSetRevisionSummary {
  id: string;
  ruleset_id: string;
  name: string;
  domain: string;
  version: number;
  content_hash: string;
  enabled_rule_count: number;
}

export interface InvestigationPlatformOption {
  id: InvestigationCreationPlatform;
  name: string;
  available: boolean;
}

export interface RecallLexiconSummary {
  id: string;
  title: string;
  risk_label: string;
  enabled_main_term_count: number;
  runtime_content_hash: string;
  enabled_main_terms: string[];
  available: boolean;
}

export interface TemporaryRuleSetJudgement {
  strategy: "temporary_ruleset";
  proposal_id: string;
  proposal_version: number;
  content_hash: string;
  content: {
    name: string;
    domain: string;
    audit_goal: string;
    categories: { rules: { enabled: boolean; [key: string]: unknown }[]; [key: string]: unknown }[];
    [key: string]: unknown;
  };
}

export interface InvestigationDraftSuggestion {
  title: string;
  objective: string;
  mode: "search" | "creator";
  creator_url: string;
  platform_options: InvestigationPlatformOption[];
  selected_platform: InvestigationCreationPlatform;
  search_terms: string[];
  ruleset_revision: RuleSetRevisionSummary | null;
  temporary_ruleset?: TemporaryRuleSetJudgement | null;
  recall_lexicons: RecallLexiconSummary[];
}

export interface InvestigationTaskParameters {
  crawler_account_id?: string | null;
  start_page: number;
  max_notes: number;
  max_total_notes: number;
  max_comments: number;
  collect_comments: boolean;
  get_sub_comment: boolean;
  collect_media: boolean;
  max_items_per_minute: number;
  max_concurrency: number;
  auto_analyze: boolean;
  analyze_limit: number;
  analysis_batch_size: number;
}

export interface InvestigationDraftConfiguration {
  task_parameters?: InvestigationTaskParameters;
  schema_version: "investigation-draft-config-v4";
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
              enabled_main_terms?: string[];
            };
      }
    | { mode: "creator"; creator_url: string };
  judgement: TemporaryRuleSetJudgement | {
    strategy: "existing_ruleset";
    ruleset_revision_id: string;
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
  task_settings_revision?: number;
  requested_parameters?: InvestigationTaskParameters;
  effective_parameters?: InvestigationTaskParameters;
  estimated_max_contents?: number;
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
    enabled_main_terms: string[];
    temporary_terms: string[];
    source_lexicon_ids: string[];
  };
  ruleset_revision: RuleSetRevisionSummary | null;
  temporary_ruleset?: TemporaryRuleSetJudgement | null;
  max_notes: number;
  max_posts_per_keyword?: number;
  max_comments_per_post?: number;
  get_sub_comment?: boolean;
  blockers: InvestigationBlocker[];
  can_confirm: boolean;
}

export interface InvestigationDraftArtifact {
  artifact_type: "investigation_draft";
  presentation_stage: "suggestion" | "confirmation";
  draft_id: string;
  draft_revision: number;
  draft: PublicInvestigationDraft;
  confirmation_preview: ConfirmationPreview;
  suggestion: InvestigationDraftSuggestion | null;
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
  audit_results: CompletedAuditResult[];
  logs?: Array<{
    time?: string;
    stage?: string;
    level?: "info" | "warning" | "error" | string;
    message?: string;
  }>;
  available_actions?: {
    pause_crawl?: boolean;
    resume_crawl?: boolean;
    pause_analysis?: boolean;
    resume_analysis?: boolean;
    stop_analysis?: boolean;
    backfill_analysis?: boolean;
    delete_job?: boolean;
  };
  report_status: string;
  report_version_id: string;
  error_code: string;
  error_message: string;
  created_at: string;
  updated_at: string;
  started_at: string;
  completed_at: string;
}

export interface CompletedAuditEvidence {
  evidence_id: string;
  evidence_type: string;
  content: string;
  translation: string;
  explanation: string;
}

export interface CompletedAuditResult {
  audit_result_id: string;
  content_key: string;
  platform: string;
  content_title: string;
  author_display_name: string;
  decision: string;
  risk_level: string;
  summary: string;
  analyzed_at: string;
  evidence: CompletedAuditEvidence[];
}

export interface InvestigationRunArtifact {
  artifact_type: "investigation_run";
  run_id: string;
  run: InvestigationRunProjection;
}

export interface GeneratedRuleSetContent {
  name: string;
  audit_goal: string;
  general_exemptions: { exemption_id: string; name: string; condition: string }[];
  categories: {
    category_id: string; name: string; description: string;
    rules: {
      rule_id: string; name: string; enabled: boolean;
      hit_condition: string; suggested_risk_level: string;
      adjudication_notes: string; application_stages: string[];
      rule_exemptions: { exemption_id: string; name: string; condition: string }[];
    }[];
  }[];
}

export interface RuleSetProposalPresentation {
  presentation_id?: string | null;
  assistant_message_id: string;
  text: string;
  snapshot?: { version: number; content: GeneratedRuleSetContent };
}

export type InvestigationConversationArtifact = (
  | InvestigationDraftArtifact
  | InvestigationRunArtifact
  | { artifact_type: "ruleset_proposal_presentation" }
) & { proposal_presentations?: RuleSetProposalPresentation[] };

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
  activity_events: InvestigationActivityEvent[];
  creation_answer_draft?: import("./investigations").InvestigationAnswerDraft | null;
  report_answer_draft?: import("./investigations").InvestigationAnswerDraft | null;
}
