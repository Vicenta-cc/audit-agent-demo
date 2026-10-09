import { expect, test } from "@playwright/test";
import { mapInvestigationRunState, shouldPollInvestigationRun } from "../src/features/investigation/investigationRunState";
import type { InvestigationRunProjection } from "../src/types/investigationCreation";

test("blocked collection offers resume or explicit partial report, never cancellation", async ({ page }, testInfo) => {
  const commands: string[] = [];
  await page.route("**/api/me/task-quota", route => route.fulfill({ json: { enabled: false } }));
  await page.route("**/api/jobs/fixture-job/control", route => {
    commands.push(route.request().postDataJSON().action);
    expect(route.request().headers()["idempotency-key"]).toBeTruthy();
    return route.fulfill({ json: {} });
  });
  await page.goto("/tests/taskLifecycle.html");
  await page.getByText("模拟采集受阻", { exact: true }).click();
  await expect(page.getByText("分析中", { exact: true })).toBeVisible();
  await expect(page.getByText("分析中", { exact: true }).locator("..")).toHaveText("分析中0");
  await expect(page.getByRole("button", { name: "继续采集", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "结束采集并生成报告", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("已采集的帖子会继续完成研判");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(commands).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("blocked-collection.png"), fullPage: true });
  await page.getByRole("button", { name: "结束采集并生成报告", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "结束采集并生成报告", exact: true }).click();
  await expect.poll(() => commands).toEqual(["finish_collection"]);
});

test("no account blocks collection, but not report from existing results", async ({ page }) => {
  await page.route("**/api/me/task-quota", route => route.fulfill({ json: { enabled: false } }));
  await page.goto("/tests/taskLifecycle.html");
  await page.getByText("模拟没有可用账号", { exact: true }).click();
  await expect(page.getByRole("button", { name: "继续采集", exact: true })).toHaveCount(0);
  await expect(page.getByText("暂无可用采集账号", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "结束采集并生成报告", exact: true })).toBeEnabled();
});

test("finish command keeps polling; stopped collection does not hide report generation", () => {
  const run = { status: "INTERRUPTED", crawl_status: "stopped", analysis_status: "completed", task_stats: {}, available_actions: { resuming: true, finishing_collection: true } } as InvestigationRunProjection;
  expect(shouldPollInvestigationRun(run)).toBe(true);
  expect(mapInvestigationRunState(run).label).toContain("随后生成报告");
  run.available_actions = {};
  run.status = "REPORT_GENERATING";
  expect(mapInvestigationRunState(run).label).toBe("正在归纳报告");
  run.status = "INTERRUPTED";
  run.task_stats.collection_resumable = true;
  expect(shouldPollInvestigationRun(run)).toBe(true);
});
