import { Settings2 } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import type { MonitoringPlan } from "../../types/focusUsers";

interface MonitoringPlanCardProps {
  plan: MonitoringPlan;
  variant?: "preview" | "full";
  onAdjust?: () => void;
}

export function MonitoringPlanCard({ plan, variant = "preview", onAdjust }: MonitoringPlanCardProps) {
  return (
    <article className={`dashboard-card plan-card${variant === "full" ? " is-full" : ""}`}>
      <div className="card-heading">
        <div>
          <h2>{variant === "full" ? "监控计划详情" : "监控计划摘要"}</h2>
          {variant === "full" ? <p className="card-heading-helper">配置来自账号来源任务</p> : null}
        </div>
        {variant === "preview" && onAdjust ? (
          <button className="btn btn-secondary compact" type="button" onClick={onAdjust}>
            <Settings2 size={15} />
            查看监控计划
          </button>
        ) : null}
      </div>

      <div className="plan-grid">
        <PlanItem label="绑定黑话库" value={plan.lexiconLibraries.join("、")} />
        <PlanItem label="监控频率" value={plan.frequency} />
        <PlanItem label="分析策略" value={plan.strategy} />
        <PlanItem label="引用方案" value={plan.referencePlan} />
        <div className="plan-item">
          <span>任务状态</span>
          <Badge tone={plan.taskStatusTone}>{plan.taskStatus}</Badge>
        </div>
        {variant === "full" ? (
          <>
            <PlanItem label="监控范围" value={plan.scope.join("、")} />
            <PlanItem label="风险类型" value={plan.riskTypes.join("、")} />
            <PlanItem label="任务处理情况" value={plan.autoBackfillRule} />
          </>
        ) : null}
      </div>
    </article>
  );
}

interface PlanItemProps {
  label: string;
  value: string;
}

function PlanItem({ label, value }: PlanItemProps) {
  return (
    <div className="plan-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
