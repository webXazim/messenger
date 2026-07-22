use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
};

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
    command_auth::{CommandAuthError, CommandIdentity},
    command_delivery::deliver_committed,
    commands::error_response,
    config::ChatMessageMutationBackend,
    database::{CommittedEvent, Database},
    protocol::{AudienceKey, AudienceKind},
    state::AppState,
};

const MAX_TEXT_CHARS: usize = 20_000;
const MAX_CIPHERTEXT_BYTES: usize = 256 * 1024;
const MAX_ENVELOPE_BYTES: usize = 300 * 1024;

#[derive(Debug, Default, Deserialize)]
pub(crate) struct MessageEditRequest {
    #[serde(default)]
    text: Option<String>,
    #[serde(default)]
    entities: Vec<MessageEntity>,
    #[serde(default)]
    is_encrypted: bool,
    #[serde(default)]
    encryption: Option<Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct MessageEntity {
    #[serde(rename = "type")]
    pub(crate) entity_type: String,
    pub(crate) offset: i64,
    pub(crate) length: i64,
    #[serde(default)]
    pub(crate) url: String,
    #[serde(default)]
    pub(crate) user_id: Option<Value>,
    #[serde(default)]
    pub(crate) username: String,
}

pub(crate) struct MutationResult {
    pub payload: Value,
    pub events: Vec<CommittedEvent>,
}

struct LockedMessage {
    conversation_id: Uuid,
    sender_id: Option<i64>,
    text: String,
    metadata: Value,
    is_deleted: bool,
    delivery_status: String,
    retry_count: i32,
    can_edit: bool,
    edit_reason: String,
    e2ee_key_version: i64,
    e2ee_rekey_required: bool,
    participant_blocked: bool,
    participant_muted: bool,
    deletion_source: String,
}

fn mutations_enabled(state: &AppState) -> bool {
    matches!(
        state.config.chat_message_mutation_backend,
        ChatMessageMutationBackend::SqlxShadow | ChatMessageMutationBackend::Axum
    )
}

fn authenticate(
    state: &AppState,
    headers: &HeaderMap,
) -> Result<CommandIdentity, axum::response::Response> {
    state.command_auth.authenticate(headers).map_err(|error| {
        let detail = match error {
            CommandAuthError::Missing => "Authentication credentials were not provided.",
            _ => "Authentication credentials are invalid or expired.",
        };
        error_response(StatusCode::UNAUTHORIZED, "authentication_failed", detail)
    })
}

fn validation_error(code: &str, detail: &str) -> anyhow::Error {
    anyhow::anyhow!("validation|{code}|{detail}")
}

fn mutation_error(error: anyhow::Error, operation: &'static str) -> axum::response::Response {
    let message = error.to_string();
    tracing::warn!(error = %error, operation, "Axum message mutation rejected");
    if let Some(rest) = message.strip_prefix("validation|") {
        let mut parts = rest.splitn(2, '|');
        let code = parts.next().unwrap_or("invalid_message_mutation");
        let detail = parts.next().unwrap_or("The message could not be changed.");
        return error_response(StatusCode::BAD_REQUEST, code, detail);
    }
    if message.contains("authenticated user does not exist") {
        error_response(
            StatusCode::UNAUTHORIZED,
            "authentication_failed",
            "The authenticated account is not available.",
        )
    } else if message.contains("participant is blocked") || message.contains("direct conversation is blocked") {
        error_response(
            StatusCode::FORBIDDEN,
            "participant_blocked",
            "Your participation in this conversation is restricted.",
        )
    } else if message.contains("participant is muted") {
        error_response(
            StatusCode::FORBIDDEN,
            "participant_muted",
            "You are temporarily muted in this conversation.",
        )
    } else if message.contains("not an active participant") || message.contains("message was not found") {
        error_response(
            StatusCode::NOT_FOUND,
            "message_not_found",
            "Message was not found.",
        )
    } else if message.contains("only your own") || message.contains("cannot be restored by its sender") {
        error_response(StatusCode::FORBIDDEN, "message_mutation_forbidden", &message)
    } else {
        tracing::error!(error = %error, operation, "Axum SQLx message mutation failed");
        error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "message_mutation_failed",
            "The message could not be changed.",
        )
    }
}

pub(crate) async fn edit_message(
    State(state): State<Arc<AppState>>,
    Path(message_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<MessageEditRequest>,
) -> impl IntoResponse {
    if !mutations_enabled(&state) {
        return error_response(
            StatusCode::NOT_FOUND,
            "axum_message_mutations_disabled",
            "Axum message mutations are not active.",
        );
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    match state.database.edit_chat_message(message_id, &identity, input).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::OK, Json(result.payload)).into_response()
        }
        Err(error) => mutation_error(error, "edit_message"),
    }
}

