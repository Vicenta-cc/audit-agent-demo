import { CircleAlert, ShieldAlert, ShieldCheck, UserRoundCheck } from "lucide-react";
import type { OutputFiltersValue, RiskLevel, TaskOutputSummary } from "../../types/taskOutputs";

interface RiskSummaryProps {
  summary: TaskOutputSummary;
  activeRiskLevel: OutputFiltersValue["riskLevel"];
  onRiskLevelChange: (level: OutputFiltersValue["riskLevel"]) => void;
}

const summaryItems = [
  { key: "highRisk", label: "高危", level: "高危", tone: "high", Icon: ShieldAlert },
  { key: "mediumRisk", label: "中危", level: "中危", tone: "medium", Icon: CircleAlert },
  { key: "pendingReview", label: "待复核", level: "待复核", tone: "review", Icon: UserRoundCheck },
  { key: "noRisk", label: "无风险", level: "无风险", tone: "safe", Icon: ShieldCheck }
] as const;

export function RiskSummary({ summary, activeRiskLevel, onRiskLevelChange }: RiskSummaryProps) {
  return (
    <section className="risk-summary" aria-label="风险统计">
      <div className="risk-summary-grid">
        {summaryItems.map((item) => {
          const isActive = activeRiskLevel === item.level;
          return (
            <button
              className={`risk-summary-item risk-summary-${item.tone}${isActive ? " is-active" : ""}`}
              key={item.key}
              type="button"
              aria-pressed={isActive}
              aria-label={`${isActive ? "取消" : "筛选"}${item.label}内容，共 ${summary[item.key]} 条`}
              onClick={() => onRiskLevelChange(isActive ? "全部" : item.level as RiskLevel)}
            >
              <span className="risk-summary-icon" aria-hidden="true">
                <item.Icon size={32} strokeWidth={1.9} />
              </span>
              <span className="risk-summary-copy">
                <span>{item.label}</span>
                <strong>{summary[item.key]}</strong>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
