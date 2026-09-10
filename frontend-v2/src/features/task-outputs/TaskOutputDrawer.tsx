import { useEffect } from "react";
import type { ConfigHistoryItem, TaskConfig, TaskDrawerType, TaskLog } from "../../types/taskOutputs";
import { TaskConfigDrawer } from "./TaskConfigDrawer";
import { TaskLogDrawer } from "./TaskLogDrawer";

interface TaskOutputDrawerProps {
  activeDrawer: TaskDrawerType;
  config: TaskConfig;
  configHistory: ConfigHistoryItem[];
  logs: TaskLog[];
  taskId: string;
  onClose: () => void;
}

export function TaskOutputDrawer({ activeDrawer, config, configHistory, logs, taskId, onClose }: TaskOutputDrawerProps) {
  useEffect(() => {
    if (!activeDrawer) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeDrawer, onClose]);

  if (!activeDrawer) return null;

  return (
    <div className="output-drawer-backdrop" role="presentation" onClick={onClose}>
      <aside
        className={`output-side-drawer output-side-drawer-${activeDrawer}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="output-drawer-title"
        onClick={(event) => event.stopPropagation()}
      >
        {activeDrawer === "config" ? (
          <TaskConfigDrawer config={config} history={configHistory} onClose={onClose} />
        ) : (
          <TaskLogDrawer taskId={taskId} logs={logs} onClose={onClose} />
        )}
      </aside>
    </div>
  );
}
