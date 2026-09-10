import { apiRequest } from "./apiClient";
import { fetchAuditResults, fetchJob, getResultTime, isRiskAuditResult } from "./jobs";
import type {
  AccountStatus,
  MonitoredAccount,
  RelatedAccount,
  RiskLevel,
  RiskOutput,
  TimelineEvent
} from "../types/focusUsers";
import type { AuditResult, RawJob } from "../types/jobs";

interface ApiMonitoredUser {
  id?: unknown;
  platform?: unknown;
  stable_key?: unknown;
  display_name?: unknown;
  profile_url?: unknown;
  raw_identity?: unknown;
  source_audit_result_id?: unknown;
  source_job_id?: unknown;
  author?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
  suspected_related_accounts?: unknown;
}

interface ApiRelatedAccount {
  id?: unknown;
  platform?: unknown;
  display_name?: unknown;
  profile_url?: unknown;
  raw_identity?: unknown;
  source_comment_id?: unknown;
  source_comment_text?: unknown;
  source_risk_content?: unknown;
  source_audit_result_id?: unknown;
  analysis_job_id?: unknown;
  analysis_status?: unknown;
  analysis_job?: unknown;
  updated_at?: unknown;
}

type PlatformLabel = MonitoredAccount["platform"];

const platformLabels: Record<string, PlatformLabel> = {
  dy: "抖音",
  douyin: "抖音",
  xhs: "小红书",
  xiaohongshu: "小红书",
  ks: "快手",
  kuaishou: "快手"
};

export async function fetchMonitoredAccounts(): Promise<MonitoredAccount[]> {
  const payload = await apiRequest<{ items?: ApiMonitoredUser[] }>("/api/monitored-users");
  const items = payload.items ?? [];
  const accounts = items.map((item) => mapMonitoredAccount(item, undefined, []));
  if (accounts[0]?.sourceJobId) {
    accounts[0] = await fetchMonitoredAccountDetail(accounts[0]);
  }
  return accounts;
}

export async function fetchMonitoredAccountDetail(account: MonitoredAccount): Promise<MonitoredAccount> {
  if (!account.sourceJobId) return { ...account, riskDataLoaded: true };
  const [sourceJob, response] = await Promise.all([
    fetchJob(account.sourceJobId),
    fetchAuditResults({ jobId: account.sourceJobId, limit: 1000, sort: "latest", compact: true })
  ]);
  const auditResults = matchAccountResults(account, response.items ?? []);
  const recentResults = auditResults.filter((result) => isWithinDays(getResultTime(result), 30));
  const riskOutputs = recentResults
    .filter(isRiskAuditResult)
    .sort((left, right) => Date.parse(getResultTime(right)) - Date.parse(getResultTime(left)))
    .map((result) => mapRiskOutput(result, account.platform));
  const taskState = mapTaskState(sourceJob.status);
  const monitoringPlan = mapMonitoringPlan(sourceJob);
  const latestUpdatedAt = latestTimestamp([
    account.updatedAt,
    toText(sourceJob.updated_at),
    ...account.relatedAccounts.map((relation) => relation.updatedAt)
  ]);

  return {
    ...account,
    riskDataLoaded: true,
    updatedAt: latestUpdatedAt,
    status: mapAccountStatus(taskState.status),
    monitorScope: monitoringPlan.scope,
    metrics: {
      ...account.metrics,
      recentRiskCount: riskOutputs.length,
      highRiskCount: riskOutputs.filter((risk) => risk.level === "high").length,
      mediumRiskCount: riskOutputs.filter((risk) => risk.level === "medium").length,
      pendingReviewCount: recentResults.filter(isPendingReview).length,
      lastSyncTime: formatDateTime(latestUpdatedAt),
      lastSyncAgo: formatRelativeTime(latestUpdatedAt),
      currentTaskName: sourceJob.display_name || sourceJob.creator_nickname || sourceJob.id,
      currentTaskStatus: taskState.status,
      currentTaskStatusLabel: taskState.label
    },
    latestRisk: riskOutputs[0] ?? null,
    riskOutputs,
    monitoringPlan,
    timeline: buildTimeline(sourceJob, auditResults, account.relatedAccounts)
  };
}