pub(crate) async fn delete_message(
    State(state): State<Arc<AppState>>,
    Path(message_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !mutations_enabled(&state) {
        return error_response(
            StatusCode::NOT_FOUND,
            "axum_message_mutations_disabled",
            "Axum message mutations are not active.",
        );
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    match state.database.delete_chat_message(message_id, &identity).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::OK, Json(result.payload)).into_response()
        }
        Err(error) => mutation_error(error, "delete_message"),
    }
}

pub(crate) async fn restore_message(
    State(state): State<Arc<AppState>>,
    Path(message_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !mutations_enabled(&state) {
        return error_response(
            StatusCode::NOT_FOUND,
            "axum_message_mutations_disabled",
            "Axum message mutations are not active.",
        );
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    match state.database.restore_chat_message(message_id, &identity).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::OK, Json(result.payload)).into_response()
        }
        Err(error) => mutation_error(error, "restore_message"),
    }
}

pub(crate) async fn retry_message(
    State(state): State<Arc<AppState>>,
    Path(message_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !mutations_enabled(&state) {
        return error_response(
            StatusCode::NOT_FOUND,
            "axum_message_mutations_disabled",
            "Axum message mutations are not active.",
        );
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    match state.database.retry_chat_message(message_id, &identity).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::OK, Json(result.payload)).into_response()
        }
        Err(error) => mutation_error(error, "retry_message"),
    }
}

async fn resolve_actor_id(
    tx: &mut Transaction<'_, Postgres>,
    identity: &CommandIdentity,
) -> Result<i64> {
    if let Some(user_id) = identity.claimed_user_id {
        if let Some(id) = sqlx::query_scalar::<_, i64>(
            "SELECT id FROM accounts_user WHERE id = $1 AND is_active = TRUE",
        )
        .bind(user_id)
        .persistent(false)
        .fetch_optional(&mut **tx)
        .await?
        {
            return Ok(id);
        }
    }
    if !identity.email.is_empty() {
        if let Some(id) = sqlx::query_scalar::<_, i64>(
            "SELECT id FROM accounts_user WHERE LOWER(email) = LOWER($1) AND is_active = TRUE LIMIT 1",
        )
        .bind(&identity.email)
        .persistent(false)
        .fetch_optional(&mut **tx)
        .await?
        {
            return Ok(id);
        }
    }
    anyhow::bail!("authenticated user does not exist locally")
}

async fn lock_message(
    tx: &mut Transaction<'_, Postgres>,
    message_id: Uuid,
    actor_id: i64,
    edit_window_seconds: i64,
) -> Result<LockedMessage> {
    let row = sqlx::query_as::<_, (
        Uuid,
        Option<i64>,
        String,
        Value,
        bool,
        String,
        i32,
        bool,
        String,
        i64,
        bool,
        bool,
        bool,
        String,
    )>(
        r#"
        SELECT
            m.conversation_id,
            m.sender_id,
            m.text,
            COALESCE(m.metadata, '{}'::jsonb),
            m.is_deleted,
            m.delivery_status,
            m.retry_count::integer,
            (
                m.sender_id = $2
                AND NOT m.is_deleted
                AND m.delivery_status <> 'failed'
                AND m.type IN ('text', 'image', 'video', 'file')
                AND (COALESCE(m.text, '') <> '' OR COALESCE(m.metadata->'encrypted' = 'true'::jsonb, FALSE))
                AND m.edit_locked_at IS NULL
                AND $3 > 0
                AND NOW() < m.created_at + ($3 * INTERVAL '1 second')
            ) AS can_edit,
            CASE
                WHEN m.sender_id IS DISTINCT FROM $2 THEN 'not_owner'
                WHEN m.is_deleted THEN 'deleted'
                WHEN m.delivery_status = 'failed' THEN 'failed'
                WHEN m.type NOT IN ('text', 'image', 'video', 'file')
                     OR (COALESCE(m.text, '') = '' AND NOT COALESCE(m.metadata->'encrypted' = 'true'::jsonb, FALSE)) THEN 'unsupported_type'
                WHEN m.edit_locked_at IS NOT NULL THEN COALESCE(NULLIF(m.edit_locked_reason, ''), 'message_activity_locked')
                WHEN $3 <= 0 OR NOW() >= m.created_at + ($3 * INTERVAL '1 second') THEN 'edit_window_expired'
                ELSE ''
            END AS edit_reason,
            COALESCE(c.e2ee_key_version, 1)::bigint,
            c.e2ee_rekey_required,
            cp.is_blocked,
            (cp.moderation_muted_until IS NOT NULL AND cp.moderation_muted_until > NOW()),
            COALESCE(m.deletion_source, '')
        FROM chat_message m
        JOIN chat_conversation c ON c.id = m.conversation_id AND c.is_active = TRUE
        JOIN chat_conversationparticipant cp
          ON cp.conversation_id = m.conversation_id
         AND cp.user_id = $2
         AND cp.left_at IS NULL
         AND cp.banned_at IS NULL
        WHERE m.id = $1
        FOR UPDATE OF m, cp
        "#,
    )
    .bind(message_id)
    .bind(actor_id)
    .bind(edit_window_seconds)
    .persistent(false)
    .fetch_optional(&mut **tx)
    .await?
    .context("message was not found or actor is not an active participant")?;

    Ok(LockedMessage {
        conversation_id: row.0,
        sender_id: row.1,
        text: row.2,
        metadata: row.3,
        is_deleted: row.4,
        delivery_status: row.5,
        retry_count: row.6,
        can_edit: row.7,
        edit_reason: row.8,
        e2ee_key_version: row.9,
        e2ee_rekey_required: row.10,
        participant_blocked: row.11,
        participant_muted: row.12,
        deletion_source: row.13,
    })
}

