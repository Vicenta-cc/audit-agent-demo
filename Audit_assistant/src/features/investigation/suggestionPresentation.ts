import type { PlatformCode, TaskDraft } from "../../types/investigation";
import type { ConfirmationPreview } from "../../types/investigationCreation";

export function selectSingleSuggestionPlatform(platform: PlatformCode): PlatformCode[] {
  return [platform];
}

export function buildSuggestionBlockerLink(preview?: ConfirmationPreview) {
  const blocker = preview?.blockers.find((item) => item.management_url);
  return blocker
    ? {
        label: blocker.code === "NO_PUBLISHED_AUDIT_POLICY"
          ? "去配置研判方案"
          : blocker.code === "NO_PUBLISHED_RECALL_LEXICON"
            ? "去配置召回词库"
            : "前往配置",
        managementUrl: blocker.management_url
      }
    : null;
}

export function buildSuggestionAssistantCopy(draft: TaskDraft, preview?: ConfirmationPreview) {
  if (preview?.blockers.length) {
    return "已根据你的调查主题整理出初步建议。当前还有一项必要配置需要处理，完成后即可继续。";
  }
  if (preview?.mode === "creator") {
    return "已识别为博主主页调查。系统已整理采集范围和推荐研判方案，请核对后生成任务配置。";
  }
  return `已识别为“${draft.taskName}”。系统已整理本次召回词、推荐研判方案和可用平台，请核对后继续。`;
}
