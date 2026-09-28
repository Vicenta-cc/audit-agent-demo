import { expect, test } from "@playwright/test";
import { isNoRiskOutput, getRiskLevel } from "../src/features/task-outputs/taskOutputUtils";

test("an incomplete post is not counted as no risk", () => {
  const result = { decision: "review", risk_level: "unknown", audit_gaps: [{ stage: "fusion_audit", label: "整帖汇总审核" }] };
  expect(isNoRiskOutput(result)).toBe(false);
  expect(getRiskLevel(result)).toBe("待复核");
  expect(isNoRiskOutput({ decision: "pass", risk_level: "none" })).toBe(true);
});

test("audit page shows skipped ranges and retains comment warnings", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.route("**/api/**", async route => {
    if (!route.request().url().includes("/api/audit-results/123")) {
      await route.fulfill({ status: 404, json: { detail: "local fixture" } });
      return;
    }
    await route.fulfill({ json: { audit_result: {
      audit_result_id: 123, job_id: "time-preview", title: "分段拦截隔离验收（本地模拟）",
      decision: "review", risk_level: "unknown", summary: "已完成部分未发现明确风险，缺失部分待人工复核。",
      video_results: [{ url: "/api/local-unavailable-video.mp4" }],
      audit_gaps: [
        { stage: "video_segment_audit", label: "视频 1 第 2 段联合审核（画面/OCR/ASR）", source: "video:1/segment:2", start: 15, end: 32.3667 },
        { stage: "asr_translation", label: "ASR 翻译", source: "video:1", start: null, end: null },
        { stage: "fusion_audit", label: "整帖汇总审核", source: "post" }
      ],
      comments: [{ comment_id: "c1", content: "本地验收未审核评论", audit_status: "failed" }]
    }, evidence_groups: [] } });
  });
  await page.goto("/");
  await page.evaluate(async () => {
    const { mountEvidenceTime } = await import("/tests/evidenceTimeMount.tsx");
    const host = document.createElement("div"); document.body.replaceChildren(host);
    mountEvidenceTime(host);
  });
  const notice = page.locator(".detail-audit-notice");
  const summary = notice.locator("summary");
  await expect(summary).toContainText("待人工复核");
  await expect(summary).toContainText("审核环节 3 项未完成");
  await expect(summary).toContainText("评论 1 条未审核");
  await expect(notice).not.toHaveAttribute("open", "");
  await page.screenshot({ path: testInfo.outputPath("partial-audit-collapsed.png"), clip: { x: 0, y: 120, width: 1440, height: 320 } });
  await summary.click();
  await expect(notice).toHaveAttribute("open", "");
  await expect(notice).toContainText("00:15 - 00:32");
  await expect(notice).toContainText("时间范围未提供");
  await expect(notice).toContainText("整帖汇总审核");
  await expect(notice.getByRole("button", { name: "定位 00:15 人工复核" })).toBeVisible();
  await expect(notice.getByRole("button", { name: "查看评论" })).toBeVisible();
  await expect(notice).toContainText("已采集 1 条 · 已审核 0 条 · 未审核 1 条");
  await expect(page.locator(".detail-verdict")).not.toContainText("未发现明显风险");
  await notice.screenshot({ path: testInfo.outputPath("partial-audit-expanded.png") });
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(notice).not.toHaveAttribute("open", "");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(summary).toBeVisible();
  expect(await notice.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
});
