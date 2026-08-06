import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

interface DropdownMenuProps {
  open: boolean;
  onClose: () => void;
  trigger: ReactNode;
  children: ReactNode;
}

export function DropdownMenu({ open, onClose, trigger, children }: DropdownMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) {
        onClose();
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onClose]);

  return (
    <div className="mt-dropdown" ref={ref}>
      {trigger}
      {open ? <div className="mt-dropdown-panel">{children}</div> : null}
    </div>
  );
}
