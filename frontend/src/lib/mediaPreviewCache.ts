import type { MessageAttachment } from "../types/chat";

const DB_NAME = "crescentsphere-private-media";
const STORE_NAME = "previews";
const DB_VERSION = 1;
const PREVIEW_CACHE_VERSION = "v2";
const MAX_PREVIEW_ENTRIES = 180;
const MAX_SESSION_MEDIA_BYTES = 64 * 1024 * 1024;
const PREVIEW_MAX_EDGE = 960;

type PreviewRecord = {
  key: string;
  blob: Blob;
  savedAt: number;
};

type SessionMediaRecord = {
  blob: Blob;
  touchedAt: number;
};

const sessionMedia = new Map<string, SessionMediaRecord>();

function attachmentCacheIdentity(attachment: MessageAttachment | string) {
  if (typeof attachment === "string") return `id:${attachment}`;
  const digest = attachment.encryption?.original_sha256?.trim();
  return digest ? `sha256:${digest}` : `id:${attachment.id}`;
}

function cacheKey(userId: string, attachment: MessageAttachment | string) {
  return `${userId}:${PREVIEW_CACHE_VERSION}:${attachmentCacheIdentity(attachment)}`;
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) database.createObjectStore(STORE_NAME, { keyPath: "key" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
  });
}

export function getSessionMedia(userId: string, attachment: MessageAttachment) {
  const key = cacheKey(userId, attachment);
  const record = sessionMedia.get(key);
  if (!record) return null;
  record.touchedAt = Date.now();
  return record.blob;
}

export function rememberSessionMedia(userId: string, attachment: MessageAttachment, blob: Blob) {
  const key = cacheKey(userId, attachment);
  sessionMedia.set(key, { blob, touchedAt: Date.now() });
  let total = [...sessionMedia.values()].reduce((sum, item) => sum + item.blob.size, 0);
  if (total <= MAX_SESSION_MEDIA_BYTES) return;
  const oldest = [...sessionMedia.entries()].sort((left, right) => left[1].touchedAt - right[1].touchedAt);
  for (const [entryKey, item] of oldest) {
    if (entryKey === key) continue;
    sessionMedia.delete(entryKey);
    total -= item.blob.size;
    if (total <= MAX_SESSION_MEDIA_BYTES) break;
  }
}

export async function readLocalPreview(userId: string, attachment: MessageAttachment | string) {
  const database = await openDatabase();
  if (!database) return null;
  return new Promise<Blob | null>((resolve) => {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).get(cacheKey(userId, attachment));
    request.onsuccess = () => resolve((request.result as PreviewRecord | undefined)?.blob || null);
    request.onerror = () => resolve(null);
    transaction.oncomplete = () => database.close();
  });
}

export async function clearPrivateMediaCache(userId: string) {
  for (const key of [...sessionMedia.keys()]) {
    if (key.startsWith(`${userId}:`)) sessionMedia.delete(key);
  }
  const database = await openDatabase();
  if (!database) return;
  const records = await new Promise<PreviewRecord[]>((resolve) => {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).getAll();
    request.onsuccess = () => resolve((request.result as PreviewRecord[]) || []);
    request.onerror = () => resolve([]);
  });
  const transaction = database.transaction(STORE_NAME, "readwrite");
  records.filter((record) => record.key.startsWith(`${userId}:`)).forEach((record) => transaction.objectStore(STORE_NAME).delete(record.key));
  await new Promise<void>((resolve) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
  });
  database.close();
}

async function writeLocalPreview(userId: string, attachment: MessageAttachment, blob: Blob) {
  const database = await openDatabase();
  if (!database) return;
  await new Promise<void>((resolve) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put({ key: cacheKey(userId, attachment), blob, savedAt: Date.now() } satisfies PreviewRecord);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
  });
  const records = await new Promise<PreviewRecord[]>((resolve) => {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).getAll();
    request.onsuccess = () => resolve((request.result as PreviewRecord[]) || []);
    request.onerror = () => resolve([]);
  });
  if (records.length > MAX_PREVIEW_ENTRIES) {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    records
      .sort((left, right) => left.savedAt - right.savedAt)
      .slice(0, records.length - MAX_PREVIEW_ENTRIES)
      .forEach((record) => transaction.objectStore(STORE_NAME).delete(record.key));
    await new Promise<void>((resolve) => {
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => resolve();
    });
  }
  database.close();
}

export async function storeLocalPreview(userId: string, attachment: MessageAttachment, preview: Blob) {
  if (!userId || !attachment.id || !preview.size) return;
  await writeLocalPreview(userId, attachment, preview);
  window.dispatchEvent(new CustomEvent("ms-local-media-preview", { detail: { userId, attachmentId: attachment.id, cacheIdentity: attachmentCacheIdentity(attachment) } }));
}

function canvasBlob(canvas: HTMLCanvasElement) {
  return new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.78));
}

function fittedSize(width: number, height: number) {
  const scale = Math.min(1, PREVIEW_MAX_EDGE / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

async function imagePreview(source: Blob) {
  const bitmap = await createImageBitmap(source);
  try {
    const size = fittedSize(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    canvas.getContext("2d")?.drawImage(bitmap, 0, 0, size.width, size.height);
    return await canvasBlob(canvas);
  } finally {
    bitmap.close();
  }
}

async function videoPreview(source: Blob) {
  const url = URL.createObjectURL(source);
  try {
    return await new Promise<Blob | null>((resolve) => {
      const video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.preload = "auto";
      let settled = false;
      let capturePending = false;
      const timeout = window.setTimeout(() => finish(null), 12_000);
      const finish = (value: Blob | null) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        video.removeAttribute("src");
        video.load();
        resolve(value);
      };
      const drawDecodedFrame = async () => {
        if (!video.videoWidth || !video.videoHeight) return;
        const size = fittedSize(video.videoWidth, video.videoHeight);
        const canvas = document.createElement("canvas");
        canvas.width = size.width;
        canvas.height = size.height;
        const context = canvas.getContext("2d");
        if (!context) {
          finish(null);
          return;
        }
        context.drawImage(video, 0, 0, size.width, size.height);
        finish(await canvasBlob(canvas));
      };
      const capture = () => {
        if (capturePending || settled) return;
        capturePending = true;
        if (typeof video.requestVideoFrameCallback === "function") {
          video.requestVideoFrameCallback(() => void drawDecodedFrame());
        } else {
          window.setTimeout(() => void drawDecodedFrame(), 120);
        }
      };
      video.onerror = () => finish(null);
      video.onloadedmetadata = () => {
        const target = video.duration > 0.4 ? Math.min(2, Math.max(0.35, video.duration * 0.1)) : 0;
        if (target > 0) video.currentTime = target;
      };
      video.onloadeddata = () => {
        if (!video.duration || video.duration <= 0.4) capture();
      };
      video.onseeked = capture;
      video.src = url;
      video.load();
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

export async function generateAndStoreLocalPreview(userId: string, attachment: MessageAttachment, source: Blob) {
  if (!userId || !attachment.id || typeof document === "undefined") return;
  if (await readLocalPreview(userId, attachment)) return;
  const mime = (attachment.mime_type || source.type || "").toLowerCase();
  const mediaKind = (attachment.media_kind || "").toLowerCase();
  const preview = mediaKind === "video" || mime.startsWith("video/")
    ? await videoPreview(source)
    : mediaKind === "image" || mime.startsWith("image/")
      ? await imagePreview(source)
      : null;
  if (!preview) return;
  await storeLocalPreview(userId, attachment, preview);
}
