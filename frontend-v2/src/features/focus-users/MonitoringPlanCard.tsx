import { Settings2 } from "lucide-react";
import { Badge } from "../../components/common/Badge";
import type { MonitoringPlan } from "../../types/focusUsers";

interface MonitoringPlanCardProps {
  plan: MonitoringPlan;
  onAdjust: () => void;
}

export function MonitoringPlanCard({ plan, onAdjust }: MonitoringPlanCardProps) {
  return (
    <article className="dashboard-card plan-card">
      <div className="card-heading">
        <h2>监控计划摘要</h2>
        <button className="btn btn-secondary compact" type="button" onClick={onAdjust}>
          <Settings2 size={15} />
          调整监控计划
        </button>
      </div>

      <div className="plan-grid">
        <PlanItem label="绑定黑话库" value={plan.lexiconLibraries.join("、")} />
        <PlanItem label="监控频率" value={plan.frequency} />
        <PlanItem label="分析策略" value={plan.strategy} />
        <PlanItem label="引用方案" value={plan.referencePlan} />
        <div className="plan-item">
          <span>任务状态</span>
          <Badge tone="green">{plan.taskStatus}</Badge>
        </div>
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
