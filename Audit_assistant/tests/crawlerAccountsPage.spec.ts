import { expect, test } from "@playwright/test";

const account = (id: string, displayName: string, platform: "xhs" | "dy") => ({
  id,
  platform,
  display_name: displayName,
  platform_account_id: `${id}-platform`,
  status: "active",
  last_validated_at: "2026-08-31T00:00:00Z",
  last_used_at: "2026-08-31T00:01:00Z",
  last_error: "",
  has_auth_state: true,
  auth_state_updated_at: "2026-08-31T00:00:00Z",
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-31T00:00:00Z"
});

test("desktop page renders exactly two authoritative accounts and restores the Draft link", async ({ page }) => {
  await page.route("**/api/crawler-accounts", async (route) => {
    await route.fulfill({
      json: {
        items: [
          account("account-real-xhs", "真实小红书账号", "xhs"),
          account("account-real-dy", "真实抖音账号", "dy")
        ]
      }
    });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/crawler-accounts?return_to=%2Finvestigation%2Fsession-ethnic-relations");

  await expect(page.getByRole("heading", { name: "采集账号", exact: true })).toBeVisible();
  await expect(page.getByText("真实小红书账号", { exact: true })).toHaveCount(1);
  await expect(page.getByText("真实抖音账号", { exact: true })).toHaveCount(1);
  await expect(page.getByText("共 2 个账号", { exact: true })).toBeVisible();
  const returnButton = page.getByRole("button", { name: "返回调查", exact: true });
  await expect(returnButton).toBeVisible();
  await expect(page.locator(".crawler-account-table-row")).toHaveCount(2);

  const pageBox = await page.locator(".crawler-accounts-page").boundingBox();
  const panelBox = await page.locator(".crawler-account-panel").boundingBox();
  expect(pageBox).not.toBeNull();
  expect(panelBox).not.toBeNull();
  expect(panelBox!.x).toBeGreaterThanOrEqual(pageBox!.x);
  expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(pageBox!.x + pageBox!.width + 1);

  await returnButton.click();
  await expect(page).toHaveURL(/\/investigation\/session-ethnic-relations$/);
});

test("empty response remains a real empty state", async ({ page }) => {
  await page.route("**/api/crawler-accounts", (route) => route.fulfill({ json: { items: [] } }));
  await page.goto("/crawler-accounts");

  await expect(page.getByText("暂无采集账号", { exact: true })).toBeVisible();
  await expect(page.locator(".crawler-account-table-row")).toHaveCount(0);
  await expect(page.getByText(/账号一|账号二|账号三|账号四|账号五|账号六/)).toHaveCount(0);
});

test("API failure is explicit and retry reloads the authoritative list", async ({ page }) => {
  let attempts = 0;
  let allowSuccess = false;
  await page.route("**/api/crawler-accounts", async (route) => {
    attempts += 1;
    if (!allowSuccess) {
      await route.fulfill({ status: 500, json: { detail: "账号服务暂不可用" } });
      return;
    }
    await route.fulfill({ json: { items: [account("account-after-retry", "重试后账号", "xhs")] } });
  });
  await page.goto("/crawler-accounts");

  await expect(page.getByText("账号服务暂不可用", { exact: true })).toBeVisible();
  const failedAttempts = attempts;
  allowSuccess = true;
  await page.getByRole("button", { name: "重试" }).click();
  await expect(page.getByText("重试后账号", { exact: true })).toBeVisible();
  expect(attempts).toBeGreaterThan(failedAttempts);
});

test("refresh failure clears the previously displayed accounts", async ({ page }) => {
  let failRefresh = false;
  await page.route("**/api/crawler-accounts", async (route) => {
    if (!failRefresh) {
      await route.fulfill({ json: { items: [account("account-before-failure", "旧账号", "xhs")] } });
      return;
    }
    await route.fulfill({ status: 500, json: { detail: "账号服务暂不可用" } });
  });
  await page.goto("/crawler-accounts");

  await expect(page.getByText("旧账号", { exact: true })).toBeVisible();
  failRefresh = true;
  const refreshButton = page.getByRole("button", { name: "刷新账号列表" });
  await expect(refreshButton).toBeVisible();
  await refreshButton.click();
  await expect(page.getByText("账号服务暂不可用", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  await expect(page.getByText("旧账号", { exact: true })).toHaveCount(0);
  await expect(page.locator(".crawler-account-table-row")).toHaveCount(0);
});

test("terminal login session in browser storage is never resumed", async ({ page }) => {
  let startCalls = 0;
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "crawler-account-login-session:account-real-1",
      "session-terminal"
    );
  });
  await page.route("**/api/crawler-accounts", (route) =>
    route.fulfill({ json: { items: [account("account-real-1", "真实账号一", "xhs")] } })
  );
  await page.route("**/api/crawler-account-login-sessions/session-terminal", (route) =>
    route.fulfill({
      json: {
        item: {
          id: "session-terminal",
          account_id: "account-real-1",
          platform: "xhs",
          status: "success",
          qr_image_data_url: "",
          qr_expires_at: "",
          finalizing_started_at: "",
          finalizing_duration_seconds: 0,
          platform_account_id: "platform-real-1",
          error: "",
          created_at: "2026-08-31T00:00:00Z",
          updated_at: "2026-08-31T00:01:00Z",
          expires_at: "2026-08-31T00:05:00Z"
        }
      }
    })
  );
  await page.route("**/api/crawler-accounts/account-real-1/login-sessions", async (route) => {
    startCalls += 1;
    await route.fulfill({
      status: 201,
      json: {
        item: {
          id: "session-new",
          account_id: "account-real-1",
          platform: "xhs",
          status: "waiting_scan",
          qr_image_data_url: "data:image/png;base64,SYNTHETIC",
          qr_expires_at: "2026-08-31T00:05:00Z",
          finalizing_started_at: "",
          finalizing_duration_seconds: 0,
          platform_account_id: "",
          error: "",
          created_at: "2026-08-31T00:00:00Z",
          updated_at: "2026-08-31T00:01:00Z",
          expires_at: "2026-08-31T00:05:00Z"
        }
      }
    });
  });
  await page.goto("/crawler-accounts");
  await page.getByRole("button", { name: "重新登录真实账号一" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("img", { name: "小红书登录二维码" })).toBeVisible();
  expect(startCalls).toBe(1);
});