fn ensure_participant_allowed(message: &LockedMessage) -> Result<()> {
    if message.participant_blocked {
        anyhow::bail!("participant is blocked");
    }
    Ok(())
}

fn ensure_owner(message: &LockedMessage, actor_id: i64, action: &str) -> Result<()> {
    if message.sender_id != Some(actor_id) {
        anyhow::bail!("You can {action} only your own messages.");
    }
    Ok(())
}

fn edit_reason_detail(code: &str) -> &'static str {
    match code {
        "not_owner" => "You can edit only your own messages.",
        "deleted" => "Deleted messages cannot be edited.",
        "failed" => "Failed messages cannot be edited. Retry or delete the message instead.",
        "unsupported_type" => "Only text and attachment captions can be edited.",
        "message_has_reactions" => "This message can no longer be edited because someone reacted to it.",
        "message_has_replies" => "This message can no longer be edited because it has replies.",
        "message_was_forwarded" => "This message can no longer be edited because it was forwarded.",
        "edit_window_expired" => "The editing window has expired.",
        _ => "This message can no longer be edited because it has activity.",
    }
}

pub(crate) fn sanitize_text(raw: &str) -> String {
    let normalized = raw.replace("\r\n", "\n").replace('\r', "\n");
    let filtered: String = normalized
        .chars()
        .filter(|ch| {
            let code = *ch as u32;
            !matches!(code, 0..=8 | 11 | 12 | 14..=31 | 127)
        })
        .collect();
    let lines = filtered
        .split('\n')
        .map(|line| line.split_whitespace().collect::<Vec<_>>().join(" "))
        .collect::<Vec<_>>()
        .join("\n");
    lines.trim().chars().take(MAX_TEXT_CHARS).collect()
}

fn string_value(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        Some(Value::Bool(value)) => value.to_string(),
        _ => String::new(),
    }
}

pub(crate) async fn sanitize_entities(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    entities: &[MessageEntity],
) -> Result<(Vec<Value>, Vec<String>)> {
    let allowed = ["bold", "italic", "underline", "strike", "code", "link", "mention"];
    let mut clean = Vec::new();
    let mut mention_ids = Vec::new();

    for entity in entities {
        let entity_type = entity.entity_type.trim().to_ascii_lowercase();
        if !allowed.contains(&entity_type.as_str()) {
            return Err(validation_error("invalid_entities", "Message formatting contains an unsupported entity type."));
        }
        if entity.offset < 0 || entity.length < 1 {
            return Err(validation_error("invalid_entities", "Message formatting offsets and lengths must be positive."));
        }
        let mut item = Map::new();
        item.insert("type".to_owned(), Value::String(entity_type.clone()));
        item.insert("offset".to_owned(), json!(entity.offset.clamp(0, 50_000)));
        item.insert("length".to_owned(), json!(entity.length.clamp(1, 50_000)));
        if entity_type == "link" {
            let url: String = entity.url.trim().chars().take(1_000).collect();
            if !url.is_empty() {
                item.insert("url".to_owned(), Value::String(url));
            }
        }
        if entity_type == "mention" {
            let user_id = string_value(entity.user_id.as_ref());
            if !user_id.is_empty() {
                item.insert("user_id".to_owned(), Value::String(user_id.clone()));
                mention_ids.push(user_id);
                let username: String = entity.username.chars().take(150).collect();
                if !username.is_empty() {
                    item.insert("username".to_owned(), Value::String(username));
                }
            }
        }
        clean.push(Value::Object(item));
    }

    if mention_ids.is_empty() {
        return Ok((clean, Vec::new()));
    }

    let numeric_ids = mention_ids
        .iter()
        .filter_map(|value| value.parse::<i64>().ok())
        .collect::<Vec<_>>();
    let valid_ids = if numeric_ids.is_empty() {
        Vec::new()
    } else {
        sqlx::query_scalar::<_, i64>(
            "SELECT DISTINCT user_id FROM chat_conversationparticipant WHERE conversation_id = $1 AND user_id = ANY($2) AND left_at IS NULL AND banned_at IS NULL",
        )
        .bind(conversation_id)
        .bind(&numeric_ids)
        .persistent(false)
        .fetch_all(&mut **tx)
        .await?
    };
    let valid = valid_ids.iter().map(ToString::to_string).collect::<HashSet<_>>();
    clean.retain(|item| {
        item.get("type").and_then(Value::as_str) != Some("mention")
            || item
                .get("user_id")
                .and_then(Value::as_str)
                .map(|value| valid.contains(value))
                .unwrap_or(false)
    });
    let mut valid_list = valid.into_iter().collect::<Vec<_>>();
    valid_list.sort();
    Ok((clean, valid_list))
}

