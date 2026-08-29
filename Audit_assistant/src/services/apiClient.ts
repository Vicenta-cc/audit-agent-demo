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

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const hasJsonBody = Boolean(options.body) && !(options.body instanceof FormData);
  const response = await fetch(`${API_BASE()}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(hasJsonBody ? { "Content-Type": "application/json" } : {}),
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
    throw new ApiError(message, response.status, code, details);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return (await response.json()) as T;
}
