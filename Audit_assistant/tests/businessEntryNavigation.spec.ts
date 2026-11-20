import { expect, test } from "@playwright/test";

const sessionId = "investigation-session:navigation-test";
const detailPath = "/tasks/example/outputs/1";

for (const [link, path, title] of [
  ["重点用户", "/users", "重点用户管理"],
  ["采集账号", "/crawler-accounts", "采集账号管理"],
]) {
  test(`${link} stays selected when sessions load and after refresh`, async ({ page }) => {
    await page.route("**/api/**", route => {
      const url = new URL(route.request().url());
      const payloads: Record<string, unknown> = {
        "/api/auth/config": { enabled: true },
        "/api/auth/me": { user: { id: "test", username: "test", role: "admin" } },
        "/api/auth/csrf": { csrf_token: "test" },
        "/api/investigation-workspaces": { items: [{ workspace_session_id: sessionId,
          title: "导航测试会话", updated_at: "2026-11-06T00:00:00Z" }], has_more: false },
      };
      if (url.pathname.includes("/state")) return route.fulfill({ json: {
        workspace: { workspace_session_id: sessionId }, messages: [], report_messages: [], activity_events: [],
      } });
      if (url.pathname.startsWith("/api/jobs/")) return route.fulfill({ status: 404, json: { detail: "test" } });
      return route.fulfill({ json: payloads[url.pathname] ?? { items: [] } });
    });
    const assertEntry = async () => {
      await expect(page.getByText("导航测试会话", { exact: true })).toBeVisible();
      await expect(page.locator(".inv-center-header").getByText(title, { exact: true })).toBeVisible();
      expect(new URL(page.url()).pathname).toBe(path);
    };
    await page.goto(detailPath);
    await page.getByRole("link", { name: link, exact: true }).click();
    await assertEntry();
    await page.reload();
    await assertEntry();
    await page.goBack();
    expect(new URL(page.url()).pathname).toBe(detailPath);
    await page.goForward();
    await assertEntry();
    await page.getByRole("button", { name: "返回调查对话" }).click();
    await expect(page).toHaveURL(new RegExp(encodeURIComponent(sessionId) + "$"));
  });
}
