import type { TaskDraft } from "../../types/investigation";
import type { ConfirmationPreview } from "../../types/investigationCreation";

const platformLabels = {
  dy: "抖音",
  xhs: "小红书",
  ks: "快手",
  wb: "微博",
  multi: "多平台"
} as const;

export function buildConfirmationCardView(
  draft: TaskDraft,
  preview?: ConfirmationPreview
) {
  const rulesManagementUrl = preview?.blockers.find(
    (blocker) => blocker.code === "NO_PUBLISHED_RULESET"
  )?.management_url;
  return {
    title: preview?.title || draft.taskName,
    platform: preview
      ? platformLabels[preview.platform]
      : draft.platforms.map((platform) => platformLabels[platform]).join("、"),
    objective: preview?.objective || "",
    termsOrCreator: preview?.mode === "creator"
      ? preview.creator_url
      : (preview?.resolved_search_terms || draft.keywords).join("、"),
    ruleSet: preview?.ruleset_revision
      ? preview.ruleset_revision.name
      : "尚未绑定",
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
  if (code === "NO_PUBLISHED_RULESET") {
    return "当前还没有匹配的已发布研判规则，配置后即可继续。";
  }
  if (code === "NO_PUBLISHED_RECALL_LEXICON") {
    return "当前没有适合本次主题的召回词库，请先配置或选择一个词库。";
  }
  if (code === "NO_SEARCH_TERMS") {
    return "当前没有可用于本次调查的召回词，请补充调查主题或配置词库。";
  }
  if (code === "NO_CRAWLER_ACCOUNT" || code === "collection_service_unavailable") {
    return "当前平台采集账号暂不可用，请稍后重试。";
  }
  void message;
  return "当前配置暂时无法继续，请调整相关设置后重试。";
}

export function formatCreationErrorMessage(message: string) {
  const normalized = message.toUpperCase();
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
