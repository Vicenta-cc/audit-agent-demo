import { formatDateTime, getPlatformLabel, getResultTime } from "../../services/jobs";
import type { AuditResult, MonitorTask } from "../../types/jobs";
import type {
  ConfigHistoryItem,
  EvidenceCounts,
  EvidenceTypeFilter,
  RiskLevel,
  RiskLibrary,
  TaskConfig,
  TaskLog,
  TaskLogLevel,
  TaskOutputItem,
  TaskOutputSummary
} from "../../types/taskOutputs";

const highRiskValues = new Set(["high", "高", "高危"]);
const mediumRiskValues = new Set(["medium", "mid", "中", "中危"]);
const noRiskValues = new Set(["", "none", "safe", "pass", "unknown", "无", "无风险"]);

export function mapAuditResultToTaskOutput(output: AuditResult): TaskOutputItem {
  return {
    id: getOutputKey(output),
    number: getOutputNumber(output),
    riskLevel: getRiskLevel(output),
    title: getOutputTitle(output),
    summary: output.summary || output.desc || "",
    riskLibrary: getRiskLibrary(output),
    evidenceCounts: getEvidenceCounts(output),
    author: getAuthorName(output),
    time: formatOutputDate(output),
    timestamp: getResultTime(output),
    thumbnailUrl: getThumbnailUrl(output),
    durationSeconds: getDurationSeconds(output),
    sourceUrl: output.url || "",
    raw: output
  };
}

export function getOutputKey(output: AuditResult) {
  return String(output.audit_result_id || output.id || output.content_key || output.note_id || output.url || "unknown");
}

export function getOutputNumber(output: AuditResult) {
  const value = Number(output.audit_result_id || output.id || 0);
  return value ? `R-${String(value).padStart(3, "0")}` : `R-${getOutputKey(output).slice(-6)}`;
}

export function getOutputTitle(output: AuditResult) {
  return (
    output.content_title ||
    output.title ||
    output.desc ||
    output.summary ||
    output.note_id ||
    output.content_key ||
    "未命名内容"
  );
}

export function getAuthorName(output: AuditResult) {
  return (
    output.author?.nickname ||
    output.author?.user_unique_id ||
    output.author?.short_user_id ||
    output.author_key ||
    "未知作者"
  );
}

export function getRiskLevel(output: AuditResult): RiskLevel {
  const value = String(output.risk_level || "").toLowerCase();
  if (highRiskValues.has(value)) {
    return "高危";
  }
  if (mediumRiskValues.has(value)) {
    return "中危";
  }
  if (isNoRiskOutput(output)) {
    return "无风险";
  }
  return "待复核";
}

export function isNoRiskOutput(output: AuditResult) {
  const decision = String(output.decision || "").toLowerCase();
  const riskLevel = String(output.risk_level || "").toLowerCase();
  return decision === "pass" || noRiskValues.has(riskLevel);
}

export function getRiskLibrary(output: AuditResult): RiskLibrary | null {
  if (isNoRiskOutput(output)) {
    return null;
  }
  const evidence = (output.evidence_items || []).find(
    (item) => item.risk_library_id || item.risk_library_label
  );
  if (!evidence) {
    return null;
  }
  const id = String(evidence.risk_library_id || evidence.risk_library_label || "");
  const label = String(evidence.risk_library_label || evidence.risk_library_id || "");
  return id && label ? { id, label } : null;
}

