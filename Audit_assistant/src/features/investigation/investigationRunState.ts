import type { AgentExecutionPhase } from "../../types/investigation";
import type { InvestigationRunProjection } from "../../types/investigationCreation";

export interface InvestigationRunViewState {
  phase: AgentExecutionPhase;
  terminal: "published" | "failed" | "interrupted" | null;
  label: string;
}

export function mapInvestigationRunState(
  run: InvestigationRunProjection
): InvestigationRunViewState {
  if (run.status === "PUBLISHED") {
    return { phase: "completed", terminal: "published", label: "报告已发布" };
  }
  if (run.status === "FAILED") {
    return { phase: "audit_working", terminal: "failed", label: "调查失败" };
  }
  if (run.status === "INTERRUPTED") {
    return { phase: "audit_working", terminal: "interrupted", label: "调查已中断" };
  }
  if (run.status === "REPORT_GENERATING") {
    return { phase: "report_generating", terminal: null, label: "正在生成报告" };
  }
  if (run.status === "QUEUED") {
    return { phase: "collection_waking", terminal: null, label: "调查已排队" };
  }
  if (run.crawl_status === "completed" && run.analysis_status === "completed") {
    return { phase: "audit_completed", terminal: null, label: "分析已完成" };
  }
  if (run.analysis_status === "running") {
    return { phase: "audit_working", terminal: null, label: "采集与分析进行中" };
  }
  return { phase: "collection_working", terminal: null, label: "正在采集" };
}
