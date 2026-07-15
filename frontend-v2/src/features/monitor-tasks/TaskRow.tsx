import { Database, Eye, FileSearch, RadioTower, Upload, UserRound } from "lucide-react";
import type { MonitorTask } from "../../types/jobs";
import { Button } from "../../components/common/Button";
import { TaskActionsMenu } from "./TaskActionsMenu";
import { TaskMetrics } from "./TaskMetrics";
import { TaskStatus } from "./TaskStatus";

interface TaskRowProps {
  task: MonitorTask;
  menuOpen: boolean;
  onMenuToggle: () => void;
  onMenuClose: () => void;
  onOpenDetail: (task: MonitorTask, mode?: "detail" | "outputs") => void;
  onControl: (task: MonitorTask, action: string, label: string) => Promise<void>;
  onDelete: (task: MonitorTask) => void;
}

const sourceIcon = {
  平台抓取: FileSearch,
  直播接入: RadioTower,
  重点用户: UserRound,
  本地视频: Upload
};

export function TaskRow({
  task,
  menuOpen,
  onMenuToggle,
  onMenuClose,
  onOpenDetail,
  onControl,
  onDelete
}: TaskRowProps) {
  const Icon = sourceIcon[task.source] || Database;

  return (
    <div className="task-table-row" role="row">
      <button className="task-info-cell" type="button" onClick={() => onOpenDetail(task, "detail")}>
        <span className={`task-source-icon source-${task.source}`} aria-hidden="true">
          <Icon size={22} />
        </span>
        <span className="task-info-text">
          <strong>{task.name}</strong>
          <span>
            {task.sourceLabel} · {task.platformLabel} · {task.objectLabel}
          </span>
          <small>引用：{task.referencePlan} · 更新于 {task.updatedDisplay}</small>
        </span>
      </button>

      <TaskMetrics metrics={task.metrics} />
      <TaskStatus task={task} />

      <div className="task-actions-cell">
        <Button type="button" variant="primary" size="small" onClick={() => onOpenDetail(task, "outputs")}>
          <Eye size={15} />
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
