export type AccountStatus = "continuous" | "watching" | "completed";

export type RiskLevel = "high" | "medium" | "review" | "low";

export interface AccountMetrics {
  recentRiskCount: number;
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
  level: RiskLevel;
  summary: string;
  sourceType: string;
  discoveredAt: string;
  hitRules: string[];
  coverUrl?: string;
}

export interface RelatedAccount {
  id: string;
  name: string;
  avatarUrl: string;
  reason: string;
  tags: string[];
  lastSeenAt: string;
}

export interface MonitoringPlan {
  lexiconLibraries: string[];
  frequency: string;
  strategy: string;
  referencePlan: string;
  taskStatus: string;
  scope: string[];
  riskTypes: string[];
  autoBackfillRule: string;
}

export interface TimelineEvent {
  id: string;
  time: string;
  title: string;
  description: string;
  type: "crawl" | "analysis" | "risk" | "relation" | "plan";
}

export interface MonitoredAccount {
  id: string;
  name: string;
  platform: "抖音" | "小红书" | "快手";
  platformAccountId: string;
  avatarUrl: string;
  monitorScope: string[];
  status: AccountStatus;
  metrics: AccountMetrics;
  latestRisk: RiskOutput | null;
  riskOutputs: RiskOutput[];
  relatedAccounts: RelatedAccount[];
  monitoringPlan: MonitoringPlan;
  timeline: TimelineEvent[];
}