function mapMonitoredAccount(item: ApiMonitoredUser, sourceJob: RawJob | undefined, auditResults: AuditResult[]): MonitoredAccount {
  const author = asRecord(item.author);
  const relatedAccounts = toRecordArray(item.suspected_related_accounts).map(mapRelatedAccount);
  const taskState = mapTaskState(sourceJob?.status ?? "linked");
  const sourceJobId = toText(item.source_job_id);
  const sourceAuditResultId = toPositiveNumber(item.source_audit_result_id);
  const platform = mapPlatform(item.platform);
  const name = toText(item.display_name, author.nickname, author.user_unique_id, "未知账号");
  const platformAccountId = toText(
    author.user_unique_id,
    author.short_user_id,
    author.user_id,
    item.raw_identity,
    item.stable_key,
    item.id
  );
  const status = mapAccountStatus(taskState.status);
  const riskOutputs = auditResults
    .filter((result) => isRiskAuditResult(result) && isWithinDays(getResultTime(result), 30))
    .sort((left, right) => Date.parse(getResultTime(right)) - Date.parse(getResultTime(left)))
    .map((result) => mapRiskOutput(result, platform));
  const latestUpdatedAt = latestTimestamp([
    toText(item.updated_at),
    toText(sourceJob?.updated_at),
    ...relatedAccounts.map((relation) => relation.updatedAt)
  ]);
  const monitoringPlan = mapMonitoringPlan(sourceJob);

  return {
    id: toText(item.id, item.stable_key, platformAccountId),
    sourceJobId,
    sourceAuditResultId,
    riskDataLoaded: !sourceJobId,
    updatedAt: latestUpdatedAt,
    name,
    platform,
    platformAccountId,
    avatarUrl: toText(author.avatar, author.avatar_url),
    monitorScope: monitoringPlan.scope,
    status,
    metrics: {
      recentRiskCount: riskOutputs.length,
      riskRangeLabel: "近 30 天",
      highRiskCount: riskOutputs.filter((risk) => risk.level === "high").length,
      mediumRiskCount: riskOutputs.filter((risk) => risk.level === "medium").length,
      pendingReviewCount: auditResults.filter(isPendingReview).length,
      relatedAccountCount: relatedAccounts.length,
      lastSyncTime: formatDateTime(latestUpdatedAt),
      lastSyncAgo: formatRelativeTime(latestUpdatedAt),
      currentTaskName: sourceJob?.display_name || sourceJob?.creator_nickname || sourceJobId || "未关联来源任务",
      currentTaskStatus: taskState.status,
      currentTaskStatusLabel: taskState.label
    },
    latestRisk: riskOutputs[0] ?? null,
    riskOutputs,
    relatedAccounts,
    monitoringPlan,
    timeline: buildTimeline(sourceJob, auditResults, relatedAccounts)
  };
}

function matchAccountResults(account: MonitoredAccount, results: AuditResult[]): AuditResult[] {
  const identities = new Set([
    account.platformAccountId,
    account.name
  ].map((value) => toText(value).toLowerCase()).filter(Boolean));

  return results.filter((result) => {
    const resultId = toPositiveNumber(result.audit_result_id ?? result.id);
    if (account.sourceAuditResultId && resultId === account.sourceAuditResultId) return true;
    const resultAuthor = result.author ?? {};
    return [
      result.author_key,
      resultAuthor.sec_uid,
      resultAuthor.user_id,
      resultAuthor.user_unique_id,
      resultAuthor.short_user_id,
      resultAuthor.nickname
    ].some((value) => identities.has(toText(value).toLowerCase()));
  });
}

function mapRiskOutput(result: AuditResult, platform: PlatformLabel): RiskOutput {
  const id = toText(result.audit_result_id, result.id, result.content_key, result.note_id);
  const jobId = toText(result.job_id);
  const hitRules = uniqueTexts([
    ...(result.score_breakdown ?? []).map((item) => toText(item.rule, item.label, item.rule_name)),
    ...(result.categories ?? []),
    result.primary_risk
  ]).slice(0, 4);

  return {
    id,
    jobId,
    title: toText(result.content_title, result.title_zh, result.title, result.desc_zh, result.desc, "未命名风险内容"),
    level: mapRiskLevel(result.risk_level, result.decision),
    riskScore: Number.isFinite(Number(result.risk_score)) ? Number(result.risk_score) : 0,
    summary: toText(result.summary, result.risk_basis, result.desc_zh, result.desc, "该内容命中风险审核规则"),
    sourceType: `${platform}${result.duration_seconds || result.video_results?.length ? "视频" : result.image_analyses?.length ? "图文" : "内容"}`,
    discoveredAt: formatDateTime(getResultTime(result)),
    hitRules,
    coverUrl: resolveCoverUrl(result)
  };
}

