import { TaskQuota } from "../../components/feedback/TaskQuota";
import { useState } from "react";
import {
  Plus,
  Search,
  Users,
  Radio,
  BookOpen,
  Tags,
  PanelLeftClose,
  MoreVertical,
  ShieldAlert
} from "lucide-react";
import type { InvestigationSession } from "../../types/investigation";
import { sortInvestigationSessions } from "./sessionOrdering";

export type SubViewType = "users" | "crawler-accounts" | "audit-rules" | "slang-library" | "task-settings" | null;

interface InvestigationSidebarProps {
  sessions: InvestigationSession[];
  activeSessionId: string;
  onSelectSession: (id: string) => void;
  onNewInvestigation: () => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDeleteSession?: (id: string) => void;
  deletingSessionId?: string;
  onRenameSession?: (id: string, newTitle: string) => void;
  activeSubView?: SubViewType;
  onSelectSubView?: (view: SubViewType) => void;
}

export function InvestigationSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewInvestigation,
  isCollapsed,
  onToggleCollapse,
  onDeleteSession,
  deletingSessionId = "",
  onRenameSession,
  activeSubView = null,
  onSelectSubView
}: InvestigationSidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [activeMenuId, setActiveMenuId] = useState<string | null>(null);

  const filteredSessions = sortInvestigationSessions(sessions).filter((s) =>
    s.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleMenuClick = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    setActiveMenuId((prev) => (prev === id ? null : id));
  };

  const handleDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    setActiveMenuId(null);
    if (onDeleteSession) onDeleteSession(id);
  };

  const handleRename = (e: React.MouseEvent, session: InvestigationSession) => {
    e.stopPropagation();
    setActiveMenuId(null);
    const newName = window.prompt("请输入新的调查会话名称：", session.title);
    if (newName && newName.trim() && onRenameSession) {
      onRenameSession(session.id, newName.trim());
    }
  };

  return (
    <aside className={`inv-sidebar ${isCollapsed ? "is-collapsed" : ""}`} aria-label="调查会话侧边栏">
      <div className="inv-sidebar-header">
        <div className="inv-brand-row">
          <div className="inv-brand-icon">
            <ShieldAlert size={18} />
          </div>
          <span className="inv-brand-title">调查会话工作区</span>
        </div>

        <button
          type="button"
          className="mt-button mt-button-primary inv-new-btn"
          style={{ width: "100%", justifyContent: "center", minHeight: "38px" }}
          onClick={() => {
            if (onSelectSubView) onSelectSubView(null);
            onNewInvestigation();
          }}
        >
          <Plus size={16} />
          <span>新建调查</span>
        </button>

        <div className="inv-search-box">
          <Search size={14} className="text-muted" />
          <input
            type="text"
            placeholder="搜索调查会话..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      <div className="inv-sidebar-body">
        {/* Recent Sessions */}
        <div className="inv-sidebar-section">
          <div className="inv-section-label">最近会话</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            {filteredSessions.map((session) => {
              const isActive = activeSubView === null && session.id === activeSessionId;
              return (
                <div
                  key={session.id}
                  className={`inv-session-item ${isActive ? "is-active" : ""}`}
                  onClick={() => {
                    if (onSelectSubView) onSelectSubView(null);
                    onSelectSession(session.id);
                  }}
                >
                  <div className="inv-session-top">
                    <span className="inv-session-title">{session.title}</span>
                    <button
                      type="button"
                      className="inv-more-trigger"
                      onClick={(e) => handleMenuClick(e, session.id)}
                      title="更多操作"
                    >
                      <MoreVertical size={13} />
                    </button>
                  </div>

                  <div className="inv-session-meta">
                    <span className={`inv-status-pill st-${session.status}`} style={{ fontSize: "11px", padding: "1px 5px", height: "18px" }}>
                      {session.status}
                    </span>
                    <span className="inv-session-time">{session.updatedAt}</span>
                  </div>

                  {/* Context Menu Popup */}
                  {activeMenuId === session.id ? (
                    <div
                      style={{
                        position: "absolute",
                        right: "8px",
                        top: "28px",
                        background: "#ffffff",
                        border: "1px solid #cbd5e1",
                        borderRadius: "6px",
                        boxShadow: "0 4px 12px rgba(0,0,0,0.1)",
                        zIndex: 50,
                        padding: "4px",
                        display: "flex",
                        flexDirection: "column",
                        gap: "2px",
                        minWidth: "100px"
                      }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        style={{ padding: "6px 10px", fontSize: "12px", border: "none", background: "transparent", textAlign: "left", cursor: "pointer", borderRadius: "4px" }}
                        onClick={(e) => handleRename(e, session)}
                      >
                        重命名
                      </button>
                      <button
                        type="button"
                        style={{ padding: "6px 10px", fontSize: "12px", border: "none", background: "transparent", textAlign: "left", cursor: "pointer", borderRadius: "4px", color: "#dc2626" }}
                        onClick={(e) => handleDelete(e, session.id)}
                        disabled={Boolean(deletingSessionId)}
                      >
                        {deletingSessionId === session.id ? "正在删除…" : "删除会话"}
                      </button>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>

        {/* Business Navigation Links */}
        <div className="inv-sidebar-section">
          <div className="inv-section-label">业务入口</div>
          <div className="inv-business-nav">
            <button type="button" className={`inv-nav-item ${activeSubView === "task-settings" ? "is-active" : ""}`}
              onClick={() => onSelectSubView?.("task-settings")}><Radio size={15} /><span>采集与分析设置</span></button>
            <button
              type="button"
              className={`inv-nav-item ${activeSubView === "users" ? "is-active" : ""}`}
              onClick={() => onSelectSubView && onSelectSubView("users")}
            >
              <Users size={15} />
              <span>重点用户</span>
            </button>
            <button
              type="button"
              className={`inv-nav-item ${activeSubView === "crawler-accounts" ? "is-active" : ""}`}
              onClick={() => onSelectSubView && onSelectSubView("crawler-accounts")}
            >
              <Radio size={15} />
              <span>采集账号</span>
            </button>
            <button
              type="button"
              className={`inv-nav-item ${activeSubView === "audit-rules" ? "is-active" : ""}`}
              onClick={() => onSelectSubView && onSelectSubView("audit-rules")}
            >
              <BookOpen size={15} />
              <span>审核规则</span>
            </button>
            <button
              type="button"
              className={`inv-nav-item ${activeSubView === "slang-library" ? "is-active" : ""}`}
              onClick={() => onSelectSubView?.("slang-library")}
            >
              <Tags size={15} />
              <span>黑话库</span>
            </button>
          </div>
        </div>
      </div>

      {/* Footer */}
      {!isCollapsed ? <div className="inv-sidebar-footer"><TaskQuota /></div> : null}
      <div className="inv-sidebar-footer">
        <button type="button" className="inv-collapse-btn" onClick={onToggleCollapse}>
          <PanelLeftClose size={15} />
          <span>收起侧栏</span>
        </button>
      </div>
    </aside>
  );
}
