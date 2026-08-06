import { Plus, MessageSquare } from "lucide-react";
import type { TaskSession, TaskSessionStatus } from "../../../types/conversationalTask";

interface SessionListSidebarProps {
  sessions: TaskSession[];
  activeSessionId: string;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
}

export function SessionListSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewSession
}: SessionListSidebarProps) {
  return (
    <aside className="conv-sidebar" aria-label="任务会话列表">
      <div className="conv-sidebar-header">
        <div className="conv-sidebar-title-row">
          <h2 className="conv-sidebar-title">任务会话</h2>
          <span className="conv-sidebar-count">{sessions.length}</span>
        </div>
        <button
          className="conv-new-button"
          type="button"
          onClick={onNewSession}
        >
          <Plus size={16} />
          <span>新建任务</span>
        </button>
      </div>

      <div className="conv-session-list" role="list">
        {sessions.map((session) => {
          const isActive = session.id === activeSessionId;
          return (
            <div
              key={session.id}
              role="listitem"
              tabIndex={0}
              className={`conv-session-item${isActive ? " is-active" : ""}`}
              onClick={() => onSelectSession(session.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelectSession(session.id);
                }
              }}
            >
              <div className="conv-session-item-top">
                <span className="conv-session-item-icon" aria-hidden="true">
                  <MessageSquare size={15} />
                </span>
                <span className="conv-session-item-title" title={session.title}>
                  {session.title}
                </span>
              </div>

              <div className="conv-session-item-meta">
                <StatusBadge status={session.draft.status} />
                <time className="conv-session-item-time">{session.updatedAt}</time>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function StatusBadge({ status }: { status: TaskSessionStatus }) {
  const statusClasses: Record<TaskSessionStatus, string> = {
    配置中: "status-configuring",
    等待确认: "status-pending",
    已创建: "status-created"
  };

  return (
    <span className={`conv-status-badge ${statusClasses[status] || ""}`}>
      {status}
    </span>
  );
}
