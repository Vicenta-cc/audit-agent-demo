import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, PencilLine, Play, Save, ShieldCheck } from "lucide-react";
import type { TaskDraft } from "../../types/investigation";
import type { ConfirmationPreview } from "../../types/investigationCreation";
import {
  buildConfirmationCardView,
  formatConfirmationBlockerMessage,
  formatCreationErrorMessage,
  parseInvestigationSearchTerms
} from "./confirmationView";

interface TaskConfirmationCardProps {
  draft: TaskDraft;
  onModifyConfig: () => void;
  onStartExecution: () => void;
  preview?: ConfirmationPreview;
  onUpdateSearchTerms?: (terms: string[]) => Promise<void>;
  isConfirming?: boolean;
  error?: string;
}

export function TaskConfirmationCard({
  draft,
  onModifyConfig,
  onStartExecution,
  preview,
  onUpdateSearchTerms,
  isConfirming = false,
  error = ""
}: TaskConfirmationCardProps) {
  const [searchTerms, setSearchTerms] = useState("");
  const [savedSearchTerms, setSavedSearchTerms] = useState("");
  const [isSavingTerms, setIsSavingTerms] = useState(false);
  const view = buildConfirmationCardView(draft, preview);
  const parsedSearchTerms = parseInvestigationSearchTerms(searchTerms);
  const normalizedSearchTerms = parsedSearchTerms.join("、");

  useEffect(() => {
    const resolvedSearchTerms = (preview?.resolved_search_terms || draft.keywords).join("、");
    setSearchTerms(resolvedSearchTerms);
    setSavedSearchTerms(resolvedSearchTerms);
  }, [draft.keywords, preview]);

  const saveTerms = async () => {
    if (!onUpdateSearchTerms) return;
    const terms = parseInvestigationSearchTerms(searchTerms);
    setIsSavingTerms(true);
    try {
      await onUpdateSearchTerms(terms);
      setSavedSearchTerms(terms.join("、"));
    } finally {
      setIsSavingTerms(false);
    }
  };

  return (
    <section className="task-final-confirm-card" aria-label="最终任务确认卡">
      <div className="task-final-heading">
        <div>
          <span className="task-final-eyebrow"><ShieldCheck size={14} aria-hidden="true" />配置确认</span>
          <h3>{view.title}</h3>
        </div>
        <span className="task-final-ready-state">等待确认</span>
      </div>

      <div className="task-final-fields">
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
              <button
                type="button"
                onClick={() => void saveTerms()}
                disabled={isSavingTerms || parsedSearchTerms.length === 0}
              >
                <Save size={14} aria-hidden="true" />
                {isSavingTerms ? "保存中" : "保存搜索词"}
              </button>
            </div>
          ) : (
            <strong>{view.termsOrCreator}</strong>
          )}
        </div>
        {preview ? (
          <>
            <div className="task-final-field">
              <span>审核规则</span>
              <strong>
                {view.ruleSet}
              </strong>
            </div>
            <div className="task-final-field">
              <span>召回方式</span>
              <strong>
                {view.recallStrategy}
              </strong>
            </div>
            <div className="task-final-field">
              <span>采集数量</span>
              <strong>预计最多采集 {preview.estimated_max_contents ?? preview.max_notes} 条；实际采集内容全部自动审核</strong>
            </div>
            {preview.max_posts_per_keyword !== undefined ? (
              <div className="task-final-field">
                <span>采集范围</span>
                <strong>每词最多 {preview.max_posts_per_keyword} 条；每帖最多 {preview.max_comments_per_post} 条评论；{preview.get_sub_comment ? "含楼中楼" : "仅一级评论"}</strong>
              </div>
            ) : null}
          </>
        ) : null}
      </div>

      {preview?.effective_parameters ? <p aria-label="本次使用的统一设置">
        本次使用统一采集与分析设置：{preview.effective_parameters.max_items_per_minute} 条/分钟，
        并发 {preview.effective_parameters.max_concurrency}，分析批次 {preview.effective_parameters.analysis_batch_size}；
        实际采集内容全部自动审核。
        如需修改，请到左侧业务入口“采集与分析设置”；启动后本任务参数保持不变。
      </p> : null}

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
              编辑/新增审核规则 <ExternalLink size={13} aria-hidden="true" />
            </a>
          ) : null}
        </div>
      ) : null}
      {error ? <p className="task-final-error" role="alert">{formatCreationErrorMessage(error)}</p> : null}

      <div className="task-final-actions">
        <button type="button" className="mt-button mt-button-secondary" onClick={onModifyConfig}>
          <PencilLine size={14} aria-hidden="true" />
          修改配置
        </button>
        <button
          type="button"
          className="mt-button mt-button-primary"
          onClick={onStartExecution}
          disabled={!view.canConfirm || isConfirming || isSavingTerms || (preview?.mode === "search" && normalizedSearchTerms !== savedSearchTerms)}
        >
          <Play size={14} aria-hidden="true" />
          {isConfirming ? "确认中" : "确认并开始调查"}
        </button>
      </div>
    </section>
  );
}
