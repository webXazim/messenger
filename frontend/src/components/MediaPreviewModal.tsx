import { useEffect, useId, useRef } from "react";
import type { MessageAttachment } from "../types/chat";
import { useModalAccessibility } from "../hooks/useModalAccessibility";
import { AudioMessagePlayer } from "./AudioMessagePlayer";
import { AuthenticatedImage, AuthenticatedPdf, AuthenticatedVideo, downloadAttachmentForUser } from "./AuthenticatedMedia";
import { getAttachmentPlaybackUrl, getAttachmentPosterUrl, getAttachmentPreviewUrl } from "./messages/messagePresentation";

export function MediaPreviewModal({
  attachment,
  onClose,
  onPrevious,
  onNext,
  onReply,
  onForward,
  currentUserId,
}: {
  attachment: MessageAttachment | null;
  onClose: () => void;
  onPrevious?: () => void;
  onNext?: () => void;
  onReply?: () => void;
  onForward?: () => void;
  currentUserId?: string;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useModalAccessibility<HTMLElement>({
    open: Boolean(attachment),
    onClose,
    initialFocusRef: closeRef,
  });

  useEffect(() => {
    if (!attachment) return;
    const handleNavigationKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft" && onPrevious) {
        event.preventDefault();
        onPrevious();
      } else if (event.key === "ArrowRight" && onNext) {
        event.preventDefault();
        onNext();
      }
    };
    document.addEventListener("keydown", handleNavigationKey);
    return () => document.removeEventListener("keydown", handleNavigationKey);
  }, [attachment, onNext, onPrevious]);

  if (!attachment) return null;
  const imagePreviewUrl = getAttachmentPreviewUrl(attachment) || getAttachmentPlaybackUrl(attachment) || "#";
  const videoPlaybackUrl = getAttachmentPlaybackUrl(attachment) || "#";
  const videoPosterUrl = getAttachmentPosterUrl(attachment);
  const mime = (attachment.mime_type || "").toLowerCase();
  const isImage = mime.startsWith("image/");
  const isVideo = mime.startsWith("video/");
  const isAudio = mime.startsWith("audio/");
  const isPdf = mime === "application/pdf";

  return (
    <div className="ms-modal-backdrop ms-modal-backdrop--media" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section
        ref={dialogRef}
        className="ms-modal ms-media-preview"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
      >
        <header className="ms-modal__header ms-modal__header--top">
          <div>
            <strong id={titleId}>{attachment.original_name}</strong>
            <div id={descriptionId} className="ms-muted">{attachment.mime_type || "Attachment"}</div>
          </div>
          <div className="ms-button-row ms-media-preview__actions">
            {onPrevious ? <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={onPrevious} aria-label="Previous attachment">← Previous</button> : null}
            {onNext ? <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={onNext} aria-label="Next attachment">Next →</button> : null}
            {onReply ? <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={onReply}>Reply</button> : null}
            {onForward ? <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={onForward}>Forward</button> : null}
            {isImage ? <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={() => { void downloadAttachmentForUser(attachment, currentUserId); }}>Save</button> : null}
            <button ref={closeRef} type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={onClose}>Close</button>
          </div>
        </header>
        {isImage ? <AuthenticatedImage className="ms-media-preview__surface" src={imagePreviewUrl} alt={attachment.original_name} attachment={attachment} currentUserId={currentUserId} /> : null}
        {isVideo ? <AuthenticatedVideo className="ms-media-preview__surface" src={videoPlaybackUrl} posterSrc={videoPosterUrl} attachment={attachment} currentUserId={currentUserId} /> : null}
        {isAudio ? <AudioMessagePlayer src={getAttachmentPlaybackUrl(attachment) || imagePreviewUrl} label={attachment.original_name} attachment={attachment} currentUserId={currentUserId} /> : null}
        {isPdf ? <AuthenticatedPdf className="ms-media-preview__surface ms-media-preview__pdf" src={videoPlaybackUrl} title={attachment.original_name} attachment={attachment} currentUserId={currentUserId} /> : null}
        {!isImage && !isVideo && !isAudio && !isPdf ? (
          <div className="ms-media-preview__unsupported">
            <p className="ms-muted">Preview is not available for this file type.</p>
          </div>
        ) : null}
      </section>
    </div>
  );
}
