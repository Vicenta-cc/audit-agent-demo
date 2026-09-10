import { getInvestigationWorkspaceState } from "../../services/investigationCreation";
import { fetchPublishedReportVersions } from "../../services/reports";

export async function resolvePublishedReportVersion(
  investigationId: string,
  explicitReportVersionId = ""
) {
  if (explicitReportVersionId) {
    if (!explicitReportVersionId.startsWith("report-version:")) {
      throw new Error("报告引用无效");
    }
    return explicitReportVersionId;
  }

  try {
    const workspace = await getInvestigationWorkspaceState(investigationId);
    if (workspace.run?.report_version_id) return workspace.run.report_version_id;
  } catch {
    // A task route can resolve through the published version list below.
  }

  const versions = await fetchPublishedReportVersions(investigationId);
  if (!versions.latest_report_version_id) throw new Error("当前调查尚无已发布报告");
  return versions.latest_report_version_id;
}
