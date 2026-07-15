import { MoreHorizontal, Pause, Play, RotateCw, Trash2 } from "lucide-react";
import { DropdownMenu } from "../../components/common/DropdownMenu";
import { Button } from "../../components/common/Button";
import type { MonitorTask } from "../../types/jobs";

interface TaskActionsMenuProps {
  task: MonitorTask;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onControl: (task: MonitorTask, action: string, label: string) => Promise<void>;
  onDelete: (task: MonitorTask) => void;
}

export function TaskActionsMenu({
  task,
  open,
  onToggle,
  onClose,
  onControl,
  onDelete
}: TaskActionsMenuProps) {
  const actions = task.raw.available_actions || {};
  const isPaused = task.status === "已暂停" || task.status === "已停止" || task.status === "已中断";

  return (
    <DropdownMenu
      open={open}
      onClose={onClose}
      trigger={
        <Button type="button" variant="secondary" size="small" aria-expanded={open} onClick={onToggle}>
          更多
          <MoreHorizontal size={15} />
        </Button>
      }
    >
      <button
        type="button"
        disabled={!actions.pause_crawl}
        onClick={() => void onControl(task, "pause_crawl", "暂停抓取")}
      >
        <Pause size={15} />
        暂停抓取
      </button>
      <button
        type="button"
        disabled={!actions.backfill_analysis || isPaused}
        onClick={() => void onControl(task, "backfill_analysis", "补抓")}
      >
        <RotateCw size={15} />
        补抓
      </button>
      <button
        type="button"
        disabled={!actions.stop_analysis}
        onClick={() => void onControl(task, "pause_analysis", "暂停分析")}
      >
        <Pause size={15} />
        暂停分析
      </button>
      <button
        type="button"
        disabled={!actions.backfill_analysis}
        onClick={() => void onControl(task, "backfill_analysis", "继续分析")}
      >
        <Play size={15} />
        继续分析
      </button>
      <button type="button" className="is-danger" disabled={!actions.delete_job} onClick={() => onDelete(task)}>
        <Trash2 size={15} />
        删除任务
      </button>
    </DropdownMenu>
  );
}
