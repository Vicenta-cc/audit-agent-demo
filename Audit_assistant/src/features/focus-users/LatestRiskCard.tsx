import { AlertTriangle, ChevronRight, Loader2 } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import { EmptyState } from "../../components/feedback/EmptyState";
import type { RiskOutput, RiskLevel } from "../../types/focusUsers";

interface LatestRiskCardProps {
  risk: RiskOutput | null;
  isRefetching: boolean;
  onRefetch: () => void;
  onViewHistory: () => void;
  onOpenDetail: (risk: RiskOutput) => void;
}

const levelMeta: Record<RiskLevel, { label: string; tone: "red" | "orange" | "blue" | "gray" }> = {
  high: { label: "高危", tone: "red" },
  medium: { label: "中危", tone: "orange" },
  review: { label: "待复核", tone: "blue" },
  low: { label: "低危", tone: "gray" }
};

export function LatestRiskCard({ risk, isRefetching, onRefetch, onViewHistory, onOpenDetail }: LatestRiskCardProps) {
  return (
    <article className="dashboard-card latest-risk-compact">
      <div className="card-heading">
        <h2>最新异常内容</h2>
      </div>

      {risk ? (
        <div className="latest-risk-content">
          {risk.coverUrl ? (
            <img className="latest-risk-cover" src={risk.coverUrl} alt={`${risk.title}封面`} />
          ) : (
            <div className="latest-risk-cover latest-risk-cover-fallback" aria-hidden="true">
              <AlertTriangle size={24} />
            </div>
          )}
          <div className="latest-risk-detail">
            <div className="latest-risk-meta">
              <Badge tone={levelMeta[risk.level].tone}>{levelMeta[risk.level].label}</Badge>
              <span>{risk.discoveredAt}</span>
              <span>{risk.sourceType}</span>
            </div>
            <h3>{risk.title}</h3>
            <p className="latest-risk-summary">{risk.summary}</p>
            <div className="latest-risk-footer">
              <div className="rule-list">
                {risk.hitRules.slice(0, 2).map((rule) => (
                  <span key={rule}>{rule}</span>
                ))}
                {risk.hitRules.length > 2 && <span>+{risk.hitRules.length - 2}</span>}
              </div>
              <button className="text-link" type="button" onClick={() => onOpenDetail(risk)}>
                查看详情
              </button>
            </div>
          </div>
        </div>
      ) : (
        <EmptyState title="暂无近期风险产出" description="最近一次分析未发现高危内容">
          <button className="btn btn-secondary compact" type="button" onClick={onViewHistory}>
            查看历史产出
          </button>
          <button className="btn btn-primary compact" type="button" onClick={onRefetch} disabled={isRefetching}>
            {isRefetching ? <Loader2 className="spin" size={14} /> : <AlertTriangle size={14} />}
            刷新数据
          </button>
        </EmptyState>
      )}
    </article>
  );
}