function resolveCoverUrl(result: AuditResult): string | undefined {
  const candidate = result.thumbnail_asset ?? result.image_analyses?.[0] ?? result.evidence_index?.image_units?.[0];
  if (!candidate) return undefined;
  const remoteUrl = toText(candidate.url);
  if (remoteUrl.startsWith("http://") || remoteUrl.startsWith("https://")) return remoteUrl;
  const jobId = toText(result.job_id);
  const path = normalizeAssetPath(toText(candidate.asset_rel, candidate.local_path), jobId);
  return path && jobId
    ? `/api/jobs/${encodeURIComponent(jobId)}/assets?path=${encodeURIComponent(path)}`
    : undefined;
}

function normalizeAssetPath(value: string, jobId: string): string {
  if (!value) return "";
  const marker = `/outputs/${jobId}/`;
  return value.includes(marker) ? value.split(marker, 2)[1] : value.replace(/^\/+/, "");
}

function mapMonitoringPlan(job: RawJob | undefined): MonitoredAccount["monitoringPlan"] {
  const revision = job?.current_audit_config_revision;
  const config = revision?.audit_config;
  const libraryIds = config?.library_ids ?? revision?.library_ids ?? job?.library_ids ?? [];
  const capabilities = config?.capabilities ?? revision?.capabilities ?? job?.capabilities ?? [];
  const scoringRules = config?.scoring_rules ?? [];
  const taskState = mapTaskState(job?.status ?? "linked");
  const ingestedCount = Number(job?.task_stats?.ingested_count ?? job?.task_stats?.batch_item_count ?? 0);
  const analyzedCount = Number(job?.task_stats?.completed_analysis_count ?? 0);
  const planName = [revision?.source_policy_name, revision?.source_policy_version].filter(Boolean).join(" ");

  return {
    lexiconLibraries: libraryIds.length ? libraryIds.map(formatLibraryName) : ["未绑定词库"],
    frequency: job ? `单次采集（${ingestedCount} 条）` : "未配置",
    strategy: capabilities.length ? capabilities.map(formatCapability).join(" + ") : "未配置",
    referencePlan: planName || revision?.id || "未引用审核方案",
    taskStatus: taskState.label,
    taskStatusTone: taskState.status === "completed" ? "blue" : taskState.status === "running" ? "green" : "orange",
    scope: buildMonitorScope(job, capabilities),
    riskTypes: uniqueTexts(scoringRules.map((rule) => toText(rule.category, rule.library_label))).length
      ? uniqueTexts(scoringRules.map((rule) => toText(rule.category, rule.library_label)))
      : libraryIds.map(formatLibraryName),
    autoBackfillRule: job ? `已分析 ${analyzedCount} / 已采集 ${ingestedCount} 条` : "未关联来源任务"
  };
}

function buildMonitorScope(job: RawJob | undefined, capabilities: string[]): string[] {
  if (!job) return ["未配置"];
  const scope = [job.crawl_mode === "creator" ? "账号主页" : "平台内容", "发布内容"];
  if (capabilities.includes("comment")) scope.push("评论区");
  return scope;
}

function buildTimeline(job: RawJob | undefined, results: AuditResult[], relatedAccounts: RelatedAccount[]): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  results.filter(isRiskAuditResult).forEach((result) => {
    const timestamp = getResultTime(result);
    const level = mapRiskLevel(result.risk_level, result.decision);
    events.push({
      id: `risk-${toText(result.audit_result_id, result.id, result.content_key)}`,
      timestamp,
      time: formatDateTime(timestamp),
      title: `发现${riskLevelLabel(level)}内容 · ${toText(result.content_title, result.title, "未命名内容")}`,
      description: toText(result.summary, result.risk_basis, result.desc, "命中风险审核规则"),
      type: "risk"
    });
  });
  (job?.logs ?? [])
    .filter((log) => /生成任务审核配置|开始任务|ingestion 完成|crawler output loaded|任务完成/.test(toText(log.message)))
    .forEach((log, index) => {
      const timestamp = toText(log.time, job?.updated_at, job?.created_at);
      events.push({
        id: `job-${index}-${timestamp}`,
        timestamp,
        time: formatDateTime(timestamp),
        title: "来源任务事件",
        description: toText(log.message),
        type: /配置/.test(toText(log.message)) ? "plan" : /分析|完成/.test(toText(log.message)) ? "analysis" : "crawl"
      });
    });
  relatedAccounts.forEach((relation) => {
    events.push({
      id: `relation-${relation.id}`,
      timestamp: relation.updatedAt,
      time: relation.updatedAtLabel,
      title: `关联评论用户 · ${relation.name}`,
      description: relation.sourceCommentText || "该评论无文本内容",
      type: "relation"
    });
  });
  return events
    .filter((event) => event.timestamp)
    .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
}

