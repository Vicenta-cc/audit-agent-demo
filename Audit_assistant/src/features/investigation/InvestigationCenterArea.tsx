import { Fragment, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Send,
  Sparkles,
  Bot,
  PanelLeft,
  ChevronRight,
  CheckCircle2,
  FileSearch,
  Loader2
} from "lucide-react";
import type {
  InvestigationSession,
  PlatformCode,
  AgentExecutionPhase,
  ReportSupportTarget
} from "../../types/investigation";
import type { InvestigationTurnStage } from "../../types/investigations";
import { AgentCollaborationCard } from "./AgentCollaborationCard";
import { TaskConfigCard } from "./TaskConfigCard";
import { TaskSuggestionCard } from "./TaskSuggestionCard";
import { buildSuggestionAssistantCopy } from "./suggestionPresentation";
import { TaskConfirmationCard } from "./TaskConfirmationCard";
import { InvestigationReportCard } from "./InvestigationReportCard";
import { StreamingAssistantText } from "./StreamingAssistantText";
import { AssistantMarkdown } from "./AssistantMarkdown";
import { HistoricalReportPendingStatus } from "./HistoricalReportPendingStatus";
import { shouldShowHistoricalPending } from "./historicalReportPresentation";
import { platformOptionsList } from "../../mocks/investigationMocks";

interface InvestigationCenterAreaProps {
  session: InvestigationSession;
  isSendingMessage: boolean;
  sendingMessageStage?: InvestigationTurnStage;
  isSidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  onUpdateDraftKeywords: (keywords: string[]) => void;
  onUpdateCreationSearchTerms: (keywords: string[]) => Promise<void>;
  onUpdateDraftPlatforms: (platforms: PlatformCode[]) => void;
  onGenerateTaskConfig: (proposalMessageId: string) => void;
  onStartAgentExecution: () => void;
  isConfirmingCreation?: boolean;
  onPhaseChange: (phase: AgentExecutionPhase) => void;
  onSendMessage: (text: string) => void;
  onOpenDrawer: (type: "task_config" | "report" | "evidence" | "key_users" | "agent_logs" | "ruleset") => void;
  onOpenReportSupport: (target: ReportSupportTarget) => void;
  onExamplePromptSelect: (prompt: string) => void;
}

const SUGGESTED_PROMPT_PREFIX = "试着问我：";

function getInitialStreamingMessageId(session: InvestigationSession) {
  const latestMessage = session.messages[session.messages.length - 1];
  if (
    session.messages.length === 2
    && latestMessage?.type === "task_proposal"
    && latestMessage.proposalData?.interactionMode === "platform-selection"
    && !latestMessage.proposalData.platformsConfirmed
  ) {
    return latestMessage.id;
  }
  return null;
}

function getSuggestedPrompt(placeholder: string) {
  if (!placeholder.startsWith(SUGGESTED_PROMPT_PREFIX)) return null;
  return placeholder.slice(SUGGESTED_PROMPT_PREFIX.length).trim() || null;
}

