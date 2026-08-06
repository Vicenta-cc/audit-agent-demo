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
    throw new Error(text || `${response.status} ${response.statusText}`);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return (await response.json()) as T;
}
