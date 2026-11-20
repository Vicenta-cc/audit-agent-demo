import { expect, test } from "@playwright/test";

const now = "2026-11-09T10:00:00+08:00";

test.beforeEach(async ({ context, page }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/config") return route.fulfill({ json: { enabled: true } });
    if (path === "/api/auth/me") return route.fulfill({ json: { user: {
      id: "user-1", username: "tester", role: "admin", status: "active",
      activation_mode: "created_at", validity_started_at: now, expires_at: null,
      created_at: now, updated_at: now
    } } });
    if (path === "/api/auth/csrf") return route.fulfill({ json: { csrf_token: "csrf" } });
    if (path === "/api/investigation-workspaces") return route.fulfill({ json: { items: [], has_more: false } });
    if (path === "/api/historical-report-workspaces") return route.fulfill({ json: { items: [] } });
    if (path === "/api/monitored-users") return route.fulfill({ json: { items: [{
      id: "focus-main", platform: "dy", display_name: "重点账号",
      public_douyin_id: "douyin-main", sec_uid: "sec-main",
      profile_url: "https://www.douyin.com/user/sec-main", author: { nickname: "重点账号" },
      suspected_related_accounts: [
        { id: "rel-a", platform: "dy", display_name: "同名账号", public_douyin_id: "same-a", sec_uid: "sec-a", profile_url: "https://www.douyin.com/user/sec-a", source_comment_id: "comment-a", analysis_status: "linked" },
        { id: "rel-b", platform: "dy", display_name: "同名账号", public_douyin_id: "same-b", sec_uid: "sec-b", profile_url: "https://www.douyin.com/user/sec-b", source_comment_id: "comment-b", analysis_status: "linked" },
        { id: "rel-c", platform: "dy", display_name: "无主页账号", public_douyin_id: "public-only", sec_uid: "", profile_url: "", source_comment_id: "comment-c", analysis_status: "linked" },
        { id: "rel-d", platform: "dy", display_name: "仅有昵称", public_douyin_id: "", sec_uid: "", profile_url: "", source_comment_id: "comment-d", analysis_status: "linked" },
        { id: "rel-e", platform: "xhs", display_name: "非抖音账号", public_douyin_id: "wrong-label", sec_uid: "not-douyin", profile_url: "https://www.douyin.com/user/not-douyin", source_comment_id: "comment-e", analysis_status: "linked" },
        { id: "rel-f", platform: "dy", display_name: "伪造主页账号", public_douyin_id: "spoofed-url", sec_uid: "spoofed-url", profile_url: "https://example.com/user/spoofed-url", source_comment_id: "comment-f", analysis_status: "linked" }
      ]
    }] } });
    if (path === "/api/me/task-quota") return route.fulfill({ json: { enabled: false } });
    return route.fulfill({ json: { items: [] } });
  });
});

test("focus users use public Douyin ids and each relation row's own trusted profile URL", async ({ page }) => {
  const penetrationRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/comment-risk-users") {
      penetrationRequests.push(request.url());
    }
  });
  await page.goto("/users");

  await expect(page.getByRole("tab", { name: /用户穿透/ })).toHaveCount(0);

  const mainLink = page.getByRole("link", { name: "重点账号" }).first();
  await expect(mainLink).toHaveAttribute("href", "https://www.douyin.com/user/sec-main");
  await expect(mainLink).toHaveAttribute("target", "_blank");
  await expect(mainLink).toHaveAttribute("rel", "noopener noreferrer");
  await expect(page.getByRole("link", { name: "抖音号：douyin-main" })).toBeVisible();

  await page.getByRole("button", { name: "复制抖音号" }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("douyin-main");
  await expect(page.getByText("抖音号已复制", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "关联账号", exact: true }).click();
  const sameNameLinks = page.getByRole("link", { name: "打开同名账号的抖音主页" });
  await expect(sameNameLinks).toHaveCount(2);
  await expect(sameNameLinks.nth(0)).toHaveAttribute("href", "https://www.douyin.com/user/sec-a");
  await expect(sameNameLinks.nth(1)).toHaveAttribute("href", "https://www.douyin.com/user/sec-b");
  await expect(sameNameLinks.nth(0)).toHaveAttribute("target", "_blank");
  await expect(sameNameLinks.nth(0)).toHaveAttribute("rel", "noopener noreferrer");
  const noProfile = page.locator(".related-comment-item-compact").filter({ hasText: "无主页账号" });
  await expect(noProfile.getByRole("link")).toHaveCount(0);
  await expect(noProfile.getByText("主页链接不可用", { exact: true })).toBeVisible();
  const nicknameOnly = page.locator(".related-comment-item-compact").filter({ hasText: "仅有昵称" });
  await expect(nicknameOnly.getByRole("link")).toHaveCount(0);
  await expect(nicknameOnly.getByText("抖音号：未提供", { exact: true })).toBeVisible();
  const nonDouyin = page.locator(".related-comment-item-compact").filter({ hasText: "非抖音账号" });
  await expect(nonDouyin.getByRole("link")).toHaveCount(0);
  await expect(nonDouyin.getByText(/抖音号/)).toHaveCount(0);
  const spoofedProfile = page.locator(".related-comment-item-compact").filter({ hasText: "伪造主页账号" });
  await expect(spoofedProfile.getByRole("link")).toHaveCount(0);
  await expect(spoofedProfile.getByText("主页链接不可用", { exact: true })).toBeVisible();
  expect(penetrationRequests).toHaveLength(0);
});

test("signed-in account list can retry a failure without showing demo accounts", async ({ page }) => {
  let first = true;
  await page.route("**/api/monitored-users", route => {
    if (!first) return route.fallback();
    first = false;
    return route.fulfill({ status: 503, json: { detail: "监控对象暂时不可用" } });
  });
  await page.goto("/users");
  await page.getByRole("button", { name: "重试" }).click();
  await expect(page.getByRole("link", { name: "重点账号", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: /用户穿透/ })).toHaveCount(0);
});
