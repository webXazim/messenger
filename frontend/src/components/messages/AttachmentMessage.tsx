import type { MessageAttachment } from "../../types/chat";
import { AttachmentDownloadButton } from "../AttachmentDownloadButton";
import { attachmentLabel, attachmentTone, formatFileSize } from "./messagePresentation";

function DownloadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 20h14" /></svg>;
}

export function AttachmentMessage({ attachments, currentUserId }: { attachments: MessageAttachment[]; currentUserId?: string }) {
  if (!attachments.length) return null;
  return (
    <div className="ms-file-message-list">
      {attachments.map((attachment) => {
        const tone = attachmentTone(attachment.mime_type || "");
        const details = [attachment.mime_type || "Document", formatFileSize(attachment.size)].filter(Boolean).join(" · ");
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
