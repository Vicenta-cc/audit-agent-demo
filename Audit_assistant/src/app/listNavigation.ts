import { useEffect } from "react";

const listScrollPrefix = "xhs-audit:list-scroll:";

export const pageSizeOptions = [10, 20, 50] as const;

export interface ReturnNavigationState {
  returnTo: string;
  returnLabel: string;
  returnTitle: string;
}

export function readEnumParam<Value extends string>(
  params: URLSearchParams,
  key: string,
  allowed: readonly Value[],
  fallback: Value
): Value {
  const value = params.get(key);
  return value && allowed.includes(value as Value) ? value as Value : fallback;
}

export function readPositiveIntParam(params: URLSearchParams, key: string, fallback: number): number {
  const value = Number(params.get(key));
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

export function readPageSizeParam(params: URLSearchParams, fallback: number): number {
  const value = readPositiveIntParam(params, "size", fallback);
  return pageSizeOptions.includes(value as typeof pageSizeOptions[number]) ? value : fallback;
}

export function writeDefaultedParam(
  params: URLSearchParams,
  key: string,
  value: string | number,
  fallback: string | number
) {
  if (value === fallback || value === "") params.delete(key);
  else params.set(key, String(value));
}

export function getListViewUrl(location: { pathname: string; search: string }): string {
  return `${location.pathname}${location.search}`;
}

export function getReturnNavigationState(value: unknown): ReturnNavigationState | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ReturnNavigationState>;
  if (
    typeof candidate.returnTo !== "string" ||
    !candidate.returnTo.startsWith("/") ||
    candidate.returnTo.startsWith("//") ||
    typeof candidate.returnLabel !== "string" ||
    typeof candidate.returnTitle !== "string"
  ) {
    return null;
  }
  return {
    returnTo: candidate.returnTo,
    returnLabel: candidate.returnLabel,
    returnTitle: candidate.returnTitle
  };
}

export function rememberListScroll(viewUrl: string) {
  try {
    window.sessionStorage.setItem(`${listScrollPrefix}${viewUrl}`, String(window.scrollY));
  } catch {
    // Navigation still works when session storage is unavailable.
  }
}

export function useRestoreListScroll(viewUrl: string, ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    let value: string | null = null;
    try {
      value = window.sessionStorage.getItem(`${listScrollPrefix}${viewUrl}`);
      if (value !== null) window.sessionStorage.removeItem(`${listScrollPrefix}${viewUrl}`);
    } catch {
      return;
    }
    if (value === null) return;
    const scrollTop = Number(value);
    if (!Number.isFinite(scrollTop) || scrollTop < 0) return;
    const frame = window.requestAnimationFrame(() => window.scrollTo({ top: scrollTop, behavior: "auto" }));
    return () => window.cancelAnimationFrame(frame);
  }, [ready, viewUrl]);
}