pub(crate) fn extract_links(text: &str) -> Vec<Value> {
    let mut seen = HashSet::new();
    let mut links = Vec::new();
    for token in text.split_whitespace() {
        let candidate = token
            .trim_matches(|ch: char| matches!(ch, '<' | '>' | '(' | ')' | ',' | '.' | ';' | ':' | '!' | '?' | '"' | '\''));
        if (candidate.starts_with("http://") || candidate.starts_with("https://"))
            && seen.insert(candidate.to_owned())
        {
            links.push(Value::String(candidate.to_owned()));
            if links.len() == 10 {
                break;
            }
        }
    }
    links
}

fn truncate_string(value: &str, max_chars: usize) -> String {
    value.trim().chars().take(max_chars).collect()
}

pub(crate) fn sanitize_encryption_envelope(value: Value, current_key_version: i64) -> Result<Value> {
    let input = value
        .as_object()
        .ok_or_else(|| validation_error("invalid_encryption", "Encryption envelope must be an object."))?;
    let ciphertext = input.get("ciphertext").and_then(Value::as_str).unwrap_or("");
    if ciphertext.is_empty() {
        return Err(validation_error("invalid_encryption", "Ciphertext is required for encrypted messages."));
    }
    if ciphertext.as_bytes().len() > MAX_CIPHERTEXT_BYTES {
        return Err(validation_error("invalid_encryption", "Ciphertext is too large."));
    }

    let mut recipient_key_ids = input
        .get("recipient_key_ids")
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .map(|value| truncate_string(&string_value(Some(value)), 256))
                .filter(|value| !value.is_empty())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let mut encrypted_keys = Vec::new();
    if let Some(values) = input.get("encrypted_keys").and_then(Value::as_array) {
        for entry in values {
            let Some(entry) = entry.as_object() else { continue; };
            let key_id = truncate_string(entry.get("key_id").and_then(Value::as_str).unwrap_or(""), 256);
            let wrapped_key = entry.get("wrapped_key").and_then(Value::as_str).unwrap_or("");
            if !key_id.is_empty() && !wrapped_key.is_empty() {
                encrypted_keys.push(json!({"key_id": key_id, "wrapped_key": wrapped_key}));
            }
        }
    }
    if !encrypted_keys.is_empty() && recipient_key_ids.is_empty() {
        recipient_key_ids = encrypted_keys
            .iter()
            .filter_map(|entry| entry.get("key_id").and_then(Value::as_str).map(ToOwned::to_owned))
            .collect();
    }
    if recipient_key_ids.is_empty() {
        return Err(validation_error("invalid_encryption", "At least one recipient key id is required."));
    }

    let algorithm = truncate_string(input.get("algorithm").and_then(Value::as_str).unwrap_or(""), 80);
    let nonce = truncate_string(input.get("nonce").and_then(Value::as_str).unwrap_or(""), 256);
    let sender_key_id = truncate_string(input.get("sender_key_id").and_then(Value::as_str).unwrap_or(""), 256);
    if algorithm.is_empty() {
        return Err(validation_error("invalid_encryption", "Encryption algorithm is required."));
    }
    if nonce.is_empty() {
        return Err(validation_error("invalid_encryption", "Encryption nonce is required."));
    }
    if sender_key_id.is_empty() {
        return Err(validation_error("invalid_encryption", "Sender key id is required."));
    }

    let requested_version = input
        .get("key_version")
        .and_then(Value::as_i64)
        .unwrap_or(current_key_version)
        .max(1);
    if requested_version != current_key_version {
        return Err(validation_error(
            "e2ee_stale_key_version",
            "Encrypted edit uses an outdated secure-device list. Refresh and try again.",
        ));
    }

    let mut envelope = Map::new();
    envelope.insert(
        "version".to_owned(),
        Value::String(truncate_string(input.get("version").and_then(Value::as_str).unwrap_or("v1"), 32)),
    );
    envelope.insert("algorithm".to_owned(), Value::String(algorithm));
    envelope.insert("ciphertext".to_owned(), Value::String(ciphertext.to_owned()));
    envelope.insert("nonce".to_owned(), Value::String(nonce));
    envelope.insert("sender_key_id".to_owned(), Value::String(sender_key_id));
    envelope.insert("recipient_key_ids".to_owned(), json!(recipient_key_ids));
    envelope.insert("key_version".to_owned(), json!(requested_version));
    if !encrypted_keys.is_empty() {
        envelope.insert("encrypted_keys".to_owned(), Value::Array(encrypted_keys));
    }
    let sender_device_id = truncate_string(input.get("sender_device_id").and_then(Value::as_str).unwrap_or(""), 256);
    if !sender_device_id.is_empty() {
        envelope.insert("sender_device_id".to_owned(), Value::String(sender_device_id));
    }
    if let Some(aad) = input.get("aad") {
        envelope.insert("aad".to_owned(), aad.clone());
    }
    let value = Value::Object(envelope);
    if serde_json::to_vec(&value)?.len() > MAX_ENVELOPE_BYTES {
        return Err(validation_error("invalid_encryption", "Encryption envelope is too large."));
    }
    Ok(value)
}

