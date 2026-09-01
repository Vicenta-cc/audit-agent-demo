import type { PublishedReportPresentation } from "./reports";
import type {
  ConfirmationPreview,
  InvestigationDraftSuggestion,
  InvestigationRunProjection,
  PublicInvestigationDraft
} from "./investigationCreation";

export type TaskSessionStatus = "配置中" | "等待确认" | "研判中" | "审核完成" | "报告已生成" | "已创建";

export type PlatformCode = "dy" | "xhs" | "ks" | "wb" | "multi";

export interface PlatformOption {
  code: PlatformCode;
  label: string;
}

// Agent definition & status machine
export type AgentId = "collection" | "evidence" | "audit" | "report";

export type AgentStatus =
  | "idle"
  | "waking"
  | "working"
  | "waiting"
  | "requesting_evidence"
  | "completed"
  | "error";

export interface AgentInfo {
  id: AgentId;
  name: string;
  role: string;
  icon: string;
  description: string;
}

export type AgentExecutionPhase =
  | "idle"
  | "collection_waking"
  | "collection_working"
  | "collection_completed"
  | "evidence_handoff"
  | "evidence_working"
  | "evidence_completed"
  | "audit_handoff"
  | "audit_working"
  | "evidence_requested"
  | "evidence_supplementing"
  | "audit_resumed"
  | "audit_completed"
  | "report_handoff"
  | "report_generating"
  | "completed";

export interface AgentPacket {
  from: AgentId;
  to: AgentId;
  label: string;
  count: string;
  isReverse?: boolean;
}

export interface TaskDraft {
  taskName: string;
  taskType: string;
  subject: string;
  platforms: PlatformCode[];
  keywords: string[];
  matchedRuleSet: string;
  ruleSetDescription: string;
  analysisPlanName?: string;
  recommendedRecallLexicons?: string[];
  scopeDescription?: string;
  reportSourceTaskId?: string;
  status: TaskSessionStatus;
  confirmed: boolean;
}

export interface EvidenceItem {
  id: string;
  title: string;
  author: string;
  authorAvatar?: string;
  platform: string;
  riskType: string;
  riskLevel: "高风险" | "中风险" | "低风险" | "正常讨论";
  snippet: string;
  ocrText?: string;
  asrText?: string;
  videoTimestamp?: string;
  source: string;
  publishTime: string;
}

export interface ReportSummary {
  id: string;
  title: string;
  totalCollected: number;
  suspectedRisks: number;
  suggestedReview: number;
  keyAuthorCandidates: number;
  findings: string[];
  riskDistribution: { name: string; count: number; percentage: number }[];
  presentation?: PublishedReportPresentation;
  versionNumber?: number;
  publishedAt?: string;
}

export interface KeyUserProfile {
  id: string;
  name: string;
  handle: string;
  platform: string;
  riskCount: number;
  followers: string;
  bio: string;
  recentRisks: string[];
}

export interface MessageInquiryOption {
  label: string;
  prompt: string;
}

export type GroundingTone = "risk" | "warning" | "safe" | "neutral" | "clue";

export interface GroundingItem {
  id: string;
  label: string;
  tone: GroundingTone;
  title: string;
  meta: string;
  summary: string;
  hideOverviewInChat?: boolean;
  quote?: string;
  translation?: string;
  facts?: Array<{ label: string; value: string }>;
  commentSamples?: Array<{
    subject: string;
    time: string;
    content: string;
    translation?: string;
    riskLabel: string;
  }>;
  reportEvidenceId?: string;
  reportSectionId?: string;
}

export interface ReportSupportTarget {
  evidenceId?: string;
  sectionId?: string;
}

