import type { CSSProperties } from "react";
import type { Message, MessageAttachment } from "../../types/chat";

export type AttachmentKind = "image" | "video" | "audio" | "file";
export type AttachmentTone = "pdf" | "sheet" | "audio" | "video" | "file";
export type CallEventPresentation = {
  title: string;
  detail: string;
  tone: "missed" | "declined" | "cancelled" | "connected" | "ended" | "neutral";
  direction: "incoming" | "outgoing" | "neutral";
  callType: "video" | "voice";
};

export function attachmentKind(attachment: MessageAttachment): AttachmentKind {
  const mediaKind = (attachment.media_kind || "").toLowerCase();
  const mime = (attachment.mime_type || "").toLowerCase();
  if (mediaKind === "image" || mime.startsWith("image/")) return "image";
  if (mediaKind === "video" || mime.startsWith("video/")) return "video";
  if (["audio", "voice", "voice_note"].includes(mediaKind) || mime.startsWith("audio/")) return "audio";
  return "file";
}

export function getAttachmentPreviewUrl(attachment: MessageAttachment) {
  return attachment.thumbnail_url || "";
}

export function getAttachmentPosterUrl(attachment: MessageAttachment) {
  return attachment.thumbnail_url || "";
}

export function getAttachmentPlaybackUrl(attachment: MessageAttachment) {
  return attachment.file_url
    || attachment.preview_url
    || attachment.signed_preview?.preview_url
    || attachment.signed_preview?.url
    || "";
}

export function getAttachmentRatioStyle(attachment: MessageAttachment): CSSProperties | undefined {
  const width = Number(attachment.width);
  const height = Number(attachment.height);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return undefined;
  return { "--ms-attachment-ratio": `${width} / ${height}` } as CSSProperties;
}

export function formatFileSize(size: number) {
  if (!size) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function attachmentTone(mime: string, fileName = ""): AttachmentTone {
  const value = mime.toLowerCase();
  if (value.includes("pdf") || fileName.toLowerCase().endsWith(".pdf")) return "pdf";
  if (value.startsWith("video/")) return "video";
  if (value.startsWith("audio/")) return "audio";
  if (value.includes("sheet") || value.includes("excel") || value.includes("csv")) return "sheet";
  return "file";
}

export function attachmentLabel(attachment: MessageAttachment) {
  const tone = attachmentTone(attachment.mime_type || "", attachment.original_name);
  if (tone === "pdf") return "PDF";
  if (tone === "sheet") return "XLS";
  if (tone === "audio") return "AUD";
  if (tone === "video") return "VID";
  const extension = attachment.original_name.split(".").pop()?.trim().slice(0, 4).toUpperCase();
  return extension || "DOC";
}

export function formatCallDuration(durationSeconds?: number) {
  if (!durationSeconds || durationSeconds <= 0) return "";
  const total = Math.max(Math.floor(durationSeconds), 0);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    const remainderMinutes = minutes % 60;
    return `${hours}h ${remainderMinutes}m`;
  }
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export function getCallEventPresentation(message: Message): CallEventPresentation | null {
  const event = message.call_event || null;
  if (!event || (event.system_event && event.system_event !== "call")) return null;

  const outcome = (event.call_outcome || event.call_status || "").toLowerCase();
  const duration = formatCallDuration(event.duration_seconds);
  const callType = event.call_type === "video" ? "video" : "voice";
  const summary = (event.summary_text || "").toLowerCase();
  const direction = summary.includes("incoming")
    ? "incoming"
    : summary.includes("outgoing")
      ? "outgoing"
      : "neutral";

  if (outcome === "missed") {
    return { title: "Missed call", detail: callType === "video" ? "Video call was not answered" : "Voice call was not answered", tone: "missed", direction, callType };
  }
  if (outcome === "declined" || outcome === "denied") {
    return { title: "Call declined", detail: callType === "video" ? "Video call declined" : "Voice call declined", tone: "declined", direction, callType };
  }
  if (outcome === "cancelled" || outcome === "canceled") {
    return { title: "Call cancelled", detail: "Ended before it was answered", tone: "cancelled", direction, callType };
  }
  if (["received", "accepted", "connected", "ongoing"].includes(outcome)) {
    return { title: "Call connected", detail: callType === "video" ? "Video call accepted" : "Voice call accepted", tone: "connected", direction, callType };
  }
  if (["completed", "ended"].includes(outcome)) {
    return { title: "Call ended", detail: duration || (callType === "video" ? "Video call" : "Voice call"), tone: "ended", direction, callType };
  }
  return {
    title: event.summary_text || "Call update",
    detail: duration || (callType === "video" ? "Video call" : "Voice call"),
    tone: "neutral",
    direction,
    callType,
  };
}

export function getFallbackMessagePreview(message: Message) {
  if (message.is_encrypted) return "Encrypted message";
  if (message.text?.trim()) return message.text.trim();
  if (message.voice_note?.is_voice_note) return "Voice note";
  if (message.call_event) return message.call_event.summary_text || "Call update";
  if (message.attachments?.length) {
    if (message.attachments.length === 1) return message.attachments[0]?.original_name || "Attachment";
    return `${message.attachments.length} attachments`;
  }
  return "Attachment or voice note";
}

export function splitAttachments(message: Message) {
  const media: MessageAttachment[] = [];
  const audio: MessageAttachment[] = [];
  const files: MessageAttachment[] = [];

  for (const attachment of message.attachments || []) {
    const kind = attachmentKind(attachment);
    if (kind === "image" || kind === "video") media.push(attachment);
    else if (kind === "audio") audio.push(attachment);
    else files.push(attachment);
  }
  return { media, audio, files };
}
