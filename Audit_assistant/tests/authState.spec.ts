import { expect, test } from "@playwright/test";
import {
  activateAuthenticatedUser,
  clearAuthenticationState,
  setCsrfToken
} from "../src/services/apiClient";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, String(value)); }
}

test.beforeEach(() => {
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: storage
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { sessionStorage: storage, location: { origin: "http://localhost" } }
  });
});

test("first authenticated user and account switches clear user-owned caches", () => {
  sessionStorage.setItem("saasv2:monitor-tasks:snapshot:v1", "user-a-jobs");
  sessionStorage.setItem("saasv2:create-monitor-task:draft:v1", "user-a-draft");
  sessionStorage.setItem("crawler-login-control-token", "user-a-token");
  sessionStorage.setItem("crawler-account-login-session:account-a", "session-a");
  sessionStorage.setItem("crawler-account-login-target", "account-a");
  sessionStorage.setItem("xhs-audit:historical-report-pending:workspace-a", "pending-a");
  sessionStorage.setItem("xhs-audit:list-scroll:/tasks", "320");
  sessionStorage.setItem("unrelated-preference", "keep");

  activateAuthenticatedUser("user-a");
  expect(sessionStorage.getItem("saasv2:monitor-tasks:snapshot:v1")).toBeNull();
  expect(sessionStorage.getItem("crawler-account-login-session:account-a")).toBeNull();
  expect(sessionStorage.getItem("unrelated-preference")).toBe("keep");

  sessionStorage.setItem("saasv2:monitor-tasks:snapshot:v1", "fresh-user-a");
  activateAuthenticatedUser("user-a");
  expect(sessionStorage.getItem("saasv2:monitor-tasks:snapshot:v1")).toBe("fresh-user-a");

  activateAuthenticatedUser("user-b");
  expect(sessionStorage.getItem("saasv2:monitor-tasks:snapshot:v1")).toBeNull();
});

test("logout or authentication expiry clears CSRF and user caches", () => {
  activateAuthenticatedUser("user-a");
  setCsrfToken("csrf-a");
  sessionStorage.setItem("xhs-audit:list-scroll:/reports", "100");

  clearAuthenticationState();

  expect(sessionStorage.getItem("xhs-audit-csrf-token")).toBeNull();
  expect(sessionStorage.getItem("xhs-audit-active-user-id:v1")).toBeNull();
  expect(sessionStorage.getItem("xhs-audit:list-scroll:/reports")).toBeNull();
});

test("duplicate task submissions share an intent and network retry reuses it", async () => {
  const { apiRequest } = await import("../src/services/apiClient");
  activateAuthenticatedUser("user-a");
  const originalFetch = globalThis.fetch;
  const keys: string[] = [];
  let failures = 1;
  globalThis.fetch = async (_url, options) => {
    keys.push(new Headers(options?.headers).get("Idempotency-Key") || "");
    if (failures-- > 0) throw new TypeError("network disconnected");
    return new Response(JSON.stringify({ id: "same-job" }), { status: 200 });
  };
  try {
    const options = { method: "POST", body: JSON.stringify({ keyword: "subject" }) };
    const first = apiRequest("/api/jobs", options);
    const duplicate = apiRequest("/api/jobs", options);
    expect(first).toBe(duplicate);
    await expect(first).rejects.toThrow("network disconnected");
    expect(await apiRequest("/api/jobs", options)).toEqual({ id: "same-job" });
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    // A new explicit submission after a successful response is a fresh intent.
    await apiRequest("/api/jobs", options);
    expect(keys[2]).not.toBe(keys[0]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
