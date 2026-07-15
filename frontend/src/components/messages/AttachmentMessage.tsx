import type { MessageAttachment } from "../../types/chat";
import { AttachmentDownloadButton } from "../AttachmentDownloadButton";
import { attachmentLabel, attachmentTone, formatFileSize } from "./messagePresentation";

function DownloadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 20h14" /></svg>;
}

function ViewIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>;
}

export function AttachmentMessage({ attachments, currentUserId, onPreviewAttachment }: { attachments: MessageAttachment[]; currentUserId?: string; onPreviewAttachment?: (attachmentId: string) => void }) {
  if (!attachments.length) return null;
  return (
    <div className="ms-file-message-list">
      {attachments.map((attachment) => {
        const tone = attachmentTone(attachment.mime_type || "", attachment.original_name);
        const details = [attachment.mime_type || "Document", formatFileSize(attachment.size)].filter(Boolean).join(" · ");
        if (tone === "pdf") {
          return (
            <div className="ms-pdf-message" key={attachment.id}>
              <span className="ms-pdf-message__type" aria-hidden="true">PDF</span>
              <span className="ms-pdf-message__copy">
                <strong title={attachment.original_name}>{attachment.original_name}</strong>
                <small>PDF · {formatFileSize(attachment.size)}</small>
              </span>
              <span className="ms-pdf-message__actions">
                <button type="button" className="ms-pdf-message__action" onClick={() => onPreviewAttachment?.(attachment.id)} aria-label={`View ${attachment.original_name}`} title="View PDF"><ViewIcon /></button>
                <AttachmentDownloadButton attachment={attachment} currentUserId={currentUserId} className="ms-pdf-message__action"><DownloadIcon /></AttachmentDownloadButton>
              </span>
            </div>
          );
        }
        return (
          <div className={`ms-file-message ms-file-message--${tone}`} key={attachment.id}>
            <span className="ms-file-message__type">{attachmentLabel(attachment)}</span>
            <span className="ms-file-message__copy">
              <strong title={attachment.original_name}>{attachment.original_name}</strong>
              <small>{details}</small>
            </span>
            <AttachmentDownloadButton attachment={attachment} currentUserId={currentUserId} className="ms-file-message__download">
              <DownloadIcon />
            </AttachmentDownloadButton>
          </div>
        );
      })}
    </div>
  );
}
