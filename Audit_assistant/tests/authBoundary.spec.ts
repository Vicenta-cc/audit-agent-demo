import { expect, test, type BrowserContext, type Page } from "@playwright/test";
const entry = "/tests/authBoundary.html";
const account = (id = "alice", role = "user") => ({ id, username: id, role, status: "active", activation_mode: "first_login", expires_at: role === "admin" ? null : new Date(Date.now() + 7 * 86400000).toISOString() });
async function server(context: BrowserContext, initial: ReturnType<typeof account> | null = null) {
  const state = { current: initial, users: [account("admin", "admin"), account()], grants: [] as {resource_type: string; resource_id: string; permission: string}[], writes: [] as {path: string; body: any; method: string; csrf?: string; owner?: string}[], active: null as any, config: true };
  await context.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    if (method !== "GET") state.writes.push({path, body, method, csrf: request.headers()["x-csrf-token"], owner: request.headers()["x-application-user"]});
    if (path === "/api/auth/config") return route.fulfill({json: {enabled: state.config}});
    if (path === "/api/auth/login") {
      if (body.password === "expired") return route.fulfill({status:403, json:{detail:{code:"ACCOUNT_EXPIRED"}}});
      if (body.password === "wrong") return route.fulfill({status:401, json:{detail:{code:"INVALID_CREDENTIALS"}}});
      state.current = account(body.username, body.username === "admin" ? "admin" : "user");
      return route.fulfill({json:{user: state.current, csrf_token:"test-csrf"}});
    }
    if (!state.current) return route.fulfill({status:401, json:{detail:{code:"AUTHENTICATION_REQUIRED"}}});
    if (path === "/api/auth/me") return route.fulfill({json:{user:state.current}});
    if (path === "/api/auth/csrf") return route.fulfill({json:{csrf_token:"test-csrf"}});
    if (path === "/api/auth/logout") { state.current = null; return route.fulfill({status:204}); }
    if (path === "/api/me/task-quota") return route.fulfill({json:{enabled:true,used:1,remaining:state.current.role === "admin" ? null : 2,limit:state.current.role === "admin" ? null : 3,unlimited:state.current.role === "admin",legacy_record_count:0,active_task:state.active}});
    if (path === "/api/investigation-workspaces") return route.fulfill({json:{items:[{workspace_session_id:"workspace-a",run_id:"run-a",title:"测试调查",updated_at:new Date().toISOString(),run_status:"QUEUED",presentation_stage:"confirmation"}],has_more:false}});
    if (path === "/api/historical-report-workspaces") return route.fulfill({json:{items:[]}});
    if (path === "/api/crawler-accounts") return route.fulfill({json:{items:[{id:"crawler-a",display_name:"测试采集账号",platform:"dy",status:"active"}]}});
    if (path === "/api/admin/users") {
      if (method === "POST") state.users.push({...account(body.username),expires_at:""});
      return route.fulfill({json:{items:state.users}});
    }
    if (path.endsWith("/grants")) return route.fulfill({json:{items:state.grants}});
    if (path.includes("/grants/")) {
      state.grants = method === "PUT" ? [{resource_type:"crawler-account",resource_id:"crawler-a",permission:body.permission}] : [];
      return route.fulfill(method === "DELETE" ? {status:204} : {json:{}});
    }
    if (method === "PATCH") {
      state.users = state.users.map(user => user.id === path.split("/").pop() ? {...user,...body} : user);
      return route.fulfill({json:{}});
    }
    return route.fulfill({status:404,json:{}});
  });
  return state;
}
async function signIn(page: Page, username = "alice", password = "test-password-123") {
  await page.getByLabel("账号", {exact:true}).fill(username);
  await page.getByLabel("密码", {exact:true}).fill(password);
  await page.getByRole("button",{name:"登录",exact:true}).click();
}
test("login errors, successful login, logout clear data and password", async ({page,context}) => {
  const state = await server(context);
  await page.goto(entry);
  await signIn(page,"alice","wrong");
  await expect(page.getByRole("alert")).toContainText("账号或密码不正确");
  await signIn(page,"alice","expired");
  await expect(page.getByRole("alert")).toContainText("账号已到期");
  await signIn(page);
  await expect(page.getByTestId("owner")).toHaveText("alice");
  await expect(page.getByTestId("rule-seed-count")).toHaveText("0");
  await expect(page.getByRole("link",{name:"应用账号管理"})).toHaveCount(0);
  await page.evaluate(() => sessionStorage.setItem("saasv2:monitor-tasks:snapshot:v1","alice-private"));
  await page.getByRole("button",{name:"退出登录"}).click();
  await expect(page.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("saasv2:monitor-tasks:snapshot:v1"))).toBeNull();
  expect(state.writes.find(x=>x.path.endsWith("logout"))?.csrf).toBe("test-csrf");
  await expect(page.getByLabel("密码",{exact:true})).toHaveValue("");
});
test("expiry and revoked server session remove the mounted workspace", async ({page,context}) => {
  const user = account();
  user.expires_at = new Date(Date.now()+3000).toISOString();
  await server(context,user);
  await page.goto(entry);
  await expect(page.getByTestId("owner")).toHaveText("alice");
  await expect(page.getByRole("alert")).toContainText("账号已到期",{timeout:6000});
  await expect(page.getByTestId("owner")).toHaveCount(0);
});
test("disabled by admin is detected on focus", async ({page,context}) => {
  const state = await server(context,account()); await page.goto(entry);
  await expect(page.getByTestId("owner")).toHaveText("alice");
  state.current = null;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
});
test("login in another tab hides previous user's data; another browser context stays independent", async ({page,context,browser}) => {
  await server(context,account()); await page.goto(entry);
  await expect(page.getByTestId("owner")).toHaveText("alice");
  const isolated = await browser.newContext(); await server(isolated,account("carol"));
  const separate = await isolated.newPage(); await separate.goto("http://127.0.0.1:4173"+entry);
  const second = await context.newPage(); await second.goto(entry);
  await expect(second.getByTestId("owner")).toHaveText("alice");
  await second.getByRole("button",{name:"退出登录"}).click(); await signIn(second,"bob");
  await expect(second.getByTestId("owner")).toHaveText("bob");
  await expect(page.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
  await expect(page.getByTestId("owner")).toHaveCount(0);
  await expect(separate.getByTestId("owner")).toHaveText("carol"); await isolated.close();
});
test("ordinary user cannot render admin controls or load admin data", async ({page,context}) => {
  await server(context,account()); let adminReads=0;
  page.on("request",req=>{if(req.url().includes("/api/admin")) adminReads++;});
  await page.goto(entry);
  await page.evaluate(()=>{history.pushState({},"","/admin/users");window.dispatchEvent(new PopStateEvent("popstate"));});
  await expect(page.getByTestId("owner")).toHaveText("alice");
  await expect(page).toHaveURL(/\/investigation$/);
  expect(adminReads).toBe(0);
});
test("admin logout replaces the protected URL before showing login", async ({page,context}) => {
  await server(context,account("admin","admin"));
  await page.goto(entry);
  await page.getByRole("link",{name:"应用账号管理"}).click();
  await expect(page).toHaveURL(/\/admin\/users$/);
  await page.getByRole("button",{name:"退出登录"}).click();
  await expect(page.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
  await expect(page).toHaveURL(/\/investigation$/);
});
test("ordinary login from an admin URL enters the investigation workspace", async ({page,context}) => {
  await server(context);
  await page.goto(entry);
  await page.evaluate(()=>{history.pushState({},"","/admin/users");window.dispatchEvent(new PopStateEvent("popstate"));});
  await signIn(page,"alice");
  await expect(page.getByTestId("owner")).toHaveText("alice");
  await expect(page).toHaveURL(/\/investigation$/);
});
test("admin manages app accounts without crawler sharing controls", async ({page,context}) => {
  const state = await server(context,account("admin","admin")); await page.goto(entry);
  await page.getByRole("link",{name:"应用账号管理"}).click();
  await page.getByLabel("新账号",{exact:true}).fill("new-user"); await page.getByLabel("初始密码").fill("12345678");
  await expect(page.getByLabel("初始密码")).toHaveAttribute("minlength", "8");
  await page.getByRole("button",{name:"开通七天账号"}).click();
  await expect(page.getByRole("row").filter({hasText:"new-user"})).toContainText("待首次登录");
  expect(state.writes.find(x=>x.path === "/api/admin/users")?.body).toMatchObject({role:"user",validity_days:7,activation_mode:"first_login"});
  const row = page.getByRole("row").filter({hasText:"alice"});
  await row.getByRole("button",{name:"续期七天"}).click(); await page.getByRole("dialog").getByRole("button",{name:"取消",exact:true}).click();
  expect(state.writes.filter(x=>x.method === "PATCH")).toHaveLength(0);
  await row.getByRole("button",{name:"续期七天"}).click(); await page.getByRole("dialog").getByRole("button",{name:"确认",exact:true}).click();
  await expect(page.getByRole("status")).toContainText("操作已保存");
  await row.getByRole("button",{name:"禁用",exact:true}).click(); await page.getByRole("dialog").getByRole("button",{name:"确认",exact:true}).click();
  await expect(row).toContainText("已禁用");
  await row.getByRole("button",{name:"密码设置"}).click();
  await expect(page.getByLabel("用户密码设置",{exact:true})).toBeInViewport();
  await expect(page.getByLabel("用户密码设置",{exact:true})).toBeFocused();
  await expect(page.getByRole("button",{name:"允许使用",exact:true})).toHaveCount(0);
  expect(state.writes.some(x => x.path.includes("/grants/"))).toBe(false);
  await page.getByLabel("新密码",{exact:true}).fill("87654321"); await page.getByRole("button",{name:"重置密码",exact:true}).click();
  await page.getByRole("dialog").getByRole("button",{name:"确认",exact:true}).click();
  await expect(page.getByLabel("新密码",{exact:true})).toHaveValue("");
  expect(state.writes.filter(x=>x.method === "PATCH").map(x=>x.body)).toEqual([{renew_days:7},{status:"disabled"},{password:"87654321"}]);
  expect(state.writes.filter(x=>x.path.startsWith("/api/admin")).every(x=>x.csrf === "test-csrf" && x.owner === "admin")).toBe(true);
  await page.screenshot({path:"/tmp/phase5-admin.png",fullPage:true});
});
test("quota explains resource waiting and finds the exact workspace by run ID", async ({page,context}) => {
  const state = await server(context,account()); state.active={task_id:"run-a",kind:"investigation",queue_state:"QUEUED",decision:"OPEN",waiting_reason:"account_busy"};
  await page.goto(entry); await expect(page.getByLabel("今日任务额度")).toContainText("采集账号正在被使用");
  await page.getByRole("button",{name:"返回当前任务"}).click();
  await expect(page.getByTestId("path")).toHaveText("/investigation/workspace-a");
  state.active.waiting_reason="no_available_authorized_account";
  await page.evaluate(()=>window.dispatchEvent(new Event("task-quota-changed")));
  await expect(page.getByLabel("今日任务额度")).toContainText("重新扫码登录");
});
test("unavailable config fails closed; explicit disabled mode retains legacy workspace", async ({page,context}) => {
  const state=await server(context); state.config=false;
  await page.goto(entry); await expect(page.getByTestId("owner")).toHaveText("legacy");
  await context.route("**/api/auth/config",route=>route.fulfill({status:503,json:{}}));
  await page.reload(); await expect(page.getByRole("heading",{name:"暂时无法连接工作台"})).toBeVisible();
  await expect(page.getByTestId("owner")).toHaveCount(0);
});

test("stream authentication loss closes the stream and gates the workspace immediately", async ({page,context}) => {
  await server(context,account()); await page.goto(entry);
  await expect(page.getByTestId("owner")).toHaveText("alice");
  const stopped = await page.evaluate(async () => {
    const load = (path:string) => import(path);
    const {waitForTurn} = await load("/src/services/investigations.ts");
    const Original = window.EventSource;
    let source: EventTarget;
    let closed = false;
    class FakeSource extends EventTarget { constructor(){super();source=this;} close(){closed=true;} }
    Object.defineProperty(window,"EventSource",{configurable:true,value:FakeSource});
    try {
      const pending = waitForTurn("turn-a",{eventPath:"/api/events",statusPath:"/api/status"}).catch(()=>{});
      source!.dispatchEvent(new MessageEvent("auth_expired",{data:'{"code":"AUTHENTICATION_EXPIRED"}'}));
      await pending; return closed;
    } finally { Object.defineProperty(window,"EventSource",{configurable:true,value:Original}); }
  });
  expect(stopped).toBe(true);
  await expect(page.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
});

test("production app gates all workspace reads until login and keeps the existing workspace shell", async ({page,context}) => {
  await server(context);
  const reads:string[]=[];
  const errors:string[]=[]; page.on("pageerror",error=>errors.push(error.message));
  page.on("request",request=>{const path=new URL(request.url()).pathname;if(path.startsWith("/api/")&&!path.startsWith("/api/auth/"))reads.push(path);});
  await page.goto("/investigation");
  await expect(page.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
  expect(reads).toEqual([]);
  await signIn(page);
  await expect(page.getByLabel("应用登录账号")).toContainText("alice");
  await expect(page.getByLabel("今日任务额度")).toContainText("剩余 2 次");
  await expect(page.getByText("维汉民族关系专项调查",{exact:true})).toHaveCount(0);
  await expect(page.getByText("世界杯博彩专项调查",{exact:true})).toHaveCount(0);
  expect(errors).toEqual([]);
  await page.screenshot({path:"/tmp/phase5-workspace.png",fullPage:true});
});


test("authenticated empty or failed workspace reads never fall back to built-in demo sessions", async ({page,context}) => {
  await server(context,account());
  await context.route("**/api/investigation-workspaces?*",route=>route.fulfill({status:503,json:{}}));
  await page.setViewportSize({width:390,height:844});
  await page.goto("/investigation");
  await expect(page.getByRole("heading",{name:"暂无调查会话"})).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("调查会话读取失败");
  await page.getByRole("button",{name:"展开侧栏",exact:true}).click();
  await expect(page.getByLabel("应用登录账号")).toContainText("alice");
  await expect(page.getByText("维汉民族关系专项调查",{exact:true})).toHaveCount(0);
  await expect(page.getByText("世界杯博彩专项调查",{exact:true})).toHaveCount(0);
});

test("administrator stays signed in without an account deadline", async ({page,context}) => {
  await server(context,account("admin","admin"));
  await page.goto(entry);
  await expect(page.getByText("管理员 · 长期有效")).toBeVisible();
  await page.getByRole("link",{name:"应用账号管理"}).click();
  const row = page.getByRole("row").filter({hasText:"admin"});
  await expect(row).toContainText("长期有效");
  await expect(row.getByRole("button",{name:"续期七天"})).toHaveCount(0);
  await row.getByRole("button",{name:"密码设置",exact:true}).click();
  await expect(page.getByRole("heading",{name:"admin · 密码设置"})).toBeVisible();
  await expect(page.getByRole("button",{name:"允许使用",exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"关闭用户密码设置"}).click();
  await page.getByRole("row").filter({hasText:"alice"}).getByRole("button",{name:"密码设置"}).click();
  await expect(page.getByLabel("用户密码设置",{exact:true})).toBeInViewport();
  await page.getByRole("button",{name:"关闭用户密码设置"}).click();
  await expect(page.getByLabel("用户密码设置",{exact:true})).toHaveCount(0);
  await expect(page.getByRole("row").filter({hasText:"alice"}).getByRole("button",{name:"密码设置"})).toBeFocused();
});


test("admin quota is unlimited and retains active task navigation", async ({page,context}) => {
  const state = await server(context,account("admin","admin"));
  state.active={task_id:"run-a",kind:"investigation",queue_state:"QUEUED",decision:"OPEN",waiting_reason:"account_busy"};
  await page.goto(entry);
  await expect(page.getByLabel("今日任务额度")).toContainText("管理员每日任务次数不限");
  await expect(page.getByLabel("今日任务额度")).not.toContainText("剩余");
  await page.getByRole("button",{name:"返回当前任务"}).click();
  await expect(page.getByTestId("path")).toHaveText("/investigation/workspace-a");
});
