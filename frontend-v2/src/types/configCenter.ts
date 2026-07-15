export type PolicyStatus = "published" | "draft" | "reviewing" | "disabled";

export type PolicyCategory =
  | "赌博博彩"
  | "诈骗"
  | "违规引流"
  | "软色情"
  | "暴恐"
  | "涉毒"
  | "仇恨歧视"
  | "综合"
  | "其他";

export type LexiconCategory = PolicyCategory;

export type PolicySortKey = "updated" | "references" | "name" | "created";
export type LexiconSortKey = "updated" | "entries" | "references" | "name";
export type ReferenceFilter = "全部" | "已被引用" | "未被引用";

export interface PolicyReference {
  id: string;
  name: string;
  status: string;
}

export interface PolicyVersion {
  version: string;
  updatedAt: string;
  updatedBy: string;
}

export interface ResearchPolicy {
  id: string;
  name: string;
  description: string;
  category: PolicyCategory;
  status: PolicyStatus;
  scenarioTags: string[];
  lexiconIds: string[];
  lexiconNames: string[];
  contentScopes: string[];
  recognitionCapabilities: string[];
  references: PolicyReference[];
  version: PolicyVersion;
  createdAt: string;
  updatedAt: string;
  updatedBy: string;
}

export interface PolicySummary {
  total: number;
  published: number;
  draft: number;
  referencedTaskCount: number;
}

export interface LexiconReference {
  id: string;
  name: string;
  status: PolicyStatus;
}

export interface RiskLexicon {
  id: string;
  name: string;
  category: LexiconCategory;
  entryCount: number;
  platformSearchWordCount: number;
  platformTagCount: number;
  keywords: string[];
  platformSearchWords: string[];
  platformTags: string[];
  references: LexiconReference[];
  updatedAt: string;
  updatedBy: string;
}

export interface LexiconSummary {
  total: number;
  entryCount: number;
  policyReferenceCount: number;
  latestUpdatedAt: string;
}

export interface ConfigCenterSnapshot {
  policies: ResearchPolicy[];
  lexicons: RiskLexicon[];
  policySummary: PolicySummary;
  lexiconSummary: LexiconSummary;
}
