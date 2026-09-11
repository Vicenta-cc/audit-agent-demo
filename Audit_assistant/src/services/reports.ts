import { apiRequest, withQuery } from "./apiClient";
import type {
  PublishedReportDetail,
  ReportAccountFilter,
  ReportAccountDetail,
  ReportAccountIndexPage,
  ReportAccountSort,
  ReportAppendixPage,
  ReportFindingEvidence,
  ReportPostDetail,
  ReportPresentationProjection,
  ReportVersionListResponse
} from "../types/reports";

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

export function fetchReportPresentation(reportVersionId: string) {
  return apiRequest<ReportPresentationProjection>(
    `/api/report-versions/${encodeURIComponent(reportVersionId)}/presentation-projection`
  );
}

export function fetchReportAccounts(
  reportVersionId: string,
  query: {
    filter?: ReportAccountFilter;
    role?: "post_author" | "comment_author";
    search?: string;
    sort?: ReportAccountSort;
    limit?: number;
    cursor?: string;
  } = {}
) {
  return apiRequest<ReportAccountIndexPage>(withQuery(
    `/api/report-versions/${encodeURIComponent(reportVersionId)}/accounts`,
    query
  ));
}

export function fetchReportAccountDetail(reportVersionId: string, entryRef: string) {
  return apiRequest<ReportAccountDetail>(
    `/api/report-versions/${encodeURIComponent(reportVersionId)}`
      + `/accounts/${encodeURIComponent(entryRef)}`
  );
}

export function fetchReportPostDetail(reportVersionId: string, postRef: string) {
  return apiRequest<ReportPostDetail>(
    `/api/report-versions/${encodeURIComponent(reportVersionId)}`
      + `/posts/${encodeURIComponent(postRef)}`
  );
}

export function fetchReportFindingEvidence(reportVersionId: string, findingRef: string) {
  return apiRequest<ReportFindingEvidence>(
    `/api/report-versions/${encodeURIComponent(reportVersionId)}`
      + `/findings/${encodeURIComponent(findingRef)}/evidence`
  );
}

export function fetchReportAppendix(
  reportVersionId: string,
  query: {
    view?: "posts" | "standalone" | "evidence";
    finding_ref?: string;
    limit?: number;
    cursor?: string;
  } = {}
) {
  return apiRequest<ReportAppendixPage>(withQuery(
    `/api/report-versions/${encodeURIComponent(reportVersionId)}/appendix`,
    query
  ));
}

export async function fetchReportAuditDetail(reportVersionId: string, postRef: string) {
  const base = `/api/report-versions/${encodeURIComponent(reportVersionId)}/posts/${encodeURIComponent(postRef)}`;
  const detail = await apiRequest<import("../types/jobs").AuditResultDetail>(`${base}/audit-detail`);
  const marker = `/outputs/${detail.audit_result.job_id}/`;
  // Resolve saved media through the same authorized report/post as the detail.
  const resolveAssets = (value: unknown): void => {
    if (Array.isArray(value)) { value.forEach(resolveAssets); return; }
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (["asset_rel", "local_path", "original_path", "path"].includes(key) && typeof child === "string" && child && !/^https?:\/\//i.test(child)) {
        const path = child.includes(marker) ? child.split(marker, 2)[1] : child;
        (value as Record<string, unknown>)[key] = `${base}/assets?path=${encodeURIComponent(path)}`;
      } else resolveAssets(child);
    }
  };
  resolveAssets(detail.audit_result);
  resolveAssets(detail.evidence_groups);
  return detail;
}
