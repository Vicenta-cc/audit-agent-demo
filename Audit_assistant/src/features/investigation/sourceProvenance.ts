import type { AuditResult } from "../../types/jobs";

export interface AnalysisSourceProvenance {
  platform: string;
  sourceLabel: string;
  title: string;
  author: string;
  contentId: string;
  analyzedAt: string;
  sourceUrl: string;
}

export function buildAnalysisSourceProvenance(
  result: AuditResult,
  fallback: {
    platform: string;
    contentTitle: string;
    author: string;
    analyzedAt: string;
  }
): AnalysisSourceProvenance {
  const platform = platformLabel(result.platform) || fallback.platform;
  return {
    platform,
    sourceLabel: `${platform}原帖`,
    title: firstText(result.title_zh, result.title, result.content_title) || fallback.contentTitle,
    author: firstText(result.author?.nickname) || fallback.author,
    contentId: firstText(result.note_id, result.content_key, result.content_id) || "未返回",
    analyzedAt: formatSourceTime(result.analyzed_at) || fallback.analyzedAt,
    sourceUrl: normalizePublicSourceUrl(result.url)
  };
}

export function normalizePublicSourceUrl(value: unknown) {
  if (typeof value !== "string" || !value.trim()) return "";
  try {
    const url = new URL(value.trim());
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : "";
  } catch {
    return "";
  }
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function platformLabel(value: unknown) {
  if (typeof value !== "string") return "";
  const normalized = value.trim().toLowerCase();
  return ({ dy: "抖音", xhs: "小红书", ks: "快手" } as Record<string, string>)[normalized]
    || value.trim();
}

function formatSourceTime(value: unknown) {
  if (typeof value !== "string" || !value.trim()) return "";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value.trim();
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(timestamp).replace(/\//g, "-");
}
