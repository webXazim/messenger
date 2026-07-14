import { forwardRef, useEffect, useMemo, useState } from "react";
import { decryptAttachment } from "../lib/e2ee";
import { API_BASE_URL } from "../lib/config";
import { getAccessToken } from "../lib/tokenStore";
import { http } from "../lib/http";
import { unwrapData } from "../lib/apiResponse";
import type { MessageAttachment } from "../types/chat";
import { generateAndStoreLocalPreview, getSessionMedia, readLocalPreview, rememberSessionMedia } from "../lib/mediaPreviewCache";

type MediaDisposition = "inline" | "attachment";
type MediaKind = "image" | "video" | "audio";

type MediaTokenPayload = {
  url?: string;
  preview_url?: string;
  download_url?: string;
};

type ResolvedMedia = {
  resolvedSrc: string;
  failed: boolean;
  loading: boolean;
  retry: () => void;
  setFailed: (value: boolean) => void;
  errorMessage: string;
};

const mediaReadyCache = new Set<string>();

export function shouldSendAccessToken(src: string) {
  if (!src || src === "#" || typeof window === "undefined") return false;
  try {
    const target = new URL(src, window.location.origin);
    const api = new URL(API_BASE_URL, window.location.origin);
    return target.origin === window.location.origin || target.origin === api.origin;
  } catch {
    return false;
  }
}


export function hasMediaAccessToken(src: string) {
  if (!src || src === "#" || typeof window === "undefined") return false;
  try {
    return Boolean(new URL(src, window.location.origin).searchParams.get("token"));
  } catch {
    return false;
  }
}

async function refreshAttachmentUrl(attachmentId: string, disposition: MediaDisposition) {
  const response = await http.post(`/chat/attachments/${attachmentId}/media-token/`);
  const payload = unwrapData<MediaTokenPayload>(response.data);
  const candidate = disposition === "attachment"
    ? payload.download_url || payload.url
    : payload.preview_url || payload.url || payload.download_url;
  return candidate || "";
}

async function fetchMediaBlob(src: string, signal?: AbortSignal) {
  const headers: HeadersInit = {};
  const token = getAccessToken();
  if (token && shouldSendAccessToken(src)) headers.Authorization = `Bearer ${token}`;
  return fetch(src, {
    headers,
    signal,
    credentials: shouldSendAccessToken(src) ? "same-origin" : "omit",
  });
}

export async function prefetchAttachmentForUser(src: string, signal?: AbortSignal) {
  if (!src || src === "#") return;
  const response = await fetchMediaBlob(src, signal);
  if (!response.ok) throw new Error(`Media prefetch failed with ${response.status}`);
  await response.arrayBuffer();
}

export async function fetchAttachmentBlobForUser(
  src: string,
  attachment?: MessageAttachment,
  currentUserId?: string,
  signal?: AbortSignal,
  disposition: MediaDisposition = "inline",
) {
  if (attachment?.id && currentUserId) {
    const cached = getSessionMedia(currentUserId, attachment);
    if (cached) return cached;
  }
  let requestSrc = src;
  let response = await fetchMediaBlob(requestSrc, signal);

  if ([401, 403].includes(response.status) && attachment?.id && !signal?.aborted) {
    const refreshedSrc = await refreshAttachmentUrl(attachment.id, disposition).catch(() => "");
    if (refreshedSrc) {
      requestSrc = refreshedSrc;
      response = await fetchMediaBlob(requestSrc, signal);
    }
  }

  if (!response.ok) {
    const error = new Error(`Media request failed with ${response.status}`);
    (error as Error & { status?: number }).status = response.status;
    throw error;
  }

  let blob = await response.blob();
  if (attachment?.is_encrypted && currentUserId) {
    const decrypted = await decryptAttachment(currentUserId, attachment, blob);
    blob = decrypted.blob;
  }
  if (attachment?.id && currentUserId) {
    rememberSessionMedia(currentUserId, attachment, blob);
    void generateAndStoreLocalPreview(currentUserId, attachment, blob).catch(() => undefined);
  }
  return blob;
}

