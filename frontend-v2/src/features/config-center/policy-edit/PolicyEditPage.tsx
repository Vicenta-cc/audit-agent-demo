import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Circle,
  FlaskConical,
  MoreHorizontal,
  Plus,
  Trash2
} from "lucide-react";
import { Button } from "../../../components/common/Button";
import { DropdownMenu } from "../../../components/common/DropdownMenu";
import { StatusTag } from "../../../components/common/StatusTag";
import { ConfirmDialog } from "../../../components/feedback/ConfirmDialog";
import { EmptyState } from "../../../components/feedback/EmptyState";
import { ErrorState } from "../../../components/feedback/ErrorState";
import { LoadingState } from "../../../components/feedback/LoadingState";
import { Toast } from "../../../components/feedback/Toast";
import {
  deletePolicy,
  formatCompactDateTime,
  formatNumber,
  getPolicyStatusLabel,
  getPolicyStatusTone,
  policyCategoryOptions
} from "../../../services/configCenter";
import type { PolicyCategory, ResearchPolicy, RiskLexicon } from "../../../types/configCenter";

type StepId = 1 | 2 | 3 | 4 | 5;
type DetectionKey = "title" | "comment" | "image" | "video" | "audio";
type ScoringMode = "balanced" | "strict" | "text_first" | "vision_first" | "custom";
type EvidenceSource = "文字内容命中" | "画面文字命中" | "语音内容命中" | "画面特征命中" | "评论聚集命中";

interface DetectionConfig {
  key: DetectionKey;
  label: string;
  enabled: boolean;
  recommendedAbilities: string[];
  customAbilities: string[];
}

interface ScoringRule {
  source: EvidenceSource;
  enabled: boolean;
  importance: "低" | "中" | "高";
  score: number;
}

interface RiskThresholds {
  review: number;
  medium: number;
  high: number;
}

interface PolicyEditDraft {
  name: string;
  description: string;
  category: PolicyCategory;
  scenarioTags: string[];
  lexiconIds: string[];
  detections: DetectionConfig[];
  scoringMode: ScoringMode;
  scoringRules: ScoringRule[];
  thresholds: RiskThresholds;
  tested: boolean;
}

interface PolicyEditPageProps {
  policies: ResearchPolicy[];
  lexicons: RiskLexicon[];
  loading: boolean;
  error: string;
  onRefresh: () => void;
}

const steps: Array<{ id: StepId; title: string }> = [
  { id: 1, title: "基础信息" },
  { id: 2, title: "风险范围" },
  { id: 3, title: "检测配置" },
  { id: 4, title: "风险评分" },
  { id: 5, title: "发布检查" }
];

const scenarioOptions = ["平台内容", "直播", "本地视频", "重点用户", "评论弹幕", "搜索巡检"];
const abilityOptions = ["文本语义", "评论聚集", "OCR", "视觉识别", "ASR"];
const detectionTemplates: Array<Omit<DetectionConfig, "enabled" | "customAbilities">> = [
  { key: "title", label: "标题与正文", recommendedAbilities: ["文本语义"] },
  { key: "comment", label: "评论与弹幕", recommendedAbilities: ["文本语义", "评论聚集"] },
  { key: "image", label: "图片与封面", recommendedAbilities: ["OCR", "视觉识别"] },
  { key: "video", label: "视频画面", recommendedAbilities: ["OCR", "视觉识别"] },
  { key: "audio", label: "语音内容", recommendedAbilities: ["ASR", "文本语义"] }
];
const evidenceSources: EvidenceSource[] = ["文字内容命中", "画面文字命中", "语音内容命中", "画面特征命中", "评论聚集命中"];

const scoringModeLabels: Record<ScoringMode, string> = {
  balanced: "均衡模式",
  strict: "严格模式",
  text_first: "文字优先",
  vision_first: "视觉优先",
  custom: "自定义"
};