export function getEvidenceCounts(output: AuditResult): EvidenceCounts {
  const evidenceIndex = output.evidence_index || {};
  const indexedText = evidenceIndex.text_context;
  const videoUnits = Array.isArray(evidenceIndex.video_units) ? evidenceIndex.video_units : [];

  const text = [
    output.title,
    output.content_title,
    output.desc,
    output.summary,
    indexedText?.title,
    indexedText?.desc
  ].some(Boolean)
    ? 1
    : 0;
  const timelineOcr = (output.timeline_frames || []).filter((item) => item.ocr_text || item.ocr_text_zh).length;
  const imageOcr = (output.image_analyses || []).filter((item) => item.ocr_text).length;
  const ocr = toArray(output.ocr_items).length + timelineOcr + imageOcr + toArray(evidenceIndex.ocr_items).length;
  const directAsr = toArray(output.asr_segments).length;
  const indexedAsr = toArray(evidenceIndex.asr_segments).length + toArray(evidenceIndex.audio_units).length;
  const videoAsr = (output.video_results || []).reduce((sum, item) => {
    const segments = item.transcript?.segments || [];
    return sum + (segments.length || (item.transcript?.text ? 1 : 0));
  }, 0);
  const indexedVisual = videoUnits.reduce(
    (sum, item) => sum + toNumber(item.timeline_frame_count) + toNumber(item.moment_count) + toNumber(item.precise_sheet_count),
    0
  );
  const videoVisual = (output.video_results || []).reduce(
    (sum, item) => sum + toArray(item.timeline_frames).length + toArray(item.moments).length + toArray(item.moment_sheets).length,
    0
  );
  const visual =
    toNumber(output.risk_image_count) +
    toNumber(output.risk_frame_count) +
    toArray(output.risk_images).length +
    toArray(output.risk_frames).length +
    toArray(output.image_analyses).length +
    toArray(evidenceIndex.image_units).length +
    indexedVisual +
    videoVisual;
  const comments = Math.max(
    toNumber(output.comment_count),
    toNumber(output.comments_count),
    toNumber(indexedText?.comments_count)
  );

  return { text, ocr, asr: directAsr + indexedAsr + videoAsr, visual, comments };
}

export function hasEvidenceType(item: TaskOutputItem, type: EvidenceTypeFilter) {
  if (type === "全部") {
    return true;
  }
  const keyMap: Record<Exclude<EvidenceTypeFilter, "全部">, keyof EvidenceCounts> = {
    文本: "text",
    OCR: "ocr",
    ASR: "asr",
    视觉: "visual",
    评论: "comments"
  };
  return item.evidenceCounts[keyMap[type]] > 0;
}

