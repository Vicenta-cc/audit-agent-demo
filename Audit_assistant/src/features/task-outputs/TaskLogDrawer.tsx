import { X } from "lucide-react";
import { useMemo, useState } from "react";
import { IconButton } from "../../components/common/IconButton";
import type { TaskLog, TaskLogLevel } from "../../types/taskOutputs";

interface TaskLogDrawerProps {
  taskId: string;
  logs: TaskLog[];
  onClose: () => void;
}

export function TaskLogDrawer({ taskId, logs, onClose }: TaskLogDrawerProps) {
  void taskId;
  const [level, setLevel] = useState<"全部" | TaskLogLevel>("全部");
  const [date, setDate] = useState("");
  const visibleLogs = useMemo(() => logs.filter((item) => {
    return (level === "全部" || item.level === level) && (!date || item.date === date);
  }), [date, level, logs]);

  return (
    <>
      <header className="output-drawer-header">
        <h2 id="output-drawer-title">运行日志</h2>
        <IconButton type="button" aria-label="关闭运行日志" onClick={onClose}><X size={19} /></IconButton>
      </header>
      <div className="log-drawer-toolbar">
        <div className="log-level-tabs" role="tablist" aria-label="日志等级筛选">
          {(["全部", "INFO", "WARN", "ERROR"] as const).map((item) => (
            <button type="button" role="tab" aria-selected={level === item} className={level === item ? "is-active" : ""} onClick={() => setLevel(item)} key={item}>{item}</button>
          ))}
        </div>
        <label className="log-date-filter">
          <span>时间</span>
          <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
        </label>
      </div>
      <div className="task-log-timeline">
        {visibleLogs.length ? visibleLogs.map((item) => (
          <article className="task-log-item" key={item.id}>
            <time>{item.time}</time>
            <span className={`task-log-level level-${item.level.toLowerCase()}`}>{item.level}</span>
            <div className="task-log-content">
              <p>{item.content}</p>
              {(item.errorCode || item.reason || item.action || item.retryable != null) && (
                <dl className="task-log-diagnostics">
                  {item.errorCode && <><dt>代码</dt><dd>{item.errorCode}</dd></>}
                  {item.reason && <><dt>原因</dt><dd>{item.reason}</dd></>}
                  {item.action && <><dt>处理</dt><dd>{item.action}</dd></>}
                  {item.retryable != null && <><dt>可重试</dt><dd>{item.retryable ? "是" : "否"}</dd></>}
                </dl>
              )}
            </div>
          </article>
        )) : <div className="task-log-empty">当前筛选下没有日志</div>}
      </div>
    </>
  );
}
