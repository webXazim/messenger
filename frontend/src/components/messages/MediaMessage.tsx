import { useEffect, useState } from "react";
import type { MessageAttachment } from "../../types/chat";
import { AuthenticatedAttachmentPreview, AuthenticatedImage, AuthenticatedVideo } from "../AuthenticatedMedia";
import { AttachmentDownloadButton } from "../AttachmentDownloadButton";
import { getAttachmentPosterUrl, getAttachmentPreviewUrl, getAttachmentPlaybackUrl, getAttachmentRatioStyle } from "./messagePresentation";

function DownloadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 20h14" /></svg>;
}

function ExpandIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" /></svg>;
}

function PlayIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 10 6-10 6V6Z" /></svg>;
}

function LazyVideo({ attachment, src, posterSrc, currentUserId }: { attachment: MessageAttachment; src: string; posterSrc: string; currentUserId?: string }) {
  const [playbackRequested, setPlaybackRequested] = useState(false);
  if (playbackRequested) {
    return <AuthenticatedVideo src={src} posterSrc={posterSrc} attachment={attachment} currentUserId={currentUserId} />;
  }
  return (
    <button type="button" className="ms-message-media__video-poster" onClick={() => setPlaybackRequested(true)} aria-label={`Play ${attachment.original_name}`}>
      <AuthenticatedAttachmentPreview attachment={attachment} currentUserId={currentUserId} fallbackSrc={posterSrc} alt={`Preview of ${attachment.original_name}`} />
      <span className="ms-message-media__play"><PlayIcon /></span>
    </button>
  );
}

function LazyImage({ attachment, thumbnailSrc, fullSrc, currentUserId, warmMedia, onOpen }: { attachment: MessageAttachment; thumbnailSrc: string; fullSrc: string; currentUserId?: string; warmMedia?: boolean; onOpen: () => void }) {
  const [useFullImage, setUseFullImage] = useState(Boolean(warmMedia && !thumbnailSrc));
  useEffect(() => {
    if (warmMedia && !thumbnailSrc) setUseFullImage(true);
  }, [thumbnailSrc, warmMedia]);
  const src = thumbnailSrc || (useFullImage ? fullSrc : "");
  const mediaAttachment = thumbnailSrc ? { ...attachment, is_encrypted: false, encryption: null } : attachment;
  return (
    <button type="button" className="ms-message-media__preview" onClick={onOpen} aria-label={`View ${attachment.original_name}`}>
      {src
        ? <AuthenticatedImage src={src} alt={attachment.original_name} attachment={mediaAttachment} currentUserId={currentUserId} />
        : <span className="ms-message-media__image-placeholder" aria-hidden="true">IMG</span>}
    </button>
  );
}

export function MediaMessage({
  attachments,
  currentUserId,
  onPreviewAttachment,
  warmMedia = false,
}: {
  attachments: MessageAttachment[];
  currentUserId?: string;
  onPreviewAttachment?: (attachmentId: string) => void;
  warmMedia?: boolean;
}) {
  if (!attachments.length) return null;
  return (
    <div className={`ms-message-media ms-message-media--count-${Math.min(attachments.length, 4)}`}>
      {attachments.map((attachment) => {
        const isVideo = (attachment.mime_type || "").toLowerCase().startsWith("video/") || attachment.media_kind === "video";
        const mediaUrl = isVideo ? getAttachmentPlaybackUrl(attachment) : getAttachmentPreviewUrl(attachment);
        const posterUrl = isVideo ? getAttachmentPosterUrl(attachment) : "";
        if (isVideo && !mediaUrl) return null;
        return (
          <div className={`ms-message-media__item ${isVideo ? "is-video" : "is-image"}`} style={getAttachmentRatioStyle(attachment)} key={attachment.id}>
            {isVideo ? (
              <LazyVideo attachment={attachment} src={mediaUrl} posterSrc={posterUrl} currentUserId={currentUserId} />
            ) : (
              <LazyImage attachment={attachment} thumbnailSrc={mediaUrl} fullSrc={getAttachmentPlaybackUrl(attachment)} currentUserId={currentUserId} warmMedia={warmMedia} onOpen={() => onPreviewAttachment?.(attachment.id)} />
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
