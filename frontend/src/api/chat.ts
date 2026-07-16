import { http } from "../lib/http";
import { unwrapCursorPage, unwrapData, unwrapObject } from "../lib/apiResponse";
import { resolveMediaUrl } from "../lib/mediaUrl";
import { API_BASE_URL } from "../lib/config";
import { safeId } from "../lib/safeId";
import { collectCursorPages, type CursorPage } from "../lib/pagination";
import type { Call, CallConfig, Conversation, ConversationE2EEKeyMaterial, ConversationInviteLink, E2EEDeviceKey, Message, NotificationPreferences, TurnCredentials, UserStatus } from "../types/chat";

export type ConversationNotificationSettings = {
  message_notifications_enabled?: boolean;
  call_notifications_enabled?: boolean;
  mentions_only?: boolean;
  muted_until?: string | null;
  mute_until?: string | null;
  is_currently_muted?: boolean;
};

export type ConversationMediaItem = {
  message_id: string;
  message_text?: string;
  created_at?: string;
  attachment: import("../types/chat").MessageAttachment;
  sender?: import("../types/chat").UserLite;
};


export type UploadFileOptions = {
  original_name?: string;
  mime_type?: string;
  signal?: AbortSignal;
  onProgress?: (progress: number) => void;
  metadata_source_file?: File;
  include_thumbnail?: boolean;
};

export type MessagePage = {
  results: Message[];
  next?: string | null;
  previous?: string | null;
};

export type MessageContext = {
  target_id: string;
  results: Message[];
};

export type UserBlock = {
  id: string;
  blocked: import("../types/chat").UserLite;
  reason?: string;
  created_at?: string;
};

export type UserDevice = {
  id: string;
  platform: string;
  push_token: string;
  is_active?: boolean;
  last_seen_at?: string | null;
  created_at?: string;
};

export type ChatCapabilities = {
  version?: string;
  features: Record<string, unknown>;
  limits: Record<string, unknown>;
  media: Record<string, unknown>;
  calls: Record<string, unknown>;
  security: Record<string, unknown>;
};

export type IntegrationHealth = {
  antivirus?: Record<string, unknown>;
  push?: Record<string, unknown>;
};

export type ChatAuditLog = {
  id: string;
  event_type: string;
  actor?: import("../types/chat").UserLite;
  conversation?: string;
  message?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
};

export type ModerationReport = Record<string, unknown>;
export type ModerationAction = Record<string, unknown>;

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as UnknownRecord) : {};
}

function firstString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function firstBoolean(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "boolean") return value;
  }
  return undefined;
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return undefined;
}


function inferMediaKind(file: File, mimeType?: string) {
  const normalizedMime = firstString(mimeType, file.type).toLowerCase();
  if (normalizedMime.startsWith("image/")) return "image";
  if (normalizedMime.startsWith("video/")) return "video";
  if (normalizedMime.startsWith("audio/")) return "audio";
  // PDFs use the generic file kind in the API contract. Their richer
  // presentation is inferred from application/pdf, not a separate kind.
  return "file";
}

function loadImageDimensions(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => reject(new Error("Failed to load image metadata."));
    image.src = url;
  });
}

function loadVideoMetadata(url: string): Promise<{ width: number; height: number; durationSeconds: number; posterBlob?: Blob | null }> {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    video.playsInline = true;
    video.crossOrigin = "anonymous";
    const cleanup = () => {
      video.removeAttribute("src");
      video.load();
    };
    const onError = () => {
      cleanup();
      reject(new Error("Failed to load video metadata."));
    };
    video.onerror = onError;
    video.onloadedmetadata = () => {
      const width = Number(video.videoWidth) || 0;
      const height = Number(video.videoHeight) || 0;
      const durationSeconds = Number.isFinite(video.duration) ? Math.max(video.duration, 0) : 0;
      const captureAt = durationSeconds > 0.4 ? Math.min(Math.max(0.45, durationSeconds * 0.1), 2.5) : 0;
      const finalize = (posterBlob?: Blob | null) => {
        cleanup();
        resolve({ width, height, durationSeconds, posterBlob });
      };
      const capturePoster = () => {
        if (!width || !height) {
          finalize(null);
          return;
        }
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext("2d");
        if (!context) {
          finalize(null);
          return;
        }
        context.drawImage(video, 0, 0, width, height);
        canvas.toBlob((blob) => finalize(blob), "image/jpeg", 0.94);
      };
      video.currentTime = captureAt;
      video.onseeked = capturePoster;
      if (captureAt === 0) {
        capturePoster();
      }
    };
    video.src = url;
  });
}

