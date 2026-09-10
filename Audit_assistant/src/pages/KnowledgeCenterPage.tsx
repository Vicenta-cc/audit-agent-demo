import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  BookOpen,
  ArrowLeft,
  Plus,
  ShieldCheck,
  Layers,
  ChevronDown,
  ChevronUp,
  Copy,
  Pencil,
  ArrowUp,
  ArrowDown,
  Trash2,
  Save,
  Sparkles,
  X,
  FileText
} from "lucide-react";
import {
  mockAuditRuleSets
} from "../mocks/investigationMocks";
import type {
  AuditRuleSet,
  RiskCategory,
  RiskRule
} from "../types/investigation";
import type { RuleAssistantCandidateRuleSet } from "../features/rule-assistant/types";
import type { RuleAssistantPreviewRuleChange } from "../features/rule-assistant/types";
import { useRuleAssistantWorkspace } from "../features/rule-assistant/RuleAssistantWorkspaceContext";
import { RecallLibraryManager } from "./RecallLibraryManager";

interface KnowledgeCenterPageProps {
  embedded?: boolean;
  initialRuleSetId?: string;
  initialTab?: "rulesets" | "recall";
  backLabel?: string;
  onBack?: () => void;
  onOpenRuleAssistant?: (ruleSetId: string) => void;
  preview?: {
    ruleSet: RuleAssistantCandidateRuleSet;
    fileName?: string;
    origin: "file" | "conversation";
    sourceRuleSetName: string;
    ruleChanges: Record<string, RuleAssistantPreviewRuleChange>;
    mode: "replace-or-create" | "create-only";
    onChange: (ruleSet: RuleAssistantCandidateRuleSet) => void;
    onRemoveCandidateRule: (categoryId: string, ruleId: string) => void;
    onChooseApplication: () => void;
    onApplyToRuleSet: () => void;
    onCreateRuleSet: () => void;
    onCancel: () => void;
  };
}