function mapRiskLevel(level: unknown, decision: unknown): RiskLevel {
  const normalized = toText(level).toLowerCase();
  if (["high", "高", "高危"].includes(normalized)) return "high";
  if (["medium", "mid", "中", "中危"].includes(normalized)) return "medium";
  if (["review", "pending", "待复核"].includes(normalized) || toText(decision).toLowerCase() === "review") return "review";
  return "low";
}

function isPendingReview(result: AuditResult): boolean {
  return toText(result.decision).toLowerCase() === "review" || ["review", "pending"].includes(toText(result.risk_level).toLowerCase());
}

function riskLevelLabel(level: RiskLevel): string {
  return { high: "高危", medium: "中危", review: "待复核", low: "低危" }[level];
}

function formatLibraryName(id: string): string {
  return ({ gambling: "赌博博彩" } as Record<string, string>)[id] ?? id;
}

function formatCapability(value: string): string {
  return ({ text: "文本", ocr: "OCR", asr: "ASR", vision: "视觉", comment: "评论" } as Record<string, string>)[value] ?? value;
}

function mapRelatedAccount(raw: Record<string, unknown>): RelatedAccount {
  const item = raw as ApiRelatedAccount;
  const analysisJob = asRecord(item.analysis_job);
  const updatedAt = toText(item.updated_at);
  const sourceAuditResultId = Number(item.source_audit_result_id);

  return {
    id: toText(item.id, item.raw_identity, item.display_name),
    name: toText(item.display_name, item.raw_identity, "未知评论用户"),
    platform: mapPlatform(item.platform),
    profileUrl: toText(item.profile_url),
    sourceCommentId: toText(item.source_comment_id),
    sourceCommentText: toText(item.source_comment_text),
    sourceRiskContent: toText(item.source_risk_content),
    sourceAuditResultId: Number.isFinite(sourceAuditResultId) && sourceAuditResultId > 0 ? sourceAuditResultId : null,
    analysisJobId: toText(item.analysis_job_id, analysisJob.id),
    analysisJobName: toText(analysisJob.display_name),
    analysisStatus: toText(analysisJob.status, item.analysis_status, "linked"),
    updatedAt,
    updatedAtLabel: formatDateTime(updatedAt)
  };
}

function mapPlatform(value: unknown): PlatformLabel {
  const key = toText(value).toLowerCase();
  return platformLabels[key] ?? "抖音";
}

function mapTaskState(status: string): {
  status: MonitoredAccount["metrics"]["currentTaskStatus"];
  label: string;
} {
  const normalized = status.toLowerCase();
  if (["completed", "done", "success"].includes(normalized)) {
    return { status: "completed", label: "分析完成" };
  }
  if (["running", "analyzing", "crawling", "queued", "pending"].includes(normalized)) {
    return { status: "running", label: normalized === "queued" || normalized === "pending" ? "排队中" : "运行中" };
  }
  if (["stopped", "failed", "error"].includes(normalized)) {
    return { status: "paused", label: normalized === "stopped" ? "已停止" : "任务异常" };
  }
  return { status: "paused", label: "已关联" };
}

function mapAccountStatus(status: MonitoredAccount["metrics"]["currentTaskStatus"]): AccountStatus {
  if (status === "completed") return "completed";
  if (status === "running") return "watching";
  return "continuous";
}

function isWithinDays(value: string, days: number): boolean {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && timestamp >= Date.now() - days * 24 * 60 * 60 * 1000;
}

function latestTimestamp(values: string[]): string {
  return values.filter(Boolean).sort((left, right) => Date.parse(right) - Date.parse(left))[0] ?? "";
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "--";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hours}:${minutes}`;
}

function formatRelativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!value || Number.isNaN(timestamp)) return "暂无同步时间";
  const diff = Math.max(0, Date.now() - timestamp);
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return formatDateTime(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function toRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord) : [];
}

function toText(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function toPositiveNumber(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function uniqueTexts(values: unknown[]): string[] {
  return [...new Set(values.map((value) => toText(value)).filter(Boolean))];
}
