import { apiRequest } from "./apiClient";
import { buildMockConfigSnapshot } from "../mocks/configCenter";
import type { RawJob } from "../types/jobs";
import type {
  ConfigCenterSnapshot,
  LexiconCategory,
  LexiconReference,
  PolicyCategory,
  PolicyReference,
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
  updated_by?: string;
}

interface RawLexiconKeyword {
  keyword?: string;
  match_type?: string;
  platform?: string;
  enabled?: boolean | number;
}

interface RawLexiconCategory {
  id?: string;
  title?: string;
  risk_label?: string;
  chips?: string[];
  keywords?: RawLexiconKeyword[];
  updated_at?: string;
  updated_by?: string;
}

interface PolicyPayload {
  items?: RawAuditPolicy[];
}

interface LexiconPayload {
  categories?: RawLexiconCategory[];
}

const fallbackSnapshot = buildMockConfigSnapshot();

const categoryByLibraryId: Record<string, PolicyCategory> = {
  gambling: "赌博博彩",
  fraud: "诈骗",
  prohibited: "违规引流",
  soft: "软色情",
  terror: "暴恐",
  drug: "涉毒",
  hate: "仇恨歧视",
  minority: "其他"
};

const policyCategories: PolicyCategory[] = [
  "赌博博彩",
  "诈骗",
  "违规引流",
  "软色情",
  "暴恐",
  "涉毒",
  "仇恨歧视",
  "综合",
  "其他"
];

const capabilityLabels: Record<string, string> = {
  text: "文本语义",
  ocr: "OCR",
  asr: "ASR",
  vision: "视觉识别",
  comment: "评论聚集"
};

const defaultContentScopes = ["标题正文", "评论弹幕", "图片视频", "语音内容"];

export const policyCategoryOptions: Array<"全部" | PolicyCategory> = ["全部", ...policyCategories];
export const lexiconCategoryOptions: Array<"全部" | LexiconCategory> = ["全部", ...policyCategories];

export async function fetchConfigCenterSnapshot(): Promise<ConfigCenterSnapshot> {
  if (shouldUseLocalMockData()) {
    return buildMockConfigSnapshot();
  }

  const [policyResult, lexiconResult, jobResult] = await Promise.allSettled([
    fetchRawPolicies(),
    fetchRawLexicons(),
    fetchRawJobs()
  ]);

  if (policyResult.status === "rejected" && lexiconResult.status === "rejected") {
    return buildMockConfigSnapshot();
  }

  const rawLexicons = lexiconResult.status === "fulfilled" ? lexiconResult.value : [];
  const lexiconNameMap = new Map(rawLexicons.map((item) => [String(item.id || ""), String(item.title || item.id || "")]));
  const rawJobs = jobResult.status === "fulfilled" ? jobResult.value : [];
  const policies =
    policyResult.status === "fulfilled"
      ? rawPoliciesToResearchPolicies(policyResult.value, rawJobs, lexiconNameMap)
      : fallbackSnapshot.policies;
  const lexicons =
    lexiconResult.status === "fulfilled"
      ? rawLexiconsToRiskLexicons(rawLexicons, policies)
      : fallbackSnapshot.lexicons;

  return buildConfigCenterSnapshot(policies, lexicons);
}

export function deletePolicy(policyId: string) {
  return apiRequest<{ ok: boolean; id: string }>(`/api/audit-policies/${encodeURIComponent(policyId)}`, {
    method: "DELETE"
  }).catch(() => ({ ok: true, id: policyId }));
}

export function deleteLexicon(lexiconId: string) {
  return apiRequest<{ ok: boolean; id: string }>(`/api/lexicons/${encodeURIComponent(lexiconId)}`, {
    method: "DELETE"
  }).catch(() => ({ ok: true, id: lexiconId }));
}

