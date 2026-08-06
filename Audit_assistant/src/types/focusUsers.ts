export type AccountStatus = "continuous" | "watching" | "completed";

export type RiskLevel = "high" | "medium" | "review" | "low";

export type FocusUserPlatform = "抖音" | "小红书" | "快手";

export type CommentRiskLevel = "high" | "medium" | "review";

export interface AccountMetrics {
  recentRiskCount: number;
  riskRangeLabel: string;
  highRiskCount: number;
  mediumRiskCount: number;
  pendingReviewCount: number;
  relatedAccountCount: number;
  lastSyncTime: string;
  lastSyncAgo: string;
  currentTaskName: string;
  currentTaskStatus: "running" | "paused" | "completed";
  currentTaskStatusLabel: string;
}

export interface RiskOutput {
  id: string;
  jobId: string;
  title: string;
  level: RiskLevel;
  riskScore: number;
  summary: string;
  sourceType: string;
  discoveredAt: string;
  hitRules: string[];
  coverUrl?: string;
}

export interface RelatedAccount {
  id: string;
  name: string;
  platform: MonitoredAccount["platform"];
  avatarUrl?: string;
  profileUrl: string;
  sourceCommentId: string;
  sourceCommentText: string;
  sourceRiskContent: string;
  sourceAuditResultId: number | null;
  analysisJobId: string;
  analysisJobName: string;
  analysisStatus: string;
  updatedAt: string;
  updatedAtLabel: string;
}

export interface MonitoringPlan {
  lexiconLibraries: string[];
  frequency: string;
  strategy: string;
  referencePlan: string;
  taskStatus: string;
  taskStatusTone: "blue" | "green" | "orange" | "gray";
  scope: string[];
  riskTypes: string[];
  autoBackfillRule: string;
}

export interface TimelineEvent {
  id: string;
  timestamp: string;
  time: string;
  title: string;
  description: string;
  type: "crawl" | "analysis" | "risk" | "relation" | "plan";
}

export interface MonitoredAccount {
  id: string;
  sourceJobId: string;
  sourceAuditResultId: number | null;
  riskDataLoaded: boolean;
  updatedAt: string;
  name: string;
  platform: FocusUserPlatform;
  platformAccountId: string;
  avatarUrl?: string;
  monitorScope: string[];
  status: AccountStatus;
  metrics: AccountMetrics;
  latestRisk: RiskOutput | null;
  riskOutputs: RiskOutput[];
  relatedAccounts: RelatedAccount[];
  monitoringPlan: MonitoringPlan;
  timeline: TimelineEvent[];
}

export interface RiskCommentRecord {
  id: string;
  platform: FocusUserPlatform;
  identity: {
    sec_uid?: string;
    user_id?: string;
    user_unique_id?: string;
    short_user_id?: string;
  };
  authorName: string;
  avatarUrl?: string;
  content: string;
  riskLevel: CommentRiskLevel;
  riskScore: number;
  primaryLibrary?: {
    id: string;
    label: string;
  };
  sourceId: string;
  sourceTitle: string;
  occurredAt: string;
}

export interface RiskDistributionItem {
  key: string;
  label: string;
  count: number;
  percentage: number;
  color: string;
}

export interface CommentRiskProfile {
  id: string;
  name: string;
  platform: FocusUserPlatform;
  avatarUrl?: string;
  violationCount: number;
  highRiskCount: number;
  mediumRiskCount: number;
  pendingReviewCount: number;
  commentAreaCount: number;
  latestAt: string;
  comments: RiskCommentRecord[];
  riskDistribution: RiskDistributionItem[];
  libraryDistribution: RiskDistributionItem[];
}
