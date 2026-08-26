import {
  FileCheck2,
  FileText,
  Users,
  Search,
  ArrowRight,
  ShieldAlert
} from "lucide-react";
import type { ReportSummary } from "../../types/investigation";

interface InvestigationReportCardProps {
  report: ReportSummary;
  onOpenReportDrawer?: () => void;
  onOpenEvidenceDrawer?: () => void;
  onOpenKeyUserDrawer?: () => void;
  onFollowUpInvestigation?: () => void;
}

export function InvestigationReportCard({
  report,
  onOpenReportDrawer,
  onOpenEvidenceDrawer,
  onOpenKeyUserDrawer,
  onFollowUpInvestigation
}: InvestigationReportCardProps) {
  const isEthnicRelationsReport = report.id.startsWith("report-ethnic-relations");
  const publishedMetrics = report.presentation?.key_metrics || [];
  const hasActions = Boolean(
    onOpenReportDrawer || onOpenEvidenceDrawer || onOpenKeyUserDrawer || onFollowUpInvestigation
  );

  return (
    <div className="inv-report-card-wrap" aria-label="研判报告卡片">
      <div className="inv-report-head">
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <FileCheck2 size={20} style={{ color: "#2563eb" }} />
          <h3 className="inv-report-title">{report.title}</h3>
        </div>
        <span style={{ fontSize: "11px", fontWeight: "700", color: "#166534", background: "#f0fdf4", padding: "2px 8px", borderRadius: "999px", border: "1px solid #bbf7d0" }}>
          研判报告已就绪
        </span>
      </div>

      {/* Metrics Row */}
      <div className="inv-report-metrics">
        {publishedMetrics.length ? publishedMetrics.slice(0, 4).map((metric) => (
          <div className="inv-metric-block" key={`${metric.label}-${metric.value}`}>
            <span className={`inv-metric-val ${metric.label.includes("风险") ? "text-danger" : metric.label.includes("复审") ? "text-warning" : ""}`}>
              {metric.value}
            </span>
            <span className="inv-metric-lbl">{metric.label}</span>
          </div>
        )) : (
          <>
            <div className="inv-metric-block">
              <span className="inv-metric-val">{report.totalCollected}</span>
              <span className="inv-metric-lbl">研判内容总量</span>
            </div>
            <div className="inv-metric-block">
              <span className="inv-metric-val text-danger">{report.suspectedRisks}</span>
              <span className="inv-metric-lbl">风险线索</span>
            </div>
            <div className="inv-metric-block">
              <span className="inv-metric-val text-warning">{report.suggestedReview}</span>
              <span className="inv-metric-lbl">建议人工复核</span>
            </div>
            <div className="inv-metric-block">
              <span className="inv-metric-val">{report.keyAuthorCandidates}</span>
              <span className="inv-metric-lbl">{isEthnicRelationsReport ? "重点调查对象" : "重点作者候选"}</span>
            </div>
          </>
        )}
      </div>

      {/* Major Findings */}
      <div>
        <div style={{ fontSize: "12.5px", fontWeight: "800", color: "#0f172a", marginBottom: "8px", display: "flex", alignItems: "center", gap: "6px" }}>
          <ShieldAlert size={14} style={{ color: "#2563eb" }} />
          <span>核心研判发现摘要</span>
        </div>
        <ul className="inv-findings-list">
          {report.findings.map((item, idx) => (
            <li key={idx}>{item}</li>
          ))}
        </ul>
      </div>

      {/* Report Actions */}
      {hasActions ? <div className="inv-report-actions">
        {onOpenReportDrawer ? (
          <button type="button" className="inv-btn-primary" onClick={onOpenReportDrawer}>
            <FileText size={14} />
            <span>查看完整报告</span>
          </button>
        ) : null}

        {onOpenEvidenceDrawer ? (
          <button type="button" className="inv-btn-secondary" onClick={onOpenEvidenceDrawer}>
            <Search size={14} />
            <span>查看原始证据</span>
          </button>
        ) : null}

        {onOpenKeyUserDrawer ? (
          <button type="button" className="inv-btn-secondary" onClick={onOpenKeyUserDrawer}>
            <Users size={14} />
            <span>{isEthnicRelationsReport ? "查看重点对象" : "查看重点作者"}</span>
          </button>
        ) : null}

        {onFollowUpInvestigation ? (
          <button
            type="button"
            className="inv-btn-secondary"
            onClick={onFollowUpInvestigation}
            style={{ marginLeft: "auto", color: "#2563eb", borderColor: "#bfdbfe" }}
          >
            <ArrowRight size={14} />
            <span>{isEthnicRelationsReport ? "查看关联线索" : "发起后续穿透调查"}</span>
          </button>
        ) : null}
      </div> : null}
    </div>
  );
}
