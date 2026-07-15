import { formatDateTime, getPlatformLabel, getResultTime } from "../../services/jobs";
import type { AuditResult, MonitorTask } from "../../types/jobs";

export type TaskDrawerType = "logs" | "config" | null;
export type OutputRiskFilter = "全部" | "高危" | "中危" | "低危" | "无风险";
export type OutputReviewFilter = "全部" | "待复核" | "已复核" | "无风险";
export type OutputSourceFilter = "全部" | "小红书" | "抖音" | "快手" | "本地上传";
export type OutputSortKey = "default" | "latest" | "risk" | "confidence";

export interface OutputSummary {
  total: number;
  highRisk: number;
  pendingReview: number;
  noRisk: number;
  evidenceable: number;
}

export interface EvidenceCounts {
  text: number;
  ocr: number;
  asr: number;
  visual: number;
  comments: number;
}

const highRiskValues = new Set(["high", "高", "高危"]);
const mediumRiskValues = new Set(["medium", "mid", "中", "中危"]);
const lowRiskValues = new Set(["low", "低", "低危"]);
const noRiskValues = new Set(["", "none", "safe", "pass", "unknown", "无", "无风险"]);

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

export function getPlatformName(output: AuditResult, task?: MonitorTask) {
  return getPlatformLabel(output.platform || task?.raw.platform || "", task?.raw.input_type || "");
}

export function getRiskLevelLabel(output: AuditResult): Exclude<OutputRiskFilter, "全部"> | "待研判" {
  const value = String(output.risk_level || "").toLowerCase();
  if (highRiskValues.has(value)) {
    return "高危";
  }
  if (mediumRiskValues.has(value)) {
    return "中危";
  }
  if (lowRiskValues.has(value)) {
    return "低危";
  }
  if (isNoRiskOutput(output)) {
    return "无风险";
  }
  return "待研判";
}

export function isNoRiskOutput(output: AuditResult) {
  const decision = String(output.decision || "").toLowerCase();
  const riskLevel = String(output.risk_level || "").toLowerCase();
  return decision === "pass" || noRiskValues.has(riskLevel);
}

export function getAuditStatusLabel(output: AuditResult): Exclude<OutputReviewFilter, "全部"> {
  const status = String(output.review_status || "").toLowerCase();
  if (isNoRiskOutput(output)) {
    return "无风险";
  }
  if (status) {
    return "已复核";
  }
  return "待复核";
}

export function getRiskTypeLabel(output: AuditResult) {
  if (isNoRiskOutput(output)) {
    return "无风险";
  }
  const category = output.categories?.find((item) => Boolean(String(item || "").trim()));
  if (category) {
    return compactLabel(String(category));
  }
  const primaryRisk = String(output.primary_risk || "").trim();
  if (primaryRisk) {
    return compactLabel(primaryRisk);
  }
  return getRiskLevelLabel(output);
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
  const indexedOcr = toArray(evidenceIndex.ocr_items).length;
  const ocr = toArray(output.ocr_items).length + timelineOcr + imageOcr + indexedOcr;

  const directAsr = toArray(output.asr_segments).length;
  const indexedAsr = toArray(evidenceIndex.asr_segments).length + toArray(evidenceIndex.audio_units).length;
  const videoAsr = (output.video_results || []).reduce((sum, item) => {
    const segments = item.transcript?.segments || [];
    return sum + (segments.length || (item.transcript?.text ? 1 : 0));
  }, 0);
  const asr = directAsr + indexedAsr + videoAsr;

  const indexedVideoVisual = videoUnits.reduce(
    (sum, unit) => sum + toNumber(unit.timeline_frame_count) + toNumber(unit.moment_count) + toNumber(unit.precise_sheet_count),
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
    indexedVideoVisual +
    videoVisual;

  const comments = Math.max(
    toNumber(output.comment_count),
    toNumber(output.comments_count),
    toNumber(indexedText?.comments_count)
  );

  return { text, ocr, asr, visual, comments };
}

export function getConfidence(output: AuditResult) {
  const explicit = toNumber((output as { confidence?: number }).confidence);
  if (explicit > 0) {
    return normalizePercent(explicit);
  }
  const score = toNumber(output.risk_score);
  if (score > 0) {
    return normalizePercent(score);
  }
  if (getRiskLevelLabel(output) === "高危") {
    return 82;
  }
  if (getRiskLevelLabel(output) === "中危") {
    return 64;
  }
  if (getRiskLevelLabel(output) === "低危") {
    return 42;
  }
  return 15;
}

export function getOutputSearchText(output: AuditResult) {
  const evidenceParts = [
    output.primary_risk,
    ...(output.categories || []),
    ...toArray(output.evidence).map(String),
    ...toArray(output.risk_evidence).map(String)
  ];
  return [
    getOutputNumber(output),
    getOutputTitle(output),
    output.summary,
    getAuthorName(output),
    output.note_id,
    output.content_key,
    output.url,
    ...evidenceParts
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function buildOutputSummary(outputs: AuditResult[]): OutputSummary {
  return {
    total: outputs.length,
    highRisk: outputs.filter((item) => getRiskLevelLabel(item) === "高危").length,
    pendingReview: outputs.filter((item) => getAuditStatusLabel(item) === "待复核").length,
    noRisk: outputs.filter(isNoRiskOutput).length,
    evidenceable: outputs.filter((item) => Boolean(item.audit_result_id || item.id)).length
  };
}

export function formatOutputDate(output: AuditResult) {
  return formatDateTime(getResultTime(output));
}

export function formatTimeOnly(value: string) {
  if (!value) {
    return "--:--";
  }
  const normalized = value.replace("T", " ");
  return normalized.length >= 16 ? normalized.slice(11, 16) : formatDateTime(value).slice(-5);
}

export function getRevisionId(task: MonitorTask) {
  return (
    task.raw.current_audit_config_revision?.id ||
    task.raw.current_audit_config_revision_id ||
    task.raw.prompt_profile_snapshot?.prompt_version ||
    "current"
  );
}

export function getPlanVersion(task: MonitorTask) {
  const revision = task.raw.current_audit_config_revision;
  return String(revision?.source_policy_version || revision?.version || "v1.0");
}

function compactLabel(value: string) {
  const cleaned = value.replace(/[，。,.\s].*$/, "").trim();
  return cleaned.length > 8 ? `${cleaned.slice(0, 8)}...` : cleaned || "待研判";
}

function normalizePercent(value: number) {
  const percent = value <= 1 ? value * 100 : value;
  return Math.max(0, Math.min(99, Math.round(percent)));
}

function toArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function toNumber(value: unknown) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number : 0;
}
