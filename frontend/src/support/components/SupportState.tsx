import type { ReactNode } from "react";
import { SupportButton } from "./SupportButton";

interface SupportStateProps {
  kind?: "loading" | "empty" | "error";
  title: ReactNode;
  description?: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
}

export function SupportState({ kind = "empty", title, description, actionLabel, onAction }: SupportStateProps) {
  return (
    <div className={`sc-state sc-state--${kind}`} role={kind === "error" ? "alert" : "status"} aria-live="polite">
      <span className="sc-state__icon" aria-hidden="true">{kind === "error" ? "!" : kind === "loading" ? "" : "○"}</span>
      <strong>{title}</strong>
      {description ? <p>{description}</p> : null}
      {actionLabel && onAction ? <SupportButton onClick={onAction}>{actionLabel}</SupportButton> : null}
    </div>
  );
}
