import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, PencilLine, Play, Save } from "lucide-react";
import type { TaskDraft } from "../../types/investigation";
import type { ConfirmationPreview } from "../../types/investigationCreation";
import {
  buildConfirmationCardView,
  formatConfirmationBlockerMessage,
  parseInvestigationSearchTerms
} from "./confirmationView";

interface TaskConfirmationCardProps {
  draft: TaskDraft;
  onModifyConfig: () => void;
  onStartExecution: () => void;
  onSaveDraft?: () => void;
  preview?: ConfirmationPreview;
  onUpdateSearchTerms?: (terms: string[]) => Promise<void>;
  isConfirming?: boolean;
  error?: string;
}

export function TaskConfirmationCard({
  draft,
  onModifyConfig,
  onStartExecution,
  onSaveDraft,
  preview,
  onUpdateSearchTerms,
  isConfirming = false,
  error = ""
}: TaskConfirmationCardProps) {
  const [isSaved, setIsSaved] = useState(false);
  const [searchTerms, setSearchTerms] = useState("");
  const [isSavingTerms, setIsSavingTerms] = useState(false);
  const view = buildConfirmationCardView(draft, preview);

  useEffect(() => {
    setSearchTerms((preview?.resolved_search_terms || draft.keywords).join("、"));
  }, [draft.keywords, preview]);

  const handleSaveDraft = () => {
    onSaveDraft?.();
    setIsSaved(true);
  };

  const saveTerms = async () => {
    if (!onUpdateSearchTerms) return;
    const terms = parseInvestigationSearchTerms(searchTerms);
    setIsSavingTerms(true);
    try {
      await onUpdateSearchTerms(terms);
    } finally {
      setIsSavingTerms(false);
    }
  };

  return (
    <section className="task-final-confirm-card" aria-label="最终任务确认卡">
      <h3>任务配置确认</h3>

      <div className="task-final-fields">
        <div className="task-final-field">
          <span>任务名称</span>
          <strong>{view.title}</strong>
        </div>
        <div className="task-final-field">
          <span>采集平台</span>
          <strong>{view.platform}</strong>
        </div>
        {preview ? (
          <div className="task-final-field">
            <span>调查目标</span>
            <strong>{view.objective}</strong>
          </div>
        ) : null}
        <div className="task-final-field">
          <span>{preview?.mode === "creator" ? "创作者主页" : "最终搜索词"}</span>
          {preview?.mode === "search" && onUpdateSearchTerms ? (
            <div className="task-final-term-editor">
              <textarea
                aria-label="最终搜索词"
                value={searchTerms}
                onChange={(event) => setSearchTerms(event.target.value)}
                rows={2}
              />
              <button type="button" onClick={() => void saveTerms()} disabled={isSavingTerms}>
                <Save size={14} aria-hidden="true" />
                {isSavingTerms ? "保存中" : "保存搜索词"}
              </button>
            </div>
          ) : (
            <strong>{view.termsOrCreator}</strong>
          )}
        </div>
        <div className="task-final-field">
          <span>Audit Policy</span>
          <strong>{view.auditPolicy}</strong>
        </div>
        {preview ? (
          <>
            <div className="task-final-field">
              <span>RuleSet</span>
              <strong>
                {view.ruleSet}
              </strong>
            </div>
            <div className="task-final-field">
              <span>召回策略</span>
              <strong>
                {view.recallStrategy}
              </strong>
            </div>
            <div className="task-final-field">
              <span>采集数量</span>
              <strong>最多 {preview.max_notes} 条（Pilot 固定）</strong>
            </div>
          </>
        ) : null}
      </div>

      {preview?.blockers.length ? (
        <div className="task-final-blockers" role="alert">
          {preview.blockers.map((blocker) => (
            <div key={`${blocker.code}-${blocker.resource_id}`}>
              <AlertTriangle size={15} aria-hidden="true" />
              <span>{formatConfirmationBlockerMessage(blocker.code, blocker.message)}</span>
            </div>
          ))}
          {view.rulesManagementUrl ? (
            <a href={view.rulesManagementUrl}>
              前往规则管理中心 <ExternalLink size={13} aria-hidden="true" />
            </a>
          ) : null}
        </div>
      ) : null}
      {error ? <p className="task-final-error" role="alert">{error}</p> : null}

      <div className="task-final-actions">
        <button
          type="button"
          className="task-save-draft-action"
          onClick={handleSaveDraft}
          disabled={Boolean(preview)}
        >
          {preview || isSaved ? "草稿已保存" : "保存草稿"}
        </button>
        <button type="button" className="mt-button mt-button-secondary" onClick={onModifyConfig}>
          <PencilLine size={14} aria-hidden="true" />
          修改配置
        </button>
        <button
          type="button"
          className="mt-button mt-button-primary"
          onClick={onStartExecution}
          disabled={!view.canConfirm || isConfirming}
        >
          <Play size={14} aria-hidden="true" />
          {isConfirming ? "确认中" : "确认并开始调查"}
        </button>
      </div>
    </section>
  );
}