export function getOutputSearchText(item: TaskOutputItem) {
  return [item.number, item.title, item.summary, item.author, item.riskLibrary?.label]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function buildOutputSummary(outputs: TaskOutputItem[], total = outputs.length): TaskOutputSummary {
  return {
    total,
    highRisk: outputs.filter((item) => item.riskLevel === "高危").length,
    mediumRisk: outputs.filter((item) => item.riskLevel === "中危").length,
    pendingReview: outputs.filter((item) => item.riskLevel === "待复核").length,
    noRisk: outputs.filter((item) => item.riskLevel === "无风险").length
  };
}

export function getRiskLibraries(outputs: TaskOutputItem[]) {
  const unique = new Map<string, RiskLibrary>();
  outputs.forEach((item) => {
    if (item.riskLibrary) {
      unique.set(item.riskLibrary.id, item.riskLibrary);
    }
  });
  return [...unique.values()];
}

export function buildTaskConfig(task: MonitorTask, riskLibraries: RiskLibrary[]): TaskConfig {
  const revision = task.raw.current_audit_config_revision;
  const capabilities = revision?.audit_config?.capabilities || task.raw.capabilities || [];
  const keywordValues = task.raw.lexicon_keywords?.length
    ? task.raw.lexicon_keywords
    : String(task.raw.keyword || "").split(",").map((item) => item.trim()).filter(Boolean);
  const configuredLibraryIds = revision?.audit_config?.library_ids || task.raw.library_ids || [];
  const resolvedLibraries = configuredLibraryIds.map((id) => {
    return riskLibraries.find((item) => item.id === id) || { id, label: id };
  });

  return {
    referencePlan: task.referencePlan,
    configVersion: getPlanVersion(task),
    updatedAt: task.updatedDisplay,
    effectiveAt: revision?.effective_from ? formatDateTime(revision.effective_from) : "--",
    platform: task.platformLabel,
    collectionTimeRange: "接口未提供",
    keywords: keywordValues,
    dataType: task.raw.input_type || "接口未提供",
    riskLibraries: resolvedLibraries,
    analysisScopes: [
      { label: "文本分析", enabled: hasCapability(capabilities, ["text", "semantic", "文本"]) },
      { label: "OCR", enabled: hasCapability(capabilities, ["ocr"]) },
      { label: "ASR", enabled: hasCapability(capabilities, ["asr", "audio", "语音"]) },
      { label: "视频帧分析", enabled: hasCapability(capabilities, ["vision", "video", "视觉"]) },
      { label: "评论分析", enabled: hasCapability(capabilities, ["comment", "评论"]) }
    ]
  };
}

export function buildConfigHistory(task: MonitorTask): ConfigHistoryItem[] {
  void task;
  return [];
}

export function buildTaskLogs(task: MonitorTask): TaskLog[] {
  return task.logs.map((log, index) => {
    const timestamp = log.time || "";
    return {
      id: `${timestamp || "log"}-${index}`,
      timestamp,
      time: formatLogTime(timestamp),
      date: timestamp ? timestamp.slice(0, 10) : "",
      level: inferLogLevel(log.message || ""),
      content: log.message || "任务状态更新"
    };
  });
}

export function getLatestRunInfo(task: MonitorTask) {
  const logs = buildTaskLogs(task);
  const latest = logs[0];
  const hasError = latest?.level === "ERROR" || task.status === "失败";
  return {
    time: formatFullDateTime(latest?.timestamp || task.updatedAt),
    status: hasError ? "运行异常" : "运行正常",
    tone: hasError ? "danger" : "success"
  } as const;
}

function formatFullDateTime(value: string) {
  if (!value) return "--";
  return value.replace("T", " ").slice(0, 16);
}

export function getPlanVersion(task: MonitorTask) {
  const revision = task.raw.current_audit_config_revision;
  return String(revision?.source_policy_version || revision?.version || "--");
}

export function getCollectionStatusLabel(task: MonitorTask) {
  const status = String(task.raw.crawl_status || task.raw.status || "").toLowerCase();
  if (status.includes("pause") || status === "stopped" || task.status === "已暂停") {
    return "采集已暂停";
  }
  if (status.includes("fail") || task.status === "失败") {
    return "采集异常";
  }
  if (status.includes("complete") || task.status === "已完成") {
    return "采集已完成";
  }
  return "采集中";
}

export function getCollectionStatusTone(task: MonitorTask) {
  const label = getCollectionStatusLabel(task);
  if (label === "采集中") return "success" as const;
  if (label === "采集异常") return "danger" as const;
  if (label === "采集已暂停") return "warning" as const;
  return "neutral" as const;
}

export function formatTimeOnly(value: string) {
  if (!value) return "--:--";
  const normalized = value.replace("T", " ");
  return normalized.length >= 16 ? normalized.slice(11, 16) : formatDateTime(value).slice(-5);
}

export function formatDuration(seconds: number) {
  const value = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(value / 60);
  return `${String(minutes).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function formatOutputDate(output: AuditResult) {
  return formatDateTime(getResultTime(output));
}

function getThumbnailUrl(output: AuditResult) {
  const candidates = [
    output.image_analyses?.[0],
    output.evidence_index?.image_units?.[0],
    output.evidence_index?.video_units?.[0],
    output.video_results?.[0]
  ];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const remoteUrl = String(candidate.url || "");
    if (remoteUrl.startsWith("http://") || remoteUrl.startsWith("https://")) {
      return remoteUrl;
    }
    const path = normalizeAssetPath(String(candidate.asset_rel || candidate.local_path || ""), String(output.job_id || ""));
    if (path && output.job_id) {
      return `/api/jobs/${encodeURIComponent(output.job_id)}/assets?path=${encodeURIComponent(path)}`;
    }
  }
  return "";
}

function normalizeAssetPath(value: string, jobId: string) {
  if (!value) return "";
  const marker = `/outputs/${jobId}/`;
  return value.includes(marker) ? value.split(marker, 2)[1] : value.replace(/^\/+/, "");
}

function getDurationSeconds(output: AuditResult) {
  const value = output.video_results?.[0]?.duration || output.evidence_index?.video_units?.[0]?.duration;
  const duration = Number(value || 0);
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

function inferLogLevel(message: string): TaskLogLevel {
  const lower = message.toLowerCase();
  if (lower.includes("error") || message.includes("失败") || message.includes("异常") || message.includes("超时")) {
    return "ERROR";
  }
  if (lower.includes("warn") || message.includes("重试") || message.includes("较慢")) {
    return "WARN";
  }
  return "INFO";
}

function formatLogTime(value: string) {
  if (!value) return "--:--:--";
  const normalized = value.replace("T", " ");
  return normalized.length >= 19 ? normalized.slice(11, 19) : formatTimeOnly(value);
}

function hasCapability(capabilities: string[], keywords: string[]) {
  if (!capabilities.length) return false;
  const text = capabilities.join(" ").toLowerCase();
  return keywords.some((keyword) => text.includes(keyword.toLowerCase()));
}

function toArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function toNumber(value: unknown) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number : 0;
}
