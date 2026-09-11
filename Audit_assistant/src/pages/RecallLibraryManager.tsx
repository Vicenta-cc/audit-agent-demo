import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ListPlus, Plus, Save, Search, Trash2 } from "lucide-react";
import { deleteResource, listResources, saveResource, type Resource, type LexiconContent, type LexiconEntry } from '../services/resourceLibrary';
import { ResourceDialog } from './ResourceDialog';
import type { RuleAssistantCandidateLexicon } from "../features/rule-assistant/types";
import type { RecallLibraryItem } from "../types/investigation";

type RecallQueryType = "关键词" | "标签";
type RecallStatusFilter = "all" | "enabled" | "disabled";

interface RecallTerm {
  id: string;
  primary: string;
  queryType: RecallQueryType;
  variants: string;
  enabled: boolean;
}

interface RecallLibraryDraft extends RecallLibraryItem {
  terms: RecallTerm[];
}

interface RecallLibraryManagerProps {
  onEditorStateChange?: (isEditing: boolean) => void;
  preview?: {
    library: RuleAssistantCandidateLexicon;
    mode?: "create" | "append";
    addedTermIds?: string[];
    onCancel: () => void;
    onAbort: () => void;
    onChange?: (library: RuleAssistantCandidateLexicon) => void;
    onCreate: (library: RuleAssistantCandidateLexicon) => void;
  };
}

const createEmptyLibrary = (): RecallLibraryDraft => ({
  id: `lexicon-${crypto.randomUUID()}`,
  name: "",
  category: "自定义黑话库",
  usageDescription: "",
  words: [],
  applicablePlatforms: ["抖音", "小红书", "微博", "快手"],
  status: "启用",
  updatedAt: "刚刚",
  wordCount: 0,
  terms: [
    {
      id: `term-${Date.now()}`,
      primary: "",
      queryType: "关键词",
      variants: "",
      enabled: true
    }
  ]
});

