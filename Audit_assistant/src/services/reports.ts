import { apiRequest } from "./apiClient";
import type { PublishedReportDetail, ReportVersionListResponse } from "../types/reports";

export function fetchPublishedReportVersions(taskId: string) {
  return apiRequest<ReportVersionListResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/report-versions`
  );
}

export function fetchPublishedReportVersion(reportVersionId: string) {
  return apiRequest<PublishedReportDetail>(
    `/api/report-versions/${encodeURIComponent(reportVersionId)}`
  );
}
