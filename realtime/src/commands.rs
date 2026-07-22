use std::{collections::{HashMap, HashSet}, sync::Arc};

use anyhow::{Context, Result};
use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use sqlx::{Postgres, Transaction};
use uuid::Uuid;

use crate::{
    command_auth::CommandIdentity,
    command_delivery::deliver_committed,
    config::ChatCommandBackend,
    database::{CommittedEvent, Database, SendMessageResult},
    message_mutations::{
        extract_links, sanitize_encryption_envelope, sanitize_entities, sanitize_text,
        validate_encryption_coverage, MessageEntity,
    },
    protocol::{AudienceKey, AudienceKind},
    state::AppState,
};

const MAX_ATTACHMENTS: usize = 12;
const MAX_ENVELOPE_BYTES: usize = 300 * 1024;
const MAX_WAVEFORM_POINTS: usize = 512;

#[derive(Debug, Default, Deserialize)]
pub(crate) struct SendMessageRequest {
    #[serde(default)]
    pub(crate) text: String,
    #[serde(default = "default_message_type", rename = "type")]
    pub(crate) message_type: String,
    #[serde(default)]
    pub(crate) client_temp_id: String,
    #[serde(default)]
    pub(crate) reply_to_id: Option<Uuid>,
    #[serde(default)]
    pub(crate) attachment_ids: Vec<Uuid>,
    #[serde(default)]
    pub(crate) view_once_attachment_ids: Vec<Uuid>,
    #[serde(default)]
    pub(crate) is_encrypted: bool,
    #[serde(default)]
    pub(crate) encryption: Option<Value>,
    #[serde(default)]
    pub(crate) attachment_encryption: Vec<Value>,
    #[serde(default)]
    pub(crate) entities: Vec<MessageEntity>,
    #[serde(default)]
    pub(crate) is_voice_note: bool,
    #[serde(default)]
    pub(crate) duration_seconds: Option<f64>,
    #[serde(default)]
    pub(crate) waveform: Vec<i64>,
    #[serde(default)]
    pub(crate) transcript_text: Option<String>,
    #[serde(default)]
    pub(crate) transcript_language_code: Option<String>,
    #[serde(default)]
    pub(crate) transcript_confidence: Option<f64>,
}

fn default_message_type() -> String { "text".to_owned() }

fn validation_error(code: &str, detail: &str) -> anyhow::Error {
    anyhow::anyhow!("validation|{code}|{detail}")
}

pub async fn send_message(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<SendMessageRequest>,
) -> impl IntoResponse {
    if state.config.chat_command_backend != ChatCommandBackend::Axum {
        return error(StatusCode::NOT_FOUND, "axum_chat_commands_disabled", "Axum chat commands are not active.");
    }
    let identity = match state.command_auth.authenticate(&headers) {
        Ok(value) => value,
        Err(_) => return error(StatusCode::UNAUTHORIZED, "authentication_failed", "Authentication credentials were not provided or are invalid."),
    };
    match state.database.send_chat_message(conversation_id, &identity, input).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            let status = if result.was_deduplicated { StatusCode::OK } else { StatusCode::CREATED };
            (status, Json(result.payload)).into_response()
        }
        Err(error) => message_error(error, conversation_id, identity.claimed_user_id),
    }
}

fn message_error(error: anyhow::Error, conversation_id: Uuid, claimed_actor_id: Option<i64>) -> axum::response::Response {
    let message = error.to_string();
    tracing::warn!(%conversation_id, ?claimed_actor_id, error = %error, "Axum message command rejected");
    if let Some(rest) = message.strip_prefix("validation|") {
        let mut parts = rest.splitn(2, '|');
        let code = parts.next().unwrap_or("invalid_message");
        let status = if code == "message_rate_limited" { StatusCode::TOO_MANY_REQUESTS } else { StatusCode::BAD_REQUEST };
        return error_response(
            status,
            code,
            parts.next().unwrap_or("The message is invalid."),
        );
    }
    if message.contains("authenticated user does not exist") {
        error_response(StatusCode::UNAUTHORIZED, "authentication_failed", "The authenticated account is not available.")
    } else if message.contains("not an active participant") || message.contains("conversation is unavailable") {
        error_response(StatusCode::NOT_FOUND, "conversation_not_found", "Conversation was not found.")
    } else if message.contains("muted") || message.contains("blocked") {
        error_response(StatusCode::FORBIDDEN, "sending_not_allowed", "You cannot send messages in this conversation.")
    } else {
        tracing::error!(%conversation_id, error = %error, "Axum SQLx message send failed");
        error_response(StatusCode::INTERNAL_SERVER_ERROR, "message_send_failed", "The message could not be sent.")
    }
}