function getEthnicRelationsFollowUpPlaceholder(session: InvestigationSession) {
  const reportMessageIndex = session.messages.reduce(
    (latestIndex, message, index) => (message.type === "report_card" ? index : latestIndex),
    -1
  );
  const followUpReplies = session.messages
    .slice(reportMessageIndex + 1)
    .filter((message) => message.sender === "assistant" && message.content);
  const latestReply = followUpReplies[followUpReplies.length - 1]?.content || "";

  if (!latestReply) {
    return "试着问我：先用三句话告诉我这次调查最重要的结论";
  }
  if (latestReply.includes("本次共研判 667 条内容")) {
    return "试着问我：有没有账号同时评论过多个重点对象？";
  }
  if (latestReply.includes("131 个账号曾跨至少两个对象评论")) {
    return "试着问我：穿透“🌺🌹红花🌹🌺”这个账号";
  }
  if (latestReply.includes("已穿透账号“🌺🌹红花🌹🌺”")) {
    return "试着问我：为什么没有把它判为风险账号？";
  }
  if (latestReply.includes("没有将该账号判为风险账号")) {
    return "试着问我：哪个重点对象的风险最突出？";
  }
  if (latestReply.includes("按风险内容数量看")) {
    return "试着问我：列出需要人工复核的风险内容";
  }
  if (latestReply.includes("建议优先复核 11 条高中风险内容")) {
    return "试着问我：这些结论的研判依据是什么？";
  }
  if (latestReply.includes("相关结论同时参考原帖内容")) {
    return "试着问我：正常内容主要有哪些？";
  }
  if (latestReply.includes("641 条无风险内容主要涉及")) {
    return "试着问我：麦热依姆古丽周边有哪些疑似关联账号？";
  }
  if (latestReply.includes("维汉胡胡~招红娘")) {
    return "试着问我：这些账号能认定为协同传播吗？";
  }

  return "继续追问报告中的对象、内容、证据或账号……";
}

