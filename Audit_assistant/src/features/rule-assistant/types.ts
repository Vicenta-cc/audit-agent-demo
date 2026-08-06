import type { AuditRuleSet, RiskRule } from "../../types/investigation";

export type RuleAssistantMessageKind =
  | "text"
  | "ruleset-summary"
  | "high-risk-rules"
  | "resource-inventory"
  | "resource-coverage"
  | "rule-change-suggestion"
  | "new-rule-suggestion"
  | "file-analysis"
  | "lexicon-answer"
  | "lexicon-suggestion"
  | "lexicon-file-analysis";

export type RuleAssistantDrawerType =
  | "rule"
  | "lexicon"
  | "candidate-editor"
  | "suggestion-diff";

export interface RuleAssistantRuleSet {
  id: string;
  name: string;
  description?: string;
  isDraft?: boolean;
}

export interface RuleAssistantLexicon {
  id: string;
  name: string;
  description?: string;
  isDraft?: boolean;
}

export interface RuleAssistantLexiconTerm {
  id: string;
  primary: string;
  queryType: "关键词" | "标签";
  variants: string;
  enabled: boolean;
}

export interface RuleAssistantCandidateLexicon {
  id: string;
  name: string;
  category: string;
  description: string;
  terms: RuleAssistantLexiconTerm[];
}

export interface RuleAssistantCandidateLexiconPreview {
  id: string;
  conversationId: string;
  sourceLexiconId: string;
  origin: "conversation" | "file";
  mode: "append" | "create";
  status: "pending" | "applied" | "created" | "cancelled";
  library: RuleAssistantCandidateLexicon;
  baselineTermIds: string[];
  addedTermIds: string[];
}

export interface RuleAssistantMessageResourceContext {
  resourceId: string;
  resourceType: "rule-set" | "lexicon";
  resourceName: string;
}

export interface RuleAssistantMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  resourceContext?: RuleAssistantMessageResourceContext;
  kind?: RuleAssistantMessageKind;
  status?: "pending" | "applied" | "completed" | "cancelled";
  sourceRuleSetIds?: string[];
  sourceLexiconIds?: string[];
  fileName?: string;
  previewId?: string;
  lexiconDraft?: RuleAssistantCandidateLexicon;
}

export interface RuleAssistantConversation {
  id: string;
  title: string;
  ruleSetId: string | null;
  lexiconId?: string | null;
  updatedAt: string;
  messages: RuleAssistantMessage[];
  purpose?: "standard" | "create-rule-set" | "create-lexicon";
  scenario?: "import-edit" | "direct-edit" | "ruleset-query" | "global-resource" | "lexicon-direct-edit" | "create-terror-rule-set" | "create-terror-lexicon";
}

export interface RuleAssistantCandidateRuleSet extends AuditRuleSet {
  description: string;
}

export interface RuleAssistantPreviewRuleChange {
  kind: "modified" | "added";
  changedFields: Array<"name" | "content" | "suggestedLevel" | "exemptionConditions" | "applicationStages" | "notes">;
  before?: RiskRule;
}

export interface RuleAssistantCandidatePreview {
  id: string;
  conversationId: string;
  sourceRuleSetId: string | null;
  origin: "file" | "conversation";
  mode: "replace-or-create" | "create-only";
  fileName?: string;
  status: "pending" | "applied" | "replaced" | "created" | "cancelled";
  ruleSet: RuleAssistantCandidateRuleSet;
  ruleChanges: Record<string, RuleAssistantPreviewRuleChange>;
}

export interface RuleAssistantReturnLocation {
  pathname: string;
  search?: string;
  scrollTop?: number;
  activeSessionId?: string;
  activeSubView?: "users" | "crawler-accounts" | "knowledge-center" | null;
}

export interface RuleAssistantRouteState {
  entryRuleSetId?: string;
  returnLocation?: RuleAssistantReturnLocation;
}

export interface RuleAssistantDrawerState {
  type: RuleAssistantDrawerType;
  ruleSetId?: string;
  lexiconId?: string;
  payloadId?: string;
}
