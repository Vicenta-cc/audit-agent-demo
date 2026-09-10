import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "medium" | "small";
  loading?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "medium",
  loading = false,
  disabled,
  children,
  className = "",
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      className={`mt-button mt-button-${variant} mt-button-${size}${className ? ` ${className}` : ""}`}
      disabled={disabled || loading}
    >
      {loading ? <Loader2 className="spin" size={16} /> : null}
      {children}
    </button>
  );
}
