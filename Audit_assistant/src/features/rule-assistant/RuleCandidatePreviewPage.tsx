import { useState } from "react";
import { Check, ChevronRight, CopyPlus, RefreshCw, X } from "lucide-react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { KnowledgeCenterPage } from "../../pages/KnowledgeCenterPage";
import { useRuleAssistantWorkspace } from "./RuleAssistantWorkspaceContext";

type ApplyDialog = "apply" | "choice" | "replace" | "create" | null;

function createResultMessage(content: string) {
  return {
    id: `rule-apply-${Date.now()}`,
    role: "assistant" as const,
    kind: "text" as const,
    content
  };
}

export function RuleCandidatePreviewPage() {
  const navigate = useNavigate();
  const { previewId } = useParams();
  const {
    ruleSets,
    candidatePreviews,
    appliedRuleSets,
    setActiveConversationId,
    updateConversation,
    updateCandidateRuleSet,
    removeCandidateRule,
    applyCandidatePreview,
    completeCandidatePreview,
    addRuleSet
  } = useRuleAssistantWorkspace();
  const preview = previewId ? candidatePreviews[decodeURIComponent(previewId)] : undefined;
  const [dialog, setDialog] = useState<ApplyDialog>(null);
  const [newRuleSetName, setNewRuleSetName] = useState(preview?.ruleSet.name || "");
  const [newRuleSetDescription, setNewRuleSetDescription] = useState(preview?.ruleSet.description || "");

  if (!preview || preview.status === "cancelled") return <Navigate to="/rule-assistant" replace />;

  const sourceRuleSetName = (preview.sourceRuleSetId ? appliedRuleSets[preview.sourceRuleSetId]?.name : undefined)
    || ruleSets.find((item) => item.id === preview.sourceRuleSetId)?.name
    || "色情低俗风险规则集";

  const returnToConversation = () => {
    setActiveConversationId(preview.conversationId);
    navigate("/rule-assistant");
  };

  const cancelImport = () => {
    updateConversation(preview.conversationId, (conversation) => ({
      ...conversation,
      updatedAt: "刚刚",
      messages: conversation.messages.map((message) => message.previewId === preview.id && message.kind === "file-analysis"
        ? { ...message, status: "cancelled" as const }
        : message)
    }));
    completeCandidatePreview(preview.id, "cancelled");
    returnToConversation();
  };

  const appendResult = (content: string) => {
    updateConversation(preview.conversationId, (conversation) => ({
      ...conversation,
      updatedAt: "刚刚",
      messages: [
        ...conversation.messages.map((message) => message.previewId === preview.id ? { ...message, status: "completed" as const } : message),
        createResultMessage(content)
      ]
    }));
  };

  const handleReplace = () => {
    applyCandidatePreview(preview.id, "replaced");
    appendResult(`已确认使用“${preview.ruleSet.name}”替换“${sourceRuleSetName}”。后续新增或修改将以这份结构为基线。`);
    setDialog(null);
    returnToConversation();
  };

  const handleApply = () => {
    applyCandidatePreview(preview.id);
    appendResult(`已将本次 ${modifiedCount} 项调整和 ${addedCount} 条新增规则应用到“${sourceRuleSetName}”。`);
    setDialog(null);
    returnToConversation();
  };

  const handleCreate = () => {
    const name = newRuleSetName.trim();
    if (!name) return;
    addRuleSet(name, newRuleSetDescription.trim(), preview.ruleSet, preview.conversationId);
    completeCandidatePreview(preview.id, "created");
    appendResult(`已将候选结构保存为新的规则集“${name}”。`);
    setDialog(null);
    navigate("/rule-assistant");
  };

  const openCreateDialog = () => {
    setNewRuleSetName(preview.ruleSet.name);
    setNewRuleSetDescription(preview.ruleSet.description);
    setDialog("create");
  };

  const changes = Object.values(preview.ruleChanges);
  const modifiedCount = changes.filter((change) => change.kind === "modified").length;
  const addedCount = changes.filter((change) => change.kind === "added").length;

  return (
    <>
      <KnowledgeCenterPage
        backLabel="返回规则对话"
        onBack={returnToConversation}
        preview={{
          ruleSet: preview.ruleSet,
          fileName: preview.fileName,
          origin: preview.origin,
          sourceRuleSetName,
          ruleChanges: preview.ruleChanges,
          mode: preview.mode,
          onChange: (ruleSet) => updateCandidateRuleSet(preview.id, ruleSet),
          onRemoveCandidateRule: (categoryId, ruleId) => removeCandidateRule(preview.id, categoryId, ruleId),
          onChooseApplication: () => setDialog("choice"),
          onApplyToRuleSet: () => setDialog("apply"),
          onCreateRuleSet: openCreateDialog,
          onCancel: cancelImport
        }}
      />

      {dialog ? (
        <div className="ra-modal-backdrop" role="presentation" onMouseDown={() => setDialog(null)}>
          <div className="ra-modal" role="dialog" aria-modal="true" aria-labelledby="ra-apply-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="ra-modal-head">
              <div>
                <h2 id="ra-apply-title">
                  {dialog === "apply" ? "应用到正式规则集" : dialog === "choice" ? "选择应用方式" : dialog === "replace" ? "替换当前规则集" : preview.mode === "create-only" ? "创建规则集" : "保存为新规则集"}
                </h2>
                <p>{dialog === "apply" ? `目标规则集：${sourceRuleSetName}` : "确认后将同步更新规则资源"}</p>
              </div>
              <button type="button" aria-label="关闭" onClick={() => setDialog(null)}><X size={18} /></button>
            </div>

            {dialog === "choice" ? (
              <div className="ra-apply-choice-list">
                <button type="button" className="ra-apply-choice" onClick={() => setDialog("replace")}>
                  <div><strong>替换当前规则集</strong><span>使用完整候选结构替换当前规则集，不进行自动合并。</span></div>
                  <RefreshCw size={17} />
                </button>
                <button type="button" className="ra-apply-choice" onClick={openCreateDialog}>
                  <div><strong>保存为新规则集</strong><span>保留当前规则集，并在左侧新增一个规则集。</span></div>
                  <CopyPlus size={17} />
                </button>
              </div>
            ) : null}

            {dialog === "apply" ? (
              <>
                <div className="ra-apply-summary">
                  <div><Check size={16} /><strong>本次将应用的变更</strong></div>
                  <dl>
                    <div><dt>规则调整</dt><dd>{modifiedCount} 项</dd></div>
                    <div><dt>新增规则</dt><dd>{addedCount} 条</dd></div>
                  </dl>
                  <p>确认后，当前正式规则集将更新为这份预览中的规则结构。</p>
                </div>
                <div className="ra-modal-actions">
                  <button type="button" onClick={() => setDialog(null)}>取消</button>
                  <button type="button" className="is-primary" onClick={handleApply}>确认应用</button>
                </div>
              </>
            ) : null}

            {dialog === "replace" ? (
              <>
                <p className="ra-apply-confirm-copy">{`确认替换“${sourceRuleSetName}”？\n\n当前规则集中的通用豁免、风险分类和风险规则，将全部替换为本次预览内容。\n\n本次操作不是合并。`}</p>
                <div className="ra-modal-actions">
                  <button type="button" onClick={() => setDialog("choice")}>取消</button>
                  <button type="button" className="is-primary" onClick={handleReplace}>确认替换</button>
                </div>
              </>
            ) : null}

            {dialog === "create" ? (
              <>
                <div className="ra-apply-form">
                  <label>规则集名称<input autoFocus value={newRuleSetName} onChange={(event) => setNewRuleSetName(event.target.value)} /></label>
                  <label>规则集说明<textarea value={newRuleSetDescription} onChange={(event) => setNewRuleSetDescription(event.target.value)} /></label>
                </div>
                <div className="ra-modal-actions">
                  <button type="button" onClick={() => preview.mode === "create-only" ? returnToConversation() : setDialog("choice")}>
                    {preview.mode === "create-only" ? "返回继续调整" : "取消"}
                  </button>
                  <button type="button" className="is-primary" disabled={!newRuleSetName.trim()} onClick={handleCreate}>
                    {preview.mode === "create-only" ? "创建规则集" : "保存为新规则集"}<ChevronRight size={14} />
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