export function InvestigationCenterArea({
  session,
  isSendingMessage,
  sendingMessageStage,
  isSidebarCollapsed,
  onToggleSidebar,
  onUpdateDraftKeywords,
  onUpdateCreationSearchTerms,
  onUpdateDraftPlatforms,
  onGenerateTaskConfig,
  onStartAgentExecution,
  isConfirmingCreation = false,
  onPhaseChange,
  onSendMessage,
  onOpenDrawer,
  onOpenReportSupport,
  onExamplePromptSelect
}: InvestigationCenterAreaProps) {
  const [inputText, setInputText] = useState("");
  const isPublishedReportSession = Boolean(session.reportBinding);
  const showHistoricalPending = shouldShowHistoricalPending(
    isPublishedReportSession,
    isSendingMessage,
    Boolean(session.reportBinding?.pendingTurn)
  );
  const showCreationPending = Boolean(session.creationBinding && isSendingMessage && !session.reportBinding);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(() => (
    getInitialStreamingMessageId(session)
  ));
  const timelineRef = useRef<HTMLDivElement>(null);
  const observedSessionIdRef = useRef(session.id);
  const knownMessageIdsRef = useRef(new Set(session.messages.map((message) => message.id)));

  useLayoutEffect(() => {
    if (observedSessionIdRef.current !== session.id) {
      observedSessionIdRef.current = session.id;
      knownMessageIdsRef.current = new Set(session.messages.map((message) => message.id));
      setStreamingMessageId(getInitialStreamingMessageId(session));
      return;
    }

    const newMessages = session.messages.filter((message) => !knownMessageIdsRef.current.has(message.id));
    session.messages.forEach((message) => knownMessageIdsRef.current.add(message.id));
    const latestTextReply = [...newMessages].reverse().find((message) => (
      message.sender === "assistant"
      && Boolean(message.content)
      && !(isPublishedReportSession && message.type === "grounded_answer")
      && (
        message.type === undefined
        || message.type === "text"
        || message.type === "task_proposal"
        || message.type === "grounded_answer"
        || message.type === "evidence_list"
      )
    ));

    if (latestTextReply && !session.creationBinding) {
      setStreamingMessageId(latestTextReply.id);
    }
  }, [isPublishedReportSession, session.id, session.messages]);

  const handleStreamingComplete = (messageId: string) => {
    setStreamingMessageId((currentMessageId) => (
      currentMessageId === messageId ? null : currentMessageId
    ));
  };

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      const timeline = timelineRef.current;
      if (!timeline) return;
      if (session.reportBinding && session.messages.length === 1) {
        timeline.scrollTo({ top: 0, behavior: "auto" });
        return;
      }
      timeline.scrollTo({
        top: timeline.scrollHeight,
        behavior: showHistoricalPending ? "auto" : "smooth"
      });
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [session.id, session.messages.length, session.reportBinding, showHistoricalPending]);

  const handleSend = () => {
    if (!inputText.trim() || isSendingMessage) return;
    onSendMessage(inputText.trim());
    setInputText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const suggestedPrompt = getSuggestedPrompt(getPlaceholder());
    if (e.key === "Tab" && !e.shiftKey && inputText === "" && suggestedPrompt) {
      e.preventDefault();
      setInputText(suggestedPrompt);
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const getPlaceholder = () => {
    if (isSendingMessage) {
      if (sendingMessageStage === "acquiring_source") return "正在查询报告资料……";
      if (sendingMessageStage === "answering") return "正在整理回答……";
      if (sendingMessageStage === "preparing_sources") return "正在准备所需资料……";
      return "正在理解你的问题……";
    }
    if (isPublishedReportSession) {
      return "询问报告内容、典型案例或证据……";
    }
    if (session.messages.length === 0) {
      return "描述你希望调查的话题、账号或内容……";
    }
    if (session.executionPhase !== "idle" && session.executionPhase !== "completed") {
      return "可以继续补充要求，或询问当前 Agent 执行进度……";
    }
    if (session.status === "报告已生成") {
      if (
        session.id === "session-ethnic-relations"
        || session.title.includes("维汉民族关系")
        || session.draft.subject.includes("维汉民族关系")
      ) {
        return getEthnicRelationsFollowUpPlaceholder(session);
      }
      return "询问报告细节、穿透证据或发起下一轮穿透调查……";
    }
    return "回复对话、补充描述或选择平台……";
  };

  return (
    <main className="inv-center-area">
      {/* Header */}
      <header className="inv-center-header">
        <div className="inv-header-left">
          {isSidebarCollapsed ? (
            <button
              type="button"
              className="inv-expand-toggle"
              onClick={onToggleSidebar}
              title="展开调查会话侧栏"
            >
              <PanelLeft size={16} />
            </button>
          ) : null}
          <span className="inv-session-title-head">{session.title}</span>
          <span className={`inv-status-pill st-${session.status}`}>{session.status}</span>
        </div>

        <div className="inv-header-actions">
          <button
            type="button"
            className="inv-head-btn"
            onClick={() => onOpenDrawer("task_config")}
          >
            <span>会话配置</span>
          </button>
          <button
            type="button"
            className="inv-head-btn"
            onClick={() => onOpenDrawer("ruleset")}
          >
            <span>引用规则集</span>
          </button>
        </div>
      </header>

      {/* Timeline Scroll Container */}
      <div ref={timelineRef} className="inv-timeline-container">
        <div className="inv-timeline-inner">
          {/* WELCOME BLANK STATE */}
          {session.messages.length === 0 ? (
            <div className="inv-welcome-box">
              <div className="inv-welcome-icon">
                <Sparkles size={28} />
              </div>
              <h2 className="inv-welcome-title">发起专项调查研判</h2>
              <p className="inv-welcome-desc">
                输入调查主题或事件名称，系统将自动关联风险规则集并调度既有调查流水线开展协同研判。
              </p>

              <div className="inv-example-prompts">
                <button
                  type="button"
                  className="inv-example-chip"
                  onClick={() => onExamplePromptSelect("调查抖音平台博彩赌博类内容风险，并形成专项调查报告。")}
                >
                  <span>1. 调查抖音平台博彩赌博类内容风险</span>
                  <ChevronRight size={14} />
                </button>

                <button
                  type="button"
                  className="inv-example-chip"
                  onClick={() => onExamplePromptSelect("对最近关于维汉民族关系这一话题相关的帖子做一下抓取和分析。")}
                >
                  <span>2. 调查最近关于维汉民族关系的相关内容</span>
                  <ChevronRight size={14} />
                </button>

                <button
                  type="button"
                  className="inv-example-chip"
                  onClick={() => onExamplePromptSelect("对重点用户‘球赛情报局长’进行主页和关联用户穿透调查。")}
                >
                  <span>3. 对某个重点用户进行主页和关联用户穿透</span>
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          ) : (
            session.messages.map((msg, messageIndex) => {
              const streamingMessageIndex = streamingMessageId
                ? session.messages.findIndex((message) => message.id === streamingMessageId)
                : -1;

              if (msg.sender === "user") {
                return (
                  <div key={msg.id} className="inv-msg-user">
                    <div className="inv-msg-user-bubble">{msg.content}</div>
                  </div>
                );
              }

              if (streamingMessageIndex >= 0 && messageIndex > streamingMessageIndex) {
                return null;
              }

              // Assistant message cases
              const withProposalPresentation = (card: ReactNode) => msg.authoritativeProposalPresentation ? (
                <Fragment key={msg.id}>
                  <div className="inv-msg-asst-card">
                    <div className="inv-asst-head"><Bot size={16} /><span>研判助手</span></div>
                    <div className="inv-assistant-text" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                      {msg.content}
                    </div>
                  </div>
                  {card}
                </Fragment>
              ) : card;
              if (msg.type === "task_proposal") {
                if (msg.proposalData?.interactionMode === "platform-selection") {
                  return (
                    <TaskSuggestionCard
                      key={msg.id}
                      literalAssistantContent={msg.authoritativeProposalPresentation}
                      assistantContent={msg.authoritativeProposalPresentation
                        ? msg.content || ""
                        : session.creationBinding
                        ? buildSuggestionAssistantCopy(
                            session.draft,
                            session.creationBinding.confirmationPreview
                          )
                        : msg.content || ""}
                      shouldStream={!session.creationBinding && streamingMessageId === msg.id}
                      showSuggestionCard={Boolean(session.creationBinding) || streamingMessageId !== msg.id}
                      onStreamingComplete={() => handleStreamingComplete(msg.id)}
                      draft={session.draft}
                      preview={session.creationBinding?.confirmationPreview}
                      platformOptions={session.creationBinding?.suggestion?.platform_options.map((platform) => ({
                        code: platform.id,
                        label: platform.name,
                        available: platform.available
                      }))}
                      isReadOnly={msg.proposalData.platformsConfirmed}
                      onUpdatePlatforms={onUpdateDraftPlatforms}
                      onUpdateSearchTerms={session.creationBinding
                        ? onUpdateCreationSearchTerms
                        : async (terms) => onUpdateDraftKeywords(terms)}
                      onGenerateConfig={() => onGenerateTaskConfig(msg.id)}
                      onOpenAnalysisPlan={() => onOpenDrawer("ruleset")}
                      error={session.creationBinding?.error}
                    />
                  );
                }

                return withProposalPresentation(
                  <TaskConfigCard
                    key={msg.id}
                    draft={session.draft}
                    onUpdateKeywords={onUpdateDraftKeywords}
                    onUpdatePlatforms={onUpdateDraftPlatforms}
                    onStartExecution={onStartAgentExecution}
                    onOpenRulesetDrawer={() => onOpenDrawer("ruleset")}
                    onOpenConfigDrawer={() => onOpenDrawer("task_config")}
                  />
                );
              }

              if (msg.type === "task_confirmation") {
                if (session.draft.confirmed) {
                  return withProposalPresentation(
                    <TaskConfigCard
                      key={msg.id}
                      draft={session.draft}
                      onUpdateKeywords={onUpdateDraftKeywords}
                      onUpdatePlatforms={onUpdateDraftPlatforms}
                      onStartExecution={onStartAgentExecution}
                      onOpenRulesetDrawer={() => onOpenDrawer("ruleset")}
                      onOpenConfigDrawer={() => onOpenDrawer("task_config")}
                    />
                  );
                }

                return withProposalPresentation(
                  <TaskConfirmationCard
                    key={msg.id}
                    draft={session.draft}
                    onModifyConfig={() => onOpenDrawer("task_config")}
                    onStartExecution={onStartAgentExecution}
                    preview={session.creationBinding?.confirmationPreview}
                    onUpdateSearchTerms={session.creationBinding ? onUpdateCreationSearchTerms : undefined}
                    isConfirming={isConfirmingCreation}
                    error={session.creationBinding?.error}
                  />
                );
              }

              if (msg.type === "historical_progress") {
                return (
                  <div key={msg.id} className="inv-msg-asst-card inv-historical-progress">
                    <div className="inv-asst-head">
                      <Bot size={16} />
                      <span>研判进度</span>
                      <span className="inv-grounded-verified">
                        <CheckCircle2 size={13} />
                        已完成
                      </span>
                    </div>
                    <p>{msg.content}</p>
                  </div>
                );
              }

              if (msg.type === "agent_collaboration") {
                return (
                  <AgentCollaborationCard
                    key={msg.id}
                    phase={session.executionPhase}
                    onPhaseChange={onPhaseChange}
                    investigationId={session.id}
                    investigationTitle={session.title}
                    taskName={session.draft.taskName}
                    platformsText={session.draft.platforms.map(
                      (code) => platformOptionsList.find((platform) => platform.code === code)?.label || code
                    ).join("、")}
                    keywordsCount={session.draft.keywords.length}
                    ruleSetName={session.draft.matchedRuleSet}
                    run={session.creationBinding?.run}
                    authoritative={Boolean(session.creationBinding)}
                  />
                );
              }

              if (msg.type === "report_card" && msg.reportData) {
                return (
                  <InvestigationReportCard
                    key={msg.id}
                    report={msg.reportData}
                    onOpenReportDrawer={() => onOpenDrawer("report")}
                    onOpenEvidenceDrawer={isPublishedReportSession ? undefined : () => onOpenDrawer("evidence")}
                    onOpenKeyUserDrawer={isPublishedReportSession ? undefined : () => onOpenDrawer("key_users")}
                    onFollowUpInvestigation={isPublishedReportSession ? undefined : () => onSendMessage(
                      msg.reportData?.id.startsWith("report-ethnic-relations")
                        ? "查看四个重点对象之间的共同评论账号与疑似关联线索。"
                        : "对这 8 个重点作者候选发起主页与关联导流网络深钻穿透。"
                    )}
                  />
                );
              }

              if (msg.type === "grounded_answer" && msg.groundingItems?.length) {
                return (
                  <div key={msg.id} className="inv-msg-asst-card inv-grounded-answer">
                    <div className="inv-asst-head">
                      <Bot size={16} />
                      <span>研判助手</span>
                      <span className="inv-grounded-verified">
                        <CheckCircle2 size={13} />
                        已关联证据
                      </span>
                    </div>

                    {isPublishedReportSession ? (
                      <AssistantMarkdown
                        content={msg.content || ""}
                        className="inv-grounded-content"
                      />
                    ) : (
                      <StreamingAssistantText
                        text={msg.content || ""}
                        shouldStream={!session.creationBinding && streamingMessageId === msg.id}
                        className="inv-grounded-content"
                        onComplete={() => handleStreamingComplete(msg.id)}
                      />
                    )}

                    {streamingMessageId === msg.id ? null : msg.groundingMode === "compact" ? (
                      <div className="inv-grounded-compact" aria-label="回答依据来源">
                        <div>
                          <FileSearch size={15} />
                          <span>依据：</span>
                          <strong>{msg.groundingItems.map((item) => item.label).join(" · ")}</strong>
                        </div>
                        <button
                          type="button"
                          onClick={() => onOpenReportSupport({
                            evidenceId: msg.groundingItems?.[0]?.reportEvidenceId,
                            sectionId: msg.groundingItems?.[0]?.reportSectionId
                          })}
                        >
                          查看支撑数据
                        </button>
                      </div>
                    ) : (
                      <section className="inv-grounded-sources" aria-label="回答支撑依据">
                        <header>
                          <div>
                            <FileSearch size={15} />
                            <strong>支撑依据</strong>
                          </div>
                          <span>{msg.groundingItems.length} 项</span>
                        </header>

                        {msg.groundingItems.map((item) => (
                          <article
                            key={item.id}
                            className={`inv-grounded-source is-${item.tone}${item.hideOverviewInChat ? " is-comments-only" : ""}`}
                          >
                            {!item.hideOverviewInChat ? (
                              <div className="inv-grounded-source-head">
                                <div>
                                  <span>{item.label}</span>
                                  <strong>{item.title}</strong>
                                </div>
                                <small>{item.meta}</small>
                              </div>
                            ) : null}

                            {!item.hideOverviewInChat && item.facts?.length ? (
                              <dl className="inv-grounded-facts">
                                {item.facts.map((fact) => (
                                  <div key={fact.label}>
                                    <dt>{fact.label}</dt>
                                    <dd>{fact.value}</dd>
                                  </div>
                                ))}
                              </dl>
                            ) : null}

                            {item.commentSamples?.length ? (
                              <div className="inv-grounded-comments">
                                <div className="inv-grounded-comments-head">
                                  <strong>代表性评论</strong>
                                  <span>展示 {item.commentSamples.length} / 56 条</span>
                                </div>
                                {item.commentSamples.map((comment, index) => (
                                  <div key={`${comment.subject}-${comment.time}-${index}`} className="inv-grounded-comment-row">
                                    <div>
                                      <strong>{comment.subject}</strong>
                                      <span>{comment.time}</span>
                                      <em>{comment.riskLabel}</em>
                                    </div>
                                    <p lang="ug">{comment.content}</p>
                                    {comment.translation ? <p><b>译文：</b>{comment.translation}</p> : null}
                                  </div>
                                ))}
                              </div>
                            ) : null}

                            {item.quote ? (
                              <div className="inv-grounded-quote">
                                <span>原文</span>
                                <p lang="ug">{item.quote}</p>
                                {item.translation ? <p><b>译文：</b>{item.translation}</p> : null}
                              </div>
                            ) : null}

                            <p className="inv-grounded-summary">{item.summary}</p>

                            {item.reportEvidenceId || item.reportSectionId ? (
                              <button
                                type="button"
                                className="inv-grounded-open"
                                onClick={() => onOpenReportSupport({
                                  evidenceId: item.reportEvidenceId,
                                  sectionId: item.reportSectionId
                                })}
                              >
                                <FileSearch size={14} />
                                <span>{item.reportEvidenceId ? "查看原文与研判依据" : "查看报告统计明细"}</span>
                              </button>
                            ) : null}
                          </article>
                        ))}
                      </section>
                    )}
                  </div>
                );
              }

              if (msg.type === "evidence_list" && msg.evidenceItems) {
                return (
                  <div key={msg.id} className="inv-msg-asst-card">
                    <div className="inv-asst-head">
                      <Bot size={16} />
                      <span>研判助手</span>
                    </div>

                    <StreamingAssistantText
                      text={msg.content || ""}
                      shouldStream={!session.creationBinding && streamingMessageId === msg.id}
                      className="inv-assistant-text"
                      onComplete={() => handleStreamingComplete(msg.id)}
                    />

                    {streamingMessageId === msg.id ? null : <div className="inv-evidence-grid">
                      {msg.evidenceItems.map((ev) => (
                        <div key={ev.id} className="inv-ev-card">
                          <div className="inv-ev-head">
                            <span className="inv-ev-title">{ev.title}</span>
                            <span style={{ fontSize: "11px", fontWeight: "700", padding: "1px 6px", borderRadius: "4px", background: "#fef2f2", color: "#dc2626" }}>
                              {ev.riskType}
                            </span>
                          </div>

                          <div style={{ fontSize: "12px", color: "#64748b" }}>
                            作者：{ev.author} ({ev.platform}) | 时间：{ev.publishTime}
                          </div>

                          <div className="inv-ev-snippet">{ev.snippet}</div>

                          {ev.ocrText ? (
                            <div style={{ fontSize: "11.5px", color: "#0f172a", background: "#f1f5f9", padding: "6px 8px", borderRadius: "4px" }}>
                              <strong>OCR 画面识别：</strong>{ev.ocrText}
                            </div>
                          ) : null}

                          {ev.asrText ? (
                            <div style={{ fontSize: "11.5px", color: "#0f172a", background: "#f1f5f9", padding: "6px 8px", borderRadius: "4px" }}>
                              <strong>ASR 语音转写：</strong>{ev.asrText}
                            </div>
                          ) : null}

                          <div className="inv-ev-meta">
                            <span>证据来源：{ev.source}</span>
                            <button
                              type="button"
                              onClick={() => onOpenDrawer("evidence")}
                              style={{ background: "transparent", border: "none", color: "#2563eb", fontSize: "11.5px", fontWeight: "700", cursor: "pointer" }}
                            >
                              查看原始证据 ➔
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>}

                    {streamingMessageId !== msg.id && msg.inquiryOptions ? (
                      <div className="inv-inquiry-chips">
                        {msg.inquiryOptions.map((opt, idx) => (
                          <button
                            key={idx}
                            type="button"
                            className="inv-inquiry-chip"
                            onClick={() => onSendMessage(opt.prompt)}
                          >
                            <Sparkles size={12} />
                            <span>{opt.label}</span>
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              }

              // Default text assistant message
              const isHistoricalAnswer = isPublishedReportSession && msg.type === "grounded_answer";
              return (
                <div
                  key={msg.id}
                  className={`inv-msg-asst-card${isHistoricalAnswer ? " inv-report-answer" : ""}`}
                >
                  <div className="inv-asst-head">
                    <Bot size={16} />
                    <span>研判助手</span>
                  </div>
                  {msg.authoritativeProposalPresentation ? (
                    <div className="inv-assistant-text" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                      {msg.content}
                    </div>
                  ) : isHistoricalAnswer ? (
                    <AssistantMarkdown content={msg.content || ""} />
                  ) : (
                    <StreamingAssistantText
                      text={msg.content || ""}
                      shouldStream={!session.creationBinding && streamingMessageId === msg.id}
                      className="inv-assistant-text"
                      onComplete={() => handleStreamingComplete(msg.id)}
                    />
                  )}
                </div>
              );
            })
          )}
          {showHistoricalPending ? (
            <div className="inv-msg-asst-card inv-report-answer-pending-wrap">
              <div className="inv-asst-head">
                <Bot size={16} />
                <span>研判助手</span>
              </div>
              <HistoricalReportPendingStatus
                stage={sendingMessageStage}
                recovering={session.reportBinding?.pendingTurn?.recovering}
              />
            </div>
          ) : null}
          {showCreationPending ? (
            <div className="inv-msg-asst-card inv-creation-pending" role="status" aria-live="polite">
              <div className="inv-asst-head">
                <Bot size={16} />
                <span>研判助手</span>
              </div>
              <div className="inv-creation-pending-status">
                <Loader2 size={17} className="spin" />
                <span>{creationPendingLabel(sendingMessageStage)}</span>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {/* Input Bar */}
      <div className="inv-input-bar-wrap">
        <div className="inv-input-box-container">
          <input
            type="text"
            className="inv-main-input"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={getPlaceholder()}
            disabled={isSendingMessage}
            aria-keyshortcuts={getSuggestedPrompt(getPlaceholder()) ? "Tab" : undefined}
          />
          <button
            type="button"
            className="inv-send-btn"
            onClick={handleSend}
            disabled={!inputText.trim() || isSendingMessage}
            title="发送消息"
          >
            <Send size={15} />
          </button>
        </div>
      </div>
    </main>
  );
}

function creationPendingLabel(stage?: InvestigationTurnStage) {
  if (stage === "planning") return "正在理解调查目标";
  if (stage === "preparing_sources") return "正在查询可用研判资源";
  if (stage === "acquiring_source") return "正在等待 Tool 和后端返回";
  if (stage === "answering") return "正在整理调查方案";
  return "正在思考";
}
