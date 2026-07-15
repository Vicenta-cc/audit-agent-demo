import { AlertTriangle, ChevronRight, Loader2 } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import { EmptyState } from "../../components/feedback/EmptyState";
import type { RiskOutput, RiskLevel } from "../../types/focusUsers";

interface LatestRiskCardProps {
  risk: RiskOutput | null;
  isRefetching: boolean;
  onRefetch: () => void;
  onViewHistory: () => void;
  onOpenDetail: () => void;
}

const levelMeta: Record<RiskLevel, { label: string; tone: "red" | "orange" | "blue" | "gray" }> = {
  high: { label: "高危", tone: "red" },
  medium: { label: "中危", tone: "orange" },
  review: { label: "待复核", tone: "blue" },
  low: { label: "低危", tone: "gray" }
};

export function LatestRiskCard({ risk, isRefetching, onRefetch, onViewHistory, onOpenDetail }: LatestRiskCardProps) {
  return (
    <article className="dashboard-card latest-risk-card">
      <div className="card-heading">
        <h2>最新异常内容</h2>
      </div>

      {risk ? (
        <div className="latest-risk-content">
          <img className="latest-risk-cover" src={risk.coverUrl} alt="风险内容封面" />
          <div className="latest-risk-detail">
            <div className="latest-risk-meta">
              <Badge tone={levelMeta[risk.level].tone}>{levelMeta[risk.level].label}</Badge>
              <span>{risk.discoveredAt}</span>
              <span>{risk.sourceType}</span>
            </div>
            <p>{risk.summary}</p>
            <div className="rule-list">
              {risk.hitRules.map((rule) => (
                <span key={rule}>{rule}</span>
              ))}
            </div>
            <button className="text-link" type="button" onClick={onOpenDetail}>
              查看详情
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      ) : (
        <EmptyState title="暂无近期风险产出" description="最近一次分析未发现高危内容">
          <button className="btn btn-secondary" type="button" onClick={onViewHistory}>
            查看历史产出
          </button>
          <button className="btn btn-primary" type="button" onClick={onRefetch} disabled={isRefetching}>
            {isRefetching ? <Loader2 className="spin" size={15} /> : <AlertTriangle size={15} />}
            立即补抓
          </button>
        </EmptyState>
      )}
    </article>
  );
}
