import type { TaskDraft } from "../../types/investigation";
import type { ConfirmationPreview, RelevanceScope, RelevanceScopeState } from "../../types/investigationCreation";

const platformLabels = {
  dy: "抖音",
  xhs: "小红书",
  ks: "快手",
  wb: "微博",
  xy: "闲鱼",
  multi: "多平台"
} as const;

export function buildRelevanceScopeView(source?: {
  relevance_scope?: RelevanceScope | null;
  relevance_scope_state?: RelevanceScopeState;
}) {
  const scope = source?.relevance_scope;
  const state = source?.relevance_scope_state;
  if (state?.status === "cleared" || (!scope && state?.status !== "pending")) return null;
  const pending = state?.status === "pending";
  const policy = pending ? state.selection_policy : scope?.selection_policy;
  const province = scope?.province.name;
  return {
    pending,
    target: pending
      ? state.target_text || "待明确地域"
      : `${scope!.target.name}${province && province !== scope!.target.name ? `（${province}）` : ""}`,
    policy: policy === "local_only"
      ? "仅本地，不补充其他地区"
      : policy === "local_first_with_supplement" ? (pending ? "本地优先，不足时补充其他地区（默认策略）" : "本地优先，不足时按已确认策略补充") : "",
    reason: pending ? state.reason || "地域尚未明确，请在对话中提供省级、地级市或直辖市名称。" : "",
    candidates: pending ? state.candidates.map((candidate) => (
      `${candidate.name}${candidate.province_name && candidate.province_name !== candidate.name ? `（${candidate.province_name}）` : ""}`
    )).join("、") : "",
    notice: pending
      ? "地域要求待澄清，当前不能启动。请在对话中补充或修改地域。"
      : "地域执行能力尚未接通，当前不能启动。"
  };
}

export function buildConfirmationCardView(
  draft: TaskDraft,
  preview?: ConfirmationPreview
) {
  const rulesManagementUrl = preview?.blockers.find(
    (blocker) => blocker.code === "NO_PUBLISHED_RULESET"
  )?.management_url;
  return {
    relevanceScope: buildRelevanceScopeView(preview),
    title: preview?.title || draft.taskName,
    platform: preview
      ? platformLabels[preview.platform]
      : draft.platforms.map((platform) => platformLabels[platform]).join("、"),
    objective: preview?.objective || "",
    termsOrCreator: preview?.mode === "creator"
      ? preview.creator_url
      : (preview?.resolved_search_terms || draft.keywords).join("、"),
    searchTermsNotice: preview?.mode === "search" ? preview.search_terms_notice || "" : "",
    ruleSet: preview?.ruleset_revision
      ? preview.ruleset_revision.name
      : preview?.temporary_ruleset ? `本次使用临时规则：${preview.temporary_ruleset.content.name}` : "尚未绑定",
    recallStrategy: preview?.mode === "creator"
      ? "主页模式不使用召回词"
      : preview?.recall_plan.strategy === "temporary_terms"
        ? "本次临时搜索词"
        : preview?.recall_plan.lexicon_title || preview?.recall_plan.strategy || "",
    maxNotes: preview?.max_notes,
    canConfirm: preview ? preview.can_confirm && preview.blockers.length === 0 : true,
    rulesManagementUrl
  };
}

export function buildConfirmationIdempotencyKey(
  workspaceSessionId: string,
  draftId: string,
  draftRevision: number
) {
  return `investigation-confirm:${workspaceSessionId}:${draftId}:${draftRevision}`;
}

export function parseInvestigationSearchTerms(value: string) {
  return Array.from(new Set(
    value
      .split(/[、，,;；\n]+/)
      .map((term) => term.trim())
      .filter(Boolean)
  ));
}

