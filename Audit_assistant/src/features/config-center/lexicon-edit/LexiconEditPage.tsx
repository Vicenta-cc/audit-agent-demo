import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
  X
} from "lucide-react";
import { Button } from "../../../components/common/Button";
import { DropdownMenu } from "../../../components/common/DropdownMenu";
import { IconButton } from "../../../components/common/IconButton";
import { ConfirmDialog } from "../../../components/feedback/ConfirmDialog";
import { EmptyState } from "../../../components/feedback/EmptyState";
import { ErrorState } from "../../../components/feedback/ErrorState";
import { LoadingState } from "../../../components/feedback/LoadingState";
import { Toast } from "../../../components/feedback/Toast";
import {
  deleteLexicon,
  formatNumber,
  saveLexicon
} from "../../../services/configCenter";
import type { ResearchPolicy, RiskLexicon } from "../../../types/configCenter";

type TermStatus = "enabled" | "disabled";
type TermStatusFilter = "全部" | TermStatus;
type QueryType = "keyword" | "tag";

interface LexiconTerm {
  id: string;
  mainTerm: string;
  variants: string[];
  queryType: QueryType;
  status: TermStatus;
  updatedAt: string;
}

interface LexiconEditDraft {
  name: string;
  terms: LexiconTerm[];
}

interface LexiconEditPageProps {
  lexicons: RiskLexicon[];
  policies: ResearchPolicy[];
  loading: boolean;
  error: string;
  onRefresh: () => void;
}

const statusOptions: Array<{ value: TermStatusFilter; label: string }> = [
  { value: "全部", label: "全部" },
  { value: "enabled", label: "启用" },
  { value: "disabled", label: "停用" }
];

const queryTypeLabels: Record<QueryType, string> = {
  keyword: "关键词",
  tag: "标签"
};

