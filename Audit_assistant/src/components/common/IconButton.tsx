import type { ButtonHTMLAttributes, ReactNode } from "react";

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
}

export function IconButton({ children, className = "", ...props }: IconButtonProps) {
  return (
    <button {...props} className={`mt-icon-button${className ? ` ${className}` : ""}`}>
      {children}
    </button>
  );
}
