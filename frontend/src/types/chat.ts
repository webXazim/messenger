export type UserLite = {
  id: string;
  username: string;
  email?: string;
  display_name: string;
  avatar?: string | null;
  is_online?: boolean;
  active_devices?: number;
  last_seen_at?: string | null;
  presence_label?: string;
  presence_status?: "active" | "idle" | "offline";
  device_type?: "desktop" | "mobile" | "tablet" | null;
  device_types?: Array<"desktop" | "mobile" | "tablet">;
  presence_visibility?: "public" | "hidden";
};

export type UserStatusMedia = {
  upload_id: string;
  media_kind: "image" | "video";
  mime_type?: string;
  preview_url: string;
  thumbnail_url?: string | null;
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
};

export type UserStatus = {
  id: string;
  author: UserLite;
  content_type: "text" | "image" | "video";
  text: string;
  background_color: string;
  text_color: string;
  media?: UserStatusMedia | null;
  is_viewed: boolean;
  is_own: boolean;
  view_count: number;
  created_at: string;
  expires_at: string;
};

export type MessageAttachment = {
  id: string;
  original_name: string;
  mime_type: string;
  media_kind?: string | null;
  size: number;
  width?: number | null;
  height?: number | null;
  rotation?: number | null;
  duration_seconds?: number | null;
  thumbnail_url?: string | null;
  metadata?: Record<string, unknown> | null;
  file_url?: string;
  preview_url?: string;
  can_preview_inline?: boolean;
  signed_download?: { download_url?: string; url?: string } | null;
  signed_preview?: { url?: string; preview_url?: string } | null;
  is_encrypted?: boolean;
  encryption?: AttachmentEncryptionEnvelope | null;
  view_once?: boolean;
  view_once_opened?: boolean;
  can_open_view_once?: boolean;
};

export type Reaction = {
  id: string;
  emoji: string;
  user: UserLite;
  created_at: string;
};

export type DeliveryReceipt = {
  id: string;
  user: UserLite;
  delivered_at?: string;
};

export type MessageEntity = {
  type: "bold" | "italic" | "underline" | "strike" | "code" | "link" | "mention";
  offset: number;
  length: number;
  url?: string;
  user_id?: string;
  username?: string;
};

export type MessageEncryptedKey = {
  key_id: string;
  wrapped_key: string;
};

export type AttachmentEncryptionEnvelope = {
  version?: string;
  algorithm: string;
  nonce: string;
  sender_key_id: string;
  sender_device_id?: string;
  key_version?: number;
  recipient_key_ids?: string[];
  encrypted_keys?: MessageEncryptedKey[];
  metadata_ciphertext: string;
  metadata_nonce: string;
  original_sha256?: string;
  preview_ciphertext?: string;
  preview_nonce?: string;
  preview_mime_type?: string;
  aad?: Record<string, unknown>;
};

export type MessageEncryptionEnvelope = {
  version?: string;
  algorithm: string;
  ciphertext: string;
  nonce: string;
  sender_key_id: string;
  sender_device_id?: string;
  key_version?: number;
  recipient_key_ids?: string[];
  encrypted_keys?: MessageEncryptedKey[];
  aad?: Record<string, unknown>;
};

export type Message = {
  id: string;
  conversation_id?: string;
  type: string;
  text: string;
  sender: UserLite;
  created_at: string;
  updated_at?: string;
  sequence?: number;
  attachments: MessageAttachment[];
  delivery_status?: string;
  failed_reason?: string | null;
  retry_count?: number;
  is_deleted?: boolean;
  can_edit?: boolean;
  edit_locked_reason?: string;
  edit_deadline?: string;
  can_restore?: boolean;
  restore_locked_reason?: string;
  transcript?: { text?: string } | null;
  voice_note?: { is_voice_note: boolean; duration_seconds?: number | string | null; waveform?: number[] } | null;
  call_event?: {
    system_event?: string;
    call_id?: string;
    call_type?: string;
    call_status?: string;
    call_outcome?: string;
    summary_text?: string;
    duration_seconds?: number;
    ringing_duration_seconds?: number;
    reason?: string;
    initiated_by_id?: string;
    answered_by_id?: string;
    actor_id?: string;
  } | null;
  reply_preview?: { id?: string; text: string } | null;
  reactions?: Reaction[];
  deliveries?: DeliveryReceipt[];
  reaction_summary?: Record<string, number>;
  entities?: MessageEntity[];
  links?: string[];
  mentioned_user_ids?: string[];
  metadata?: Record<string, unknown>;
  client_temp_id?: string;
  is_encrypted?: boolean;
  encryption?: MessageEncryptionEnvelope | null;
  decryption_state?: "pending" | "ready" | "unavailable" | "error";
  decryption_message?: string;
};

