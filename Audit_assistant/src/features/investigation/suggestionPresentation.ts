import type { PlatformCode, TaskDraft } from "../../types/investigation";
import type { ConfirmationPreview } from "../../types/investigationCreation";

export function selectSingleSuggestionPlatform(platform: PlatformCode): PlatformCode[] {
  return [platform];
}

export function buildSuggestionBlockerLink(preview?: ConfirmationPreview) {
  const blocker = preview?.blockers.find((item) => item.management_url);
  return blocker
    ? {
        label: blocker.code === "NO_PUBLISHED_RULESET"
          ? "去配置研判规则"
          : blocker.code === "NO_PUBLISHED_RECALL_LEXICON"
            ? "去配置黑话库"
            : "前往配置",
        managementUrl: blocker.management_url
      }
    : null;
}

export function buildSuggestionAssistantCopy(draft: TaskDraft, preview?: ConfirmationPreview) {
  if (preview?.temporary_ruleset && preview.blockers.every(
    (blocker) => blocker.code === "TEMPORARY_RULESET_EXECUTION_UNAVAILABLE"
  )) {
    return "本次临时规则已采用，调查尚未启动。";
  }
  if (preview?.blockers.length) {
    return "已根据你的调查主题整理出初步建议。当前还有一项必要配置需要处理，完成后即可继续。";
  }
  if (preview?.mode === "creator") {
    return "已识别为博主主页调查。系统已整理采集范围和推荐研判规则，请核对后生成任务配置。";
  }
  return `已识别为“${draft.taskName}”。系统已整理本次召回词、推荐研判规则和可用平台，请核对后继续。`;
}
