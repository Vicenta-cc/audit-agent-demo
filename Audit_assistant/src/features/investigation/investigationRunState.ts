import type { AgentExecutionPhase } from "../../types/investigation";
import type { InvestigationRunProjection } from "../../types/investigationCreation";

export interface InvestigationRunViewState {
  phase: AgentExecutionPhase;
  terminal: "published" | "failed" | "interrupted" | "paused" | "ended" | "completed" | null;
  label: string;
  step: 1 | 2 | 3 | 4;
  activity: "queued" | "running" | "stopped" | "completed";
}

export function mapInvestigationRunState(
  run: InvestigationRunProjection
): InvestigationRunViewState {
  if (run.available_actions?.ending) {
    return { phase: "audit_working", terminal: null, label: "正在结束，等待后台停止", step: inferStoppedStep(run), activity: "running" };
  }
  if (run.available_actions?.ended && run.error_code === "cancelled") {
    return { phase: "audit_working", terminal: "ended", label: "任务已结束", step: inferStoppedStep(run), activity: "stopped" };
  }
  const crawlActive = ["queued", "running", "pausing"].includes(run.crawl_status);
  const analysisActive = ["queued", "running", "pausing"].includes(run.analysis_status);
  if (run.status === "AUDIT_COMPLETED") {
    if (Number(run.task_stats?.failed_analysis_count) > 0 && Number(run.task_stats?.completed_analysis_count) === 0) {
      return { phase: "audit_completed", terminal: "completed", label: "处理结束，帖子审核失败，未生成报告", step: 3, activity: "completed" };
    }
    return { phase: "audit_completed", terminal: "completed", label: run.analysis_status === "partial" ? "处理结束，部分帖子审核失败" : "审核完成", step: 3, activity: "completed" };
  }
  if (run.status === "PUBLISHED") {
    return { phase: "completed", terminal: "published", label: "报告已发布", step: 4, activity: "completed" };
  }
  if (run.status === "FAILED") {
    return { phase: "audit_working", terminal: "failed", label: "调查失败", step: inferStoppedStep(run), activity: "stopped" };
  }
  if (run.status === "INTERRUPTED" && (crawlActive || analysisActive)) {
    return { phase: "audit_resumed", terminal: null, label: "正在从检查点恢复", step: inferStoppedStep(run), activity: "running" };
  }
  if (run.status === "INTERRUPTED") {
    return { phase: "audit_working", terminal: "interrupted", label: "调查已中断，可恢复", step: inferStoppedStep(run), activity: "stopped" };
  }
  if (["paused", "stopped"].includes(run.analysis_status) && crawlActive) {
    return { phase: "collection_working", terminal: null, label: "分析已暂停，采集继续", step: 1, activity: "running" };
  }
  if (["paused", "stopped", "interrupted"].includes(run.crawl_status) && analysisActive) {
    return { phase: "evidence_working", terminal: null, label: "采集已暂停，分析继续", step: 2, activity: "running" };
  }
  if (["paused", "stopped"].includes(run.analysis_status) || ["paused", "stopped"].includes(run.crawl_status)) {
    return { phase: "audit_working", terminal: "paused", label: "当前执行阶段已暂停，可恢复", step: inferStoppedStep(run), activity: "stopped" };
  }
  if (run.status === "REPORT_GENERATING") {
    return { phase: "report_generating", terminal: null, label: "正在归纳报告", step: 4, activity: "running" };
  }
  if (run.status === "QUEUED") {
    return { phase: "collection_waking", terminal: null, label: "已确认，等待任务开始", step: 1, activity: "queued" };
  }
  if (run.crawl_status === "completed" && run.analysis_status === "completed") {
    return { phase: "audit_completed", terminal: null, label: "风险研判已完成，等待报告归纳", step: 3, activity: "queued" };
  }
  if (run.analysis_status === "running") {
    return { phase: "evidence_working", terminal: null, label: "证据分析与风险研判中", step: 2, activity: "running" };
  }
  if (run.crawl_status === "completed") {
    return { phase: "evidence_handoff", terminal: null, label: "采集完成，等待分析", step: 2, activity: "queued" };
  }
  return { phase: "collection_working", terminal: null, label: "正在采集数据", step: 1, activity: "running" };
}

function inferStoppedStep(run: InvestigationRunProjection): 1 | 2 | 3 | 4 {
  if (run.report_status && !["pending", "not_started", "unknown"].includes(run.report_status)) return 4;
  if (["running", "paused", "completed", "failed", "stopped"].includes(run.analysis_status)) return 2;
  return 1;
}

// A pause stops polling only while no end request is being reconciled.
export function shouldPollInvestigationRun(run: InvestigationRunProjection): boolean {
  if (run.available_actions?.ending) return true;
  if (["AUDIT_COMPLETED", "PUBLISHED", "FAILED"].includes(run.status)) {
    return Boolean(run.available_actions?.end_task);
  }
  if (run.status === "INTERRUPTED") {
    return ["queued", "running", "pausing"].includes(run.crawl_status)
      || ["queued", "running", "pausing"].includes(run.analysis_status);
  }
  return true;
}
