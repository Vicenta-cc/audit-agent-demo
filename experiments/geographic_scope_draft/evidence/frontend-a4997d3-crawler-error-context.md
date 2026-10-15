# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: crawlerAccountsPage.spec.ts >> ordinary user sees only private accounts plus shared pool capacity
- Location: tests/crawlerAccountsPage.spec.ts:121:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText('Alice 私有账号', { exact: true })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText('Alice 私有账号', { exact: true })

```

```yaml
- main:
  - heading "登录调查工作区" [level=1]
  - paragraph: 使用管理员为你开通的应用账号登录。
  - text: 账号
  - textbox "账号"
  - text: 密码
  - textbox "密码"
  - alert: 账号已到期，请联系管理员续期后重新登录。
  - button "登录"
  - text: 账号默认首次登录起七天有效；到期请联系管理员。不开放自行注册。
  - link "平台用户操作手册 第一次使用？先了解账号限制与调查流程":
    - /url: /manual.html
    - strong: 平台用户操作手册
    - text: 第一次使用？先了解账号限制与调查流程
```

# Test source

```ts
  41  |       contentType: "image/png", headers: { "X-Frame-Sequence": "1" },
  42  |       body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6Eo0AAAAASUVORK5CYII=", "base64")
  43  |     });
  44  |     if (route.request().url().endsWith("/input")) {
  45  |       operations.push(route.request().postDataJSON()); return route.fulfill({ status: 204 });
  46  |     }
  47  |     if (route.request().method() === "DELETE") { cancelled = true; return route.fulfill({ status: 204 }); }
  48  |     return route.fulfill({ json: { item: session } });
  49  |   });
  50  |   await page.setViewportSize({ width: 1280, height: 1100 });
  51  |   await page.goto("/crawler-accounts");
  52  |   await page.getByRole("button", { name: "重新登录交互测试" }).click();
  53  |   await expect(page.getByText("请扫码，并在下方页面完成验证")).toBeVisible();
  54  |   await expect(page.getByRole("img", { name: "可操作的平台登录页面" })).toBeVisible();
  55  |   const screen = page.getByRole("group", { name: "平台登录页面" });
  56  |   const box = await screen.boundingBox();
  57  |   await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  58  |   await page.keyboard.type("123456");
  59  |   await page.keyboard.press("Enter");
  60  |   await expect.poll(() => operations.filter(x => x.type === "text").map(x => x.text).join("")).toBe("123456");
  61  |   expect(operations[0].type).toBe("click");
  62  |   expect(Number(operations[0].x)).toBeCloseTo(500, 0);
  63  |   expect(Number(operations[0].y)).toBeCloseTo(380, 0);
  64  |   await page.getByRole("button", { name: "取消浏览器提示" }).click();
  65  |   await expect.poll(() => operations.some(x => x.type === "dismiss_browser_prompt")).toBe(true);
  66  |   expect(new Set(tokens).size).toBe(1);
  67  |   expect(tokens[0].length).toBeGreaterThanOrEqual(32);
  68  |   await page.getByRole("button", { name: "关闭登录窗口" }).click();
  69  |   await expect.poll(() => cancelled).toBe(true);
  70  |   await expect(page.getByRole("dialog")).toHaveCount(0);
  71  | });
  72  | 
  73  | const account = (id: string, displayName: string, platform: "xhs" | "dy") => ({
  74  |   id,
  75  |   platform,
  76  |   display_name: displayName,
  77  |   platform_account_id: `${id}-platform`,
  78  |   status: "active",
  79  |   last_validated_at: "2026-08-31T00:00:00Z",
  80  |   last_used_at: "2026-08-31T00:01:00Z",
  81  |   last_error: "",
  82  |   has_auth_state: true,
  83  |   auth_state_updated_at: "2026-08-31T00:00:00Z",
  84  |   created_at: "2026-08-30T00:00:00Z",
  85  |   updated_at: "2026-08-31T00:00:00Z"
  86  | });
  87  | 
  88  | test("desktop page renders exactly two authoritative accounts and restores the Draft link", async ({ page }) => {
  89  |   await page.route("**/api/crawler-accounts", async (route) => {
  90  |     await route.fulfill({
  91  |       json: {
  92  |         items: [
  93  |           account("account-real-xhs", "真实小红书账号", "xhs"),
  94  |           account("account-real-dy", "真实抖音账号", "dy")
  95  |         ]
  96  |       }
  97  |     });
  98  |   });
  99  |   await page.setViewportSize({ width: 1440, height: 1000 });
  100 |   await page.goto("/crawler-accounts?return_to=%2Finvestigation%2Fsession-ethnic-relations");
  101 | 
  102 |   await expect(page.getByRole("heading", { name: "采集账号", exact: true })).toBeVisible();
  103 |   await expect(page.getByText("真实小红书账号", { exact: true })).toHaveCount(1);
  104 |   await expect(page.getByText("真实抖音账号", { exact: true })).toHaveCount(1);
  105 |   await expect(page.getByText("共 2 个账号", { exact: true })).toBeVisible();
  106 |   const returnButton = page.getByRole("button", { name: "返回调查", exact: true });
  107 |   await expect(returnButton).toBeVisible();
  108 |   await expect(page.locator(".crawler-account-table-row")).toHaveCount(2);
  109 | 
  110 |   const pageBox = await page.locator(".crawler-accounts-page").boundingBox();
  111 |   const panelBox = await page.locator(".crawler-account-panel").boundingBox();
  112 |   expect(pageBox).not.toBeNull();
  113 |   expect(panelBox).not.toBeNull();
  114 |   expect(panelBox!.x).toBeGreaterThanOrEqual(pageBox!.x);
  115 |   expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(pageBox!.x + pageBox!.width + 1);
  116 | 
  117 |   await returnButton.click();
  118 |   await expect(page).toHaveURL(/\/investigation\/session-ethnic-relations$/);
  119 | });
  120 | 
  121 | test("ordinary user sees only private accounts plus shared pool capacity", async ({ page }) => {
  122 |   await useAuthenticatedRole(page, "user");
  123 |   await page.route("**/api/crawler-accounts", route => route.fulfill({
  124 |     json: {
  125 |       items: [account("private-alice", "Alice 私有账号", "dy")],
  126 |       shared_pool: {
  127 |         total: 3,
  128 |         ready: 1,
  129 |         busy: 1,
  130 |         unavailable: 1,
  131 |         by_platform: {
  132 |           dy: { total: 3, ready: 1, busy: 1, unavailable: 1 }
  133 |         }
  134 |       }
  135 |     }
  136 |   }));
  137 | 
  138 |   await page.goto("/crawler-accounts");
  139 | 
  140 |   await expect(page.getByRole("button", { name: "添加私有账号" })).toBeVisible();
> 141 |   await expect(page.getByText("Alice 私有账号", { exact: true })).toBeVisible();
      |                                                               ^ Error: expect(locator).toBeVisible() failed
  142 |   await expect(page.getByLabel("公共账号池概况")).toContainText("公共账号总数3");
  143 |   await expect(page.getByLabel("公共账号池概况")).toContainText("当前空闲1");
  144 |   await expect(page.getByLabel("公共账号池概况")).toContainText("正在使用1");
  145 |   await expect(page.getByLabel("公共账号池概况")).toContainText("不可用1");
  146 |   await expect(page.getByText("公共账号明细", { exact: true })).toHaveCount(0);
  147 | });
  148 | 
  149 | test("administrator switches between clearly separated public and private pools", async ({ page }) => {
  150 |   await useAuthenticatedRole(page, "admin");
  151 |   await page.route("**/api/crawler-accounts", route => route.fulfill({
  152 |     json: {
  153 |       items: [
  154 |         { ...account("public-account", "公共采集账号", "dy"), access_scope: "public", can_manage: true },
  155 |         { ...account("private-account", "管理员私有账号", "dy"), access_scope: "private", can_manage: true }
  156 |       ],
  157 |       shared_pool: { total: 1, ready: 1, busy: 0, unavailable: 0 }
  158 |     }
  159 |   }));
  160 | 
  161 |   await page.goto("/crawler-accounts");
  162 | 
  163 |   await expect(page.getByRole("tab", { name: /公共账号池 1/ })).toBeVisible();
  164 |   await expect(page.getByRole("tab", { name: /我的私有账号 1/ })).toBeVisible();
  165 |   await expect(page.locator(".crawler-account-table-row").filter({ hasText: "管理员私有账号" })).toBeVisible();
  166 |   await expect(page.locator(".crawler-account-table-row").filter({ hasText: "公共采集账号" })).toHaveCount(0);
  167 | 
  168 |   await page.getByRole("tab", { name: /公共账号池 1/ }).click();
  169 | 
  170 |   await expect(page.locator(".crawler-account-table-row").filter({ hasText: "公共采集账号" })).toBeVisible();
  171 |   await expect(page.locator(".crawler-account-table-row").filter({ hasText: "管理员私有账号" })).toHaveCount(0);
  172 | });
  173 | 
  174 | test("administrator can create a public pool account with explicit scope", async ({ page }) => {
  175 |   await useAuthenticatedRole(page, "admin");
  176 |   let createdBody: Record<string, unknown> | undefined;
  177 |   await page.route("**/api/crawler-accounts", async route => {
  178 |     if (route.request().method() === "POST") {
  179 |       createdBody = route.request().postDataJSON();
  180 |       return route.fulfill({
  181 |         status: 201,
  182 |         json: {
  183 |           item: {
  184 |             ...account("public-new", "新公共账号", "dy"),
  185 |             access_scope: "public",
  186 |             can_manage: true
  187 |           }
  188 |         }
  189 |       });
  190 |     }
  191 |     return route.fulfill({
  192 |       json: {
  193 |         items: [],
  194 |         shared_pool: { total: 0, ready: 0, busy: 0, unavailable: 0 }
  195 |       }
  196 |     });
  197 |   });
  198 | 
  199 |   await page.goto("/crawler-accounts");
  200 |   await page.getByRole("button", { name: "添加采集账号" }).click();
  201 |   await page.getByLabel("所属平台").selectOption("dy");
  202 |   await page.getByRole("radio", { name: /公共账号池/ }).check();
  203 |   await page.getByLabel("账号名称").fill("新公共账号");
  204 |   await page.getByRole("button", { name: "保存账号" }).click();
  205 | 
  206 |   await expect(page.getByText("新公共账号", { exact: true })).toBeVisible();
  207 |   expect(createdBody).toMatchObject({
  208 |     platform: "dy",
  209 |     display_name: "新公共账号",
  210 |     access_scope: "public"
  211 |   });
  212 | });
  213 | 
  214 | test("empty response remains a real empty state", async ({ page }) => {
  215 |   await page.route("**/api/crawler-accounts", (route) => route.fulfill({ json: { items: [] } }));
  216 |   await page.goto("/crawler-accounts");
  217 | 
  218 |   await expect(page.getByText("暂无采集账号", { exact: true })).toBeVisible();
  219 |   await expect(page.locator(".crawler-account-table-row")).toHaveCount(0);
  220 |   await expect(page.getByText(/账号一|账号二|账号三|账号四|账号五|账号六/)).toHaveCount(0);
  221 | });
  222 | 
  223 | test("API failure is explicit and retry reloads the authoritative list", async ({ page }) => {
  224 |   let attempts = 0;
  225 |   let allowSuccess = false;
  226 |   await page.route("**/api/crawler-accounts", async (route) => {
  227 |     attempts += 1;
  228 |     if (!allowSuccess) {
  229 |       await route.fulfill({ status: 500, json: { detail: "账号服务暂不可用" } });
  230 |       return;
  231 |     }
  232 |     await route.fulfill({ json: { items: [account("account-after-retry", "重试后账号", "xhs")] } });
  233 |   });
  234 |   await page.goto("/crawler-accounts");
  235 | 
  236 |   await expect(page.getByText("账号服务暂不可用", { exact: true })).toBeVisible();
  237 |   const failedAttempts = attempts;
  238 |   allowSuccess = true;
  239 |   await page.getByRole("button", { name: "重试" }).click();
  240 |   await expect(page.getByText("重试后账号", { exact: true })).toBeVisible();
  241 |   expect(attempts).toBeGreaterThan(failedAttempts);
```