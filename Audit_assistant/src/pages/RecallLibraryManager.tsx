import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ListPlus, Plus, Save, Search, Trash2 } from "lucide-react";
import { mockRecallLibraries } from "../mocks/investigationMocks";
import { gamblingLexiconTerms } from "../features/rule-assistant/mockData";
import { useRuleAssistantWorkspace } from "../features/rule-assistant/RuleAssistantWorkspaceContext";
import type { RuleAssistantLexicon } from "../features/rule-assistant/types";
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

const termSeeds: Record<string, RecallTerm[]> = {
  "recall-gambling": [
    ...gamblingLexiconTerms.map((term) => ({ ...term }))
  ],
  "recall-ethnicity": [
    { id: "ethnicity-1", primary: "民族通婚", queryType: "关键词", variants: "跨民族联姻, 异族婚姻, 民族融合通婚", enabled: true },
    { id: "ethnicity-2", primary: "维汉通婚", queryType: "关键词", variants: "维汉联姻, 维汉结亲", enabled: true },
    { id: "ethnicity-3", primary: "清真饮食争议", queryType: "标签", variants: "清真泛化, 饮食习惯差异, 专用餐具争议", enabled: true },
    { id: "ethnicity-4", primary: "地域偏见", queryType: "关键词", variants: "地域黑, 区域歧视, 地域标签化", enabled: false },
    { id: "ethnicity-5", primary: "民族风俗冲突", queryType: "标签", variants: "礼仪习惯冲突, 宗教风俗差异", enabled: true }
  ],
  "recall-fraud": [
    { id: "fraud-1", primary: "日赚百元兼职", queryType: "关键词", variants: "日赚数百, 在家兼职, 手机轻松日结", enabled: true },
    { id: "fraud-2", primary: "高额返利刷单", queryType: "关键词", variants: "刷单返利, 垫付佣金, 任务连刷", enabled: true },
    { id: "fraud-3", primary: "内幕炒股群", queryType: "标签", variants: "内幕消息, 导师带盘, 涨停妖股荐股", enabled: true },
    { id: "fraud-4", primary: "代办高额信用卡", queryType: "关键词", variants: "黑户包过, 大额提额, 强开微粒贷", enabled: false },
    { id: "fraud-5", primary: "零风险套利", queryType: "关键词", variants: "对冲套利, 平台漏洞提现, 稳赚不赔", enabled: true }
  ]
};

const buildLibraryDrafts = (
  workspaceLexicons: RuleAssistantLexicon[],
  appliedLexicons: Record<string, RuleAssistantCandidateLexicon>
): RecallLibraryDraft[] => {
  const mockById = new Map(mockRecallLibraries.map((library) => [library.id, library]));
  const isTransientDraft = (summary: RuleAssistantLexicon) => summary.isDraft === true || (
    summary.isDraft === undefined
    && (
      summary.id.startsWith("lexicon-draft-")
      || (summary.name === "新建黑话库" && summary.description === "正在通过对话创建")
    )
  );
  return workspaceLexicons.filter((summary) => (
    Boolean(appliedLexicons[summary.id]) || !isTransientDraft(summary)
  )).map((summary) => {
    const appliedLibrary = appliedLexicons[summary.id];
    const library = mockById.get(summary.id) || {
      id: summary.id,
      name: summary.name,
      category: "自定义黑话库",
      usageDescription: summary.description || "",
      words: [],
      applicablePlatforms: ["抖音", "小红书", "微博", "快手"],
      status: "启用" as const,
      updatedAt: "刚刚",
      wordCount: 0
    };
    const initialTerms = termSeeds[library.id] || library.words.map((word, index) => ({
      id: `${library.id}-${index}`,
      primary: word,
      queryType: "关键词" as const,
      variants: "",
      enabled: true
    }));
    const terms = appliedLibrary?.terms || initialTerms;
    const addedTermCount = appliedLibrary ? Math.max(0, terms.length - initialTerms.length) : 0;

    return {
      ...library,
      name: appliedLibrary?.name || summary.name,
      category: appliedLibrary?.category || library.category,
      usageDescription: appliedLibrary?.description || summary.description || library.usageDescription,
      words: terms.map((term) => term.primary),
      wordCount: library.wordCount + addedTermCount,
      terms: terms.map((term) => ({ ...term }))
    };
  });
};

const createEmptyLibrary = (): RecallLibraryDraft => ({
  id: `recall-${Date.now()}`,
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
  const { appliedLexicons, lexicons: workspaceLexicons, upsertLexicon } = useRuleAssistantWorkspace();
  const [libraries, setLibraries] = useState<RecallLibraryDraft[]>(() => buildLibraryDrafts(workspaceLexicons, appliedLexicons));
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

  useEffect(() => {
    const nextLibraries = buildLibraryDrafts(workspaceLexicons, appliedLexicons);
    setLibraries((current) => {
      const currentById = new Map(current.map((library) => [library.id, library]));
      return nextLibraries.map((nextLibrary) => {
        if (appliedLexicons[nextLibrary.id]) return nextLibrary;
        const summary = workspaceLexicons.find((item) => item.id === nextLibrary.id);
        const existing = currentById.get(nextLibrary.id);
        if (existing) {
          return {
            ...existing,
            name: summary?.name || nextLibrary.name,
            usageDescription: summary?.description || existing.usageDescription
          };
        }
        return nextLibrary;
      });
    });
  }, [appliedLexicons, workspaceLexicons]);

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

  const saveLibrary = () => {
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

    setLibraries((current) => current.some((library) => library.id === savedLibrary.id)
      ? current.map((library) => library.id === savedLibrary.id ? savedLibrary : library)
      : [savedLibrary, ...current]);
    upsertLexicon({
      id: savedLibrary.id,
      name: savedLibrary.name,
      category: savedLibrary.category,
      description: savedLibrary.usageDescription,
      terms: savedLibrary.terms.map((term) => ({ ...term }))
    });
    closeEditor();
  };

  if (draft) {
    const isExistingLibrary = !preview && libraries.some((library) => library.id === draft.id);
    const isAppendPreview = preview?.mode === "append";
    const addedTermIds = new Set(preview?.addedTermIds || []);
    const remainingAddedTermCount = draft.terms.filter((term) => addedTermIds.has(term.id)).length;

    return (
      <div className="recall-editor-view">
        <div className="recall-editor-header">
          <button type="button" className="recall-back-button" onClick={closeEditor}>
            <ArrowLeft size={15} />
            <span>{preview ? "返回词库对话" : "返回黑话库"}</span>
          </button>
          <span className="recall-header-divider" aria-hidden="true" />
          <div className="recall-editor-heading">
            <h2>{isAppendPreview ? "词库变更预览" : preview ? "词库结构预览" : isExistingLibrary ? "编辑黑话库" : "新建黑话库"}</h2>
            <p>{draft.name || "未命名黑话库"}</p>
          </div>
        </div>

        <div className="recall-editor-content">
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
            <button type="button" className="recall-secondary-button" onClick={closeEditor}>{preview ? "返回词库对话" : "取消"}</button>
            <button type="button" className="recall-primary-button recall-save-button" onClick={saveLibrary}>
              <Save size={15} />
              <span>{isAppendPreview ? "确认添加到词库" : preview ? "创建黑话库" : "保存配置"}</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="recall-library-view">
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
