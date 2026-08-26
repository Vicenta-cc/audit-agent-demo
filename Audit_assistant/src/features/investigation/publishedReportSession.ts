import type { ReportSummary } from "../../types/investigation";
import type { PublishedReportDetail, ReportKeyMetric } from "../../types/reports";

function numericMetric(metrics: ReportKeyMetric[], labels: string[]) {
  const metric = metrics.find((item) => labels.some((label) => item.label.includes(label)));
  const match = metric?.value.match(/[\d,.]+/);
  return match ? Number(match[0].replace(/,/g, "")) : 0;
}

function reportFindings(report: PublishedReportDetail) {
  const sectionFindings = report.presentation.sections.slice(0, 4).map((section) => {
    const text = section.paragraphs[0]?.text.trim() || "";
    const firstSentence = text.match(/^.*?[。！？]/)?.[0] || text;
    return firstSentence ? `${section.title}：${firstSentence}` : section.title;
  });
  if (sectionFindings.length) return sectionFindings;
  return report.presentation.case_blocks.slice(0, 4).map((item) => `${item.title}：${item.text}`);
}

export function buildPublishedReportSummary(report: PublishedReportDetail): ReportSummary {
  const metrics = report.presentation.key_metrics;
  return {
    id: report.report_version_id,
    title: report.presentation.title,
    totalCollected: numericMetric(metrics, ["分析内容", "研判内容", "总量", "采集"]),
    suspectedRisks: numericMetric(metrics, ["中高风险", "风险内容", "风险线索"]),
    suggestedReview: numericMetric(metrics, ["进入复审", "人工复核", "建议复核"]),
    keyAuthorCandidates: numericMetric(metrics, ["重点作者", "重点对象"]),
    findings: reportFindings(report),
    riskDistribution: [],
    presentation: report.presentation,
    versionNumber: report.version_number,
    publishedAt: report.published_at
  };
}
