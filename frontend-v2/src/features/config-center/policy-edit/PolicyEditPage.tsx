import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ClipboardList,
  MoreHorizontal,
  Plus,
  Trash2
} from "lucide-react";
import { Button } from "../../../components/common/Button";
import { DropdownMenu } from "../../../components/common/DropdownMenu";
import { ConfirmDialog } from "../../../components/feedback/ConfirmDialog";
import { EmptyState } from "../../../components/feedback/EmptyState";
import { ErrorState } from "../../../components/feedback/ErrorState";
import { LoadingState } from "../../../components/feedback/LoadingState";
import { Toast } from "../../../components/feedback/Toast";
import {
  deletePolicy,
  detectionCapabilityByEditorKey,
  detectionScopeByEditorKey,
  formatCompactDateTime,
  formatNumber,
  savePolicy
} from "../../../services/configCenter";
import type { PolicyDetectionConfig, PolicyReference, ResearchPolicy, RiskLexicon } from "../../../types/configCenter";

type DetectionKey = "title" | "comment" | "image" | "video" | "audio";
type ScoringMode = "balanced" | "strict" | "text_first" | "vision_first" | "custom";
type EvidenceSource = "文字内容命中" | "画面文字命中" | "语音内容命中" | "画面特征命中" | "逐条评论命中";

interface DetectionConfig {
  key: DetectionKey;
  label: string;
  enabled: boolean;
}

interface ScoringRule {
  source: EvidenceSource;
  enabled: boolean;
  importance: "低" | "中" | "高";
}

interface PolicyEditDraft {
  name: string;
  description: string;
  lexiconIds: string[];
  detections: DetectionConfig[];
  scoringMode: ScoringMode;
  scoringRules: ScoringRule[];
}

interface PolicyEditPageProps {
  policies: ResearchPolicy[];
  lexicons: RiskLexicon[];
  loading: boolean;
  error: string;
  onRefresh: () => void;
}

const detectionTemplates: Array<Omit<DetectionConfig, "enabled">> = [
  { key: "title", label: "标题与正文" },
  { key: "comment", label: "评论与弹幕" },
  { key: "image", label: "图片与封面" },
  { key: "video", label: "视频画面" },
  { key: "audio", label: "语音内容" }
];
const evidenceSources: EvidenceSource[] = ["文字内容命中", "画面文字命中", "语音内容命中", "画面特征命中", "逐条评论命中"];
const evidenceSourceByDetection: Record<DetectionKey, EvidenceSource> = {
  title: "文字内容命中",
  comment: "逐条评论命中",
  image: "画面文字命中",
  video: "画面特征命中",
  audio: "语音内容命中"
};

const scoringModeLabels: Record<ScoringMode, string> = {
  balanced: "均衡模式",
  strict: "严格模式",
  text_first: "文字优先",
  vision_first: "视觉优先",
  custom: "自定义"
};

