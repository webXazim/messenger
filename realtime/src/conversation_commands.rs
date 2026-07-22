use std::{collections::HashSet, sync::Arc};

use anyhow::{Context, Result};
use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Value};
use sqlx::{Postgres, Transaction};
use uuid::Uuid;

use crate::{
    command_auth::{CommandAuthError, CommandIdentity},
    command_delivery::deliver_committed,
    commands::error_response,
    config::ChatConversationCommandBackend,
    database::{CommittedEvent, Database},
    nats_core::{self, EphemeralPriority},
    protocol::{event_message, AudienceKey, AudienceKind},
    state::AppState,
};

#[derive(Debug, Default, Deserialize)]
pub(crate) struct CreateConversationRequest {
    #[serde(default, rename = "type")]
    conversation_type: String,
    #[serde(default)]
    title: String,
    #[serde(default)]
    slug: String,
    #[serde(default)]
    participant_ids: Vec<i64>,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct DraftRequest {
    #[serde(default)]
    text: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_optional")]
    reply_to_id: Option<Option<Uuid>>,
    #[serde(default)]
    metadata: Option<Value>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct ParticipantsRequest {
    participant_ids: Vec<i64>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct RoleRequest {
    role: String,
}

#[derive(Debug, Deserialize)]
pub(crate) struct MuteParticipantRequest {
    minutes: i64,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct BanParticipantRequest {
    #[serde(default)]
    reason: String,
}

#[derive(Debug, Deserialize)]
pub(crate) struct TransferOwnershipRequest {
    target_user_id: i64,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct BlockUserRequest {
    blocked_user_id: i64,
    #[serde(default)]
    reason: String,
}

pub(crate) struct ConversationCommandResult {
    pub payload: Value,
    pub events: Vec<CommittedEvent>,
}

fn deserialize_present_optional<'de, D, T>(deserializer: D) -> Result<Option<Option<T>>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer).map(Some)
}

fn enabled(state: &AppState) -> bool {
    matches!(
        state.config.chat_conversation_command_backend,
        ChatConversationCommandBackend::SqlxShadow | ChatConversationCommandBackend::Axum
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

fn validation(code: &str, detail: &str) -> anyhow::Error {
    anyhow::anyhow!("validation|{code}|{detail}")
}

fn forbidden(code: &str, detail: &str) -> anyhow::Error {
    anyhow::anyhow!("forbidden|{code}|{detail}")
}

fn command_error(error: anyhow::Error, operation: &'static str) -> axum::response::Response {
    let message = error.to_string();
    tracing::warn!(error = %error, operation, "Axum conversation command rejected");
    for (prefix, status) in [
        ("validation|", StatusCode::BAD_REQUEST),
        ("forbidden|", StatusCode::FORBIDDEN),
    ] {
        if let Some(rest) = message.strip_prefix(prefix) {
            let mut parts = rest.splitn(2, '|');
            return error_response(
                status,
                parts.next().unwrap_or("conversation_command_failed"),
                parts.next().unwrap_or("The conversation command is invalid."),
            );
        }
    }
    if message.contains("authenticated user does not exist") {
        error_response(StatusCode::UNAUTHORIZED, "authentication_failed", "The authenticated account is unavailable.")
    } else if message.contains("conversation not found") || message.contains("participant not found") {
        error_response(StatusCode::NOT_FOUND, "conversation_not_found", "Conversation or participant was not found.")
    } else {
        tracing::error!(error = %error, operation, "Axum SQLx conversation command failed");
        error_response(StatusCode::INTERNAL_SERVER_ERROR, "conversation_command_failed", "The conversation command could not be completed.")
    }
}

async fn schedule_conversation_cleanup(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    reason: &str,
) -> Result<()> {
    let job_id = Uuid::new_v4();
    sqlx::query(
        "INSERT INTO chat_chatdataplanejob (id,created_at,updated_at,kind,dedupe_key,conversation_id,message_id,payload,status,attempts,available_at,locked_at,last_error) VALUES ($1,NOW(),NOW(),'conversation_cleanup',$2,$3,NULL,$4,'pending',0,NOW()+INTERVAL '2 seconds',NULL,'')",
    )
    .bind(job_id)
    .bind(format!("conversation_cleanup:{conversation_id}:{job_id}"))
    .bind(conversation_id)
    .bind(json!({"source":"axum_sqlx","reason":reason}))
    .persistent(false)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

fn sanitize_text(raw: &str, max_chars: usize, multiline: bool) -> String {
    let normalized = raw.replace("\r\n", "\n").replace('\r', "\n");
    let filtered: String = normalized
        .chars()
        .filter(|ch| {
            let code = *ch as u32;
            !matches!(code, 0..=8 | 11 | 12 | 14..=31 | 127)
        })
        .collect();
    let cleaned = if multiline {
        filtered
            .split('\n')
            .map(|line| line.split_whitespace().collect::<Vec<_>>().join(" "))
            .collect::<Vec<_>>()
            .join("\n")
    } else {
        filtered.split_whitespace().collect::<Vec<_>>().join(" ")
    };
    cleaned.trim().chars().take(max_chars).collect()
}

fn direct_key_for_users(user_a: i64, user_b: i64) -> String {
    // Preserve Django's existing contract: decimal ids are sorted as strings.
    let user_a = user_a.to_string();
    let user_b = user_b.to_string();
    if user_a <= user_b {
        format!("{user_a}:{user_b}")
    } else {
        format!("{user_b}:{user_a}")
    }
}

fn normalize_slug(raw: &str) -> String {
    let mut result = String::new();
    let mut pending_dash = false;
    for ch in raw.trim().to_lowercase().chars() {
        if ch.is_alphanumeric() {
            if pending_dash && !result.is_empty() && result.chars().count() < 80 {
                result.push('-');
            }
            pending_dash = false;
            if result.chars().count() < 80 {
                result.push(ch);
            }
        } else if ch == '-' || ch == '_' || ch.is_whitespace() {
            pending_dash = !result.is_empty();
        }
        if result.chars().count() >= 80 {
            break;
        }
    }
    result.trim_matches('-').to_owned()
}

async fn finish(
    state: &Arc<AppState>,
    result: Result<ConversationCommandResult>,
    operation: &'static str,
    success: StatusCode,
) -> axum::response::Response {
    match result {
        Ok(result) => {
            deliver_committed(state, &result.events).await;
            (success, Json(result.payload)).into_response()
        }
        Err(error) => command_error(error, operation),
    }
}

pub(crate) async fn create_conversation(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(input): Json<CreateConversationRequest>,
) -> impl IntoResponse {
    if !enabled(&state) {
        return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active.");
    }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    if input.conversation_type.trim().eq_ignore_ascii_case("group") && state.config.central_payments_enabled {
        return error_response(
            StatusCode::UNPROCESSABLE_ENTITY,
            "django_fallback_required",
            "Group creation requires Django to enforce billing access and record usage.",
        );
    }
    match state.database.create_conversation(&identity, input).await {
        Ok(result) => {
            let status = if result.events.is_empty() { StatusCode::OK } else { StatusCode::CREATED };
            deliver_committed(&state, &result.events).await;
            (status, Json(result.payload)).into_response()
        }
        Err(error) => command_error(error, "create_conversation"),
    }
}

macro_rules! toggle_handler {
    ($name:ident, $method:ident, $op:literal) => {
        pub(crate) async fn $name(
            State(state): State<Arc<AppState>>,
            Path(conversation_id): Path<Uuid>,
            headers: HeaderMap,
        ) -> impl IntoResponse {
            if !enabled(&state) {
                return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active.");
            }
            let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
            finish(&state, state.database.$method(conversation_id, &identity).await, $op, StatusCode::OK).await
        }
    };
}

toggle_handler!(toggle_mute, toggle_conversation_mute, "toggle_mute");
toggle_handler!(toggle_archive, toggle_conversation_archive, "toggle_archive");
toggle_handler!(toggle_pin, toggle_conversation_pin, "toggle_pin");

pub(crate) async fn get_draft(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !enabled(&state) {
        return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active.");
    }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match state.database.get_conversation_draft(conversation_id, &identity).await {
        Ok(payload) => (StatusCode::OK, Json(payload)).into_response(),
        Err(error) => command_error(error, "get_draft"),
    }
}

pub(crate) async fn save_draft(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<DraftRequest>,
) -> impl IntoResponse {
    if !enabled(&state) {
        return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active.");
    }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match state.database.save_conversation_draft(conversation_id, &identity, input).await {
        Ok(payload) => (StatusCode::OK, Json(payload)).into_response(),
        Err(error) => command_error(error, "save_draft"),
    }
}

pub(crate) async fn delete_draft(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !enabled(&state) {
        return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active.");
    }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match state.database.delete_conversation_draft(conversation_id, &identity).await {
        Ok(payload) => (StatusCode::OK, Json(payload)).into_response(),
        Err(error) => command_error(error, "delete_draft"),
    }
}

pub(crate) async fn add_participants(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<ParticipantsRequest>,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    finish(&state, state.database.add_conversation_participants(conversation_id, &identity, input.participant_ids).await, "add_participants", StatusCode::OK).await
}

pub(crate) async fn remove_participant(
    State(state): State<Arc<AppState>>,
    Path((conversation_id, user_id)): Path<(Uuid, i64)>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    finish(&state, state.database.remove_conversation_participant(conversation_id, &identity, user_id).await, "remove_participant", StatusCode::OK).await
}

pub(crate) async fn update_participant_role(
    State(state): State<Arc<AppState>>,
    Path((conversation_id, user_id)): Path<(Uuid, i64)>,
    headers: HeaderMap,
    Json(input): Json<RoleRequest>,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    finish(&state, state.database.update_conversation_participant_role(conversation_id, &identity, user_id, &input.role).await, "update_participant_role", StatusCode::OK).await
}

pub(crate) async fn mute_participant(
    State(state): State<Arc<AppState>>,
    Path((conversation_id, user_id)): Path<(Uuid, i64)>,
    headers: HeaderMap,
    Json(input): Json<MuteParticipantRequest>,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    finish(&state, state.database.mute_conversation_participant(conversation_id, &identity, user_id, input.minutes).await, "mute_participant", StatusCode::OK).await
}

pub(crate) async fn ban_participant(
    State(state): State<Arc<AppState>>,
    Path((conversation_id, user_id)): Path<(Uuid, i64)>,
    headers: HeaderMap,
    Json(input): Json<BanParticipantRequest>,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    finish(&state, state.database.ban_conversation_participant(conversation_id, &identity, user_id, &input.reason).await, "ban_participant", StatusCode::OK).await
}

pub(crate) async fn unban_participant(
    State(state): State<Arc<AppState>>,
    Path((conversation_id, user_id)): Path<(Uuid, i64)>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    finish(&state, state.database.unban_conversation_participant(conversation_id, &identity, user_id).await, "unban_participant", StatusCode::OK).await
}

pub(crate) async fn transfer_ownership(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<TransferOwnershipRequest>,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    finish(&state, state.database.transfer_conversation_ownership(conversation_id, &identity, input.target_user_id).await, "transfer_ownership", StatusCode::OK).await
}

pub(crate) async fn leave_conversation(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    finish(&state, state.database.leave_chat_conversation(conversation_id, &identity).await, "leave_conversation", StatusCode::OK).await
}

pub(crate) async fn block_user(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(input): Json<BlockUserRequest>,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match state.database.block_chat_user(&identity, input.blocked_user_id, &input.reason).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            refresh_pair_presence_from_result(&state, &result, true).await;
            (StatusCode::CREATED, Json(result.payload)).into_response()
        }
        Err(error) => command_error(error, "block_user"),
    }
}

pub(crate) async fn unblock_user(
    State(state): State<Arc<AppState>>,
    Path(user_id): Path<i64>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !enabled(&state) { return error_response(StatusCode::NOT_FOUND, "axum_conversation_commands_disabled", "Axum conversation commands are not active."); }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match state.database.unblock_chat_user(&identity, user_id).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            refresh_pair_presence_from_result(&state, &result, false).await;
            StatusCode::NO_CONTENT.into_response()
        }
        Err(error) => command_error(error, "unblock_user"),
    }
}

async fn refresh_pair_presence_from_result(
    state: &Arc<AppState>,
    result: &ConversationCommandResult,
    blocked: bool,
) {
    let Some(event) = result.events.first() else { return; };
    let data = event.payload.get("data").and_then(Value::as_object);
    let actor_id = data.and_then(|value| value.get("user_id")).and_then(Value::as_str).unwrap_or_default();
    let target_id = data.and_then(|value| value.get("blocked_user_id")).and_then(Value::as_str).unwrap_or_default();
    let blocked = data
        .and_then(|value| value.get("pair_blocked"))
        .and_then(Value::as_bool)
        .unwrap_or(blocked);
    if actor_id.is_empty() || target_id.is_empty() { return; }
    for (subject_id, recipient_id) in [(actor_id, target_id), (target_id, actor_id)] {
        let mut snapshot = if blocked {
            json!({
                "is_online": false,
                "active_devices": 0,
                "last_seen_at": Value::Null,
                "presence_status": "offline",
                "presence_label": "offline",
                "device_type": Value::Null,
                "device_types": [],
                "visibility": "hidden",
            })
        } else {
            match state.presence.user_snapshot(subject_id).await {
                Ok(value) => value,
                Err(error) => {
                    tracing::warn!(error=%error, subject_id, "presence refresh after unblock failed");
                    json!({
                        "is_online": false,
                        "active_devices": 0,
                        "last_seen_at": Value::Null,
                        "presence_status": "offline",
                        "presence_label": "offline",
                        "device_type": Value::Null,
                        "device_types": [],
                    })
                }
            }
        };
        if let Some(object) = snapshot.as_object_mut() {
            object.insert("user_id".to_owned(), Value::String(subject_id.to_owned()));
            object.insert("visibility".to_owned(), Value::String(if blocked { "hidden" } else { "public" }.to_owned()));
        }
        let Ok(message) = event_message("presence.updated", snapshot) else { continue; };
        let audiences = vec![AudienceKey { kind: AudienceKind::User, identifier: recipient_id.to_owned() }];
        state.registry.fanout_low(&audiences, message.clone(), None, None);
        nats_core::publish_after_local(state, audiences, message, EphemeralPriority::Low, None, None).await;
    }
}

fn user_audience(user_id: i64) -> AudienceKey {
    AudienceKey { kind: AudienceKind::User, identifier: user_id.to_string() }
}

fn conversation_audience(conversation_id: Uuid) -> AudienceKey {
    AudienceKey { kind: AudienceKind::Conversation, identifier: conversation_id.to_string() }
}

async fn resolve_actor_id(tx: &mut Transaction<'_, Postgres>, identity: &CommandIdentity) -> Result<i64> {
    if let Some(user_id) = identity.claimed_user_id {
        if let Some(id) = sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE id=$1 AND is_active=TRUE")
            .bind(user_id).persistent(false).fetch_optional(&mut **tx).await? {
            return Ok(id);
        }
    }
    if !identity.email.is_empty() {
        if let Some(id) = sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE LOWER(email)=$1 AND is_active=TRUE LIMIT 1")
            .bind(&identity.email).persistent(false).fetch_optional(&mut **tx).await? {
            return Ok(id);
        }
    }
    anyhow::bail!("authenticated user does not exist locally")
}

async fn active_membership(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    actor_id: i64,
) -> Result<(String, String, bool)> {
    let row = sqlx::query_as::<_, (String, String, bool)>(
        "SELECT c.type, cp.role, cp.is_blocked FROM chat_conversation c JOIN chat_conversationparticipant cp ON cp.conversation_id=c.id WHERE c.id=$1 AND c.is_active=TRUE AND cp.user_id=$2 AND cp.left_at IS NULL AND cp.banned_at IS NULL FOR UPDATE OF c,cp",
    )
    .bind(conversation_id).bind(actor_id).persistent(false).fetch_optional(&mut **tx).await?;
    let Some(row) = row else { anyhow::bail!("conversation not found or participant not found"); };
    if row.2 { return Err(forbidden("participant_blocked", "Your participation in this conversation is restricted.")); }
    Ok(row)
}

async fn ensure_admin(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    actor_id: i64,
) -> Result<String> {
    let (kind, role, _) = active_membership(tx, conversation_id, actor_id).await?;
    if kind != "group" { return Err(validation("group_required", "Participant management is supported only for group conversations.")); }
    if role != "admin" && role != "owner" { return Err(forbidden("group_admin_required", "Only group admins can manage participants.")); }
    Ok(role)
}

async fn ensure_owner(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    actor_id: i64,
) -> Result<()> {
    let (kind, role, _) = active_membership(tx, conversation_id, actor_id).await?;
    if kind != "group" { return Err(validation("group_required", "Ownership management is supported only for group conversations.")); }
    if role != "owner" { return Err(forbidden("group_owner_required", "Only the group owner can perform this action.")); }
    Ok(())
}

async fn has_block_relationship(
    tx: &mut Transaction<'_, Postgres>,
    user_a: i64,
    user_b: i64,
) -> Result<bool> {
    sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_userblock WHERE (blocker_id=$1 AND blocked_id=$2) OR (blocker_id=$2 AND blocked_id=$1))")
        .bind(user_a).bind(user_b).persistent(false).fetch_one(&mut **tx).await.map_err(Into::into)
}

async fn audit(
    tx: &mut Transaction<'_, Postgres>,
    event_type: &str,
    actor_id: i64,
    conversation_id: Option<Uuid>,
    metadata: Value,
) -> Result<()> {
    sqlx::query("INSERT INTO chat_chatauditlog (id,created_at,updated_at,actor_id,conversation_id,message_id,event_type,metadata) VALUES ($1,NOW(),NOW(),$2,$3,NULL,$4,$5)")
        .bind(Uuid::new_v4()).bind(actor_id).bind(conversation_id).bind(event_type).bind(metadata)
        .persistent(false).execute(&mut **tx).await?;
    Ok(())
}

async fn record_event(
    tx: &mut Transaction<'_, Postgres>,
    name: &str,
    data: Value,
    audiences: Vec<AudienceKey>,
) -> Result<CommittedEvent> {
    let event_id = Uuid::new_v4();
    let payload = json!({
        "type": "chat.event",
        "version": 1,
        "event": name,
        "event_id": event_id.to_string(),
        "occurred_at": data.get("updated_at").cloned().unwrap_or(Value::Null),
        "data": data,
    });
    let audience_json = Value::Array(audiences.iter().map(|audience| json!({
        "kind": audience.kind.to_string(),
        "id": audience.identifier.clone(),
    })).collect());
    sqlx::query("INSERT INTO common_realtimeoutboxevent (id,created_at,updated_at,event_id,event_name,payload,audiences,status,attempts,available_at,published_at,delivery_target,published_transport,stream_entry_id,last_error) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,'pending',0,NOW(),NULL,'nats_jetstream','','','')")
        .bind(Uuid::new_v4()).bind(event_id).bind(name).bind(&payload).bind(&audience_json)
        .persistent(false).execute(&mut **tx).await?;
    Ok(CommittedEvent { event_id, event_name: name.to_owned(), payload, audiences })
}

async fn mark_rekey(tx: &mut Transaction<'_, Postgres>, conversation_id: Uuid) -> Result<()> {
    sqlx::query("UPDATE chat_conversation SET e2ee_rekey_required=TRUE,e2ee_last_security_event_at=NOW(),updated_at=NOW() WHERE id=$1")
        .bind(conversation_id).persistent(false).execute(&mut **tx).await?;
    Ok(())
}

fn empty_draft(conversation_id: Uuid) -> Value {
    json!({
        "id": Value::Null,
        "conversation": conversation_id.to_string(),
        "text": "",
        "reply_to": Value::Null,
        "metadata": {},
        "has_draft": false,
        "created_at": Value::Null,
        "updated_at": Value::Null,
    })
}

async fn draft_payload(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    user_id: i64,
) -> Result<Value> {
    let payload = sqlx::query_scalar::<_, Value>(r#"
        SELECT jsonb_build_object(
            'id', d.id::text,
            'conversation', d.conversation_id::text,
            'text', d.text,
            'reply_to', CASE WHEN m.id IS NULL THEN NULL ELSE jsonb_build_object(
                'id', m.id::text,
                'text', m.text,
                'type', m.type,
                'sender', CASE WHEN u.id IS NULL THEN NULL ELSE jsonb_build_object(
                    'id', u.id::text,
                    'username', u.username,
                    'email', u.email,
                    'display_name', COALESCE(NULLIF(p.display_name,''),NULLIF(BTRIM(CONCAT_WS(' ',u.first_name,u.last_name)),''),u.username),
                    'avatar', CASE WHEN COALESCE(p.avatar,'')='' THEN NULL ELSE '/media/'||p.avatar END
                ) END,
                'is_deleted', m.is_deleted,
                'created_at', m.created_at
            ) END,
            'metadata', COALESCE(d.metadata,'{}'::jsonb),
            'has_draft', true,
            'created_at', d.created_at,
            'updated_at', d.updated_at
        )
        FROM chat_conversationdraft d
        LEFT JOIN chat_message m ON m.id=d.reply_to_id
        LEFT JOIN accounts_user u ON u.id=m.sender_id
        LEFT JOIN accounts_profile p ON p.user_id=u.id
        WHERE d.conversation_id=$1 AND d.user_id=$2
    "#)
        .bind(conversation_id).bind(user_id).persistent(false).fetch_optional(&mut **tx).await?;
    Ok(payload.unwrap_or_else(|| empty_draft(conversation_id)))
}

async fn e2ee_enabled(tx: &mut Transaction<'_, Postgres>, conversation_id: Uuid) -> Result<bool> {
    sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_conversationparticipant cp JOIN chat_usere2eedevicekey k ON k.user_id=cp.user_id AND k.is_active=TRUE WHERE cp.conversation_id=$1 AND cp.left_at IS NULL AND cp.banned_at IS NULL)")
        .bind(conversation_id).persistent(false).fetch_one(&mut **tx).await.map_err(Into::into)
}

impl Database {
    pub(crate) async fn create_conversation(&self, identity: &CommandIdentity, input: CreateConversationRequest) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let kind = input.conversation_type.trim().to_ascii_lowercase();
        let mut participant_ids = Vec::new();
        let mut seen = HashSet::new();
        for id in input.participant_ids {
            if seen.insert(id) { participant_ids.push(id); } else { return Err(validation("duplicate_participants", "Choose each participant only once.")); }
        }
        if participant_ids.is_empty() { return Err(validation("participant_ids", "Choose at least one participant.")); }
        let (conversation_id, created) = if kind == "direct" {
            if participant_ids.len() != 1 { return Err(validation("participant_ids", "Direct conversation requires exactly one other user.")); }
            let target_id = participant_ids[0];
            if target_id == actor_id { return Err(validation("participant_ids", "You cannot start a direct conversation with yourself.")); }
            let target_exists = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM accounts_user WHERE id=$1 AND is_active=TRUE)")
                .bind(target_id).persistent(false).fetch_one(&mut *tx).await?;
            if !target_exists { return Err(validation("participant_ids", "User does not exist.")); }
            if has_block_relationship(&mut tx, actor_id, target_id).await? { return Err(forbidden("direct_conversation_blocked", "Direct conversation is blocked between these users.")); }
            let direct_key = direct_key_for_users(actor_id, target_id);
            sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
                .bind(format!("chat-direct:{direct_key}"))
                .persistent(false)
                .execute(&mut *tx)
                .await?;
            if let Some(existing) = sqlx::query_scalar::<_, Uuid>("SELECT id FROM chat_conversation WHERE direct_key=$1 LIMIT 1 FOR UPDATE")
                .bind(&direct_key).persistent(false).fetch_optional(&mut *tx).await? {
                (existing, false)
            } else {
                let id = Uuid::new_v4();
                sqlx::query("INSERT INTO chat_conversation (id,created_at,updated_at,type,title,slug,avatar,created_by_id,is_active,direct_key,last_message_id,last_message_at,e2ee_key_version,e2ee_rekey_required,e2ee_last_key_rotation_at,e2ee_last_security_event_at,next_message_sequence) VALUES ($1,NOW(),NOW(),'direct','',NULL,'',$2,TRUE,$3,NULL,NULL,1,FALSE,NULL,NULL,0)")
                    .bind(id).bind(actor_id).bind(&direct_key).persistent(false).execute(&mut *tx).await?;
                for (user_id, role) in [(actor_id, "owner"), (target_id, "member")] {
                    sqlx::query("INSERT INTO chat_conversationparticipant (id,created_at,updated_at,conversation_id,user_id,role,joined_at,left_at,is_muted,is_archived,is_pinned,is_blocked,last_read_message_id,last_read_at,last_delivered_message_id,last_delivered_at,moderation_muted_until,banned_at,banned_by_id,ban_reason) VALUES ($1,NOW(),NOW(),$2,$3,$4,NOW(),NULL,FALSE,FALSE,FALSE,FALSE,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'')")
                        .bind(Uuid::new_v4()).bind(id).bind(user_id).bind(role).persistent(false).execute(&mut *tx).await?;
                }
                (id, true)
            }
        } else if kind == "group" {
            let title = sanitize_text(&input.title, 100, false);
            if title.chars().count() < 2 || !title.chars().any(|ch| ch.is_alphanumeric()) {
                return Err(validation("title", "Use at least two characters and include a letter or number in the group title."));
            }
            participant_ids.retain(|id| *id != actor_id);
            let valid_count = sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM accounts_user WHERE id=ANY($1) AND is_active=TRUE")
                .bind(&participant_ids).persistent(false).fetch_one(&mut *tx).await?;
            if valid_count != participant_ids.len() as i64 { return Err(validation("participant_ids", "One or more users do not exist.")); }
            for target_id in &participant_ids {
                if has_block_relationship(&mut tx, actor_id, *target_id).await? { return Err(forbidden("blocked_participant", "Group creation is blocked for one or more selected users.")); }
            }
            let requested_slug = !input.slug.trim().is_empty();
            let base = normalize_slug(if requested_slug { &input.slug } else { &title });
            if requested_slug && base.chars().count() < 3 { return Err(validation("slug", "Use at least three letters or numbers.")); }
            let mut slug = if base.is_empty() { format!("group-{}", &Uuid::new_v4().simple().to_string()[..8]) } else { base.clone() };
            sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
                .bind(format!("chat-group-slug:{slug}"))
                .persistent(false)
                .execute(&mut *tx)
                .await?;
            let mut suffix = 2;
            loop {
                let unavailable = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_conversation WHERE LOWER(slug)=LOWER($1)) OR EXISTS(SELECT 1 FROM accounts_user WHERE LOWER(username)=LOWER($1))")
                    .bind(&slug).persistent(false).fetch_one(&mut *tx).await?;
                if !unavailable { break; }
                if requested_slug { return Err(validation("slug", "This unique group name is already in use.")); }
                let tail = format!("-{suffix}");
                let keep = 80usize.saturating_sub(tail.chars().count());
                slug = format!("{}{}", base.chars().take(keep).collect::<String>(), tail);
                suffix += 1;
                if suffix > 50 { return Err(validation("slug", "A unique group name could not be generated.")); }
            }
            let id = Uuid::new_v4();
            sqlx::query("INSERT INTO chat_conversation (id,created_at,updated_at,type,title,slug,avatar,created_by_id,is_active,direct_key,last_message_id,last_message_at,e2ee_key_version,e2ee_rekey_required,e2ee_last_key_rotation_at,e2ee_last_security_event_at,next_message_sequence) VALUES ($1,NOW(),NOW(),'group',$2,$3,'',$4,TRUE,NULL,NULL,NULL,1,FALSE,NULL,NULL,0)")
                .bind(id).bind(&title).bind(&slug).bind(actor_id).persistent(false).execute(&mut *tx).await?;
            let mut all_users = vec![actor_id]; all_users.extend(participant_ids.iter().copied());
            for user_id in &all_users {
                let role = if *user_id == actor_id { "owner" } else { "member" };
                sqlx::query("INSERT INTO chat_conversationparticipant (id,created_at,updated_at,conversation_id,user_id,role,joined_at,left_at,is_muted,is_archived,is_pinned,is_blocked,last_read_message_id,last_read_at,last_delivered_message_id,last_delivered_at,moderation_muted_until,banned_at,banned_by_id,ban_reason) VALUES ($1,NOW(),NOW(),$2,$3,$4,NOW(),NULL,FALSE,FALSE,FALSE,FALSE,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'')")
                    .bind(Uuid::new_v4()).bind(id).bind(*user_id).bind(role).persistent(false).execute(&mut *tx).await?;
            }
            (id, true)
        } else {
            return Err(validation("type", "Conversation type must be direct or group."));
        };
        let mut events = Vec::new();
        if created {
            audit(&mut tx, "participants_added", actor_id, Some(conversation_id), json!({"created": true})).await?;
            let user_ids = sqlx::query_scalar::<_, i64>("SELECT user_id FROM chat_conversationparticipant WHERE conversation_id=$1 AND left_at IS NULL")
                .bind(conversation_id).persistent(false).fetch_all(&mut *tx).await?;
            events.push(record_event(&mut tx, "conversation.created", json!({"conversation_id": conversation_id.to_string()}), user_ids.into_iter().map(user_audience).collect()).await?);
        }
        tx.commit().await?;
        let payload = self.get_chat_conversation(actor_id, conversation_id).await?.context("conversation not found after creation")?;
        Ok(ConversationCommandResult { payload, events })
    }

    async fn toggle_viewer_state(&self, conversation_id: Uuid, identity: &CommandIdentity, field: &str) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        active_membership(&mut tx, conversation_id, actor_id).await?;
        let sql = match field {
            "is_muted" => "UPDATE chat_conversationparticipant SET is_muted=NOT is_muted,updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2 RETURNING is_muted",
            "is_archived" => "UPDATE chat_conversationparticipant SET is_archived=NOT is_archived,updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2 RETURNING is_archived",
            "is_pinned" => "UPDATE chat_conversationparticipant SET is_pinned=NOT is_pinned,updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2 RETURNING is_pinned",
            _ => anyhow::bail!("unsupported viewer state"),
        };
        let value = sqlx::query_scalar::<_, bool>(sql).bind(conversation_id).bind(actor_id).persistent(false).fetch_one(&mut *tx).await?;
        if field == "is_archived" && value {
            let retained = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_conversationparticipant WHERE conversation_id=$1 AND left_at IS NULL AND is_archived=FALSE)")
                .bind(conversation_id).persistent(false).fetch_one(&mut *tx).await?;
            if !retained { schedule_conversation_cleanup(&mut tx, conversation_id, "final_retained_copy_archived").await?; }
        }
        let mut payload_object = serde_json::Map::new();
        payload_object.insert("conversation_id".to_owned(), Value::String(conversation_id.to_string()));
        payload_object.insert(field.to_owned(), Value::Bool(value));
        let payload = Value::Object(payload_object);
        let event = record_event(&mut tx, "conversation.viewer_state_updated", payload.clone(), vec![user_audience(actor_id)]).await?;
        tx.commit().await?;
        Ok(ConversationCommandResult { payload, events: vec![event] })
    }

    pub(crate) async fn toggle_conversation_mute(&self, conversation_id: Uuid, identity: &CommandIdentity) -> Result<ConversationCommandResult> { self.toggle_viewer_state(conversation_id, identity, "is_muted").await }
    pub(crate) async fn toggle_conversation_archive(&self, conversation_id: Uuid, identity: &CommandIdentity) -> Result<ConversationCommandResult> { self.toggle_viewer_state(conversation_id, identity, "is_archived").await }
    pub(crate) async fn toggle_conversation_pin(&self, conversation_id: Uuid, identity: &CommandIdentity) -> Result<ConversationCommandResult> { self.toggle_viewer_state(conversation_id, identity, "is_pinned").await }

    pub(crate) async fn get_conversation_draft(&self, conversation_id: Uuid, identity: &CommandIdentity) -> Result<Value> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        active_membership(&mut tx, conversation_id, actor_id).await?;
        let payload = if e2ee_enabled(&mut tx, conversation_id).await? { empty_draft(conversation_id) } else { draft_payload(&mut tx, conversation_id, actor_id).await? };
        tx.commit().await?;
        Ok(payload)
    }

    pub(crate) async fn save_conversation_draft(&self, conversation_id: Uuid, identity: &CommandIdentity, input: DraftRequest) -> Result<Value> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        active_membership(&mut tx, conversation_id, actor_id).await?;
        let existing = sqlx::query_as::<_, (String, Option<Uuid>, Value)>("SELECT text,reply_to_id,metadata FROM chat_conversationdraft WHERE conversation_id=$1 AND user_id=$2 FOR UPDATE")
            .bind(conversation_id).bind(actor_id).persistent(false).fetch_optional(&mut *tx).await?;
        let text = sanitize_text(input.text.as_deref().unwrap_or(existing.as_ref().map(|row| row.0.as_str()).unwrap_or("")), 10_000, true);
        let reply_to = input.reply_to_id.unwrap_or_else(|| existing.as_ref().map(|row| row.1).unwrap_or(None));
        let metadata = input.metadata.unwrap_or_else(|| existing.as_ref().map(|row| row.2.clone()).unwrap_or_else(|| json!({})));
        if !metadata.is_object() { return Err(validation("metadata", "Draft metadata must be an object.")); }
        if serde_json::to_vec(&metadata)?.len() > 16 * 1024 { return Err(validation("metadata", "Draft metadata is too large.")); }
        if let Some(message_id) = reply_to {
            let valid = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_message WHERE id=$1 AND conversation_id=$2 AND is_deleted=FALSE)")
                .bind(message_id).bind(conversation_id).persistent(false).fetch_one(&mut *tx).await?;
            if !valid { return Err(validation("reply_to_id", "Reply target must belong to this conversation.")); }
        }
        if e2ee_enabled(&mut tx, conversation_id).await? {
            sqlx::query("DELETE FROM chat_conversationdraft WHERE conversation_id=$1 AND user_id=$2").bind(conversation_id).bind(actor_id).persistent(false).execute(&mut *tx).await?;
            if text.is_empty() && reply_to.is_none() && metadata.as_object().is_some_and(|object| object.is_empty()) {
                tx.commit().await?; return Ok(empty_draft(conversation_id));
            }
            return Err(validation("e2ee_local_drafts_only", "Server-side drafts are disabled for end-to-end encrypted conversations. Keep drafts on this device."));
        }
        if text.is_empty() && reply_to.is_none() && metadata.as_object().is_some_and(|object| object.is_empty()) {
            sqlx::query("DELETE FROM chat_conversationdraft WHERE conversation_id=$1 AND user_id=$2").bind(conversation_id).bind(actor_id).persistent(false).execute(&mut *tx).await?;
            tx.commit().await?; return Ok(empty_draft(conversation_id));
        }
        sqlx::query("INSERT INTO chat_conversationdraft (id,created_at,updated_at,conversation_id,user_id,text,reply_to_id,metadata) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,$6) ON CONFLICT (conversation_id,user_id) DO UPDATE SET text=EXCLUDED.text,reply_to_id=EXCLUDED.reply_to_id,metadata=EXCLUDED.metadata,updated_at=NOW()")
            .bind(Uuid::new_v4()).bind(conversation_id).bind(actor_id).bind(&text).bind(reply_to).bind(&metadata).persistent(false).execute(&mut *tx).await?;
        let payload = draft_payload(&mut tx, conversation_id, actor_id).await?;
        tx.commit().await?;
        Ok(payload)
    }

    pub(crate) async fn delete_conversation_draft(&self, conversation_id: Uuid, identity: &CommandIdentity) -> Result<Value> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        active_membership(&mut tx, conversation_id, actor_id).await?;
        sqlx::query("DELETE FROM chat_conversationdraft WHERE conversation_id=$1 AND user_id=$2").bind(conversation_id).bind(actor_id).persistent(false).execute(&mut *tx).await?;
        tx.commit().await?;
        Ok(empty_draft(conversation_id))
    }

    pub(crate) async fn add_conversation_participants(&self, conversation_id: Uuid, identity: &CommandIdentity, participant_ids: Vec<i64>) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        ensure_admin(&mut tx, conversation_id, actor_id).await?;
        let mut unique = Vec::new(); let mut seen = HashSet::new();
        for id in participant_ids { if seen.insert(id) { unique.push(id); } else { return Err(validation("participant_ids", "Choose each participant only once.")); } }
        if unique.is_empty() { return Err(validation("participant_ids", "Choose at least one participant.")); }
        let valid_count = sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM accounts_user WHERE id=ANY($1) AND is_active=TRUE").bind(&unique).persistent(false).fetch_one(&mut *tx).await?;
        if valid_count != unique.len() as i64 { return Err(validation("participant_ids", "One or more selected users are unavailable.")); }
        let existing = sqlx::query_as::<_, (i64, bool)>("SELECT user_id,(left_at IS NULL AND banned_at IS NULL) AS is_active FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id=ANY($2) FOR UPDATE")
            .bind(conversation_id).bind(&unique).persistent(false).fetch_all(&mut *tx).await?;
        let existing: std::collections::HashMap<i64, bool> = existing.into_iter().collect();
        let mut added = Vec::new();
        for user_id in unique {
            if has_block_relationship(&mut tx, actor_id, user_id).await? { return Err(forbidden("blocked_participant", "One or more selected users cannot be added.")); }
            if let Some(is_active) = existing.get(&user_id) {
                if *is_active { continue; }
                return Err(validation("participant_ids", "A selected user previously left or was banned. Use the appropriate restore or unban action."));
            }
            sqlx::query("INSERT INTO chat_conversationparticipant (id,created_at,updated_at,conversation_id,user_id,role,joined_at,left_at,is_muted,is_archived,is_pinned,is_blocked,last_read_message_id,last_read_at,last_delivered_message_id,last_delivered_at,moderation_muted_until,banned_at,banned_by_id,ban_reason) VALUES ($1,NOW(),NOW(),$2,$3,'member',NOW(),NULL,FALSE,FALSE,FALSE,FALSE,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'')")
                .bind(Uuid::new_v4()).bind(conversation_id).bind(user_id).persistent(false).execute(&mut *tx).await?;
            added.push(user_id);
        }
        let mut events = Vec::new();
        if !added.is_empty() {
            mark_rekey(&mut tx, conversation_id).await?;
            audit(&mut tx, "participants_added", actor_id, Some(conversation_id), json!({"participant_ids": added})).await?;
            let mut audiences = vec![conversation_audience(conversation_id)]; audiences.extend(added.iter().copied().map(user_audience));
            events.push(record_event(&mut tx, "conversation.participants_added", json!({"conversation_id": conversation_id.to_string(), "added_user_ids": added}), audiences).await?);
        }
        tx.commit().await?;
        let payload = self.get_chat_conversation(actor_id, conversation_id).await?.context("conversation not found after participant update")?;
        Ok(ConversationCommandResult { payload, events })
    }

    pub(crate) async fn remove_conversation_participant(&self, conversation_id: Uuid, identity: &CommandIdentity, user_id: i64) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?; let actor_role = ensure_admin(&mut tx, conversation_id, actor_id).await?;
        let target = sqlx::query_as::<_, (String,)>("SELECT role FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL FOR UPDATE")
            .bind(conversation_id).bind(user_id).persistent(false).fetch_optional(&mut *tx).await?.ok_or_else(|| validation("participant", "Participant not found."))?;
        if target.0 == "owner" { return Err(validation("participant", "Owner cannot be removed.")); }
        if target.0 == "admin" && actor_role != "owner" { return Err(forbidden("owner_required", "Only the owner can remove another admin.")); }
        let retained_after = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id<>$2 AND left_at IS NULL AND is_archived=FALSE)")
            .bind(conversation_id).bind(user_id).persistent(false).fetch_one(&mut *tx).await?;
        sqlx::query("UPDATE chat_conversationparticipant SET left_at=NOW(),updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2").bind(conversation_id).bind(user_id).persistent(false).execute(&mut *tx).await?;
        if !retained_after { schedule_conversation_cleanup(&mut tx, conversation_id, "final_retained_participant_removed").await?; }
        mark_rekey(&mut tx, conversation_id).await?; audit(&mut tx, "participant_removed", actor_id, Some(conversation_id), json!({"user_id": user_id.to_string()})).await?;
        let payload = json!({"conversation_id": conversation_id.to_string(), "removed_user_id": user_id.to_string()});
        let event = record_event(&mut tx, "conversation.participant_removed", payload.clone(), vec![conversation_audience(conversation_id), user_audience(user_id)]).await?;
        tx.commit().await?; Ok(ConversationCommandResult { payload, events: vec![event] })
    }

    pub(crate) async fn update_conversation_participant_role(&self, conversation_id: Uuid, identity: &CommandIdentity, user_id: i64, role: &str) -> Result<ConversationCommandResult> {
        let role = role.trim().to_ascii_lowercase(); if role != "member" && role != "admin" { return Err(validation("role", "Only member/admin roles are assignable here.")); }
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?; ensure_owner(&mut tx, conversation_id, actor_id).await?;
        let old_role = sqlx::query_scalar::<_, String>("SELECT role FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL FOR UPDATE")
            .bind(conversation_id).bind(user_id).persistent(false).fetch_optional(&mut *tx).await?.ok_or_else(|| validation("participant", "Participant not found."))?;
        if old_role == "owner" { return Err(validation("participant", "Owner role cannot be changed here.")); }
        let mut events = Vec::new();
        if old_role != role {
            sqlx::query("UPDATE chat_conversationparticipant SET role=$3,updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2").bind(conversation_id).bind(user_id).bind(&role).persistent(false).execute(&mut *tx).await?;
            audit(&mut tx, "role_changed", actor_id, Some(conversation_id), json!({"user_id": user_id.to_string(), "from": old_role, "to": role})).await?;
            events.push(record_event(&mut tx, "conversation.participant_role_updated", json!({"conversation_id": conversation_id.to_string(), "user_id": user_id.to_string(), "role": role}), vec![conversation_audience(conversation_id)]).await?);
        }
        tx.commit().await?; Ok(ConversationCommandResult { payload: json!({"conversation_id": conversation_id.to_string(), "user_id": user_id.to_string(), "role": role}), events })
    }

    pub(crate) async fn mute_conversation_participant(&self, conversation_id: Uuid, identity: &CommandIdentity, user_id: i64, minutes: i64) -> Result<ConversationCommandResult> {
        if !(1..=43_200).contains(&minutes) { return Err(validation("minutes", "Mute duration must be between 1 and 43200 minutes.")); }
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?; ensure_admin(&mut tx, conversation_id, actor_id).await?;
        let target_role = sqlx::query_scalar::<_, String>("SELECT role FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL FOR UPDATE")
            .bind(conversation_id).bind(user_id).persistent(false).fetch_optional(&mut *tx).await?.ok_or_else(|| validation("user_id", "Participant not found."))?;
        if target_role == "owner" { return Err(forbidden("owner_cannot_be_muted", "Group owner cannot be muted.")); }
        let until = sqlx::query_scalar::<_, String>("UPDATE chat_conversationparticipant SET moderation_muted_until=NOW()+($3*INTERVAL '1 minute'),updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2 RETURNING to_char(moderation_muted_until AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
            .bind(conversation_id).bind(user_id).bind(minutes).persistent(false).fetch_one(&mut *tx).await?;
        audit(&mut tx, "participant_muted", actor_id, Some(conversation_id), json!({"target_user_id": user_id.to_string(), "minutes": minutes})).await?;
        let payload = json!({"conversation_id": conversation_id.to_string(), "user_id": user_id.to_string(), "moderation_muted_until": until});
        let event = record_event(&mut tx, "conversation.participant_muted", payload.clone(), vec![conversation_audience(conversation_id)]).await?;
        tx.commit().await?; Ok(ConversationCommandResult { payload, events: vec![event] })
    }

    pub(crate) async fn ban_conversation_participant(&self, conversation_id: Uuid, identity: &CommandIdentity, user_id: i64, reason: &str) -> Result<ConversationCommandResult> {
        let reason = sanitize_text(reason, 255, false); let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?; let actor_role = ensure_admin(&mut tx, conversation_id, actor_id).await?;
        let target_role = sqlx::query_scalar::<_, String>("SELECT role FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL FOR UPDATE")
            .bind(conversation_id).bind(user_id).persistent(false).fetch_optional(&mut *tx).await?.ok_or_else(|| validation("user_id", "Participant not found."))?;
        if target_role == "owner" { return Err(forbidden("owner_cannot_be_banned", "Group owner cannot be banned.")); }
        if target_role == "admin" && actor_role != "owner" { return Err(forbidden("owner_required", "Only the owner can prevent another admin from rejoining.")); }
        let retained_after = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id<>$2 AND left_at IS NULL AND is_archived=FALSE)")
            .bind(conversation_id).bind(user_id).persistent(false).fetch_one(&mut *tx).await?;
        let banned_at = sqlx::query_scalar::<_, String>("UPDATE chat_conversationparticipant SET banned_at=NOW(),banned_by_id=$3,ban_reason=$4,left_at=NOW(),updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2 RETURNING to_char(banned_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
            .bind(conversation_id).bind(user_id).bind(actor_id).bind(&reason).persistent(false).fetch_one(&mut *tx).await?;
        if !retained_after { schedule_conversation_cleanup(&mut tx, conversation_id, "final_retained_participant_banned").await?; }
        mark_rekey(&mut tx, conversation_id).await?; audit(&mut tx, "participant_banned", actor_id, Some(conversation_id), json!({"target_user_id": user_id.to_string(), "reason": reason})).await?;
        let payload = json!({"conversation_id": conversation_id.to_string(), "user_id": user_id.to_string(), "banned_at": banned_at, "ban_reason": reason});
        let event = record_event(&mut tx, "conversation.participant_banned", payload.clone(), vec![conversation_audience(conversation_id), user_audience(user_id)]).await?;
        tx.commit().await?; Ok(ConversationCommandResult { payload, events: vec![event] })
    }

    pub(crate) async fn unban_conversation_participant(&self, conversation_id: Uuid, identity: &CommandIdentity, user_id: i64) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?; ensure_admin(&mut tx, conversation_id, actor_id).await?;
        let found = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id=$2)").bind(conversation_id).bind(user_id).persistent(false).fetch_one(&mut *tx).await?;
        if !found { return Err(validation("user_id", "Participant not found.")); }
        if has_block_relationship(&mut tx, actor_id, user_id).await? { return Err(forbidden("blocked_participant", "This participant cannot be re-added while a block relationship exists.")); }
        sqlx::query("UPDATE chat_conversationparticipant SET banned_at=NULL,banned_by_id=NULL,ban_reason='',left_at=NULL,updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2").bind(conversation_id).bind(user_id).persistent(false).execute(&mut *tx).await?;
        mark_rekey(&mut tx, conversation_id).await?; audit(&mut tx, "participant_unbanned", actor_id, Some(conversation_id), json!({"target_user_id": user_id.to_string()})).await?;
        let payload = json!({"conversation_id": conversation_id.to_string(), "user_id": user_id.to_string(), "unbanned": true});
        let event = record_event(&mut tx, "conversation.participant_unbanned", payload.clone(), vec![conversation_audience(conversation_id), user_audience(user_id)]).await?;
        tx.commit().await?; Ok(ConversationCommandResult { payload, events: vec![event] })
    }

    pub(crate) async fn transfer_conversation_ownership(&self, conversation_id: Uuid, identity: &CommandIdentity, target_user_id: i64) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?; ensure_owner(&mut tx, conversation_id, actor_id).await?;
        if actor_id == target_user_id { return Err(validation("participant", "You already own this conversation.")); }
        let target_exists = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL AND banned_at IS NULL)")
            .bind(conversation_id).bind(target_user_id).persistent(false).fetch_one(&mut *tx).await?;
        if !target_exists { return Err(validation("participant", "Target participant not found.")); }
        sqlx::query("UPDATE chat_conversationparticipant SET role=CASE WHEN user_id=$2 THEN 'admin' WHEN user_id=$3 THEN 'owner' ELSE role END,updated_at=NOW() WHERE conversation_id=$1 AND user_id IN ($2,$3)")
            .bind(conversation_id).bind(actor_id).bind(target_user_id).persistent(false).execute(&mut *tx).await?;
        audit(&mut tx, "ownership_transferred", actor_id, Some(conversation_id), json!({"from_user_id": actor_id.to_string(), "to_user_id": target_user_id.to_string()})).await?;
        let payload = json!({"conversation_id": conversation_id.to_string(), "owner_user_id": target_user_id.to_string()});
        let event = record_event(&mut tx, "conversation.ownership_transferred", payload.clone(), vec![conversation_audience(conversation_id)]).await?;
        tx.commit().await?; Ok(ConversationCommandResult { payload, events: vec![event] })
    }

    pub(crate) async fn leave_chat_conversation(&self, conversation_id: Uuid, identity: &CommandIdentity) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?; let (_, role, _) = active_membership(&mut tx, conversation_id, actor_id).await?;
        let active_count = sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM chat_conversationparticipant WHERE conversation_id=$1 AND left_at IS NULL").bind(conversation_id).persistent(false).fetch_one(&mut *tx).await?;
        if role == "owner" && active_count > 1 { return Err(validation("conversation", "Owner cannot leave until ownership is transferred or others are removed.")); }
        let retained_after = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_conversationparticipant WHERE conversation_id=$1 AND user_id<>$2 AND left_at IS NULL AND is_archived=FALSE)")
            .bind(conversation_id).bind(actor_id).persistent(false).fetch_one(&mut *tx).await?;
        let left_at = sqlx::query_scalar::<_, String>("UPDATE chat_conversationparticipant SET left_at=NOW(),updated_at=NOW() WHERE conversation_id=$1 AND user_id=$2 RETURNING to_char(left_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
            .bind(conversation_id).bind(actor_id).persistent(false).fetch_one(&mut *tx).await?;
        if !retained_after { schedule_conversation_cleanup(&mut tx, conversation_id, "final_retained_participant_left").await?; }
        mark_rekey(&mut tx, conversation_id).await?;
        let payload = json!({"conversation_id": conversation_id.to_string(), "user_id": actor_id.to_string(), "left_at": left_at});
        let event = record_event(&mut tx, "conversation.participant_left", payload.clone(), vec![conversation_audience(conversation_id), user_audience(actor_id)]).await?;
        tx.commit().await?; Ok(ConversationCommandResult { payload, events: vec![event] })
    }

    pub(crate) async fn block_chat_user(&self, identity: &CommandIdentity, target_user_id: i64, reason: &str) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        if actor_id == target_user_id { return Err(validation("blocked_user_id", "You cannot block yourself.")); }
        let target_exists = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM accounts_user WHERE id=$1 AND is_active=TRUE)").bind(target_user_id).persistent(false).fetch_one(&mut *tx).await?;
        if !target_exists { return Err(validation("blocked_user_id", "User does not exist.")); }
        let requested_reason = sanitize_text(reason, 255, false); let block_id = Uuid::new_v4();
        let (actual_id, actual_reason, created_at) = sqlx::query_as::<_, (Uuid, String, String)>("INSERT INTO chat_userblock (id,created_at,updated_at,blocker_id,blocked_id,reason) VALUES ($1,NOW(),NOW(),$2,$3,$4) ON CONFLICT (blocker_id,blocked_id) DO UPDATE SET updated_at=chat_userblock.updated_at RETURNING id,reason,to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
            .bind(block_id).bind(actor_id).bind(target_user_id).bind(&requested_reason).persistent(false).fetch_one(&mut *tx).await?;
        audit(&mut tx, "user_blocked", actor_id, None, json!({"blocked_user_id": target_user_id.to_string(), "reason": actual_reason.clone()})).await?;
        let payload = sqlx::query_scalar::<_, Value>("SELECT jsonb_build_object('id',$1::text,'blocked',jsonb_build_object('id',u.id::text,'username',u.username,'email',u.email,'display_name',COALESCE(NULLIF(p.display_name,''),NULLIF(BTRIM(CONCAT_WS(' ',u.first_name,u.last_name)),''),u.username),'avatar',CASE WHEN COALESCE(p.avatar,'')='' THEN NULL ELSE '/media/'||p.avatar END),'reason',$2,'created_at',$3) FROM accounts_user u LEFT JOIN accounts_profile p ON p.user_id=u.id WHERE u.id=$4")
            .bind(actual_id).bind(&actual_reason).bind(&created_at).bind(target_user_id).persistent(false).fetch_one(&mut *tx).await?;
        let event = record_event(&mut tx, "user.blocked", json!({"user_id": actor_id.to_string(), "blocked_user_id": target_user_id.to_string(), "pair_blocked": true}), vec![user_audience(actor_id)]).await?;
        tx.commit().await?; Ok(ConversationCommandResult { payload, events: vec![event] })
    }

    pub(crate) async fn unblock_chat_user(&self, identity: &CommandIdentity, target_user_id: i64) -> Result<ConversationCommandResult> {
        let pool = self.pool.as_ref().context("SQLx conversation command backend is disabled")?; let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let target_exists = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM accounts_user WHERE id=$1 AND is_active=TRUE)").bind(target_user_id).persistent(false).fetch_one(&mut *tx).await?;
        if !target_exists { return Err(validation("blocked_user_id", "User does not exist.")); }
        sqlx::query("DELETE FROM chat_userblock WHERE blocker_id=$1 AND blocked_id=$2").bind(actor_id).bind(target_user_id).persistent(false).execute(&mut *tx).await?;
        let pair_blocked = has_block_relationship(&mut tx, actor_id, target_user_id).await?;
        audit(&mut tx, "user_unblocked", actor_id, None, json!({"blocked_user_id": target_user_id.to_string()})).await?;
        let event = record_event(&mut tx, "user.unblocked", json!({"user_id": actor_id.to_string(), "blocked_user_id": target_user_id.to_string(), "pair_blocked": pair_blocked}), vec![user_audience(actor_id)]).await?;
        tx.commit().await?; Ok(ConversationCommandResult { payload: Value::Null, events: vec![event] })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn direct_key_matches_django_lexicographic_sorting() {
        assert_eq!(direct_key_for_users(2, 10), "10:2");
        assert_eq!(direct_key_for_users(10, 2), "10:2");
        assert_eq!(direct_key_for_users(2, 3), "2:3");
    }

    #[test]
    fn draft_reply_null_is_distinct_from_missing() {
        let missing: DraftRequest = serde_json::from_value(json!({})).expect("missing draft field");
        assert!(missing.reply_to_id.is_none());

        let cleared: DraftRequest = serde_json::from_value(json!({"reply_to_id": null})).expect("nullable draft field");
        assert_eq!(cleared.reply_to_id, Some(None));
    }

    #[test]
    fn group_slug_normalization_is_bounded_and_stable() {
        assert_eq!(normalize_slug("  Product_Design Team  "), "product-design-team");
        assert!(normalize_slug(&"a".repeat(200)).chars().count() <= 80);
    }
}
