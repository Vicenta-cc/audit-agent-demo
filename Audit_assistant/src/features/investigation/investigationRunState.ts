import type { AgentExecutionPhase } from "../../types/investigation";
import type { InvestigationRunProjection } from "../../types/investigationCreation";

export interface InvestigationRunViewState {
  phase: AgentExecutionPhase;
  terminal: "published" | "failed" | "interrupted" | "paused" | null;
  label: string;
  step: 1 | 2 | 3 | 4;
  activity: "queued" | "running" | "stopped" | "completed";
}

export function mapInvestigationRunState(
  run: InvestigationRunProjection
): InvestigationRunViewState {
  if (run.status === "AUDIT_COMPLETED") {
    return { phase: "audit_completed", terminal: null, label: "审核完成", step: 3, activity: "completed" };
  }
  if (run.status === "PUBLISHED") {
    return { phase: "completed", terminal: "published", label: "报告已发布", step: 4, activity: "completed" };
  }
  if (run.status === "FAILED") {
    return { phase: "audit_working", terminal: "failed", label: "调查失败", step: inferStoppedStep(run), activity: "stopped" };
  }
  if (run.status === "INTERRUPTED") {
    return { phase: "audit_working", terminal: "interrupted", label: "调查已中断，可恢复", step: inferStoppedStep(run), activity: "stopped" };
  }
  if (run.analysis_status === "paused" || run.crawl_status === "paused") {
    return { phase: "audit_working", terminal: "paused", label: "调查已暂停，可恢复", step: inferStoppedStep(run), activity: "stopped" };
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
