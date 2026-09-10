import type { TaskRunMetrics } from "../../types/jobs";

interface TaskMetricsProps {
  metrics: TaskRunMetrics;
}

export function TaskMetrics({ metrics }: TaskMetricsProps) {
  const progressItems = [
    { label: "已抓取", value: metrics.crawled },
    { label: "已分析", value: metrics.analyzed },
    { label: "待处理", value: metrics.waiting },
    { label: "失败", value: metrics.failed }
  ];
  const resultItems = [
    { label: "产出", value: metrics.outputs },
    { label: "高危", value: metrics.highRisk }
  ];

  return (
    <div className="task-metrics-cell">
      <div className="metric-group">
        {progressItems.map((item) => (
          <MetricItem key={item.label} label={item.label} value={item.value} />
        ))}
      </div>
      <div className="metric-group result-group">
        {resultItems.map((item) => (
          <MetricItem key={item.label} label={item.label} value={item.value} danger={item.label === "高危"} />
        ))}
      </div>
    </div>
  );
}

interface MetricItemProps {
  label: string;
  value: number;
  danger?: boolean;
}

function MetricItem({ label, value, danger }: MetricItemProps) {
  return (
    <div className="task-metric-item">
      <strong className={danger && value > 0 ? "is-danger" : ""}>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
