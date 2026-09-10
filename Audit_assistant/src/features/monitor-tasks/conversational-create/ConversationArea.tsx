import { useState, useRef, useEffect } from "react";
import {
  Send,
  Bot,
  User,
  ShieldCheck,
  Tag,
  Plus,
  X,
  ChevronDown,
  ChevronUp,
  Check,
  CheckCircle2,
  Info
} from "lucide-react";
import type {
  TaskSession,
  PlatformCode,
  PlatformOption
} from "../../../types/conversationalTask";
import { platformOptionsList } from "../../../mocks/conversationalTaskMocks";

interface ConversationAreaProps {
  session: TaskSession;
  onUpdateDraftPlatforms: (platforms: PlatformCode[]) => void;
  onRemoveKeyword: (keyword: string) => void;
  onAddKeyword: (keyword: string) => void;
  onConfirmPlatforms: () => void;
  onSendMessage: (text: string) => void;
}

export function ConversationArea({
  session,
  onUpdateDraftPlatforms,
  onRemoveKeyword,
  onAddKeyword,
  onConfirmPlatforms,
  onSendMessage
}: ConversationAreaProps) {
  const [inputText, setInputText] = useState("");
  const [newKeywordInput, setNewKeywordInput] = useState("");
  const [isAddingKeyword, setIsAddingKeyword] = useState(false);
  const [expandedPolicy, setExpandedPolicy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto scroll to bottom when messages change
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [session.messages, session.draft.keywords, session.draft.confirmed]);

  const handleSend = () => {
    if (!inputText.trim()) return;
    onSendMessage(inputText.trim());
    setInputText("");
  };

  const handleAddKeywordSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = newKeywordInput.trim();
    if (trimmed) {
      onAddKeyword(trimmed);
      setNewKeywordInput("");
      setIsAddingKeyword(false);
    }
  };

  const handleTogglePlatform = (code: PlatformCode) => {
    const current = session.draft.platforms;
    if (code === "multi") {
      // Toggle all platforms
      const allCodes: PlatformCode[] = ["dy", "xhs", "wb"];
      const hasAll = allCodes.every((p) => current.includes(p));
      if (hasAll) {
        onUpdateDraftPlatforms(["dy"]);
      } else {
        onUpdateDraftPlatforms(["dy", "xhs", "wb"]);
      }
      return;
    }

    let next: PlatformCode[];
    if (current.includes(code)) {
      if (current.length === 1) return; // keep at least 1
      next = current.filter((p) => p !== code);
    } else {
      next = [...current, code];
    }
    onUpdateDraftPlatforms(next);
  };

  return (
    <div className="conv-middle-panel">
      {/* Session Header */}
      <div className="conv-middle-header">
        <div className="conv-middle-header-main">
          <h1 className="conv-session-title">{session.title}</h1>
          <span className={`conv-header-status status-${session.draft.status}`}>
            {session.draft.status}
          </span>
        </div>
        <p className="conv-session-subtitle">
          对话式任务创建助手 · 自然语言需求与识别分类规则自动匹配
        </p>
      </div>

      {/* Messages Scroll Area */}
      <div className="conv-messages-container" ref={scrollRef}>
        {session.messages.map((msg) => {
          const isUser = msg.sender === "user";

          return (
            <div
              key={msg.id}
              className={`conv-message-wrapper ${isUser ? "is-user" : "is-assistant"}`}
            >
              <div className="conv-avatar" aria-hidden="true">
                {isUser ? <User size={16} /> : <Bot size={18} />}
              </div>

              <div className="conv-message-content">
                <div className="conv-message-header">
                  <span className="conv-sender-name">
                    {isUser ? "你" : "任务配置助手"}
                  </span>
                  <time className="conv-message-time">{msg.timestamp}</time>
                </div>

                {/* Text Content */}
                {msg.content ? (
                  <div className="conv-message-bubble">
                    {msg.content.split("\n\n").map((paragraph, idx) => (
                      <p key={idx}>{paragraph}</p>
                    ))}
                  </div>
                ) : null}

                {/* Structured Proposal Block (Embedded in Assistant Response) */}
                {msg.type === "task_proposal" && msg.proposalData ? (
                  <div className="conv-proposal-card">
                    {/* 1. Recognized Task Header */}
                    <div className="conv-proposal-section conv-proposal-task">
                      <div className="conv-proposal-tag">
                        <CheckCircle2 size={15} />
                        <span>已识别任务</span>
                      </div>
                      <div className="conv-proposal-task-name">
                        {msg.proposalData.taskName}
                      </div>
                      <div className="conv-proposal-task-meta">
                        <span>任务类型：{msg.proposalData.taskType}</span>
                        <span>·</span>
                        <span>采集主题：{msg.proposalData.subject}</span>
                      </div>
                    </div>

                    {/* 2. Research Policy Card */}
                    <div className="conv-proposal-section conv-policy-card">
                      <div className="conv-section-title">
                        <ShieldCheck size={16} className="text-primary" />
                        <span>推荐研判方案</span>
                      </div>
                      <div className="conv-policy-name">
                        {msg.proposalData.policyName}
                      </div>
                      <p className="conv-policy-desc">
                        {msg.proposalData.policyDescription}
                      </p>

                      {msg.proposalData.policyExpandedReason ? (
                        <div className="conv-policy-expand-block">
                          <button
                            type="button"
                            className="conv-expand-toggle"
                            onClick={() => setExpandedPolicy(!expandedPolicy)}
                          >
                            <span>
                              {expandedPolicy ? "收起研判逻辑" : "查看研判逻辑说明"}
                            </span>
                            {expandedPolicy ? (
                              <ChevronUp size={14} />
                            ) : (
                              <ChevronDown size={14} />
                            )}
                          </button>

                          {expandedPolicy ? (
                            <div className="conv-policy-reason-box">
                              <p>{msg.proposalData.policyExpandedReason}</p>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>

                    {/* 3. Temporary Search Terms Section */}
                    <div className="conv-proposal-section conv-keywords-section">
                      <div className="conv-section-title">
                        <Tag size={16} className="text-primary" />
                        <span>推荐临时搜索词 ({session.draft.keywords.length}个)</span>
                      </div>

                      <div className="conv-keywords-list">
                        {session.draft.keywords.map((kw) => (
                          <span key={kw} className="conv-keyword-tag">
                            <span>{kw}</span>
                            <button
                              type="button"
                              className="conv-keyword-del"
                              title={`删除关键词 "${kw}"`}
                              onClick={() => onRemoveKeyword(kw)}
                            >
                              <X size={12} />
                            </button>
                          </span>
                        ))}

                        {isAddingKeyword ? (
                          <form
                            onSubmit={handleAddKeywordSubmit}
                            className="conv-add-keyword-form"
                          >
                            <input
                              type="text"
                              autoFocus
                              value={newKeywordInput}
                              placeholder="输入新搜索词"
                              onChange={(e) => setNewKeywordInput(e.target.value)}
                              onBlur={() => {
                                if (!newKeywordInput.trim()) {
                                  setIsAddingKeyword(false);
                                }
                              }}
                            />
                            <button type="submit" className="conv-add-confirm-btn">
                              <Check size={12} />
                            </button>
                          </form>
                        ) : (
                          <button
                            type="button"
                            className="conv-add-keyword-btn"
                            onClick={() => setIsAddingKeyword(true)}
                          >
                            <Plus size={13} />
                            <span>添加搜索词</span>
                          </button>
                        )}
                      </div>

                      <div className="conv-notice-box">
                        <Info size={14} className="text-muted flex-shrink-0" />
                        <span>
                          {msg.proposalData.keywordsNotice ||
                            "这些临时搜索词仅用于本次任务的平台搜索和内容召回，不会自动保存到正式黑话库。"}
                        </span>
                      </div>
                    </div>

                    {/* 4. Platform Selection */}
                    <div className="conv-proposal-section conv-platform-section">
                      <div className="conv-section-title">
                        <span>选择采集平台</span>
                      </div>

                      {session.draft.confirmed ? (
                        <div className="conv-platform-confirmed-bar">
                          <CheckCircle2 size={16} className="text-success" />
                          <span>
                            已选择采集平台：
                            <strong>
                              {session.draft.platforms
                                .map(
                                  (code) =>
                                    platformOptionsList.find((p) => p.code === code)
                                      ?.label || code
                                )
                                .join("、")}
                            </strong>
                          </span>
                        </div>
                      ) : (
                        <div className="conv-platform-select-container">
                          <div className="conv-platform-options">
                            {platformOptionsList.map((platform) => {
                              const isSelected =
                                platform.code === "multi"
                                  ? session.draft.platforms.length >= 3
                                  : session.draft.platforms.includes(platform.code);

                              return (
                                <button
                                  key={platform.code}
                                  type="button"
                                  className={`conv-platform-chip${
                                    isSelected ? " is-selected" : ""
                                  }`}
                                  onClick={() => handleTogglePlatform(platform.code)}
                                >
                                  <span className="conv-chip-check">
                                    {isSelected ? <Check size={13} /> : null}
                                  </span>
                                  <span>{platform.label}</span>
                                </button>
                              );
                            })}
                          </div>

                          <div className="conv-platform-confirm-row">
                            <button
                              type="button"
                              className="conv-confirm-platform-btn"
                              onClick={onConfirmPlatforms}
                            >
                              确认平台并生成任务草稿
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      {/* Fixed Bottom Input Field */}
      <div className="conv-input-bar">
        <div className="conv-input-container">
          <input
            ref={inputRef}
            type="text"
            className="conv-text-input"
            value={inputText}
            placeholder="通过自然语言描述采集需求..."
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleSend();
              }
            }}
          />
          <button
            type="button"
            className="conv-send-btn"
            disabled={!inputText.trim()}
            onClick={handleSend}
            title="发送需求描述"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
