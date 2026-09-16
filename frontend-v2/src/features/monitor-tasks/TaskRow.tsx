import { FileVideo, RadioTower, UserRound, Workflow } from "lucide-react";
import type { MonitorTask } from "../../types/jobs";
import { Button } from "../../components/common/Button";
import { TaskActionsMenu } from "./TaskActionsMenu";

interface TaskRowProps {
  task: MonitorTask;
  menuOpen: boolean;
  onMenuToggle: () => void;
  onMenuClose: () => void;
  onOpenDetail: (task: MonitorTask, mode?: "detail" | "outputs") => void;
  onControl: (task: MonitorTask, action: string, label: string) => Promise<void>;
  onDelete: (task: MonitorTask) => void;
}

type PipelineTone = "success" | "warning" | "danger" | "neutral";

interface PipelineStatusMeta {
  label: "进行中" | "已暂停" | "已完成" | "未开始" | "失败" | "未知";
  tone: PipelineTone;
}

export function TaskRow({
  task,
  menuOpen,
  onMenuToggle,
  onMenuClose,
  onOpenDetail,
  onControl,
  onDelete
}: TaskRowProps) {
  const sourceIcon = getTaskSourceIcon(task);
  const SourceIcon = sourceIcon.Icon;
  const updatedAt = getUpdatedAtParts(task);

  return (
    <div className="task-table-row" role="row">
      <button
        className="task-info-cell task-table-cell"
        type="button"
        role="cell"
        onClick={() => onOpenDetail(task, "detail")}
      >
        <span className={`task-source-icon task-source-${sourceIcon.tone}`} aria-hidden="true">
          <SourceIcon size={22} />
        </span>
        <span className="task-info-text task-info-content">
          <strong className="task-name">{task.name}</strong>
          <span className="task-description">
            {task.sourceLabel} · {formatTaskObjectLabel(task)}
          </span>
        </span>
      </button>

      <TaskPipelineStatus task={task} />
      <TaskDataProgress task={task} />
      <TaskRiskSummary task={task} />

      <div className="task-updated-cell task-table-cell" role="cell">
        <span>{updatedAt.time}</span>
        <small>{updatedAt.dateTime}</small>
      </div>

      <div className="task-actions-cell task-table-cell" role="cell">
        <Button type="button" variant="primary" size="small" onClick={() => onOpenDetail(task, "outputs")}>
          查看产出
        </Button>
        <TaskActionsMenu
          task={task}
          open={menuOpen}
          onToggle={onMenuToggle}
          onClose={onMenuClose}
          onControl={onControl}
          onDelete={onDelete}
        />
      </div>
    </div>
  );
}

function getTaskSourceIcon(task: MonitorTask) {
  if (task.raw.input_type === "local_video" || task.source === "本地视频") {
    return { Icon: FileVideo, tone: "local" };
  }
  if (task.source === "重点用户") {
    return { Icon: UserRound, tone: "user" };
  }
  if (task.source === "直播接入") {
    return { Icon: RadioTower, tone: "live" };
  }
  return { Icon: Workflow, tone: "platform" };
}

function TaskPipelineStatus({ task }: { task: MonitorTask }) {
  // Use separate pipeline status fields when the backend sends them; otherwise fall back to the job status.
  const crawlStatus = getPipelineStatus(task.raw.crawl_status || task.raw.status);
  const analysisStatus = getPipelineStatus(task.raw.analysis_status || task.raw.status);

  return (
    <div className="task-running-group task-pipeline-cell task-table-cell" role="cell">
      <PipelineLine label="采集" status={crawlStatus} />
      <PipelineLine label="分析" status={analysisStatus} />
    </div>
  );
}

function PipelineLine({ label, status }: { label: string; status: PipelineStatusMeta }) {
  return (
    <div className="pipeline-line">
      <span className="pipeline-label">{label}</span>
      <span className={`pipeline-dot pipeline-${status.tone}`} aria-hidden="true" />
      <span className={`pipeline-tag pipeline-${status.tone}`}>{status.label}</span>
    </div>
  );
}

