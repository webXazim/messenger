import { useEffect, useId, useRef, useState } from "react";
import type { MessageAttachment } from "../types/chat";
import { useModalAccessibility } from "../hooks/useModalAccessibility";
import { AudioMessagePlayer } from "./AudioMessagePlayer";
import { AuthenticatedImage, AuthenticatedVideo, downloadAttachmentForUser, fetchAttachmentBlobForUser } from "./AuthenticatedMedia";
import { PdfDocumentPreview } from "./composer/PdfDocumentPreview";
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
  const [imageControlsVisible, setImageControlsVisible] = useState(false);
  const [pdfBlob, setPdfBlob] = useState<Blob | null>(null);
  const [pdfError, setPdfError] = useState("");
  const titleId = useId();
  const descriptionId = useId();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const imageStageRef = useRef<HTMLDivElement | null>(null);
  const attachmentMime = (attachment?.mime_type || "").toLowerCase();
  const attachmentIsImage = attachmentMime.startsWith("image/");
  const attachmentIsPdf = attachmentMime === "application/pdf" || Boolean(attachment?.original_name.toLowerCase().endsWith(".pdf"));
  const dialogRef = useModalAccessibility<HTMLElement>({
    open: Boolean(attachment),
    onClose,
    initialFocusRef: attachmentIsImage || attachmentIsPdf ? imageStageRef : closeRef,
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

  useEffect(() => {
    setImageControlsVisible(false);
  }, [attachment?.id]);

  useEffect(() => {
    setPdfBlob(null);
    setPdfError("");
    if (!attachment || !attachmentIsPdf) return;
    const src = getAttachmentPlaybackUrl(attachment);
    if (!src) {
      setPdfError("PDF preview is unavailable.");
      return;
    }
    const controller = new AbortController();
    void fetchAttachmentBlobForUser(src, attachment, currentUserId, controller.signal, "inline")
      .then(setPdfBlob)
      .catch((reason: unknown) => {
        if ((reason as { name?: string })?.name !== "AbortError") setPdfError(reason instanceof Error ? reason.message : "PDF preview is unavailable.");
      });
    return () => controller.abort();
  }, [attachment, attachmentIsPdf, currentUserId]);

  if (!attachment) return null;
  const imagePreviewUrl = getAttachmentPlaybackUrl(attachment) || getAttachmentPreviewUrl(attachment) || "#";
  const videoPlaybackUrl = getAttachmentPlaybackUrl(attachment) || "#";
  const videoPosterUrl = getAttachmentPosterUrl(attachment);
  const mime = (attachment.mime_type || "").toLowerCase();
  const isImage = mime.startsWith("image/");
  const isVideo = mime.startsWith("video/");
  const isAudio = mime.startsWith("audio/");
  const isPdf = mime === "application/pdf" || attachment.original_name.toLowerCase().endsWith(".pdf");

  if (isImage || isPdf) {
    const toggleControls = () => setImageControlsVisible((visible) => !visible);
    const stopOverlayClick = (event: React.MouseEvent) => event.stopPropagation();

    return (
      <div className="ms-modal-backdrop ms-modal-backdrop--media ms-image-viewer-backdrop" role="presentation" onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}>
        <section
          ref={dialogRef}
          className={`ms-image-viewer${isPdf ? " ms-image-viewer--pdf" : ""}${imageControlsVisible ? " is-controls-visible" : ""}`}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-describedby={descriptionId}
          tabIndex={-1}
        >
          <div
            ref={imageStageRef}
            className={`ms-image-viewer__stage${isPdf ? " ms-image-viewer__stage--pdf" : ""}`}
            role="group"
            tabIndex={0}
            aria-label={`${isPdf ? "PDF" : "Image"} viewer. Press Enter or Space to show or hide controls.`}
            aria-expanded={imageControlsVisible}
            onClick={toggleControls}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                toggleControls();
              }
            }}
          >
            {isImage ? <AuthenticatedImage className="ms-image-viewer__image" src={imagePreviewUrl} alt={attachment.original_name} attachment={attachment} currentUserId={currentUserId} /> : null}
            {isPdf && pdfBlob ? <PdfDocumentPreview source={pdfBlob} title={attachment.original_name} /> : null}
            {isPdf && !pdfBlob ? <div className="ms-image-viewer__pdf-state" role="status">{pdfError || "Preparing PDF…"}</div> : null}
            <header className="ms-image-viewer__overlay" onClick={stopOverlayClick}>
              <div className="ms-image-viewer__identity">
                <strong id={titleId}>{attachment.original_name}</strong>
                <span id={descriptionId}>{attachment.mime_type || "Image"}</span>
              </div>
              <div className="ms-image-viewer__actions">
                {onPrevious ? (
                  <button type="button" className="ms-image-viewer__button" onClick={onPrevious} aria-label="Previous attachment" title="Previous attachment">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>
                  </button>
                ) : null}
                {onNext ? (
                  <button type="button" className="ms-image-viewer__button" onClick={onNext} aria-label="Next attachment" title="Next attachment">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
                  </button>
                ) : null}
                {onReply ? (
                  <button type="button" className="ms-image-viewer__button" onClick={onReply} aria-label="Reply" title="Reply">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 17-5-5 5-5" /><path d="M20 18c0-4-3-6-8-6H4" /></svg>
                  </button>
                ) : null}
                {onForward ? (
                  <button type="button" className="ms-image-viewer__button" onClick={onForward} aria-label="Forward" title="Forward">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 7 5 5-5 5" /><path d="M4 18c0-4 3-6 8-6h8" /></svg>
                  </button>
                ) : null}
                <button type="button" className="ms-image-viewer__button" onClick={() => { void downloadAttachmentForUser(attachment, currentUserId); }} aria-label={`Download ${isPdf ? "PDF" : "image"}`} title="Download">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>
                </button>
                <button ref={closeRef} type="button" className="ms-image-viewer__button" onClick={onClose} aria-label={`Close ${isPdf ? "PDF" : "image"} viewer`} title="Close">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6 18 18M18 6 6 18" /></svg>
                </button>
              </div>
            </header>
          </div>
        </section>
      </div>
    );
  }

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
            <button ref={closeRef} type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={onClose}>Close</button>
          </div>
        </header>
        {isVideo ? <AuthenticatedVideo className="ms-media-preview__surface" src={videoPlaybackUrl} posterSrc={videoPosterUrl} attachment={attachment} currentUserId={currentUserId} /> : null}
        {isAudio ? <AudioMessagePlayer src={getAttachmentPlaybackUrl(attachment) || imagePreviewUrl} label={attachment.original_name} attachment={attachment} currentUserId={currentUserId} /> : null}
        {!isImage && !isVideo && !isAudio && !isPdf ? (
          <div className="ms-media-preview__unsupported">
            <p className="ms-muted">Preview is not available for this file type.</p>
          </div>
        ) : null}
      </section>
    </div>
  );
}
