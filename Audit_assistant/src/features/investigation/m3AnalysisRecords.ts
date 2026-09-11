import {
  fetchReportAppendix,
  fetchReportPostDetail,
  fetchReportPresentation
} from "../../services/reports";
import type { InvestigationRunProjection } from "../../types/investigationCreation";
import type {
  ReportEvidencePresentation,
  ReportPostDetail,
  ReportPostPresentation
} from "../../types/reports";
import type {
  AnalysisEvidenceType,
  AnalysisKeyEvidence,
  AnalysisRecord,
  AnalysisRisk
} from "./analysisRecords";

export interface M3AnalysisRecordsResult {
  records: AnalysisRecord[];
  completedCount: number;
  totalCount: number;
}

export async function loadM3AnalysisRecords(
  run: InvestigationRunProjection
): Promise<M3AnalysisRecordsResult> {
  if (run.status !== "PUBLISHED" && (run.status === "AUDIT_COMPLETED" || run.audit_results.length > 0)) {
    const records = run.audit_results.map((item, index) => {
      const risk = normalizeRisk(item.risk_level, item.decision);
      const evidence = item.evidence.map((value) => ({
        id: value.evidence_id,
        type: normalizeEvidenceType(value.evidence_type) || "text",
        content: value.content,
        translation: value.translation || undefined,
        explanation: value.explanation
      }));
      return {
        catalogVersion: 1,
        source: "m3-job" as const,
        itemNumber: index + 1,
        risk,
        riskLabel: riskLabel(risk),
        decisionLabel: decisionLabel(item.decision),
        summary: item.summary || "审核结果未提供摘要",
        conclusion: item.summary || "审核结果未提供摘要",
        contentTitle: item.content_title || "未命名内容",
        author: item.author_display_name || "未知作者",
        platform: platformLabel(item.platform),
        analyzedAt: formatAnalyzedAt(item.analyzed_at || run.completed_at || run.updated_at),
        evidenceCounts: countEvidence(evidence),
        keyEvidence: evidence,
        taskId: run.job_id,
        outputId: item.audit_result_id
      };
    }).reverse();
    return {
      records,
      completedCount: records.length,
      totalCount: readRunTotal(run)
    };
  }
  if (run.status !== "PUBLISHED" || !run.report_version_id) {
    return {
      records: [],
      completedCount: readRunCount(run, "completed_analysis_count"),
      totalCount: readRunTotal(run)
    };
  }

  const [presentation, page] = await Promise.all([
    fetchReportPresentation(run.report_version_id),
    fetchReportAppendix(run.report_version_id, { view: "posts", limit: 100 })
  ]);
  if (page.item_kind !== "post") {
    throw new Error("报告未返回可用的逐条分析记录");
  }

  const posts = page.items as ReportPostPresentation[];
  const details = await Promise.all(posts.map((post) => (
    fetchReportPostDetail(run.report_version_id, post.post_ref)
  )));
  const platform = presentation.report_metadata.platform.status === "available"
    ? presentation.report_metadata.platform.label || presentation.report_metadata.platform.code || "未知平台"
    : "未知平台";
  const analyzedAt = formatAnalyzedAt(run.completed_at || run.updated_at);
  const records = details.map((post, index) => mapM3PostToAnalysisRecord({
    post,
    itemNumber: index + 1,
    platform,
    analyzedAt,
    reportVersionId: run.report_version_id
  })).reverse();

  return {
    records,
    completedCount: records.length,
    totalCount: page.matched_count
  };
}

export function mapM3PostToAnalysisRecord(input: {
  post: ReportPostDetail;
  itemNumber: number;
  platform: string;
  analyzedAt: string;
  reportVersionId: string;
}): AnalysisRecord {
  const risk = normalizeRisk(input.post.risk_level, input.post.decision);
  const evidence = input.post.direct_evidence.map(mapReportEvidence).filter(Boolean) as AnalysisKeyEvidence[];
  return {
    catalogVersion: 1,
    source: "m3-report",
    itemNumber: input.itemNumber,
    risk,
    riskLabel: riskLabel(risk),
    decisionLabel: decisionLabel(input.post.decision),
    summary: input.post.content_summary || input.post.audit_summary || "报告未提供分析摘要",
    conclusion: input.post.audit_summary || input.post.content_summary || "报告未提供研判结论",
    contentTitle: input.post.title || "未命名内容",
    author: input.post.author_display_name || "未知作者",
    platform: input.platform,
    analyzedAt: input.analyzedAt,
    evidenceCounts: countEvidence(evidence),
    keyEvidence: evidence,
    reportVersionId: input.reportVersionId,
    postRef: input.post.post_ref,
    findingRef: input.post.investigation_finding_refs[0],
    taskId: input.post.audit_source?.task_id,
    outputId: input.post.audit_source?.output_id
  };
}

