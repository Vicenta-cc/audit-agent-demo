import type { ReactNode } from "react";

export type StatusTone = "success" | "warning" | "danger" | "info" | "neutral";

interface StatusTagProps {
  tone?: StatusTone;
  children: ReactNode;
}

export function StatusTag({ tone = "neutral", children }: StatusTagProps) {
  return <span className={`status-tag status-tag-${tone}`}>{children}</span>;
}
