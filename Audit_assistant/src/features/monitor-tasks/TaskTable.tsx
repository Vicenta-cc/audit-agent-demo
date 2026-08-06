import type { MonitorTask } from "../../types/jobs";
import { TaskRow } from "./TaskRow";

interface TaskTableProps {
  tasks: MonitorTask[];
  openMenuId: string | null;
  onMenuChange: (taskId: string | null) => void;
  onOpenDetail: (task: MonitorTask, mode?: "detail" | "outputs") => void;
  onControl: (task: MonitorTask, action: string, label: string) => Promise<void>;
  onDelete: (task: MonitorTask) => void;
}

export function TaskTable({
  tasks,
  openMenuId,
  onMenuChange,
  onOpenDetail,
  onControl,
  onDelete
}: TaskTableProps) {
  return (
    <div className="task-table" role="table" aria-label="任务运行列表">
      <div className="task-table-head" role="row">
        <span className="task-table-head-cell task-info-head" role="columnheader">
          任务信息
        </span>
        <span className="task-table-head-cell task-run-head" role="columnheader">
          运行概览
        </span>
        <span className="task-table-head-cell task-update-head" role="columnheader">
          最近更新
        </span>
        <span className="task-table-head-cell task-action-head" role="columnheader">
          操作
        </span>
      </div>
      {tasks.map((task) => (
        <TaskRow
          key={task.id}
          task={task}
          menuOpen={openMenuId === task.id}
          onMenuToggle={() => onMenuChange(openMenuId === task.id ? null : task.id)}
          onMenuClose={() => onMenuChange(null)}
          onOpenDetail={onOpenDetail}
          onControl={onControl}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
