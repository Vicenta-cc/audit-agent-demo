import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Download,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
  X
} from "lucide-react";
import { Button } from "../../../components/common/Button";
import { DropdownMenu } from "../../../components/common/DropdownMenu";
import { IconButton } from "../../../components/common/IconButton";
import { StatusTag } from "../../../components/common/StatusTag";
import { ConfirmDialog } from "../../../components/feedback/ConfirmDialog";
import { EmptyState } from "../../../components/feedback/EmptyState";
import { ErrorState } from "../../../components/feedback/ErrorState";
import { LoadingState } from "../../../components/feedback/LoadingState";
import { Toast } from "../../../components/feedback/Toast";
import {
  deleteLexicon,
  formatCompactDateTime,
  formatNumber,
  getPolicyStatusLabel,
  getPolicyStatusTone,
  policyCategoryOptions
} from "../../../services/configCenter";
import type { LexiconCategory, PolicyCategory, ResearchPolicy, RiskLexicon } from "../../../types/configCenter";

type StepId = 1 | 2 | 3 | 4;
type TermStatus = "enabled" | "disabled";
type TermStatusFilter = "全部" | TermStatus;
type MatchMethod = "精确" | "模糊" | "正则";
type RiskWeight = "低" | "中" | "高";
type PlatformName = "抖音" | "小红书" | "快手" | "其他平台";

interface LexiconTerm {
  id: string;
  mainTerm: string;
  variants: string[];
  matchMethod: MatchMethod;
  riskWeight: RiskWeight;
  platforms: PlatformName[];
  platformSearchTerms: string[];
  platformTags: string[];
  status: TermStatus;
  updatedAt: string;
  note: string;
}

interface PlatformConfig {
  platform: PlatformName;
  enabled: boolean;
  matchMethod: MatchMethod;
  searchTerms: string[];
  tags: string[];
}

interface LexiconEditDraft {
  category: LexiconCategory;
  name: string;
  version: string;
  description: string;
  terms: LexiconTerm[];
  platforms: PlatformConfig[];
}

interface LexiconEditPageProps {
  lexicons: RiskLexicon[];
  policies: ResearchPolicy[];
  loading: boolean;
  error: string;
  onRefresh: () => void;
}

const steps: Array<{ id: StepId; title: string }> = [
  { id: 1, title: "基础信息" },
  { id: 2, title: "词条管理" },
  { id: 3, title: "平台配置" },
  { id: 4, title: "引用方案" }
];

const platformOptions: PlatformName[] = ["抖音", "小红书", "快手", "其他平台"];
const matchMethodOptions: MatchMethod[] = ["精确", "模糊", "正则"];
const riskWeightOptions: RiskWeight[] = ["低", "中", "高"];
const statusOptions: Array<{ value: TermStatusFilter; label: string }> = [
  { value: "全部", label: "全部" },
  { value: "enabled", label: "启用" },
  { value: "disabled", label: "停用" }
];

