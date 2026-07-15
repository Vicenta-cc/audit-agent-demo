import { ArrowLeft, ChevronRight, Download, FileText, MoreHorizontal, Pause, Play, RotateCcw, Settings, Square } from "lucide-react";
import { useState } from "react";
import { Button } from "../../components/common/Button";
import { DropdownMenu } from "../../components/common/DropdownMenu";
import { StatusTag } from "../../components/common/StatusTag";
import type { MonitorTask } from "../../types/jobs";
import { getPlanVersion } from "./taskOutputUtils";

interface TaskOutputHeaderProps {
  task: MonitorTask;
  busy?: boolean;
  onBack: () => void;
  onOpenConfig: () => void;
  onControl: (action: string, label: string) => Promise<void>;
}

export function TaskOutputHeader({ task, busy = false, onBack, onOpenConfig, onControl }: TaskOutputHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const isPaused = task.status === "已暂停" || task.status === "已停止" || task.status === "已中断";
  const pauseLabel = isPaused ? "继续采集" : "暂停采集";
  const pauseAction = isPaused ? "backfill_analysis" : "pause_crawl";
  const canPauseOrResume = isPaused ? task.raw.available_actions?.backfill_analysis : task.raw.available_actions?.pause_crawl;

  const exportTask = () => {
    setMenuOpen(false);
    const blob = new Blob([JSON.stringify(task.raw, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${task.id}-task.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <header className="task-output-header">
      <div className="task-output-title-row">
        <button className="task-output-back-icon" type="button" aria-label="返回监控任务" onClick={onBack}>
          <ArrowLeft size={21} />
        </button>

        <div className="task-output-heading">
          <div className="task-output-title-line">
            <h1>任务产出 · {task.name}</h1>
            <StatusTag tone={task.statusTone}>{task.status}</StatusTag>
          </div>
          <div className="task-output-subtitle">
            <span>{task.sourceLabel}</span>
            <span>{task.platformLabel}</span>
            <span>引用方案：{task.referencePlan} {getPlanVersion(task)}</span>
            <span>更新于 {task.updatedDisplay}</span>
            <button type="button" className="task-output-text-link" onClick={onOpenConfig}>
              查看配置 <ChevronRight size={15} />
            </button>
          </div>
        </div>
      </div>

      <div className="task-output-actions">
        <Button type="button" variant="secondary" onClick={onBack}>
          <ArrowLeft size={16} />
          返回监控任务
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={busy || !canPauseOrResume}
          onClick={() => void onControl(pauseAction, pauseLabel)}
        >
          {isPaused ? <Play size={16} /> : <Pause size={16} />}
          {pauseLabel}
        </Button>
        <Button
          type="button"
          variant="primary"
          disabled={busy || !task.raw.available_actions?.backfill_analysis}
          onClick={() => void onControl("backfill_analysis", "补抓近期内容")}
        >
          <RotateCcw size={16} />
          补抓近期内容
        </Button>
        <DropdownMenu
          open={menuOpen}
          onClose={() => setMenuOpen(false)}
          trigger={
            <Button type="button" variant="secondary" aria-expanded={menuOpen} onClick={() => setMenuOpen((value) => !value)}>
              更多
              <MoreHorizontal size={16} />
            </Button>
          }
        >
          <button
            type="button"
            onClick={() => {
              setMenuOpen(false);
              onOpenConfig();
            }}
          >
            <Settings size={15} />
            更新审核策略
          </button>
          <button type="button" onClick={() => setMenuOpen(false)}>
            <FileText size={15} />
            查看任务信息
          </button>
          <button type="button" onClick={exportTask}>
            <Download size={15} />
            导出任务数据
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setMenuOpen(false);
              void onControl("stop_all", "停止任务");
            }}
          >
            <Square size={15} />
            停止任务
          </button>
          <button type="button" className="is-danger" disabled>
            删除任务
          </button>
        </DropdownMenu>
      </div>
    </header>
  );
}
