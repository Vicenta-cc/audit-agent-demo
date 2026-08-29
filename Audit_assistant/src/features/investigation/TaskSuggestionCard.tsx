import { useEffect, useRef, useState } from "react";
import { Bot, Check } from "lucide-react";
import type { PlatformCode, TaskDraft } from "../../types/investigation";
import { StreamingAssistantText } from "./StreamingAssistantText";

const defaultPlatformOptions: Array<{ code: PlatformCode; label: string }> = [
  { code: "dy", label: "抖音" },
  { code: "xhs", label: "小红书" },
  { code: "ks", label: "快手" }
];

export function selectSingleSuggestionPlatform(
  platform: PlatformCode
): PlatformCode[] {
  return [platform];
}

interface TaskSuggestionCardProps {
  assistantContent: string;
  shouldStream: boolean;
  showSuggestionCard: boolean;
  onStreamingComplete: () => void;
  draft: TaskDraft;
  platformOptions?: Array<{ code: PlatformCode; label: string }>;
  isReadOnly: boolean;
  onUpdatePlatforms: (platforms: PlatformCode[]) => void;
  onGenerateConfig: () => void;
  onOpenAnalysisPlan: () => void;
}

export function TaskSuggestionCard({
  assistantContent,
  shouldStream,
  showSuggestionCard,
  onStreamingComplete,
  draft,
  platformOptions = defaultPlatformOptions,
  isReadOnly,
  onUpdatePlatforms,
  onGenerateConfig,
  onOpenAnalysisPlan
}: TaskSuggestionCardProps) {
  const [isKeywordsExpanded, setIsKeywordsExpanded] = useState(false);
  const [isKeywordsOverflowing, setIsKeywordsOverflowing] = useState(false);
  const keywordsRef = useRef<HTMLParagraphElement>(null);
  const keywordsText = draft.keywords.join("、");

  useEffect(() => {
    const element = keywordsRef.current;
    if (!element) return;

    const measureOverflow = () => {
      if (!isKeywordsExpanded) {
        setIsKeywordsOverflowing(element.scrollHeight > element.clientHeight + 1);
      }
    };

    measureOverflow();
    const observer = new ResizeObserver(measureOverflow);
    observer.observe(element);
    return () => observer.disconnect();
  }, [isKeywordsExpanded, keywordsText]);

  const handleTogglePlatform = (platform: PlatformCode) => {
    if (isReadOnly) return;
    if (draft.platforms[0] === platform) return;
    onUpdatePlatforms(selectSingleSuggestionPlatform(platform));
  };

  return (
    <div className="task-suggestion-message-group">
      <div className="inv-msg-asst-card task-suggestion-intro">
        <div className="inv-asst-head">
          <Bot size={16} />
          <span>研判助手</span>
        </div>
        <StreamingAssistantText
          text={assistantContent}
          shouldStream={shouldStream}
          className="task-suggestion-copy"
          onComplete={onStreamingComplete}
        />
      </div>

      {showSuggestionCard ? <section className={`task-suggestion-card${isReadOnly ? " is-read-only" : ""}`} aria-label="任务建议卡片">
        <div className="task-suggestion-header">
          <h3>{draft.taskName}</h3>
          <span className={`task-suggestion-status${isReadOnly ? " is-ready" : ""}`}>
            {isReadOnly ? "已生成配置" : "待选择平台"}
          </span>
        </div>

        <div className="task-suggestion-sections">
          <section className="task-suggestion-section">
            <div className="task-suggestion-label-row">
              <span className="task-suggestion-label">本次搜索词</span>
              <span className="task-suggestion-note">仅用于本次任务</span>
            </div>
            <div className="task-suggestion-field-body">
              <p
                ref={keywordsRef}
                className={`task-suggestion-keywords${isKeywordsExpanded ? " is-expanded" : ""}`}
              >
                {keywordsText}
              </p>
              {isKeywordsOverflowing ? (
                <button
                  type="button"
                  className="task-suggestion-text-action"
                  onClick={() => setIsKeywordsExpanded((current) => !current)}
                >
                  {isKeywordsExpanded ? "收起" : "展开全部"}
                </button>
              ) : null}
            </div>
          </section>

          <section className="task-suggestion-section task-suggestion-plan-section">
            <div className="task-suggestion-label">推荐研判方案</div>
            <div className="task-suggestion-field-body task-suggestion-plan-body">
              <div className="task-plan-row">
                <div className="task-suggestion-plan-heading">
                  <div className="task-suggestion-value">
                    {draft.analysisPlanName || draft.matchedRuleSet}
                  </div>
                  <span className="task-suggestion-recommend-tag">系统推荐</span>
                </div>
                <button
                  type="button"
                  className="task-suggestion-text-action"
                  onClick={onOpenAnalysisPlan}
                >
                  查看或调整
                </button>
              </div>
              <p className="task-suggestion-help">
                {draft.ruleSetDescription}
                {draft.recommendedRecallLexicons?.length
                  ? ` · 推荐召回词库：${draft.recommendedRecallLexicons.join("、")}`
                  : ""}
              </p>
            </div>
          </section>

          <section className="task-suggestion-section">
            <div className="task-suggestion-label">采集平台</div>
            <div className="task-suggestion-field-body task-suggestion-platforms" aria-label="采集平台单选">
              {platformOptions.map((platform) => {
                const isSelected = draft.platforms.includes(platform.code);
                return (
                  <button
                    key={platform.code}
                    type="button"
                    className={`task-platform-option${isSelected ? " is-selected" : ""}`}
                    aria-pressed={isSelected}
                    disabled={isReadOnly}
                    onClick={() => handleTogglePlatform(platform.code)}
                  >
                    {isSelected ? <Check size={13} aria-hidden="true" /> : null}
                    <span>{platform.label}</span>
                  </button>
                );
              })}
            </div>
          </section>
        </div>

        <div className="task-suggestion-actions">
          <button
            type="button"
            className="mt-button mt-button-primary"
            disabled={isReadOnly || draft.platforms.length === 0}
            onClick={onGenerateConfig}
          >
            生成任务配置
          </button>
        </div>
      </section> : null}
    </div>
  );
}
