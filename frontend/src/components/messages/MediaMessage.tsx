import type { MessageAttachment } from "../../types/chat";
import { AuthenticatedImage, AuthenticatedVideo } from "../AuthenticatedMedia";
import { AttachmentDownloadButton } from "../AttachmentDownloadButton";
import { getAttachmentPosterUrl, getAttachmentPreviewUrl, getAttachmentPlaybackUrl, getAttachmentRatioStyle } from "./messagePresentation";

function DownloadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 20h14" /></svg>;
}

function ExpandIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" /></svg>;
}

export function MediaMessage({
  attachments,
  currentUserId,
  onPreviewAttachment,
}: {
  attachments: MessageAttachment[];
  currentUserId?: string;
  onPreviewAttachment?: (attachmentId: string) => void;
}) {
  if (!attachments.length) return null;
  return (
    <div className={`ms-message-media ms-message-media--count-${Math.min(attachments.length, 4)}`}>
      {attachments.map((attachment) => {
        const isVideo = (attachment.mime_type || "").toLowerCase().startsWith("video/") || attachment.media_kind === "video";
        const mediaUrl = isVideo ? getAttachmentPlaybackUrl(attachment) : getAttachmentPreviewUrl(attachment);
        const posterUrl = isVideo ? getAttachmentPosterUrl(attachment) : "";
        if (!mediaUrl) return null;
        return (
          <div className={`ms-message-media__item ${isVideo ? "is-video" : "is-image"}`} style={getAttachmentRatioStyle(attachment)} key={attachment.id}>
            {isVideo ? (
              <AuthenticatedVideo src={mediaUrl} posterSrc={posterUrl} attachment={attachment} currentUserId={currentUserId} />
            ) : (
              <button type="button" className="ms-message-media__preview" onClick={() => onPreviewAttachment?.(attachment.id)} aria-label={`View ${attachment.original_name}`}>
                <AuthenticatedImage src={mediaUrl} alt={attachment.original_name} attachment={attachment} currentUserId={currentUserId} />
              </button>
            )}
            <div className="ms-message-media__actions">
              <AttachmentDownloadButton attachment={attachment} currentUserId={currentUserId}>
                <DownloadIcon />
              </AttachmentDownloadButton>
              <button type="button" onClick={() => onPreviewAttachment?.(attachment.id)} aria-label={`Open ${attachment.original_name}`}>
                <ExpandIcon />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