export function RecallLibraryManager({ onEditorStateChange, preview }: RecallLibraryManagerProps) {
  const [libraries, setLibraries] = useState<RecallLibraryDraft[]>([]);
  const [resources, setResources] = useState<Record<string, Resource<LexiconContent>>>({});
  const [loading, setLoading] = useState(!preview);
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<RecallLibraryDraft | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [saveAttempt, setSaveAttempt] = useState<{ signature: string; id: string } | null>(null);
  const libraryView = (r: Resource<LexiconContent>): RecallLibraryDraft => {
    const entries = r.content.entries;
    const terms = entries.filter(e => e.kind !== 'variant').map(e => ({ id: e.id, primary: e.term,
      queryType: e.kind === 'tag' ? '标签' as const : '关键词' as const,
      variants: entries.filter(v => v.kind === 'variant' && v.parent_id === e.id).map(v => v.term).join(', '), enabled: e.enabled }));
    return { id: r.id, name: r.content.title, category: r.content.risk_label || '自定义黑话库', usageDescription: r.content.description || '',
      words: terms.map(t => t.primary), terms, wordCount: terms.length, applicablePlatforms: ['小红书'], status: terms.some(t => t.enabled) ? '启用' : '停用', updatedAt: '已同步' };
  };
  const reload = async () => {
    setLoading(true);
    try {
      const rows = await listResources<LexiconContent>('lexicon');
      setResources(Object.fromEntries(rows.map(r => [r.id, r])));
      setLibraries(rows.map(libraryView));
      setValidationMessage('');
    } catch (e) { setValidationMessage(e instanceof Error ? e.message : '加载失败，请重试。'); }
    finally { setLoading(false); }
  };
  const confirmDelete = async () => {
    if (!deleteTarget || busy) return;
    setBusy(true); setDeleteError('');
    try {
      await deleteResource(resources[deleteTarget.id]);
      setLibraries(current => current.filter(r => r.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (e) { setDeleteError(e instanceof Error ? e.message : '删除失败'); }
    finally { setBusy(false); }
  };
  const [librarySearch, setLibrarySearch] = useState("");
  const [draft, setDraft] = useState<RecallLibraryDraft | null>(() => preview ? {
    id: preview.library.id,
    name: preview.library.name,
    category: preview.library.category,
    usageDescription: preview.library.description,
    words: preview.library.terms.map((term) => term.primary),
    applicablePlatforms: ["抖音", "小红书", "微博", "快手"],
    status: "启用",
    updatedAt: "刚刚",
    wordCount: preview.library.terms.length,
    terms: preview.library.terms.map((term) => ({ ...term }))
  } : null);
  const [termSearch, setTermSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<RecallStatusFilter>("all");
  const [validationMessage, setValidationMessage] = useState("");

  useEffect(() => { if (!preview) void reload(); }, []);

  useEffect(() => {
    if (!preview?.onChange || !draft) return;
    preview.onChange({
      id: draft.id,
      name: draft.name,
      category: draft.category,
      description: draft.usageDescription,
      terms: draft.terms.map((term) => ({ ...term }))
    });
  }, [draft]);

  useEffect(() => {
    document.querySelector<HTMLElement>(".kc-page-root > main")?.scrollTo({ top: 0, left: 0 });
  }, [draft?.id]);

  const filteredLibraries = useMemo(() => {
    const query = librarySearch.trim().toLowerCase();
    if (!query) return libraries;
    return libraries.filter((library) =>
      [library.name, library.category, library.usageDescription, ...library.words]
        .some((value) => value.toLowerCase().includes(query))
    );
  }, [libraries, librarySearch]);

  const filteredTerms = useMemo(() => {
    if (!draft) return [];
    const query = termSearch.trim().toLowerCase();
    return draft.terms.filter((term) => {
      const matchesSearch = !query || [term.primary, term.variants]
        .some((value) => value.toLowerCase().includes(query));
      const matchesStatus = statusFilter === "all"
        || (statusFilter === "enabled" && term.enabled)
        || (statusFilter === "disabled" && !term.enabled);
      return matchesSearch && matchesStatus;
    });
  }, [draft, statusFilter, termSearch]);

  const openEditor = (library?: RecallLibraryDraft) => {
    setDraft(library ? { ...library, terms: library.terms.map((term) => ({ ...term })) } : createEmptyLibrary());
    setTermSearch("");
    setStatusFilter("all");
    setValidationMessage("");
    onEditorStateChange?.(true);
  };

  const closeEditor = () => {
    if (preview) {
      preview.onCancel();
      return;
    }
    setDraft(null);
    setValidationMessage("");
    onEditorStateChange?.(false);
  };

  const updateDraft = (patch: Partial<RecallLibraryDraft>) => {
    setDraft((current) => current ? { ...current, ...patch } : current);
  };

  const updateTerm = (termId: string, patch: Partial<RecallTerm>) => {
    setDraft((current) => current ? {
      ...current,
      terms: current.terms.map((term) => term.id === termId ? { ...term, ...patch } : term)
    } : current);
  };

  const addTerm = () => {
    const term: RecallTerm = {
      id: `term-${Date.now()}`,
      primary: "",
      queryType: "关键词",
      variants: "",
      enabled: true
    };
    setDraft((current) => current ? { ...current, terms: [...current.terms, term] } : current);
    setTermSearch("");
    setStatusFilter("all");
  };

  const deleteTerm = (termId: string) => {
    setDraft((current) => current ? {
      ...current,
      terms: current.terms.filter((term) => term.id !== termId)
    } : current);
  };

  const saveLibrary = async () => {
    if (busy) return;
    if (!draft) return;
    const name = draft.name.trim();
    const usageDescription = draft.usageDescription.trim();
    const terms = draft.terms
      .map((term) => ({ ...term, primary: term.primary.trim(), variants: term.variants.trim() }))
      .filter((term) => term.primary);

    if (!name) {
      setValidationMessage("请填写词库名称。");
      return;
    }
    if (!terms.length) {
      setValidationMessage("请至少保留一个有效词条。");
      return;
    }

    const savedLibrary: RecallLibraryDraft = {
      ...draft,
      name,
      usageDescription,
      words: terms.map((term) => term.primary),
      terms,
      updatedAt: "刚刚",
      wordCount: libraries.some((library) => library.id === draft.id) ? draft.wordCount : terms.length
    };

    if (preview) {
      preview.onCreate({
        id: savedLibrary.id,
        name: savedLibrary.name,
        category: savedLibrary.category,
        description: savedLibrary.usageDescription,
        terms: savedLibrary.terms.map((term) => ({ ...term }))
      });
      return;
    }

    setBusy(true);
    try {
      const source = resources[draft.id];
      const previous = source?.content.entries || [];
      const entries: LexiconEntry[] = [];
      const usedEntryIds = new Set([...previous.map(e => e.id), ...terms.map(t => t.id)]);
      for (const term of terms) {
        const old = previous.find(e => e.id === term.id);
        const entry: LexiconEntry = { id: term.id, term: term.primary, kind: term.queryType === '标签' ? 'tag' : 'main', parent_id: '',
          enabled: term.enabled, platform: old?.platform || '全平台', match_type: old?.match_type || '黑话词', risk_level: old?.risk_level || '中', note: old?.note || '' };
        if ((entry.kind === 'tag') !== ['tag', '平台标签'].includes(entry.match_type)) entry.match_type = entry.kind === 'tag' ? '平台标签' : '黑话词';
        entries.push(entry);
        const variants = [...new Set(term.variants.split(/[,，\n]/).map(v => v.trim()).filter(Boolean))];
        if (entry.kind === 'tag' && variants.length) throw new Error(`标签“${term.primary}”不能带搜索词变体，请改为关键词或清空变体。`);
        variants.forEach((variant, index) => {
          const oldVariant = previous.find(e => e.kind === 'variant' && e.parent_id === term.id && e.term === variant);
          let suffix = 0;
          let newId = `variant-${term.id.slice(0, 100)}-${index}-${suffix}`;
          while (usedEntryIds.has(newId)) newId = `variant-${term.id.slice(0, 100)}-${index}-${++suffix}`;
          const id = oldVariant?.id || newId;
          usedEntryIds.add(id);
          entries.push({ ...entry, ...oldVariant, id, term: variant, kind: 'variant', parent_id: term.id,
            enabled: term.enabled && (oldVariant?.enabled ?? true) });
        });
      }
      const content: LexiconContent = { title: name, risk_label: source?.content.risk_label || draft.category, description: usageDescription, entries };
      const signature = JSON.stringify({ id: draft.id, content, version: source?.version || 0 });
      const operation = saveAttempt?.signature === signature ? saveAttempt.id : crypto.randomUUID();
      setSaveAttempt({ signature, id: operation });
      const saved = await saveResource('lexicon', draft.id, content, source?.version || 0, operation);
      setResources(current => ({ ...current, [saved.id]: saved }));
      setLibraries(current => [libraryView(saved), ...current.filter(r => r.id !== saved.id)]);
      setSaveAttempt(null);
      closeEditor();
    } catch (e) { setValidationMessage(e instanceof Error ? e.message : '保存失败'); }
    finally { setBusy(false); }
  };

  if (draft) {
    const isExistingLibrary = !preview && libraries.some((library) => library.id === draft.id);
    const isAppendPreview = preview?.mode === "append";
    const addedTermIds = new Set(preview?.addedTermIds || []);
    const remainingAddedTermCount = draft.terms.filter((term) => addedTermIds.has(term.id)).length;

    return (
      <div className="recall-editor-view">
        <div className="recall-editor-header">
          <button type="button" className="recall-back-button" disabled={busy} onClick={closeEditor}>
            <ArrowLeft size={15} />
            <span>{preview ? "返回词库对话" : "返回黑话库"}</span>
          </button>
          <span className="recall-header-divider" aria-hidden="true" />
          <div className="recall-editor-heading">
            <h2>{isAppendPreview ? "词库变更预览" : preview ? "词库结构预览" : isExistingLibrary ? "编辑黑话库" : "新建黑话库"}</h2>
            <p>{draft.name || "未命名黑话库"}</p>
          </div>
        </div>

        <fieldset disabled={busy} className="recall-editor-content" style={{ border: 0, margin: 0, minWidth: 0 }}>
          {isAppendPreview ? (
            <div className="recall-preview-notice" role="status">
              <ListPlus size={19} />
              <div>
                <strong>本次拟新增 {remainingAddedTermCount} 个词条</strong>
                <span>可在下方核对主词、表达变体与查询类型；确认前不会写入正式词库。</span>
              </div>
            </div>
          ) : null}
          <section className="recall-editor-panel recall-basic-panel" aria-labelledby="recall-basic-title">
            <h3 id="recall-basic-title">基础信息</h3>
            <label className="recall-form-field recall-name-field">
              <span>词库名称 <b>*</b></span>
              <input
                type="text"
                value={draft.name}
                onChange={(event) => updateDraft({ name: event.target.value })}
                placeholder="请输入黑话库名称"
              />
            </label>
            <label className="recall-form-field recall-description-field">
              <span>词库说明</span>
              <textarea
                value={draft.usageDescription}
                onChange={(event) => updateDraft({ usageDescription: event.target.value })}
                placeholder="说明该词库的适用范围和使用场景"
                rows={3}
              />
            </label>
          </section>

          <section className="recall-editor-panel recall-terms-panel" aria-labelledby="recall-terms-title">
            <div className="recall-panel-heading">
              <h3 id="recall-terms-title">词条管理</h3>
              <span>共 {draft.terms.length} 个词条（已筛选 {filteredTerms.length} 个）</span>
            </div>

            <div className="recall-term-toolbar">
              <label className="recall-search-box">
                <Search size={15} aria-hidden="true" />
                <input
                  type="search"
                  value={termSearch}
                  onChange={(event) => setTermSearch(event.target.value)}
                  placeholder="搜索主词或变体..."
                  aria-label="搜索主词或变体"
                />
              </label>
              <div className="recall-status-filter" aria-label="词条状态筛选">
                <span>状态</span>
                <div className="recall-status-options">
                  {([
                    ["all", "全部"],
                    ["enabled", "启用"],
                    ["disabled", "停用"]
                  ] as const).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      className={statusFilter === value ? "is-active" : ""}
                      aria-pressed={statusFilter === value}
                      onClick={() => setStatusFilter(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <button type="button" className="recall-primary-button" onClick={addTerm}>
                <Plus size={15} />
                <span>新建词条</span>
              </button>
            </div>

            <div className="recall-term-table" role="table" aria-label="召回词条列表">
              <div className="recall-term-table-head" role="row">
                <span>主词</span>
                <span>查询类型</span>
                <span>变体</span>
                <span>状态</span>
                <span>操作</span>
              </div>
              <div className="recall-term-table-body">
                {filteredTerms.length ? filteredTerms.map((term) => (
                  <div className={`recall-term-row${addedTermIds.has(term.id) ? " is-candidate" : ""}`} role="row" key={term.id}>
                    <div className="recall-term-primary">
                      <input
                        type="text"
                        value={term.primary}
                        onChange={(event) => updateTerm(term.id, { primary: event.target.value })}
                        placeholder="请输入主词"
                        aria-label={`主词 ${term.primary || "新词条"}`}
                      />
                      {addedTermIds.has(term.id) ? <span>新增</span> : null}
                    </div>
                    <div className="recall-query-type" aria-label={`${term.primary || "新词条"}查询类型`}>
                      {(["关键词", "标签"] as const).map((type) => (
                        <button
                          key={type}
                          type="button"
                          className={term.queryType === type ? "is-active" : ""}
                          aria-pressed={term.queryType === type}
                          onClick={() => updateTerm(term.id, { queryType: type })}
                        >
                          {type}
                        </button>
                      ))}
                    </div>
                    <input
                      type="text"
                      value={term.variants}
                      onChange={(event) => updateTerm(term.id, { variants: event.target.value })}
                      placeholder="使用逗号分隔多个变体"
                      aria-label={`${term.primary || "新词条"}变体`}
                    />
                    <div className="recall-term-status">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={term.enabled}
                        aria-label={`${term.enabled ? "停用" : "启用"}${term.primary || "新词条"}`}
                        className={`recall-switch${term.enabled ? " is-on" : ""}`}
                        onClick={() => updateTerm(term.id, { enabled: !term.enabled })}
                      >
                        <span />
                      </button>
                      <span className={term.enabled ? "is-enabled" : ""}>{term.enabled ? "启用" : "停用"}</span>
                    </div>
                    <button type="button" className="recall-delete-button" onClick={() => deleteTerm(term.id)}>
                      <Trash2 size={14} />
                      <span>删除</span>
                    </button>
                  </div>
                )) : (
                  <div className="recall-table-empty">没有符合当前条件的词条</div>
                )}
              </div>
            </div>
          </section>

          {validationMessage ? <div className="recall-validation" role="alert">{validationMessage}</div> : null}

          <div className="recall-editor-footer">
            {preview ? <button type="button" className="recall-secondary-button" onClick={preview.onAbort}>{isAppendPreview ? "取消本次新增" : "取消本次导入"}</button> : null}
            <button type="button" className="recall-secondary-button" disabled={busy} onClick={closeEditor}>{preview ? "返回词库对话" : "取消"}</button>
            <button type="button" className="recall-primary-button recall-save-button" disabled={busy} onClick={saveLibrary}>
              <Save size={15} />
              <span>{busy ? "正在保存…" : isAppendPreview ? "确认添加到词库" : preview ? "创建黑话库" : "保存配置"}</span>
            </button>
          </div>
        </fieldset>
      </div>
    );
  }

  return (
    <div className="recall-library-view">
      {deleteTarget ? <ResourceDialog title={`删除黑话库“${deleteTarget.name}”`} description="删除后不能再用于新任务。已有报告的历史快照保留。" busy={busy} error={deleteError} onCancel={() => setDeleteTarget(null)} onConfirm={confirmDelete} /> : null}
      {loading ? <p role="status">正在加载黑话库…</p> : null}
      {validationMessage ? <div role="alert" className="recall-validation">{validationMessage}<button type="button" onClick={() => void reload()}>重新加载</button></div> : null}
      <div className="recall-library-toolbar">
        <label className="recall-search-box recall-library-search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            value={librarySearch}
            onChange={(event) => setLibrarySearch(event.target.value)}
            placeholder="搜索黑话库..."
            aria-label="搜索黑话库"
          />
        </label>
        <button type="button" className="recall-primary-button" onClick={() => openEditor()}>
          <Plus size={15} />
          <span>新建黑话库</span>
        </button>
      </div>

      {filteredLibraries.length ? (
        <div className="recall-library-grid">
          {filteredLibraries.map((library) => (
            <article className="recall-library-card" key={library.id}>
              <div className="recall-library-card-head">
                <h3>{library.name}</h3>
                <span className={`recall-library-status${library.status === "启用" ? " is-enabled" : ""}`}>
                  {library.status}
                </span>
              </div>
              <p>{library.usageDescription}</p>
              <div className="recall-library-words">
                <strong>核心词汇（{library.wordCount} 个）：</strong>
                <div>
                  {library.words.slice(0, 10).map((word) => <span key={word}>{word}</span>)}
                </div>
              </div>
              <div className="recall-library-card-footer">
                <span>更新时间：{library.updatedAt}</span>
                <button type="button" className="recall-delete-button" aria-label={`删除${library.name}`} onClick={() => { setDeleteError(''); setDeleteTarget(library); }}><Trash2 size={14} />删除</button>
                <button
                  type="button"
                  aria-label={`管理${library.name}`}
                  onClick={() => openEditor(library)}
                >
                  管理
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="recall-library-empty">
          <Search size={20} />
          <strong>没有找到匹配的黑话库</strong>
          <span>请调整搜索内容后重试</span>
        </div>
      )}
    </div>
  );
}