async function extractUploadMetadata(file: File, mimeType?: string): Promise<{
  media_kind: string;
  width?: number;
  height?: number;
  rotation?: number;
  duration_seconds?: string;
  thumbnail?: File;
}> {
  const mediaKind = inferMediaKind(file, mimeType);
  const objectUrl = URL.createObjectURL(file);
  try {
    if (mediaKind === "image") {
      const { width, height } = await loadImageDimensions(objectUrl);
      return {
        media_kind: mediaKind,
        width: width || undefined,
        height: height || undefined,
        rotation: 0,
      };
    }
    if (mediaKind === "video") {
      const { width, height, durationSeconds, posterBlob } = await loadVideoMetadata(objectUrl);
      return {
        media_kind: mediaKind,
        width: width || undefined,
        height: height || undefined,
        rotation: 0,
        duration_seconds: durationSeconds > 0 ? durationSeconds.toFixed(2) : undefined,
        thumbnail: posterBlob ? new File([posterBlob], `${file.name.replace(/\.[^.]+$/, "") || "video"}-thumb.jpg`, { type: "image/jpeg" }) : undefined,
      };
    }
    if (mediaKind === "audio") {
      return {
        media_kind: mediaKind,
      };
    }
    return { media_kind: mediaKind };
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function normalizeUserLite(value: unknown): import("../types/chat").UserLite {
  const item = asRecord(value);
  const profile = asRecord(item.profile);
  return {
    id: firstString(item.id, item.user_id),
    username: firstString(item.username, item.email, item.id) || "user",
    email: firstString(item.email) || undefined,
    display_name: firstString(item.display_name, profile.display_name, item.full_name, item.username, item.id) || "Unknown user",
    avatar: firstString(item.avatar, profile.avatar) || null,
    is_online: firstBoolean(item.is_online, profile.is_online),
    active_devices: firstNumber(item.active_devices),
    last_seen_at: firstString(item.last_seen_at) || null,
    presence_label: firstString(item.presence_label) || undefined,
    presence_status: (["active", "idle", "offline"].includes(firstString(item.presence_status))
      ? firstString(item.presence_status)
      : undefined) as import("../types/chat").UserLite["presence_status"],
    device_type: (["desktop", "mobile", "tablet"].includes(firstString(item.device_type))
      ? firstString(item.device_type)
      : null) as import("../types/chat").UserLite["device_type"],
    device_types: (Array.isArray(item.device_types) ? item.device_types : [])
      .map((entry) => String(entry))
      .filter((entry): entry is "desktop" | "mobile" | "tablet" => ["desktop", "mobile", "tablet"].includes(entry)),
    presence_visibility: firstString(item.presence_visibility, item.visibility) === "hidden" ? "hidden" : "public",
  };
}

function normalizeAttachment(value: unknown): import("../types/chat").MessageAttachment {
  const item = asRecord(value);
  const metadata = asRecord(item.metadata);
  const signedDownload = asRecord(item.signed_download);
  const signedPreview = asRecord(item.signed_preview);
  const encryption = asRecord(item.encryption);
  const signedDownloadUrl = resolveMediaUrl(firstString(signedDownload.download_url, signedDownload.url));
  const signedPreviewUrl = resolveMediaUrl(firstString(signedPreview.preview_url, signedPreview.url));
  const isEncrypted = Boolean(item.is_encrypted);
  // Prefer the stable authenticated endpoint so immutable browser caching can
  // survive refreshes; signed URLs change whenever messages are serialized.
  const resolvedFileUrl = resolveMediaUrl(firstString(item.file_url, item.url, item.download_url)) || signedDownloadUrl;
  const resolvedPreviewUrl = isEncrypted
    ? (resolvedFileUrl || signedPreviewUrl || resolveMediaUrl(firstString(item.preview_url)))
    : (signedPreviewUrl || resolveMediaUrl(firstString(item.preview_url)));
  return {
    id: firstString(item.id),
    original_name: firstString(item.original_name, item.name, item.filename) || "Attachment",
    mime_type: firstString(item.mime_type, item.content_type) || "application/octet-stream",
    media_kind: firstString(item.media_kind) || null,
    size: firstNumber(item.size, item.file_size) ?? 0,
    width: firstNumber(metadata.display_width, item.width) ?? null,
    height: firstNumber(metadata.display_height, item.height) ?? null,
    rotation: firstNumber(item.rotation) ?? null,
    duration_seconds: firstNumber(item.duration_seconds) ?? null,
    thumbnail_url: resolveMediaUrl(firstString(item.thumbnail_url)) || null,
    metadata,
    file_url: resolvedFileUrl,
    preview_url: resolvedPreviewUrl,
    can_preview_inline: firstBoolean(item.can_preview_inline),
    signed_download: Object.keys(signedDownload).length ? { download_url: signedDownloadUrl, url: signedDownloadUrl } : null,
    signed_preview: Object.keys(signedPreview).length ? { url: signedPreviewUrl, preview_url: signedPreviewUrl } : null,
    is_encrypted: isEncrypted,
    view_once: firstBoolean(item.view_once),
    view_once_opened: firstBoolean(item.view_once_opened),
    can_open_view_once: firstBoolean(item.can_open_view_once),
    encryption: Object.keys(encryption).length ? {
      version: firstString(encryption.version) || undefined,
      algorithm: firstString(encryption.algorithm),
      nonce: firstString(encryption.nonce),
      sender_key_id: firstString(encryption.sender_key_id),
      sender_device_id: firstString(encryption.sender_device_id) || undefined,
      key_version: firstNumber(encryption.key_version) ?? undefined,
      recipient_key_ids: Array.isArray(encryption.recipient_key_ids) ? encryption.recipient_key_ids.map((entry) => String(entry)) : undefined,
      encrypted_keys: Array.isArray(encryption.encrypted_keys)
        ? encryption.encrypted_keys
            .map((entry) => asRecord(entry))
            .map((entry) => ({ key_id: firstString(entry.key_id), wrapped_key: firstString(entry.wrapped_key) }))
            .filter((entry) => entry.key_id && entry.wrapped_key)
        : undefined,
      metadata_ciphertext: firstString(encryption.metadata_ciphertext),
      metadata_nonce: firstString(encryption.metadata_nonce),
      original_sha256: firstString(encryption.original_sha256) || undefined,
      preview_ciphertext: firstString(encryption.preview_ciphertext) || undefined,
      preview_nonce: firstString(encryption.preview_nonce) || undefined,
      preview_mime_type: firstString(encryption.preview_mime_type) || undefined,
      aad: asRecord(encryption.aad),
    } : null,
  };
}

function normalizeUserStatus(value: unknown): UserStatus {
  const item = asRecord(value);
  const media = asRecord(item.media);
  const contentType = firstString(item.content_type);
  const mediaKind = firstString(media.media_kind);
  return {
    id: firstString(item.id),
    author: normalizeUserLite(item.author),
    content_type: (["text", "image", "video"].includes(contentType) ? contentType : "text") as UserStatus["content_type"],
    text: firstString(item.text),
    background_color: firstString(item.background_color) || "#111111",
    text_color: firstString(item.text_color) || "#ffffff",
    media: Object.keys(media).length ? {
      upload_id: firstString(media.upload_id),
      media_kind: (mediaKind === "video" ? "video" : "image"),
      mime_type: firstString(media.mime_type) || undefined,
      preview_url: resolveMediaUrl(firstString(media.preview_url)) || "",
      thumbnail_url: resolveMediaUrl(firstString(media.thumbnail_url)) || null,
      width: firstNumber(media.width) ?? null,
      height: firstNumber(media.height) ?? null,
      duration_seconds: firstNumber(media.duration_seconds) ?? null,
    } : null,
    is_viewed: Boolean(item.is_viewed),
    is_own: Boolean(item.is_own),
    view_count: firstNumber(item.view_count) ?? 0,
    created_at: firstString(item.created_at),
    expires_at: firstString(item.expires_at),
  };
}

function normalizeReactionSummary(value: unknown): Record<string, number> {
  if (!value) return {};
  if (Array.isArray(value)) {
    const result: Record<string, number> = {};
    for (const entry of value) {
      const item = asRecord(entry);
      const emoji = firstString(item.emoji);
      const count = firstNumber(item.count, item.total) ?? 0;
      if (emoji && count > 0) result[emoji] = count;
    }
    return result;
  }
  if (typeof value === "object") {
    const result: Record<string, number> = {};
    for (const [emoji, count] of Object.entries(value as Record<string, unknown>)) {
      const numeric = firstNumber(count);
      if (emoji && numeric && numeric > 0) result[emoji] = numeric;
    }
    return result;
  }
  return {};
}

function normalizeNumericMap(value: unknown): Record<string, number> {
  const item = asRecord(value);
  const result: Record<string, number> = {};
  for (const [key, raw] of Object.entries(item)) {
    const numeric = firstNumber(raw);
    if (!key || numeric === undefined) continue;
    result[key] = numeric;
  }
  return result;
}

function normalizeReaction(value: unknown): import("../types/chat").Reaction {
  const item = asRecord(value);
  return {
    id: firstString(item.id, safeId("reaction")),
    emoji: firstString(item.emoji),
    user: normalizeUserLite(item.user ?? item.actor ?? item.created_by),
    created_at: firstString(item.created_at) || new Date().toISOString(),
  };
}

function normalizeDelivery(value: unknown): import("../types/chat").DeliveryReceipt {
  const item = asRecord(value);
  return {
    id: firstString(item.id, safeId("delivery")),
    user: normalizeUserLite(item.user ?? item.actor ?? item.created_by),
    delivered_at: firstString(item.delivered_at, item.created_at) || undefined,
  };
}

export function normalizeMessage(value: unknown): Message {
  const item = asRecord(value);
  const sender = normalizeUserLite(item.sender ?? item.created_by ?? item.user);
  const metadata = asRecord(item.metadata);
  const callEvent = asRecord(metadata.call_event ?? (metadata.system_event === "call" ? metadata : undefined));
  const voiceNote = asRecord(item.voice_note ?? metadata.voice_note);
  const transcript = asRecord(item.transcript);
  const replyPreview = asRecord(item.reply_preview ?? item.reply_to_snapshot);
  const encryption = asRecord(item.encryption);
  const rawAttachments = Array.isArray(item.attachments) ? item.attachments : Array.isArray(item.files) ? item.files : [];
  const reactions = Array.isArray(item.reactions) ? item.reactions.map(normalizeReaction).filter((entry) => entry.emoji) : [];
  const deliveries = Array.isArray(item.deliveries) ? item.deliveries.map(normalizeDelivery).filter((entry) => entry.user.id) : [];
  const text = firstString(item.text, item.body, item.content);
  const links = Array.isArray(item.links)
    ? item.links.filter((entry): entry is string => typeof entry === "string")
    : Array.from(text.matchAll(/https?:\/\/\S+/g)).map((match) => match[0]);

  return {
    id: firstString(item.id),
    conversation_id: firstString(item.conversation_id, item.conversation) || undefined,
    type: firstString(item.type, item.message_type) || "text",
    text,
    sender,
    created_at: firstString(item.created_at, item.sent_at) || new Date().toISOString(),
    updated_at: firstString(item.updated_at, item.edited_at) || undefined,
    attachments: rawAttachments.map(normalizeAttachment).filter((entry) => entry.id),
    delivery_status: firstString(item.delivery_status, item.status) || undefined,
    failed_reason: firstString(item.failed_reason, item.error_message) || null,
    retry_count: firstNumber(item.retry_count) ?? 0,
    is_deleted: Boolean(item.is_deleted ?? item.deleted_at),
    can_edit: typeof item.can_edit === "boolean" ? item.can_edit : undefined,
    edit_locked_reason: firstString(item.edit_locked_reason) || undefined,
    edit_deadline: firstString(item.edit_deadline) || undefined,
    transcript: Object.keys(transcript).length ? { text: firstString(transcript.text, transcript.content) || undefined } : null,
    voice_note: Object.keys(voiceNote).length
      ? {
          is_voice_note: Boolean(voiceNote.is_voice_note ?? metadata.voice_note),
          duration_seconds: firstNumber(voiceNote.duration_seconds, voiceNote.duration, item.duration_seconds) ?? null,
          waveform: Array.isArray(voiceNote.waveform)
            ? voiceNote.waveform.map(Number).filter(Number.isFinite)
            : [],
        }
      : null,
    call_event: Object.keys(callEvent).length
      ? {
          system_event: firstString(callEvent.system_event) || undefined,
          call_id: firstString(callEvent.call_id) || undefined,
          call_type: firstString(callEvent.call_type) || undefined,
          call_status: firstString(callEvent.call_status) || undefined,
          call_outcome: firstString(callEvent.call_outcome) || undefined,
          summary_text: firstString(callEvent.summary_text) || undefined,
          duration_seconds: firstNumber(callEvent.duration_seconds) ?? undefined,
          ringing_duration_seconds: firstNumber(callEvent.ringing_duration_seconds) ?? undefined,
          reason: firstString(callEvent.reason) || undefined,
          initiated_by_id: firstString(callEvent.initiated_by_id) || undefined,
          answered_by_id: firstString(callEvent.answered_by_id) || undefined,
          actor_id: firstString(callEvent.actor_id) || undefined,
        }
      : null,
    reply_preview: Object.keys(replyPreview).length
      ? { id: firstString(replyPreview.id) || undefined, text: firstString(replyPreview.text, replyPreview.body) || "Attachment / voice note" }
      : null,
    reactions,
    deliveries,
    reaction_summary: normalizeReactionSummary(item.reaction_summary),
    entities: Array.isArray(item.entities) ? (item.entities as Message["entities"]) : [],
    links,
    mentioned_user_ids: Array.isArray(item.mentioned_user_ids) ? item.mentioned_user_ids.map((entry) => String(entry)) : [],
    metadata,
    client_temp_id: firstString(item.client_temp_id) || undefined,
    is_encrypted: Boolean(item.is_encrypted ?? metadata.encrypted),
    encryption: Object.keys(encryption).length ? {
      version: firstString(encryption.version) || undefined,
      algorithm: firstString(encryption.algorithm),
      ciphertext: firstString(encryption.ciphertext),
      nonce: firstString(encryption.nonce),
      sender_key_id: firstString(encryption.sender_key_id),
      sender_device_id: firstString(encryption.sender_device_id) || undefined,
      key_version: firstNumber(encryption.key_version) ?? undefined,
      recipient_key_ids: Array.isArray(encryption.recipient_key_ids) ? encryption.recipient_key_ids.map((entry) => String(entry)) : undefined,
      encrypted_keys: Array.isArray(encryption.encrypted_keys)
        ? encryption.encrypted_keys
            .map((entry) => asRecord(entry))
            .map((entry) => ({ key_id: firstString(entry.key_id), wrapped_key: firstString(entry.wrapped_key) }))
            .filter((entry) => entry.key_id && entry.wrapped_key)
        : undefined,
      aad: asRecord(encryption.aad),
    } : null,
  };
}

function normalizeParticipant(value: unknown): import("../types/chat").Participant {
  const item = asRecord(value);
  return {
    id: firstString(item.id, item.user_id),
    role: firstString(item.role) || "member",
    user: normalizeUserLite(item.user ?? item.member ?? item.account),
    is_muted: firstBoolean(item.is_muted),
    is_archived: firstBoolean(item.is_archived),
    is_pinned: firstBoolean(item.is_pinned),
    is_blocked: firstBoolean(item.is_blocked),
    left_at: firstString(item.left_at) || null,
    last_read_message: firstString(item.last_read_message) || null,
    last_read_at: firstString(item.last_read_at) || null,
    last_delivered_message: firstString(item.last_delivered_message) || null,
    last_delivered_at: firstString(item.last_delivered_at) || null,
  };
}

function buildConversationTitle(raw: UnknownRecord, participants: import("../types/chat").Participant[]) {
  const explicit = firstString(raw.title, raw.name);
  if (explicit) return explicit;
  const names = participants.map((item) => item.user.display_name || item.user.username).filter(Boolean);
  return names.slice(0, 3).join(", ") || "Conversation";
}

export function normalizeConversation(value: unknown): Conversation {
  const item = asRecord(value);
  const participants = (Array.isArray(item.participants) ? item.participants : []).map(normalizeParticipant);
  const lastMessageRaw = item.last_message ?? item.latest_message;
  return {
    id: firstString(item.id),
    type: firstString(item.type, item.conversation_type) === "group" ? "group" : "direct",
    title: firstString(item.type, item.conversation_type) === "group" ? buildConversationTitle(item, participants) : firstString(item.title, item.name),
    slug: firstString(item.slug, item.route_name) || null,
    unread_count: firstNumber(item.unread_count, item.unread_messages) ?? 0,
    e2ee_key_version: firstNumber(item.e2ee_key_version) ?? undefined,
    e2ee_rekey_required: firstBoolean(item.e2ee_rekey_required),
    e2ee_last_key_rotation_at: firstString(item.e2ee_last_key_rotation_at) || null,
    e2ee_last_security_event_at: firstString(item.e2ee_last_security_event_at) || null,
    participants,
    last_message: lastMessageRaw ? normalizeMessage(lastMessageRaw) : null,
    last_message_at: firstString(item.last_message_at, asRecord(lastMessageRaw).created_at) || null,
  };
}

function normalizeCallParticipant(value: unknown): import("../types/chat").CallParticipant {
  const item = asRecord(value);
  return {
    id: firstString(item.id),
    state: firstString(item.state, item.status) || "unknown",
    network_quality: firstString(item.network_quality) || undefined,
    preferred_video_quality: firstString(item.preferred_video_quality) || undefined,
    audio_enabled: firstBoolean(item.audio_enabled, item.microphone_enabled),
    video_enabled: firstBoolean(item.video_enabled, item.camera_enabled),
    is_on_hold: firstBoolean(item.is_on_hold),
    reconnecting: firstBoolean(item.reconnecting),
    connection_state: firstString(item.connection_state) || undefined,
    audio_route: firstString(item.audio_route) || undefined,
    screen_share_enabled: firstBoolean(item.screen_share_enabled, item.screen_sharing),
    hand_raised: firstBoolean(item.hand_raised),
    is_speaking: firstBoolean(item.is_speaking),
    speaking_level: firstNumber(item.speaking_level),
    quality_score: firstNumber(item.quality_score),
    quality_alert: firstString(item.quality_alert) || undefined,
    user: normalizeUserLite(item.user ?? item.participant_user),
  };
}

export function normalizeCall(value: unknown): Call {
  const item = asRecord(value);
  return {
    id: firstString(item.id),
    conversation: firstString(item.conversation, asRecord(item.conversation_data).id) || undefined,
    initiated_by: item.initiated_by ? normalizeUserLite(item.initiated_by) : undefined,
    answered_by: item.answered_by ? normalizeUserLite(item.answered_by) : null,
    call_type: firstString(item.call_type, item.type) === "video" ? "video" : "voice",
    status: firstString(item.status) || "unknown",
    started_at: firstString(item.started_at, item.created_at) || new Date().toISOString(),
    answered_at: firstString(item.answered_at) || null,
    ended_at: firstString(item.ended_at) || null,
    ended_reason: firstString(item.ended_reason) || null,
    duration_seconds: firstNumber(item.duration_seconds) ?? null,
    ringing_seconds: firstNumber(item.ringing_seconds) ?? null,
    ring_timeout_seconds: firstNumber(item.ring_timeout_seconds) ?? null,
    call_state: firstString(item.call_state) || undefined,
    participant_summary: normalizeNumericMap(item.participant_summary),
    participants: Array.isArray(item.participants) ? item.participants.map(normalizeCallParticipant) : [],
    network_recommendation: asRecord(item.network_recommendation),
  };
}

function normalizeNotificationPreferences(value: unknown): NotificationPreferences {
  const item = asRecord(value);
  return {
    id: firstString(item.id) || undefined,
    push_enabled: firstBoolean(item.push_enabled),
    email_enabled: firstBoolean(item.email_enabled),
    message_preview_enabled: firstBoolean(item.message_preview_enabled),
    mute_all: firstBoolean(item.mute_all),
    call_quality_preference: (firstString(item.call_quality_preference) as NotificationPreferences["call_quality_preference"]) || "auto",
  };
}

function normalizeInviteLink(value: unknown): ConversationInviteLink {
  const item = asRecord(value);
  return {
    id: firstString(item.id),
    conversation: firstString(item.conversation),
    token: firstString(item.token),
    expires_at: firstString(item.expires_at) || null,
    revoked_at: firstString(item.revoked_at) || null,
    max_uses: firstNumber(item.max_uses),
    use_count: firstNumber(item.use_count),
    is_active: firstBoolean(item.is_active),
    join_url: firstString(item.join_url) || undefined,
    created_at: firstString(item.created_at) || undefined,
  };
}

function normalizeCallConfig(value: unknown): CallConfig {
  const item = asRecord(value);
  const presets = Array.isArray(item.available_quality_presets)
    ? item.available_quality_presets.filter((entry): entry is string => typeof entry === "string")
    : [];
  return {
    available_quality_presets: presets,
    selected_quality_preset: firstString(item.selected_quality_preset, item.default_quality_preset) || presets[0] || "auto",
    applied_quality_profile: asRecord(item.applied_quality_profile),
    ice_servers: Array.isArray(item.ice_servers) ? (item.ice_servers as RTCIceServer[]) : [],
    ice_transport_policy: firstString(item.ice_transport_policy) as RTCIceTransportPolicy | undefined,
    ice_candidate_pool_size: firstNumber(item.ice_candidate_pool_size),
    reconnect_grace_seconds: firstNumber(item.reconnect_grace_seconds),
    quality_report_interval_seconds: firstNumber(item.quality_report_interval_seconds),
    heartbeat_interval_seconds: firstNumber(item.heartbeat_interval_seconds),
    codec_preferences: asRecord(item.codec_preferences),
    quality_reporting: asRecord(item.quality_reporting),
  };
}

function normalizeTurnCredentials(value: unknown): TurnCredentials {
  const item = asRecord(value);
  return {
    configured: firstBoolean(item.configured) ?? false,
    ttl_seconds: firstNumber(item.ttl_seconds) ?? 0,
    username: firstString(item.username) || undefined,
    credential: firstString(item.credential) || undefined,
    credential_type: firstString(item.credential_type) || undefined,
    ice_servers: Array.isArray(item.ice_servers) ? (item.ice_servers as RTCIceServer[]) : [],
  };
}

function normalizeConversationNotifications(value: unknown): ConversationNotificationSettings {
  const item = asRecord(value);
  const mutedUntil = firstString(item.muted_until, item.mute_until) || null;
  return {
    message_notifications_enabled: firstBoolean(item.message_notifications_enabled),
    call_notifications_enabled: firstBoolean(item.call_notifications_enabled),
    mentions_only: firstBoolean(item.mentions_only),
    muted_until: mutedUntil,
    mute_until: mutedUntil,
    is_currently_muted: firstBoolean(item.is_currently_muted),
  };
}

function normalizeConversationMediaItem(value: unknown): ConversationMediaItem {
  const item = asRecord(value);
  const message = asRecord(item.message);
  return {
    message_id: firstString(item.message_id, message.id),
    message_text: firstString(item.message_text, message.text) || undefined,
    created_at: firstString(item.created_at, message.created_at) || undefined,
    attachment: normalizeAttachment(item.attachment ?? item.file ?? item),
    sender: item.sender || item.message ? normalizeUserLite(item.sender ?? message.sender) : undefined,
  };
}

function normalizeE2EEDeviceKey(value: unknown): E2EEDeviceKey {
  const item = asRecord(value);
  return {
    id: firstString(item.id, item.key_id),
    device_id: firstString(item.device_id),
    key_id: firstString(item.key_id),
    label: firstString(item.label) || undefined,
    algorithm: firstString(item.algorithm),
    fingerprint: firstString(item.fingerprint) || undefined,
    public_key_jwk: asRecord(item.public_key_jwk) as JsonWebKey,
    is_active: firstBoolean(item.is_active),
    revoked_at: firstString(item.revoked_at) || null,
    last_seen_at: firstString(item.last_seen_at) || undefined,
    security_changed: firstBoolean(item.security_changed),
  };
}

function normalizeUserBlock(value: unknown): UserBlock {
  const item = asRecord(value);
  return {
    id: firstString(item.id),
    blocked: normalizeUserLite(item.blocked),
    reason: firstString(item.reason) || undefined,
    created_at: firstString(item.created_at) || undefined,
  };
}

function normalizeUserDevice(value: unknown): UserDevice {
  const item = asRecord(value);
  return {
    id: firstString(item.id),
    platform: firstString(item.platform) || "web",
    push_token: firstString(item.push_token),
    is_active: firstBoolean(item.is_active),
    last_seen_at: firstString(item.last_seen_at) || null,
    created_at: firstString(item.created_at) || undefined,
  };
}

function normalizeCapabilities(value: unknown): ChatCapabilities {
  const item = asRecord(value);
  return {
    version: firstString(item.version) || undefined,
    features: asRecord(item.features),
    limits: asRecord(item.limits),
    media: asRecord(item.media),
    calls: asRecord(item.calls),
    security: asRecord(item.security),
  };
}

function normalizeAuditLog(value: unknown): ChatAuditLog {
  const item = asRecord(value);
  return {
    id: firstString(item.id),
    event_type: firstString(item.event_type),
    actor: item.actor ? normalizeUserLite(item.actor) : undefined,
    conversation: firstString(item.conversation) || undefined,
    message: firstString(item.message) || undefined,
    metadata: asRecord(item.metadata),
    created_at: firstString(item.created_at) || undefined,
  };
}

type PaginatedChatRequestOptions<T> = {
  params?: Record<string, unknown>;
  signal?: AbortSignal;
  getKey?: (item: T) => string;
  maxPages?: number;
};

async function collectChatPages<T>(
  initialPath: string,
  normalize: (value: unknown) => T,
  options: PaginatedChatRequestOptions<T> = {},
): Promise<T[]> {
  let firstPage = true;
  return collectCursorPages<T>(
    initialPath,
    async (url, signal): Promise<CursorPage<T>> => {
      const response = await http.get(url, {
        signal,
        params: firstPage ? options.params : undefined,
      });
      firstPage = false;
      const page = unwrapCursorPage<unknown>(response.data);
      return {
        results: page.results.map(normalize),
        next: page.next,
        previous: page.previous,
      };
    },
    {
      signal: options.signal,
      getKey: options.getKey,
      maxPages: options.maxPages,
      baseUrl: API_BASE_URL,
    },
  );
}

function normalizeMessagePage(value: unknown): MessagePage {
  const envelope = asRecord(value);
  const payload = "data" in envelope && envelope.data !== undefined ? envelope.data : value;
  if (Array.isArray(payload)) {
    return {
      results: payload.map(normalizeMessage).filter((item) => Boolean(item.id)),
      next: null,
      previous: null,
    };
  }
  const item = asRecord(payload);
  const results = Array.isArray(item.results) ? item.results : Array.isArray(item.data) ? item.data : [];
  return {
    results: results.map(normalizeMessage).filter((message) => Boolean(message.id)),
    next: firstString(item.next) || null,
    previous: firstString(item.previous) || null,
  };
}

export const chatApi = {
  async getCapabilities() {
    const response = await http.get("/chat/capabilities/");
    return normalizeCapabilities(unwrapData<unknown>(response.data));
  },
  async getIntegrationHealth() {
    const response = await http.get("/chat/integrations/health/");
    return unwrapObject<IntegrationHealth>(response.data, {});
  },
  async listConversations(signal?: AbortSignal) {
    const items = await collectChatPages("/chat/conversations/", normalizeConversation, {
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => Boolean(item.id));
  },
  async createDirectConversation(userId: string) {
    const response = await http.post("/chat/conversations/", {
      type: "direct",
      participant_ids: [Number.isFinite(Number(userId)) ? Number(userId) : userId],
    });
    return normalizeConversation(unwrapData<unknown>(response.data));
  },
  async createGroupConversation(title: string, uniqueName: string, participantIds: string[]) {
    const response = await http.post("/chat/conversations/", {
      type: "group",
      title,
      slug: uniqueName,
      participant_ids: participantIds.map((id) => (Number.isFinite(Number(id)) ? Number(id) : id)),
    });
    return normalizeConversation(unwrapData<unknown>(response.data));
  },
  async checkGroupNameAvailability(name: string, signal?: AbortSignal) {
    const response = await http.get("/chat/conversations/group-name-availability/", { params: { name }, signal });
    const payload = unwrapObject<{ available?: boolean; normalized?: string; message?: string }>(response.data, {});
    return { available: Boolean(payload.available), normalized: String(payload.normalized || ""), message: String(payload.message || "") };
  },
  async getConversation(id: string) {
    const response = await http.get(`/chat/conversations/${id}/`);
    return normalizeConversation(unwrapData<unknown>(response.data));
  },
  async getDirectConversationByUsername(username: string) {
    const response = await http.get(`/chat/conversations/by-username/${encodeURIComponent(username.replace(/^@/, ""))}/`);
    return normalizeConversation(unwrapData<unknown>(response.data));
  },
  async getConversationByRoute(routeKey: string) {
    const normalized = routeKey.replace(/^@/, "").trim();
    const response = await http.get(`/chat/conversations/by-route/${encodeURIComponent(normalized)}/`);
    return normalizeConversation(unwrapData<unknown>(response.data));
  },
  async deleteConversation(conversationId: string) {
    await http.delete(`/chat/conversations/${conversationId}/`);
  },
  async searchConversations(query: string, signal?: AbortSignal) {
    const items = await collectChatPages("/chat/conversations/search/", normalizeConversation, {
      params: { q: query },
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => Boolean(item.id));
  },
  async toggleConversationMute(conversationId: string) {
    const response = await http.post(`/chat/conversations/${conversationId}/mute/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async toggleConversationArchive(conversationId: string) {
    const response = await http.post(`/chat/conversations/${conversationId}/archive/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async toggleConversationPin(conversationId: string) {
    const response = await http.post(`/chat/conversations/${conversationId}/pin/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async addGroupParticipants(conversationId: string, participantIds: string[]) {
    const response = await http.post(`/chat/conversations/${conversationId}/participants/`, {
      participant_ids: participantIds.map((id) => (Number.isFinite(Number(id)) ? Number(id) : id)),
    });
    return normalizeConversation(unwrapData<unknown>(response.data));
  },
  async removeGroupParticipant(conversationId: string, userId: string) {
    const response = await http.delete(`/chat/conversations/${conversationId}/participants/${userId}/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async updateGroupParticipantRole(conversationId: string, userId: string, role: "member" | "admin") {
    const response = await http.patch(`/chat/conversations/${conversationId}/participants/${userId}/role/`, { role });
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async muteGroupParticipant(conversationId: string, userId: string, minutes: number) {
    const response = await http.post(`/chat/conversations/${conversationId}/participants/${userId}/mute/`, { minutes });
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async banGroupParticipant(conversationId: string, userId: string, reason?: string) {
    const response = await http.post(`/chat/conversations/${conversationId}/participants/${userId}/ban/`, { reason: reason ?? "" });
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async unbanGroupParticipant(conversationId: string, userId: string) {
    const response = await http.delete(`/chat/conversations/${conversationId}/participants/${userId}/ban/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async transferGroupOwnership(conversationId: string, targetUserId: string) {
    const response = await http.post(`/chat/conversations/${conversationId}/transfer-ownership/`, { target_user_id: targetUserId });
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async leaveConversation(conversationId: string) {
    const response = await http.post(`/chat/conversations/${conversationId}/leave/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async listInviteLinks(conversationId: string, signal?: AbortSignal) {
    const items = await collectChatPages(`/chat/conversations/${conversationId}/invite-links/`, normalizeInviteLink, {
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => item.id);
  },
  async createInviteLink(conversationId: string, payload?: { expires_in_hours?: number; max_uses?: number }) {
    const response = await http.post(`/chat/conversations/${conversationId}/invite-links/`, payload ?? {});
    return normalizeInviteLink(unwrapData<unknown>(response.data));
  },
  async revokeInviteLink(conversationId: string, inviteId: string) {
    const response = await http.post(`/chat/conversations/${conversationId}/invite-links/${inviteId}/revoke/`);
    return normalizeInviteLink(unwrapData<unknown>(response.data));
  },
  async listMessages(conversationId: string, pageUrl?: string | null, signal?: AbortSignal) {
    const response = await http.get(pageUrl || `/chat/conversations/${conversationId}/messages/`, { signal });
    return normalizeMessagePage(response.data);
  },
  async sendMessage(conversationId: string, payload: Record<string, unknown>) {
    const requestPayload = Object.fromEntries(
      Object.entries(payload).filter(([key]) => !key.startsWith("_")),
    );
    const response = await http.post(`/chat/conversations/${conversationId}/messages/`, requestPayload);
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async openViewOnceAttachment(attachmentId: string) {
    const response = await http.post(`/chat/attachments/${attachmentId}/view-once/open/`);
    const payload = asRecord(unwrapData<unknown>(response.data));
    const url = resolveMediaUrl(firstString(payload.preview_url, payload.url));
    if (!url) throw new Error("A secure viewing session could not be created.");
    return url;
  },
  async editMessage(messageId: string, payload: Record<string, unknown>) {
    const response = await http.patch(`/chat/messages/${messageId}/manage/`, payload);
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async deleteMessage(messageId: string) {
    const response = await http.delete(`/chat/messages/${messageId}/manage/`);
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async getMessage(messageId: string) {
    const response = await http.get(`/chat/messages/${messageId}/`);
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async getMessageContext(messageId: string, signal?: AbortSignal): Promise<MessageContext> {
    const response = await http.get(`/chat/messages/${messageId}/context/`, { signal });
    const payload = asRecord(unwrapData<unknown>(response.data));
    const results = Array.isArray(payload.results) ? payload.results.map(normalizeMessage).filter((message) => Boolean(message.id)) : [];
    return {
      target_id: firstString(payload.target_id, messageId),
      results,
    };
  },
  async searchMessages(query: string, conversationId?: string, signal?: AbortSignal) {
    const items = await collectChatPages("/chat/messages/search/", normalizeMessage, {
      params: { q: query, conversation_id: conversationId || undefined },
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => Boolean(item.id));
  },
  async markConversationDelivered(conversationId: string, payload: Record<string, unknown> = {}) {
    const response = await http.post(`/chat/conversations/${conversationId}/mark-delivered/`, payload);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async markConversationRead(conversationId: string, payload: Record<string, unknown> = {}) {
    const response = await http.post(`/chat/conversations/${conversationId}/mark-read/`, payload);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async retryMessage(messageId: string) {
    const response = await http.post(`/chat/messages/${messageId}/retry/`);
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async reactToMessage(messageId: string, emoji: string) {
    const response = await http.post(`/chat/messages/${messageId}/reactions/`, { emoji }, { timeout: 12000 });
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async removeReaction(messageId: string, emoji: string) {
    const response = await http.delete(`/chat/messages/${messageId}/reactions/`, { data: { emoji }, timeout: 12000 });
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async forwardMessage(messageId: string, conversationId: string) {
    const response = await http.post(`/chat/messages/${messageId}/forward/`, { conversation_id: conversationId });
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async reportMessage(messageId: string, payload: { reason?: string; details?: string } = {}) {
    const response = await http.post(`/chat/messages/${messageId}/report/`, payload);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async upsertMessageTranscript(messageId: string, payload: { text?: string; language_code?: string; confidence?: number; source?: string }) {
    const response = await http.post(`/chat/messages/${messageId}/transcript/`, payload);
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async failMessage(messageId: string, reason: string) {
    const response = await http.post(`/chat/messages/${messageId}/fail/`, { reason });
    return normalizeMessage(unwrapData<unknown>(response.data));
  },
  async uploadFile(file: File, options?: UploadFileOptions) {
    if (options?.signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    let extracted: Awaited<ReturnType<typeof extractUploadMetadata>>;
    try {
      extracted = await extractUploadMetadata(options?.metadata_source_file || file, options?.mime_type);
    } catch {
      // Metadata improves presentation but must never make an otherwise valid
      // attachment impossible to upload. The backend will probe supported
      // unencrypted media after the security scan.
      extracted = { media_kind: inferMediaKind(options?.metadata_source_file || file, options?.mime_type) };
    }
    const localThumbnail = extracted.thumbnail;
    if (options?.include_thumbnail === false) extracted.thumbnail = undefined;
    if (options?.signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    const formData = new FormData();
    formData.append("file", file);
    if (options?.original_name) formData.append("original_name", options.original_name);
    if (options?.mime_type) formData.append("mime_type", options.mime_type);
    if (extracted.media_kind) formData.append("media_kind", extracted.media_kind);
    if (extracted.width) formData.append("width", String(extracted.width));
    if (extracted.height) formData.append("height", String(extracted.height));
    if (typeof extracted.rotation === "number") formData.append("rotation", String(extracted.rotation));
    if (extracted.duration_seconds) formData.append("duration_seconds", extracted.duration_seconds);
    if (extracted.thumbnail) formData.append("thumbnail", extracted.thumbnail, extracted.thumbnail.name);
    const response = await http.post("/chat/uploads/", formData, {
      signal: options?.signal,
      onUploadProgress: options?.onProgress
        ? (event) => {
            const total = event.total || file.size || 0;
            if (total > 0) options.onProgress?.((event.loaded / total) * 100);
          }
        : undefined,
    });
    const payload = unwrapData<unknown>(response.data);
    const item = asRecord(payload);
    return {
      id: firstString(item.id, item.upload_id),
      localThumbnail,
      mediaKind: extracted.media_kind,
      width: extracted.width,
      height: extracted.height,
      rotation: extracted.rotation,
      durationSeconds: extracted.duration_seconds ? Number(extracted.duration_seconds) : undefined,
    };
  },
  async listStatuses(signal?: AbortSignal) {
    const response = await http.get("/chat/statuses/", { signal });
    const payload = unwrapData<unknown>(response.data);
    const record = asRecord(payload);
    const items = Array.isArray(payload) ? payload : Array.isArray(record.results) ? record.results : [];
    return items.map(normalizeUserStatus).filter((item) => Boolean(item.id));
  },
  async createStatus(payload: { text?: string; upload_id?: string; background_color?: string; text_color?: string }) {
    const response = await http.post("/chat/statuses/", payload);
    return normalizeUserStatus(unwrapData<unknown>(response.data));
  },
  async markStatusViewed(statusId: string) {
    const response = await http.post(`/chat/statuses/${statusId}/view/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async deleteStatus(statusId: string) {
    await http.delete(`/chat/statuses/${statusId}/`);
  },
  async listCalls(status?: string, signal?: AbortSignal) {
    const items = await collectChatPages("/chat/calls/recent/", normalizeCall, {
      params: status ? { status } : undefined,
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => Boolean(item.id));
  },
  async startCall(conversationId: string, payload: { call_type: "voice" | "video"; metadata?: Record<string, unknown> }) {
    const response = await http.post(`/chat/conversations/${conversationId}/calls/start/`, payload);
    return normalizeCall(unwrapData<unknown>(response.data));
  },
  async getCall(callId: string) {
    const response = await http.get(`/chat/calls/${callId}/`);
    return normalizeCall(unwrapData<unknown>(response.data));
  },
  async acceptCall(callId: string) {
    const response = await http.post(`/chat/calls/${callId}/accept/`);
    return normalizeCall(unwrapData<unknown>(response.data));
  },
  async declineCall(callId: string, reason?: string) {
    const response = await http.post(`/chat/calls/${callId}/decline/`, reason ? { reason } : {});
    return normalizeCall(unwrapData<unknown>(response.data));
  },
  async endCall(callId: string, reason?: string) {
    const response = await http.post(`/chat/calls/${callId}/end/`, reason ? { reason } : {});
    return normalizeCall(unwrapData<unknown>(response.data));
  },
  async sendCallSignal(callId: string, signal_type: string, payload?: Record<string, unknown>) {
    const response = await http.post(`/chat/calls/${callId}/signal/`, { signal_type, payload: payload ?? {} });
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async updateCallMediaState(callId: string, payload: Record<string, unknown>) {
    const response = await http.post(`/chat/calls/${callId}/media-state/`, payload);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async sendCallHeartbeat(callId: string, payload?: Record<string, unknown>) {
    const response = await http.post(`/chat/calls/${callId}/heartbeat/`, payload ?? {});
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async getCallOrchestration(callId: string) {
    const response = await http.get(`/chat/calls/${callId}/orchestration/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async getCallDiagnostics(callId: string) {
    const response = await http.get(`/chat/calls/${callId}/diagnostics/`);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async sendCallQualityReport(callId: string, payload: Record<string, unknown>) {
    const response = await http.post(`/chat/calls/${callId}/quality-report/`, payload);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async updateCallSpeakerState(callId: string, payload: Record<string, unknown>) {
    const response = await http.post(`/chat/calls/${callId}/speaker-state/`, payload);
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async getNotificationPreferences() {
    const response = await http.get("/chat/notifications/preferences/");
    return normalizeNotificationPreferences(unwrapData<unknown>(response.data));
  },
  async updateNotificationPreferences(payload: Partial<NotificationPreferences>) {
    const response = await http.patch("/chat/notifications/preferences/", payload);
    return normalizeNotificationPreferences(unwrapData<unknown>(response.data));
  },
  async getCallingConfig(quality?: string) {
    const response = await http.get("/chat/calls/config/", { params: quality ? { quality } : undefined });
    return normalizeCallConfig(unwrapData<unknown>(response.data));
  },
  async getTurnCredentials() {
    const response = await http.get("/chat/calls/turn-credentials/");
    return normalizeTurnCredentials(unwrapData<unknown>(response.data));
  },
  async getConversationNotifications(conversationId: string) {
    const response = await http.get(`/chat/conversations/${conversationId}/notifications/`);
    return normalizeConversationNotifications(unwrapData<unknown>(response.data));
  },
  async updateConversationNotifications(conversationId: string, payload: Partial<ConversationNotificationSettings>) {
    const { mute_until, ...requestPayload } = payload;
    const response = await http.patch(`/chat/conversations/${conversationId}/notifications/`, {
      ...requestPayload,
      muted_until: payload.muted_until ?? mute_until,
    });
    return normalizeConversationNotifications(unwrapData<unknown>(response.data));
  },
  async listConversationMedia(
    conversationId: string,
    kind: "all" | "image" | "video" | "audio" | "file" = "all",
    signal?: AbortSignal,
  ) {
    const items = await collectChatPages(`/chat/conversations/${conversationId}/media/`, normalizeConversationMediaItem, {
      params: { kind },
      signal,
      getKey: (item) => `${item.message_id}:${item.attachment?.id || ""}`,
    });
    return items.filter((item) => Boolean(item.attachment?.id));
  },
  async registerE2EEDeviceKey(payload: Record<string, unknown>) {
    const response = await http.post("/chat/e2ee/devices/", payload);
    return normalizeE2EEDeviceKey(unwrapData<unknown>(response.data));
  },
  async listE2EEDeviceKeys(signal?: AbortSignal) {
    const items = await collectChatPages("/chat/e2ee/devices/", normalizeE2EEDeviceKey, {
      signal,
      getKey: (item) => item.id || item.key_id,
    });
    return items.filter((item) => item.key_id);
  },
  async revokeE2EEDeviceKey(keyId: string) {
    const response = await http.post(`/chat/e2ee/devices/${keyId}/revoke/`);
    return normalizeE2EEDeviceKey(unwrapData<unknown>(response.data));
  },
  async getConversationE2EEKeys(conversationId: string): Promise<ConversationE2EEKeyMaterial> {
    const response = await http.get(`/chat/conversations/${conversationId}/e2ee/keys/`);
    const payload = unwrapObject<Record<string, unknown>>(response.data, {});
    const participantsRaw = asRecord(payload.participants);
    const participants: Record<string, E2EEDeviceKey[]> = {};
    for (const [userId, keys] of Object.entries(participantsRaw)) {
      participants[String(userId)] = Array.isArray(keys) ? keys.map(normalizeE2EEDeviceKey).filter((item) => item.key_id) : [];
    }
    return {
      conversation_id: firstString(payload.conversation_id, conversationId),
      key_version: firstNumber(payload.key_version) ?? 1,
      rekey_required: firstBoolean(payload.rekey_required) ?? false,
      last_key_rotation_at: firstString(payload.last_key_rotation_at) || null,
      last_security_event_at: firstString(payload.last_security_event_at) || null,
      participants,
    };
  },
  async presencePing(deviceId = "web", presence?: { device_type?: "desktop" | "mobile" | "tablet"; presence_status?: "active" | "idle" }) {
    const response = await http.post("/chat/presence/ping/", { device_id: deviceId, ...presence });
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async presenceDisconnect(deviceId = "web", accessToken?: string | null) {
    const response = await http.post("/chat/presence/disconnect/", { device_id: deviceId }, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    });
    return unwrapObject<Record<string, unknown>>(response.data, {});
  },
  async listBlocks(signal?: AbortSignal) {
    const items = await collectChatPages("/chat/blocks/", normalizeUserBlock, {
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => item.id);
  },
  async blockUser(userId: string, reason?: string) {
    const response = await http.post("/chat/blocks/", { blocked_user_id: Number.isFinite(Number(userId)) ? Number(userId) : userId, reason: reason ?? "" });
    return normalizeUserBlock(unwrapData<unknown>(response.data));
  },
  async unblockUser(userId: string) {
    await http.delete(`/chat/blocks/${userId}/`);
  },
  async listDevices(signal?: AbortSignal) {
    const items = await collectChatPages("/chat/devices/", normalizeUserDevice, {
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => item.id);
  },
  async registerDevice(payload: { platform: "web" | "android" | "ios"; push_token: string }) {
    const response = await http.post("/chat/devices/", payload);
    return normalizeUserDevice(unwrapData<unknown>(response.data));
  },
  async deactivateDevice(pushToken: string, accessToken?: string | null) {
    const response = await http.post("/chat/devices/deactivate/", { push_token: pushToken }, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    });
    return normalizeUserDevice(unwrapData<unknown>(response.data));
  },
  async sync(params: { since?: string; conversation_id?: string; limit?: number } = {}) {
    const response = await http.get("/chat/sync/", { params });
    const payload = unwrapObject<Record<string, unknown>>(response.data, {});
    return {
      conversations: Array.isArray(payload.conversations) ? payload.conversations.map(normalizeConversation).filter((item) => item.id) : [],
      messages: Array.isArray(payload.messages) ? payload.messages.map(normalizeMessage).filter((item) => item.id) : [],
      active_calls: Array.isArray(payload.active_calls) ? payload.active_calls.map(normalizeCall).filter((item) => item.id) : [],
      has_more_conversations: firstBoolean(payload.has_more_conversations) ?? false,
      has_more_messages: firstBoolean(payload.has_more_messages) ?? false,
      next_since: firstString(payload.next_since) || undefined,
      server_time: firstString(payload.server_time) || undefined,
    };
  },
  async listModerationReports(signal?: AbortSignal) {
    return collectChatPages("/chat/moderation/reports/", (value) => value as ModerationReport, {
      signal,
      getKey: (item) => String(item.id || ""),
    });
  },
  async resolveModerationReport(reportId: string, payload: { notes?: string; hide_message?: boolean } = {}) {
    const response = await http.post(`/chat/moderation/reports/${reportId}/resolve/`, payload);
    return unwrapObject<ModerationAction>(response.data, {});
  },
  async dismissModerationReport(reportId: string, payload: { notes?: string } = {}) {
    const response = await http.post(`/chat/moderation/reports/${reportId}/dismiss/`, payload);
    return unwrapObject<ModerationAction>(response.data, {});
  },
  async restoreModeratedMessage(messageId: string, payload: { notes?: string } = {}) {
    const response = await http.post(`/chat/moderation/messages/${messageId}/restore/`, payload);
    return unwrapObject<ModerationAction>(response.data, {});
  },
  async listAuditLogs(signal?: AbortSignal) {
    const items = await collectChatPages("/chat/audit-logs/", normalizeAuditLog, {
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => item.id);
  },
};