fn truncate_string(value: &str, max_chars: usize) -> String {
    value.trim().chars().take(max_chars).collect()
}

fn string_value(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        Some(Value::Bool(value)) => value.to_string(),
        _ => String::new(),
    }
}

fn sanitize_attachment_encryption_payloads(
    values: Vec<Value>,
    current_key_version: i64,
) -> Result<HashMap<Uuid, Value>> {
    let mut result = HashMap::new();
    for value in values {
        let item = value.as_object().ok_or_else(|| validation_error(
            "invalid_attachment_encryption",
            "Each attachment encryption payload must be an object.",
        ))?;
        let upload_raw = item.get("upload_id").and_then(Value::as_str).unwrap_or("").trim();
        let upload_id = Uuid::parse_str(upload_raw).map_err(|_| validation_error(
            "invalid_attachment_encryption",
            "Each attachment encryption payload must include a valid upload id.",
        ))?;
        if result.contains_key(&upload_id) {
            return Err(validation_error("invalid_attachment_encryption", "Attachment encryption payloads cannot be duplicated."));
        }

        let mut recipient_key_ids = item
            .get("recipient_key_ids")
            .and_then(Value::as_array)
            .map(|values| values.iter().map(|entry| truncate_string(&string_value(Some(entry)), 256)).filter(|entry| !entry.is_empty()).collect::<Vec<_>>())
            .unwrap_or_default();
        let mut encrypted_keys = Vec::new();
        if let Some(values) = item.get("encrypted_keys").and_then(Value::as_array) {
            for entry in values {
                let Some(entry) = entry.as_object() else { continue; };
                let key_id = truncate_string(entry.get("key_id").and_then(Value::as_str).unwrap_or(""), 256);
                let wrapped_key = entry.get("wrapped_key").and_then(Value::as_str).unwrap_or("");
                if !key_id.is_empty() && !wrapped_key.is_empty() {
                    encrypted_keys.push(json!({"key_id": key_id, "wrapped_key": wrapped_key}));
                }
            }
        }
        if recipient_key_ids.is_empty() && !encrypted_keys.is_empty() {
            recipient_key_ids = encrypted_keys.iter().filter_map(|entry| entry.get("key_id").and_then(Value::as_str).map(ToOwned::to_owned)).collect();
        }

        let algorithm = truncate_string(item.get("algorithm").and_then(Value::as_str).unwrap_or(""), 80);
        let nonce = truncate_string(item.get("nonce").and_then(Value::as_str).unwrap_or(""), 256);
        let sender_key_id = truncate_string(item.get("sender_key_id").and_then(Value::as_str).unwrap_or(""), 256);
        let sender_device_id = truncate_string(item.get("sender_device_id").and_then(Value::as_str).unwrap_or(""), 256);
        let metadata_ciphertext = item.get("metadata_ciphertext").and_then(Value::as_str).unwrap_or("");
        let metadata_nonce = truncate_string(item.get("metadata_nonce").and_then(Value::as_str).unwrap_or(""), 256);
        let preview_ciphertext = item.get("preview_ciphertext").and_then(Value::as_str).unwrap_or("");
        let preview_nonce = truncate_string(item.get("preview_nonce").and_then(Value::as_str).unwrap_or(""), 256);
        let preview_mime_type = truncate_string(item.get("preview_mime_type").and_then(Value::as_str).unwrap_or(""), 120);
        if algorithm.is_empty() || nonce.is_empty() || sender_key_id.is_empty() || metadata_ciphertext.is_empty() || metadata_nonce.is_empty() {
            return Err(validation_error("invalid_attachment_encryption", "Attachment encryption payload is incomplete."));
        }
        if recipient_key_ids.is_empty() || encrypted_keys.is_empty() {
            return Err(validation_error("invalid_attachment_encryption", "Attachment encryption payload must include wrapped recipient keys."));
        }
        if preview_ciphertext.is_empty() != preview_nonce.is_empty() {
            return Err(validation_error("invalid_attachment_encryption", "Encrypted attachment previews require both ciphertext and nonce."));
        }
        if !preview_ciphertext.is_empty() && !preview_mime_type.to_ascii_lowercase().starts_with("image/") {
            return Err(validation_error("invalid_attachment_encryption", "Encrypted attachment previews must use an image MIME type."));
        }
        let key_version = item.get("key_version").and_then(Value::as_i64).unwrap_or(current_key_version).max(1);
        if key_version != current_key_version {
            return Err(validation_error("e2ee_stale_key_version", "Encrypted attachment uses an outdated secure-device list. Refresh and try again."));
        }
        let mut payload = Map::new();
        payload.insert("version".to_owned(), Value::String(truncate_string(item.get("version").and_then(Value::as_str).unwrap_or("v1"), 32)));
        payload.insert("algorithm".to_owned(), Value::String(algorithm));
        payload.insert("nonce".to_owned(), Value::String(nonce));
        payload.insert("sender_key_id".to_owned(), Value::String(sender_key_id));
        payload.insert("sender_device_id".to_owned(), Value::String(sender_device_id));
        payload.insert("recipient_key_ids".to_owned(), json!(recipient_key_ids));
        payload.insert("encrypted_keys".to_owned(), Value::Array(encrypted_keys));
        payload.insert("metadata_ciphertext".to_owned(), Value::String(metadata_ciphertext.to_owned()));
        payload.insert("metadata_nonce".to_owned(), Value::String(metadata_nonce));
        payload.insert("original_sha256".to_owned(), Value::String(truncate_string(item.get("original_sha256").and_then(Value::as_str).unwrap_or(""), 128)));
        payload.insert("preview_ciphertext".to_owned(), Value::String(preview_ciphertext.to_owned()));
        payload.insert("preview_nonce".to_owned(), Value::String(preview_nonce));
        payload.insert("preview_mime_type".to_owned(), Value::String(preview_mime_type));
        payload.insert("key_version".to_owned(), json!(key_version));
        if let Some(aad) = item.get("aad") { payload.insert("aad".to_owned(), aad.clone()); }
        let payload = Value::Object(payload);
        if serde_json::to_vec(&payload)?.len() > MAX_ENVELOPE_BYTES {
            return Err(validation_error("invalid_attachment_encryption", "Attachment encryption payload is too large."));
        }
        result.insert(upload_id, payload);
    }
    Ok(result)
}