export interface ChatMessage {
  id: string;
  sender: "user" | "assistant" | "system";
  timestamp: string;
  content?: string;
  type?: "text" | "task_proposal" | "task_confirmation" | "agent_collaboration" | "historical_progress" | "report_card" | "evidence_list" | "comparison_list" | "grounded_answer";
  proposalData?: {
    taskName: string;
    taskType: string;
    subject: string;
    matchedRuleSet: string;
    ruleSetDescription: string;
    keywordsNotice?: string;
    platformsSelected: PlatformCode[];
    platformsConfirmed: boolean;
    interactionMode?: "platform-selection";
  };
  reportData?: ReportSummary;
  evidenceItems?: EvidenceItem[];
  comparisonData?: {
    normalCases: EvidenceItem[];
    riskCases: EvidenceItem[];
    reviewCases: EvidenceItem[];
  };
  inquiryOptions?: MessageInquiryOption[];
  groundingItems?: GroundingItem[];
  groundingMode?: "compact" | "expanded";
}

export interface InvestigationSession {
  id: string;
  title: string;
  status: TaskSessionStatus;
  updatedAt: string;
  draft: TaskDraft;
  messages: ChatMessage[];
  executionPhase: AgentExecutionPhase;
  executionProgress: number;
  keyUsers?: KeyUserProfile[];
  creationBinding?: {
    workspaceSessionId: string;
    draft?: PublicInvestigationDraft;
    confirmationPreview?: ConfirmationPreview;
    suggestion?: InvestigationDraftSuggestion;
    presentationStage?: "suggestion" | "confirmation";
    pendingTurnId?: string;
    pendingTurnStage?: import("./investigations").InvestigationTurnStage;
    resumeAttempted?: boolean;
    run?: InvestigationRunProjection;
    confirmationKey?: string;
    pendingReportTurn?: {
      turnId: string;
      clientMessageId: string;
      question: string;
      stage: import("./investigations").InvestigationTurnStage;
      resumeAttempted?: boolean;
    };
    error?: string;
  };
  reportBinding?: {
    workspaceId?: string;
    reportVersionId: string;
    reportId: string;
    taskId: string;
    versionNumber: number;
    publishedAt: string;
    pendingTurn?: {
      turnId: string;
      clientMessageId: string;
      stage: import("./investigations").InvestigationTurnStage;
      afterSequence?: number;
      resumeAttempted?: boolean;
      recovering?: boolean;
    };
  };
}

// Knowledge Base & Recall Library Types
export interface SopStrategyItem {
  id: string;
  title: string;
  riskLevel: "高风险" | "中风险" | "低风险";
  category: string;
  triggerCondition: string;
  actionSteps: string[];
  evidenceRequirement: string;
  reviewPeriod: string;
  updatedAt: string;
}

export interface CipherVariantItem {
  id: string;
  originalTerm: string;
  variants: string[];
  category: string;
  riskPattern: string;
  detectionRate: string;
  updatedAt: string;
}

export interface LegalPolicyItem {
  id: string;
  title: string;
  issuingBody: string;
  effectiveDate: string;
  clauseNo: string;
  summary: string;
  relevantRiskTypes: string[];
  docUrl?: string;
}

export interface RecallLibraryItem {
  id: string;
  name: string;
  category: string;
  usageDescription: string;
  words: string[];
  applicablePlatforms: string[];
  status: "启用" | "停用";
  updatedAt: string;
  wordCount: number;
}

export type RiskLevel = "低风险" | "中风险" | "高风险";

export interface RiskRule {
  id: string;
  name: string;
  content: string;
  suggestedLevel: RiskLevel;
  exemptionConditions: string;
  applicationStages: ("图片证据提取" | "视频关键帧提取" | "融合研判")[];
  notes?: string;
  enabled: boolean;
}

export interface RiskCategory {
  id: string;
  name: string;
  rules: RiskRule[];
}

export interface GeneralExemption {
  id: string;
  title: string;
  description: string;
  enabled: boolean;
}

export interface AuditRuleSet {
  id: string;
  name: string;
  category: string;
  version: string;
  status: "已发布" | "草稿" | "待审核";
  updatedAt: string;
  referencedTaskCount: number;
  generalExemptions: GeneralExemption[];
  categories: RiskCategory[];
}
