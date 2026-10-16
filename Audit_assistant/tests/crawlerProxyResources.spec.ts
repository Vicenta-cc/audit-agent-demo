import { expect, test, type Page } from "@playwright/test";

const account = (id: string, extra: Record<string, unknown> = {}) => ({
  id, platform: "dy", display_name: id, platform_account_id: "", status: "active",
  access_scope: "private", can_manage: true, has_auth_state: true, proxy_id: "backend-default",
  proxy_required: true, ...extra
});

async function forbidProxyManagement(page: Page) {
  const calls: string[] = [];
  await page.route(/\/api\/(?:crawler-proxies|admin\/users\/.*\/grants\/crawler-proxy)|\/proxy-binding(?:\?|$)/, route => {
    calls.push(route.request().url());
    return route.fulfill({ status: 500, json: { detail: "frontend must not manage network configuration" } });
  });
  return calls;
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/config", route => route.fulfill({ json: { enabled: false } }));
});

test("account page and editor expose no proxy management, endpoints, credentials or binding controls", async ({ page }) => {
  const calls = await forbidProxyManagement(page);
  await page.route("**/api/crawler-accounts", route => route.fulfill({ json: { items: [account("后台配置账号")] } }));
  await page.goto("/crawler-accounts");
  await expect(page.getByText("后台配置账号", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "采集代理资源" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /添加代理|编辑代理|删除代理|授予代理|撤销代理|检查出口|确认维护/ })).toHaveCount(0);
  await page.getByRole("button", { name: "编辑后台配置账号", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "编辑采集账号" })).toBeVisible();
  await expect(page.getByLabel(/采集代理|代理用户名|代理密码|HTTP 入口服务器|最大绑定账号数|授权用户 ID|出口检查账号 ID/)).toHaveCount(0);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.getByText("backend-default", { exact: false })).toHaveCount(0);
  expect(calls).toEqual([]);
});

test("Douyin creation uses ordinary payload and displays backend default binding without choosing it", async ({ page }) => {
  const calls = await forbidProxyManagement(page);
  let created: Record<string, unknown> | null = null;
  await page.route("**/api/crawler-accounts", route => {
    if (route.request().method() === "POST") {
      created = route.request().postDataJSON();
      return route.fulfill({ status: 201, json: { item: account("后台默认网络账号", { ...created, status: "login_required", has_auth_state: false }) } });
    }
    return route.fulfill({ json: { items: [] } });
  });
  await page.route("**/api/crawler-accounts/后台默认网络账号/login-sessions", route => route.fulfill({ status: 503, json: { detail: "采集网络暂不可用" } }));
  await page.route("**/api/crawler-accounts/*/login-sessions", route => route.fulfill({ status: 503, json: { detail: "采集网络暂不可用" } }));
  await page.goto("/crawler-accounts");
  await page.getByRole("button", { name: "添加采集账号", exact: true }).click();
  await page.getByLabel("所属平台").selectOption("dy");
  await page.getByLabel("账号名称", { exact: true }).fill("后台默认网络账号");
  await page.getByRole("button", { name: "保存账号", exact: true }).click();
  await expect.poll(() => created).toEqual({ platform: "dy", display_name: "后台默认网络账号", platform_account_id: "", access_scope: "private" });
  await expect(page.getByRole("dialog", { name: "添加采集账号" })).toHaveCount(0);
  await expect(page.getByText("采集网络暂不可用", { exact: true })).toBeVisible();
  expect(calls).toEqual([]);
});

test("editing metadata never changes or removes an existing backend binding", async ({ page }) => {
  const calls = await forbidProxyManagement(page);
  let updated: Record<string, unknown> | null = null;
  await page.route("**/api/crawler-accounts", route => route.fulfill({ json: { items: [account("已有账号")] } }));
  await page.route("**/api/crawler-accounts/*", route => {
    updated = route.request().postDataJSON();
    return route.fulfill({ json: { item: account("已有账号", { ...updated }) } });
  });
  await page.goto("/crawler-accounts");
  await page.getByRole("button", { name: "编辑已有账号", exact: true }).click();
  await page.getByLabel("账号名称", { exact: true }).fill("修改名称");
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByText("修改名称", { exact: true })).toBeVisible();
  expect(updated).toEqual({ display_name: "修改名称", platform_account_id: "" });
  expect(calls).toEqual([]);
});

