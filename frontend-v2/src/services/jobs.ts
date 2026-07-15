import { apiRequest, withQuery } from "./apiClient";
import type {
  AuditResult,
  DeleteJobResponse,
  JobsSnapshot,
  JobTaskStats,
  MonitorTask,
  MonitorTaskSource,
  RawJob,
  TaskRunMetrics,
  TaskStatsSummary
} from "../types/jobs";

const riskLevelsWithoutRisk = new Set(["", "none", "safe", "pass", "unknown"]);

export async function fetchJobsSnapshot(): Promise<JobsSnapshot> {
  const [jobs, auditResultsPayload] = await Promise.all([
    fetchJobs(),
    fetchAuditResults({ limit: 1000, sort: "latest" })
  ]);
  const auditResults = auditResultsPayload.items || [];
  const tasks = mapJobsToMonitorTasks(jobs, auditResults);
  return {
    jobs,
    auditResults,
    tasks,
    stats: computeTaskStats(tasks, auditResults)
  };
}

export function fetchJobs() {
  return apiRequest<RawJob[]>("/api/jobs");
}

export function fetchAuditResults(query: { jobId?: string; limit?: number; sort?: string } = {}) {
  const path = query.jobId
    ? withQuery(`/api/jobs/${encodeURIComponent(query.jobId)}/audit-results`, {
        limit: query.limit ?? 1000,
        sort: query.sort ?? "latest"
      })
    : withQuery("/api/audit-results", {
        limit: query.limit ?? 1000,
        sort: query.sort ?? "latest"
      });
  return apiRequest<{ items: AuditResult[]; total: number; next_after_id: number }>(path);
}

export function controlJob(jobId: string, action: string) {
  return apiRequest<RawJob>(`/api/jobs/${encodeURIComponent(jobId)}/control`, {
    method: "POST",
    body: JSON.stringify({ action })
  });
}

