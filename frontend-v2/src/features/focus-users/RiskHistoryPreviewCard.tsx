import { ChevronRight } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import { EmptyState } from "../../components/feedback/EmptyState";
import type { RiskLevel, RiskOutput } from "../../types/focusUsers";

interface RiskHistoryPreviewCardProps {
  risks: RiskOutput[];
  variant?: "preview" | "full";
  onViewAll?: () => void;
  onOpenRisk: (risk: RiskOutput) => void;
}

const riskTone: Record<RiskLevel, { label: string; tone: "red" | "orange" | "blue" | "gray" }> = {
  high: { label: "高危", tone: "red" },
  medium: { label: "中危", tone: "orange" },
  review: { label: "待复核", tone: "blue" },
  low: { label: "低危", tone: "gray" }
};

export function RiskHistoryPreviewCard({
  risks,
  variant = "preview",
  onViewAll,
  onOpenRisk
}: RiskHistoryPreviewCardProps) {
  const visibleRisks = variant === "preview" ? risks.slice(0, 4) : risks;
  return (
    <article className={`dashboard-card history-card${variant === "full" ? " is-full" : ""}`}>
      <div className="card-heading">
        <div>
          <h2>{variant === "full" ? "风险产出" : "历史产出记录预览"}</h2>
          {variant === "full" ? <p className="card-heading-helper">共 {risks.length} 条真实风险审核结果</p> : null}
        </div>
        {variant === "preview" && onViewAll ? (
          <button className="text-link" type="button" onClick={onViewAll}>
            查看全部
            <ChevronRight size={15} />
          </button>
        ) : null}
      </div>

      {risks.length ? (
        <div className="risk-table" role="table" aria-label="历史产出记录预览">
          <div className="risk-table-row risk-table-head" role="row">
            <span>风险等级</span>
            <span>内容摘要</span>
            <span>来源类型</span>
            <span>发现时间</span>
            <span>操作</span>
          </div>
          {visibleRisks.map((risk) => (
            <div className="risk-table-row" role="row" key={risk.id}>
              <span>
                <Badge tone={riskTone[risk.level].tone}>{riskTone[risk.level].label}</Badge>
              </span>
              <strong title={risk.summary}>{risk.title}</strong>
              <span>{risk.sourceType}</span>
              <span>{risk.discoveredAt}</span>
              <button className="text-link" type="button" onClick={() => onOpenRisk(risk)}>
                查看
              </button>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title="暂无历史产出记录" description="该账号当前没有可展示的历史风险产出" />
      )}
    </article>
  );
}
