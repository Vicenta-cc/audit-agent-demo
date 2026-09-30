import { createRoot } from "react-dom/client";
import { InvestigationReportCard } from "../src/features/investigation/InvestigationReportCard";
import type { ReportKeyMetric } from "../src/types/reports";

export function mountReportCard(host: HTMLElement, keyMetrics: ReportKeyMetric[]) {
  createRoot(host).render(
    <InvestigationReportCard
      report={{
        id: "report-version-1",
        title: "测试报告",
        totalCollected: 0,
        suspectedRisks: 0,
        suggestedReview: 0,
        keyAuthorCandidates: 0,
        findings: [],
        riskDistribution: [],
        presentation: {
          presentation_version: "human-report-v1",
          title: "测试报告",
          summary: { text: "" },
          key_metrics: keyMetrics,
          sections: [],
          case_blocks: [],
          conclusion: { text: "" },
          data_quality_note: { text: "" }
        }
      }}
    />
  );
}
