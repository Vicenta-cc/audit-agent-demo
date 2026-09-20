// Opt-in against serve_acceptance.py only. Real HTTP API + DB, no route mocks.
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
const password = "Local-acceptance-only-2026";
async function login(page: Page, username: string, pw = password, path = "/crawler-accounts") {
 await page.goto(path);
 await page.getByLabel("账号", {exact:true}).fill(username);
 await page.getByLabel("密码", {exact:true}).fill(pw);
 await page.getByRole("button", {name:"登录", exact:true}).click();
}
async function csrf(page: Page) {
 const response = await page.request.get("/api/auth/csrf");
 expect(response.status()).toBe(200);
 return {"X-CSRF-Token":(await response.json()).csrf_token};
}
test("real browser/API: private accounts, admin lifecycle, expiry, CSRF and cross-tab logout", async ({browser}) => {
 const adminContext=await browser.newContext(), aliceContext=await browser.newContext(), bobContext=await browser.newContext();
 const admin=await adminContext.newPage(), alice=await aliceContext.newPage(), bob=await bobContext.newPage();
 const errors:string[]=[];
 for (const p of [admin,alice,bob]) p.on("pageerror", e=>errors.push(e.message));
 const suffix=Date.now().toString();
 try {
  await login(admin,"accept-admin",password,"/admin/users");
  await expect(admin.getByRole("heading",{name:"应用账号管理",exact:true})).toBeVisible();
  await expect(admin.getByRole("row").filter({hasText:"accept-admin"})).toContainText("长期有效");
  const username="accept-new-"+suffix;
  await admin.getByLabel("新账号",{exact:true}).fill(username);
  await admin.getByLabel("初始密码").fill(password);
  await admin.getByRole("button",{name:"开通七天账号"}).click();
  const row=admin.getByRole("row").filter({hasText:username});
  await expect(row).toContainText("待首次登录");
  await login(alice,username);
  await login(bob,"accept-bob");
  for (const page of [alice,bob]) await expect(page.getByRole("heading",{name:"采集账号",exact:true})).toBeVisible();
  const ah=await csrf(alice), bh=await csrf(bob), mh=await csrf(admin);
  const aUser=(await (await alice.request.get("/api/auth/me")).json()).user;
  expect(Date.parse(aUser.expires_at)-Date.parse(aUser.validity_started_at)).toBe(7*86400000);
  const own=await alice.request.post("/api/crawler-accounts",{headers:ah,data:{platform:"dy",display_name:"Alice-private-"+suffix}});
  expect(own.status()).toBe(201); const account=(await own.json()).item;
  expect((await bob.request.patch(`/api/crawler-accounts/${account.id}`,{headers:bh,data:{display_name:"hijack"}})).status()).toBe(404);
  expect((await admin.request.delete(`/api/crawler-accounts/${account.id}`,{headers:mh})).status()).toBe(404);
  expect((await bob.request.post(`/api/crawler-accounts/${account.id}/login-sessions`,{headers:bh})).status()).toBe(404);
  expect((await alice.request.post("/api/crawler-accounts",{data:{platform:"dy",display_name:"no-csrf"}})).status()).toBe(403);
  await alice.reload();
  await expect(alice.getByText(account.display_name,{exact:true})).toBeVisible();
  await bob.reload(); await expect(bob.getByText(account.display_name,{exact:true})).toHaveCount(0);
  await alice.getByRole("button",{name:`停用${account.display_name}`,exact:true}).click();
  await expect(alice.getByRole("button",{name:`启用${account.display_name}`,exact:true})).toBeVisible();
  await alice.reload(); await expect(alice.getByRole("button",{name:`启用${account.display_name}`,exact:true})).toBeVisible();
  await alice.goto("/admin/users"); await expect(alice.getByRole("heading",{name:"此页面仅限管理员"})).toBeVisible();
  expect((await alice.request.get("/api/admin/users")).status()).toBe(403);
  await admin.getByRole("button",{name:"刷新列表",exact:true}).click();
  await expect(row).toContainText("有效");
  await row.getByRole("button",{name:"禁用",exact:true}).click();
  await admin.getByRole("dialog").getByRole("button",{name:"确认",exact:true}).click();
  await expect(row).toContainText("已禁用");
  await alice.reload(); await expect(alice.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
  expect((await alice.request.get("/api/crawler-accounts")).status()).toBe(401);
  await row.getByRole("button",{name:"启用",exact:true}).click();
  await admin.getByRole("dialog").getByRole("button",{name:"确认",exact:true}).click();
  await expect(row.getByRole("button",{name:"禁用",exact:true})).toBeVisible();
  await row.getByRole("button",{name:"密码设置",exact:true}).click();
  await admin.getByLabel("新密码",{exact:true}).fill(password+"-reset");
  await admin.getByRole("button",{name:"重置密码",exact:true}).click();
  await admin.getByRole("dialog").getByRole("button",{name:"确认",exact:true}).click();
  await expect(admin.getByRole("region",{name:"用户密码设置",exact:true})).toContainText("操作已保存");
  await login(alice,username); await expect(alice.getByRole("alert")).toContainText("账号或密码不正确");
  await login(alice,username,password+"-reset");
  await expect(alice.getByText(account.display_name,{exact:true})).toBeVisible();
  // Test time travel only inside the dedicated disposable acceptance database.
  execFileSync("/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-investigation-report-integration/.venv/bin/python",["-c", "import sqlite3,sys; db=sqlite3.connect('/Users/ext.wanghongtao6/Documents/Codex/runtimes/xhs-audit-agent-multi-user-v1-20260920/full-acceptance/data/investigation_creation.sqlite3'); db.execute(\"UPDATE app_users SET expires_at='2020-01-01T00:00:00+00:00' WHERE id=? AND username LIKE 'accept-new-%'\",(sys.argv[1],)); db.execute(\"UPDATE login_sessions SET expires_at='2020-01-01T00:00:00+00:00' WHERE user_id=?\",(sys.argv[1],)); db.commit()",aUser.id]);
  await alice.reload(); await expect(alice.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
  await login(alice,username,password+"-reset"); await expect(alice.getByRole("alert")).toContainText("账号已到期");
  await row.getByRole("button",{name:"续期七天",exact:true}).click();
  await admin.getByRole("dialog").getByRole("button",{name:"确认",exact:true}).click();
  await expect(admin.getByRole("region",{name:"用户密码设置",exact:true})).toContainText("操作已保存");
  await login(alice,username,password+"-reset"); await expect(alice.getByText(account.display_name,{exact:true})).toBeVisible();
  await alice.goto("/investigation");
  const second=await aliceContext.newPage(); await second.goto("http://127.0.0.1:3398/investigation");
  await expect(second.locator("summary").filter({hasText:username})).toBeVisible();
  await alice.locator("summary").filter({hasText:username}).click();
  await alice.getByRole("button",{name:"退出登录",exact:true}).click();
  await expect(second.getByRole("heading",{name:"登录调查工作区"})).toBeVisible();
  expect((await bob.request.get("/api/auth/me")).status()).toBe(200);
  expect(errors).toEqual([]);
 } finally { await Promise.all([adminContext.close(),aliceContext.close(),bobContext.close()]); }
});
