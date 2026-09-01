import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import {
  cancelCrawlerAccountLoginSession,
  createCrawlerAccount,
  deleteCrawlerAccount,
  fetchCrawlerAccountLoginSession,
  fetchCrawlerAccounts,
  startCrawlerAccountLogin,
  updateCrawlerAccount
} from "../src/services/crawlerAccounts";

const apiAccount = (id: string, displayName: string) => ({
  id,
  platform: "xhs" as const,
  display_name: displayName,
  platform_account_id: `${id}-platform`,
  status: "active" as const,
  last_validated_at: "2026-08-31T00:00:00Z",
  last_used_at: "2026-08-31T00:01:00Z",
  last_error: "",
  has_auth_state: true,
  auth_state_updated_at: "2026-08-31T00:00:00Z",
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-31T00:00:00Z"
});

const apiSession = (status: string, qrImageDataUrl = "") => ({
  id: "session-real-1",
  account_id: "account-real-1",
  platform: "xhs" as const,
  status,
  qr_image_data_url: qrImageDataUrl,
  qr_expires_at: "2026-08-31T00:05:00Z",
  finalizing_started_at: status === "finalizing" ? "2026-08-31T00:01:00Z" : "",
  finalizing_duration_seconds: status === "finalizing" ? 3 : 0,
  platform_account_id: status === "success" ? "real-platform-account" : "",
  error: status === "failed" ? "平台拒绝登录" : "",
  created_at: "2026-08-31T00:00:00Z",
  updated_at: "2026-08-31T00:01:00Z",
  expires_at: "2026-08-31T00:05:00Z"
});

test.beforeEach(() => {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      XHS_AUDIT_API_BASE: "http://api.test",
      location: { origin: "http://ui.test" }
    }
  });
});

test("maps exactly two real API accounts", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    items: [apiAccount("account-real-1", "真实账号一"), apiAccount("account-real-2", "真实账号二")]
  }), { status: 200, headers: { "Content-Type": "application/json" } });

  const accounts = await fetchCrawlerAccounts();
  expect(accounts).toHaveLength(2);
  expect(accounts.map((account) => account.displayName)).toEqual(["真实账号一", "真实账号二"]);
  expect(accounts.map((account) => account.id)).toEqual(["account-real-1", "account-real-2"]);
});

test("an empty API list remains empty and authoritative sources contain no account fallback", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ items: [] }), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
  expect(await fetchCrawlerAccounts()).toEqual([]);

  const serviceSource = readFileSync(new URL("../src/services/crawlerAccounts.ts", import.meta.url), "utf8");
  const pageSource = readFileSync(new URL("../src/features/crawler-accounts/CrawlerAccountsPage.tsx", import.meta.url), "utf8");
  expect(serviceSource).not.toContain("DEFAULT_MOCK_CRAWLER_ACCOUNTS");
  expect(serviceSource).not.toContain("Math.random");
  expect(serviceSource).not.toContain("data:image/svg+xml");
  expect(pageSource).toContain('title={accounts.length ? "暂无匹配账号" : "暂无采集账号"}');
  expect(pageSource).toContain("<ErrorState");
});

test("list failures remain explicit for API errors and network errors", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "账号服务暂不可用" }), {
    status: 500,
    headers: { "Content-Type": "application/json" }
  });
  await expect(fetchCrawlerAccounts()).rejects.toThrow("账号服务暂不可用");

  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await expect(fetchCrawlerAccounts()).rejects.toThrow("Failed to fetch");
});

test("create update and delete failures cannot mutate a local fallback", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "write rejected" }), {
    status: 500,
    headers: { "Content-Type": "application/json" }
  });
  await expect(createCrawlerAccount({
    platform: "xhs", displayName: "不得伪造", platformAccountId: ""
  })).rejects.toThrow("write rejected");
  await expect(updateCrawlerAccount("account-real-1", {
    displayName: "不得本地更新"
  })).rejects.toThrow("write rejected");
  await expect(deleteCrawlerAccount("account-real-1")).rejects.toThrow("write rejected");

  globalThis.fetch = async () => new Response(JSON.stringify({ items: [] }), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
  expect(await fetchCrawlerAccounts()).toEqual([]);
});

test("login failures return no fabricated QR and cancellation failures stay explicit", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "登录服务未就绪" }), {
    status: 503,
    headers: { "Content-Type": "application/json" }
  });
  await expect(startCrawlerAccountLogin("account-real-1")).rejects.toThrow("登录服务未就绪");
  await expect(fetchCrawlerAccountLoginSession("session-real-1")).rejects.toThrow("登录服务未就绪");
  await expect(cancelCrawlerAccountLoginSession("session-real-1")).rejects.toThrow("登录服务未就绪");
});

test("QR and every login status come only from API responses", async () => {
  const qr = "data:image/png;base64,REAL_BACKEND_QR";
  globalThis.fetch = async () => new Response(JSON.stringify({
    item: apiSession("waiting_scan", qr)
  }), { status: 201, headers: { "Content-Type": "application/json" } });
  const started = await startCrawlerAccountLogin("account-real-1");
  expect(started.qrImageDataUrl).toBe(qr);
  expect(started.status).toBe("waiting_scan");

  for (const status of ["waiting_scan", "finalizing", "success", "expired", "failed", "cancelled"] as const) {
    globalThis.fetch = async () => new Response(JSON.stringify({ item: apiSession(status) }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
    const session = await fetchCrawlerAccountLoginSession("session-real-1");
    expect(session.status).toBe(status);
    expect(session.id).toBe("session-real-1");
  }
});
