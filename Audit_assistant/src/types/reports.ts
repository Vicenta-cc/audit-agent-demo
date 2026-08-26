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
