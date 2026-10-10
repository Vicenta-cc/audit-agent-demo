import { expect, test } from "@playwright/test";

const resource = { id: "proxy-one", display_name: "悟空试用", platform: "dy", provider: "wukong", endpoint: { scheme: "http", host: "127.0.0.1", port: 18080 }, has_credentials: true, config_revision: 1, status: "active", expires_at: null, max_bound_accounts: 2, max_active_accounts: 1, bound_accounts: 0, observed_exit_ip: "203.0.113.8", observed_exit_at: 1791630000 };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/config", route => route.fulfill({ json: { enabled: false } }));
  await page.route("**/api/crawler-accounts", route => route.fulfill({ json: { items: [] } }));
});

test("admin sees distinct entry and exit, writes credentials without reading them", async ({ page }) => {
  let created: Record<string, unknown> | null = null;
  await page.route("**/api/crawler-proxies", async route => {
    if (route.request().method() === "POST") {
      created = route.request().postDataJSON();
      return route.fulfill({ status: 201, json: { item: resource } });
    }
    return route.fulfill({ json: { items: [resource], enabled: true, can_manage: true } });
  });
  await page.goto("/crawler-accounts");
  await expect(page.getByText("入口 HTTP 127.0.0.1:18080")).toBeVisible();
  await expect(page.getByText(/最近出口：203.0.113.8/)).toBeVisible();
  await page.getByRole("button", { name: "编辑代理", exact: true }).click();
  await expect(page.getByLabel("代理密码")).toHaveValue("");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "添加代理", exact: true }).click();
  await page.getByLabel("代理名称").fill("第二条入口");
  await page.getByLabel("HTTP 入口服务器").fill("localhost");
  await page.getByLabel("端口", { exact: true }).fill("18081");
  await page.getByLabel("代理用户名").fill("fixture-user");
  await page.getByLabel("代理密码").fill("ui-secret-canary");
  await page.getByRole("button", { name: "保存代理", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "代理资源编辑" })).toHaveCount(0);
  expect(created).toMatchObject({ endpoint: { scheme: "http", host: "localhost", port: 18081 }, credentials: { username: "fixture-user", password: "ui-secret-canary" } });
  await expect(page.getByText("ui-secret-canary")).toHaveCount(0);
});

test("account creation requires authorized binding before login", async ({ page }) => {
  let created: Record<string, unknown> | null = null;
  await page.route("**/api/crawler-proxies", route => route.fulfill({ json: { items: [resource], enabled: true, can_manage: false } }));
  await page.route("**/api/crawler-accounts", async route => {
    if (route.request().method() === "POST") {
      created = route.request().postDataJSON();
      return route.fulfill({ status: 201, json: { item: { ...created, id: "account-one", status: "login_required", proxy_required: true, binding_revision: 1 } } });
    }
    return route.fulfill({ json: { items: [] } });
  });
  await page.route("**/api/crawler-accounts/account-one/login-sessions", route => route.fulfill({ status: 409, json: { detail: "offline fixture stops before login" } }));
  await page.goto("/crawler-accounts");
  await page.getByRole("button", { name: "添加采集账号", exact: true }).click();
  await page.getByLabel("所属平台").selectOption("dy");
  await page.getByLabel("账号名称", { exact: true }).fill("抖音试用号");
  await page.getByRole("button", { name: "保存账号", exact: true }).click();
  await expect(page.getByText("请先绑定已授权的抖音代理，绑定后再登录")).toBeVisible();
  expect(created).toBeNull();
  await page.getByLabel("采集代理", { exact: true }).selectOption("proxy-one");
  await page.getByRole("button", { name: "保存账号", exact: true }).click();
  await expect.poll(() => created).toMatchObject({ platform: "dy", proxy_id: "proxy-one" });
  await expect(page.getByRole("button", { name: "添加代理", exact: true })).toHaveCount(0);
});