export function PolicyEditPage({ policies, lexicons, loading, error, onRefresh }: PolicyEditPageProps) {
  const navigate = useNavigate();
  const { policyId = "" } = useParams();
  const isNewPolicy = policyId === "new";
  const policy = policies.find((item) => item.id === policyId);
  const [draft, setDraft] = useState<PolicyEditDraft | null>(null);
  const [loadedPolicyId, setLoadedPolicyId] = useState("");
  const [bindOpen, setBindOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  useEffect(() => {
    if ((!policy && !isNewPolicy) || loadedPolicyId === policyId) {
      return;
    }
    setDraft(buildDraft(policy));
    setLoadedPolicyId(policyId);
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

  const showToast = (message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  };

  const updateDraft = (patch: Partial<PolicyEditDraft>) => {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  };

  const saveDraft = async () => {
    if (!draft.name.trim()) {
      showToast("请输入方案名称", "info");
      return;
    }
    setSaving(true);
    try {
      await savePolicy({
        id: isNewPolicy ? undefined : policyId,
        name: draft.name,
        description: draft.description,
        lexiconIds: draft.lexiconIds,
        detectionConfigs: buildSaveDetectionConfigs(draft, policy),
        scoringMode: draft.scoringMode
      });
      await onRefresh();
      showToast("方案配置已保存");
      if (isNewPolicy) {
        navigate("/config/policies");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "保存失败", "info");
    } finally {
      setSaving(false);
    }
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
            {policy?.updatedAt ? <span>最近更新：{formatCompactDateTime(policy.updatedAt)}</span> : <span>新建方案</span>}
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
        <div className="policy-edit-content">
          <div className="policy-config-workspace">
            <div className="policy-config-left-column">
              <div className="policy-config-card policy-name-card">
                <StepBasicInfo draft={draft} onChange={updateDraft} />
              </div>
              <div className="policy-config-card policy-risk-card">
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
              </div>
            </div>
            <div className="policy-config-card policy-detection-scoring-card">
              <StepDetectionScoring draft={draft} onChange={updateDraft} />
            </div>
            <div className="policy-config-card policy-reference-card">
              <StepReferences references={policy?.references || []} />
            </div>
          </div>

          <footer className="policy-edit-actionbar">
            <Button type="button" variant="secondary" onClick={() => navigate("/config/policies")}>
              取消
            </Button>
            <div className="policy-edit-actionbar-right">
              <Button type="button" variant="primary" onClick={() => void saveDraft()} disabled={!draft.name.trim() || saving}>
                {saving ? "保存中..." : "保存配置"}
              </Button>
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

interface StepReferencesProps {
  references: PolicyReference[];
}

function StepReferences({ references }: StepReferencesProps) {
  return (
    <div className="policy-step-content policy-reference-content">
      <StepHeading title="引用任务" description="当前使用该方案的监控任务。" />
      <div className="policy-reference-summary">
        <span className="policy-reference-icon" aria-hidden="true">
          <ClipboardList size={20} />
        </span>
        <div>
          <strong>被 {formatNumber(references.length)} 个任务引用</strong>
          <span>任务配置将使用此方案</span>
        </div>
      </div>
      {references.length ? (
        <div className="policy-reference-list" aria-label="引用该方案的监控任务">
          {references.map((reference) => (
            <div className="policy-reference-row" key={reference.id}>
              <strong title={reference.name}>{reference.name}</strong>
              <span>{reference.status}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="policy-reference-empty">当前暂无监控任务引用该方案</div>
      )}
    </div>
  );
}

interface StepBasicInfoProps {
  draft: PolicyEditDraft;
  onChange: (patch: Partial<PolicyEditDraft>) => void;
}

function StepBasicInfo({ draft, onChange }: StepBasicInfoProps) {
  return (
    <div className="policy-step-content">
      <StepHeading title="方案名称" description="修改当前研判方案的名称。" />
      <div className="policy-form-grid policy-name-form">
        <label className="policy-form-field full">
          <span>方案名称</span>
          <input value={draft.name} maxLength={50} onChange={(event) => onChange({ name: event.target.value })} />
        </label>
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
      <StepHeading title="风险范围" description="管理当前方案绑定的知识库。" />
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
          <span>知识库</span>
          <span>词条数</span>
          <span>操作</span>
        </div>
        {boundLexicons.length ? (
          boundLexicons.map((lexicon) => (
            <div className="knowledge-list-row" role="row" key={lexicon.id}>
              <div className="knowledge-list-main">
                <strong>{lexicon.name}</strong>
                <span title={lexicon.keywords.join("、")}>{lexicon.keywords.slice(0, 5).join("、") || "-"}</span>
              </div>
              <span>{formatNumber(lexicon.entryCount)} 个</span>
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

interface StepDetectionScoringProps {
  draft: PolicyEditDraft;
  onChange: (patch: Partial<PolicyEditDraft>) => void;
}

function StepDetectionScoring({ draft, onChange }: StepDetectionScoringProps) {
  const updateDetection = (key: DetectionKey, enabled: boolean) => {
    const source = evidenceSourceByDetection[key];
    onChange({
      scoringMode: "custom",
      detections: draft.detections.map((item) => (item.key === key ? { ...item, enabled } : item)),
      scoringRules: draft.scoringRules.map((rule) => (rule.source === source ? { ...rule, enabled } : rule))
    });
  };

  const updateRule = (source: EvidenceSource, patch: Partial<ScoringRule>) => {
    onChange({
      scoringMode: "custom",
      scoringRules: draft.scoringRules.map((rule) => (rule.source === source ? { ...rule, ...patch } : rule))
    });
  };

  return (
    <div className="policy-step-content">
      <StepHeading title="检测与评分配置" description="配置检测位置、对应证据来源和重要程度。" />
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
      <div className="combined-config-table" role="table" aria-label="检测与评分配置表格">
        <div className="combined-config-head" role="row">
          <span>检测位置</span>
          <span>证据来源</span>
          <span>是否检测</span>
          <span>重要程度</span>
        </div>
        {draft.detections.map((detection) => {
          const source = evidenceSourceByDetection[detection.key];
          const rule = draft.scoringRules.find((item) => item.source === source);
          return (
            <div className="combined-config-row" role="row" key={detection.key}>
              <strong>{detection.label}</strong>
              <span>{source}</span>
              <label className="switch-control">
                <input
                  type="checkbox"
                  checked={detection.enabled}
                  onChange={(event) => updateDetection(detection.key, event.target.checked)}
                />
                <span />
              </label>
              <select
                aria-label={`${detection.label}重要程度`}
                value={rule?.importance || "中"}
                onChange={(event) =>
                  updateRule(source, { importance: event.target.value as ScoringRule["importance"] })
                }
              >
                {["低", "中", "高"].map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </div>
          );
        })}
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

function buildDraft(policy?: ResearchPolicy): PolicyEditDraft {
  const detections = detectionTemplates.map((item) => {
    const scope = detectionScopeByEditorKey[item.key];
    const enabled = policy ? Boolean(policy.detectionConfigs.find((config) => config.scope === scope)?.enabled) : true;
    return {
      ...item,
      enabled
    };
  });

  return {
    name: policy?.name || "",
    description: policy?.description || "",
    lexiconIds: policy?.lexiconIds || [],
    detections,
    scoringMode: isScoringMode(policy?.scoringMode) ? policy.scoringMode : "balanced",
    scoringRules: evidenceSources.map((source) => ({
      source,
      enabled: detections.find((item) => evidenceSourceByDetection[item.key] === source)?.enabled ?? true,
      importance: policy?.detectionConfigs.find((item) => item.evidence === source)?.importance || "中"
    }))
  };
}

function buildSaveDetectionConfigs(draft: PolicyEditDraft, policy?: ResearchPolicy): PolicyDetectionConfig[] {
  const capabilityLabels: Record<DetectionKey, string> = {
    title: "文本语义",
    comment: "评论识别",
    image: "画面文字",
    video: "视觉识别",
    audio: "语音识别"
  };
  return draft.detections.map((detection) => {
    const evidence = evidenceSourceByDetection[detection.key];
    const existing = policy?.detectionConfigs.find((item) => item.scope === detectionScopeByEditorKey[detection.key]);
    return {
      scope: detectionScopeByEditorKey[detection.key],
      location: detection.label,
      evidence,
      capability: detectionCapabilityByEditorKey[detection.key],
      capabilityLabel: existing?.capabilityLabel || capabilityLabels[detection.key],
      enabled: detection.enabled,
      importance: draft.scoringRules.find((item) => item.source === evidence)?.importance || "中"
    };
  });
}

function isScoringMode(value: string | undefined): value is ScoringMode {
  return Boolean(value && value in scoringModeLabels);
}
