import { useEffect, useState, type ReactNode } from "react";
import type { MessageAttachment } from "../../types/chat";
import { AuthenticatedAttachmentPreview, AuthenticatedImage, AuthenticatedVideo } from "../AuthenticatedMedia";
import { chatApi } from "../../api/chat";
import { getAttachmentPosterUrl, getAttachmentPreviewUrl, getAttachmentPlaybackUrl, getAttachmentRatioStyle } from "./messagePresentation";

function PlayIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 10 6-10 6V6Z" /></svg>;
}

function ViewOnceIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 8v8M10 10l2-2 2 2" /></svg>;
}

function CloseIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6 18 18M18 6 6 18" /></svg>;
}

function ViewOnceMedia({ attachment, currentUserId, own }: { attachment: MessageAttachment; currentUserId?: string; own: boolean }) {
  const [opened, setOpened] = useState(Boolean(attachment.view_once_opened));
  const [opening, setOpening] = useState(false);
  const [sessionUrl, setSessionUrl] = useState("");
  const [error, setError] = useState("");
  const isVideo = (attachment.mime_type || "").toLowerCase().startsWith("video/") || attachment.media_kind === "video";
  const available = !own && !opened && attachment.can_open_view_once !== false;

  useEffect(() => {
    if (!sessionUrl) return;
    const hide = () => {
      if (document.hidden) setSessionUrl("");
    };
    document.addEventListener("visibilitychange", hide);
    return () => document.removeEventListener("visibilitychange", hide);
  }, [sessionUrl]);

  const open = async () => {
    if (!available || opening) return;
    setOpening(true);
    setError("");
    try {
      const url = await chatApi.openViewOnceAttachment(attachment.id);
      setOpened(true);
      setSessionUrl(url);
    } catch (reason) {
      const status = (reason as { response?: { status?: number } })?.response?.status;
      if (status === 403) setOpened(true);
      setError(reason instanceof Error ? reason.message : "This media cannot be opened again.");
    } finally {
      setOpening(false);
    }
  };

  return (
    <div className="ms-view-once">
      <button type="button" className="ms-view-once__card" onClick={() => void open()} disabled={!available || opening}>
        <span className="ms-view-once__icon"><ViewOnceIcon /></span>
        <span>
          <strong>{opened ? "Opened" : own ? `View once ${isVideo ? "video" : "photo"} sent` : `View once ${isVideo ? "video" : "photo"}`}</strong>
          <small>{error || (available ? "Tap to view" : "This media is no longer available")}</small>
        </span>
      </button>
      {sessionUrl ? (
        <div className="ms-view-once__backdrop" role="dialog" aria-modal="true" aria-label={`View once ${isVideo ? "video" : "image"}`} onContextMenu={(event) => event.preventDefault()}>
          <div className="ms-view-once__viewer">
            {isVideo ? (
              <AuthenticatedVideo src={sessionUrl} posterSrc="" attachment={attachment} currentUserId={currentUserId} autoPlay restricted />
            ) : (
              <AuthenticatedImage src={sessionUrl} alt="View once image" attachment={attachment} currentUserId={currentUserId} />
            )}
            <button type="button" className="ms-view-once__close" onClick={() => setSessionUrl("")} aria-label="Close view-once media"><CloseIcon /></button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function LazyVideo({ attachment, src, posterSrc, currentUserId, onPlayingChange }: { attachment: MessageAttachment; src: string; posterSrc: string; currentUserId?: string; onPlayingChange?: (playing: boolean) => void }) {
  const [playbackRequested, setPlaybackRequested] = useState(false);
  if (playbackRequested) {
    return <AuthenticatedVideo src={src} posterSrc={posterSrc} attachment={attachment} currentUserId={currentUserId} autoPlay onPlayingChange={onPlayingChange} />;
  }
  return (
    <button type="button" className="ms-message-media__video-poster" onClick={() => setPlaybackRequested(true)} aria-label={`Play ${attachment.original_name}`}>
      <AuthenticatedAttachmentPreview attachment={attachment} currentUserId={currentUserId} fallbackSrc={posterSrc} alt={`Preview of ${attachment.original_name}`} />
      <span className="ms-message-media__play"><PlayIcon /></span>
    </button>
  );
}

function LazyImage({ attachment, thumbnailSrc, fullSrc, currentUserId, warmMedia, onOpen }: { attachment: MessageAttachment; thumbnailSrc: string; fullSrc: string; currentUserId?: string; warmMedia?: boolean; onOpen: () => void }) {
  const [useFullImage, setUseFullImage] = useState(!thumbnailSrc);
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
  own = false,
  footer,
}: {
  attachments: MessageAttachment[];
  currentUserId?: string;
  onPreviewAttachment?: (attachmentId: string) => void;
  warmMedia?: boolean;
  own?: boolean;
  footer?: ReactNode;
}) {
  const [playingVideoIds, setPlayingVideoIds] = useState<Set<string>>(() => new Set());
  if (!attachments.length) return null;
  return (
    <div className={`ms-message-media ms-message-media--count-${Math.min(attachments.length, 4)} ${playingVideoIds.size ? "has-playing-video" : ""}`}>
      {attachments.map((attachment) => {
        if (attachment.view_once) return <ViewOnceMedia key={attachment.id} attachment={attachment} currentUserId={currentUserId} own={own} />;
        const isVideo = (attachment.mime_type || "").toLowerCase().startsWith("video/") || attachment.media_kind === "video";
        const mediaUrl = isVideo ? getAttachmentPlaybackUrl(attachment) : getAttachmentPreviewUrl(attachment);
        const posterUrl = isVideo ? getAttachmentPosterUrl(attachment) : "";
        if (isVideo && !mediaUrl) return null;
        return (
          <div className={`ms-message-media__item ${isVideo ? "is-video" : "is-image"}`} style={getAttachmentRatioStyle(attachment)} key={attachment.id}>
            {isVideo ? (
              <LazyVideo
                attachment={attachment}
                src={mediaUrl}
                posterSrc={posterUrl}
                currentUserId={currentUserId}
                onPlayingChange={(playing) => setPlayingVideoIds((current) => {
                  const next = new Set(current);
                  if (playing) next.add(attachment.id);
                  else next.delete(attachment.id);
                  return next;
                })}
              />
            ) : (
              <LazyImage attachment={attachment} thumbnailSrc={mediaUrl} fullSrc={getAttachmentPlaybackUrl(attachment)} currentUserId={currentUserId} warmMedia={warmMedia} onOpen={() => onPreviewAttachment?.(attachment.id)} />
            )}
          </div>
        );
      })}
      {footer ? <div className="ms-message-media__meta">{footer}</div> : null}
    </div>
  );
}
