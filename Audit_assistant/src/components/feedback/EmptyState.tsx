import type { ReactNode } from "react";
import { SearchX } from "lucide-react";

interface EmptyStateProps {
  title: string;
  description: string;
  children?: ReactNode;
}

export function EmptyState({ title, description, children }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon" aria-hidden="true">
        <SearchX size={34} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {children ? <div className="empty-state-actions">{children}</div> : null}
    </div>
  );
}