export function publishPolicy(policyId: string) {
  return apiRequest<RawAuditPolicy>(`/api/audit-policies/${encodeURIComponent(policyId)}/publish`, {
    method: "POST"
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

export function getPolicyStatusLabel(status: PolicyStatus) {
  const labels: Record<PolicyStatus, string> = {
    published: "已发布",
    draft: "草稿",
    reviewing: "待审核",
    disabled: "已停用"
  };
  return labels[status];
}

export function getPolicyStatusTone(status: PolicyStatus) {
  const tones: Record<PolicyStatus, "success" | "warning" | "info" | "neutral"> = {
    published: "success",
    draft: "info",
    reviewing: "warning",
    disabled: "neutral"
  };
  return tones[status];
}

function fetchRawPolicies() {
  return apiRequest<PolicyPayload>("/api/audit-policies").then((payload) => payload.items || []);
}

function shouldUseLocalMockData() {
  return !window.XHS_AUDIT_API_BASE && Boolean(window.location.port);
}

function fetchRawLexicons() {
  return apiRequest<LexiconPayload>("/api/lexicons").then((payload) => payload.categories || []);
}

function fetchRawJobs() {
  return apiRequest<RawJob[]>("/api/jobs").catch(() => []);
}

function rawPoliciesToResearchPolicies(
  rawPolicies: RawAuditPolicy[],
  rawJobs: RawJob[],
  lexiconNameMap: Map<string, string>
): ResearchPolicy[] {
  const referencesByPolicy = buildPolicyReferences(rawJobs);

  return rawPolicies.map((policy) => {
    const config = pickPolicyConfig(policy);
    const libraryIds = toStringList(config.library_ids);
    const capabilities = toStringList(config.capabilities);
    const contentScopes = toStringList(config.outputs).filter((item) => item !== "询证材料");
    const scenarioTags = toStringList(config.scenarios);
    const fallback = fallbackSnapshot.policies.find((item) => item.id === policy.id);
    const category = inferPolicyCategory(policy, libraryIds, fallback?.category);
    const status = normalizePolicyStatus(policy.status);
    const updatedAt = policy.updated_at || policy.published_at || fallback?.updatedAt || "";

    return {
      id: String(policy.id || fallback?.id || ""),
      name: String(policy.name || fallback?.name || "未命名研判方案"),
      description: String(policy.description || fallback?.description || "暂无方案说明"),
      category,
      status,
      scenarioTags: scenarioTags.length ? scenarioTags : fallback?.scenarioTags || ["平台内容"],
      lexiconIds: libraryIds.length ? libraryIds : fallback?.lexiconIds || [],
      lexiconNames: libraryIds.map((id) => lexiconNameMap.get(id) || id).filter(Boolean),
      contentScopes: contentScopes.length ? contentScopes : fallback?.contentScopes || defaultContentScopes,
      recognitionCapabilities: capabilities.length
        ? capabilities.map((item) => capabilityLabels[item] || item)
        : fallback?.recognitionCapabilities || ["文本语义"],
      references: referencesByPolicy.get(String(policy.id || "")) || fallback?.references || [],
      version: {
        version: String(policy.published_version || policy.draft_version || fallback?.version.version || "draft"),
        updatedAt,
        updatedBy: String(policy.updated_by || fallback?.updatedBy || "系统")
      },
      createdAt: policy.created_at || fallback?.createdAt || "",
      updatedAt,
      updatedBy: String(policy.updated_by || fallback?.updatedBy || "系统")
    };
  });
}

function rawLexiconsToRiskLexicons(rawLexicons: RawLexiconCategory[], policies: ResearchPolicy[]): RiskLexicon[] {
  const referencesByLexicon = buildLexiconReferences(policies);

  return rawLexicons.map((lexicon) => {
    const fallback = fallbackSnapshot.lexicons.find((item) => item.id === lexicon.id);
    const keywords = (lexicon.keywords || []).filter((item) => item.enabled !== false && item.enabled !== 0);
    const platformSearchWords = keywordValuesByType(keywords, "平台搜索词");
    const platformTags = keywordValuesByType(keywords, "平台标签", "tag");
    const termKeywords = keywords
      .filter((item) => !["平台搜索词", "平台标签", "tag"].includes(String(item.match_type || "")))
      .map((item) => String(item.keyword || "").trim())
      .filter(Boolean);

    return {
      id: String(lexicon.id || fallback?.id || ""),
      name: String(lexicon.title || fallback?.name || "未命名黑话库"),
      category: inferLexiconCategory(lexicon, fallback?.category),
      entryCount: termKeywords.length || fallback?.entryCount || 0,
      platformSearchWordCount: platformSearchWords.length || fallback?.platformSearchWordCount || 0,
      platformTagCount: platformTags.length || fallback?.platformTagCount || 0,
      keywords: termKeywords.length ? termKeywords.slice(0, 8) : fallback?.keywords || [],
      platformSearchWords: platformSearchWords.length ? platformSearchWords.slice(0, 8) : fallback?.platformSearchWords || [],
      platformTags: platformTags.length ? platformTags.slice(0, 8) : fallback?.platformTags || [],
      references: referencesByLexicon.get(String(lexicon.id || "")) || fallback?.references || [],
      updatedAt: lexicon.updated_at || fallback?.updatedAt || "",
      updatedBy: String(lexicon.updated_by || fallback?.updatedBy || "系统")
    };
  });
}

export function buildConfigCenterSnapshot(policies: ResearchPolicy[], lexicons: RiskLexicon[]): ConfigCenterSnapshot {
  return {
    policies,
    lexicons,
    policySummary: {
      total: policies.length,
      published: policies.filter((policy) => policy.status === "published").length,
      draft: policies.filter((policy) => policy.status === "draft").length,
      referencedTaskCount: policies.reduce((sum, policy) => sum + policy.references.length, 0)
    },
    lexiconSummary: {
      total: lexicons.length,
      entryCount: lexicons.reduce((sum, lexicon) => sum + lexicon.entryCount, 0),
      policyReferenceCount: lexicons.reduce((sum, lexicon) => sum + lexicon.references.length, 0),
      latestUpdatedAt: lexicons
        .map((lexicon) => lexicon.updatedAt)
        .filter(Boolean)
        .sort((a, b) => Date.parse(b) - Date.parse(a))[0] || ""
    }
  };
}

function pickPolicyConfig(policy: RawAuditPolicy) {
  const publishedConfig = policy.published_config || {};
  const draftConfig = policy.config || {};
  return Object.keys(publishedConfig).length ? publishedConfig : draftConfig;
}

function buildPolicyReferences(rawJobs: RawJob[]) {
  const map = new Map<string, PolicyReference[]>();
  rawJobs.forEach((job) => {
    const revision = job.current_audit_config_revision;
    const policyId = String(revision?.source_policy_id || "");
    if (!policyId) {
      return;
    }
    const item: PolicyReference = {
      id: job.id,
      name: job.display_name || job.keyword || job.input_filename || job.id,
      status: normalizeJobStatus(job.status)
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

function keywordValuesByType(keywords: RawLexiconKeyword[], ...types: string[]) {
  const typeSet = new Set(types);
  return keywords
    .filter((item) => typeSet.has(String(item.match_type || "")))
    .map((item) => String(item.keyword || "").trim())
    .filter(Boolean);
}

function normalizePolicyStatus(status: RawPolicyStatus = "draft"): PolicyStatus {
  if (status === "published") {
    return "published";
  }
  if (status === "reviewing" || status === "pending_review") {
    return "reviewing";
  }
  if (status === "disabled" || status === "inactive") {
    return "disabled";
  }
  return "draft";
}

function inferPolicyCategory(policy: RawAuditPolicy, libraryIds: string[], fallback: PolicyCategory = "其他"): PolicyCategory {
  const firstMatch = libraryIds.map((id) => categoryByLibraryId[id]).find(Boolean);
  if (firstMatch) {
    return firstMatch;
  }
  const haystack = `${policy.name || ""} ${policy.description || ""}`;
  return inferCategoryFromText(haystack, fallback);
}

function inferLexiconCategory(lexicon: RawLexiconCategory, fallback: LexiconCategory = "其他"): LexiconCategory {
  const byId = categoryByLibraryId[String(lexicon.id || "")];
  if (byId) {
    return byId;
  }
  return inferCategoryFromText(`${lexicon.title || ""} ${lexicon.risk_label || ""}`, fallback);
}

function inferCategoryFromText(value: string, fallback: PolicyCategory): PolicyCategory {
  if (value.includes("博彩") || value.includes("赌博")) {
    return "赌博博彩";
  }
  if (value.includes("诈")) {
    return "诈骗";
  }
  if (value.includes("引流") || value.includes("违禁")) {
    return "违规引流";
  }
  if (value.includes("色情") || value.includes("低俗")) {
    return "软色情";
  }
  if (value.includes("暴恐") || value.includes("极端")) {
    return "暴恐";
  }
  if (value.includes("毒")) {
    return "涉毒";
  }
  if (value.includes("仇恨") || value.includes("歧视") || value.includes("宗教")) {
    return "仇恨歧视";
  }
  return fallback;
}

function normalizeJobStatus(status: string) {
  if (status === "completed") {
    return "已完成";
  }
  if (status === "failed") {
    return "失败";
  }
  if (["analysis_stopped", "analysis_paused", "crawl_paused", "stopped"].includes(status)) {
    return "已暂停";
  }
  return "运行中";
}

function toStringList(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item || "").trim()).filter(Boolean) : [];
}