export function formatConfirmationBlockerMessage(code: string, message: string) {
  if (code === "TEMPORARY_RULESET_EXECUTION_UNAVAILABLE") {
    return "本次使用临时规则，暂不支持启动调查。";
  }
  if (code === "NO_PUBLISHED_RULESET") {
    return "当前还没有匹配的已发布研判规则，配置后即可继续。";
  }
  if (code === "NO_PUBLISHED_RECALL_LEXICON") {
    return "当前没有适合本次主题的黑话库，请先配置或选择一个词库。";
  }
  if (code === "NO_SEARCH_TERMS") {
    return "当前没有可用于本次调查的召回词，请补充调查主题或配置词库。";
  }
  if (code === "NO_CRAWLER_ACCOUNT" || code === "collection_service_unavailable") {
    return "当前平台采集账号暂不可用，请稍后重试。";
  }
  if (code === "RELEVANCE_SCOPE_PENDING") {
    return message.trim() ? message : "地域要求待澄清，当前不能启动。请在对话中提供省级、地级市或直辖市名称。";
  }
  if (code === "RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE") {
    return message.trim() ? message : "地域执行能力尚未接通，当前不能启动。";
  }
  if (code === "RELEVANCE_SCOPE_MODE_UNSUPPORTED") {
    return message.trim() ? message : "当前模式暂不支持地域调查。";
  }
  return "当前配置暂时无法继续，请调整相关设置后重试。";
}

export function formatCreationErrorMessage(message: string) {
  const normalized = message.toUpperCase();
  if (normalized.includes("DAILY_REPORT_LIMIT") || /今日三次任务额度已用完/.test(message)) {
    return "今日三次任务额度已用完，北京时间次日 00:00 重置。";
  }
  if (normalized.includes("USER_TASK_LIMIT") || /已有未完成任务/.test(message)) {
    return "已有未完成任务，请继续原任务，或结束整个任务后再发起新调查。";
  }
  if (normalized.includes("TASK_ALREADY_ENDED")) return "任务已结束，请在新对话发起调查。";
  if (normalized.includes("CRAWLER_ACCOUNT_VERIFICATION_REQUIRED") || /账号.*平台验证/.test(message)) {
    return "采集账号需要平台验证，请完成验证后再继续；登录态未判定失效。";
  }
  if (normalized.includes("CRAWLER_RATE_LIMITED")) {
    return "采集受到平台限流，请等待冷却结束后再继续。";
  }
  if (normalized.includes("CRAWLER_ACCOUNT_LOGIN_REQUIRED")) {
    return "采集账号需要重新登录，请在采集账号中处理后再继续。";
  }
  if (normalized.includes("DATA_INSPECTION_FAILED")) {
    return "本轮未完成：模型服务的内容检查拒绝了请求。可以调整请求或使用已有资源。";
  }
  if (normalized.includes("NO_PUBLISHED_RULESET")) {
    return formatConfirmationBlockerMessage("NO_PUBLISHED_RULESET", message);
  }
  if (normalized.includes("NO_PUBLISHED_RECALL_LEXICON")) {
    return formatConfirmationBlockerMessage("NO_PUBLISHED_RECALL_LEXICON", message);
  }
  if (normalized.includes("NO_SEARCH_TERMS")) {
    return formatConfirmationBlockerMessage("NO_SEARCH_TERMS", message);
  }
  if (
    normalized.includes("NO_CRAWLER_ACCOUNT")
    || normalized.includes("COLLECTION_SERVICE_UNAVAILABLE")
  ) {
    return formatConfirmationBlockerMessage("NO_CRAWLER_ACCOUNT", message);
  }
  if (normalized.includes("RESOURCE_STALE") || /REVISION|版本已变化|已被更新/i.test(message)) {
    return "配置刚刚发生变化，已载入最新内容，请重新核对。";
  }
  if (/搜索词|召回词/.test(message)) return "召回词暂时无法保存，请稍后重试。";
  if (/平台/.test(message)) return "平台设置暂时无法保存，请稍后重试。";
  if (/确认/.test(message)) return "任务配置暂时无法确认，请稍后重试。";
  return "当前操作暂时无法完成，请稍后重试。";
}

export function isTaskAdmissionRejection(code: string): boolean {
  return ["USER_TASK_LIMIT", "DAILY_REPORT_LIMIT", "TASK_ALREADY_ENDED", "TASK_ADMISSION_REJECTED", "ACCOUNT_EXPIRED", "CRAWLER_ACCOUNT_FORBIDDEN"].includes(code);
}
