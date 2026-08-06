import { FileText, RotateCcw, Save, Play, CheckCircle2 } from "lucide-react";
import type { TaskDraft, TaskSessionStatus } from "../../../types/conversationalTask";
import { platformOptionsList } from "../../../mocks/conversationalTaskMocks";

interface TaskDraftPanelProps {
  draft: TaskDraft;
  onReturnToEdit: () => void;
  onSaveDraft: () => void;
  onCreateAndStart: () => void;
}

export function TaskDraftPanel({
  draft,
  onReturnToEdit,
  onSaveDraft,
  onCreateAndStart
}: TaskDraftPanelProps) {
  const platformLabels = draft.platforms
    .map((code) => platformOptionsList.find((p) => p.code === code)?.label || code)
    .join("、");

  const canCreate = draft.confirmed && draft.platforms.length > 0;

  return (
    <aside className="conv-draft-panel" aria-label="当前任务草稿">
      <div className="conv-draft-header">
        <div className="conv-draft-title-row">
          <FileText size={18} className="text-primary" />
          <h2 className="conv-draft-title">当前任务草稿</h2>
        </div>
        <StatusBadge status={draft.status} />
      </div>

      <div className="conv-draft-body">
        <dl className="conv-draft-list">
          <div className="conv-draft-row">
            <dt>任务名称</dt>
            <dd className="font-semibold text-main">
              {draft.taskName || "待确认"}
            </dd>
          </div>

          <div className="conv-draft-row">
            <dt>任务类型</dt>
            <dd>{draft.taskType || "待确认"}</dd>
          </div>

          <div className="conv-draft-row">
            <dt>采集主题</dt>
            <dd>{draft.subject || "待确认"}</dd>
          </div>

          <div className="conv-draft-row">
            <dt>采集平台</dt>
            <dd>
              {draft.confirmed && platformLabels ? (
                <span className="conv-draft-tag platform-tag">
                  {platformLabels}
                </span>
              ) : (
                <span className="text-muted">待确认</span>
              )}
            </dd>
          </div>

          <div className="conv-draft-row">
            <dt>临时搜索词</dt>
            <dd>
              {draft.keywords.length > 0 ? (
                <span className="conv-draft-tag keyword-count-tag">
                  {draft.keywords.length} 个
                </span>
              ) : (
                <span className="text-muted">待确认</span>
              )}
            </dd>
          </div>

          <div className="conv-draft-row">
            <dt>研判方案</dt>
            <dd>
              {draft.policyName ? (
                <span className="conv-draft-policy-label">
                  {draft.policyName}
                </span>
              ) : (
                <span className="text-muted">待确认</span>
              )}
            </dd>
          </div>

          <div className="conv-draft-row">
            <dt>当前状态</dt>
            <dd>
              <span className={`conv-status-text status-${draft.status}`}>
                {draft.status}
              </span>
            </dd>
          </div>
        </dl>

        {draft.status === "已创建" ? (
          <div className="conv-created-notice">
            <CheckCircle2 size={16} />
            <span>任务已成功创建并提交至后台引擎执行</span>
          </div>
        ) : null}
      </div>

      <div className="conv-draft-footer">
        <button
          type="button"
          className="conv-btn conv-btn-ghost"
          onClick={onReturnToEdit}
          disabled={draft.status === "已创建"}
          title="返回重新调整平台或搜索词"
        >
          <RotateCcw size={15} />
          <span>返回修改</span>
        </button>

        <button
          type="button"
          className="conv-btn conv-btn-secondary"
          onClick={onSaveDraft}
          title="保存当前草稿"
        >
          <Save size={15} />
          <span>保存草稿</span>
        </button>

        <button
          type="button"
          className="conv-btn conv-btn-primary"
          disabled={!canCreate || draft.status === "已创建"}
          onClick={onCreateAndStart}
          title={canCreate ? "确认并立即启动采集任务" : "请先确定采集平台"}
        >
          <Play size={15} />
          <span>{draft.status === "已创建" ? "已创建" : "创建并开始"}</span>
        </button>
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