function useLocalMediaPreview(attachment?: MessageAttachment, currentUserId?: string) {
  const [previewSrc, setPreviewSrc] = useState("");
  useEffect(() => {
    if (!attachment?.id || !currentUserId) {
      setPreviewSrc("");
      return;
    }
    let objectUrl = "";
    let cancelled = false;
    const load = async () => {
      const blob = await readLocalPreview(currentUserId, attachment);
      if (!blob || cancelled) return;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(blob);
      setPreviewSrc(objectUrl);
    };
    const handlePreview = (event: Event) => {
      const detail = (event as CustomEvent<{ userId?: string; attachmentId?: string; cacheIdentity?: string }>).detail;
      const digestIdentity = attachment.encryption?.original_sha256 ? `sha256:${attachment.encryption.original_sha256}` : "";
      if (detail?.userId === currentUserId && (detail.attachmentId === attachment.id || (digestIdentity && detail.cacheIdentity === digestIdentity))) void load();
    };
    void load();
    window.addEventListener("ms-local-media-preview", handlePreview);
    return () => {
      cancelled = true;
      window.removeEventListener("ms-local-media-preview", handlePreview);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment?.encryption?.original_sha256, attachment?.id, currentUserId]);
  return previewSrc;
}

function useAuthenticatedMediaUrl(src: string, attachment?: MessageAttachment, currentUserId?: string): ResolvedMedia {
  const requiresProtectedBlob = Boolean(
    src
    && src !== "#"
    && (attachment?.is_encrypted || (shouldSendAccessToken(src) && !hasMediaAccessToken(src))),
  );
  const [resolvedSrc, setResolvedSrc] = useState(requiresProtectedBlob ? "" : src);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(requiresProtectedBlob);
  const [errorMessage, setErrorMessage] = useState("");
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    setFailed(false);
    setErrorMessage("");
    if (!src || src === "#") {
      setResolvedSrc("");
      setFailed(true);
      setLoading(false);
      return;
    }

    const requiresBlobUrl = Boolean(
      attachment?.is_encrypted || (shouldSendAccessToken(src) && !hasMediaAccessToken(src)),
    );

    if (!requiresBlobUrl) {
      let cancelled = false;
      if (retryKey > 0 && attachment?.id && hasMediaAccessToken(src)) {
        setLoading(true);
        void refreshAttachmentUrl(attachment.id, "inline")
          .then((refreshedSrc) => {
            if (cancelled) return;
            if (!refreshedSrc) throw new Error("A fresh media link could not be created.");
            setResolvedSrc(refreshedSrc);
            setFailed(false);
          })
          .catch((error) => {
            if (cancelled) return;
            setResolvedSrc("");
            setErrorMessage(error instanceof Error ? error.message : "This media could not be opened.");
            setFailed(true);
          })
          .finally(() => {
            if (!cancelled) setLoading(false);
          });
        return () => { cancelled = true; };
      }
      setResolvedSrc(src);
      setLoading(false);
      return;
    }

    let objectUrl = "";
    let cancelled = false;
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setResolvedSrc("");
      try {
        const blob = await fetchAttachmentBlobForUser(src, attachment, currentUserId, controller.signal, "inline");
        if (cancelled || controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setResolvedSrc(objectUrl);
        setFailed(false);
      } catch (error) {
        if (!cancelled && !controller.signal.aborted) {
          setResolvedSrc("");
          setErrorMessage(error instanceof Error ? error.message : "This media could not be opened.");
          setFailed(true);
        }
      } finally {
        if (!cancelled && !controller.signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment?.id, attachment?.is_encrypted, currentUserId, retryKey, src]);

  return {
    resolvedSrc,
    failed,
    loading,
    retry: () => setRetryKey((current) => current + 1),
    setFailed,
    errorMessage,
  };
}

function useMediaReady(src: string, kind: MediaKind) {
  const [ready, setReady] = useState(() => (!src ? false : mediaReadyCache.has(`${kind}:${src}`)));

  useEffect(() => {
    if (!src) {
      setReady(false);
      return;
    }

    const cacheKey = `${kind}:${src}`;
    if (mediaReadyCache.has(cacheKey)) {
      setReady(true);
      return;
    }

    let cancelled = false;
    setReady(false);

    if (kind === "image") {
      const image = new Image();
      image.decoding = "async";
      image.src = src;
      const markReady = () => {
        if (cancelled) return;
        mediaReadyCache.add(cacheKey);
        setReady(true);
      };
      image.onload = markReady;
      image.onerror = markReady;
      if (typeof image.decode === "function") image.decode().then(markReady).catch(() => undefined);
      return () => {
        cancelled = true;
      };
    }

    if (kind === "video") {
      const video = document.createElement("video");
      video.preload = "metadata";
      video.muted = true;
      video.playsInline = true;
      video.src = src;
      const markReady = () => {
        if (cancelled) return;
        mediaReadyCache.add(cacheKey);
        setReady(true);
      };
      video.onloadedmetadata = markReady;
      video.onloadeddata = markReady;
      video.oncanplay = markReady;
      video.onerror = markReady;
      video.load();
      return () => {
        cancelled = true;
        video.pause();
        video.removeAttribute("src");
        video.load();
      };
    }

    mediaReadyCache.add(cacheKey);
    setReady(true);
    return () => { cancelled = true; };
  }, [kind, src]);

  return ready;
}

function MediaFallback({ kind, name, loading, onRetry, message }: { kind: MediaKind; name?: string; loading?: boolean; onRetry?: () => void; message?: string; }) {
  const label = loading
    ? (kind === "image" ? "Photo" : kind === "video" ? "Video" : "Voice message")
    : message || `${kind[0]?.toUpperCase()}${kind.slice(1)} unavailable`;
  return (
    <div className={`ms-media-fallback ${loading ? "ms-media-fallback--loading" : ""} ${kind === "audio" ? "ms-media-fallback--audio" : ""}`}>
      <span className="ms-media-fallback__icon" aria-hidden="true">{kind === "image" ? "▧" : kind === "video" ? "▷" : "◖"}</span>
      <span className="ms-media-fallback__copy">
        <strong>{label}</strong>
        {name ? <small title={name}>{name}</small> : null}
      </span>
      {!loading && onRetry ? <button type="button" onClick={onRetry}>Retry</button> : null}
    </div>
  );
}

export async function downloadAttachmentForUser(attachment: MessageAttachment, currentUserId?: string) {
  const src = attachment.signed_download?.download_url
    || attachment.signed_download?.url
    || attachment.file_url
    || attachment.preview_url
    || attachment.signed_preview?.preview_url
    || attachment.signed_preview?.url;
  if (!src) throw new Error("This attachment is no longer available.");
  const blob = await fetchAttachmentBlobForUser(src, attachment, currentUserId, undefined, "attachment");
  const fileName = attachment.original_name;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = fileName;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export function AuthenticatedImage({ src, alt, className, attachment, currentUserId }: { src: string; alt: string; className?: string; attachment?: MessageAttachment; currentUserId?: string }) {
  const { resolvedSrc, failed, loading, retry, setFailed, errorMessage } = useAuthenticatedMediaUrl(src, attachment, currentUserId);
  const localPreviewSrc = useLocalMediaPreview(attachment, currentUserId);
  const ready = useMediaReady(resolvedSrc, "image");
  if (failed) return <MediaFallback kind="image" name={attachment?.original_name || alt} message={errorMessage} onRetry={retry} />;
  if ((loading && !resolvedSrc) || !ready) return localPreviewSrc
    ? <img className={className} src={localPreviewSrc} alt={alt} />
    : <div className="ms-auth-media-shell ms-auth-media-shell--image" role="img" aria-label={alt} />;
  return <img className={className} src={resolvedSrc} alt={alt} loading="lazy" decoding="async" onError={() => setFailed(true)} />;
}

export const AuthenticatedAudio = forwardRef<HTMLAudioElement, { src: string; className?: string; onLoadedMetadata?: () => void; attachment?: MessageAttachment; currentUserId?: string }>(
  function AuthenticatedAudio({ src, className, onLoadedMetadata, attachment, currentUserId }, ref) {
    const { resolvedSrc, failed, loading, retry, setFailed, errorMessage } = useAuthenticatedMediaUrl(src, attachment, currentUserId);
    if (failed) return <MediaFallback kind="audio" name={attachment?.original_name} message={errorMessage} onRetry={retry} />;
    if (loading && !resolvedSrc) return <MediaFallback kind="audio" name={attachment?.original_name} loading />;
    return <audio ref={ref} className={className} src={resolvedSrc} controls playsInline preload="metadata" onLoadedMetadata={onLoadedMetadata} onError={() => setFailed(true)} />;
  },
);

export function AuthenticatedVideo({ src, posterSrc, className, attachment, currentUserId }: { src: string; posterSrc?: string; className?: string; attachment?: MessageAttachment; currentUserId?: string }) {
  const { resolvedSrc, failed, retry, setFailed, errorMessage } = useAuthenticatedMediaUrl(src, attachment, currentUserId);
  const posterMedia = useAuthenticatedMediaUrl(posterSrc || "", undefined, currentUserId);
  const localPosterSrc = useLocalMediaPreview(attachment, currentUserId);
  const stableName = useMemo(() => attachment?.original_name || "Video", [attachment?.original_name]);
  const [frameReady, setFrameReady] = useState(false);

  useEffect(() => {
    setFrameReady(false);
  }, [resolvedSrc]);

  if (failed) return <MediaFallback kind="video" name={stableName} message={errorMessage} onRetry={retry} />;
  return (
    <video
      className={`${className || ""} ms-auth-media-shell ${frameReady ? "is-frame-ready" : "is-frame-loading"}`.trim()}
      src={resolvedSrc || undefined}
      poster={posterMedia.resolvedSrc || localPosterSrc || undefined}
      controls
      playsInline
      preload={resolvedSrc ? "metadata" : "none"}
      onLoadedData={() => setFrameReady(true)}
      onCanPlay={() => setFrameReady(true)}
      onError={() => setFailed(true)}
    />
  );
}
