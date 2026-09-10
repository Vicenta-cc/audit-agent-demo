export type TaskSessionStatus = "配置中" | "等待确认" | "已创建";

export type PlatformCode = "dy" | "xhs" | "wb" | "multi";

export interface PlatformOption {
  code: PlatformCode;
  label: string;
}

export interface ChatMessage {
  id: string;
  sender: "user" | "assistant";
  timestamp: string;
  content?: string;
  type?: "text" | "task_proposal";
  // Structured payload for assistant task creation proposal
  proposalData?: {
    taskName: string;
    taskType: string;
    subject: string;
    policyName: string;
    policyDescription: string;
    policyExpandedReason?: string;
    noticeText?: string;
    keywordsNotice?: string;
    platformsSelected: PlatformCode[];
    platformsConfirmed: boolean;
  };
}

export interface TaskDraft {
  taskName: string;
  taskType: string;
  subject: string;
  platforms: PlatformCode[];
  keywords: string[];
  policyName: string;
  policyDescription: string;
  status: TaskSessionStatus;
  confirmed: boolean;
}

export interface TaskSession {
  id: string;
  title: string;
  status: TaskSessionStatus;
  updatedAt: string;
  messages: ChatMessage[];
  draft: TaskDraft;
}
