import { expect, test } from "@playwright/test";

test("broken workspace stays visible while other sessions open; refresh recovers after repair", async ({ page }) => {
  const brokenId = "investigation-session:broken";
  const healthyId = "investigation-session:healthy";
  let repaired = false;
  const writes: string[] = [];
  const now = "2026-10-10T10:00:00+00:00";
  await page.route("**/api/**", async route => {
    const req = route.request();
    const path = decodeURIComponent(new URL(req.url()).pathname);
    if (req.method() !== "GET") writes.push(path);
    if (path === "/api/auth/config") return route.fulfill({ json: { enabled: false } });
    if (path === "/api/me/task-quota") return route.fulfill({ json: { enabled: false } });
    if (path === "/api/historical-report-workspaces") return route.fulfill({ json: { items: [] } });
    if (path === "/api/investigation-workspaces") return route.fulfill({ json: {
      items: [
        { workspace_session_id: brokenId, title: repaired ? "恢复后的调查" : "调查会话（暂时无法读取）",
          updated_at: now, run_status: "", presentation_stage: "",
          read_error: repaired ? "" : "WORKSPACE_READ_FAILED" },
        { workspace_session_id: healthyId, title: "正常调查", updated_at: now,
          run_status: "", presentation_stage: "", read_error: "" }
      ], has_more: false
    } });
    if (path.endsWith("/state")) {
      const id = path.split("/").at(-2);
      if (id === brokenId && !repaired) return route.fulfill({ status: 422, json: { detail: "读取失败" } });
      return route.fulfill({ json: {
        workspace: { workspace_session_id: id, created_at: now, updated_at: now, status: "active" },
        messages: [{ message_id: `question-${id}`, turn_id: "fixture", role: "user",
          content: id === brokenId ? "恢复后的调查" : "正常调查", created_at: now }],
        latest_turn: null, draft_artifact: null, run: null, report_messages: [],
        latest_report_turn: null, activity_events: []
      } });
    }
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto("/investigation");
  const sidebar = page.getByRole("complementary", { name: "调查会话侧边栏" });
  await expect(sidebar.getByText("读取异常", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("正常调查", { exact: true })).toBeVisible();
  await expect(page.getByText("调查会话读取失败，请刷新后重试。", { exact: true })).toHaveCount(0);
  await sidebar.getByText("调查会话（暂时无法读取）", { exact: true }).click();
  await expect(page.getByText("当前会话暂时无法读取，请刷新后重试。其他会话仍可正常使用。", { exact: true })).toBeVisible();
  await expect(page.locator("textarea")).toBeDisabled();
  await sidebar.getByText("正常调查", { exact: true }).click();
  await expect(page.locator("textarea")).toBeEnabled();
  await expect(page).toHaveURL(new RegExp(encodeURIComponent(healthyId)));
  repaired = true;
  await page.goto(`/investigation/${encodeURIComponent(brokenId)}`);
  await expect(sidebar.getByText("恢复后的调查", { exact: true })).toBeVisible();
  await expect(page.locator("textarea")).toBeEnabled();
  await expect(sidebar.getByText("读取异常", { exact: true })).toHaveCount(0);
  expect(writes).toEqual([]);
});
