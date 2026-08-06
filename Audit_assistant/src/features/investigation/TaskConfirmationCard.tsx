import { useState } from "react";
import { PencilLine, Play } from "lucide-react";
import type { TaskDraft } from "../../types/investigation";

const platformLabels = {
  dy: "抖音",
  xhs: "小红书",
  ks: "快手",
  wb: "微博",
  multi: "多平台"
} as const;

interface TaskConfirmationCardProps {
  draft: TaskDraft;
  onModifyConfig: () => void;
  onStartExecution: () => void;
  onSaveDraft?: () => void;
}

export function TaskConfirmationCard({
  draft,
  onModifyConfig,
  onStartExecution,
  onSaveDraft
}: TaskConfirmationCardProps) {
  const [isSaved, setIsSaved] = useState(false);
  const platformsText = draft.platforms.map((platform) => platformLabels[platform]).join("、");

  const handleSaveDraft = () => {
    onSaveDraft?.();
    setIsSaved(true);
  };

  return (
    <section className="task-final-confirm-card" aria-label="最终任务确认卡">
      <h3>任务配置确认</h3>

      <div className="task-final-fields">
        <div className="task-final-field">
          <span>任务名称</span>
          <strong>{draft.taskName}</strong>
        </div>
        <div className="task-final-field">
          <span>采集平台</span>
          <strong>{platformsText}</strong>
        </div>
        <div className="task-final-field">
          <span>本次搜索词</span>
          <strong>{draft.keywords.join("、")}</strong>
        </div>
        <div className="task-final-field">
          <span>推荐研判方案</span>
          <strong>{draft.analysisPlanName || draft.matchedRuleSet}</strong>
        </div>
      </div>

      <div className="task-final-actions">
        <button type="button" className="task-save-draft-action" onClick={handleSaveDraft}>
          {isSaved ? "草稿已保存" : "保存草稿"}
        </button>
        <button type="button" className="mt-button mt-button-secondary" onClick={onModifyConfig}>
          <PencilLine size={14} aria-hidden="true" />
          修改配置
        </button>
        <button type="button" className="mt-button mt-button-primary" onClick={onStartExecution}>
          <Play size={14} aria-hidden="true" />
          创建并开始调查
        </button>
      </div>
    </section>
  );
}