test("network configuration and backend readiness failures never count as ready", async ({ page }) => {
  const calls = await forbidProxyManagement(page);
  await page.route("**/api/crawler-accounts", route => route.fulfill({ json: {
    items: [
      account("待配置账号", { proxy_id: null, collection_ready: false, collection_block_reason: "PROXY_CONFIGURATION_REQUIRED" }),
      account("待校验账号", { needs_proxy_login_validation: true, collection_ready: false, collection_block_reason: "PROXY_LOGIN_VALIDATION_REQUIRED" }),
      account("网络异常账号", { collection_ready: false, collection_block_reason: "PROXY_UNAVAILABLE" }),
      account("旧账号网络未配置", { proxy_id: null, proxy_required: false, collection_ready: false, collection_block_reason: "PROXY_CONFIGURATION_REQUIRED" }),
      account("停用账号", { status: "disabled", collection_ready: false, collection_block_reason: "PROXY_UNAVAILABLE" }),
      account("正常账号", { collection_ready: true }),
      account("无登录态账号", { has_auth_state: false })
    ], shared_pool: { total: 4, ready: 1, busy: 0, unavailable: 3 }
  } }));
  await page.goto("/crawler-accounts");
  await expect(page.getByText("待配置采集网络", { exact: true })).toHaveCount(2);
  await expect(page.getByText("采集网络已变更，需校验登录", { exact: true })).toBeVisible();
  await expect(page.getByText("采集网络暂不可用", { exact: true })).toBeVisible();
  await expect(page.getByText("可用 1 / 异常 6", { exact: true })).toBeVisible();
  await expect(page.getByLabel("账号概况")).toContainText("1 可用");
  await expect(page.getByLabel("公共账号池概况")).toContainText("当前空闲1");
  await expect(page.getByRole("button", { name: "校验登录待校验账号", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "启用停用账号", exact: true })).toBeVisible();
  expect(calls).toEqual([]);
});

for (const [code, message] of [
  ["PROXY_CONFIGURATION_REQUIRED", "采集网络尚未配置，请联系管理员"],
  ["PROXY_UNAVAILABLE", "采集网络暂不可用，请稍后重试或联系管理员"],
  ["PROXY_BINDING_CAPACITY", "采集网络容量不足，请联系管理员"]
]) {
  test(`account creation explains backend network failure ${code} without asking for frontend configuration`, async ({ page }) => {
    const calls = await forbidProxyManagement(page);
    await page.route("**/api/crawler-accounts", route => route.request().method() === "POST"
      ? route.fulfill({ status: 409, json: { detail: code } })
      : route.fulfill({ json: { items: [] } }));
    await page.goto("/crawler-accounts");
    await page.getByRole("button", { name: "添加采集账号", exact: true }).click();
    await page.getByLabel("所属平台").selectOption("dy");
    await page.getByLabel("账号名称", { exact: true }).fill("待添加账号");
    await page.getByRole("button", { name: "保存账号", exact: true }).click();
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: "添加采集账号" })).toBeVisible();
    await expect(page.getByLabel("采集代理", { exact: true })).toHaveCount(0);
    expect(calls).toEqual([]);
  });
}

test("backend network unavailable reason wins over pending login validation", async ({ page }) => {
  const calls = await forbidProxyManagement(page);
  await page.route("**/api/crawler-accounts", route => route.fulfill({ json: {
    items: [account("网络不可用待校验账号", {
      collection_ready: false,
      collection_block_reason: "PROXY_UNAVAILABLE",
      needs_proxy_login_validation: true
    })]
  } }));
  await page.goto("/crawler-accounts");
  await expect(page.getByText("采集网络暂不可用", { exact: true })).toBeVisible();
  await expect(page.getByText("采集网络已变更，需校验登录", { exact: true })).toHaveCount(0);
  await expect(page.getByText("可用 0 / 异常 1", { exact: true })).toBeVisible();
  await expect(page.getByLabel("账号概况")).toContainText("0 可用");
  expect(calls).toEqual([]);
});

test("expired exit observations are distinct from protection and neither counts as ready", async ({ page }) => {
  const calls = await forbidProxyManagement(page);
  await page.route("**/api/crawler-accounts", route => route.fulfill({ json: {
    items: [
      account("出口待复查账号", { collection_ready: false, collection_block_reason: "EXIT_OBSERVATION_STALE" }),
      account("出口保护账号", { collection_ready: false, collection_block_reason: "SHARED_EXIT_UNAVAILABLE" })
    ]
  } }));
  await page.goto("/crawler-accounts");
  await expect(page.getByText("采集网络待校验，任务启动时自动检查", { exact: true })).toBeVisible();
  await expect(page.getByText("共享出口不可用，采集已暂停", { exact: true })).toBeVisible();
  await expect(page.getByText("可用 0 / 异常 2", { exact: true })).toBeVisible();
  expect(calls).toEqual([]);
});
