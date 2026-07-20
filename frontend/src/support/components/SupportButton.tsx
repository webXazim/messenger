import type { ButtonHTMLAttributes, ReactNode } from "react";
import { supportClassNames } from "../utils/classNames";
import type { SupportSize } from "../types/ui";

interface SupportButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: SupportSize;
  icon?: ReactNode;
  isLoading?: boolean;
}

export function SupportButton({
  variant = "secondary",
  size = "md",
  icon,
  isLoading = false,
  children,
  className,
  disabled,
  type = "button",
  ...props
}: SupportButtonProps) {
  return (
    <button
      type={type}
      className={supportClassNames("sc-button", `sc-button--${variant}`, `sc-button--${size}`, className)}
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      {...props}
    >
      {isLoading ? <span className="sc-button__spinner" aria-hidden="true" /> : icon ? <span className="sc-button__icon">{icon}</span> : null}
      <span>{isLoading ? "Working…" : children}</span>
    </button>
  );
}
