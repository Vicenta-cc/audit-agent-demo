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
    (blocker) => blocker.code === "NO_PUBLISHED_AUDIT_POLICY"
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
    auditPolicy: preview
      ? preview.audit_policy?.name || "尚未选择审核策略"
      : draft.analysisPlanName || draft.matchedRuleSet,
    ruleSet: preview?.ruleset_revision
      ? `${preview.ruleset_revision.name} · v${preview.ruleset_revision.version}`
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
  if (code === "NO_PUBLISHED_AUDIT_POLICY") {
    return "当前没有已发布的审核策略，无法确认执行。";
  }
  if (code === "NO_PUBLISHED_RECALL_LEXICON") {
    return "当前没有可用的已发布召回词库，无法创建关键词调查。";
  }
  return message;
}
