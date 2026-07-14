import type { MessageAttachment } from "../../types/chat";
import { AttachmentDownloadButton } from "../AttachmentDownloadButton";
import { AuthenticatedAttachmentPreview } from "../AuthenticatedMedia";
import { attachmentLabel, attachmentTone, formatFileSize } from "./messagePresentation";

function DownloadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 20h14" /></svg>;
}

export function AttachmentMessage({ attachments, currentUserId, onPreviewAttachment }: { attachments: MessageAttachment[]; currentUserId?: string; onPreviewAttachment?: (attachmentId: string) => void }) {
  if (!attachments.length) return null;
  return (
    <div className="ms-file-message-list">
      {attachments.map((attachment) => {
        const tone = attachmentTone(attachment.mime_type || "");
        const details = [attachment.mime_type || "Document", formatFileSize(attachment.size)].filter(Boolean).join(" · ");
        if (tone === "pdf") {
          return (
            <button type="button" className="ms-pdf-message" key={attachment.id} onClick={() => onPreviewAttachment?.(attachment.id)} aria-label={`Open ${attachment.original_name}`}>
              <span className="ms-pdf-message__preview">
                <AuthenticatedAttachmentPreview attachment={attachment} currentUserId={currentUserId} alt={`First page of ${attachment.original_name}`} />
              </span>
              <span className="ms-pdf-message__copy">
                <strong title={attachment.original_name}>{attachment.original_name}</strong>
                <small>PDF · {formatFileSize(attachment.size)}</small>
              </span>
            </button>
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
