import type { ReportPostPresentation } from "../../types/reports";

const riskLabels: Record<string, string> = {
  high: "高风险",
  medium: "中风险",
  low: "低风险",
  none: "无风险"
};

export function ReportRiskTag({ post }: { post: ReportPostPresentation }) {
  const label = post.pending_manual_review ? "风险未定" : riskLabels[post.risk_level];
  const tone = post.pending_manual_review || !label ? "unknown" : post.risk_level;
  return <span className={`r31-risk-tag is-${tone}`}>{label || "未分级"}</span>;
}

export function ReportAuditConclusion({ post, decisionLabel }: { post: ReportPostPresentation; decisionLabel?: string }) {
  return (
    <section className="r31-drawer-section">
      <h3>审核结论</h3>
      <p>{post.pending_manual_review ? "待人工复核 · 风险未定" : [decisionLabel, riskLabels[post.risk_level] || "未分级"].filter(Boolean).join(" · ")}</p>
      {post.pending_manual_review && Boolean(post.audit_gap_stages?.length) && <p>未完成环节：{post.audit_gap_stages?.join("、")}</p>}
      <p>{post.audit_summary || "原审核结果未提供文字说明。"}</p>
    </section>
  );
}