export function deleteJob(jobId: string) {
  return apiRequest<DeleteJobResponse>(`/api/jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE"
  });
}

export function mapJobsToMonitorTasks(jobs: RawJob[], auditResults: AuditResult[]): MonitorTask[] {
  const outputsByJob = new Map<string, AuditResult[]>();
  auditResults.forEach((result) => {
    const jobId = String(result.job_id || "");
    if (!jobId) {
      return;
    }
    const list = outputsByJob.get(jobId) || [];
    list.push(result);
    outputsByJob.set(jobId, list);
  });

  return jobs.map((job) => {
    const outputs = (outputsByJob.get(job.id) || []).filter(isRiskAuditResult);
    const metrics = buildRunMetrics(job.task_stats || {}, outputs);
    const source = getTaskSource(job);
    const revision = job.current_audit_config_revision;
    const planName = [revision?.source_policy_name || "自定义审核配置", revision?.source_policy_version || ""]
      .join(" ")
      .trim();
    const statusMeta = getStatusMeta(job.status);

    return {
      id: job.id,
      name: job.display_name || job.keyword || job.input_filename || job.id,
      source,
      sourceLabel: getTaskSourceLabel(source),
      platformLabel: getPlatformLabel(job.platform, job.input_type),
      objectLabel: getObjectLabel(job),
      referencePlan: planName || "自定义审核配置",
      status: statusMeta.label,
      statusTone: statusMeta.tone,
      statusTimeLabel: buildStatusTimeLabel(statusMeta.verb, job.updated_at || job.created_at || ""),
      createdAt: job.created_at || "",
      updatedAt: job.updated_at || job.created_at || "",
      updatedDisplay: formatDateTime(job.updated_at || job.created_at || ""),
      metrics,
      logs: normalizeLogs(job.logs || [], job),
      recentOutputs: outputs.slice(0, 5),
      raw: job
    };
  });
}

export function computeTaskStats(tasks: MonitorTask[], auditResults: AuditResult[]): TaskStatsSummary {
  return {
    runningTasks: tasks.filter((task) => isRunningJobStatus(task.raw.status)).length,
    recentRiskCount: auditResults.filter((result) => isRiskAuditResult(result) && isWithinRecentDays(getResultTime(result), 7)).length,
    liveTaskCount: tasks.filter((task) => task.source === "直播接入").length,
    focusUserTaskCount: tasks.filter((task) => task.source === "重点用户").length,
    platformCrawlTaskCount: tasks.filter((task) => task.source === "平台抓取").length
  };
}

export function isRiskAuditResult(result: AuditResult) {
  const decision = String(result.decision || "").toLowerCase();
  const riskLevel = String(result.risk_level || "").toLowerCase();
  return decision !== "pass" && !riskLevelsWithoutRisk.has(riskLevel);
}

export function getResultTime(result: AuditResult) {
  return result.analyzed_at || result.updated_at || result.created_at || "";
}

export function formatDateTime(value: string) {
  if (!value) {
    return "刚刚";
  }
  return value.replace("T", " ").slice(5, 16);
}

export function getPlatformLabel(platform = "", inputType = "") {
  if (inputType === "local_video") {
    return "本地上传";
  }
  const normalized = platform.toLowerCase();
  if (normalized === "dy" || normalized === "douyin") {
    return "抖音";
  }
  if (normalized === "xhs" || normalized === "xiaohongshu") {
    return "小红书";
  }
  if (normalized === "ks" || normalized === "kuaishou") {
    return "快手";
  }
  return platform || "-";
}

function buildRunMetrics(stats: JobTaskStats, outputs: AuditResult[]): TaskRunMetrics {
  const crawled = Math.max(
    toNumber(stats.ingested_count),
    toNumber(stats.batch_processed_count),
    toNumber(stats.batch_item_count)
  );
  return {
    crawled,
    analyzed: toNumber(stats.completed_analysis_count),
    waiting: toNumber(stats.queued_analysis_count) + toNumber(stats.analyzing_count),
    failed: toNumber(stats.failed_analysis_count),
    outputs: outputs.length,
    highRisk: outputs.filter((result) => String(result.risk_level || "").toLowerCase() === "high").length
  };
}

function normalizeLogs(logs: Array<{ time?: string; message?: string }>, job: RawJob) {
  const items = logs
    .slice()
    .reverse()
    .filter((log) => log.message)
    .slice(0, 8);
  if (items.length) {
    return items;
  }
  return [{ time: job.updated_at || job.created_at || "", message: "任务已创建" }];
}

function getTaskSource(job: RawJob): MonitorTaskSource {
  if (job.input_type === "local_video") {
    return "本地视频";
  }
  const name = `${job.display_name || ""} ${job.keyword || ""}`.toLowerCase();
  if (name.includes("live") || name.includes("直播")) {
    return "直播接入";
  }
  if (job.crawl_mode === "creator") {
    return "重点用户";
  }
  return "平台抓取";
}

function getTaskSourceLabel(source: MonitorTaskSource) {
  if (source === "平台抓取") {
    return "平台内容抓取";
  }
  if (source === "重点用户") {
    return "重点用户监控";
  }
  if (source === "本地视频") {
    return "本地视频分析";
  }
  return "直播监控";
}

function getObjectLabel(job: RawJob) {
  if (job.input_type === "local_video") {
    return job.input_filename || job.display_name || "本地视频";
  }
  if (job.crawl_mode === "creator") {
    const value = job.creator_nickname || job.creator_id || job.creator_url || job.id;
    return `用户尾号 ${tailToken(value)}`;
  }
  const keyword = (job.lexicon_keywords && job.lexicon_keywords.length ? job.lexicon_keywords.join("、") : job.keyword) || job.id;
  return `关键词 ${compactText(keyword, 18)}`;
}

function tailToken(value: string) {
  const cleaned = String(value || "").replace(/\/$/, "");
  if (!cleaned) {
    return "-";
  }
  return cleaned.slice(-10);
}

function compactText(value: string, maxLength: number) {
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

function getStatusMeta(status: string): {
  label: MonitorTask["status"];
  tone: MonitorTask["statusTone"];
  verb: string;
} {
  if (status === "completed") {
    return { label: "已完成", tone: "success", verb: "完成于" };
  }
  if (status === "failed") {
    return { label: "失败", tone: "danger", verb: "失败于" };
  }
  if (status === "queued") {
    return { label: "排队中", tone: "info", verb: "排队于" };
  }
  if (["analysis_stopped", "analysis_paused", "crawl_paused"].includes(status)) {
    return { label: "已暂停", tone: "warning", verb: "暂停于" };
  }
  if (["stopped"].includes(status)) {
    return { label: "已停止", tone: "neutral", verb: "停止于" };
  }
  if (["interrupted"].includes(status)) {
    return { label: "已中断", tone: "neutral", verb: "中断于" };
  }
  if (["crawl_pausing", "analysis_stopping", "stopping"].includes(status)) {
    return { label: "停止中", tone: "warning", verb: "更新于" };
  }
  if (status === "analysis_running") {
    return { label: "运行中", tone: "success", verb: "开始于" };
  }
  return { label: "运行中", tone: "success", verb: "开始于" };
}

function buildStatusTimeLabel(verb: string, value: string) {
  return value ? `${verb} ${formatDateTime(value)}` : "";
}

function isRunningJobStatus(status: string) {
  return ["running", "analysis_running", "crawl_pausing", "analysis_stopping", "stopping"].includes(status);
}

function isWithinRecentDays(value: string, days: number) {
  if (!value) {
    return true;
  }
  const time = Date.parse(value);
  if (!Number.isFinite(time)) {
    return true;
  }
  return time >= Date.now() - days * 24 * 60 * 60 * 1000;
}

function toNumber(value: unknown) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number : 0;
}
