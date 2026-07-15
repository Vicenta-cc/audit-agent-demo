import { Activity, Clock3, Download, FileText } from "lucide-react";
import type { ReactNode } from "react";
import type { AuditResult, MonitorTask } from "../../types/jobs";
import { formatTimeOnly } from "./taskOutputUtils";

interface TaskRuntimeSummaryProps {
  task: MonitorTask;
  outputs: AuditResult[];
  onOpenLogs: () => void;
}

export function TaskRuntimeSummary({ task, outputs, onOpenLogs }: TaskRuntimeSummaryProps) {
  const logCount = task.raw.logs?.length || task.logs.length;
  const latestLogTime = task.logs[0]?.time || task.updatedAt;
  const highRiskCount = outputs.filter((item) => String(item.risk_level || "").toLowerCase() === "high").length;

  return (
    <section className="task-runtime-summary" aria-label="任务运行摘要">
      <RuntimeBlock
        icon={<Download size={21} />}
        title="采集"
        status={getCrawlStatus(task)}
        items={[
          ["已抓取", String(task.metrics.crawled)],
          ["最近抓取", formatTimeOnly(task.updatedAt)]
        ]}
      />
      <RuntimeBlock
        icon={<Activity size={21} />}
        title="分析"
        status={getAnalysisStatus(task)}
        items={[
          ["已分析", String(task.metrics.analyzed)],
          ["待分析", String(task.metrics.waiting)]
        ]}
      />
      <RuntimeBlock
        icon={<FileText size={21} />}
        title="产出"
        status={getOutputStatus(task)}
        items={[
          ["风险产出", String(outputs.length)],
          ["高危", String(highRiskCount), "danger"]
        ]}
      />
      <div className="runtime-block runtime-log-block">
        <div className="runtime-icon" aria-hidden="true">
          <Clock3 size={21} />
        </div>
        <div className="runtime-content">
          <div className="runtime-heading">
            <strong>日志</strong>
            <span>·</span>
            <em>共 {logCount} 条</em>
          </div>
          <div className="runtime-inline-items">
            <span>最近日志</span>
            <strong>{formatTimeOnly(latestLogTime || task.updatedAt)}</strong>
          </div>
        </div>
        <div className="runtime-log-actions">
          <button type="button" onClick={onOpenLogs}>
            查看日志
          </button>
          <button type="button" onClick={onOpenLogs}>
            更多
          </button>
        </div>
      </div>
    </section>
  );
}

interface RuntimeBlockProps {
  icon: ReactNode;
  title: string;
  status: string;
  items: Array<[string, string, "danger"?]>;
}

function RuntimeBlock({ icon, title, status, items }: RuntimeBlockProps) {
  return (
    <div className="runtime-block">
      <div className="runtime-icon" aria-hidden="true">
        {icon}
      </div>
      <div className="runtime-content">
        <div className="runtime-heading">
          <strong>{title}</strong>
          <span>·</span>
          <em>{status}</em>
        </div>
        <div className="runtime-inline-items">
          {items.map(([label, value, tone]) => (
            <span key={label} className="runtime-pair">
              {label} <strong className={tone === "danger" ? "is-danger" : ""}>{value}</strong>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function getCrawlStatus(task: MonitorTask) {
  const status = String(task.raw.crawl_status || task.raw.status || "").toLowerCase();
  if (status.includes("pause") || task.status === "已暂停") {
    return "已暂停";
  }
  if (status.includes("fail") || task.status === "失败") {
    return "异常";
  }
  if (task.status === "已完成") {
    return "已完成";
  }
  return "运行中";
}

function getAnalysisStatus(task: MonitorTask) {
  const status = String(task.raw.analysis_status || task.raw.status || "").toLowerCase();
  if (status.includes("pause") || status.includes("stop")) {
    return "已暂停";
  }
  if (status.includes("fail") || task.status === "失败") {
    return "异常";
  }
  if (task.status === "已完成") {
    return "已完成";
  }
  return "运行中";
}

function getOutputStatus(task: MonitorTask) {
  if (task.status === "失败") {
    return "生成异常";
  }
  if (task.status === "已完成") {
    return "已生成";
  }
  return "持续生成";
}
