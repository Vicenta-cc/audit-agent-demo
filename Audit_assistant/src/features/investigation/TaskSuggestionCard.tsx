import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Bot, Check, Edit3, ExternalLink } from "lucide-react";
import type { PlatformCode, TaskDraft } from "../../types/investigation";
import type { RuleSetProposalPresentation } from "../../types/investigationCreation";
import { GeneratedRulesMessage } from "./GeneratedRulesMessage";
import type { ConfirmationPreview } from "../../types/investigationCreation";
import {
  formatConfirmationBlockerMessage,
  formatCreationErrorMessage
} from "./confirmationView";
import { StreamingAssistantText } from "./StreamingAssistantText";
import {
  buildSuggestionBlockerLink,
  selectSingleSuggestionPlatform
} from "./suggestionPresentation";

export {
  buildSuggestionAssistantCopy,
  buildSuggestionBlockerLink,
  selectSingleSuggestionPlatform
} from "./suggestionPresentation";

const defaultPlatformOptions: Array<{ code: PlatformCode; label: string; available?: boolean }> = [
  { code: "dy", label: "抖音", available: true },
  { code: "xhs", label: "小红书", available: false },
  { code: "ks", label: "快手", available: false },
  { code: "wb", label: "微博", available: false },
  { code: "xy", label: "闲鱼", available: false }
];

interface TaskSuggestionCardProps {
  assistantContent: string;
  literalAssistantContent?: boolean;
  rulePresentations?: RuleSetProposalPresentation[];
  shouldStream: boolean;
  showSuggestionCard: boolean;
  onStreamingComplete: () => void;
  draft: TaskDraft;
  preview?: ConfirmationPreview;
  platformOptions?: Array<{ code: PlatformCode; label: string; available?: boolean }>;
  isReadOnly: boolean;
  onUpdatePlatforms: (platforms: PlatformCode[]) => void;
  onOpenKeywordEditor?: () => void;
  onGenerateConfig: () => void;
  onOpenAnalysisPlan: () => void;
  error?: string;
}

