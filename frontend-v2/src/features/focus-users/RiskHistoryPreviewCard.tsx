import { ChevronRight } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import { EmptyState } from "../../components/feedback/EmptyState";
import type { RiskLevel, RiskOutput } from "../../types/focusUsers";

interface RiskHistoryPreviewCardProps {
  risks: RiskOutput[];
  onViewAll: () => void;
}

const riskTone: Record<RiskLevel, { label: string; tone: "red" | "orange" | "blue" | "gray" }> = {
  high: { label: "高危", tone: "red" },
  medium: { label: "中危", tone: "orange" },
  review: { label: "待复核", tone: "blue" },
  low: { label: "低危", tone: "gray" }
};

export function RiskHistoryPreviewCard({ risks, onViewAll }: RiskHistoryPreviewCardProps) {
  return (
    <article className="dashboard-card history-card">
      <div className="card-heading">
        <h2>历史产出记录预览</h2>
        <button className="text-link" type="button" onClick={onViewAll}>
          查看全部
          <ChevronRight size={15} />
        </button>
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
          {risks.slice(0, 4).map((risk) => (
            <div className="risk-table-row" role="row" key={risk.id}>
              <span>
                <Badge tone={riskTone[risk.level].tone}>{riskTone[risk.level].label}</Badge>
              </span>
              <strong>{risk.summary}</strong>
              <span>{risk.sourceType}</span>
              <span>{risk.discoveredAt}</span>
              <button className="text-link" type="button" onClick={onViewAll}>
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
