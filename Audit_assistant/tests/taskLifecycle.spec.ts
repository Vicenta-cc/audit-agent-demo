import { test, expect } from "@playwright/test";
import { mapInvestigationRunState } from "../src/features/investigation/investigationRunState";
import type { InvestigationRunProjection } from "../src/types/investigationCreation";

test("ending is distinct from paused and ended", () => {
  const run = { status: "INTERRUPTED", crawl_status: "stopped", analysis_status: "paused", available_actions: {} } as InvestigationRunProjection;
  expect(mapInvestigationRunState(run).label).toContain("可恢复");
  run.available_actions = { ending: true };
  expect(mapInvestigationRunState(run).label).toContain("正在结束");
  run.available_actions = { ended: true };
  run.error_code = "cancelled";
  expect(mapInvestigationRunState(run).terminal).toBe("ended");
});

for (const paused of [false, true]) {
  test(`${paused ? "paused" : "queued without job"} task confirms end and waits for backend stop`, async ({ page }) => {
    let calls = 0;
    await page.route("**/api/me/task-quota", route => route.fulfill({ json: { enabled: true, used: 1, remaining: 2, limit: 3, legacy_record_count: 0, reset_at: "2026-09-21T00:00:00+08:00" } }));
    await page.route("**/api/tasks/fixture-run/cancel", route => {
      calls += 1;
      return route.fulfill({ json: { state: "RESERVED", decision: "CANCELLED" } });
    });
    await page.goto("/tests/taskLifecycle.html");
    await expect(page.getByLabel("今日任务额度")).toContainText("今日已用 1 / 3 次 · 剩余 2 次");
    await expect(page.locator(".investigation-run-metrics div").filter({ hasText: "待分析" })).toContainText("0");
    if (paused) await page.getByText("模拟暂停状态", {exact: true}).click();
    await page.getByRole("button", {name: "结束任务", exact: true}).click();
    await expect(page.getByRole("dialog")).toContainText("已扣次数不返还");
    await page.getByRole("button", {name: "取消", exact: true}).click();
    expect(calls).toBe(0);
    await page.getByRole("button", {name: "结束任务", exact: true}).click();
    await page.getByRole("button", {name: "确认结束任务", exact: true}).click();
    await expect(page.getByRole("button", {name: "正在结束…", exact: true})).toBeDisabled();
    expect(calls).toBe(1);
    await expect(page.getByRole("button", {name: "继续分析", exact: true})).toHaveCount(0);
    await page.getByText("模拟后台确认停止", {exact: true}).click();
    await expect(page.locator(".investigation-run-summary")).toContainText("任务已结束");
    await expect(page.getByRole("button", {name: "结束任务", exact: true})).toHaveCount(0);
    await expect(page.getByLabel("今日任务额度")).toContainText("剩余 2 次");
    if (paused) await page.screenshot({path: "/tmp/quota-ended-ui.png", fullPage: true});
  });
}

test("paused task keeps polling after end, and stops only on settled terminal response", async () => {
  const { shouldPollInvestigationRun } = await import("../src/features/investigation/investigationRunState");
  const paused = { status: "INTERRUPTED", crawl_status: "stopped", analysis_status: "paused", available_actions: { end_task: true } } as InvestigationRunProjection;
  expect(shouldPollInvestigationRun(paused)).toBe(false);
  expect(shouldPollInvestigationRun({ ...paused, available_actions: { ending: true } })).toBe(true);
  expect(shouldPollInvestigationRun({ ...paused, status: "FAILED", available_actions: { end_task: true } })).toBe(true);
  expect(shouldPollInvestigationRun({ ...paused, status: "FAILED", available_actions: { ended: true } })).toBe(false);
});

test("quota rejection does not masquerade as a changed draft", async () => {
  const { isTaskAdmissionRejection, formatCreationErrorMessage } = await import("../src/features/investigation/confirmationView");
  expect(isTaskAdmissionRejection("DAILY_REPORT_LIMIT")).toBe(true);
  expect(isTaskAdmissionRejection("USER_TASK_LIMIT")).toBe(true);
  expect(isTaskAdmissionRejection("RESOURCE_STALE")).toBe(false);
  expect(formatCreationErrorMessage("今日三次任务额度已用完。")).toContain("次日 00:00");
  expect(formatCreationErrorMessage("已有未完成任务，请完成或结束整个任务后再提交。")).toContain("继续原任务");
});

test("natural outcomes and user stops have distinct colors and controls", async ({ page }) => {
  await page.route("**/api/me/task-quota", route => route.fulfill({ json: { enabled: false } }));
  await page.goto("/tests/taskLifecycle.html");
  const tag = page.locator(".agent-exec-status-tag");
  for (const [scene, tone, label] of [
    ["模拟正常发布", "done", "已完成"],
    ["模拟部分失败发布", "warning", "已完成，部分失败"],
    ["模拟全部失败", "error", "执行失败"],
    ["模拟后台确认停止", "neutral", "已结束"]
  ]) {
    await page.getByRole("button", { name: scene, exact: true }).click();
    await expect(tag).toHaveText(label);
    await expect(tag).toHaveClass(`agent-exec-status-tag is-${tone}`);
    await expect(page.locator(".investigation-run-projection")).toHaveClass(`investigation-run-projection tone-${tone}`);
    await expect(page.getByRole("button", { name: "结束任务", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("流水线实时控制")).toHaveCount(0);
  }
  await page.getByRole("button", { name: "模拟暂停状态" }).click();
  await expect(tag).toHaveText("已暂停");
  await expect(tag).toHaveClass("agent-exec-status-tag is-neutral");
  await expect(page.getByRole("button", { name: "继续分析", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "暂停分析", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "模拟运行状态" }).click();
  await expect(page.getByRole("button", { name: "暂停分析", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "继续分析", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "停止分析", exact: true })).toHaveCount(0);
  let action = "";
  await page.route("**/api/jobs/fixture-job/control", async route => {
    action = route.request().postDataJSON().action;
    await route.fulfill({ json: {} });
  });
  await page.getByRole("button", { name: "暂停分析", exact: true }).click();
  await expect.poll(() => action).toBe("pause_analysis");
});
