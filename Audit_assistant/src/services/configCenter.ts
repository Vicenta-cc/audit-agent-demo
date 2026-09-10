import { apiRequest } from "./apiClient";
import type {
  ConfigCenterSnapshot,
  DetectionCapability,
  DetectionScope,
  ImportanceLevel,
  LexiconReference,
  LexiconSaveInput,
  LexiconTerm,
  PolicyDetectionConfig,
  PolicyReference,
  PolicySaveInput,
  PolicyStatus,
  ResearchPolicy,
  RiskLexicon
} from "../types/configCenter";

type RawPolicyStatus = "published" | "draft" | "reviewing" | "disabled" | string;

interface RawAuditPolicy {
  id?: string;
  name?: string;
  description?: string;
  status?: RawPolicyStatus;
  draft_version?: string;
  published_version?: string;
  config?: Record<string, unknown>;
  published_config?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  published_at?: string;
}

interface RawLexiconKeyword {
  entry_id?: string;
  parent_entry_id?: string;
  entry_kind?: string;
  id?: number | string;
  keyword?: string;
  match_type?: string;
  enabled?: boolean | number;
  note?: string;
}

interface RawLexiconCategory {
  version?: number;
  id?: string;
  title?: string;
  keywords?: RawLexiconKeyword[];
  created_at?: string;
  updated_at?: string;
}

interface PolicyPayload {
  items?: RawAuditPolicy[];
}

interface LexiconPayload {
  categories?: RawLexiconCategory[];
}

interface LexiconMutationResponse {
  category: RawLexiconCategory;
}

interface RawPolicyReference {
  id?: string;
  display_name?: string;
  keyword?: string;
  input_filename?: string;
  status?: string;
  source_policy_id?: string;
}

interface PolicyReferencePayload {
  items?: RawPolicyReference[];
}

const detectionTemplates: Array<
  Omit<PolicyDetectionConfig, "enabled" | "importance">
> = [
  {
    scope: "title_body",
    location: "标题与正文",
    evidence: "文字内容命中",
    capability: "text",
    capabilityLabel: "文本语义"
  },
  {
    scope: "comment",
    location: "评论与弹幕",
    evidence: "逐条评论命中",
    capability: "comment",
    capabilityLabel: "评论识别"
  },
  {
    scope: "image",
    location: "图片与封面",
    evidence: "画面文字命中",
    capability: "ocr",
    capabilityLabel: "画面文字"
  },
  {
    scope: "video",
    location: "视频画面",
    evidence: "画面特征命中",
    capability: "vision",
    capabilityLabel: "视觉识别"
  },
  {
    scope: "audio",
    location: "语音内容",
    evidence: "语音内容命中",
    capability: "asr",
    capabilityLabel: "语音识别"
  }
];

const scoringModeLabels: Record<string, string> = {
  balanced: "均衡模式",
  strict: "严格模式",
  text_first: "文字优先",
  vision_first: "视觉优先",
  custom: "自定义"
};

export async function fetchConfigCenterSnapshot(): Promise<ConfigCenterSnapshot> {
  const [rawPolicies, rawLexicons, rawJobs] = await Promise.all([
    fetchRawPolicies(),
    fetchRawLexicons(),
    fetchRawJobs()
  ]);
  const lexiconNameMap = new Map(
    rawLexicons.map((item) => [String(item.id || ""), String(item.title || item.id || "")])
  );
  const policies = rawPoliciesToResearchPolicies(rawPolicies, rawJobs, lexiconNameMap);
  const lexicons = rawLexiconsToRiskLexicons(rawLexicons, policies);
  return buildConfigCenterSnapshot(policies, lexicons);
}

export function deletePolicy(policyId: string) {
  return apiRequest<{ ok: boolean; id: string }>(`/api/audit-policies/${encodeURIComponent(policyId)}`, {
    method: "DELETE"
  });
}

export function deleteLexicon(lexiconId: string) {
  return apiRequest<{ ok: boolean; id: string }>(`/api/lexicons/${encodeURIComponent(lexiconId)}`, {
    method: "DELETE"
  });
}

export async function savePolicy(input: PolicySaveInput) {
  const enabled = input.detectionConfigs.filter((item) => item.enabled);
  const ruleImportance = Object.fromEntries(
    enabled.map((item) => [item.capability, importanceToApi(item.importance)])
  );
  const config = {
    library_ids: input.lexiconIds,
    capabilities: enabled.map((item) => item.capability),
    scopes: enabled.map((item) => item.scope),
    scoring_template: input.scoringMode,
    rule_importance: ruleImportance,
    outputs: enabled.map((item) => item.evidence),
    rule_snapshot: { rule_importance: ruleImportance }
  };
  const path = input.id
    ? `/api/audit-policies/${encodeURIComponent(input.id)}`
    : "/api/audit-policies";
  return apiRequest<RawAuditPolicy>(path, {
    method: input.id ? "PATCH" : "POST",
    body: JSON.stringify({
      name: input.name.trim(),
      description: input.description.trim(),
      library_ids: input.lexiconIds,
      capabilities: config.capabilities,
      scoring_template: input.scoringMode,
      rule_snapshot: config.rule_snapshot,
      config
    })
  });
}

