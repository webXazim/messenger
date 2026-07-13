import { useId, useRef } from "react";
import { useModalAccessibility } from "../hooks/useModalAccessibility";

export type ConfirmDialogProps = {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
  pending?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onClose: () => void;
};

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  tone = "default",
  pending = false,
  error,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const errorId = useId();
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useModalAccessibility<HTMLElement>({
    open,
    onClose,
    initialFocusRef: cancelRef,
    closeOnEscape: !pending,
  });

  if (!open) return null;

  return (
    <div
      className="ms-modal-backdrop ms-confirm-dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="ms-confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={`${descriptionId}${error ? ` ${errorId}` : ""}`}
        aria-busy={pending}
        tabIndex={-1}
      >
        <div className={`ms-confirm-dialog__icon ${tone === "danger" ? "is-danger" : ""}`} aria-hidden="true">
          {tone === "danger" ? "!" : "?"}
        </div>
        <div className="ms-confirm-dialog__copy">
          <h3 id={titleId}>{title}</h3>
          <p id={descriptionId}>{description}</p>
          {error ? <div id={errorId} className="ms-confirm-dialog__error" role="alert">{error}</div> : null}
        </div>
        <div className="ms-confirm-dialog__actions">
          <button ref={cancelRef} type="button" className="ms-button ms-button--ghost" disabled={pending} onClick={onClose}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`ms-button ${tone === "danger" ? "ms-button--danger" : "ms-button--primary"}`}
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? "Please wait…" : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
