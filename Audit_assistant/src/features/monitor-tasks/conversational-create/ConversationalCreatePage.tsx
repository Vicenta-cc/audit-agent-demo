import { useState, useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Sparkles } from "lucide-react";
import { Toast } from "../../../components/feedback/Toast";
import type {
  TaskSession,
  PlatformCode
} from "../../../types/conversationalTask";
import {
  initialSessions,
  platformOptionsList
} from "../../../mocks/conversationalTaskMocks";
import { SessionListSidebar } from "./SessionListSidebar";
import { ConversationArea } from "./ConversationArea";
import { TaskDraftPanel } from "./TaskDraftPanel";

export function ConversationalCreatePage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<TaskSession[]>(() => initialSessions);
  const [activeSessionId, setActiveSessionId] = useState<string>("session-wc-gambling");
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "info" } | null>(null);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "对话式任务创建 · 内容巡查研判平台";
    return () => {
      document.title = previousTitle;
    };
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2400);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const showToast = useCallback((message: string, tone: "success" | "info" = "success") => {
    setToast({ message, tone });
  }, []);

  // Find active session
  const activeSession = sessions.find((s) => s.id === activeSessionId) || sessions[0];

  // Helper to update active session state
  const updateActiveSession = useCallback(
    (updater: (prev: TaskSession) => TaskSession) => {
      setSessions((prevSessions) =>
        prevSessions.map((session) =>
          session.id === activeSessionId ? updater(session) : session
        )
      );
    },
    [activeSessionId]
  );

  // Switch session
  const handleSelectSession = (id: string) => {
    setActiveSessionId(id);
  };

  // Create new custom session
  const handleNewSession = () => {
    const newId = `session-custom-${Date.now()}`;
    const nowTime = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit"
    });

    const newSession: TaskSession = {
      id: newId,
      title: "新需求任务配置",
      status: "配置中",
      updatedAt: nowTime,
      draft: {
        taskName: "自定义巡查任务",
        taskType: "平台话题采集",
        subject: "待描述",
        platforms: ["dy"],
        keywords: [],
        policyName: "综合风险研判方案",
        policyDescription: "根据输入主题识别违法违规内容与风险要素。",
        status: "配置中",
        confirmed: false
      },
      messages: [
        {
          id: `msg-${Date.now()}`,
          sender: "assistant",
          timestamp: nowTime,
          content:
            "欢迎使用对话式任务创建助手！请用自然语言描述您想采集的内容与分析需求（例如：“抓取抖音上关于外汇套利的帖子”）..."
        }
      ]
    };

    setSessions((prev) => [newSession, ...prev]);
    setActiveSessionId(newId);
    showToast("已新建任务会话", "info");
  };

  // Update draft platforms
  const handleUpdateDraftPlatforms = (platforms: PlatformCode[]) => {
    updateActiveSession((session) => ({
      ...session,
      draft: {
        ...session.draft,
        platforms
      }
    }));
  };

  // Remove keyword
  const handleRemoveKeyword = (keyword: string) => {
    updateActiveSession((session) => ({
      ...session,
      draft: {
        ...session.draft,
        keywords: session.draft.keywords.filter((k) => k !== keyword)
      }
    }));
  };

  // Add keyword
  const handleAddKeyword = (keyword: string) => {
    updateActiveSession((session) => {
      if (session.draft.keywords.includes(keyword)) return session;
      return {
        ...session,
        draft: {
          ...session.draft,
          keywords: [...session.draft.keywords, keyword]
        }
      };
    });
  };

  // Confirm platforms
  const handleConfirmPlatforms = () => {
    if (!activeSession) return;

    const platformLabels = activeSession.draft.platforms
      .map((code) => platformOptionsList.find((p) => p.code === code)?.label || code)
      .join("和");

    const nowTime = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit"
    });

    const userMsgText = `采集${platformLabels}。`;
    const assistantReplyText = `已完成本次任务配置。\n\n系统将使用本次临时搜索词在${platformLabels}进行内容召回，并使用‘${activeSession.draft.policyName}’分析采集结果。\n\n请确认任务配置后创建任务。`;

    updateActiveSession((session) => ({
      ...session,
      status: "等待确认",
      draft: {
        ...session.draft,
        confirmed: true,
        status: "等待确认"
      },
      messages: [
        ...session.messages,
        {
          id: `msg-user-confirm-${Date.now()}`,
          sender: "user",
          timestamp: nowTime,
          content: userMsgText
        },
        {
          id: `msg-asst-confirm-${Date.now()}`,
          sender: "assistant",
          timestamp: nowTime,
          content: assistantReplyText
        }
      ]
    }));

    showToast("平台选择已确认，任务草稿生成完毕");
  };

  // Send message
  const handleSendMessage = (text: string) => {
    const nowTime = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit"
    });

    const fixedResponse =
      "已收到您的任务需求。当前原型暂未接入智能解析服务，请选择左侧预设会话体验完整任务创建流程。";

    updateActiveSession((session) => ({
      ...session,
      messages: [
        ...session.messages,
        {
          id: `msg-u-${Date.now()}`,
          sender: "user",
          timestamp: nowTime,
          content: text
        },
        {
          id: `msg-a-${Date.now()}`,
          sender: "assistant",
          timestamp: nowTime,
          content: fixedResponse
        }
      ]
    }));
  };

  // Return to edit
  const handleReturnToEdit = () => {
    updateActiveSession((session) => ({
      ...session,
      status: "配置中",
      draft: {
        ...session.draft,
        confirmed: false,
        status: "配置中"
      }
    }));
    showToast("重新进入编辑模式", "info");
  };

  // Save draft
  const handleSaveDraft = () => {
    showToast("草稿已保存");
  };

  // Create and start
  const handleCreateAndStart = () => {
    updateActiveSession((session) => ({
      ...session,
      status: "已创建",
      draft: {
        ...session.draft,
        status: "已创建"
      }
    }));
    showToast("任务创建成功，已启动后台巡查");
  };

  return (
    <main className="conv-page-wrapper">
      {/* Page Header */}
      <header className="conv-header">
        <div className="conv-header-left">
          <button
            type="button"
            className="conv-back-btn"
            onClick={() => navigate("/tasks")}
          >
            <ArrowLeft size={18} />
            <span>返回监控任务</span>
          </button>
          <div className="conv-header-titles">
            <div className="conv-title-badge-row">
              <h1>对话式任务创建</h1>
              <span className="conv-ai-badge">
                <Sparkles size={13} />
                <span>智能引导</span>
              </span>
            </div>
            <p>
              通过自然语言描述需求，系统推荐研判方案、生成临时搜索词，完成任务草稿并一键提交。
            </p>
          </div>
        </div>
      </header>

      {/* Main 3-Column Layout */}
      <section className="conv-main-layout">
        {/* Column 1: Left Session List Sidebar */}
        <SessionListSidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelectSession={handleSelectSession}
          onNewSession={handleNewSession}
        />

        {/* Column 2: Middle Conversation Area */}
        <ConversationArea
          session={activeSession}
          onUpdateDraftPlatforms={handleUpdateDraftPlatforms}
          onRemoveKeyword={handleRemoveKeyword}
          onAddKeyword={handleAddKeyword}
          onConfirmPlatforms={handleConfirmPlatforms}
          onSendMessage={handleSendMessage}
        />

        {/* Column 3: Right Task Draft Panel */}
        <TaskDraftPanel
          draft={activeSession.draft}
          onReturnToEdit={handleReturnToEdit}
          onSaveDraft={handleSaveDraft}
          onCreateAndStart={handleCreateAndStart}
        />
      </section>

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </main>
  );
}