function TaskDataProgress({ task }: { task: MonitorTask }) {
  return (
    <div className="task-running-group task-progress-cell task-table-cell" role="cell">
      <MetricLine label="已抓取" value={task.metrics.crawled} />
      <MetricLine label="待分析" value={task.metrics.waiting} warning={task.metrics.waiting >= 50} />
    </div>
  );
}

function TaskRiskSummary({ task }: { task: MonitorTask }) {
  return (
    <div className="task-running-group task-risk-cell task-table-cell" role="cell">
      <MetricLine label="风险内容" value={task.metrics.outputs} suffix="条" />
      <MetricLine label="高危内容" value={task.metrics.highRisk} suffix="条" danger={task.metrics.highRisk > 0} />
    </div>
  );
}

function MetricLine({
  label,
  value,
  suffix = "",
  warning = false,
  danger = false
}: {
  label: string;
  value: number;
  suffix?: string;
  warning?: boolean;
  danger?: boolean;
}) {
  const toneClass = danger ? " is-danger" : warning ? " is-warning" : "";

  return (
    <div className="metric-line">
      <span>{label}</span>
      <strong className={toneClass}>{value}</strong>
      {suffix ? <em>{suffix}</em> : null}
    </div>
  );
}

function getPipelineStatus(value = ""): PipelineStatusMeta {
  const normalized = value.toLowerCase();

  if (normalized.includes("fail") || normalized.includes("error") || normalized.includes("interrupted")) {
    return { label: "失败", tone: "danger" };
  }
  if (normalized.includes("pause") || normalized.includes("stopped") || normalized.includes("stopping")) {
    return { label: "已暂停", tone: "warning" };
  }
  if (normalized.includes("complete") || normalized.includes("done") || normalized.includes("success")) {
    return { label: "已完成", tone: "neutral" };
  }
  if (normalized.includes("queued") || normalized.includes("pending") || normalized.includes("idle")) {
    return { label: "未开始", tone: "neutral" };
  }
  if (normalized.includes("running") || normalized === "running") {
    return { label: "进行中", tone: "success" };
  }

  return { label: "未知", tone: "warning" };
}

function getUpdatedAtParts(task: MonitorTask) {
  const display = task.updatedDisplay || formatDisplayDateTime(task.updatedAt);
  const match = display.match(/^(\d{2}-\d{2}) (\d{2}:\d{2})$/);

  if (match) {
    return {
      time: match[2],
      dateTime: display
    };
  }

  return {
    time: display || "刚刚",
    dateTime: display || "刚刚"
  };
}

function formatDisplayDateTime(value: string) {
  return value ? value.replace("T", " ").slice(5, 16) : "";
}

function formatTaskObjectLabel(task: MonitorTask) {
  if (task.raw.input_type === "local_video") {
    return compactText(task.raw.input_filename || task.objectLabel || "本地视频", 28);
  }

  if (task.source === "重点用户") {
    const actor = task.raw.creator_nickname || task.raw.creator_id || task.raw.creator_url || task.objectLabel;
    return `用户：${compactText(normalizeActorLabel(actor), 22)}`;
  }

  const keywords =
    task.raw.lexicon_keywords && task.raw.lexicon_keywords.length
      ? task.raw.lexicon_keywords.join("、")
      : task.raw.keyword || task.objectLabel.replace(/^关键词\s*/, "");

  return `关键词：${keywords || task.objectLabel}`;
}

function normalizeActorLabel(value: string) {
  const trimmed = String(value || "").trim().replace(/\/$/, "");
  if (!trimmed) {
    return "-";
  }
  if (trimmed.startsWith("用户尾号 ")) {
    return trimmed.replace("用户尾号 ", "");
  }
  if (trimmed.includes("/")) {
    return trimmed.slice(-10);
  }
  return trimmed;
}

function compactText(value: string, maxLength: number) {
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}
