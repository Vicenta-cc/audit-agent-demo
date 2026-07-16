import { Play } from "lucide-react";
import { Button } from "../../components/common/Button";
import { StatusTag } from "../../components/common/StatusTag";
import type { MonitorTask } from "../../types/jobs";
import { getCollectionStatusLabel, getCollectionStatusTone } from "./taskOutputUtils";

interface TaskOutputHeaderProps {
  task: MonitorTask;
  busy?: boolean;
  onBack: () => void;
  onOpenConfig: () => void;
  onOpenLogs: () => void;
  onContinueCollection: () => void;
}

export function TaskOutputHeader({
  task,
  busy = false,
  onBack,
  onOpenConfig,
  onOpenLogs,
  onContinueCollection
}: TaskOutputHeaderProps) {
  const collectionStatus = getCollectionStatusLabel(task);

  return (
    <header className="task-output-header">
      <div className="task-output-title-row">
        <div className="task-output-heading">
          <div className="task-output-title-line">
            <h1>任务产出 · {task.name}</h1>
            <StatusTag tone={getCollectionStatusTone(task)}>{collectionStatus}</StatusTag>
          </div>
          <div className="task-output-subtitle">
            {[task.sourceLabel, task.platformLabel, `引用方案：${task.referencePlan}`, `更新于 ${formatFullDateTime(task.updatedAt)}`].map(
              (item) => (
                <span className="task-output-subtitle-item" key={item}>
                  {item}
                </span>
              )
            )}
          </div>
        </div>
      </div>

      <div className="task-output-actions">
        <Button type="button" variant="primary" disabled={busy} onClick={onContinueCollection}>
          <Play size={16} />
          {collectionStatus === "采集已暂停" ? "继续采集" : "持续采集"}
        </Button>
        <span className="task-output-action-divider" aria-hidden="true" />
        <button type="button" className="task-output-action-link" onClick={onOpenConfig}>查看配置</button>
        <span className="task-output-action-divider" aria-hidden="true" />
        <button type="button" className="task-output-action-link" onClick={onOpenLogs}>查看日志</button>
        <span className="task-output-action-divider" aria-hidden="true" />
        <button type="button" className="task-output-action-link" onClick={onBack}>返回监控任务</button>
      </div>
    </header>
  );
}

function formatFullDateTime(value: string) {
  if (!value) return "--";
  return value.replace("T", " ").slice(0, 16);
}
