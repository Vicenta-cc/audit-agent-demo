import type { InvestigationSession } from "../../types/investigation";

function sortableTime(value?: string) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

export function latestSessionTimestamp(...values: Array<string | undefined | null>) {
  return values.reduce<string>((latest, value) => (
    sortableTime(value || undefined) > sortableTime(latest) ? String(value) : latest
  ), "");
}

export function formatSessionTimestamp(value?: string, fallback = "刚刚") {
  if (!value || !sortableTime(value)) return fallback;
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function sortInvestigationSessions(sessions: InvestigationSession[]) {
  return [...sessions].sort((left, right) => {
    if (!left.updatedAtIso && !right.updatedAtIso) return 0;
    const timeDelta = sortableTime(right.updatedAtIso) - sortableTime(left.updatedAtIso);
    if (timeDelta) return timeDelta;
    return right.id.localeCompare(left.id);
  });
}