export function reportPostDetailPath(investigationId: string, record: AnalysisRecord) {
  if (!record.reportVersionId || !record.postRef) return "";
  const query = new URLSearchParams({ report: record.reportVersionId, view: "posts", post_ref: record.postRef });
  return `/investigation/${encodeURIComponent(investigationId)}/report/evidence?${query}`;
}

export function auditDetailPath(source?: { task_id: string; output_id: string } | null) {
  return source?.task_id && source.output_id
    ? `/tasks/${encodeURIComponent(source.task_id)}/outputs/${encodeURIComponent(source.output_id)}`
    : "";
}

export function mapReportEvidence(evidence: ReportEvidencePresentation): AnalysisKeyEvidence | null {
  const type = normalizeEvidenceType(evidence.evidence_type);
  if (!type) return null;
  return {
    id: evidence.evidence_ref,
    type,
    content: evidence.original_text,
    translation: evidence.translated_text && evidence.translated_text !== evidence.original_text
      ? evidence.translated_text
      : undefined,
    explanation: evidence.summary
  };
}

export function readRunAnalysisCounts(run: InvestigationRunProjection) {
  return {
    completedCount: readRunCount(run, "completed_analysis_count"),
    totalCount: readRunTotal(run)
  };
}

function readRunTotal(run: InvestigationRunProjection) {
  return readRunCount(run, "ingested_count");
}

function readRunCount(run: InvestigationRunProjection, key: string) {
  const value = run.task_stats[key];
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function countEvidence(evidence: AnalysisKeyEvidence[]): Record<AnalysisEvidenceType, number> {
  return evidence.reduce<Record<AnalysisEvidenceType, number>>((counts, item) => {
    counts[item.type] += 1;
    return counts;
  }, { text: 0, ocr: 0, asr: 0, comment: 0, vision: 0 });
}

function normalizeRisk(riskLevel: string, decision: string): AnalysisRisk {
  const normalized = `${riskLevel} ${decision}`.toLowerCase();
  if (normalized.includes("high") || normalized.includes("reject")) return "high";
  if (normalized.includes("medium")) return "medium";
  if (normalized.includes("low") || normalized.includes("review")) return "low";
  return "safe";
}

function riskLabel(risk: AnalysisRisk) {
  return ({ safe: "无风险", low: "低风险", medium: "中风险", high: "高风险" } as const)[risk];
}

function decisionLabel(decision: string) {
  return ({ pass: "通过", review: "需复核", reject: "拒绝" } as Record<string, string>)[decision.toLowerCase()] || "待确认";
}

function platformLabel(platform: string) {
  return ({ dy: "抖音", xhs: "小红书", ks: "快手" } as Record<string, string>)[platform.toLowerCase()] || platform || "未知平台";
}

function normalizeEvidenceType(value: string): AnalysisEvidenceType | null {
  const normalized = value.toLowerCase();
  if (normalized.includes("ocr") || normalized.includes("image_text")) return "ocr";
  if (normalized.includes("asr") || normalized.includes("audio")) return "asr";
  if (normalized.includes("comment")) return "comment";
  if (normalized.includes("vision") || normalized.includes("visual") || normalized.includes("frame")) return "vision";
  if (normalized.includes("text") || normalized.includes("title") || normalized.includes("content")) return "text";
  return null;
}

function formatAnalyzedAt(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value || "时间暂不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(timestamp).replace(/\//g, "-");
}

export function reportAuditDetailPath(record: Pick<AnalysisRecord, "taskId" | "outputId" | "reportVersionId" | "postRef">) {
  if (!record.taskId || !record.outputId || !record.reportVersionId || !record.postRef) return "";
  const query = new URLSearchParams({ report_version: record.reportVersionId, post_ref: record.postRef });
  return `${auditDetailPath({task_id: record.taskId, output_id: record.outputId})}?${query}`;
}