test("admin grants and revokes only crawler-proxy use through existing auth API", async ({ page }) => {
  const requests: { method: string; url: string; body: unknown }[] = [];
  await page.route("**/api/crawler-proxies", route => route.fulfill({ json: { items: [resource], enabled: true, can_manage: true } }));
  await page.route("**/api/admin/users/**", route => {
    requests.push({ method: route.request().method(), url: new URL(route.request().url()).pathname, body: route.request().postData() ? route.request().postDataJSON() : null });
    return route.fulfill({ status: route.request().method() === "DELETE" ? 204 : 200, ...(route.request().method() === "DELETE" ? {} : { json: { grant: { permission: "use" } } }) });
  });
  await page.goto("/crawler-accounts");
  await page.getByLabel("授权用户 ID：悟空试用", { exact: true }).fill("alice-id");
  await page.getByRole("button", { name: "授予代理使用权", exact: true }).click();
  await expect(page.getByRole("region", { name: "采集代理资源", exact: true }).getByRole("status")).toHaveText("已授予 悟空试用 的使用权");
  expect(requests[0]).toEqual({ method: "PUT", url: "/api/admin/users/alice-id/grants/crawler-proxy/proxy-one", body: { permission: "use" } });
  await page.getByRole("button", { name: "撤销代理使用权", exact: true }).click();
  await expect(page.getByRole("region", { name: "采集代理资源", exact: true }).getByRole("status")).toHaveText("已撤销 悟空试用 的使用权");
  expect(requests[1]).toEqual({ method: "DELETE", url: "/api/admin/users/alice-id/grants/crawler-proxy/proxy-one/use", body: null });
});

test("ordinary proxy user never sees grant controls", async ({ page }) => {
  await page.route("**/api/crawler-proxies", route => route.fulfill({ json: { items: [resource], enabled: true, can_manage: false } }));
  await page.goto("/crawler-accounts");
  await expect(page.getByText("入口 HTTP 127.0.0.1:18080")).toBeVisible();
  await expect(page.getByRole("button", { name: "授予代理使用权", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "撤销代理使用权", exact: true })).toHaveCount(0);
});


test("explicit maintenance recovery uses revisioned recover endpoint", async ({ page }) => {
  let current = { ...resource, status: "exit_changed", config_revision: 3 };
  let recoveryBody: unknown = null;
  await page.route("**/api/crawler-proxies", route => route.fulfill({ json: { items: [current], enabled: true, can_manage: true } }));
  await page.route("**/api/crawler-proxies/proxy-one/recover", route => {
    expect(route.request().method()).toBe("POST");
    recoveryBody = route.request().postDataJSON();
    current = { ...current, status: "active", config_revision: 4 };
    return route.fulfill({ json: { item: current } });
  });
  await page.goto("/crawler-accounts");
  await page.getByRole("button", { name: "确认维护完成，运行前重新校验", exact: true }).click();
  await expect(page.getByText(/健康状态：已启用，运行前校验出口/)).toBeVisible();
  expect(recoveryBody).toEqual({ expected_revision: 3 });
  await expect(page.getByText("恢复只清除该资源停止标记，不清除平台限速冷却；新运行仍需重新校验出口。")).toBeVisible();
});


test("maintenance exit check stays diagnostic without recovery", async ({ page }) => {
  let probeBody: unknown = null;
  let recoverCalls = 0;
  await page.route("**/api/crawler-proxies", route => route.fulfill({ json: { items: [{ ...resource, status: "maintenance" }], enabled: true, can_manage: true } }));
  await page.route("**/api/crawler-proxies/proxy-one/probe", route => {
    expect(route.request().method()).toBe("POST"); probeBody = route.request().postDataJSON();
    return route.fulfill({ json: { item: { status: "maintenance", business_enabled: false } } });
  });
  await page.route("**/api/crawler-proxies/proxy-one/recover", route => { recoverCalls++; return route.fulfill({ status: 500 }); });
  await page.goto("/crawler-accounts");
  await page.getByLabel("出口检查账号 ID：悟空试用", { exact: true }).fill("admin-owned-account");
  await page.getByRole("button", { name: "检查出口", exact: true }).click();
  await expect(page.getByText("出口检查完成，仅记录观测样本；不会自动恢复采集。")).toBeVisible();
  await expect(page.getByText(/健康状态：维护中/)).toBeVisible();
  expect(probeBody).toEqual({ account_id: "admin-owned-account", expected_revision: 1 });
  expect(recoverCalls).toBe(0);
});

test("proxy changes require login validation and never count as ready", async ({ page }) => {
  await page.route("**/api/crawler-proxies", route => route.fulfill({ json: { items: [resource], enabled: true, can_manage: true, max_active_accounts_cap: 1 } }));
  await page.route("**/api/crawler-accounts", route => route.fulfill({ json: { items: [{ id: "pending-account", platform: "dy", display_name: "待校验账号", status: "active", access_scope: "private", can_manage: true,
    proxy_id: "proxy-one", proxy_required: true, needs_proxy_login_validation: true, binding_revision: 2 }] } }));
  await page.goto("/crawler-accounts");
  await expect(page.getByText("代理配置变更，需校验登录", { exact: true })).toBeVisible();
  await expect(page.getByText("可用 0 / 异常 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "校验登录待校验账号", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "添加代理", exact: true }).click();
  await expect(page.getByLabel("最大活跃账号数", { exact: true })).toHaveAttribute("max", "1");
});
