import { ChevronRight } from "lucide-react";
import type { AccountMetrics } from "../../types/focusUsers";

interface RiskSummaryCardProps {
  metrics: AccountMetrics;
  onViewAll: () => void;
}

export function RiskSummaryCard({ metrics, onViewAll }: RiskSummaryCardProps) {
  const items = [
    { label: "总产出", value: metrics.recentRiskCount, className: "summary-total" },
    { label: "高危", value: metrics.highRiskCount, className: "summary-high" },
    { label: "中危", value: metrics.mediumRiskCount, className: "summary-medium" },
    { label: "待复核", value: metrics.pendingReviewCount, className: "summary-review" }
  ];

  return (
    <article className="dashboard-card risk-summary-card">
      <div className="card-heading">
        <h2>近期风险产出统计</h2>
      </div>
      <div className="summary-grid">
        {items.map((item) => (
          <div key={item.label} className="summary-item">
            <span>{item.label}</span>
            <strong className={item.className}>{item.value}</strong>
          </div>
        ))}
      </div>
      <button className="card-bottom-link" type="button" onClick={onViewAll}>
        查看全部风险产出
        <ChevronRight size={15} />
      </button>
    </article>
  );
}