export function LexiconEditPage({ lexicons, policies, loading, error, onRefresh }: LexiconEditPageProps) {
  const navigate = useNavigate();
  const { lexiconId = "" } = useParams();
  const isNewLexicon = lexiconId === "new";
  const lexicon = lexicons.find((item) => item.id === lexiconId);
  const [step, setStep] = useState<StepId>(1);
  const [draft, setDraft] = useState<LexiconEditDraft | null>(null);
  const [loadedLexiconId, setLoadedLexiconId] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<TermStatusFilter>("全部");
  const [drawerTerm, setDrawerTerm] = useState<LexiconTerm | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  useEffect(() => {
    if ((!lexicon && !isNewLexicon) || loadedLexiconId === lexiconId) {
      return;
    }
    setDraft(readStoredLexiconDraft(lexiconId) || buildLexiconDraft(lexicon));
    setLoadedLexiconId(lexiconId);
    setStep(1);
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
        [term.mainTerm, term.variants.join(" "), term.platformSearchTerms.join(" "), term.platformTags.join(" ")]
          .join(" ")
          .toLowerCase()
          .includes(keyword);
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

  const updateTerm = (next: LexiconTerm) => {
    updateDraft({ terms: draft.terms.map((term) => (term.id === next.id ? next : term)) });
    setDrawerTerm(next);
  };

  const addTerm = () => {
    const next: LexiconTerm = {
      id: `term_${Date.now()}`,
      mainTerm: "新词条",
      variants: [],
      matchMethod: "模糊",
      riskWeight: "中",
      platforms: ["抖音", "小红书", "快手"],
      platformSearchTerms: [],
      platformTags: [],
      status: "enabled",
      updatedAt: new Date().toISOString(),
      note: ""
    };
    updateDraft({ terms: [next, ...draft.terms] });
    setDrawerTerm(next);
  };

  const saveDraft = () => {
    window.localStorage.setItem(`lexicon-edit-draft:${lexiconId}`, JSON.stringify(draft));
    showToast("黑话库草稿已保存");
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

  const categoryOptions = policyCategoryOptions.filter((item): item is PolicyCategory => item !== "全部");

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
            <span>{draft.category}</span>
            <span>当前版本：{draft.version}</span>
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
        <aside className="lexicon-edit-steps" aria-label="黑话库编辑步骤">
          {steps.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`lexicon-step-item${item.id === step ? " is-active" : ""}${item.id < step ? " is-done" : ""}`}
              onClick={() => setStep(item.id)}
            >
              <span>{item.id}</span>
              <strong>{item.title}</strong>
            </button>
          ))}
        </aside>

        <div className="lexicon-edit-content">
          <div className="lexicon-edit-panel">
            {step === 1 ? (
              <StepBasicInfo
                draft={draft}
                categoryOptions={categoryOptions}
                onChange={updateDraft}
              />
            ) : null}
            {step === 2 ? (
              <StepTerms
                query={query}
                statusFilter={statusFilter}
                terms={filteredTerms}
                onQueryChange={setQuery}
                onStatusFilterChange={setStatusFilter}
                onImport={() => showToast("批量导入入口已保留", "info")}
                onCreateTerm={addTerm}
                onEditTerm={setDrawerTerm}
              />
            ) : null}
            {step === 3 ? (
              <StepPlatformConfig
                platforms={draft.platforms}
                onChange={(platforms) => updateDraft({ platforms })}
              />
            ) : null}
            {step === 4 ? <StepReferences policies={references} /> : null}
          </div>

          <footer className="lexicon-edit-actionbar">
            <div>
              {step === 1 ? (
                <Button type="button" variant="secondary" onClick={() => navigate("/config/lexicons")}>
                  取消
                </Button>
              ) : (
                <Button type="button" variant="secondary" onClick={() => setStep((current) => Math.max(1, current - 1) as StepId)}>
                  上一步
                </Button>
              )}
            </div>
            <div className="lexicon-edit-actionbar-right">
              <Button type="button" variant="secondary" onClick={saveDraft}>
                保存草稿
              </Button>
              {step === 4 ? (
                <Button type="button" variant="primary" onClick={() => showToast("黑话库配置已保存")}>
                  完成
                </Button>
              ) : (
                <Button type="button" variant="primary" onClick={() => setStep((current) => Math.min(4, current + 1) as StepId)}>
                  下一步
                </Button>
              )}
            </div>
          </footer>
        </div>
      </section>

      <TermDrawer
        term={drawerTerm}
        onClose={() => setDrawerTerm(null)}
        onChange={updateTerm}
      />

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
  categoryOptions: PolicyCategory[];
  onChange: (patch: Partial<LexiconEditDraft>) => void;
}

