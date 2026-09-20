import { expect, test } from "@playwright/test";
import {
  formatConfirmationBlockerMessage,
  formatCreationErrorMessage
} from "../src/features/investigation/confirmationView";
import { buildRunProgressItems, formatRunFailureMessage } from "../src/features/investigation/runPresentation";
import {
  buildSuggestionAssistantCopy,
  buildSuggestionBlockerLink
} from "../src/features/investigation/suggestionPresentation";
import {
  buildAnalysisSourceProvenance,
  normalizePublicSourceUrl
} from "../src/features/investigation/sourceProvenance";
import { presentCreationAssistantContent } from "../src/features/investigation/workspaceRecovery";
import type { TaskDraft } from "../src/types/investigation";
import type { ConfirmationPreview, InvestigationRunProjection } from "../src/types/investigationCreation";

const draft: TaskDraft = {
  taskName: "世界杯博彩风险调查",
  taskType: "风险调查",
  subject: "世界杯博彩风险",
  platforms: ["dy"],
  keywords: ["世界杯博彩", "看球下注"],
  matchedRuleSet: "博彩风险规则",
  ruleSetDescription: "识别博彩引流与下注诱导。",
  status: "配置中",
  confirmed: false
};

const blockedPreview = {
  mode: "search",
  blockers: [{
    code: "NO_PUBLISHED_RULESET",
    message: "internal message",
    resource_type: "audit_policy",
    resource_id: "",
    latest_safe_summary: {},
    management_url: "/rule-assistant/rulesets?return_to=/investigation"
  }]
} as ConfirmationPreview;

test("creation presentation hides internal diagnostics and keeps real management navigation", () => {
  expect(buildSuggestionAssistantCopy(draft, blockedPreview)).not.toContain("NO_PUBLISHED");
  expect(buildSuggestionBlockerLink(blockedPreview)).toEqual({
    label: "去配置研判规则",
    managementUrl: "/rule-assistant/rulesets?return_to=/investigation"
  });
  expect(formatConfirmationBlockerMessage("NO_PUBLISHED_RULESET", "internal"))
    .toBe("当前还没有匹配的已发布研判规则，配置后即可继续。");
  // Obsolete/unknown codes must keep internal diagnostics out of user-visible copy.
  expect(formatConfirmationBlockerMessage("NO_PUBLISHED_AUDIT_POLICY", "internal"))
    .toBe("当前配置暂时无法继续，请调整相关设置后重试。");
  expect(presentCreationAssistantContent(
    "| 项目 | 内容 |\n|---|---|\n| Draft ID | investigation-draft:secret |"
  )).toBe("当前操作暂时无法完成，请稍后重试。");
  expect(formatCreationErrorMessage("RESOURCE_STALE revision conflict"))
    .toBe("配置刚刚发生变化，已载入最新内容，请重新核对。");
});

test("run presentation exposes curated counts and sanitizes provider failures", () => {
  const run = {
    status: "FAILED",
    error_code: "audit_provider_failed",
    error_message: "Provider returned invalid JSON at /internal/provider",
    task_stats: {
      ingested_count: 1,
      pending_analysis_count: 0,
      analyzing_count: 0,
      completed_analysis_count: 0,
      analysis_status_counts: { failed: 1 },
      batch_item_count: 8
    }
  } as InvestigationRunProjection;
  expect(buildRunProgressItems(run)).toEqual([
    { label: "进入研判", value: 1 },
    { label: "待分析", value: 0 },
    { label: "分析中", value: 0 },
    { label: "已完成", value: 0 }
  ]);
  expect(formatRunFailureMessage(run)).toBe("研判服务返回异常，本次调查已停止。");
});

test("M3 source provenance exposes the stored original post without accepting unsafe links", () => {
  expect(buildAnalysisSourceProvenance({
    platform: "dy",
    note_id: "7680534677446309540",
    url: "https://www.douyin.com/video/7680534677446309540",
    title: "真实采集帖子",
    author: { nickname: "原帖作者" },
    analyzed_at: "2026-09-02T19:23:25Z"
  }, {
    platform: "抖音",
    contentTitle: "回退标题",
    author: "未知作者",
    analyzedAt: "时间暂不可用"
  })).toMatchObject({
    platform: "抖音",
    sourceLabel: "抖音原帖",
    title: "真实采集帖子",
    author: "原帖作者",
    contentId: "7680534677446309540",
    sourceUrl: "https://www.douyin.com/video/7680534677446309540"
  });
  expect(normalizePublicSourceUrl("javascript:alert(1)")).toBe("");
  expect(normalizePublicSourceUrl("not a url")).toBe("");
});

test("failed posts are separate from queued posts, including old payloads", () => {
  const run = { task_stats: { pending_analysis_count: 1, failed_analysis_count: 1, completed_analysis_count: 4 } } as InvestigationRunProjection;
  expect(buildRunProgressItems(run)).toContainEqual({ label: "待分析", value: 0 });
  expect(buildRunProgressItems(run)).toContainEqual({ label: "审核失败", value: 1 });
  run.task_stats.queued_analysis_count = 2;
  expect(buildRunProgressItems(run)).toContainEqual({ label: "待分析", value: 2 });
});
