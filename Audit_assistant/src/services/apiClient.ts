type QueryValue = string | number | boolean | null | undefined;

declare global {
  interface Window {
    XHS_AUDIT_API_BASE?: string;
  }
}

export const API_BASE = () => {
  const value = window.XHS_AUDIT_API_BASE || window.location.origin;
  return value.replace(/\/$/, "");
};

const CSRF_STORAGE_KEY = "xhs-audit-csrf-token";
const ACTIVE_USER_STORAGE_KEY = "xhs-audit-active-user-id:v1";
const USER_CACHE_KEYS = new Set([
  "saasv2:monitor-tasks:snapshot:v1",
  "saasv2:create-monitor-task:draft:v1",
  "crawler-login-control-token",
  "crawler-account-login-target"
]);
const USER_CACHE_PREFIXES = [
  "crawler-account-login-session:",
  "xhs-audit:historical-report-pending:",
  "xhs-audit:list-scroll:",
  "xhs-audit:task-intent:"
];

function browserSessionStorage(): Storage | null {
  try {
    return typeof window !== "undefined" && window.sessionStorage
      ? window.sessionStorage
      : null;
  } catch {
    return null;
  }
}

export const setCsrfToken = (token: string) => {
  const storage = browserSessionStorage();
  if (!storage) return;
  if (token) storage.setItem(CSRF_STORAGE_KEY, token);
  else storage.removeItem(CSRF_STORAGE_KEY);
};

function clearUserScopedBrowserState() {
  const storage = browserSessionStorage();
  if (!storage) return;
  const keys: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key) keys.push(key);
  }
  keys.forEach((key) => {
    if (USER_CACHE_KEYS.has(key) || USER_CACHE_PREFIXES.some((prefix) => key.startsWith(prefix))) {
      storage.removeItem(key);
    }
  });
}

export const activateAuthenticatedUser = (userId: string) => {
  const normalized = String(userId || "").trim();
  if (!normalized) {
    clearAuthenticationState();
    return;
  }
  const storage = browserSessionStorage();
  if (!storage) return;
  const previous = storage.getItem(ACTIVE_USER_STORAGE_KEY) || "";
  if (previous !== normalized) {
    clearUserScopedBrowserState();
  }
  storage.setItem(ACTIVE_USER_STORAGE_KEY, normalized);
};

export const clearAuthenticationState = () => {
  const storage = browserSessionStorage();
  clearUserScopedBrowserState();
  storage?.removeItem(CSRF_STORAGE_KEY);
  storage?.removeItem(ACTIVE_USER_STORAGE_KEY);
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "",
    readonly details: Record<string, unknown> = {}
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function withQuery(path: string, query: Record<string, QueryValue> = {}) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

const pendingTaskRequests = new Map<string, Promise<unknown>>();

export function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const taskMutation = String(options.method || "GET").toUpperCase() === "POST" &&
    (path === "/api/jobs" || /\/api\/jobs\/[^/]+\/control$/.test(path) ||
      /\/api\/tasks\/[^/]+\/retry-report$/.test(path));
  if (!taskMutation) return apiRequestOnce<T>(path, options);
  const headers = new Headers(options.headers);
  const user = browserSessionStorage()?.getItem(ACTIVE_USER_STORAGE_KEY) || "";
  const identity = JSON.stringify([user, path, options.body, headers.get("Idempotency-Key")]);
  const pending = pendingTaskRequests.get(identity);
  if (pending) return pending as Promise<T>;
  const request = (async () => {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(identity));
    const storageKey = "xhs-audit:task-intent:" + Array.from(new Uint8Array(digest),
      value => value.toString(16).padStart(2, "0")).join("");
    const storage = browserSessionStorage();
    const key = headers.get("Idempotency-Key") || storage?.getItem(storageKey) || crypto.randomUUID();
    storage?.setItem(storageKey, key);
    headers.set("Idempotency-Key", key);
    try {
      const result = await apiRequestOnce<T>(path, { ...options, headers: Object.fromEntries(headers) });
      storage?.removeItem(storageKey);
      return result;
    } catch (error) {
      // An uncertain network/server result keeps its intent across retry/refresh.
      if (error instanceof ApiError && error.status < 500) storage?.removeItem(storageKey);
      throw error;
    }
  })().finally(() => pendingTaskRequests.delete(identity));
  pendingTaskRequests.set(identity, request);
  return request;
}

async function apiRequestOnce<T>(path: string, options: RequestInit = {}): Promise<T> {
  const hasJsonBody = Boolean(options.body) && !(options.body instanceof FormData);
  const method = String(options.method || "GET").toUpperCase();
  const csrfToken = browserSessionStorage()?.getItem(CSRF_STORAGE_KEY) || "";
  const requiresCsrf = ["POST", "PUT", "PATCH", "DELETE"].includes(method);
  const response = await fetch(`${API_BASE()}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(hasJsonBody ? { "Content-Type": "application/json" } : {}),
      ...(requiresCsrf && csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...options.headers
    }
  });

  if (!response.ok) {
    const text = await response.text();
    let message = text || `${response.status} ${response.statusText}`;
    let code = "";
    let details: Record<string, unknown> = {};
    try {
      const payload = JSON.parse(text) as {
        detail?: string | { code?: string; message?: string; details?: Record<string, unknown> };
      };
      if (typeof payload.detail === "string") message = payload.detail;
      else if (payload.detail) {
        message = payload.detail.message || message;
        code = payload.detail.code || "";
        details = payload.detail.details || {};
      }
    } catch {
      // Keep the server's plain-text error as the public message.
    }
    if (response.status === 401 || code === "ACCOUNT_EXPIRED") {
      clearAuthenticationState();
    }
    throw new ApiError(message, response.status, code, details);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return (await response.json()) as T;
}