function StepBasicInfo({ draft, categoryOptions, onChange }: StepBasicInfoProps) {
  return (
    <div className="lexicon-step-content">
      <StepHeading title="基础信息" />
      <div className="lexicon-basic-grid">
        <label className="lexicon-form-field">
          <span>风险分类</span>
          <select value={draft.category} onChange={(event) => onChange({ category: event.target.value as LexiconCategory })}>
            {categoryOptions.map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
        </label>
        <label className="lexicon-form-field">
          <span>词库名称</span>
          <input value={draft.name} maxLength={50} onChange={(event) => onChange({ name: event.target.value })} />
        </label>
        <label className="lexicon-form-field">
          <span>当前版本</span>
          <input value={draft.version} maxLength={24} onChange={(event) => onChange({ version: event.target.value })} />
        </label>
        <label className="lexicon-form-field full">
          <span>词库说明</span>
          <textarea
            value={draft.description}
            maxLength={200}
            rows={5}
            onChange={(event) => onChange({ description: event.target.value })}
          />
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
  onImport: () => void;
  onCreateTerm: () => void;
  onEditTerm: (term: LexiconTerm) => void;
}

function StepTerms({
  query,
  statusFilter,
  terms,
  onQueryChange,
  onStatusFilterChange,
  onImport,
  onCreateTerm,
  onEditTerm
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
        <Button type="button" variant="secondary" onClick={onImport}>
          <Download size={16} />
          批量导入
        </Button>
        <Button type="button" variant="primary" onClick={onCreateTerm}>
          <Plus size={16} />
          新建词条
        </Button>
      </div>

      <div className="term-table" role="table" aria-label="词条管理表格">
        <div className="term-table-head" role="row">
          <span>主词</span>
          <span>变体数</span>
          <span>平台搜索词数</span>
          <span>平台标签数</span>
          <span>状态</span>
          <span>更新时间</span>
          <span>操作</span>
        </div>
        {terms.length ? (
          terms.map((term) => (
            <div className="term-table-row" role="row" key={term.id}>
              <strong title={term.mainTerm}>{term.mainTerm}</strong>
              <span>{term.variants.length} 个</span>
              <span>{term.platformSearchTerms.length} 个</span>
              <span>{term.platformTags.length} 个</span>
              <StatusTag tone={term.status === "enabled" ? "success" : "neutral"}>
                {term.status === "enabled" ? "启用" : "停用"}
              </StatusTag>
              <span>{formatCompactDateTime(term.updatedAt)}</span>
              <button className="config-text-button" type="button" onClick={() => onEditTerm(term)}>
                编辑
              </button>
            </div>
          ))
        ) : (
          <div className="term-empty">暂无匹配词条</div>
        )}
      </div>
    </div>
  );
}

interface StepPlatformConfigProps {
  platforms: PlatformConfig[];
  onChange: (platforms: PlatformConfig[]) => void;
}

function StepPlatformConfig({ platforms, onChange }: StepPlatformConfigProps) {
  const updatePlatform = (platform: PlatformName, patch: Partial<PlatformConfig>) => {
    onChange(platforms.map((item) => (item.platform === platform ? { ...item, ...patch } : item)));
  };

  return (
    <div className="lexicon-step-content">
      <StepHeading title="平台配置" />
      <div className="platform-config-grid">
        {platforms.map((item) => (
          <section className="platform-config-group" key={item.platform}>
            <header>
              <div>
                <h3>{item.platform}</h3>
                <span>{item.searchTerms.length} 个搜索词 · {item.tags.length} 个标签</span>
              </div>
              <label className="switch-control">
                <input
                  type="checkbox"
                  checked={item.enabled}
                  onChange={(event) => updatePlatform(item.platform, { enabled: event.target.checked })}
                />
                <span />
              </label>
            </header>
            <label className="lexicon-form-field">
              <span>匹配方式</span>
              <select
                value={item.matchMethod}
                onChange={(event) => updatePlatform(item.platform, { matchMethod: event.target.value as MatchMethod })}
              >
                {matchMethodOptions.map((method) => (
                  <option key={method}>{method}</option>
                ))}
              </select>
            </label>
            <TokenEditor
              label="平台搜索词"
              values={item.searchTerms}
              placeholder="输入搜索词后回车"
              onChange={(values) => updatePlatform(item.platform, { searchTerms: values })}
            />
            <TokenEditor
              label="平台标签"
              values={item.tags}
              placeholder="输入平台标签后回车"
              onChange={(values) => updatePlatform(item.platform, { tags: values })}
            />
          </section>
        ))}
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
          <span>状态</span>
          <span>适用场景</span>
          <span>引用版本</span>
          <span>操作</span>
        </div>
        {policies.length ? (
          policies.map((policy) => (
            <div className="reference-policy-row" role="row" key={policy.id}>
              <strong title={policy.name}>{policy.name}</strong>
              <StatusTag tone={getPolicyStatusTone(policy.status)}>{getPolicyStatusLabel(policy.status)}</StatusTag>
              <span title={policy.scenarioTags.join("、")}>{policy.scenarioTags.join("、")}</span>
              <span>{policy.version.version}</span>
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

interface TermDrawerProps {
  term: LexiconTerm | null;
  onClose: () => void;
  onChange: (term: LexiconTerm) => void;
}

function TermDrawer({ term, onClose, onChange }: TermDrawerProps) {
  if (!term) {
    return null;
  }

  const patch = (update: Partial<LexiconTerm>) => {
    onChange({ ...term, ...update, updatedAt: new Date().toISOString() });
  };

  return (
    <div className="term-drawer-backdrop" role="presentation" onClick={onClose}>
      <aside
        className="term-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="term-drawer-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="term-drawer-header">
          <div>
            <span>词条编辑</span>
            <h2 id="term-drawer-title">{term.mainTerm}</h2>
          </div>
          <IconButton type="button" aria-label="关闭词条抽屉" onClick={onClose}>
            <X size={20} />
          </IconButton>
        </header>
        <div className="term-drawer-body">
          <label className="lexicon-form-field">
            <span>主词</span>
            <input value={term.mainTerm} onChange={(event) => patch({ mainTerm: event.target.value })} />
          </label>
          <TokenEditor label="变体" values={term.variants} placeholder="输入变体后回车" onChange={(values) => patch({ variants: values })} />
          <label className="lexicon-form-field">
            <span>匹配方式</span>
            <select value={term.matchMethod} onChange={(event) => patch({ matchMethod: event.target.value as MatchMethod })}>
              {matchMethodOptions.map((method) => (
                <option key={method}>{method}</option>
              ))}
            </select>
          </label>
          <label className="lexicon-form-field">
            <span>风险权重</span>
            <select value={term.riskWeight} onChange={(event) => patch({ riskWeight: event.target.value as RiskWeight })}>
              {riskWeightOptions.map((weight) => (
                <option key={weight}>{weight}</option>
              ))}
            </select>
          </label>
          <div className="lexicon-form-field">
            <span>适用平台</span>
            <div className="drawer-platform-grid">
              {platformOptions.map((platform) => (
                <label key={platform} className={term.platforms.includes(platform) ? "is-selected" : ""}>
                  <input
                    type="checkbox"
                    checked={term.platforms.includes(platform)}
                    onChange={() => {
                      const platforms = term.platforms.includes(platform)
                        ? term.platforms.filter((item) => item !== platform)
                        : [...term.platforms, platform];
                      patch({ platforms });
                    }}
                  />
                  {platform}
                </label>
              ))}
            </div>
          </div>
          <TokenEditor
            label="平台搜索词"
            values={term.platformSearchTerms}
            placeholder="输入平台搜索词后回车"
            onChange={(values) => patch({ platformSearchTerms: values })}
          />
          <TokenEditor
            label="平台标签"
            values={term.platformTags}
            placeholder="输入平台标签后回车"
            onChange={(values) => patch({ platformTags: values })}
          />
          <label className="lexicon-form-field">
            <span>备注</span>
            <textarea rows={4} value={term.note} onChange={(event) => patch({ note: event.target.value })} />
          </label>
        </div>
        <footer className="term-drawer-footer">
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button type="button" variant="primary" onClick={onClose}>
            保存词条
          </Button>
        </footer>
      </aside>
    </div>
  );
}

interface TokenEditorProps {
  label: string;
  values: string[];
  placeholder: string;
  onChange: (values: string[]) => void;
}

function TokenEditor({ label, values, placeholder, onChange }: TokenEditorProps) {
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
    <div className="token-editor">
      <span>{label}</span>
      <div className="token-editor-box">
        {values.map((item) => (
          <button key={item} type="button" onClick={() => onChange(values.filter((valueItem) => valueItem !== item))}>
            {item}
            <X size={13} />
          </button>
        ))}
        <input
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

function readStoredLexiconDraft(lexiconId: string): LexiconEditDraft | null {
  try {
    const stored = window.localStorage.getItem(`lexicon-edit-draft:${lexiconId}`);
    if (!stored) {
      return null;
    }
    const parsed = JSON.parse(stored) as Partial<LexiconEditDraft>;
    if (!parsed || !Array.isArray(parsed.terms) || !Array.isArray(parsed.platforms)) {
      return null;
    }
    return parsed as LexiconEditDraft;
  } catch {
    return null;
  }
}

function buildLexiconDraft(lexicon?: RiskLexicon): LexiconEditDraft {
  const keywords = lexicon?.keywords.length ? lexicon.keywords : ["新词条"];
  const terms: LexiconTerm[] = keywords.map((keyword, index) => ({
    id: `${lexicon?.id || "new"}_${index}`,
    mainTerm: keyword,
    variants: [`${keyword}变体`, `${keyword}谐音`].slice(0, index % 3),
    matchMethod: (index % 3 === 0 ? "模糊" : index % 3 === 1 ? "精确" : "正则") as MatchMethod,
    riskWeight: (index % 3 === 0 ? "高" : index % 3 === 1 ? "中" : "低") as RiskWeight,
    platforms: index % 2 === 0 ? (["抖音", "小红书"] as PlatformName[]) : (["快手", "其他平台"] as PlatformName[]),
    platformSearchTerms: (lexicon?.platformSearchWords || []).slice(index, index + 2),
    platformTags: (lexicon?.platformTags || []).slice(index, index + 2),
    status: (index % 5 === 4 ? "disabled" : "enabled") as TermStatus,
    updatedAt: lexicon?.updatedAt || new Date().toISOString(),
    note: ""
  }));

  return {
    category: lexicon?.category || "赌博博彩",
    name: lexicon?.name || "",
    version: lexicon ? "v1.0" : "draft",
    description: lexicon ? `用于识别${lexicon.category}相关黑话、搜索词和平台标签。` : "",
    terms,
    platforms: platformOptions.map((platform, index) => ({
      platform,
      enabled: true,
      matchMethod: index === 3 ? "模糊" : "精确",
      searchTerms: (lexicon?.platformSearchWords || []).slice(0, 4),
      tags: (lexicon?.platformTags || []).slice(0, 4)
    }))
  };
}