export type E2EEDeviceKey = {
  id: string;
  device_id: string;
  key_id: string;
  label?: string;
  algorithm: string;
  fingerprint?: string;
  public_key_jwk: JsonWebKey;
  is_active?: boolean;
  revoked_at?: string | null;
  last_seen_at?: string;
  security_changed?: boolean;
};

export type ConversationE2EEKeyMaterial = {
  conversation_id: string;
  key_version: number;
  rekey_required: boolean;
  last_key_rotation_at?: string | null;
  last_security_event_at?: string | null;
  participants: Record<string, E2EEDeviceKey[]>;
};

export type Participant = {
  id: string;
  role: string;
  user: UserLite;
  is_muted?: boolean;
  is_archived?: boolean;
  is_pinned?: boolean;
  is_blocked?: boolean;
  left_at?: string | null;
  banned_at?: string | null;
  last_read_message?: string | null;
  last_read_at?: string | null;
  last_delivered_message?: string | null;
  last_delivered_at?: string | null;
};

export type ConversationInviteLink = {
  id: string;
  conversation: string;
  token: string;
  expires_at?: string | null;
  revoked_at?: string | null;
  max_uses?: number;
  use_count?: number;
  is_active?: boolean;
  join_url?: string;
  created_at?: string;
};

export type Conversation = {
  id: string;
  type: "direct" | "group";
  title: string;
  slug?: string | null;
  unread_count: number;
  e2ee_key_version?: number;
  e2ee_rekey_required?: boolean;
  e2ee_last_key_rotation_at?: string | null;
  e2ee_last_security_event_at?: string | null;
  participants: Participant[];
  last_message?: Message | null;
  last_message_at?: string | null;
};

export type CallParticipant = {
  id: string;
  state: string;
  network_quality?: string;
  preferred_video_quality?: string;
  audio_enabled?: boolean;
  video_enabled?: boolean;
  is_on_hold?: boolean;
  reconnecting?: boolean;
  connection_state?: string;
  audio_route?: string;
  screen_share_enabled?: boolean;
  hand_raised?: boolean;
  is_speaking?: boolean;
  speaking_level?: number;
  quality_score?: number;
  quality_alert?: string;
  user: UserLite;
};

export type Call = {
  id: string;
  conversation?: string;
  initiated_by?: UserLite;
  answered_by?: UserLite | null;
  call_type: "voice" | "video";
  status: string;
  started_at: string;
  answered_at?: string | null;
  ended_at?: string | null;
  ended_reason?: string | null;
  duration_seconds?: number | null;
  ringing_seconds?: number | null;
  ring_timeout_seconds?: number | null;
  call_state?: string;
  participant_summary?: Record<string, number>;
  participants?: CallParticipant[];
  network_recommendation?: Record<string, unknown>;
};

export type NotificationPreferences = {
  id?: string;
  push_enabled?: boolean;
  email_enabled?: boolean;
  message_preview_enabled?: boolean;
  mute_all?: boolean;
  call_quality_preference: "auto" | "low" | "mid" | "clear";
};

export type CallConfig = {
  available_quality_presets: string[];
  selected_quality_preset: string;
  applied_quality_profile: Record<string, unknown>;
  ice_servers?: RTCIceServer[];
  ice_transport_policy?: RTCIceTransportPolicy;
  ice_candidate_pool_size?: number;
  reconnect_grace_seconds?: number;
  quality_report_interval_seconds?: number;
  heartbeat_interval_seconds?: number;
  codec_preferences?: Record<string, unknown>;
  quality_reporting?: Record<string, unknown>;
  network_profiles?: Record<string, unknown>;
};

export type TurnCredentials = {
  configured: boolean;
  ttl_seconds: number;
  username?: string;
  credential?: string;
  credential_type?: string;
  ice_servers?: RTCIceServer[];
};
