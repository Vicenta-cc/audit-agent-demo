export type PolicyStatus = "published" | "draft" | "reviewing" | "disabled";

export type PolicySortKey = "updated" | "references" | "name" | "created";
export type LexiconSortKey = "updated" | "entries" | "references" | "name";
export type ReferenceFilter = "全部" | "已被引用" | "未被引用";
export type DetectionScope = "title_body" | "comment" | "image" | "video" | "audio";
export type DetectionCapability = "text" | "comment" | "ocr" | "vision" | "asr";
export type ImportanceLevel = "低" | "中" | "高";
export type LexiconQueryType = "keyword" | "tag";

export interface PolicyReference {
  id: string;
  name: string;
  status: string;
}

export interface PolicyVersion {
  version: string;
  updatedAt: string;
}

export interface PolicyDetectionConfig {
  scope: DetectionScope;
  location: string;
  evidence: string;
  capability: DetectionCapability;
  capabilityLabel: string;
  enabled: boolean;
  importance: ImportanceLevel;
}

export interface ResearchPolicy {
  id: string;
  name: string;
  description: string;
  status: PolicyStatus;
  lexiconIds: string[];
  lexiconNames: string[];
  contentScopes: string[];
  recognitionCapabilities: string[];
  detectionConfigs: PolicyDetectionConfig[];
  scoringMode: string;
  scoringModeLabel: string;
  references: PolicyReference[];
  version: PolicyVersion;
  createdAt: string;
  updatedAt: string;
}

export interface PolicySummary {
  total: number;
  lexiconReferenceCount: number;
  referencedTaskCount: number;
}

export interface LexiconReference {
  id: string;
  name: string;
  status: PolicyStatus;
}

export interface LexiconTerm {
  id: string;
  mainTerm: string;
  variants: string[];
  queryType: LexiconQueryType;
  enabled: boolean;
}

export interface RiskLexicon {
  description?: string;
  version?: number;
  id: string;
  name: string;
  entryCount: number;
  keywords: string[];
  terms: LexiconTerm[];
  references: LexiconReference[];
  createdAt: string;
  updatedAt: string;
}

export interface LexiconSummary {
  total: number;
  entryCount: number;
  policyReferenceCount: number;
}

export interface PolicySaveInput {
  id?: string;
  name: string;
  description: string;
  lexiconIds: string[];
  detectionConfigs: PolicyDetectionConfig[];
  scoringMode: string;
}

export interface LexiconSaveInput {
  description?: string;
  expectedVersion?: number;
  id?: string;
  name: string;
  terms: LexiconTerm[];
}

export interface ConfigCenterSnapshot {
  policies: ResearchPolicy[];
  lexicons: RiskLexicon[];
  policySummary: PolicySummary;
  lexiconSummary: LexiconSummary;
}
