import type { Message } from "../../types/chat";

function ReceiptIcon({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  if (normalized === "failed" || normalized === "sending") return null;
  const isDelivered = normalized === "delivered" || normalized === "read";
  const label = normalized === "read" ? "Read" : isDelivered ? "Delivered" : "Sent";
  return (
    <span className={`ms-message-receipt ms-message-receipt--${normalized}`} aria-label={label} title={label}>
      <svg viewBox="0 0 18 16" aria-hidden="true">
        <path d="M1.8 8.4 4.8 11l4-5" />
        {isDelivered ? <path d="M7 8.4 10 11l4-5" /> : null}
      </svg>
    </span>
  );
}

export function MessageMeta({
  message,
  own,
  receiptStatus,
  receiptSummary,
  onRetry,
  actionError,
  actionPending = false,
}: {
  message: Message;
  own: boolean;
  receiptStatus: string;
  receiptSummary: string;
  onRetry?: (message: Message) => void;
  actionError?: string | null;
  actionPending?: boolean;
}) {
  const normalizedStatus = receiptStatus.toLowerCase();
  const failed = normalizedStatus === "failed";
  const sending = normalizedStatus === "sending";
  const statusLabel = sending ? "Sending…" : actionPending ? "Updating…" : "";

  return (
    <div className="ms-message-meta">
      <time dateTime={message.created_at}>{new Date(message.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
      {statusLabel ? <span className="ms-message-meta__pending" role="status">{statusLabel}</span> : null}
      {own && normalizedStatus ? <ReceiptIcon status={normalizedStatus} /> : null}
      {own && receiptSummary ? <span className="ms-message-meta__summary" title={receiptSummary}>{receiptSummary}</span> : null}
      {message.failed_reason ? <span className="ms-message-meta__failure" role="alert">{message.failed_reason}</span> : null}
      {actionError ? <span className="ms-message-meta__failure" role="alert">{actionError}</span> : null}
      {own && failed && onRetry ? <button type="button" disabled={actionPending} onClick={() => onRetry(message)}>Retry</button> : null}
    </div>
  );
}