export async function saveLexicon(input: LexiconSaveInput) {
  const path = input.id ? `/api/lexicons/${encodeURIComponent(input.id)}` : "/api/lexicons";
  return apiRequest<LexiconMutationResponse>(path, {
    method: input.id ? "PATCH" : "POST",
    body: JSON.stringify({
      title: input.name.trim(),
      expected_version: input.expectedVersion,
      entries: input.terms
        .filter((term) => term.mainTerm.trim())
        .map((term) => ({
          id: term.id,
          main_term: term.mainTerm.trim(),
          variants: term.variants.map((item) => item.trim()).filter(Boolean),
          query_type: term.queryType,
          enabled: term.enabled
        }))
    })
  });
}

export function formatCompactDateTime(value: string) {
  if (!value) {
    return "-";
  }
  return value.replace("T", " ").slice(5, 16);
}

export function formatFullDateTime(value: string) {
  if (!value) {
    return "-";
  }
  return value.replace("T", " ").slice(0, 16);
}

export function formatNumber(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function fetchRawPolicies() {
  return apiRequest<PolicyPayload>("/api/audit-policies").then((payload) => payload.items || []);
}

function fetchRawLexicons() {
  return apiRequest<LexiconPayload>("/api/lexicons").then((payload) => payload.categories || []);
}

function fetchRawJobs() {
  return apiRequest<PolicyReferencePayload>("/api/job-policy-references").then((payload) => payload.items || []);
}

function rawPoliciesToResearchPolicies(
  rawPolicies: RawAuditPolicy[],
  rawJobs: RawPolicyReference[],
  lexiconNameMap: Map<string, string>
): ResearchPolicy[] {
  const referencesByPolicy = buildPolicyReferences(rawJobs);

  return rawPolicies.map((policy) => {
    const config = pickPolicyConfig(policy);
    const libraryIds = toStringList(config.library_ids);
    const capabilities = new Set(toStringList(config.capabilities));
    const scopes = new Set(toStringList(config.scopes));
    const hasExplicitScopes = scopes.size > 0;
    const importance = toRecord(config.rule_importance) || toRecord(toRecord(config.rule_snapshot)?.rule_importance);
    const detectionConfigs = detectionTemplates.map((item) => ({
      ...item,
      enabled: hasExplicitScopes ? scopes.has(item.scope) : capabilities.has(item.capability),
      importance: importanceFromApi(importance?.[item.capability])
    }));
    const enabledDetection = detectionConfigs.filter((item) => item.enabled);
    const scoringMode = String(config.scoring_template || "balanced");
    const updatedAt = String(policy.updated_at || policy.published_at || "");

    return {
      id: String(policy.id || ""),
      name: String(policy.name || "未命名研判方案"),
      description: String(policy.description || ""),
      status: normalizePolicyStatus(policy.status),
      lexiconIds: libraryIds,
      lexiconNames: libraryIds.map((id) => lexiconNameMap.get(id) || id).filter(Boolean),
      contentScopes: enabledDetection.map((item) => item.location),
      recognitionCapabilities: enabledDetection.map((item) => item.capabilityLabel),
      detectionConfigs,
      scoringMode,
      scoringModeLabel: scoringModeLabels[scoringMode] || scoringMode,
      references: referencesByPolicy.get(String(policy.id || "")) || [],
      version: {
        version: String(policy.published_version || policy.draft_version || "draft"),
        updatedAt
      },
      createdAt: String(policy.created_at || ""),
      updatedAt
    };
  });
}

function rawLexiconsToRiskLexicons(rawLexicons: RawLexiconCategory[], policies: ResearchPolicy[]): RiskLexicon[] {
  const referencesByLexicon = buildLexiconReferences(policies);

  return rawLexicons.map((lexicon) => {
    const terms = groupLexiconTerms(lexicon.keywords || []);
    return {
      id: String(lexicon.id || ""),
      name: String(lexicon.title || "未命名黑话库"),
      version: lexicon.version,
      entryCount: terms.length,
      keywords: terms.map((item) => item.mainTerm),
      terms,
      references: referencesByLexicon.get(String(lexicon.id || "")) || [],
      createdAt: String(lexicon.created_at || ""),
      updatedAt: String(lexicon.updated_at || lexicon.created_at || "")
    };
  });
}

export function buildConfigCenterSnapshot(policies: ResearchPolicy[], lexicons: RiskLexicon[]): ConfigCenterSnapshot {
  return {
    policies,
    lexicons,
    policySummary: {
      total: policies.length,
      lexiconReferenceCount: policies.reduce((sum, policy) => sum + policy.lexiconIds.length, 0),
      referencedTaskCount: policies.reduce((sum, policy) => sum + policy.references.length, 0)
    },
    lexiconSummary: {
      total: lexicons.length,
      entryCount: lexicons.reduce((sum, lexicon) => sum + lexicon.entryCount, 0),
      policyReferenceCount: lexicons.reduce((sum, lexicon) => sum + lexicon.references.length, 0)
    }
  };
}

function groupLexiconTerms(keywords: RawLexiconKeyword[]): LexiconTerm[] {
  const variantsByParent = new Map<string, string[]>();
  const mainRows: RawLexiconKeyword[] = [];

  keywords.forEach((row) => {
    const variantOf = row.parent_entry_id || (!row.entry_kind ? parseVariantOf(row.note) : "");
    const keyword = String(row.keyword || "").trim();
    if (!keyword) {
      return;
    }
    if (variantOf || row.entry_kind === "variant") {
      variantsByParent.set(variantOf, [...(variantsByParent.get(variantOf) || []), keyword]);
      return;
    }
    mainRows.push(row);
  });

  const grouped = new Map<string, LexiconTerm>();
  mainRows.forEach((row, index) => {
    const mainTerm = String(row.keyword || "").trim();
    const groupingKey = row.entry_id || mainTerm;
    const existing = grouped.get(groupingKey);
    const queryType = (row.entry_kind ? row.entry_kind === "tag" : isTagType(row.match_type)) ? "tag" : "keyword";
    if (existing) {
      if (queryType === "tag") {
        existing.queryType = "tag";
      }
      existing.enabled = existing.enabled || (row.enabled !== false && row.enabled !== 0);
      return;
    }
    grouped.set(groupingKey, {
      id: row.entry_id || String(row.id ?? `term_${index}`),
      mainTerm,
      variants: dedupeStrings(variantsByParent.get(row.entry_id || "") || variantsByParent.get(mainTerm) || []),
      queryType,
      enabled: row.enabled !== false && row.enabled !== 0
    });
  });

  return [...grouped.values()];
}

function pickPolicyConfig(policy: RawAuditPolicy) {
  const draftConfig = policy.config || {};
  return Object.keys(draftConfig).length ? draftConfig : policy.published_config || {};
}

function buildPolicyReferences(rawJobs: RawPolicyReference[]) {
  const map = new Map<string, PolicyReference[]>();
  rawJobs.forEach((job) => {
    const policyId = String(job.source_policy_id || "");
    if (!policyId) {
      return;
    }
    const item: PolicyReference = {
      id: String(job.id || ""),
      name: job.display_name || job.keyword || job.input_filename || String(job.id || ""),
      status: normalizeJobStatus(String(job.status || ""))
    };
    map.set(policyId, [...(map.get(policyId) || []), item]);
  });
  return map;
}

function buildLexiconReferences(policies: ResearchPolicy[]) {
  const map = new Map<string, LexiconReference[]>();
  policies.forEach((policy) => {
    policy.lexiconIds.forEach((lexiconId) => {
      const item: LexiconReference = { id: policy.id, name: policy.name, status: policy.status };
      map.set(lexiconId, [...(map.get(lexiconId) || []), item]);
    });
  });
  return map;
}

function normalizePolicyStatus(status: RawPolicyStatus = "draft"): PolicyStatus {
  if (status === "published") return "published";
  if (status === "reviewing" || status === "pending_review") return "reviewing";
  if (status === "disabled" || status === "inactive") return "disabled";
  return "draft";
}

function normalizeJobStatus(status: string) {
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  if (["analysis_stopped", "analysis_paused", "crawl_paused", "stopped", "interrupted"].includes(status)) return "已暂停";
  return "运行中";
}

function importanceFromApi(value: unknown): ImportanceLevel {
  if (value === "high" || value === "very_high") return "高";
  if (value === "low") return "低";
  return "中";
}

function importanceToApi(value: ImportanceLevel) {
  if (value === "高") return "high";
  if (value === "低") return "low";
  return "medium";
}

function parseVariantOf(note: unknown) {
  if (!note) return "";
  try {
    const parsed = JSON.parse(String(note)) as { variant_of?: unknown };
    return String(parsed.variant_of || "").trim();
  } catch {
    return "";
  }
}

function isTagType(value: unknown) {
  return ["平台标签", "tag"].includes(String(value || ""));
}

function toStringList(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item || "").trim()).filter(Boolean) : [];
}

function toRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function dedupeStrings(values: string[]) {
  return [...new Set(values.map((item) => item.trim()).filter(Boolean))];
}

export const detectionScopeByEditorKey: Record<string, DetectionScope> = {
  title: "title_body",
  comment: "comment",
  image: "image",
  video: "video",
  audio: "audio"
};

export const detectionCapabilityByEditorKey: Record<string, DetectionCapability> = {
  title: "text",
  comment: "comment",
  image: "ocr",
  video: "vision",
  audio: "asr"
};
