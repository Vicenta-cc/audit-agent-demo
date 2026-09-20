import { MoreHorizontal, Pause, Play, RotateCw, Square, Trash2 } from "lucide-react";
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
  return (
    <DropdownMenu
      open={open}
      onClose={onClose}
      trigger={
        <Button
          type="button"
          className="task-more-button"
          variant="secondary"
          size="small"
          aria-label="更多操作"
          aria-expanded={open}
          onClick={onToggle}
        >
          <MoreHorizontal size={18} />
        </Button>
      }
    >
      {actions.pause_crawl ? <button
        type="button"
        disabled={!actions.pause_crawl}
        onClick={() => void onControl(task, "pause_crawl", "暂停抓取")}
      >
        <Pause size={15} />
        暂停抓取
      </button> : null}
      {actions.resume_crawl ? <button
        type="button"
        disabled={!actions.resume_crawl}
        onClick={() => void onControl(task, "resume_crawl", "继续采集")}
      >
        <RotateCw size={15} />
        继续采集
      </button> : null}
      {actions.pause_analysis ? <button
        type="button"
        disabled={!actions.pause_analysis}
        onClick={() => void onControl(task, "pause_analysis", "暂停分析")}
      >
        <Pause size={15} />
        暂停分析
      </button> : null}
      {actions.resume_analysis ? <button
        type="button"
        disabled={!actions.resume_analysis}
        onClick={() => void onControl(task, "resume_analysis", "继续分析")}
      >
        <Play size={15} />
        继续分析
      </button> : null}
      {actions.end_task || actions.ending ? <button type="button" disabled={!actions.end_task}
        onClick={() => void onControl(task, "stop_all", "结束任务")}><Square size={14} />{actions.ending ? "正在结束…" : "结束任务"}</button> : null}
      <button type="button" className="is-danger" disabled={!actions.delete_job} onClick={() => onDelete(task)}>
        <Trash2 size={15} />
        删除任务
      </button>
    </DropdownMenu>
  );
}
