import { apiRequest } from "../../services/apiClient";
import type { ReportPostDetail } from "../../types/reports";
import { mapM3PostToAnalysisRecord } from "./m3AnalysisRecords";

export async function loadHistoricalAnalysisRecords(workspaceId: string) {
  const data = await apiRequest<{
    report_version_id: string; title: string; record_count: number; candidate_count: number;
    notice: string; excluded_posts: { post_id: string; status: string; reason: string }[];
    comment_coverage: { total?: number; completed?: number; failed?: number; translation_failed?: number };
    records: ReportPostDetail[];
  }>(`/api/historical-report-workspaces/${encodeURIComponent(workspaceId)}/analysis-records`);
  return { ...data, records: data.records.map((post, index) => mapM3PostToAnalysisRecord({
    post, itemNumber: index + 1,
    platform: "抖音", analyzedAt: "历史审核记录", reportVersionId: data.report_version_id
  })) };
}