export function KnowledgeCenterPage({
  embedded = false,
  initialRuleSetId = "ruleset-erotic",
  initialTab = "rulesets",
  backLabel = "返回工作区",
  onBack,
  onOpenRuleAssistant,
  preview
}: KnowledgeCenterPageProps = {}) {
  const navigate = useNavigate();
  const { appliedRuleSets, upsertRuleSet } = useRuleAssistantWorkspace();
  const [activeTab, setActiveTab] = useState<"rulesets" | "recall">(initialTab);
  const [isRecallEditorOpen, setIsRecallEditorOpen] = useState(false);

  const [ruleSets, setRuleSets] = useState<AuditRuleSet[]>(preview ? [preview.ruleSet] : [
    ...mockAuditRuleSets.map((ruleSet) => appliedRuleSets[ruleSet.id] || ruleSet),
    ...Object.entries(appliedRuleSets)
      .filter(([ruleSetId]) => !mockAuditRuleSets.some((ruleSet) => ruleSet.id === ruleSetId))
      .map(([, ruleSet]) => ruleSet)
  ]);
  const [selectedRuleSetId, setSelectedRuleSetId] = useState<string>(preview?.ruleSet.id || initialRuleSetId);
  const isPreview = Boolean(preview);

  const selectedRuleSet = ruleSets.find((r) => r.id === selectedRuleSetId) || ruleSets[0];

  const handleOpenRuleAssistant = () => {
    if (onOpenRuleAssistant) {
      onOpenRuleAssistant(selectedRuleSet.id);
      return;
    }
    navigate("/investigation");
  };

  // Drawer State for Creating / Editing a Rule
  const [isRuleDrawerOpen, setIsRuleDrawerOpen] = useState(false);
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [editingCategoryId, setEditingCategoryId] = useState<string>("");
  const [editingCategoryName, setEditingCategoryName] = useState<string>("");

  const [ruleForm, setRuleForm] = useState<{
    name: string;
    content: string;
    suggestedLevel: "低风险" | "中风险" | "高风险";
    exemptionConditions: string;
    applicationStages: ("图片证据提取" | "视频关键帧提取" | "融合研判")[];
    notes: string;
    enabled: boolean;
  }>({
    name: "",
    content: "",
    suggestedLevel: "中风险",
    exemptionConditions: "",
    applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
    notes: "",
    enabled: true
  });

  // State for expanded rule cards
  const [expandedRuleIds, setExpandedRuleIds] = useState<Set<string>>(
    new Set([preview?.ruleSet.categories[0]?.rules[0]?.id || "rule-ero-2"])
  );
  const [previewDrawer, setPreviewDrawer] = useState<{ type: "summary" | "detail"; ruleId?: string } | null>(null);
  const [focusedRuleId, setFocusedRuleId] = useState<string | null>(null);
  const previewChanges = Object.entries(preview?.ruleChanges || {});
  const modifiedChanges = previewChanges.filter(([, change]) => change.kind === "modified");
  const addedChanges = previewChanges.filter(([, change]) => change.kind === "added");

  const findPreviewRule = (ruleId: string) => {
    for (const category of selectedRuleSet.categories) {
      const rule = category.rules.find((item) => item.id === ruleId);
      if (rule) return { category, rule, change: preview?.ruleChanges[ruleId] };
    }
    return null;
  };

  const focusPreviewRule = (ruleId: string) => {
    setPreviewDrawer(null);
    window.setTimeout(() => {
      document.getElementById(`preview-rule-${ruleId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
      setFocusedRuleId(ruleId);
      window.setTimeout(() => setFocusedRuleId((current) => current === ruleId ? null : current), 1600);
    }, 80);
  };

  const handleRemoveCandidateRule = (categoryId: string, ruleId: string) => {
    const nextRuleSets = ruleSets.map((ruleSet) => ruleSet.id === selectedRuleSetId ? {
      ...ruleSet,
      categories: ruleSet.categories.map((category) => category.id === categoryId ? {
        ...category,
        rules: category.rules.filter((rule) => rule.id !== ruleId)
      } : category)
    } : ruleSet);
    setRuleSets(nextRuleSets);
    preview?.onRemoveCandidateRule(categoryId, ruleId);
  };

  const toggleExpandRule = (ruleId: string) => {
    setExpandedRuleIds((prev) => {
      const next = new Set(prev);
      if (next.has(ruleId)) next.delete(ruleId);
      else next.add(ruleId);
      return next;
    });
  };

  // Helper: toggle general exemption
  const handleToggleExemption = (ruleSetId: string, exId: string) => {
    setRuleSets((prev) =>
      prev.map((rs) => {
        if (rs.id !== ruleSetId) return rs;
        return {
          ...rs,
          generalExemptions: rs.generalExemptions.map((ex) =>
            ex.id === exId ? { ...ex, enabled: !ex.enabled } : ex
          )
        };
      })
    );
  };

  // Helper: toggle rule enabled state
  const handleToggleRuleEnabled = (ruleSetId: string, catId: string, ruleId: string) => {
    setRuleSets((prev) =>
      prev.map((rs) => {
        if (rs.id !== ruleSetId) return rs;
        return {
          ...rs,
          categories: rs.categories.map((cat) => {
            if (cat.id !== catId) return cat;
            return {
              ...cat,
              rules: cat.rules.map((rl) =>
                rl.id === ruleId ? { ...rl, enabled: !rl.enabled } : rl
              )
            };
          })
        };
      })
    );
  };

  // Open drawer for creating new rule
  const handleOpenCreateRule = (catId: string, catName: string) => {
    setEditingCategoryId(catId);
    setEditingCategoryName(catName);
    setEditingRuleId(null);
    setRuleForm({
      name: "",
      content: "",
      suggestedLevel: "中风险",
      exemptionConditions: "",
      applicationStages: ["图片证据提取", "视频关键帧提取", "融合研判"],
      notes: "",
      enabled: true
    });
    setIsRuleDrawerOpen(true);
  };

  // Open drawer for editing rule
  const handleOpenEditRule = (catId: string, catName: string, rule: RiskRule) => {
    setEditingCategoryId(catId);
    setEditingCategoryName(catName);
    setEditingRuleId(rule.id);
    setRuleForm({
      name: rule.name,
      content: rule.content,
      suggestedLevel: rule.suggestedLevel,
      exemptionConditions: rule.exemptionConditions || "",
      applicationStages: rule.applicationStages || ["图片证据提取", "视频关键帧提取", "融合研判"],
      notes: rule.notes || "",
      enabled: rule.enabled
    });
    setIsRuleDrawerOpen(true);
  };

  // Copy rule
  const handleCopyRule = (catId: string, rule: RiskRule) => {
    setRuleSets((prev) =>
      prev.map((rs) => {
        if (rs.id !== selectedRuleSetId) return rs;
        return {
          ...rs,
          categories: rs.categories.map((cat) => {
            if (cat.id !== catId) return cat;
            const clonedRule: RiskRule = {
              ...rule,
              id: `rule-copy-${Date.now()}`,
              name: `${rule.name} (副本)`
            };
            return { ...cat, rules: [...cat.rules, clonedRule] };
          })
        };
      })
    );
  };

  // Move rule up/down
  const handleMoveRule = (catId: string, ruleId: string, direction: "up" | "down") => {
    setRuleSets((prev) =>
      prev.map((rs) => {
        if (rs.id !== selectedRuleSetId) return rs;
        return {
          ...rs,
          categories: rs.categories.map((cat) => {
            if (cat.id !== catId) return cat;
            const idx = cat.rules.findIndex((r) => r.id === ruleId);
            if (idx < 0) return cat;
            const targetIdx = direction === "up" ? idx - 1 : idx + 1;
            if (targetIdx < 0 || targetIdx >= cat.rules.length) return cat;
            const newRules = [...cat.rules];
            const [moved] = newRules.splice(idx, 1);
            newRules.splice(targetIdx, 0, moved);
            return { ...cat, rules: newRules };
          })
        };
      })
    );
  };

  // Delete rule
  const handleDeleteRule = (catId: string, ruleId: string) => {
    if (!window.confirm("确定要删除这条规则吗？")) return;
    setRuleSets((prev) =>
      prev.map((rs) => {
        if (rs.id !== selectedRuleSetId) return rs;
        return {
          ...rs,
          categories: rs.categories.map((cat) => {
            if (cat.id !== catId) return cat;
            return {
              ...cat,
              rules: cat.rules.filter((r) => r.id !== ruleId)
            };
          })
        };
      })
    );
  };

  // Save rule form
  const handleSaveRuleForm = () => {
    if (!ruleForm.name.trim()) {
      alert("请填写规则名称");
      return;
    }
    if (!ruleForm.content.trim()) {
      alert("请填写规则内容");
      return;
    }

    const nextRuleSets = ruleSets.map((rs) => {
        if (rs.id !== selectedRuleSetId) return rs;
        return {
          ...rs,
          categories: rs.categories.map((cat) => {
            if (cat.id !== editingCategoryId) return cat;
            if (editingRuleId) {
              return {
                ...cat,
                rules: cat.rules.map((r) =>
                  r.id === editingRuleId
                    ? {
                        ...r,
                        name: ruleForm.name.trim(),
                        content: ruleForm.content.trim(),
                        suggestedLevel: ruleForm.suggestedLevel,
                        exemptionConditions: ruleForm.exemptionConditions.trim(),
                        applicationStages: ruleForm.applicationStages,
                        notes: ruleForm.notes.trim(),
                        enabled: ruleForm.enabled
                      }
                    : r
                )
              };
            } else {
              const newRule: RiskRule = {
                id: `rule-${Date.now()}`,
                name: ruleForm.name.trim(),
                content: ruleForm.content.trim(),
                suggestedLevel: ruleForm.suggestedLevel,
                exemptionConditions: ruleForm.exemptionConditions.trim(),
                applicationStages: ruleForm.applicationStages,
                notes: ruleForm.notes.trim(),
                enabled: ruleForm.enabled
              };
              return {
                ...cat,
                rules: [...cat.rules, newRule]
              };
            }
          })
        };
      });

    setRuleSets(nextRuleSets);
    if (preview) {
      const nextCandidate = nextRuleSets.find((item) => item.id === selectedRuleSetId);
      if (nextCandidate) preview.onChange(nextCandidate as RuleAssistantCandidateRuleSet);
    }

    setIsRuleDrawerOpen(false);
  };

  // Add new risk category
  const handleAddCategory = () => {
    const name = window.prompt("请输入新风险类型名称：");
    if (!name || !name.trim()) return;
    setRuleSets((prev) =>
      prev.map((rs) => {
        if (rs.id !== selectedRuleSetId) return rs;
        const newCat: RiskCategory = {
          id: `cat-${Date.now()}`,
          name: name.trim(),
          rules: []
        };
        return { ...rs, categories: [...rs.categories, newCat] };
      })
    );
  };

  // Add General Exemption
  const handleAddGeneralExemption = () => {
    const title = window.prompt("通用豁免场景（如：新闻报道、警方通报）：");
    if (!title || !title.trim()) return;

    setRuleSets((prev) =>
      prev.map((rs) => {
        if (rs.id !== selectedRuleSetId) return rs;
        return {
          ...rs,
          generalExemptions: [
            ...rs.generalExemptions,
            { id: `gen-ex-${Date.now()}`, title: title.trim(), description: title.trim(), enabled: true }
          ]
        };
      })
    );
  };

  // Delete General Exemption
  const handleDeleteGeneralExemption = (exId: string) => {
    setRuleSets((prev) =>
      prev.map((rs) => {
        if (rs.id !== selectedRuleSetId) return rs;
        return {
          ...rs,
          generalExemptions: rs.generalExemptions.filter((ex) => ex.id !== exId)
        };
      })
    );
  };

  // Toggle stage selection in drawer form
  const handleToggleStage = (stage: "图片证据提取" | "视频关键帧提取" | "融合研判") => {
    setRuleForm((prev) => {
      const exists = prev.applicationStages.includes(stage);
      if (exists) {
        return {
          ...prev,
          applicationStages: prev.applicationStages.filter((s) => s !== stage)
        };
      } else {
        return {
          ...prev,
          applicationStages: [...prev.applicationStages, stage]
        };
      }
    });
  };

  return (
    <div className="kc-page-root" style={{ background: "#f8fafc", minHeight: embedded ? "100%" : "100vh", display: "flex", flexDirection: "column" }}>
      {/* Top Header Bar */}
      {!embedded && !isRecallEditorOpen ? <header
        style={{
          height: "56px",
          background: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
          padding: "0 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          boxShadow: "0 1px 2px rgba(0,0,0,0.02)"
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <button
            type="button"
            onClick={() => onBack ? onBack() : navigate("/investigation")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              fontSize: "13px",
              color: "#64748b",
              textDecoration: "none",
              fontWeight: "600",
              border: "none",
              background: "transparent",
              padding: 0,
              cursor: "pointer"
            }}
          >
            <ArrowLeft size={16} />
            <span>{backLabel}</span>
          </button>

          <span style={{ color: "#cbd5e1" }}>|</span>

          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <BookOpen size={18} style={{ color: "#2563eb" }} />
            <span style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a" }}>
              {isPreview ? "规则结构预览 ·" : "审核规则管理"}
            </span>
            {isPreview ? <span className="kc-preview-status">尚未应用</span> : null}
          </div>
        </div>

        {/* Navigation Segment Control */}
        {!isPreview ? <div style={{ background: "#f1f5f9", padding: "3px", borderRadius: "6px", display: "flex", gap: "2px" }}>
          <button
            type="button"
            style={{
              padding: "5px 14px",
              border: "none",
              borderRadius: "4px",
              fontSize: "12.5px",
              fontWeight: "600",
              cursor: "pointer",
              background: activeTab === "rulesets" ? "#ffffff" : "transparent",
              color: activeTab === "rulesets" ? "#2563eb" : "#64748b",
              boxShadow: activeTab === "rulesets" ? "0 1px 3px rgba(0,0,0,0.06)" : "none",
              transition: "all 0.15s ease"
            }}
            onClick={() => setActiveTab("rulesets")}
          >
            审核规则
          </button>
          <button
            type="button"
            style={{
              padding: "5px 14px",
              border: "none",
              borderRadius: "4px",
              fontSize: "12.5px",
              fontWeight: "600",
              cursor: "pointer",
              background: activeTab === "recall" ? "#ffffff" : "transparent",
              color: activeTab === "recall" ? "#2563eb" : "#64748b",
              boxShadow: activeTab === "recall" ? "0 1px 3px rgba(0,0,0,0.06)" : "none",
              transition: "all 0.15s ease"
            }}
            onClick={() => setActiveTab("recall")}
          >
            黑话库
          </button>
        </div> : null}
      </header> : null}

      {/* Main Body Area */}
      <main style={{ flex: 1, padding: "20px 24px", overflowY: "auto" }}>
        {/* TAB 1: 审核规则编辑器 */}
        {activeTab === "rulesets" ? (
          <div style={{ display: "grid", gridTemplateColumns: isPreview ? "minmax(0, 1fr)" : "240px minmax(0, 1fr)", gap: "20px", maxWidth: "1400px", margin: "0 auto" }}>
            {/* Left: Rule Set Directory Tree */}
            {!isPreview ? <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: "8px", padding: "14px", display: "flex", flexDirection: "column", gap: "10px", height: "fit-content" }}>
              <div style={{ fontSize: "12.5px", fontWeight: "700", color: "#64748b", display: "flex", alignItems: "center", gap: "6px", marginBottom: "4px" }}>
                <Layers size={15} style={{ color: "#2563eb" }} />
                <span>审核规则列表</span>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {ruleSets.map((rs) => {
                  const isSelected = rs.id === selectedRuleSetId;
                  return (
                    <div
                      key={rs.id}
                      style={{
                        padding: "10px 12px",
                        borderRadius: "6px",
                        cursor: "pointer",
                        background: isSelected ? "#eff6ff" : "#ffffff",
                        border: isSelected ? "1px solid #3b82f6" : "1px solid #f1f5f9",
                        transition: "all 0.15s ease"
                      }}
                      onClick={() => setSelectedRuleSetId(rs.id)}
                    >
                      <div style={{ fontSize: "13px", fontWeight: "700", color: isSelected ? "#1d4ed8" : "#1e293b" }}>
                        {rs.name}
                      </div>
                      <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "3px" }}>
                        <span>{rs.category}</span>
                      </div>
                    </div>
                  );
                })}
              </div>

              <button
                type="button"
                style={{
                  marginTop: "8px",
                  padding: "8px",
                  border: "1px dashed #cbd5e1",
                  borderRadius: "6px",
                  background: "#ffffff",
                  color: "#475569",
                  fontSize: "12px",
                  fontWeight: "600",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "6px"
                }}
                onClick={() => {
                  const name = window.prompt("请输入新审核规则名称：");
                  if (!name || !name.trim()) return;
                  const newSet: AuditRuleSet = {
                    id: `ruleset-${Date.now()}`,
                    name: name.trim(),
                    category: "通用性规约",
                    version: "v1.0",
                    status: "已发布",
                    updatedAt: "刚刚",
                    referencedTaskCount: 0,
                    generalExemptions: [],
                    categories: []
                  };
                  setRuleSets((prev) => [...prev, newSet]);
                  upsertRuleSet(newSet);
                  setSelectedRuleSetId(newSet.id);
                }}
              >
                <Plus size={14} />
                <span>新建审核规则</span>
              </button>
            </div> : null}

            {/* Right Main Editor Area */}
            <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: "8px", padding: "20px", display: "flex", flexDirection: "column", gap: "20px" }}>
              {/* Top Banner */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "20px", borderBottom: "1px solid #f1f5f9", paddingBottom: "14px" }}>
                <div>
                  {isPreview ? <div className="kc-preview-field-label">审核规则名称</div> : null}
                  <h2 style={{ fontSize: "16px", fontWeight: "700", color: "#0f172a", margin: "0 0 2px" }}>
                    {selectedRuleSet.name}
                  </h2>
                  <div style={{ fontSize: "12px", color: "#64748b", marginTop: isPreview ? "7px" : 0 }}>
                    {isPreview ? <><strong style={{ color: "#475569" }}>审核规则说明：</strong>{preview?.ruleSet.description}</> : selectedRuleSet.category}
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  {isPreview && preview?.origin === "file" ? (
                    <button type="button" className="kc-preview-secondary" onClick={preview.onCancel}>取消本次导入</button>
                  ) : null}
                  {isPreview && preview?.mode === "create-only" ? (
                    <>
                      <button type="button" className="kc-preview-secondary" onClick={() => onBack?.()}>返回继续调整</button>
                      <button type="button" className="kc-preview-primary" onClick={preview.onCreateRuleSet}>创建审核规则</button>
                    </>
                  ) : null}
                  {isPreview && preview?.origin === "file" && preview?.mode === "replace-or-create" ? (
                    <button type="button" className="kc-preview-primary" onClick={preview.onChooseApplication}>选择应用方式</button>
                  ) : null}
                  {isPreview && preview?.origin === "conversation" ? (
                    <button type="button" className="kc-preview-primary" onClick={preview.onApplyToRuleSet}>
                      <Save size={14} />应用到正式审核规则
                    </button>
                  ) : null}
                  {!isPreview && !embedded ? <button
                    type="button"
                    style={{
                      padding: "6px 13px",
                      background: "#ffffff",
                      color: "#245fac",
                      border: "1px solid #a9c2e2",
                      borderRadius: "6px",
                      fontSize: "12.5px",
                      fontWeight: "600",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: "6px"
                    }}
                    onClick={handleOpenRuleAssistant}
                  >
                    <Sparkles size={14} />
                    <span>返回调查对话</span>
                  </button> : null}
                  {!isPreview ? <button
                    type="button"
                    style={{
                      padding: "6px 16px",
                      background: "#2563eb",
                      color: "#ffffff",
                      border: "none",
                      borderRadius: "6px",
                      fontSize: "12.5px",
                      fontWeight: "600",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: "6px"
                    }}
                    onClick={() => alert(`审核规则《${selectedRuleSet.name}》修改已发布生效！`)}
                  >
                    <Save size={14} />
                    <span>保存发布</span>
                  </button> : null}
                </div>
              </div>

              {isPreview ? (
                <div className="kc-preview-notice">
                  <FileText size={17} />
                  <div>
                    <strong>{preview?.origin === "file" ? "当前结构由上传文件独立生成，尚未写入任何审核规则。" : `当前预览包含 ${modifiedChanges.length} 项调整和 ${addedChanges.length} 条新增规则，尚未应用。`}</strong>
                    <span>{preview?.origin === "file" ? preview.fileName : `基于“${preview?.sourceRuleSetName}”生成，仅保存在当前前端预览中。`}</span>
                  </div>
                  {preview?.origin === "conversation" && previewChanges.length ? (
                    <div className="kc-preview-notice-actions">
                      <button type="button" className="kc-preview-notice-action" onClick={() => setPreviewDrawer({ type: "summary" })}>查看变更</button>
                      <button type="button" className="kc-preview-notice-action is-primary" onClick={preview.onApplyToRuleSet}>应用到审核规则</button>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {/* Universal Exemption Strip (审核规则通用豁免条件) */}
              <div style={{ background: "#fafafa", border: "1px solid #e2e8f0", borderRadius: "6px", padding: "12px 16px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
                  <div style={{ fontSize: "13px", fontWeight: "700", color: "#334155", display: "flex", alignItems: "center", gap: "6px" }}>
                    <ShieldCheck size={15} style={{ color: "#166534" }} />
                    <span>通用豁免条件 (适用于这套审核规则中的全部规则)</span>
                  </div>
                  {!isPreview ? <button
                    type="button"
                    style={{ background: "transparent", border: "none", color: "#2563eb", fontSize: "12px", fontWeight: "600", cursor: "pointer", display: "flex", alignItems: "center", gap: "4px" }}
                    onClick={handleAddGeneralExemption}
                  >
                    <Plus size={12} />
                    <span>添加通用豁免</span>
                  </button> : null}
                </div>

                {selectedRuleSet.generalExemptions.length === 0 ? (
                  <div style={{ fontSize: "12px", color: "#94a3b8" }}>暂未配置通用豁免，针对具体规则独立判定。</div>
                ) : (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                    {selectedRuleSet.generalExemptions.map((ex) => (
                      <div
                        key={ex.id}
                        style={{
                          background: "#ffffff",
                          border: "1px solid #cbd5e1",
                          borderRadius: "4px",
                          padding: "4px 10px",
                          display: "flex",
                          alignItems: "center",
                          gap: "8px",
                          fontSize: "12px"
                        }}
                      >
                        {isPreview ? <input type="checkbox" checked readOnly aria-label={`${ex.title}，已启用`} /> : (
                          <input
                            aria-label={`${ex.title}${ex.enabled ? "，已启用" : "，已停用"}`}
                            type="checkbox"
                            checked={ex.enabled}
                            onChange={() => handleToggleExemption(selectedRuleSet.id, ex.id)}
                          />
                        )}
                        <span style={{ color: ex.enabled ? "#0f172a" : "#94a3b8" }}>{ex.title}</span>
                        {!isPreview ? (
                          <button
                            type="button"
                            aria-label={`删除通用豁免：${ex.title}`}
                            onClick={() => handleDeleteGeneralExemption(ex.id)}
                            style={{ background: "transparent", border: "none", color: "#94a3b8", cursor: "pointer", padding: 0 }}
                          >
                            <X size={12} />
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Risk Categories & Rules List */}
              <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span style={{ fontSize: "14px", fontWeight: "700", color: "#0f172a" }}>{isPreview ? "候选规则结构" : "规则管理"}</span>
                  {!isPreview ? <button
                    type="button"
                    style={{
                      padding: "4px 10px",
                      background: "#ffffff",
                      border: "1px solid #cbd5e1",
                      borderRadius: "6px",
                      fontSize: "12px",
                      fontWeight: "600",
                      color: "#334155",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: "4px"
                    }}
                    onClick={handleAddCategory}
                  >
                    <Plus size={13} />
                    <span>添加风险分类</span>
                  </button> : null}
                </div>

                {selectedRuleSet.categories.length === 0 ? (
                  <div style={{ padding: "24px", textAlign: "center", background: "#f8fafc", borderRadius: "6px", color: "#94a3b8", fontSize: "13px" }}>
                    暂无风险分类，请点击“添加风险分类”。
                  </div>
                ) : (
                  selectedRuleSet.categories.map((cat) => (
                    <div
                      key={cat.id}
                      style={{
                        border: "1px solid #e2e8f0",
                        borderRadius: "8px",
                        padding: "16px",
                        background: "#ffffff"
                      }}
                    >
                      {/* Risk Category Row */}
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          marginBottom: "12px",
                          paddingBottom: "8px",
                          borderBottom: "1px solid #f1f5f9"
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <span style={{ fontSize: "13.5px", fontWeight: "700", color: "#2563eb" }}>
                            所属风险类型：{cat.name}
                          </span>
                          <span style={{ fontSize: "11px", color: "#94a3b8" }}>({cat.rules.length} 条)</span>
                        </div>

                        {!isPreview ? <button
                          type="button"
                          style={{
                            padding: "4px 10px",
                            background: "#eff6ff",
                            border: "1px solid #bfdbfe",
                            borderRadius: "4px",
                            fontSize: "12px",
                            fontWeight: "600",
                            color: "#1d4ed8",
                            cursor: "pointer",
                            display: "flex",
                            alignItems: "center",
                            gap: "4px"
                          }}
                          onClick={() => handleOpenCreateRule(cat.id, cat.name)}
                        >
                          <Plus size={13} />
                          <span>新增风险规则</span>
                        </button> : null}
                      </div>

                      {/* Rule Cards List */}
                      {cat.rules.length === 0 ? (
                        <div style={{ padding: "12px", textAlign: "center", color: "#94a3b8", fontSize: "12px" }}>
                          该风险类型下暂无规则。
                        </div>
                      ) : (
                        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                          {cat.rules.map((rule, ruleIdx) => {
                            const isExpanded = expandedRuleIds.has(rule.id);
                            const isFirst = ruleIdx === 0;
                            const isLast = ruleIdx === cat.rules.length - 1;
                            const previewChange = preview?.ruleChanges[rule.id];
                            const isModifiedCandidate = previewChange?.kind === "modified";
                            const isAddedCandidate = previewChange?.kind === "added";

                            return (
                              <div
                                key={rule.id}
                                id={isPreview ? `preview-rule-${rule.id}` : undefined}
                                className={`kc-rule-card${isModifiedCandidate ? " is-modified-candidate" : ""}${isAddedCandidate ? " is-added-candidate" : ""}${focusedRuleId === rule.id ? " is-focused-candidate" : ""}`}
                                style={{
                                  background: "#ffffff",
                                  border: isPreview || rule.enabled ? "1px solid #cbd5e1" : "1px dashed #cbd5e1",
                                  borderLeft: previewChange ? "3px solid #77a8e8" : undefined,
                                  borderRadius: "6px",
                                  padding: "12px 16px",
                                  display: "flex",
                                  flexDirection: "column",
                                  gap: "8px",
                                  opacity: isPreview || rule.enabled ? 1 : 0.6
                                }}
                              >
                                {/* Card Line 1: Title + Risk Level Badge */}
                                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                    <span style={{ fontSize: "14px", fontWeight: "700", color: "#0f172a" }}>
                                      {rule.name}
                                    </span>
                                    {previewChange ? <span className="kc-rule-change-tag">{isModifiedCandidate ? "已调整" : "新增"}</span> : null}
                                    <span
                                      className={isModifiedCandidate && previewChange.changedFields.includes("suggestedLevel") ? "kc-changed-field" : undefined}
                                      style={{
                                        fontSize: "11px",
                                        fontWeight: "700",
                                        padding: "1px 6px",
                                        borderRadius: "4px",
                                        background:
                                          rule.suggestedLevel === "高风险"
                                            ? "#fef2f2"
                                            : rule.suggestedLevel === "中风险"
                                            ? "#fffbeb"
                                            : "#f0fdf4",
                                        color:
                                          rule.suggestedLevel === "高风险"
                                            ? "#dc2626"
                                            : rule.suggestedLevel === "中风险"
                                            ? "#b45309"
                                            : "#166534"
                                      }}
                                    >
                                      {rule.suggestedLevel}
                                    </span>
                                  </div>

                                  {!isPreview ? <span style={{ fontSize: "11.5px", color: rule.enabled ? "#166534" : "#94a3b8", fontWeight: "600" }}>
                                    {rule.enabled ? "● 已启用" : "○ 已停用"}
                                  </span> : null}
                                </div>

                                {/* Card Line 2: Rule Content (命中) */}
                                <div className={isModifiedCandidate && previewChange.changedFields.includes("content") ? "kc-changed-field" : undefined} style={{ fontSize: "12.5px", color: "#334155", lineHeight: "1.5" }}>
                                  <strong style={{ color: "#0f172a" }}>命中：</strong>
                                  {rule.content}
                                </div>

                                {/* Card Line 3: Exemption Conditions (豁免) */}
                                {rule.exemptionConditions ? (
                                  <div style={{ fontSize: "12px", color: "#475569", lineHeight: "1.5" }}>
                                    <strong style={{ color: "#15803d" }}>豁免：</strong>
                                    {rule.exemptionConditions}
                                  </div>
                                ) : null}

                                {/* Optional Notes if Expanded */}
                                {isExpanded && rule.notes ? (
                                  <div style={{ fontSize: "12px", color: "#1e40af", background: "#f8fafc", padding: "6px 10px", borderRadius: "4px", borderLeft: "2px solid #3b82f6" }}>
                                    <strong>判断注意事项：</strong> {rule.notes}
                                  </div>
                                ) : null}

                                {/* Card Line 4: Footer Stages & Action Controls */}
                                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingTop: "6px", borderTop: "1px solid #f8fafc", fontSize: "11.5px" }}>
                                  <div style={{ display: "flex", alignItems: "center", gap: "6px", color: "#64748b" }}>
                                    {rule.applicationStages.join(" · ")}
                                  </div>

                                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                    {/* Expand/Collapse */}
                                    <button
                                      type="button"
                                      onClick={() => toggleExpandRule(rule.id)}
                                      style={{ background: "transparent", border: "none", color: "#64748b", cursor: "pointer", display: "flex", alignItems: "center", gap: "2px", fontSize: "11.5px" }}
                                    >
                                      <span>{isExpanded ? "收起" : "展开"}</span>
                                      {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                                    </button>

                                    <span style={{ color: "#e2e8f0" }}>|</span>

                                    {isPreview ? <>
                                      {isModifiedCandidate ? (
                                        <button
                                          type="button"
                                          className="kc-rule-inline-action"
                                          onClick={() => setPreviewDrawer({ type: "detail", ruleId: rule.id })}
                                        >
                                          查看修改内容
                                        </button>
                                      ) : null}
                                      <button
                                        type="button"
                                        aria-label={`调整候选规则：${rule.name}`}
                                        className="kc-rule-inline-action is-primary"
                                        onClick={() => handleOpenEditRule(cat.id, cat.name, rule)}
                                      >
                                        {isModifiedCandidate ? "继续调整" : "调整"}
                                      </button>
                                      {isAddedCandidate ? (
                                        <button
                                          type="button"
                                          className="kc-rule-inline-action is-remove"
                                          onClick={() => handleRemoveCandidateRule(cat.id, rule.id)}
                                        >
                                          从预览中移除
                                        </button>
                                      ) : null}
                                    </> : <>
                                    {/* Edit */}
                                    <button
                                      type="button"
                                      aria-label={`编辑风险规则：${rule.name}`}
                                      style={{ background: "transparent", border: "none", color: "#2563eb", cursor: "pointer", fontSize: "11.5px" }}
                                      onClick={() => handleOpenEditRule(cat.id, cat.name, rule)}
                                    >
                                      编辑
                                    </button>

                                    {/* Copy */}
                                    <button
                                      type="button"
                                      style={{ background: "transparent", border: "none", color: "#475569", cursor: "pointer", fontSize: "11.5px" }}
                                      onClick={() => handleCopyRule(cat.id, rule)}
                                    >
                                      复制
                                    </button>

                                    {/* Toggle Enable */}
                                    <button
                                      type="button"
                                      style={{ background: "transparent", border: "none", color: rule.enabled ? "#dc2626" : "#166534", cursor: "pointer", fontSize: "11.5px" }}
                                      onClick={() => handleToggleRuleEnabled(selectedRuleSet.id, cat.id, rule.id)}
                                    >
                                      {rule.enabled ? "停用" : "启用"}
                                    </button>

                                    {/* Order Adjustment */}
                                    {!isFirst && (
                                      <button
                                        type="button"
                                        style={{ background: "transparent", border: "none", color: "#64748b", cursor: "pointer" }}
                                        title="上移"
                                        onClick={() => handleMoveRule(cat.id, rule.id, "up")}
                                      >
                                        <ArrowUp size={12} />
                                      </button>
                                    )}
                                    {!isLast && (
                                      <button
                                        type="button"
                                        style={{ background: "transparent", border: "none", color: "#64748b", cursor: "pointer" }}
                                        title="下移"
                                        onClick={() => handleMoveRule(cat.id, rule.id, "down")}
                                      >
                                        <ArrowDown size={12} />
                                      </button>
                                    )}

                                    {/* Delete */}
                                    <button
                                      type="button"
                                      style={{ background: "transparent", border: "none", color: "#94a3b8", cursor: "pointer" }}
                                      title="删除"
                                      onClick={() => handleDeleteRule(cat.id, rule.id)}
                                    >
                                      <Trash2 size={12} />
                                    </button>
                                    </>}
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        ) : null}

        {/* TAB 2: 黑话库 */}
        {activeTab === "recall" ? (
          <RecallLibraryManager onEditorStateChange={setIsRecallEditorOpen} />
        ) : null}
      </main>

      {/* RULE DRAWER MODAL (新增 / 编辑风险规则抽屉) */}
      {isRuleDrawerOpen ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15, 23, 42, 0.4)",
            zIndex: 1000,
            display: "flex",
            justifyContent: "flex-end"
          }}
          onClick={() => setIsRuleDrawerOpen(false)}
        >
          <div
            style={{
              width: "500px",
              height: "100%",
              background: "#ffffff",
              boxShadow: "-4px 0 24px rgba(0,0,0,0.15)",
              display: "flex",
              flexDirection: "column"
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Drawer Header */}
            <div
              style={{
                padding: "16px 20px",
                borderBottom: "1px solid #e2e8f0",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                background: "#ffffff"
              }}
            >
              <h3 style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a", margin: 0 }}>
                {isPreview ? "调整候选规则" : editingRuleId ? "编辑风险规则" : "新增风险规则"}
              </h3>
              <button
                type="button"
                onClick={() => setIsRuleDrawerOpen(false)}
                style={{ background: "transparent", border: "none", color: "#64748b", cursor: "pointer" }}
              >
                <X size={18} />
              </button>
            </div>

            {/* Drawer Form Body */}
            <div style={{ flex: 1, overflowY: "auto", padding: "20px", display: "flex", flexDirection: "column", gap: "16px" }}>
              {/* 所属风险类型 */}
              <div>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "600", color: "#64748b", marginBottom: "6px" }}>
                  所属风险类型
                </label>
                <div
                  style={{
                    padding: "8px 12px",
                    background: "#f1f5f9",
                    border: "1px solid #e2e8f0",
                    borderRadius: "6px",
                    fontSize: "13px",
                    fontWeight: "600",
                    color: "#334155"
                  }}
                >
                  {editingCategoryName}
                </div>
              </div>

              {/* 规则名称 */}
              <div>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "600", color: "#334155", marginBottom: "6px" }}>
                  规则名称 <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input
                  type="text"
                  placeholder="例如：局部怼拍"
                  value={ruleForm.name}
                  onChange={(e) => setRuleForm({ ...ruleForm, name: e.target.value })}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    border: "1px solid #cbd5e1",
                    borderRadius: "6px",
                    fontSize: "13px",
                    outline: "none"
                  }}
                />
              </div>

              {/* 规则内容 */}
              <div>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "600", color: "#334155", marginBottom: "6px" }}>
                  规则内容 <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <textarea
                  rows={4}
                  placeholder="描述什么情况下应当命中这条规则..."
                  value={ruleForm.content}
                  onChange={(e) => setRuleForm({ ...ruleForm, content: e.target.value })}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    border: "1px solid #cbd5e1",
                    borderRadius: "6px",
                    fontSize: "13px",
                    outline: "none",
                    lineHeight: "1.5",
                    resize: "vertical"
                  }}
                />
              </div>

              {/* 建议风险等级 */}
              <div>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "600", color: "#334155", marginBottom: "6px" }}>
                  建议风险等级 <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <div style={{ display: "flex", gap: "10px" }}>
                  {(["低风险", "中风险", "高风险"] as const).map((lvl) => {
                    const isSelected = ruleForm.suggestedLevel === lvl;
                    return (
                      <button
                        key={lvl}
                        type="button"
                        style={{
                          flex: 1,
                          padding: "8px 0",
                          border: isSelected ? "1.5px solid #2563eb" : "1px solid #cbd5e1",
                          borderRadius: "6px",
                          background: isSelected ? "#eff6ff" : "#ffffff",
                          color: isSelected ? "#1d4ed8" : "#475569",
                          fontSize: "12.5px",
                          fontWeight: "600",
                          cursor: "pointer"
                        }}
                        onClick={() => setRuleForm({ ...ruleForm, suggestedLevel: lvl })}
                      >
                        {isSelected ? "● " : "○ "}
                        {lvl}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* 豁免条件 */}
              <div>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "600", color: "#334155", marginBottom: "6px" }}>
                  豁免条件
                </label>
                <textarea
                  rows={3}
                  placeholder="描述哪些表面相似的情况不应依据本规则判断为风险..."
                  value={ruleForm.exemptionConditions}
                  onChange={(e) => setRuleForm({ ...ruleForm, exemptionConditions: e.target.value })}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    border: "1px solid #cbd5e1",
                    borderRadius: "6px",
                    fontSize: "13px",
                    outline: "none",
                    lineHeight: "1.5",
                    resize: "vertical"
                  }}
                />
              </div>

              {/* 应用阶段 */}
              <div>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "600", color: "#334155", marginBottom: "6px" }}>
                  应用阶段
                </label>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {(["图片证据提取", "视频关键帧提取", "融合研判"] as const).map((st) => {
                    const isChecked = ruleForm.applicationStages.includes(st);
                    return (
                      <label
                        key={st}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "8px",
                          fontSize: "12.5px",
                          color: "#334155",
                          cursor: "pointer"
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => handleToggleStage(st)}
                        />
                        <span>{st}</span>
                      </label>
                    );
                  })}
                </div>
              </div>

              {/* 判断注意事项（选填） */}
              <div>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "600", color: "#334155", marginBottom: "6px" }}>
                  判断注意事项（选填）
                </label>
                <textarea
                  rows={2}
                  placeholder="研判人员或模型需要关注的特例辅助判断事项..."
                  value={ruleForm.notes}
                  onChange={(e) => setRuleForm({ ...ruleForm, notes: e.target.value })}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    border: "1px solid #cbd5e1",
                    borderRadius: "6px",
                    fontSize: "13px",
                    outline: "none",
                    lineHeight: "1.5"
                  }}
                />
              </div>

              {/* 状态 */}
              {!isPreview ? <div>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "600", color: "#334155", marginBottom: "6px" }}>
                  规则状态
                </label>
                <div style={{ display: "flex", gap: "16px" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", fontSize: "13px", color: "#334155" }}>
                    <input
                      type="radio"
                      name="ruleStatus"
                      checked={ruleForm.enabled}
                      onChange={() => setRuleForm({ ...ruleForm, enabled: true })}
                    />
                    <span>启用</span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", fontSize: "13px", color: "#334155" }}>
                    <input
                      type="radio"
                      name="ruleStatus"
                      checked={!ruleForm.enabled}
                      onChange={() => setRuleForm({ ...ruleForm, enabled: false })}
                    />
                    <span>停用</span>
                  </label>
                </div>
              </div> : null}
            </div>

            {/* Drawer Footer Actions */}
            <div
              style={{
                padding: "16px 20px",
                borderTop: "1px solid #e2e8f0",
                display: "flex",
                alignItems: "center",
                justifyContent: "flex-end",
                gap: "12px",
                background: "#fafafa"
              }}
            >
              <button
                type="button"
                style={{
                  padding: "8px 16px",
                  background: "#ffffff",
                  border: "1px solid #cbd5e1",
                  borderRadius: "6px",
                  fontSize: "13px",
                  fontWeight: "600",
                  color: "#475569",
                  cursor: "pointer"
                }}
                onClick={() => setIsRuleDrawerOpen(false)}
              >
                取消
              </button>
              <button
                type="button"
                style={{
                  padding: "8px 20px",
                  background: "#2563eb",
                  border: "none",
                  borderRadius: "6px",
                  fontSize: "13px",
                  fontWeight: "600",
                  color: "#ffffff",
                  cursor: "pointer"
                }}
                onClick={handleSaveRuleForm}
              >
                {isPreview ? "保存调整" : "保存规则"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {previewDrawer ? (
        <div className="kc-change-drawer-backdrop" onMouseDown={() => setPreviewDrawer(null)}>
          <aside className="kc-change-drawer" aria-label={previewDrawer.type === "summary" ? "本次变更" : "规则修改内容"} onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><span>规则结构预览</span><h2>{previewDrawer.type === "summary" ? "本次变更" : "规则修改内容"}</h2></div>
              <button type="button" aria-label="关闭" onClick={() => setPreviewDrawer(null)}><X size={18} /></button>
            </header>
            {previewDrawer.type === "summary" ? (
              <div className="kc-change-drawer-body">
                <section>
                  <h3>已调整（{modifiedChanges.length}）</h3>
                  <div className="kc-change-list">
                    {modifiedChanges.map(([ruleId]) => {
                      const item = findPreviewRule(ruleId);
                      return <button type="button" key={ruleId} onClick={() => focusPreviewRule(ruleId)}><span>{item?.rule.name || ruleId}</span><ChevronDown size={14} /></button>;
                    })}
                    {!modifiedChanges.length ? <p>暂无已调整规则</p> : null}
                  </div>
                </section>
                <section>
                  <h3>新增（{addedChanges.length}）</h3>
                  <div className="kc-change-list">
                    {addedChanges.map(([ruleId]) => {
                      const item = findPreviewRule(ruleId);
                      return <button type="button" key={ruleId} onClick={() => focusPreviewRule(ruleId)}><span>{item?.rule.name || ruleId}</span><ChevronDown size={14} /></button>;
                    })}
                    {!addedChanges.length ? <p>暂无新增规则</p> : null}
                  </div>
                </section>
              </div>
            ) : (() => {
              const item = previewDrawer.ruleId ? findPreviewRule(previewDrawer.ruleId) : null;
              if (!item?.change?.before) return <div className="kc-change-drawer-body"><p>未找到修改记录。</p></div>;
              return (
                <>
                  <div className="kc-change-drawer-body">
                    <div className="kc-change-rule-heading"><span>所属风险类型</span><strong>{item.category.name} · {item.rule.name}</strong></div>
                    {item.change.changedFields.includes("suggestedLevel") ? <section><h3>风险等级</h3><div className="kc-field-comparison"><span>修改前</span><p>{item.change.before.suggestedLevel}</p></div><div className="kc-field-comparison is-after"><span>修改后</span><p>{item.rule.suggestedLevel}</p></div></section> : null}
                    {item.change.changedFields.includes("content") ? <section><h3>规则内容</h3><div className="kc-field-comparison"><span>修改前</span><p>{item.change.before.content}</p></div><div className="kc-field-comparison is-after"><span>修改后</span><p>{item.rule.content}</p></div></section> : null}
                    {item.change.changedFields.includes("exemptionConditions") ? <section><h3>豁免条件</h3><div className="kc-field-comparison"><span>修改前</span><p>{item.change.before.exemptionConditions}</p></div><div className="kc-field-comparison is-after"><span>修改后</span><p>{item.rule.exemptionConditions}</p></div></section> : null}
                  </div>
                  <footer>
                    <button type="button" onClick={() => setPreviewDrawer(null)}>关闭</button>
                    <button type="button" className="is-primary" onClick={() => { setPreviewDrawer(null); handleOpenEditRule(item.category.id, item.category.name, item.rule); }}>继续调整</button>
                  </footer>
                </>
              );
            })()}
          </aside>
        </div>
      ) : null}
    </div>
  );
}
