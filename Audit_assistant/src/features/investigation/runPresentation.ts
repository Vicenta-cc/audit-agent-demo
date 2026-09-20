import type { InvestigationRunProjection } from "../../types/investigationCreation";

function readCount(stats: Record<string, unknown>, key: string) {
  const value = stats[key];
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function buildRunProgressItems(run: InvestigationRunProjection) {
  const stats = run.task_stats;
  const pending = readCount(stats, "pending_analysis_count");
  const failed = readCount(stats, "failed_analysis_count");
  const queued = readCount(stats, "queued_analysis_count") ?? (pending === null ? null : Math.max(0, pending - (failed ?? 0)));
  const items = [
    { label: "进入研判", value: readCount(stats, "ingested_count") },
    { label: "待分析", value: queued },
    { label: "审核失败", value: failed },
    { label: "分析中", value: readCount(stats, "analyzing_count") },
    { label: "已完成", value: readCount(stats, "completed_analysis_count") }
  ];
  return items.filter((item): item is { label: string; value: number } => item.value !== null);
}

export function formatRunFailureMessage(run: InvestigationRunProjection) {
  if (run.error_code === "cancelled") return "任务已结束，已扣次数不返还；可在新对话发起调查。";
  const signal = `${run.error_code} ${run.error_message}`.toLowerCase();
  if (run.available_actions?.ended && (signal.includes("crawler") || signal.includes("account"))) {
    return "本次任务已结束。请检查采集账号或采集服务后，在新对话发起调查。";
  }
  if (signal.includes("crawler_account_verification_required")) return "采集账号需要平台验证，请完成验证后再继续；这不代表登录态已失效。";
  if (signal.includes("crawler_rate_limited")) return "平台请求限流，采集已停止，请等待冷却结束后再继续。";
  if (signal.includes("crawler_account_login_required")) return "采集账号不可用，请到采集账号页面检查登录状态后再继续。";
  if (signal.includes("no_valid_content_selected")) {
    return "本次未采集到有效帖子，尚未进行审核或生成报告。请调整关键词或检查采集账号后重新发起调查。";
  }
  if (signal.includes("audit_provider") || signal.includes("provider")) {
    return "研判服务返回异常，本次调查已停止。";
  }
  if (signal.includes("crawler") || signal.includes("collection") || signal.includes("account")) {
    return "采集服务暂时不可用，本次调查已停止。";
  }
  return run.available_actions?.ended
    ? "本次任务已结束，可在新对话发起调查或联系管理员。"
    : "本次调查未能继续，请稍后重试或联系管理员。";
}