async fn resolve_actor_id(tx: &mut Transaction<'_, Postgres>, identity: &CommandIdentity) -> Result<i64> {
    if let Some(user_id) = identity.claimed_user_id {
        if let Some(id) = sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE id=$1 AND is_active=TRUE")
            .bind(user_id).persistent(false).fetch_optional(&mut **tx).await? { return Ok(id); }
    }
    if !identity.email.is_empty() {
        if let Some(id) = sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE LOWER(email)=$1 AND is_active=TRUE LIMIT 1")
            .bind(&identity.email).persistent(false).fetch_optional(&mut **tx).await? { return Ok(id); }
    }
    anyhow::bail!("authenticated user does not exist locally")
}

pub(crate) async fn conversation_event_audiences(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
) -> Result<Vec<AudienceKey>> {
    let participant_ids = sqlx::query_scalar::<_, i64>(
        "SELECT user_id FROM chat_conversationparticipant WHERE conversation_id = $1 AND left_at IS NULL AND banned_at IS NULL ORDER BY user_id",
    )
    .bind(conversation_id)
    .persistent(false)
    .fetch_all(&mut **tx)
    .await?;
    let mut audiences = Vec::with_capacity(participant_ids.len() + 1);
    audiences.push(AudienceKey {
        kind: AudienceKind::Conversation,
        identifier: conversation_id.to_string(),
    });
    audiences.extend(participant_ids.into_iter().map(|user_id| AudienceKey {
        kind: AudienceKind::User,
        identifier: user_id.to_string(),
    }));
    Ok(audiences)
}

