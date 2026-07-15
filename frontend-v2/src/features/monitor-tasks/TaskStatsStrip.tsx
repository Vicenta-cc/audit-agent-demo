import { RadioTower, ShieldAlert, Target, UserRound, Workflow } from "lucide-react";
import type { TaskStatsSummary } from "../../types/jobs";

interface TaskStatsStripProps {
  stats: TaskStatsSummary;
  loading: boolean;
}

export function TaskStatsStrip({ stats, loading }: TaskStatsStripProps) {
  const items = [
    {
      label: "运行中任务",
      value: stats.runningTasks,
      helper: "当前",
      icon: Workflow,
      tone: "blue"
    },
    {
      label: "新增风险",
      value: stats.recentRiskCount,
      helper: "近 7 天",
      icon: ShieldAlert,
      tone: "red"
    },
    {
      label: "直播监控任务",
      value: stats.liveTaskCount,
      helper: stats.liveTaskCount > 0 ? "任务" : "暂未接入",
      icon: RadioTower,
      tone: "green"
    },
    {
      label: "重点用户任务",
      value: stats.focusUserTaskCount,
      helper: "任务",
      icon: UserRound,
      tone: "purple"
    },
    {
      label: "平台抓取任务",
      value: stats.platformCrawlTaskCount,
      helper: "任务",
      icon: Target,
      tone: "orange"
    }
  ] as const;

  return (
    <section className="task-stats-strip" aria-label="监控任务统计">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <div className="task-stat-item" key={item.label}>
            <div className={`task-stat-icon task-stat-${item.tone}`} aria-hidden="true">
              <Icon size={22} />
            </div>
            <div>
              <div className="task-stat-label">{item.label}</div>
              <div className={`task-stat-value${item.label === "新增风险" ? " is-danger" : ""}`}>
                {loading ? "-" : item.value}
              </div>
              <div className="task-stat-helper">{item.helper}</div>
            </div>
          </div>
        );
      })}
    </section>
  );
}
