import { useState, type MouseEvent, type PointerEvent } from "react";
import type { PendingComposerUpload } from "./types";
import { PdfDocumentPreview } from "./PdfDocumentPreview";

function formatFileSize(size: number) {
  if (!size) return "0 B";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentKindLabel(file: File) {
  const mime = file.type.toLowerCase();
  if (mime === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) return "PDF";
  if (mime.startsWith("image/")) return "Image";
  if (mime.startsWith("video/")) return "Video";
  if (mime.startsWith("audio/")) return "Audio";
  return "Document";
}

function RemoveIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>;
}

function RetryIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 5v6h-6" /></svg>;
}

function ViewOnceIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 8v8M10 10l2-2 2 2" /></svg>;
}

export function UploadQueue({
  uploads,
  onRetry,
  onRemove,
  onToggleViewOnce,
}: {
  uploads: PendingComposerUpload[];
  onRetry: (localId: string) => void;
  onRemove: (localId: string) => void;
  onToggleViewOnce?: (localId: string) => void;
}) {
  const [hiddenControls, setHiddenControls] = useState<Record<string, boolean>>({});
  if (!uploads.length) return null;

  const showControls = (localId: string) => {
    setHiddenControls((current) => current[localId] ? { ...current, [localId]: false } : current);
  };

  const toggleControls = (event: MouseEvent<HTMLElement>, localId: string, visual: boolean) => {
    if (!visual || (event.target as Element).closest("button, video")) return;
    setHiddenControls((current) => ({ ...current, [localId]: !current[localId] }));
  };

  const handlePointerEnter = (_event: PointerEvent<HTMLElement>, localId: string) => showControls(localId);

  return (
    <div className="ms-upload-queue" aria-label="Pending attachments">
      {uploads.map((upload) => {
        const mime = upload.file.type.toLowerCase();
        const isPdf = mime === "application/pdf" || upload.fileName.toLowerCase().endsWith(".pdf");
        const isVisualMedia = mime.startsWith("image/") || mime.startsWith("video/") || isPdf;
        const kind = attachmentKindLabel(upload.file);
        const progress = Math.max(0, Math.min(100, upload.progress ?? 0));
        const statusLabel = upload.status === "queued"
          ? "Waiting to upload"
          : upload.status === "uploading"
            ? progress > 0 ? `Uploading ${progress}%` : "Uploading"
            : upload.status === "uploaded"
              ? "Ready"
              : upload.error || "Upload failed";
        const removeLabel = upload.status === "uploading"
          ? `Cancel upload of ${upload.fileName}`
          : `Remove ${upload.fileName}`;

        return (
          <article
            className={`ms-upload-card ${isVisualMedia ? "is-visual-media" : ""} ${isPdf ? "is-pdf" : ""} ${hiddenControls[upload.localId] ? "has-hidden-controls" : ""} is-${upload.status}`}
            key={upload.localId}
            onClick={(event) => toggleControls(event, upload.localId, isVisualMedia)}
            onPointerEnter={(event) => handlePointerEnter(event, upload.localId)}
            onFocusCapture={() => showControls(upload.localId)}
          >
            <div className={`ms-upload-card__preview ${isVisualMedia ? "is-visual-media" : ""} ${isPdf ? "is-pdf" : ""}`}>
              {(upload.thumbnailUrl || upload.previewUrl) && mime.startsWith("image/") ? <img src={upload.thumbnailUrl || upload.previewUrl} alt="" /> : null}
              {upload.previewUrl && mime.startsWith("video/") ? (
                <video
                  src={upload.previewUrl}
                  muted
                  playsInline
                  controls
                  preload="metadata"
                  poster={upload.thumbnailUrl}
                  aria-label={`Preview ${upload.fileName}`}
                  onLoadedMetadata={(event) => {
                    const video = event.currentTarget;
                    if (video.duration > 0.2 && video.currentTime === 0) video.currentTime = Math.min(0.35, video.duration * 0.08);
                  }}
                />
              ) : null}
              {isPdf ? <PdfDocumentPreview source={upload.file} title={upload.fileName} /> : null}
              {(!upload.previewUrl && !isPdf) || mime.startsWith("audio/") ? <span>{kind.slice(0, 3).toUpperCase()}</span> : null}
            </div>
            <div className="ms-upload-card__copy">
              <strong title={upload.fileName}>{upload.fileName}</strong>
              <span>{kind} · {formatFileSize(upload.file.size)}</span>
              <span className="ms-upload-card__status" aria-live="polite"><i aria-hidden="true" />{statusLabel}</span>
              {upload.status === "uploading" ? (
                <span className="ms-upload-card__progress" aria-hidden="true">
                  <span style={{ width: `${progress}%` }} />
                </span>
              ) : null}
            </div>
            <div className="ms-upload-card__actions">
              {onToggleViewOnce && (mime.startsWith("image/") || mime.startsWith("video/")) ? (
                <button
                  type="button"
                  className={upload.viewOnce ? "is-active" : ""}
                  onClick={() => onToggleViewOnce(upload.localId)}
                  aria-pressed={Boolean(upload.viewOnce)}
                  aria-label={`${upload.viewOnce ? "Disable" : "Enable"} view once for ${upload.fileName}`}
                  title={upload.viewOnce ? "View once enabled" : "Send as view once"}
                >
                  <ViewOnceIcon />
                </button>
              ) : null}
              {upload.status === "failed" ? (
                <button type="button" onClick={() => onRetry(upload.localId)} aria-label={`Retry ${upload.fileName}`} title="Retry upload">
                  <RetryIcon />
                </button>
              ) : null}
              <button type="button" onClick={() => onRemove(upload.localId)} aria-label={removeLabel} title={upload.status === "uploading" ? "Cancel upload" : "Remove attachment"}>
                <RemoveIcon />
              </button>
            </div>
          </article>
        );
      })}
    </div>
  );
}