async fn insert_event(
    tx: &mut Transaction<'_, Postgres>,
    event_name: &str,
    data: Value,
    conversation_id: Uuid,
) -> Result<CommittedEvent> {
    let event_id = Uuid::new_v4();
    let payload = json!({
        "type": "chat.event",
        "version": 1,
        "event": event_name,
        "event_id": event_id.to_string(),
        "occurred_at": data.get("updated_at").or_else(|| data.get("created_at")).cloned().unwrap_or(Value::Null),
        "data": data,
    });
    // Conversation subscriptions cover the open chat. User audiences also
    // reach every participant while the chat is closed, which is required for
    // background notifications, unread state, delivery acks, and multi-device
    // synchronization.
    let audiences = conversation_event_audiences(tx, conversation_id).await?;
    let audiences_json = json!(&audiences);
    sqlx::query(
        "INSERT INTO common_realtimeoutboxevent (id,created_at,updated_at,event_id,event_name,payload,audiences,status,attempts,available_at,published_at,delivery_target,published_transport,stream_entry_id,last_error) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,'pending',0,NOW(),NULL,'nats_jetstream','','','')",
    )
    .bind(Uuid::new_v4()).bind(event_id).bind(event_name).bind(&payload).bind(audiences_json)
    .persistent(false).execute(&mut **tx).await?;
    Ok(CommittedEvent { event_id, event_name: event_name.to_owned(), payload, audiences })
}