pub(crate) async fn validate_encryption_coverage(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    actor_id: i64,
    envelope: &Value,
) -> Result<()> {
    let rows = sqlx::query_as::<_, (i64, Option<String>)>(
        "SELECT cp.user_id, key.key_id FROM chat_conversationparticipant cp LEFT JOIN chat_usere2eedevicekey key ON key.user_id = cp.user_id AND key.is_active = TRUE WHERE cp.conversation_id = $1 AND cp.left_at IS NULL AND cp.banned_at IS NULL",
    )
    .bind(conversation_id)
    .persistent(false)
    .fetch_all(&mut **tx)
    .await?;

    let mut participant_has_key: HashMap<i64, bool> = HashMap::new();
    let mut active_key_ids = HashSet::new();
    for (user_id, key_id) in rows {
        participant_has_key.entry(user_id).or_insert(false);
        if let Some(key_id) = key_id {
            participant_has_key.insert(user_id, true);
            active_key_ids.insert(key_id);
        }
    }
    if participant_has_key.values().any(|has_key| !has_key) {
        return Err(validation_error(
            "e2ee_participant_device_missing",
            "Encrypted edit cannot be sent until every participant has a registered secure device.",
        ));
    }

    let recipient_ids = envelope
        .get("recipient_key_ids")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(ToOwned::to_owned)
        .collect::<HashSet<_>>();
    let wrapped_ids = envelope
        .get("encrypted_keys")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|entry| entry.get("key_id").and_then(Value::as_str))
        .map(ToOwned::to_owned)
        .collect::<HashSet<_>>();
    if active_key_ids.difference(&recipient_ids).next().is_some()
        || active_key_ids.difference(&wrapped_ids).next().is_some()
    {
        return Err(validation_error(
            "e2ee_device_coverage_incomplete",
            "Encrypted edit does not cover every active secure device.",
        ));
    }

    let sender_key_id = envelope.get("sender_key_id").and_then(Value::as_str).unwrap_or("");
    let sender_device_id = envelope.get("sender_device_id").and_then(Value::as_str).unwrap_or("");
    let valid_sender = if sender_device_id.is_empty() {
        sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM chat_usere2eedevicekey WHERE user_id = $1 AND key_id = $2 AND is_active = TRUE)",
        )
        .bind(actor_id)
        .bind(sender_key_id)
        .persistent(false)
        .fetch_one(&mut **tx)
        .await?
    } else {
        sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM chat_usere2eedevicekey WHERE user_id = $1 AND key_id = $2 AND device_id = $3 AND is_active = TRUE)",
        )
        .bind(actor_id)
        .bind(sender_key_id)
        .bind(sender_device_id)
        .persistent(false)
        .fetch_one(&mut **tx)
        .await?
    };
    if !valid_sender {
        return Err(validation_error(
            "e2ee_sender_device_invalid",
            "Encrypted edit was not created by an active secure device for this account.",
        ));
    }
    Ok(())
}

