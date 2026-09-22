import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Plus, Save, Trash2 } from "lucide-react";
import type { TaskDraft } from "../../types/investigation";
import type { DraftLexiconContent } from "../../types/investigationCreation";
import {
  buildLegacyStructuredLexicon,
  createLexiconEntry,
  normalizeLexiconContent,
  projectLexiconSearchTerms,
  validateLexiconDraft
} from "./lexiconDraft";

interface InvestigationKeywordEditorProps {
  draft: TaskDraft;
  content?: DraftLexiconContent | null;
  fallbackTerms: string[];
  canEdit: boolean;
  canSave: boolean;
  canPublish: boolean;
  onApply: (content: DraftLexiconContent) => Promise<boolean | void>;
  onSave: (content: DraftLexiconContent) => Promise<{ resourceId: string } | false | void>;
}

export function InvestigationKeywordEditor({
  draft,
  content,
  fallbackTerms,
  canEdit,
  canSave,
  canPublish,
  onApply,
  onSave
}: InvestigationKeywordEditorProps) {
  const legacyFallback = !content;
  const initial = useMemo(
    () => content || buildLegacyStructuredLexicon(draft.taskName, draft.subject, fallbackTerms),
    [content, draft.subject, draft.taskName, fallbackTerms]
  );
  const [working, setWorking] = useState<DraftLexiconContent>(initial);
  const [busy, setBusy] = useState<"apply" | "save" | "">("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setWorking(initial);
    setNotice("");
    setError("");
  }, [initial]);

  const mains = working.entries.filter((entry) => entry.kind === "main");
  const tags = working.entries.filter((entry) => entry.kind === "tag");
  const searchTerms = projectLexiconSearchTerms(working);
  const validationErrors = validateLexiconDraft(working);

  const updateEntry = (id: string, values: Record<string, string | boolean>) => {
    setWorking((current) => ({
      ...current,
      entries: current.entries.map((entry) => entry.id === id ? { ...entry, ...values } : entry)
    }));
    setNotice("");
  };

  const removeEntry = (id: string) => {
    setWorking((current) => ({
      ...current,
      entries: current.entries.filter((entry) => entry.id !== id && entry.parent_id !== id)
    }));
    setNotice("");
  };

  const addTheme = () => {
    setWorking((current) => ({
      ...current,
      entries: [...current.entries, createLexiconEntry("main")]
    }));
  };

  const addVariant = (parentId: string) => {
    setWorking((current) => ({
      ...current,
      entries: [...current.entries, createLexiconEntry("variant", parentId)]
    }));
  };

  const submit = async (mode: "apply" | "save") => {
    if (validationErrors.length) return;
    setBusy(mode);
    setError("");
    setNotice("");
    try {
      const normalized = normalizeLexiconContent(working);
      const result = mode === "apply" ? await onApply(normalized) : await onSave(normalized);
      if (result === false) return;
      setNotice(
        mode === "apply"
          ? "已应用到当前 Draft，实际搜索词已按启用变体更新。"
          : `已保存为正式黑话库${typeof result === "object" && result?.resourceId ? `（${result.resourceId}）` : ""}。`
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请重新读取最新 Draft 后再试。");
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="keyword-structure-editor">
      {legacyFallback ? (
        <div className="keyword-structure-warning">
          <AlertTriangle size={15} aria-hidden="true" />
          这是旧版扁平搜索词，系统已暂放到一个主题中；应用或保存前请确认归属。
        </div>
      ) : null}

      <label className="keyword-structure-meta">
        <span>词库名称</span>
        <input
          value={working.title}
          disabled={!canEdit}
          onChange={(event) => setWorking((current) => ({ ...current, title: event.target.value }))}
        />
      </label>

      <div className="keyword-structure-summary">
        <span>主题 {mains.length} 个</span>
        <span>实际搜索词 {searchTerms.length} 个</span>
      </div>

      <div className="keyword-theme-list">
        {mains.map((main) => {
          const variants = working.entries.filter(
            (entry) => entry.kind === "variant" && entry.parent_id === main.id
          );
          return (
            <section className="keyword-theme-card" key={main.id}>
              <div className="keyword-theme-heading">
                <label>
                  <span>主题主词</span>
                  <input
                    aria-label="主题主词"
                    value={main.term}
                    disabled={!canEdit}
                    onChange={(event) => updateEntry(main.id, { term: event.target.value })}
                  />
                </label>
                <label className="keyword-enabled-toggle">
                  <input
                    type="checkbox"
                    checked={main.enabled}
                    disabled={!canEdit}
                    onChange={(event) => updateEntry(main.id, { enabled: event.target.checked })}
                  />
                  启用主题
                </label>
                {canEdit ? (
                  <button type="button" aria-label={`删除主题 ${main.term}`} onClick={() => removeEntry(main.id)}>
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                ) : null}
              </div>

              <div className="keyword-variant-list">
                {variants.map((variant) => (
                  <div className="keyword-variant-row" key={variant.id}>
                    <input
                      aria-label="变体搜索词"
                      value={variant.term}
                      disabled={!canEdit}
                      placeholder="填写平台上真实出现的隐晦黑话"
                      onChange={(event) => updateEntry(variant.id, { term: event.target.value })}
                    />
                    <select
                      aria-label="变体所属主题"
                      value={variant.parent_id}
                      disabled={!canEdit}
                      onChange={(event) => updateEntry(variant.id, { parent_id: event.target.value })}
                    >
                      {mains.map((option) => (
                        <option key={option.id} value={option.id}>{option.term || "未命名主题"}</option>
                      ))}
                    </select>
                    <label className="keyword-enabled-toggle">
                      <input
                        type="checkbox"
                        checked={variant.enabled}
                        disabled={!canEdit}
                        onChange={(event) => updateEntry(variant.id, { enabled: event.target.checked })}
                      />
                      搜索
                    </label>
                    {canEdit ? (
                      <button type="button" aria-label={`删除变体 ${variant.term}`} onClick={() => removeEntry(variant.id)}>
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
              {canEdit ? (
                <button type="button" className="keyword-add-action" onClick={() => addVariant(main.id)}>
                  <Plus size={14} aria-hidden="true" /> 添加变体词
                </button>
              ) : null}
            </section>
          );
        })}
      </div>

      {canEdit ? (
        <button type="button" className="keyword-add-theme" onClick={addTheme}>
          <Plus size={14} aria-hidden="true" /> 添加主题
        </button>
      ) : null}

      {tags.length ? <p className="keyword-preserved-tags">另有 {tags.length} 个标签词会原样保留，但不参与搜索。</p> : null}

      <div className="keyword-search-preview">
        <strong>本次实际搜索词</strong>
        <div>{searchTerms.map((term) => <span key={term}>{term}</span>)}</div>
      </div>

      {validationErrors.length ? (
        <div className="keyword-structure-errors" role="alert">
          {validationErrors.map((message) => <p key={message}>{message}</p>)}
        </div>
      ) : null}
      {error ? <p className="keyword-structure-errors" role="alert">{error}</p> : null}
      {notice ? <p className="keyword-structure-success"><CheckCircle2 size={14} aria-hidden="true" />{notice}</p> : null}

      <div className="keyword-structure-actions">
        {canEdit ? (
          <button
            type="button"
            className="mt-button mt-button-secondary"
            disabled={Boolean(busy) || validationErrors.length > 0}
            onClick={() => void submit("apply")}
          >
            {busy === "apply" ? "应用中" : "应用到本次任务"}
          </button>
        ) : null}
        <button
          type="button"
          className="mt-button mt-button-primary"
          disabled={Boolean(busy) || validationErrors.length > 0 || legacyFallback || !canSave || !canPublish}
          onClick={() => void submit("save")}
        >
          <Save size={14} aria-hidden="true" />
          {busy === "save" ? "保存中" : "保存为共享黑话库"}
        </button>
      </div>
      {legacyFallback ? <p className="keyword-structure-footnote">旧版词必须先“应用到本次任务”，形成结构化 Draft 后才能正式保存。</p> : null}
      {!legacyFallback && !canSave ? <p className="keyword-structure-footnote">请先应用结构化编辑，再保存为正式黑话库。</p> : null}
      {!canPublish ? <p className="keyword-structure-footnote">当前账号可保留和使用 Draft 编辑结果；只有管理员可以发布共享黑话库。</p> : null}
      {!canEdit ? <p className="keyword-structure-footnote">任务已启动，搜索配置保持冻结；当前结构仍会保留在 Draft 中，管理员可另存供未来任务使用。</p> : null}
    </div>
  );
}
