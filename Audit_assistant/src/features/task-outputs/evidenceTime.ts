import type { AuditEvidenceGroupItem } from "../../types/jobs";

export function evidenceSeconds(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

export function formatEvidenceTime(value: unknown): string {
  const seconds = evidenceSeconds(value);
  if (seconds === null) return "--";
  const rounded = Math.floor(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const rest = rounded % 60;
  const clock = [minutes, rest].map(part => String(part).padStart(2, "0")).join(":");
  return hours > 0 ? `${String(hours).padStart(2, "0")}:${clock}` : clock;
}

export function formatEvidenceRange(start: unknown, end: unknown): string {
  const from = evidenceSeconds(start);
  const to = evidenceSeconds(end);
  if (from !== null && to !== null && to < from) return "--";
  const startText = formatEvidenceTime(from);
  const endText = formatEvidenceTime(to);
  if (startText === "--") return endText;
  return endText === "--" || startText === endText ? startText : `${startText} - ${endText}`;
}

export function frameTimeRange(evidence: AuditEvidenceGroupItem): { start: number | null; end: number | null } {
  // Frame numbers are identifiers, not seconds. Prefer the actual frame time,
  // then the evidence interval, then the saved OCR context's interval.
  const timestamp = evidenceSeconds(evidence.timestamp);
  if (timestamp !== null) return { start: timestamp, end: null };
  const start = evidenceSeconds(evidence.start);
  const end = evidenceSeconds(evidence.end);
  if ((start !== null || end !== null) && !(start !== null && end !== null && end < start)) return { start, end };
  if (Array.isArray(evidence.ocr_context)) {
    const times = evidence.ocr_context.flatMap(item => {
      if (!item || typeof item !== "object") return [];
      const start = evidenceSeconds(item.start);
      const end = evidenceSeconds(item.end);
      if (start !== null && end !== null && end < start) return [];
      return [start, end].filter((value): value is number => value !== null);
    });
    if (times.length) return { start: Math.min(...times), end: Math.max(...times) };
  }
  return { start: null, end: null };
}

export function formatFramePosition(evidence: AuditEvidenceGroupItem): string {
  const { start, end } = frameTimeRange(evidence);
  const time = formatEvidenceRange(start, end);
  const frame = evidenceSeconds(evidence.frame_number);
  return `${time === "--" ? "时间未知" : time}${frame !== null && Number.isInteger(frame) ? ` · 帧 ${frame}` : ""}`;
}