export function TaskSuggestionCard({
  assistantContent,
  literalAssistantContent = false,
  rulePresentations = [],
  shouldStream,
  showSuggestionCard,
  onStreamingComplete,
  draft,
  preview,
  platformOptions = defaultPlatformOptions,
  isReadOnly,
  onUpdatePlatforms,
  onOpenKeywordEditor,
  onGenerateConfig,
  onOpenAnalysisPlan,
  error = ""
}: TaskSuggestionCardProps) {
  const [isKeywordsExpanded, setIsKeywordsExpanded] = useState(false);
  const [isKeywordsOverflowing, setIsKeywordsOverflowing] = useState(false);
  const keywordsRef = useRef<HTMLParagraphElement>(null);
  const keywordsText = draft.keywords.join("、");
  const blockerLink = buildSuggestionBlockerLink(preview);
  const hasAnalysisPlan = preview
    ? Boolean(preview.ruleset_revision || preview.temporary_ruleset)
    : Boolean(draft.analysisPlanName || draft.matchedRuleSet);
  const displayPlatformOptions = defaultPlatformOptions.map((fallback) => {
    const authoritative = platformOptions.find((item) => item.code === fallback.code);
    return {
      code: fallback.code,
      label: authoritative?.label || fallback.label,
      // Rollout is intentionally Douyin-only. Backend options can add labels,
      // but cannot accidentally make an unopened platform interactive.
      available: fallback.code === "dy" && authoritative?.available === true
    };
  });

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
  }, [isKeywordsExpanded, keywordsText, preview?.creator_url]);

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
        {literalAssistantContent ? (
          <GeneratedRulesMessage content={assistantContent} presentations={rulePresentations} />
        ) : <StreamingAssistantText
          text={assistantContent}
          shouldStream={shouldStream}
          className="task-suggestion-copy"
          onComplete={onStreamingComplete}
        />}
      </div>

      {showSuggestionCard && draft.historicalConfiguration?.length ? (
        <section className="task-suggestion-card is-read-only" aria-label="历史报告配置">
          <div className="task-suggestion-header">
            <h3>{draft.taskName}</h3>
            <span className="task-suggestion-status is-ready">已有审核资料</span>
          </div>
          <div className="task-suggestion-sections">
            <section className="task-suggestion-section">
              <span className="task-suggestion-label">实际来源词</span>
              <p>{draft.keywords.join("、")}</p>
            </section>
            <section className="task-suggestion-section">
              <span className="task-suggestion-label">分析范围</span>
              <p>{draft.scopeDescription || draft.ruleSetDescription}</p>
            </section>
            <button type="button" className="task-suggestion-text-action" onClick={onOpenAnalysisPlan}>
              查看原始配置与规则
            </button>
          </div>
        </section>
      ) : showSuggestionCard ? <section className={`task-suggestion-card${isReadOnly ? " is-read-only" : ""}`} aria-label="任务建议卡片">
        <div className="task-suggestion-header">
          <h3>{draft.taskName}</h3>
          <span className={`task-suggestion-status${isReadOnly ? " is-ready" : ""}`}>
            {isReadOnly
              ? "已生成配置"
              : preview?.mode === "creator"
                ? "待核对主页"
                : "待选择平台"}
          </span>
        </div>

        <div className="task-suggestion-sections">
          <section className="task-suggestion-section">
            <div className="task-suggestion-label-row">
              <span className="task-suggestion-label">
                {preview?.mode === "creator" ? "博主主页 URL" : "本次实际搜索词"}
              </span>
              <span className="task-suggestion-note">
                {preview?.mode === "creator" ? "主页采集" : "优先使用黑话变体召回"}
              </span>
            </div>
            <div className="task-suggestion-field-body">
              <div className="task-suggestion-keyword-line">
                <p
                  ref={keywordsRef}
                  className={`task-suggestion-keywords${isKeywordsExpanded ? " is-expanded" : ""}`}
                >
                  {preview?.mode === "creator"
                    ? preview.creator_url
                    : keywordsText || "暂未生成可用召回词"}
                </p>
                {preview?.mode !== "creator" && onOpenKeywordEditor && !draft.confirmed ? (
                  <button type="button" className="task-suggestion-text-action task-suggestion-edit-terms" onClick={onOpenKeywordEditor}>
                    <Edit3 size={13} aria-hidden="true" /> 编辑主题与变体
                  </button>
                ) : null}
              </div>
              {preview?.mode !== "creator" && draft.confirmed ? (
                <p className="task-suggestion-locked-note">任务已启动，搜索词已冻结；如需修改，请新建调查。</p>
              ) : null}
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
            <div className="task-suggestion-label">推荐审核规则</div>
            <div className="task-suggestion-field-body task-suggestion-plan-body">
              <div className="task-plan-row">
                <div className="task-suggestion-plan-heading">
                  <div className={`task-suggestion-value${hasAnalysisPlan ? "" : " is-missing"}`}>
                    {hasAnalysisPlan
                      ? draft.analysisPlanName || draft.matchedRuleSet
                      : "审核规则待配置"}
                  </div>
                  {hasAnalysisPlan ? <span className="task-suggestion-recommend-tag">系统推荐</span> : null}
                </div>
                <button
                  type="button"
                  className="task-suggestion-text-action"
                  onClick={onOpenAnalysisPlan}
                >
                  查看规则详情
                </button>
              </div>
              <p className="task-suggestion-help">
                {hasAnalysisPlan ? draft.ruleSetDescription : "选择匹配的已发布审核规则后，系统将校验规则版本。"}
                {draft.recommendedRecallLexicons?.length
                  ? ` · 推荐黑话库：${draft.recommendedRecallLexicons.join("、")}`
                  : ""}
              </p>
            </div>
          </section>

          <section className="task-suggestion-section">
            <div className="task-suggestion-label">采集平台</div>
            <div className="task-suggestion-field-body task-suggestion-platforms" aria-label="采集平台单选">
              {displayPlatformOptions.map((platform) => {
                const isSelected = draft.platforms.includes(platform.code);
                return (
                  <button
                    key={platform.code}
                    type="button"
                    className={`task-platform-option${isSelected ? " is-selected" : ""}`}
                    aria-pressed={isSelected}
                    disabled={isReadOnly || platform.available === false}
                    onClick={() => handleTogglePlatform(platform.code)}
                  >
                    {isSelected ? <Check size={13} aria-hidden="true" /> : null}
                    <span>{platform.label}</span>
                    {platform.available === false ? <small>暂不可用</small> : null}
                  </button>
                );
              })}
            </div>
          </section>
        </div>

        {preview?.blockers.length ? (
          <div className="task-suggestion-blockers" role="alert">
            {preview.blockers.map((blocker) => (
              <div key={`${blocker.code}-${blocker.resource_id}`}>
                <AlertTriangle size={15} aria-hidden="true" />
                <span>{formatConfirmationBlockerMessage(blocker.code, blocker.message)}</span>
              </div>
            ))}
            {blockerLink ? (
              <a href={blockerLink.managementUrl}>
                {blockerLink.label} <ExternalLink size={13} aria-hidden="true" />
              </a>
            ) : null}
          </div>
        ) : null}

        {error && !preview?.blockers.length ? (
          <div className="task-suggestion-inline-error" role="alert">
            <AlertTriangle size={15} aria-hidden="true" />
            <span>{formatCreationErrorMessage(error)}</span>
          </div>
        ) : null}

        <div className="task-suggestion-actions">
          <button
            type="button"
            className="mt-button mt-button-primary"
            disabled={isReadOnly || draft.platforms.length === 0 || Boolean(preview?.blockers.some(
              (blocker) => blocker.code !== "TEMPORARY_RULESET_EXECUTION_UNAVAILABLE"
            ))}
            onClick={onGenerateConfig}
          >
            生成任务配置
          </button>
        </div>
      </section> : null}
    </div>
  );
}
