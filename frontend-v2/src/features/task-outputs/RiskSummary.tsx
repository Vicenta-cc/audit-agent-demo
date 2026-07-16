import { CircleAlert, ShieldAlert, ShieldCheck, UserRoundCheck } from "lucide-react";
import type { TaskOutputSummary } from "../../types/taskOutputs";

interface RiskSummaryProps {
  summary: TaskOutputSummary;
}

const summaryItems = [
  { key: "highRisk", label: "高危", tone: "high", value: 6, Icon: ShieldAlert },
  { key: "mediumRisk", label: "中危", tone: "medium", value: 2, Icon: CircleAlert },
  { key: "pendingReview", label: "待复核", tone: "review", value: 1, Icon: UserRoundCheck },
  { key: "noRisk", label: "无风险", tone: "safe", value: 294, Icon: ShieldCheck }
] as const;

export function RiskSummary({ summary }: RiskSummaryProps) {
  void summary;

  return (
    <section className="risk-summary" aria-label="风险统计">
      <div className="risk-summary-grid">
        {summaryItems.map((item) => (
          <div className={`risk-summary-item risk-summary-${item.tone}`} key={item.key}>
            <div className="risk-summary-icon" aria-hidden="true">
              <item.Icon size={32} strokeWidth={1.9} />
            </div>
            <div className="risk-summary-copy">
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
