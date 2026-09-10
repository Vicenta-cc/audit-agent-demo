import { useState } from "react";
import {
  FileText,
  Plus,
  X,
  ShieldCheck,
  Play,
  Save,
  CheckCircle2,
  Edit3,
  ChevronDown,
  ChevronUp
} from "lucide-react";
import { PlatformIcon } from "../../components/common/PlatformIcon";
import type { TaskDraft, PlatformCode } from "../../types/investigation";
import { platformOptionsList } from "../../mocks/investigationMocks";

interface TaskConfigCardProps {
  draft: TaskDraft;
  onUpdateKeywords: (keywords: string[]) => void;
  onUpdatePlatforms: (platforms: PlatformCode[]) => void;
  onStartExecution: () => void;
  onSaveDraft?: () => void;
  onOpenRulesetDrawer?: () => void;
  onOpenConfigDrawer?: () => void;
}

export function TaskConfigCard({
  draft,
  onUpdateKeywords,
  onUpdatePlatforms,
  onStartExecution,
  onSaveDraft,
  onOpenRulesetDrawer,
  onOpenConfigDrawer
}: TaskConfigCardProps) {
  const [newKeywordInput, setNewKeywordInput] = useState("");
  const [isAddingKeyword, setIsAddingKeyword] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [isExpandedOverride, setIsExpandedOverride] = useState(false);

  const handleRemoveKeyword = (wordToRemove: string) => {
    onUpdateKeywords(draft.keywords.filter((w) => w !== wordToRemove));
  };

  const handleAddKeyword = () => {
    if (!newKeywordInput.trim()) return;
    if (!draft.keywords.includes(newKeywordInput.trim())) {
      onUpdateKeywords([...draft.keywords, newKeywordInput.trim()]);
    }
    setNewKeywordInput("");
    setIsAddingKeyword(false);
  };

  const handleTogglePlatform = (code: PlatformCode) => {
    if (draft.platforms.includes(code)) {
      if (draft.platforms.length > 1) {
        onUpdatePlatforms(draft.platforms.filter((p) => p !== code));
      }
    } else {
      onUpdatePlatforms([...draft.platforms, code]);
    }
  };

  const isCreated = draft.status === "已创建" || draft.confirmed;
  const platformNames = draft.platforms
    .map((code) => platformOptionsList.find((platform) => platform.code === code)?.label || code)
    .join("、");

  // Section 7: If task is started/created, auto-collapse into lightweight summary row unless expanded
  if (isCreated && !isExpandedOverride) {
    return (
      <div className="task-summary-collapsed-card" aria-label="任务配置轻量摘要">
        <div className="task-summary-left">
          <div className="task-summary-title-row">
            <CheckCircle2 size={15} style={{ color: "var(--color-success)", flexShrink: 0 }} />
            <span className="task-summary-name">{draft.taskName}</span>
            <span className="task-summary-badge">任务配置已确认</span>
          </div>
          <div className="task-summary-detail-row">
            <span>{platformNames || "全平台"}</span>
            <span className="dot-sep">·</span>
            <span>{draft.keywords.length} 个召回词</span>
            <span className="dot-sep">·</span>
            <span>{draft.matchedRuleSet}</span>
          </div>
        </div>

        <button
          type="button"
          className="mt-button mt-button-secondary mt-button-small"
          onClick={() => {
            if (onOpenConfigDrawer) {
              onOpenConfigDrawer();
            } else {
              setIsExpandedOverride(true);
            }
          }}
        >
          <span>查看配置</span>
          <ChevronDown size={13} />
        </button>
      </div>
    );
  }

  return (
    <div className="inv-msg-asst-card task-confirm-card" aria-label="研判任务确认卡">
      {/* Assistant Intro Message */}
      <div className="task-asst-intro">
        已根据你的调查目标生成任务配置，请确认。
      </div>

      <div className="task-confirm-body">
        {/* Task Name */}
        <div className="task-confirm-row">
          <span className="task-confirm-label">任务名称</span>
          <strong className="task-confirm-value">{draft.taskName}</strong>
        </div>

        {/* Recall Words */}
        <div className="task-confirm-row">
          <span className="task-confirm-label">临时召回词</span>
          <div className="task-confirm-tags">
            {draft.keywords.map((word) => (
              <span key={word} className="inv-recall-tag">
                <span>{word}</span>
                {!isCreated && isEditing ? (
                  <button
                    type="button"
                    className="inv-recall-del"
                    onClick={() => handleRemoveKeyword(word)}
                    title="删除"
                  >
                    <X size={11} />
                  </button>
                ) : null}
              </span>
            ))}
            {!isCreated && isEditing ? (
              isAddingKeyword ? (
                <div style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                  <input
                    type="text"
                    value={newKeywordInput}
                    onChange={(e) => setNewKeywordInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleAddKeyword()}
                    placeholder="输入召回词..."
                    style={{
                      fontSize: "12px",
                      padding: "2px 6px",
                      border: "1px solid var(--color-primary)",
                      borderRadius: "4px",
                      outline: "none"
                    }}
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={handleAddKeyword}
                    className="mt-button mt-button-primary mt-button-small"
                    style={{ height: "24px", padding: "0 8px", fontSize: "12px" }}
                  >
                    确认
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="inv-add-tag-btn"
                  onClick={() => setIsAddingKeyword(true)}
                >
                  <Plus size={12} />
                  <span>添加词</span>
                </button>
              )
            ) : null}
          </div>
        </div>

        {/* Platforms */}
        <div className="task-confirm-row">
          <span className="task-confirm-label">采集平台</span>
          <div className="task-confirm-platforms">
            {platformOptionsList.map((p) => {
              const selected = draft.platforms.includes(p.code);
              if (!isEditing && !selected) return null;
              return (
                <button
                  key={p.code}
                  type="button"
                  className={`inv-platform-chip ${selected ? "is-selected" : ""}`}
                  onClick={() => isEditing && !isCreated && handleTogglePlatform(p.code)}
                  disabled={!isEditing || isCreated}
                  style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                >
                  <PlatformIcon platform={p.code} size={15} />
                  <span>{p.label}</span>
                  {selected ? <CheckCircle2 size={12} style={{ color: "var(--color-primary)" }} /> : null}
                </button>
              );
            })}
          </div>
        </div>

        {/* Rule Set Row */}
        <div className="task-confirm-ruleset-line">
          <ShieldCheck size={15} style={{ color: "var(--color-primary)", flexShrink: 0 }} />
          <span className="ruleset-name">{draft.matchedRuleSet}</span>
          <span className="inv-status-pill st-completed" style={{ fontSize: "11px", padding: "1px 6px" }}>
            自动匹配
          </span>
          {onOpenRulesetDrawer ? (
            <button
              type="button"
              className="ruleset-text-btn"
              onClick={onOpenRulesetDrawer}
            >
              查看审核规则
            </button>
          ) : null}
        </div>

        {/* Bottom Actions */}
        <div className="task-confirm-actions">
          {!isCreated ? (
            <>
              <button
                type="button"
                className="mt-button mt-button-secondary mt-button-small"
                onClick={() => setIsEditing((prev) => !prev)}
              >
                <Edit3 size={13} />
                <span>{isEditing ? "完成修改" : "修改配置"}</span>
              </button>

              <button
                type="button"
                className="mt-button mt-button-secondary mt-button-small"
                onClick={onSaveDraft}
              >
                <Save size={13} />
                <span>保存草稿</span>
              </button>

              <button
                type="button"
                className="mt-button mt-button-primary mt-button-small"
                onClick={onStartExecution}
              >
                <Play size={13} />
                <span>创建并开始调查</span>
              </button>
            </>
          ) : (
            <button
              type="button"
              className="mt-button mt-button-secondary mt-button-small"
              onClick={() => setIsExpandedOverride(false)}
            >
              <span>收起配置</span>
              <ChevronUp size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
