export interface ReportVersionSummary {
  report_version_id: string;
  report_id: string;
  task_id: string;
  version_number: number;
  status: "published";
  title: string;
  presentation_version: string;
  published_at: string;
}

export interface ReportVersionListResponse {
  task_id: string;
  items: ReportVersionSummary[];
  latest_report_version_id: string | null;
}

export interface ReportTextBlock {
  text: string;
}

export interface ReportKeyMetric {
  label: string;
  value: string;
  detail: string;
}

export interface ReportCitationAction {
  type: "evidence_drawer";
  label: string;
  claim_ref: string;
}

export interface ReportCaseBlock {
  title: string;
  text: string;
  citation_actions: ReportCitationAction[];
}

export interface ReportSection {
  section_id: string;
  title: string;
  paragraphs: ReportTextBlock[];
}

export interface PublishedReportPresentation {
  presentation_version: string;
  title: string;
  summary: ReportTextBlock;
  key_metrics: ReportKeyMetric[];
  sections: ReportSection[];
  case_blocks: ReportCaseBlock[];
  conclusion: ReportTextBlock;
  data_quality_note: ReportTextBlock;
}

export interface PublishedReportDetail {
  report_version_id: string;
  report_id: string;
  task_id: string;
  version_number: number;
  status: "published";
  title: string;
  published_at: string;
  presentation: PublishedReportPresentation;
}

export interface ReportAvailability {
  status: "available" | "unavailable";
}

export interface ReportPlatformProjection extends ReportAvailability {
  code?: string;
  label?: string;
}

export interface ReportScopeProjection extends ReportAvailability {
  text?: string;
}

export interface ReportAccountMetricStatistics {
  published_post_count: number | null;
  risk_published_post_count: number | null;
  comment_count: number | null;
  risk_comment_count: number | null;
  commented_post_count: number | null;
  commented_post_author_count: number | null;
}

export interface ReportAccountStatistics extends ReportAccountMetricStatistics {
  earliest_activity_at: string | null;
  latest_activity_at: string | null;
}

export interface ReportAccountEntry {
  entry_ref: string;
  display_name: string;
  roles: Array<"post_author" | "comment_author">;
  is_target_account: boolean;
  statistics: ReportAccountStatistics;
  comment_investigation_count?: number;
}

export type ReportAccountFilter =
  | "cross_investigation_commenter"
  | "risk_commenter";

export type ReportAccountSort =
  | "investigation_count"
  | "risk_comment_count"
  | "comment_count"
  | "commented_post_count"
  | "latest_activity";

export interface ReportAccountPresentationGroup {
  status: "available" | "unavailable";
  entries: ReportAccountEntry[];
  total_count: number;
  action_label: string;
  drawer_title: string;
  basis_label?: string;
  unavailable_message?: string;
}

export interface ReportPostPresentation {
  post_ref: string;
  title: string;
  content_summary: string;
  author_display_name: string;
  audit_finding_ref: string;
  decision: string;
  risk_level: string;
  audit_summary: string;
}

export interface ReportEvidencePresentation {
  evidence_ref: string;
  post_ref: string;
  audit_finding_ref: string;
  support_type: "direct" | "indirect" | "counter";
  evidence_type: string;
  original_text: string;
  translated_text: string;
  summary: string;
}

export interface AvailableFindingBinding {
  status: "available";
  investigation_finding_ref: string;
  title: string;
  statement: string;
  boundary_notes: string[];
  related_post_count: number;
  representative_post_count: number;
  direct_evidence_count: number;
  representative_posts: ReportPostPresentation[];
}

export interface UnavailableFindingBinding {
  status: "unavailable";
}

export interface ReportPresentationSection {
  section_ref: string;
  section_number: string;
  parent_section_ref: string | null;
  section_type: string;
  title: string;
  paragraphs: string[];
  presentation_paragraphs?: string[];
  presentation_kind: string;
  finding_binding?: AvailableFindingBinding | UnavailableFindingBinding;
  standalone_items?: Array<ReportPostPresentation & { disposition_note: string }>;
  standalone_count?: number;
}

