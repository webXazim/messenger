import { AuthenticatedImage, AuthenticatedVideo, downloadAttachmentForUser } from "../../AuthenticatedMedia";
import type { ConversationMediaItem } from "../../../api/chat";
import { DetailsSection } from "./DetailsSection";
import { getAttachmentPlaybackUrl, getAttachmentPosterUrl, getAttachmentPreviewUrl } from "../../messages/messagePresentation";

type MediaKind = "all" | "image" | "video" | "audio" | "file";

function countKind(media: ConversationMediaItem[], kind: Exclude<MediaKind, "all">) {
  return media.filter((item) => {
    const mime = (item.attachment.mime_type || "").toLowerCase();
    if (kind === "file") return !/^(image|video|audio)\//.test(mime);
    return mime.startsWith(`${kind}/`);
  }).length;
}

function fileKindLabel(item: ConversationMediaItem) {
  const mime = (item.attachment.mime_type || "").toLowerCase();
  if (mime.startsWith("audio/")) return "AUDIO";
  const extension = item.attachment.original_name.split(".").pop()?.slice(0, 5).toUpperCase();
  return extension || "FILE";
}

export function SharedContentSection({
  allMedia,
  visibleMedia,
  mediaKind,
  currentUserId,
  onChangeMediaKind,
}: {
  allMedia: ConversationMediaItem[];
  visibleMedia: ConversationMediaItem[];
  mediaKind: MediaKind;
  currentUserId: string;
  onChangeMediaKind: (kind: MediaKind) => void;
}) {
  const counts = {
    image: countKind(allMedia, "image"),
    video: countKind(allMedia, "video"),
    audio: countKind(allMedia, "audio"),
    file: countKind(allMedia, "file"),
  };
  const linkCount = allMedia.reduce(
    (total, item) => total + ((item.message_text || "").match(/https?:\/\/\S+/g)?.length || 0),
    0,
  );

  const categories: Array<{ kind: MediaKind; label: string; count: number }> = [
    { kind: "image", label: "Photos", count: counts.image },
    { kind: "video", label: "Videos", count: counts.video },
    { kind: "audio", label: "Audio", count: counts.audio },
    { kind: "file", label: "Files", count: counts.file },
  ];

  return (
    <DetailsSection title="Shared content" eyebrow="Conversation" note={`${allMedia.length} items`}>
      <div className="ms-details-content-links">
        {categories.map((category) => (
          <button
            key={category.kind}
            type="button"
            className={mediaKind === category.kind ? "is-active" : ""}
            onClick={() => onChangeMediaKind(category.kind)}
          >
            <span>{category.label}</span>
            <strong>{category.count}</strong>
            <i aria-hidden="true">›</i>
          </button>
        ))}
        <div className="ms-details-content-links__static">
          <span>Links</span>
          <strong>{linkCount}</strong>
        </div>
      </div>

      <div className="ms-details-media-toolbar">
        <div className="ms-details-filter" role="group" aria-label="Filter shared content">
          {(["all", "image", "video", "audio", "file"] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              className={mediaKind === kind ? "is-active" : ""}
              onClick={() => onChangeMediaKind(kind)}
            >
              {kind === "all" ? "All" : kind === "image" ? "Photos" : `${kind[0].toUpperCase()}${kind.slice(1)}`}
            </button>
          ))}
        </div>
      </div>

      {visibleMedia.length ? (
        <div className="ms-details-media-grid">
          {visibleMedia.slice(0, 8).map((item) => {
            const mime = (item.attachment.mime_type || "").toLowerCase();
            const isImage = mime.startsWith("image/");
            const isVideo = mime.startsWith("video/");
            const previewUrl = isVideo
              ? getAttachmentPlaybackUrl(item.attachment)
              : getAttachmentPreviewUrl(item.attachment);
            const posterUrl = isVideo ? getAttachmentPosterUrl(item.attachment) : "";

            return (
              <article key={`${item.message_id}-${item.attachment.id}`} className={`ms-details-media-item ${isImage || isVideo ? "is-visual" : "is-file"}`}>
                {isImage && previewUrl ? (
                  <AuthenticatedImage
                    className="ms-details-media-item__preview"
                    src={previewUrl}
                    alt={item.attachment.original_name}
                    attachment={item.attachment}
                    currentUserId={currentUserId}
                  />
                ) : null}
                {isVideo && previewUrl ? (
                  <AuthenticatedVideo
                    className="ms-details-media-item__preview"
                    src={previewUrl}
                    posterSrc={posterUrl}
                    attachment={item.attachment}
                    currentUserId={currentUserId}
                  />
                ) : null}
                {!isImage && !isVideo ? (
                  <div className="ms-details-media-item__file-type">{fileKindLabel(item)}</div>
                ) : null}
                <div className="ms-details-media-item__copy">
                  <strong title={item.attachment.original_name}>{item.attachment.original_name}</strong>
                  <span>{item.sender?.display_name || item.sender?.username || "Shared file"}</span>
                </div>
                <button
                  type="button"
                  className="ms-details-media-item__download"
                  aria-label={`Download ${item.attachment.original_name}`}
                  onClick={() => { void downloadAttachmentForUser(item.attachment, currentUserId); }}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10m0 0 4-4m-4 4-4-4M5 20h14" /></svg>
                </button>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="ms-details-empty">No shared content in this category yet.</div>
      )}
    </DetailsSection>
  );
}