const scoringModeSummaries: Record<ScoringMode, string> = {
  balanced: "文字、画面、语音和评论证据均衡参与研判，适合通用巡检任务。",
  strict: "提高关键证据的重要程度，适合风险容忍度较低的专项任务。",
  text_first: "突出标题正文、评论弹幕和语音转写，适合话术类风险。",
  vision_first: "突出 OCR 与视觉特征，适合图片、封面、视频画面风险。",
  custom: "允许逐项调整证据启用状态、重要程度和分值。"
};

export function PolicyEditPage({ policies, lexicons, loading, error, onRefresh }: PolicyEditPageProps) {
  const navigate = useNavigate();
  const { policyId = "" } = useParams();
  const isNewPolicy = policyId === "new";
  const policy = policies.find((item) => item.id === policyId);
  const [step, setStep] = useState<StepId>(1);
  const [draft, setDraft] = useState<PolicyEditDraft | null>(null);
  const [loadedPolicyId, setLoadedPolicyId] = useState("");
  const [bindOpen, setBindOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  useEffect(() => {
    if ((!policy && !isNewPolicy) || loadedPolicyId === policyId) {
      return;
    }
    setDraft(buildDraft(policy));
    setLoadedPolicyId(policyId);
    setStep(1);
  }, [isNewPolicy, loadedPolicyId, policy, policyId]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const boundLexicons = useMemo(() => {
    if (!draft) {
      return [];
    }
    return draft.lexiconIds.map((id) => lexicons.find((item) => item.id === id)).filter(Boolean) as RiskLexicon[];
  }, [draft, lexicons]);

  const availableLexicons = useMemo(() => {
    if (!draft) {
      return lexicons;
    }
    return lexicons.filter((lexicon) => !draft.lexiconIds.includes(lexicon.id));
  }, [draft, lexicons]);

  const allAbilities = useMemo(() => {
    if (!draft) {
      return [];
    }
    return Array.from(new Set(draft.detections.flatMap((item) => (item.enabled ? item.customAbilities : []))));
  }, [draft]);

  if (loading && !draft) {
    return (
      <main className="policy-edit-page">
        <LoadingState label="正在加载研判方案..." />
      </main>
    );
  }

  if (error && !draft) {
    return (
      <main className="policy-edit-page">
        <ErrorState message={error} onRetry={onRefresh} />
      </main>
    );
  }

  if (!draft || (!policy && !isNewPolicy)) {
    return (
      <main className="policy-edit-page">
        <div className="policy-edit-empty">
          <EmptyState title="未找到研判方案" description="返回配置底座后重新选择需要编辑的方案。">
            <Button type="button" variant="primary" onClick={() => navigate("/config/policies")}>
              返回配置底座
            </Button>
          </EmptyState>
        </div>
      </main>
    );
  }

  const thresholdValid = isThresholdValid(draft.thresholds);
  const checklist = buildChecklist(draft, boundLexicons, allAbilities, thresholdValid);

  const showToast = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const updateDraft = (patch: Partial<PolicyEditDraft>) => {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  };

  const saveDraft = () => {
    window.localStorage.setItem(`policy-edit-draft:${policyId}`, JSON.stringify(draft));
    showToast("草稿已保存");
  };

  const testPolicy = () => {
    updateDraft({ tested: true });
    showToast("测试已提交，当前配置可进入发布检查");
  };

  const publishPolicyDraft = () => {
    const blocking = checklist.filter((item) => item.blocking && item.status !== "pass");
    if (blocking.length) {
      showToast("请先完成发布检查中的必填项", "info");
      return;
    }
    showToast(draft.tested ? "方案已提交发布" : "方案已提交发布，建议尽快补充测试记录", "info");
  };

  const handleConfirmDelete = async () => {
    if (isNewPolicy) {
      navigate("/config/policies");
      return;
    }
    setDeleting(true);
    try {
      await deletePolicy(policyId);
      showToast("方案已删除");
      navigate("/config/policies");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "删除失败", "info");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <main className="policy-edit-page">
      <header className="policy-edit-header">
        <button className="policy-edit-back" type="button" onClick={() => navigate("/config/policies")}>
          <ArrowLeft size={18} />
          返回配置底座
        </button>
        <div className="policy-edit-title-block">
          <h1>{draft.name || "未命名研判方案"}</h1>
          <div className="policy-edit-meta">
            <StatusTag tone={getPolicyStatusTone(policy?.status || "draft")}>
              {getPolicyStatusLabel(policy?.status || "draft")}
            </StatusTag>
            <span>当前版本：{policy?.version.version || "draft"}</span>
            <span>最近更新：{formatCompactDateTime(policy?.updatedAt || "")}</span>
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
            删除方案
          </button>
        </DropdownMenu>
      </header>

      <section className="policy-edit-shell">
        <aside className="policy-edit-steps" aria-label="研判方案编辑步骤">
          {steps.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`policy-step-item${item.id === step ? " is-active" : ""}${item.id < step ? " is-done" : ""}`}
              onClick={() => setStep(item.id)}
            >
              <span>{item.id}</span>
              <strong>{item.title}</strong>
            </button>
          ))}
        </aside>

        <div className="policy-edit-content">
          <div className="policy-edit-panel">
            {step === 1 ? <StepBasicInfo draft={draft} onChange={updateDraft} /> : null}
            {step === 2 ? (
              <StepRiskScope
                boundLexicons={boundLexicons}
                availableLexicons={availableLexicons}
                bindOpen={bindOpen}
                onBindOpenChange={setBindOpen}
                onAddLexicon={(lexiconId) => updateDraft({ lexiconIds: [...draft.lexiconIds, lexiconId] })}
                onRemoveLexicon={(lexiconId) =>
                  updateDraft({ lexiconIds: draft.lexiconIds.filter((item) => item !== lexiconId) })
                }
                onEditLexicon={(lexiconId) => navigate(`/config/lexicons/${encodeURIComponent(lexiconId)}/edit`)}
                onCreateLexicon={() => navigate("/config/lexicons/new/edit")}
              />
            ) : null}
            {step === 3 ? (
              <StepDetectionConfig
                detections={draft.detections}
                onChange={(detections) => updateDraft({ detections })}
              />
            ) : null}
            {step === 4 ? (
              <StepRiskScoring
                draft={draft}
                thresholdValid={thresholdValid}
                onChange={updateDraft}
              />
            ) : null}
            {step === 5 ? (
              <StepPublishCheck
                draft={draft}
                boundLexicons={boundLexicons}
                abilities={allAbilities}
                checklist={checklist}
                thresholdValid={thresholdValid}
                references={policy?.references || []}
              />
            ) : null}
          </div>

          <footer className="policy-edit-actionbar">
            <div>
              {step === 1 ? (
                <Button type="button" variant="secondary" onClick={() => navigate("/config/policies")}>
                  取消
                </Button>
              ) : (
                <Button type="button" variant="secondary" onClick={() => setStep((current) => Math.max(1, current - 1) as StepId)}>
                  上一步
                </Button>
              )}
            </div>
            <div className="policy-edit-actionbar-right">
              {step === 5 ? (
                <Button type="button" variant="secondary" onClick={testPolicy}>
                  <FlaskConical size={16} />
                  测试方案
                </Button>
              ) : null}
              <Button type="button" variant="secondary" onClick={saveDraft}>
                保存草稿
              </Button>
              {step === 5 ? (
                <Button type="button" variant="primary" onClick={publishPolicyDraft}>
                  发布方案
                </Button>
              ) : (
                <Button type="button" variant="primary" onClick={() => setStep((current) => Math.min(5, current + 1) as StepId)}>
                  下一步
                </Button>
              )}
            </div>
          </footer>
        </div>
      </section>

      <ConfirmDialog
        open={deleteOpen}
        title="删除方案"
        description={`确认删除「${draft.name || "未命名研判方案"}」？删除后无法继续编辑该方案。`}
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
  draft: PolicyEditDraft;
  onChange: (patch: Partial<PolicyEditDraft>) => void;
}

