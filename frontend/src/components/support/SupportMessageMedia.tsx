import { useEffect, useState } from "react";
import { supportApi } from "../../api/support";
import type { SupportAttachment } from "../../types/support";

function formatSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

function MediaPreview({ attachment }: { attachment: SupportAttachment }) {
  const [objectUrl, setObjectUrl] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!attachment.preview_url || !attachment.can_preview_inline) return;
    const controller = new AbortController();
    let url = "";
    void supportApi
      .fetchMediaBlob(attachment.preview_url, controller.signal)
      .then((blob) => {
        url = URL.createObjectURL(blob);
        setObjectUrl(url);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [attachment.can_preview_inline, attachment.preview_url]);

  if (failed || !attachment.can_preview_inline) return null;
  if (!objectUrl)
    return (
      <span
        className="ms-support-media-loading"
        aria-label={`Loading ${attachment.original_name}`}
      />
    );
  if (attachment.media_kind === "image") {
    return (
      <img
        className="ms-support-media-image"
        src={objectUrl}
        alt={attachment.original_name}
        loading="lazy"
      />
    );
  }
  if (attachment.media_kind === "video") {
    return (
      <video
        className="ms-support-media-video"
        src={objectUrl}
        controls
        preload="metadata"
        playsInline
      />
    );
  }
  if (attachment.media_kind === "audio") {
    return (
      <audio
        className="ms-support-media-audio"
        src={objectUrl}
        controls
        preload="metadata"
      />
    );
  }
  return null;
}

async function downloadAttachment(attachment: SupportAttachment) {
  const blob = await supportApi.fetchMediaBlob(attachment.download_url);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = attachment.original_name || "download";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function SupportMessageMedia({
  attachments,
  voiceNote = false,
}: {
  attachments: SupportAttachment[];
  voiceNote?: boolean;
}) {
  const [downloadError, setDownloadError] = useState<string | null>(null);
  if (!attachments.length) return null;
  return (
    <div className={`ms-support-message-media${voiceNote ? " is-voice" : ""}`}>
      {attachments.map((attachment) => (
        <article
          className={`ms-support-media-item is-${attachment.media_kind}`}
          key={attachment.id}
        >
          <MediaPreview attachment={attachment} />
          {attachment.media_kind === "file" ||
          !attachment.can_preview_inline ? (
            <div className="ms-support-file-card">
              <span className="ms-support-file-card__icon" aria-hidden="true">
                ↧
              </span>
              <span className="ms-support-file-card__body">
                <strong>{attachment.original_name}</strong>
                <small>
                  {formatSize(attachment.size) ||
                    attachment.mime_type ||
                    "File"}
                </small>
              </span>
            </div>
          ) : null}
          <button
            type="button"
            className="ms-support-media-download"
            onClick={() => {
              setDownloadError(null);
              void downloadAttachment(attachment).catch(() =>
                setDownloadError("Download failed."),
              );
            }}
            aria-label={`Download ${attachment.original_name}`}
          >
            Download
          </button>
        </article>
      ))}
      {downloadError ? (
        <span className="ms-support-media-error" role="alert">
          {downloadError}
        </span>
      ) : null}
    </div>
  );
}
