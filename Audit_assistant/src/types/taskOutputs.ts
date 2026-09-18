import type { AuditResult } from "./jobs";

export type RiskLevel = "高危" | "中危" | "待复核" | "无风险";

export interface RiskLibrary {
  id: string;
  label: string;
}

export interface EvidenceCounts {
  text: number;
  ocr: number;
  asr: number;
  visual: number;
  comments: number;
}

export interface TaskOutputItem {
  id: string;
  number: string;
  riskLevel: RiskLevel;
  title: string;
  summary: string;
  riskLibrary: RiskLibrary | null;
  evidenceCounts: EvidenceCounts;
  author: string;
  time: string;
  timestamp: string;
  thumbnailUrl: string;
  durationSeconds: number | null;
  sourceUrl: string;
  raw: AuditResult;
}

export interface TaskConfig {
  referencePlan: string;
  configVersion: string;
  updatedAt: string;
  effectiveAt: string;
  platform: string;
  collectionTimeRange: string;
  keywords: string[];
  dataType: string;
  riskLibraries: RiskLibrary[];
  analysisScopes: Array<{ label: string; enabled: boolean }>;
}

export interface ConfigHistoryItem {
  id: string;
  version: string;
  updatedAt: string;
  operator: string;
  summary: string;
  isCurrent: boolean;
}

export type TaskLogLevel = "INFO" | "WARN" | "ERROR";

export interface TaskLog {
  id: string;
  timestamp: string;
  time: string;
  date: string;
  level: TaskLogLevel;
  content: string;
  reason?: string;
  errorCode?: string;
  retryable?: boolean | null;
  action?: string;
}

export type EvidenceTypeFilter = "全部" | "文本" | "OCR" | "ASR" | "视觉" | "评论";
export type OutputSortKey = "latest" | "risk";
export type TaskDrawerType = "logs" | "config" | null;

export interface OutputFiltersValue {
  riskLevel: "全部" | RiskLevel;
  riskLibraryId: string;
  evidenceType: EvidenceTypeFilter;
  query: string;
  sort: OutputSortKey;
}

export interface TaskOutputSummary {
  total: number;
  highRisk: number;
  mediumRisk: number;
  pendingReview: number;
  noRisk: number;
}
