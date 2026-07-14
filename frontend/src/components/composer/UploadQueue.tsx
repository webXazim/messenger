import type { PendingComposerUpload } from "./types";

function formatFileSize(size: number) {
  if (!size) return "0 B";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentKindLabel(file: File) {
  const mime = file.type.toLowerCase();
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

export function UploadQueue({
  uploads,
  onRetry,
  onRemove,
}: {
  uploads: PendingComposerUpload[];
  onRetry: (localId: string) => void;
  onRemove: (localId: string) => void;
}) {
  if (!uploads.length) return null;

  return (
    <div className="ms-upload-queue" aria-label="Pending attachments">
      {uploads.map((upload) => {
        const mime = upload.file.type.toLowerCase();
        const isPdf = mime === "application/pdf";
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
          <article className={`ms-upload-card ${isVisualMedia ? "is-visual-media" : ""} is-${upload.status}`} key={upload.localId}>
            <div className={`ms-upload-card__preview ${isVisualMedia ? "is-visual-media" : ""}`}>
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
              {isPdf && upload.thumbnailUrl ? <img src={upload.thumbnailUrl} alt={`First page of ${upload.fileName}`} /> : null}
              {isPdf && !upload.thumbnailUrl && upload.previewUrl ? <iframe src={`${upload.previewUrl}#page=1&toolbar=0&navpanes=0`} title={`Preview ${upload.fileName}`} /> : null}
              {!upload.previewUrl || mime.startsWith("audio/") ? <span>{kind.slice(0, 3).toUpperCase()}</span> : null}
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