function StepBasicInfo({ draft, onChange }: StepBasicInfoProps) {
  const categoryOptions = policyCategoryOptions.filter((item): item is PolicyCategory => item !== "全部");

  return (
    <div className="policy-step-content">
      <StepHeading title="基础信息" description="维护方案的名称、说明、风险分类和适用业务场景。" />
      <div className="policy-form-grid">
        <label className="policy-form-field">
          <span>方案名称</span>
          <input value={draft.name} maxLength={50} onChange={(event) => onChange({ name: event.target.value })} />
        </label>
        <label className="policy-form-field full">
          <span>方案说明</span>
          <textarea
            value={draft.description}
            maxLength={200}
            rows={4}
            onChange={(event) => onChange({ description: event.target.value })}
          />
        </label>
        <label className="policy-form-field">
          <span>风险分类</span>
          <select value={draft.category} onChange={(event) => onChange({ category: event.target.value as PolicyCategory })}>
            {categoryOptions.map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
        </label>
        <div className="policy-form-field full">
          <span>适用场景</span>
          <div className="scenario-chip-grid">
            {scenarioOptions.map((item) => (
              <label key={item} className={`scenario-chip${draft.scenarioTags.includes(item) ? " is-selected" : ""}`}>
                <input
                  type="checkbox"
                  checked={draft.scenarioTags.includes(item)}
                  onChange={() => {
                    const next = draft.scenarioTags.includes(item)
                      ? draft.scenarioTags.filter((value) => value !== item)
                      : [...draft.scenarioTags, item];
                    onChange({ scenarioTags: next });
                  }}
                />
                {item}
              </label>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

interface StepRiskScopeProps {
  boundLexicons: RiskLexicon[];
  availableLexicons: RiskLexicon[];
  bindOpen: boolean;
  onBindOpenChange: (open: boolean) => void;
  onAddLexicon: (lexiconId: string) => void;
  onRemoveLexicon: (lexiconId: string) => void;
  onEditLexicon: (lexiconId: string) => void;
  onCreateLexicon: () => void;
}

function StepRiskScope({
  boundLexicons,
  availableLexicons,
  bindOpen,
  onBindOpenChange,
  onAddLexicon,
  onRemoveLexicon,
  onEditLexicon,
  onCreateLexicon
}: StepRiskScopeProps) {
  return (
    <div className="policy-step-content">
      <StepHeading title="风险范围" description="此步骤只管理方案绑定的知识库。" />
      <div className="step-toolbar compact">
        <DropdownMenu
          open={bindOpen}
          onClose={() => onBindOpenChange(false)}
          trigger={
            <Button type="button" variant="secondary" onClick={() => onBindOpenChange(!bindOpen)}>
              <BookOpen size={16} />
              绑定已有知识库
              <ChevronDown size={15} />
            </Button>
          }
        >
          {availableLexicons.length ? (
            availableLexicons.map((lexicon) => (
              <button
                key={lexicon.id}
                type="button"
                onClick={() => {
                  onAddLexicon(lexicon.id);
                  onBindOpenChange(false);
                }}
              >
                {lexicon.name}
              </button>
            ))
          ) : (
            <button type="button" disabled>
              无可绑定知识库
            </button>
          )}
        </DropdownMenu>
        <Button type="button" variant="primary" onClick={onCreateLexicon}>
          <Plus size={16} />
          新建黑话库
        </Button>
      </div>

      <div className="knowledge-list" role="table" aria-label="已绑定知识库列表">
        <div className="knowledge-list-head" role="row">
          <span>知识库名称</span>
          <span>风险分类</span>
          <span>词条数</span>
          <span>示例词</span>
          <span>操作</span>
        </div>
        {boundLexicons.length ? (
          boundLexicons.map((lexicon) => (
            <div className="knowledge-list-row" role="row" key={lexicon.id}>
              <strong>{lexicon.name}</strong>
              <span>{lexicon.category}</span>
              <span>{formatNumber(lexicon.entryCount)} 个</span>
              <span title={lexicon.keywords.join("、")}>{lexicon.keywords.slice(0, 5).join("、") || "-"}</span>
              <div className="knowledge-actions">
                <button type="button" className="config-text-button" onClick={() => onEditLexicon(lexicon.id)}>
                  编辑
                </button>
                <button type="button" className="config-text-button is-danger-text" onClick={() => onRemoveLexicon(lexicon.id)}>
                  移除
                </button>
              </div>
            </div>
          ))
        ) : (
          <div className="knowledge-empty">尚未绑定知识库</div>
        )}
      </div>
    </div>
  );
}

interface StepDetectionConfigProps {
  detections: DetectionConfig[];
  onChange: (detections: DetectionConfig[]) => void;
}

function StepDetectionConfig({ detections, onChange }: StepDetectionConfigProps) {
  const updateRow = (key: DetectionKey, patch: Partial<DetectionConfig>) => {
    onChange(detections.map((item) => (item.key === key ? { ...item, ...patch } : item)));
  };

  return (
    <div className="policy-step-content">
      <StepHeading title="检测配置" description="检测位置决定系统在哪些内容区域执行识别，识别能力随检测位置配置。" />
      <div className="detection-matrix" role="table" aria-label="检测位置与识别能力矩阵">
        <div className="detection-matrix-head" role="row">
          <span>检测位置</span>
          <span>是否检测</span>
          <span>推荐识别能力</span>
          <span>自定义识别能力</span>
          <span>高级设置</span>
        </div>
        {detections.map((row) => (
          <div className="detection-matrix-row" role="row" key={row.key}>
            <strong>{row.label}</strong>
            <label className="switch-control">
              <input
                type="checkbox"
                checked={row.enabled}
                onChange={(event) => {
                  const enabled = event.target.checked;
                  updateRow(row.key, {
                    enabled,
                    customAbilities: enabled ? row.recommendedAbilities : row.customAbilities
                  });
                }}
              />
              <span />
            </label>
            <div className="inline-chip-list">
              {row.recommendedAbilities.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
            <div className="ability-toggle-list" aria-disabled={!row.enabled}>
              {abilityOptions.map((ability) => (
                <button
                  key={ability}
                  type="button"
                  disabled={!row.enabled}
                  className={row.customAbilities.includes(ability) ? "is-selected" : ""}
                  onClick={() => {
                    const next = row.customAbilities.includes(ability)
                      ? row.customAbilities.filter((item) => item !== ability)
                      : [...row.customAbilities, ability];
                    updateRow(row.key, { customAbilities: next });
                  }}
                >
                  {ability}
                </button>
              ))}
            </div>
            <button className="config-text-button" type="button">
              设置
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

interface StepRiskScoringProps {
  draft: PolicyEditDraft;
  thresholdValid: boolean;
  onChange: (patch: Partial<PolicyEditDraft>) => void;
}

function StepRiskScoring({ draft, thresholdValid, onChange }: StepRiskScoringProps) {
  const editable = draft.scoringMode === "custom";

  const updateRule = (source: EvidenceSource, patch: Partial<ScoringRule>) => {
    onChange({
      scoringRules: draft.scoringRules.map((rule) => (rule.source === source ? { ...rule, ...patch } : rule))
    });
  };

  return (
    <div className="policy-step-content">
      <StepHeading title="风险评分" description="选择评分模式，并在自定义模式下调整证据来源、重要程度和分值。" />
      <div className="scoring-mode-group" role="radiogroup" aria-label="评分模式">
        {(Object.keys(scoringModeLabels) as ScoringMode[]).map((mode) => (
          <button
            key={mode}
            type="button"
            className={draft.scoringMode === mode ? "is-selected" : ""}
            onClick={() => onChange({ scoringMode: mode })}
          >
            {scoringModeLabels[mode]}
          </button>
        ))}
      </div>
      <div className="scoring-summary">
        <strong>{scoringModeLabels[draft.scoringMode]}</strong>
        <span>{scoringModeSummaries[draft.scoringMode]}</span>
      </div>

      <div className="scoring-rule-table" role="table" aria-label="评分规则表格">
        <div className="scoring-rule-head" role="row">
          <span>证据来源</span>
          <span>是否启用</span>
          <span>重要程度</span>
          <span>分值</span>
        </div>
        {draft.scoringRules.map((rule) => (
          <div className="scoring-rule-row" role="row" key={rule.source}>
            <strong>{rule.source}</strong>
            <label className="switch-control">
              <input
                type="checkbox"
                checked={rule.enabled}
                disabled={!editable}
                onChange={(event) => updateRule(rule.source, { enabled: event.target.checked })}
              />
              <span />
            </label>
            <select
              value={rule.importance}
              disabled={!editable}
              onChange={(event) => updateRule(rule.source, { importance: event.target.value as ScoringRule["importance"] })}
            >
              {["低", "中", "高"].map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
            <input
              type="number"
              min={0}
              max={100}
              value={rule.score}
              disabled={!editable}
              onChange={(event) => updateRule(rule.source, { score: Number(event.target.value) })}
            />
          </div>
        ))}
      </div>

      <div className="threshold-editor">
        <div className="threshold-inputs">
          <label>
            <span>待复核阈值</span>
            <input
              type="number"
              min={0}
              max={100}
              value={draft.thresholds.review}
              onChange={(event) => onChange({ thresholds: { ...draft.thresholds, review: Number(event.target.value) } })}
            />
          </label>
          <label>
            <span>中危阈值</span>
            <input
              type="number"
              min={0}
              max={100}
              value={draft.thresholds.medium}
              onChange={(event) => onChange({ thresholds: { ...draft.thresholds, medium: Number(event.target.value) } })}
            />
          </label>
          <label>
            <span>高危阈值</span>
            <input
              type="number"
              min={0}
              max={100}
              value={draft.thresholds.high}
              onChange={(event) => onChange({ thresholds: { ...draft.thresholds, high: Number(event.target.value) } })}
            />
          </label>
        </div>
        <div className="threshold-scale" aria-label="风险阈值刻度">
          <span style={{ left: `${draft.thresholds.review}%` }}>待复核 {draft.thresholds.review}</span>
          <span style={{ left: `${draft.thresholds.medium}%` }}>中危 {draft.thresholds.medium}</span>
          <span style={{ left: `${draft.thresholds.high}%` }}>高危 {draft.thresholds.high}</span>
        </div>
        {!thresholdValid ? <p className="threshold-error">待复核阈值必须小于中危阈值，中危阈值必须小于高危阈值。</p> : null}
      </div>
    </div>
  );
}

interface StepPublishCheckProps {
  draft: PolicyEditDraft;
  boundLexicons: RiskLexicon[];
  abilities: string[];
  checklist: CheckItem[];
  thresholdValid: boolean;
  references: ResearchPolicy["references"];
}

function StepPublishCheck({ draft, boundLexicons, abilities, checklist, thresholdValid, references }: StepPublishCheckProps) {
  const enabledDetections = draft.detections.filter((item) => item.enabled).map((item) => item.label);

  return (
    <div className="policy-step-content">
      <StepHeading title="发布检查" description="检查当前方案配置，确认无误后发布。" />
      <div className="publish-summary-grid">
        <SummaryItem label="方案名称" value={draft.name || "-"} />
        <SummaryItem label="风险分类" value={draft.category} />
        <SummaryItem label="知识库数量" value={`${boundLexicons.length} 个`} />
        <SummaryItem label="检测位置" value={enabledDetections.join("、") || "-"} />
        <SummaryItem label="识别能力" value={abilities.join("、") || "-"} />
        <SummaryItem label="评分模式" value={scoringModeLabels[draft.scoringMode]} />
        <SummaryItem
          label="风险阈值"
          value={`待复核 ${draft.thresholds.review} / 中危 ${draft.thresholds.medium} / 高危 ${draft.thresholds.high}`}
          tone={thresholdValid ? undefined : "warning"}
        />
        <SummaryItem label="引用任务" value={`${references.length} 个`} />
      </div>

      <div className="publish-checklist">
        {checklist.map((item) => (
          <div key={item.label} className={`checklist-row is-${item.status}`}>
            <span aria-hidden="true">
              {item.status === "pass" ? <CheckCircle2 size={18} /> : item.status === "warning" ? <AlertTriangle size={18} /> : <Circle size={18} />}
            </span>
            <div>
              <strong>{item.label}</strong>
              <small>{item.helper}</small>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface StepHeadingProps {
  title: string;
  description: string;
}

function StepHeading({ title, description }: StepHeadingProps) {
  return (
    <div className="policy-step-heading">
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
  );
}

interface SummaryItemProps {
  label: string;
  value: string;
  tone?: "warning";
}

function SummaryItem({ label, value, tone }: SummaryItemProps) {
  return (
    <div className={`publish-summary-item${tone ? ` is-${tone}` : ""}`}>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

interface CheckItem {
  label: string;
  helper: string;
  status: "pass" | "warning" | "pending";
  blocking: boolean;
}

function buildDraft(policy?: ResearchPolicy): PolicyEditDraft {
  const scopes = new Set(policy?.contentScopes || []);
  const detections = detectionTemplates.map((item) => {
    const enabled =
      (item.key === "title" && scopes.has("标题正文")) ||
      (item.key === "comment" && scopes.has("评论弹幕")) ||
      (item.key === "image" && (scopes.has("图片视频") || scopes.has("图片封面"))) ||
      (item.key === "video" && (scopes.has("图片视频") || scopes.has("视频画面"))) ||
      (item.key === "audio" && scopes.has("语音内容"));
    return {
      ...item,
      enabled,
      customAbilities: enabled ? item.recommendedAbilities : []
    };
  });

  return {
    name: policy?.name || "",
    description: policy?.description || "",
    category: policy?.category || "赌博博彩",
    scenarioTags: policy?.scenarioTags.length ? policy.scenarioTags : ["平台内容"],
    lexiconIds: policy?.lexiconIds || [],
    detections,
    scoringMode: "balanced",
    scoringRules: evidenceSources.map((source, index) => ({
      source,
      enabled: true,
      importance: index === 0 || index === 3 ? "高" : "中",
      score: [30, 20, 18, 22, 10][index]
    })),
    thresholds: { review: 40, medium: 60, high: 80 },
    tested: false
  };
}

function buildChecklist(
  draft: PolicyEditDraft,
  boundLexicons: RiskLexicon[],
  abilities: string[],
  thresholdValid: boolean
): CheckItem[] {
  const enabledDetections = draft.detections.filter((item) => item.enabled);
  const enabledRules = draft.scoringRules.filter((item) => item.enabled && item.score > 0);
  return [
    {
      label: "已绑定至少一个知识库",
      helper: boundLexicons.length ? `已绑定 ${boundLexicons.length} 个知识库` : "请先绑定知识库",
      status: boundLexicons.length ? "pass" : "pending",
      blocking: true
    },
    {
      label: "已选择至少一个检测位置",
      helper: enabledDetections.length ? enabledDetections.map((item) => item.label).join("、") : "请至少开启一个检测位置",
      status: enabledDetections.length ? "pass" : "pending",
      blocking: true
    },
    {
      label: "已配置评分规则",
      helper: enabledRules.length ? `${enabledRules.length} 条证据来源已启用，${abilities.length} 项识别能力参与` : "请启用至少一条评分规则",
      status: enabledRules.length ? "pass" : "pending",
      blocking: true
    },
    {
      label: "风险阈值顺序正确",
      helper: thresholdValid ? "待复核 < 中危 < 高危" : "请调整阈值顺序",
      status: thresholdValid ? "pass" : "pending",
      blocking: true
    },
    {
      label: "是否执行过方案测试",
      helper: draft.tested ? "已完成一次方案测试" : "尚未执行测试，建议发布前先测试",
      status: draft.tested ? "pass" : "warning",
      blocking: false
    }
  ];
}

function isThresholdValid(thresholds: RiskThresholds) {
  return thresholds.review < thresholds.medium && thresholds.medium < thresholds.high;
}
