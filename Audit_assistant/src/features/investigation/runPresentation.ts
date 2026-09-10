import type { InvestigationRunProjection } from "../../types/investigationCreation";

function readCount(stats: Record<string, unknown>, key: string) {
  const value = stats[key];
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function buildRunProgressItems(run: InvestigationRunProjection) {
  const stats = run.task_stats;
  const items = [
    { label: "进入研判", value: readCount(stats, "ingested_count") },
    { label: "待分析", value: readCount(stats, "pending_analysis_count") },
    { label: "分析中", value: readCount(stats, "analyzing_count") },
    { label: "已完成", value: readCount(stats, "completed_analysis_count") }
  ];
  return items.filter((item): item is { label: string; value: number } => item.value !== null);
}

export function formatRunFailureMessage(run: InvestigationRunProjection) {
  const signal = `${run.error_code} ${run.error_message}`.toLowerCase();
  if (signal.includes("no_valid_content_selected")) {
    return "本次未采集到有效帖子，尚未进行审核或生成报告。请调整关键词或检查采集账号后重新发起调查。";
  }
  if (signal.includes("audit_provider") || signal.includes("provider")) {
    return "研判服务返回异常，本次调查已停止。";
  }
  if (signal.includes("crawler") || signal.includes("collection") || signal.includes("account")) {
    return "采集服务暂时不可用，本次调查已停止。";
  }
  return "本次调查未能继续，请稍后重试或联系管理员。";
}