async fn insert_audit_event(
    tx: &mut Transaction<'_, Postgres>,
    event_type: &str,
    actor_id: i64,
    conversation_id: Uuid,
    message_id: Uuid,
    metadata: Value,
) -> Result<()> {
    sqlx::query(
        "INSERT INTO chat_chatauditlog (id, created_at, updated_at, actor_id, conversation_id, message_id, event_type, metadata) VALUES ($1, NOW(), NOW(), $2, $3, $4, $5, $6)",
    )
    .bind(Uuid::new_v4())
    .bind(actor_id)
    .bind(conversation_id)
    .bind(message_id)
    .bind(event_type)
    .bind(metadata)
    .persistent(false)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

async fn insert_conversation_event(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    event_name: &str,
    data: Value,
) -> Result<CommittedEvent> {
    let event_id = Uuid::new_v4();
    let event_payload = sqlx::query_scalar::<_, Value>(
        "SELECT jsonb_build_object('type', 'chat.event', 'version', 1, 'event', $1::text, 'event_id', $2::text, 'occurred_at', NOW(), 'data', $3::jsonb)",
    )
    .bind(event_name)
    .bind(event_id.to_string())
    .bind(&data)
    .persistent(false)
    .fetch_one(&mut **tx)
    .await?;
    let audiences = json!([{"kind": "conversation", "id": conversation_id.to_string()}]);
    sqlx::query(
        "INSERT INTO common_realtimeoutboxevent (id, created_at, updated_at, event_id, event_name, payload, audiences, status, attempts, available_at, published_at, delivery_target, published_transport, stream_entry_id, last_error) VALUES ($1, NOW(), NOW(), $2, $3, $4, $5, 'pending', 0, NOW(), NULL, 'nats_jetstream', '', '', '')",
    )
    .bind(Uuid::new_v4())
    .bind(event_id)
    .bind(event_name)
    .bind(&event_payload)
    .bind(&audiences)
    .persistent(false)
    .execute(&mut **tx)
    .await?;
    Ok(CommittedEvent {
        event_id,
        event_name: event_name.to_owned(),
        payload: event_payload,
        audiences: vec![AudienceKey {
            kind: AudienceKind::Conversation,
            identifier: conversation_id.to_string(),
        }],
    })
}

impl Database {
    pub(crate) async fn edit_chat_message(
        &self,
        message_id: Uuid,
        identity: &CommandIdentity,
        input: MessageEditRequest,
    ) -> Result<MutationResult> {
        let pool = self.pool.as_ref().context("SQLx message mutation backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let message = lock_message(&mut tx, message_id, actor_id, self.message_edit_window_seconds).await?;
        ensure_participant_allowed(&message)?;
        if !message.can_edit {
            if message.edit_reason == "not_owner" {
                anyhow::bail!("You can edit only your own messages.");
            }
            return Err(validation_error(&message.edit_reason, edit_reason_detail(&message.edit_reason)));
        }

        let encrypted_edit = input.is_encrypted || input.encryption.is_some();
        let mut metadata = message.metadata.as_object().cloned().unwrap_or_default();
        let new_text;

        if encrypted_edit {
            if input.text.as_deref().is_some_and(|value| !value.is_empty()) {
                return Err(validation_error("invalid_encrypted_edit", "Encrypted edits must not include plaintext text."));
            }
            if !input.entities.is_empty() {
                return Err(validation_error("invalid_encrypted_edit", "Encrypted edits cannot include plaintext formatting entities."));
            }
            let envelope = sanitize_encryption_envelope(
                input.encryption.ok_or_else(|| validation_error("invalid_encryption", "Encryption envelope is required for an encrypted edit."))?,
                message.e2ee_key_version,
            )?;
            validate_encryption_coverage(&mut tx, message.conversation_id, actor_id, &envelope).await?;
            if metadata.get("encrypted").and_then(Value::as_bool).unwrap_or(false)
                && metadata.get("encryption") == Some(&envelope)
            {
                let payload = self
                    .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
                    .await?
                    .context("message was not found after no-op encrypted edit")?;
                tx.commit().await?;
                return Ok(MutationResult { payload, events: Vec::new() });
            }
            sqlx::query(
                "INSERT INTO chat_messageedithistory (id, created_at, updated_at, message_id, edited_by_id, previous_text, new_text) VALUES ($1, NOW(), NOW(), $2, $3, '', '')",
            )
            .bind(Uuid::new_v4())
            .bind(message_id)
            .bind(actor_id)
            .persistent(false)
            .execute(&mut *tx)
            .await?;
            metadata.insert("encrypted".to_owned(), Value::Bool(true));
            metadata.insert("encryption".to_owned(), envelope);
            metadata.insert("raw_text".to_owned(), Value::String(String::new()));
            metadata.insert("entities".to_owned(), Value::Array(Vec::new()));
            metadata.insert("links".to_owned(), Value::Array(Vec::new()));
            metadata.insert("mentioned_user_ids".to_owned(), Value::Array(Vec::new()));
            new_text = String::new();
        } else {
            if metadata.get("encrypted").and_then(Value::as_bool).unwrap_or(false) {
                return Err(validation_error(
                    "e2ee_edit_envelope_required",
                    "Encrypted messages must be edited with a new encryption envelope.",
                ));
            }
            let raw_text = input
                .text
                .ok_or_else(|| validation_error("text_required", "Message text is required."))?;
            new_text = sanitize_text(&raw_text);
            if new_text == message.text {
                let payload = self
                    .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
                    .await?
                    .context("message was not found after no-op edit")?;
                tx.commit().await?;
                return Ok(MutationResult { payload, events: Vec::new() });
            }
            sqlx::query(
                "INSERT INTO chat_messageedithistory (id, created_at, updated_at, message_id, edited_by_id, previous_text, new_text) VALUES ($1, NOW(), NOW(), $2, $3, $4, $5)",
            )
            .bind(Uuid::new_v4())
            .bind(message_id)
            .bind(actor_id)
            .bind(&message.text)
            .bind(&new_text)
            .persistent(false)
            .execute(&mut *tx)
            .await?;
            metadata.insert("raw_text".to_owned(), Value::String(new_text.clone()));
            let (entities, mentioned_ids) = sanitize_entities(&mut tx, message.conversation_id, &input.entities).await?;
            if entities.is_empty() {
                metadata.remove("entities");
            } else {
                metadata.insert("entities".to_owned(), Value::Array(entities));
            }
            if mentioned_ids.is_empty() {
                metadata.remove("mentioned_user_ids");
            } else {
                metadata.insert("mentioned_user_ids".to_owned(), json!(mentioned_ids));
            }
            let links = extract_links(&new_text);
            if links.is_empty() {
                metadata.remove("links");
            } else {
                metadata.insert("links".to_owned(), Value::Array(links));
            }
        }

        sqlx::query(
            "UPDATE chat_message SET text = $2, metadata = $3, is_edited = TRUE, edited_at = NOW(), updated_at = NOW() WHERE id = $1",
        )
        .bind(message_id)
        .bind(&new_text)
        .bind(Value::Object(metadata))
        .persistent(false)
        .execute(&mut *tx)
        .await?;

        if encrypted_edit && message.e2ee_rekey_required {
            sqlx::query(
                "UPDATE chat_conversation SET e2ee_rekey_required = FALSE, e2ee_last_key_rotation_at = NOW(), updated_at = NOW() WHERE id = $1",
            )
            .bind(message.conversation_id)
            .persistent(false)
            .execute(&mut *tx)
            .await?;
        }

        let payload = self
            .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
            .await?
            .context("message was not found after edit")?;
        insert_audit_event(
            &mut tx,
            "message_edited",
            actor_id,
            message.conversation_id,
            message_id,
            json!({}),
        )
        .await?;
        let event = insert_conversation_event(
            &mut tx,
            message.conversation_id,
            "message.updated",
            payload.clone(),
        )
        .await?;
        tx.commit().await?;
        Ok(MutationResult { payload, events: vec![event] })
    }

    pub(crate) async fn delete_chat_message(
        &self,
        message_id: Uuid,
        identity: &CommandIdentity,
    ) -> Result<MutationResult> {
        let pool = self.pool.as_ref().context("SQLx message mutation backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let message = lock_message(&mut tx, message_id, actor_id, self.message_edit_window_seconds).await?;
        ensure_participant_allowed(&message)?;
        ensure_owner(&message, actor_id, "delete")?;
        if message.is_deleted {
            let payload = self
                .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
                .await?
                .context("message was not found after no-op delete")?;
            tx.commit().await?;
            return Ok(MutationResult { payload, events: Vec::new() });
        }

        sqlx::query(
            "UPDATE chat_message SET deleted_text_backup = text, deletion_source = 'sender', text = '', is_deleted = TRUE, deleted_at = NOW(), updated_at = NOW() WHERE id = $1",
        )
        .bind(message_id)
        .persistent(false)
        .execute(&mut *tx)
        .await?;
        let payload = self
            .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
            .await?
            .context("message was not found after delete")?;
        insert_audit_event(
            &mut tx,
            "message_deleted",
            actor_id,
            message.conversation_id,
            message_id,
            json!({}),
        )
        .await?;
        let event = insert_conversation_event(
            &mut tx,
            message.conversation_id,
            "message.deleted",
            payload.clone(),
        )
        .await?;
        tx.commit().await?;
        Ok(MutationResult { payload, events: vec![event] })
    }

    pub(crate) async fn restore_chat_message(
        &self,
        message_id: Uuid,
        identity: &CommandIdentity,
    ) -> Result<MutationResult> {
        let pool = self.pool.as_ref().context("SQLx message mutation backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let message = lock_message(&mut tx, message_id, actor_id, self.message_edit_window_seconds).await?;
        ensure_participant_allowed(&message)?;
        ensure_owner(&message, actor_id, "restore")?;
        if !message.is_deleted {
            let payload = self
                .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
                .await?
                .context("message was not found after no-op restore")?;
            tx.commit().await?;
            return Ok(MutationResult { payload, events: Vec::new() });
        }
        if message.deletion_source != "sender" {
            anyhow::bail!("This deleted message cannot be restored by its sender.");
        }

        sqlx::query(
            "UPDATE chat_message SET text = deleted_text_backup, deleted_text_backup = '', deletion_source = '', is_deleted = FALSE, deleted_at = NULL, updated_at = NOW() WHERE id = $1",
        )
        .bind(message_id)
        .persistent(false)
        .execute(&mut *tx)
        .await?;
        let payload = self
            .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
            .await?
            .context("message was not found after restore")?;
        insert_audit_event(
            &mut tx,
            "message_restored",
            actor_id,
            message.conversation_id,
            message_id,
            json!({}),
        )
        .await?;
        let event = insert_conversation_event(
            &mut tx,
            message.conversation_id,
            "message.restored",
            payload.clone(),
        )
        .await?;
        tx.commit().await?;
        Ok(MutationResult { payload, events: vec![event] })
    }

    pub(crate) async fn retry_chat_message(
        &self,
        message_id: Uuid,
        identity: &CommandIdentity,
    ) -> Result<MutationResult> {
        let pool = self.pool.as_ref().context("SQLx message mutation backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let message = lock_message(&mut tx, message_id, actor_id, self.message_edit_window_seconds).await?;
        ensure_participant_allowed(&message)?;
        ensure_owner(&message, actor_id, "retry")?;
        if message.participant_muted {
            anyhow::bail!("participant is muted");
        }
        let direct_blocked = sqlx::query_scalar::<_, bool>(
            "SELECT CASE WHEN c.type='direct' THEN EXISTS(SELECT 1 FROM chat_userblock b JOIN chat_conversationparticipant peer ON peer.conversation_id=c.id AND peer.user_id<>$2 AND peer.left_at IS NULL AND peer.banned_at IS NULL WHERE (b.blocker_id=$2 AND b.blocked_id=peer.user_id) OR (b.blocker_id=peer.user_id AND b.blocked_id=$2)) ELSE FALSE END FROM chat_conversation c WHERE c.id=$1",
        )
        .bind(message.conversation_id)
        .bind(actor_id)
        .persistent(false)
        .fetch_one(&mut *tx)
        .await?;
        if direct_blocked { anyhow::bail!("direct conversation is blocked"); }
        if message.delivery_status != "failed" {
            if message.delivery_status == "sent" && message.retry_count > 0 {
                let payload = self
                    .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
                    .await?
                    .context("message was not found after idempotent retry")?;
                tx.commit().await?;
                return Ok(MutationResult { payload, events: Vec::new() });
            }
            return Err(validation_error("message_not_failed", "Only failed messages can be retried."));
        }

        let retry_count = sqlx::query_scalar::<_, i32>(
            "UPDATE chat_message SET delivery_status = 'sent', failed_reason = '', retry_count = retry_count + 1, updated_at = NOW() WHERE id = $1 RETURNING retry_count::integer",
        )
        .bind(message_id)
        .persistent(false)
        .fetch_one(&mut *tx)
        .await?;
        let payload = self
            .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
            .await?
            .context("message was not found after retry")?;
        insert_audit_event(
            &mut tx,
            "message_retried",
            actor_id,
            message.conversation_id,
            message_id,
            json!({"retry_count": retry_count}),
        )
        .await?;
        let event = insert_conversation_event(
            &mut tx,
            message.conversation_id,
            "message.retried",
            payload.clone(),
        )
        .await?;
        tx.commit().await?;
        Ok(MutationResult { payload, events: vec![event] })
    }
}
