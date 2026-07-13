import { useState, type ReactNode } from "react";
import { downloadAttachmentForUser } from "./AuthenticatedMedia";
import type { MessageAttachment } from "../types/chat";

export function AttachmentDownloadButton({
  attachment,
  currentUserId,
  className,
  children,
}: {
  attachment: MessageAttachment;
  currentUserId?: string;
  className?: string;
  children: ReactNode;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <span className="ms-attachment-download-action">
      <button
        type="button"
        className={className}
        disabled={pending}
        aria-label={pending ? `Preparing ${attachment.original_name}` : `Download ${attachment.original_name}`}
        title={pending ? "Preparing download…" : error || "Download"}
        onClick={async () => {
          if (pending) return;
          try {
            setPending(true);
            setError(null);
            await downloadAttachmentForUser(attachment, currentUserId);
          } catch (downloadError) {
            setError(downloadError instanceof Error ? downloadError.message : "Download failed. Try again.");
          } finally {
            setPending(false);
          }
        }}
      >
        {pending ? <span className="ms-attachment-download-action__spinner" aria-hidden="true" /> : children}
      </button>
      {error ? <span className="ms-visually-hidden" role="alert">{error}</span> : null}
    </span>
  );
}