export function LexiconEditPage({ lexicons, policies, loading, error, onRefresh }: LexiconEditPageProps) {
  const navigate = useNavigate();
  const { lexiconId = "" } = useParams();
  const isNewLexicon = lexiconId === "new";
  const lexicon = lexicons.find((item) => item.id === lexiconId);
  const [draft, setDraft] = useState<LexiconEditDraft | null>(null);
  const [loadedVersion, setLoadedVersion] = useState<number | undefined>();
  const [loadedLexiconId, setLoadedLexiconId] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<TermStatusFilter>("全部");
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  useEffect(() => {
    if ((!lexicon && !isNewLexicon) || loadedLexiconId === lexiconId) {
      return;
    }
    setDraft(buildLexiconDraft(lexicon));
    setLoadedVersion(lexicon?.version);
    setLoadedLexiconId(lexiconId);
  }, [isNewLexicon, loadedLexiconId, lexicon, lexiconId]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const references = useMemo(() => {
    if (!lexicon && !isNewLexicon) {
      return [];
    }
    return policies.filter((policy) => policy.lexiconIds.includes(lexiconId));
  }, [isNewLexicon, lexicon, lexiconId, policies]);

  const filteredTerms = useMemo(() => {
    if (!draft) {
      return [];
    }
    const keyword = query.trim().toLowerCase();
    return draft.terms.filter((term) => {
      const matchesKeyword =
        !keyword ||
        [term.mainTerm, term.variants.join(" ")].join(" ").toLowerCase().includes(keyword);
      const matchesStatus = statusFilter === "全部" || term.status === statusFilter;
      return matchesKeyword && matchesStatus;
    });
  }, [draft, query, statusFilter]);

  if (loading && !draft) {
    return (
      <main className="lexicon-edit-page">
        <LoadingState label="正在加载黑话库..." />
      </main>
    );
  }

  if (error && !draft) {
    return (
      <main className="lexicon-edit-page">
        <ErrorState message={error} onRetry={onRefresh} />
      </main>
    );
  }

  if (!draft || (!lexicon && !isNewLexicon)) {
    return (
      <main className="lexicon-edit-page">
        <div className="lexicon-edit-empty">
          <EmptyState title="未找到黑话库" description="返回配置底座后重新选择需要编辑的黑话库。">
            <Button type="button" variant="primary" onClick={() => navigate("/config/lexicons")}>
              返回配置底座
            </Button>
          </EmptyState>
        </div>
      </main>
    );
  }

  const showToast = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const updateDraft = (patch: Partial<LexiconEditDraft>) => {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  };

  const updateTerm = (termId: string, patch: Partial<LexiconTerm>) => {
    updateDraft({
      terms: draft.terms.map((term) =>
        term.id === termId ? { ...term, ...patch, updatedAt: new Date().toISOString() } : term
      )
    });
  };

  const removeTerm = (termId: string) => {
    updateDraft({ terms: draft.terms.filter((term) => term.id !== termId) });
  };

  const addTerm = () => {
    const next: LexiconTerm = {
      id: `term_${Date.now()}`,
      mainTerm: "新词条",
      variants: [],
      queryType: "keyword",
      status: "enabled",
      updatedAt: new Date().toISOString()
    };
    updateDraft({ terms: [next, ...draft.terms] });
  };

  const saveDraft = async () => {
    if (!draft.name.trim()) {
      showToast("请输入词库名称", "info");
      return;
    }
    setSaving(true);
    try {
      const saved = await saveLexicon({
        expectedVersion: loadedVersion,
        id: isNewLexicon ? undefined : lexiconId,
        name: draft.name,
        terms: draft.terms.map((term) => ({
          id: term.id,
          mainTerm: term.mainTerm,
          variants: term.variants,
          queryType: term.queryType,
          enabled: term.status === "enabled"
        }))
      });
      setLoadedVersion(saved.category.version);
      await onRefresh();
      showToast("黑话库配置已保存");
      if (isNewLexicon) {
        navigate("/config/lexicons");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "保存失败", "info");
    } finally {
      setSaving(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (isNewLexicon) {
      navigate("/config/lexicons");
      return;
    }
    setDeleting(true);
    try {
      await deleteLexicon(lexiconId);
      showToast("黑话库已删除");
      navigate("/config/lexicons");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "删除失败", "info");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <main className="lexicon-edit-page">
      <header className="lexicon-edit-header">
        <button className="lexicon-edit-back" type="button" onClick={() => navigate("/config/lexicons")}>
          <ArrowLeft size={18} />
          返回配置底座
        </button>
        <div className="lexicon-edit-title-block">
          <h1>{draft.name || "未命名黑话库"}</h1>
          <div className="lexicon-edit-meta">
            <span>词条：{formatNumber(draft.terms.length)} 个</span>
          </div>
        </div>
        <DropdownMenu
          open={menuOpen}
          onClose={() => setMenuOpen(false)}
          trigger={
            <Button type="button" variant="secondary" onClick={() => setMenuOpen((open) => !open)}>
              更多
              <MoreHorizontal size={16} />
            </Button>
          }
        >
          <button className="is-danger" type="button" onClick={() => setDeleteOpen(true)}>
            <Trash2 size={15} />
            删除词库
          </button>
        </DropdownMenu>
      </header>

      <section className="lexicon-edit-shell">
        <div className="lexicon-edit-content">
          <div className="lexicon-card-list">
            <div className="lexicon-config-card lexicon-basic-card">
              <StepBasicInfo draft={draft} onChange={updateDraft} />
            </div>
            <div className="lexicon-config-card lexicon-terms-card">
              <StepTerms
                query={query}
                statusFilter={statusFilter}
                terms={filteredTerms}
                onQueryChange={setQuery}
                onStatusFilterChange={setStatusFilter}
                onCreateTerm={addTerm}
                onChangeTerm={updateTerm}
                onRemoveTerm={removeTerm}
              />
            </div>
            <div className="lexicon-config-card lexicon-references-card">
              <StepReferences policies={references} />
            </div>
          </div>

          <footer className="lexicon-edit-actionbar">
            <Button type="button" variant="secondary" onClick={() => navigate("/config/lexicons")}>
              取消
            </Button>
            <div className="lexicon-edit-actionbar-right">
              <Button type="button" variant="primary" onClick={() => void saveDraft()} disabled={!draft.name.trim() || saving}>
                {saving ? "保存中..." : "保存配置"}
              </Button>
            </div>
          </footer>
        </div>
      </section>

      <ConfirmDialog
        open={deleteOpen}
        title="删除黑话库"
        description={
          references.length
            ? `「${draft.name}」仍被 ${references.length} 个研判方案引用，删除后这些方案会失去该知识库引用。确认继续删除？`
            : `确认删除「${draft.name}」？删除后无法恢复。`
        }
        confirmText={deleting ? "删除中..." : "确认删除"}
        onCancel={() => {
          if (!deleting) {
            setDeleteOpen(false);
          }
        }}
        onConfirm={handleConfirmDelete}
      />

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}

interface StepBasicInfoProps {
  draft: LexiconEditDraft;
  onChange: (patch: Partial<LexiconEditDraft>) => void;
}

function StepBasicInfo({ draft, onChange }: StepBasicInfoProps) {
  return (
    <div className="lexicon-step-content">
      <StepHeading title="基础信息" />
      <div className="lexicon-basic-grid">
        <label className="lexicon-form-field">
          <span>词库名称</span>
          <input value={draft.name} maxLength={50} onChange={(event) => onChange({ name: event.target.value })} />
        </label>
      </div>
    </div>
  );
}

interface StepTermsProps {
  query: string;
  statusFilter: TermStatusFilter;
  terms: LexiconTerm[];
  onQueryChange: (value: string) => void;
  onStatusFilterChange: (value: TermStatusFilter) => void;
  onCreateTerm: () => void;
  onChangeTerm: (termId: string, patch: Partial<LexiconTerm>) => void;
  onRemoveTerm: (termId: string) => void;
}

function StepTerms({
  query,
  statusFilter,
  terms,
  onQueryChange,
  onStatusFilterChange,
  onCreateTerm,
  onChangeTerm,
  onRemoveTerm
}: StepTermsProps) {
  return (
    <div className="lexicon-step-content">
      <StepHeading title="词条管理" />
      <div className="lexicon-term-toolbar">
        <label className="lexicon-term-search">
          <Search size={18} />
          <input value={query} type="search" placeholder="搜索词条" onChange={(event) => onQueryChange(event.target.value)} />
        </label>
        <label className="lexicon-term-select">
          <span>状态：</span>
          <select value={statusFilter} onChange={(event) => onStatusFilterChange(event.target.value as TermStatusFilter)}>
            {statusOptions.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <Button type="button" variant="primary" onClick={onCreateTerm}>
          <Plus size={16} />
          新建词条
        </Button>
      </div>

      <div className="term-table" role="table" aria-label="词条管理表格">
        <div className="term-table-head" role="row">
          <span>主词</span>
          <span>查询类型</span>
          <span>变体</span>
          <span>状态</span>
          <span>操作</span>
        </div>
        {terms.length ? (
          terms.map((term) => (
            <div className="term-table-row" role="row" key={term.id}>
              <input
                className="term-main-input"
                aria-label={`${term.mainTerm}主词`}
                value={term.mainTerm}
                maxLength={50}
                onChange={(event) => onChangeTerm(term.id, { mainTerm: event.target.value })}
              />
              <div className="query-type-segmented" role="group" aria-label={`${term.mainTerm}查询类型`}>
                {(Object.keys(queryTypeLabels) as QueryType[]).map((queryType) => (
                  <button
                    key={queryType}
                    type="button"
                    aria-pressed={term.queryType === queryType}
                    className={term.queryType === queryType ? "is-selected" : ""}
                    onClick={() => onChangeTerm(term.id, { queryType })}
                  >
                    {queryTypeLabels[queryType]}
                  </button>
                ))}
              </div>
              <TokenEditor
                label="变体"
                values={term.variants}
                placeholder="添加变体后回车"
                compact
                onChange={(variants) => onChangeTerm(term.id, { variants })}
              />
              <label className="switch-control" title={term.status === "enabled" ? "已启用" : "已停用"}>
                <input
                  type="checkbox"
                  aria-label={`${term.mainTerm}状态`}
                  checked={term.status === "enabled"}
                  onChange={(event) => onChangeTerm(term.id, { status: event.target.checked ? "enabled" : "disabled" })}
                />
                <span />
              </label>
              <IconButton type="button" aria-label={`删除词条${term.mainTerm}`} onClick={() => onRemoveTerm(term.id)}>
                <Trash2 size={17} />
              </IconButton>
            </div>
          ))
        ) : (
          <div className="term-empty">暂无匹配词条</div>
        )}
      </div>
    </div>
  );
}

interface StepReferencesProps {
  policies: ResearchPolicy[];
}

function StepReferences({ policies }: StepReferencesProps) {
  const navigate = useNavigate();

  return (
    <div className="lexicon-step-content">
      <StepHeading title="引用方案" />
      <div className="reference-policy-table" role="table" aria-label="引用方案列表">
        <div className="reference-policy-head" role="row">
          <span>方案名称</span>
          <span>操作</span>
        </div>
        {policies.length ? (
          policies.map((policy) => (
            <div className="reference-policy-row" role="row" key={policy.id}>
              <strong title={policy.name}>{policy.name}</strong>
              <button className="config-text-button" type="button" onClick={() => navigate(`/config/policies/${policy.id}/edit`)}>
                查看方案
              </button>
            </div>
          ))
        ) : (
          <div className="reference-empty">当前暂无研判方案引用该词库</div>
        )}
      </div>
    </div>
  );
}

interface TokenEditorProps {
  label: string;
  values: string[];
  placeholder: string;
  compact?: boolean;
  onChange: (values: string[]) => void;
}

function TokenEditor({ label, values, placeholder, compact = false, onChange }: TokenEditorProps) {
  const [value, setValue] = useState("");

  const addValue = () => {
    const cleaned = value.trim();
    if (!cleaned || values.includes(cleaned)) {
      setValue("");
      return;
    }
    onChange([...values, cleaned]);
    setValue("");
  };

  return (
    <div className={`token-editor${compact ? " is-compact" : ""}`}>
      {compact ? null : <span>{label}</span>}
      <div className="token-editor-box">
        {values.map((item) => (
          <button key={item} type="button" onClick={() => onChange(values.filter((valueItem) => valueItem !== item))}>
            {item}
            <X size={13} />
          </button>
        ))}
        <input
          aria-label={label}
          value={value}
          placeholder={placeholder}
          onChange={(event) => setValue(event.target.value)}
          onBlur={addValue}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addValue();
            }
          }}
        />
      </div>
    </div>
  );
}

interface StepHeadingProps {
  title: string;
  description?: string;
}

function StepHeading({ title, description }: StepHeadingProps) {
  return (
    <div className="lexicon-step-heading">
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
    </div>
  );
}

function buildLexiconDraft(lexicon?: RiskLexicon): LexiconEditDraft {
  const terms: LexiconTerm[] = (lexicon?.terms || []).map((term) => ({
    id: term.id,
    mainTerm: term.mainTerm,
    variants: term.variants,
    queryType: term.queryType,
    status: term.enabled ? "enabled" : "disabled",
    updatedAt: lexicon?.updatedAt || new Date().toISOString()
  }));

  return {
    name: lexicon?.name || "",
    terms
  };
}