export interface ReportPresentationProjection {
  schema_version: "r31-report-presentation/v1";
  investigation_summary: {
    status: "available";
    paragraphs: string[];
  };
  report_metadata: {
    title: string;
    source_name: string;
    status: string;
    published_at: string;
    platform: ReportPlatformProjection;
    scope: ReportScopeProjection;
  };
  statistics: {
    canonical_posts: number;
    investigation_finding_count: number;
    independently_reviewed_comments: number | null;
    comment_own_risk: number | null;
    decision: { pass: number; review: number; reject: number };
    risk_level: { high: number; medium: number; low: number; none: number };
    risk_distribution: {
      total: number;
      no_risk_summary: {
        label: string;
        count: number;
        percentage_label: string;
        description: string;
      };
      risk_bars: Array<{
        key: "high" | "medium" | "low";
        label: string;
        count: number;
        width_label: string;
      }>;
    };
    evidence: {
      direct: number;
      direct_finding_count: number;
      indirect: number;
      counter: number;
    };
  };
  accounts: {
    status: "available" | "unavailable";
    coverage?: {
      target_account_count: number;
      post_author_account_count: number;
      comment_author_account_count: number;
      distinct_account_count: number;
    };
    target_entries?: ReportAccountEntry[];
    post_author_entries?: ReportAccountEntry[];
    cross_investigation_commenters?: ReportAccountPresentationGroup;
    risk_commenters?: ReportAccountPresentationGroup;
    index?: {
      available: boolean;
      total_count: number;
      default_page_size: number;
      maximum_page_size: number;
    };
  };
  ordered_sections: ReportPresentationSection[];
  appendix: {
    available: boolean;
    post_count: number;
    direct_evidence_count: number;
  };
}

export interface ReportAccountIndexPage {
  status: "available" | "unavailable";
  filter: ReportAccountFilter;
  action_label: string;
  drawer_title: string;
  basis_label?: string;
  unavailable_message?: string;
  entries: ReportAccountEntry[];
  total_count: number;
  has_more: boolean;
  next_cursor: string | null;
  search: string;
  sort: ReportAccountSort;
  filter_counts: Record<ReportAccountFilter, {
    status: "available" | "unavailable";
    total_count: number;
  }>;
}

export interface ReportAccountDetail {
  entry: {
    entry_ref: string;
    display_name: string;
    is_target_account: boolean;
  };
  current_report: {
    status: "available";
    statistics: ReportAccountStatistics;
  };
  authorized_investigations: {
    status: "available";
    investigation_count: number;
    statistics: ReportAccountStatistics;
    single_investigation_message: string | null;
  } | {
    status: "unavailable";
    unavailable_message: string;
  };
  activity: {
    status: "available";
    earliest_activity_at: string | null;
    latest_activity_at: string | null;
    latest_comment_at: string | null;
    latest_published_at: string | null;
  } | {
    status: "unavailable";
    unavailable_message: string;
  };
  investigation_distribution: Array<{
    investigation_name: string;
    is_current_report: boolean;
    statistics: ReportAccountStatistics;
  }>;
  primary_comment_targets: Array<{
    target_ref: string;
    display_name: string;
    comment_count: number;
    commented_post_count: number;
  }>;
}

export interface ReportPostDetail extends ReportPostPresentation {
  investigation_finding_refs: string[];
  direct_evidence: ReportEvidencePresentation[];
}

export interface ReportFindingEvidence {
  investigation_finding_ref: string;
  title: string;
  direct_evidence_count: number;
  items: ReportEvidencePresentation[];
}

export interface ReportAppendixPage {
  view: "posts" | "standalone" | "evidence";
  finding_ref: string | null;
  item_kind: "post" | "evidence";
  items: Array<ReportPostPresentation | ReportEvidencePresentation>;
  matched_count: number;
  has_more: boolean;
  cursor: string | null;
}
