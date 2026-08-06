import { StatusTag } from "../../components/common/StatusTag";
import type { MonitorTask } from "../../types/jobs";

interface TaskStatusProps {
  task: MonitorTask;
}

export function TaskStatus({ task }: TaskStatusProps) {
  return (
    <div className="task-status-cell">
      <StatusTag tone={task.statusTone}>{task.status}</StatusTag>
      {task.statusTimeLabel ? <span>{task.statusTimeLabel}</span> : null}
    </div>
  );
}