impl Database {
    pub(crate) async fn send_chat_message(
        &self,
        conversation_id: Uuid,
        identity: &CommandIdentity,
        input: SendMessageRequest,
    ) -> Result<SendMessageResult> {
        let pool = self.pool.as_ref().context("SQLx message backend is disabled")?;
        if input.attachment_ids.len() > MAX_ATTACHMENTS {
            return Err(validation_error("too_many_attachments", "A message can contain at most 12 attachments."));
        }
        let attachment_ids = input.attachment_ids.iter().copied().collect::<HashSet<_>>();
        if attachment_ids.len() != input.attachment_ids.len() {
            return Err(validation_error("duplicate_attachments", "The same upload cannot be attached more than once."));
        }
        let view_once_ids = input.view_once_attachment_ids.iter().copied().collect::<HashSet<_>>();
        if !view_once_ids.is_subset(&attachment_ids) {
            return Err(validation_error("invalid_view_once", "View-once uploads must belong to this message."));
        }

        let requested_type = if input.is_voice_note { "audio".to_owned() } else { input.message_type.trim().to_ascii_lowercase() };
        if !matches!(requested_type.as_str(), "text" | "image" | "video" | "audio" | "file") {
            return Err(validation_error("invalid_message_type", "Message type is not supported."));
        }
        let encrypted = input.is_encrypted || input.encryption.is_some();
        let text = sanitize_text(&input.text);
        if encrypted && !text.is_empty() {
            return Err(validation_error("encrypted_plaintext", "Encrypted messages must not include plaintext text."));
        }
        if encrypted && !input.entities.is_empty() {
            return Err(validation_error("encrypted_entities", "Encrypted messages cannot include plaintext formatting entities."));
        }
        if encrypted && (input.transcript_text.is_some() || input.transcript_language_code.is_some() || input.transcript_confidence.is_some()) {
            return Err(validation_error("encrypted_transcript", "Encrypted messages cannot include plaintext transcripts."));
        }
        if text.is_empty() && input.attachment_ids.is_empty() && !encrypted {
            return Err(validation_error("empty_message", "Message text or at least one attachment is required."));
        }
        if !input.attachment_encryption.is_empty() && input.attachment_ids.is_empty() {
            return Err(validation_error("invalid_attachment_encryption", "Encrypted attachment metadata requires attachment uploads."));
        }
        if input.entities.len() > 100 {
            return Err(validation_error("invalid_entities", "A message contains too many formatting entities."));
        }
        let link_count = text.match_indices("http://").count() + text.match_indices("https://").count();
        if link_count > 10 {
            return Err(validation_error("too_many_links", "A message can contain at most 10 links."));
        }

        let client_temp_id: String = input.client_temp_id.trim().chars().take(100).collect();
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let participant = sqlx::query_as::<_, (bool, bool, String, i64, bool)>(
            "SELECT cp.is_blocked,(cp.moderation_muted_until IS NOT NULL AND cp.moderation_muted_until>NOW()),c.type,COALESCE(c.e2ee_key_version,1)::bigint,c.e2ee_rekey_required FROM chat_conversationparticipant cp JOIN chat_conversation c ON c.id=cp.conversation_id WHERE cp.conversation_id=$1 AND cp.user_id=$2 AND cp.left_at IS NULL AND cp.banned_at IS NULL AND c.is_active=TRUE FOR UPDATE OF cp,c",
        )
        .bind(conversation_id).bind(actor_id).persistent(false).fetch_optional(&mut *tx).await?;
        let Some((participant_blocked, participant_muted, conversation_type, key_version, rekey_required)) = participant else {
            anyhow::bail!("actor is not an active participant");
        };
        if participant_blocked { anyhow::bail!("participant is blocked"); }
        if participant_muted { anyhow::bail!("participant is muted"); }
        if conversation_type == "direct" {
            let blocked = sqlx::query_scalar::<_, bool>(
                "SELECT EXISTS(SELECT 1 FROM chat_userblock b JOIN chat_conversationparticipant peer ON peer.conversation_id=$1 AND peer.user_id<>$2 AND peer.left_at IS NULL AND peer.banned_at IS NULL WHERE (b.blocker_id=$2 AND b.blocked_id=peer.user_id) OR (b.blocker_id=peer.user_id AND b.blocked_id=$2))",
            ).bind(conversation_id).bind(actor_id).persistent(false).fetch_one(&mut *tx).await?;
            if blocked { anyhow::bail!("direct conversation is blocked"); }
        }

        if !client_temp_id.is_empty() {
            if let Some(existing_id) = sqlx::query_scalar::<_, Uuid>(
                "SELECT id FROM chat_message WHERE conversation_id=$1 AND sender_id=$2 AND client_temp_id=$3 LIMIT 1",
            ).bind(conversation_id).bind(actor_id).bind(&client_temp_id).persistent(false).fetch_optional(&mut *tx).await? {
                let mut payload = self.get_chat_message_in_transaction(&mut tx, actor_id, existing_id).await?.context("existing message is unavailable")?;
                if let Some(object) = payload.as_object_mut() { object.insert("was_deduplicated".to_owned(), Value::Bool(true)); }
                tx.commit().await?;
                return Ok(SendMessageResult { payload, was_deduplicated: true, events: Vec::new() });
            }
        }

        let recent_count = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*) FROM chat_message WHERE sender_id=$1 AND conversation_id=$2 AND created_at>NOW()-INTERVAL '10 seconds'",
        ).bind(actor_id).bind(conversation_id).persistent(false).fetch_one(&mut *tx).await?;
        if recent_count >= 40 {
            return Err(validation_error("message_rate_limited", "Too many messages were sent. Try again shortly."));
        }

        let reply_to_id = if let Some(reply_id) = input.reply_to_id {
            sqlx::query_scalar::<_, Uuid>(
                "SELECT id FROM chat_message WHERE id=$1 AND conversation_id=$2 AND NOT is_deleted FOR UPDATE",
            ).bind(reply_id).bind(conversation_id).persistent(false).fetch_optional(&mut *tx).await?
                .ok_or_else(|| validation_error("reply_not_found", "Reply target was not found in this conversation."))?;
            Some(reply_id)
        } else { None };

        let upload_rows = if input.attachment_ids.is_empty() {
            Vec::new()
        } else {
            sqlx::query_as::<_, (Uuid, String, String)>(
                "SELECT id,COALESCE(media_kind,'file'),COALESCE(mime_type,'') FROM chat_pendingupload WHERE id=ANY($1) AND user_id=$2 AND purpose='messenger' AND status='pending' AND scan_status='clean' AND expires_at>NOW() FOR UPDATE",
            ).bind(&input.attachment_ids).bind(actor_id).persistent(false).fetch_all(&mut *tx).await?
        };
        if upload_rows.len() != input.attachment_ids.len() {
            return Err(validation_error("upload_unavailable", "One or more uploads are not clean, have expired, or are already attached."));
        }
        for (upload_id, media_kind, mime_type) in &upload_rows {
            if view_once_ids.contains(upload_id) && !matches!(media_kind.as_str(), "image" | "video") {
                return Err(validation_error("invalid_view_once", "Only images and videos can be sent as view once."));
            }
            if requested_type == "audio" && media_kind != "audio" && !mime_type.to_ascii_lowercase().starts_with("audio/") {
                return Err(validation_error("invalid_voice_attachment", "Voice note attachments must be audio files."));
            }
        }

        let message_encryption = if encrypted {
            let envelope = input.encryption.ok_or_else(|| validation_error("invalid_encryption", "Encryption envelope is required when is_encrypted is true."))?;
            let envelope = sanitize_encryption_envelope(envelope, key_version)?;
            validate_encryption_coverage(&mut tx, conversation_id, actor_id, &envelope).await?;
            Some(envelope)
        } else { None };
        let attachment_encryption = sanitize_attachment_encryption_payloads(input.attachment_encryption, key_version)?;
        if attachment_encryption.keys().any(|upload_id| !attachment_ids.contains(upload_id)) {
            return Err(validation_error(
                "invalid_attachment_encryption",
                "Attachment encryption metadata must belong to an upload attached to this message.",
            ));
        }
        let requires_attachment_encryption = message_encryption.is_some() || !attachment_encryption.is_empty();
        if requires_attachment_encryption && input.attachment_ids.iter().any(|id| !attachment_encryption.contains_key(id)) {
            return Err(validation_error("attachment_encryption_missing", "Encrypted messages with attachments must include encryption metadata for every attachment."));
        }
        for envelope in attachment_encryption.values() {
            validate_encryption_coverage(&mut tx, conversation_id, actor_id, envelope).await?;
        }

        let (entities, mentioned_user_ids) = if encrypted { (Vec::new(), Vec::new()) } else { sanitize_entities(&mut tx, conversation_id, &input.entities).await? };
        let links = if encrypted { Vec::new() } else { extract_links(&text) };
        let waveform = input.waveform.into_iter().take(MAX_WAVEFORM_POINTS).map(|value| value.clamp(0, 100)).collect::<Vec<_>>();
        let duration_seconds = input.duration_seconds.filter(|value| value.is_finite() && *value >= 0.0).map(|value| value.min(86_400.0));
        let transcript_text = input.transcript_text.unwrap_or_default().trim().chars().take(20_000).collect::<String>();
        let transcript_language = input.transcript_language_code.unwrap_or_default().trim().chars().take(16).collect::<String>();
        let transcript_confidence = input.transcript_confidence.filter(|value| value.is_finite()).map(|value| value.clamp(0.0, 100.0));
        let transcript_requested = !transcript_text.is_empty() || !transcript_language.is_empty() || transcript_confidence.is_some();

        let mut metadata = Map::new();
        metadata.insert("raw_text".to_owned(), Value::String(text.clone()));
        metadata.insert("entities".to_owned(), Value::Array(entities));
        metadata.insert("mentioned_user_ids".to_owned(), json!(mentioned_user_ids));
        metadata.insert("links".to_owned(), Value::Array(links));
        if input.is_voice_note || requested_type == "audio" {
            metadata.insert("voice_note".to_owned(), Value::Bool(true));
            metadata.insert("duration_seconds".to_owned(), duration_seconds.map_or(Value::Null, |value| json!(value)));
            metadata.insert("waveform".to_owned(), json!(waveform));
        }
        if let Some(envelope) = message_encryption.as_ref() {
            metadata.insert("encrypted".to_owned(), Value::Bool(true));
            metadata.insert("encryption".to_owned(), envelope.clone());
        }
        if transcript_requested {
            metadata.insert("transcript_requested".to_owned(), Value::Bool(true));
            if !transcript_language.is_empty() { metadata.insert("transcript_language_code".to_owned(), Value::String(transcript_language.clone())); }
        }
        let metadata = Value::Object(metadata);

        let sequence = sqlx::query_scalar::<_, i64>(
            "UPDATE chat_conversation SET next_message_sequence=next_message_sequence+1,updated_at=NOW() WHERE id=$1 AND is_active=TRUE RETURNING next_message_sequence::bigint",
        ).bind(conversation_id).persistent(false).fetch_optional(&mut *tx).await?.context("conversation is unavailable")?;
        let inferred_type = if requested_type == "text" && text.is_empty() && !upload_rows.is_empty() {
            if upload_rows.len() == 1 { match upload_rows[0].1.as_str() { "image" => "image", "video" => "video", "audio" => "audio", _ => "file" } } else { "file" }
        } else { requested_type.as_str() };
        let message_id = Uuid::new_v4();
        sqlx::query(
            "INSERT INTO chat_message (id,created_at,updated_at,conversation_id,sender_id,type,text,metadata,reply_to_id,forwarded_from_id,is_edited,edited_at,edit_locked_at,edit_locked_reason,is_deleted,deleted_at,deleted_text_backup,deletion_source,client_temp_id,sequence,delivery_status,failed_reason,retry_count) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,$6,$7,NULL,FALSE,NULL,NULL,'',FALSE,NULL,'','',$8,$9,'sent','',0)",
        ).bind(message_id).bind(conversation_id).bind(actor_id).bind(inferred_type).bind(&text).bind(&metadata).bind(reply_to_id).bind(&client_temp_id).bind(sequence)
        .persistent(false).execute(&mut *tx).await?;

        for upload_id in &input.attachment_ids {
            let encryption_metadata = attachment_encryption.get(upload_id)
                .map(|envelope| json!({"source_pending_upload_id":upload_id.to_string(),"encrypted_attachment":true,"encryption":envelope}))
                .unwrap_or_else(|| json!({"source_pending_upload_id":upload_id.to_string()}));
            sqlx::query(
                "INSERT INTO chat_messageattachment (id,created_at,updated_at,message_id,file,original_name,media_kind,mime_type,size,width,height,rotation,duration_seconds,thumbnail,scan_status,scan_notes,scanned_at,metadata,view_once) SELECT $1,NOW(),NOW(),$2,file,original_name,COALESCE(media_kind,'file'),mime_type,size,width,height,rotation,duration_seconds,thumbnail,'clean',scan_notes,scanned_at,COALESCE(metadata,'{}'::jsonb)||$4::jsonb,$5 FROM chat_pendingupload WHERE id=$3",
            ).bind(Uuid::new_v4()).bind(message_id).bind(upload_id).bind(encryption_metadata).bind(view_once_ids.contains(upload_id))
            .persistent(false).execute(&mut *tx).await?;
        }
        if !input.attachment_ids.is_empty() {
            sqlx::query("UPDATE chat_pendingupload SET status='attached',updated_at=NOW() WHERE id=ANY($1)")
                .bind(&input.attachment_ids).persistent(false).execute(&mut *tx).await?;
        }
        if transcript_requested {
            sqlx::query(
                "INSERT INTO chat_messagetranscript (id,created_at,updated_at,message_id,status,language_code,text,confidence,source) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,CASE WHEN $6::double precision IS NULL THEN NULL ELSE ROUND(($6::double precision)::numeric,2) END,'manual') ON CONFLICT (message_id) DO UPDATE SET status=EXCLUDED.status,language_code=EXCLUDED.language_code,text=EXCLUDED.text,confidence=EXCLUDED.confidence,source='manual',updated_at=NOW()",
            ).bind(Uuid::new_v4()).bind(message_id).bind(if transcript_text.is_empty() { "pending" } else { "completed" }).bind(&transcript_language).bind(&transcript_text).bind(transcript_confidence)
            .persistent(false).execute(&mut *tx).await?;
        }

        let reply_updated = if let Some(reply_id) = reply_to_id {
            sqlx::query("UPDATE chat_message SET edit_locked_at=COALESCE(edit_locked_at,NOW()),edit_locked_reason=CASE WHEN edit_locked_at IS NULL THEN 'message_has_replies' ELSE edit_locked_reason END,updated_at=NOW() WHERE id=$1")
                .bind(reply_id).persistent(false).execute(&mut *tx).await?.rows_affected() > 0
        } else { false };
        sqlx::query("UPDATE chat_conversation SET last_message_id=$2,last_message_at=NOW(),e2ee_rekey_required=CASE WHEN $3 THEN FALSE ELSE e2ee_rekey_required END,updated_at=NOW() WHERE id=$1")
            .bind(conversation_id).bind(message_id).bind(rekey_required && requires_attachment_encryption)
            .persistent(false).execute(&mut *tx).await?;
        sqlx::query(
            "INSERT INTO chat_chatauditlog (id,created_at,updated_at,actor_id,conversation_id,message_id,event_type,metadata) VALUES ($1,NOW(),NOW(),$2,$3,$4,'message_sent',$5)",
        ).bind(Uuid::new_v4()).bind(actor_id).bind(conversation_id).bind(message_id).bind(json!({"attachment_count":input.attachment_ids.len(),"encrypted":encrypted,"source":"axum_sqlx","django_hot_path":false}))
        .persistent(false).execute(&mut *tx).await?;
        sqlx::query(
            "INSERT INTO chat_chatdataplanejob (id,created_at,updated_at,kind,dedupe_key,conversation_id,message_id,payload,status,attempts,available_at,locked_at,last_error) VALUES ($1,NOW(),NOW(),'message_created',$2,$3,$4,$5,'pending',0,NOW(),NULL,'') ON CONFLICT (dedupe_key) DO NOTHING",
        ).bind(Uuid::new_v4()).bind(format!("message_created:{message_id}")).bind(conversation_id).bind(message_id).bind(json!({"source":"axum_sqlx","notification_fanout":true,"usage_action":"send_message"}))
        .persistent(false).execute(&mut *tx).await?;

        let mut payload = self.get_chat_message_in_transaction(&mut tx, actor_id, message_id).await?.context("new message is unavailable")?;
        if let Some(object) = payload.as_object_mut() { object.insert("was_deduplicated".to_owned(), Value::Bool(false)); }
        let mut events = vec![insert_event(&mut tx, "message.created", payload.clone(), conversation_id).await?];
        if reply_updated {
            if let Some(reply_id) = reply_to_id {
                if let Some(reply_payload) = self.get_chat_message_in_transaction(&mut tx, actor_id, reply_id).await? {
                    events.push(insert_event(&mut tx, "message.updated", reply_payload, conversation_id).await?);
                }
            }
        }
        tx.commit().await?;
        Ok(SendMessageResult { payload, was_deduplicated: false, events })
    }
}

fn error(status: StatusCode, code: &str, detail: &str) -> axum::response::Response {
    error_response(status, code, detail)
}

pub(crate) fn error_response(status: StatusCode, code: &str, detail: &str) -> axum::response::Response {
    (status, Json(json!({"code": code, "detail": detail}))).into_response()
}
