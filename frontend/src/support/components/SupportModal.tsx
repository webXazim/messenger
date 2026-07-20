import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { SupportButton } from "./SupportButton";

interface SupportModalProps {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  onClose: () => void;
  primaryAction?: { label: string; onClick: () => void; disabled?: boolean; isLoading?: boolean };
  secondaryAction?: { label: string; onClick?: () => void };
  danger?: boolean;
  size?: "md" | "lg";
}

export function SupportModal({ open, title, description, children, onClose, primaryAction, secondaryAction, danger = false, size = "md" }: SupportModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKeyDown);
    window.requestAnimationFrame(() => dialogRef.current?.focus());
    return () => { document.removeEventListener("keydown", onKeyDown); previous?.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="sc-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className={`sc-modal sc-modal--${size}`} role="dialog" aria-modal="true" aria-labelledby="sc-modal-title" tabIndex={-1} ref={dialogRef}>
        <header><div><h2 id="sc-modal-title">{title}</h2>{description ? <p>{description}</p> : null}</div><button type="button" aria-label="Close" onClick={onClose}>×</button></header>
        <div className="sc-modal__body">{children}</div>
        {(primaryAction || secondaryAction) ? <footer>{secondaryAction ? <SupportButton onClick={secondaryAction.onClick || onClose}>{secondaryAction.label}</SupportButton> : null}{primaryAction ? <SupportButton variant={danger ? "danger" : "primary"} onClick={primaryAction.onClick} disabled={primaryAction.disabled} isLoading={primaryAction.isLoading}>{primaryAction.label}</SupportButton> : null}</footer> : null}
      </div>
    </div>, document.body,
  );
}
