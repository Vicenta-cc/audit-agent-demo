import type { ReactNode } from "react";

type BadgeTone = "blue" | "green" | "orange" | "red" | "gray";

interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
}

export function Badge({ tone = "gray", children }: BadgeProps) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
