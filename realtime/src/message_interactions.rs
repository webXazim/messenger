use std::sync::Arc;

use anyhow::{Context, Result};
use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::{Postgres, QueryBuilder, Transaction};
use uuid::Uuid;

use crate::{
    command_auth::{CommandAuthError, CommandIdentity},
    command_delivery::deliver_committed,
    commands::error_response,
    config::ChatInteractionBackend,
    database::{CommittedEvent, Database},
    protocol::{AudienceKey, AudienceKind},
    state::AppState,
};

#[derive(Debug, Default, Deserialize)]
pub(crate) struct ReceiptRequest {
    #[serde(default)]
    message_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct ReactionRequest {
    emoji: String,
}

pub(crate) struct InteractionResult {
    pub payload: Value,
    pub events: Vec<CommittedEvent>,
}

fn interactions_enabled(state: &AppState) -> bool {
    matches!(
        state.config.chat_interaction_backend,
        ChatInteractionBackend::SqlxShadow | ChatInteractionBackend::Axum
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

fn optional_uuid(raw: Option<&str>) -> Result<Option<Uuid>, axum::response::Response> {
    let Some(raw) = raw.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    raw.parse::<Uuid>().map(Some).map_err(|_| {
        error_response(
            StatusCode::BAD_REQUEST,
            "invalid_message_id",
            "message_id must be a valid UUID.",
        )
    })
}

fn interaction_error(error: anyhow::Error, operation: &'static str) -> axum::response::Response {
    let message = error.to_string();
    tracing::warn!(error = %error, operation, "Axum message interaction rejected");
    if message.contains("authenticated user does not exist") {
        error_response(
            StatusCode::UNAUTHORIZED,
            "authentication_failed",
            "The authenticated account is not available.",
        )
    } else if message.contains("participant is blocked") {
        error_response(
            StatusCode::FORBIDDEN,
            "participant_blocked",
            "Your participation in this conversation is restricted.",
        )
    } else if message.contains("active participant") || message.contains("conversation is unavailable") {
        error_response(
            StatusCode::NOT_FOUND,
            "conversation_not_found",
            "Conversation was not found.",
        )
    } else if message.contains("message does not belong") {
        error_response(
            StatusCode::BAD_REQUEST,
            "message_not_in_conversation",
            "Message not found in this conversation.",
        )
    } else if message.contains("message was not found") {
        error_response(
            StatusCode::NOT_FOUND,
            "message_not_found",
            "Message was not found.",
        )
    } else {
        tracing::error!(error = %error, operation, "Axum SQLx message interaction failed");
        error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "message_interaction_failed",
            "The message interaction could not be completed.",
        )
    }
}

pub(crate) async fn mark_delivered(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<ReceiptRequest>,
) -> impl IntoResponse {
    if !interactions_enabled(&state) {
        return error_response(
            StatusCode::NOT_FOUND,
            "axum_message_interactions_disabled",
            "Axum message interactions are not active.",
        );
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let message_id = match optional_uuid(input.message_id.as_deref()) {
        Ok(message_id) => message_id,
        Err(response) => return response,
    };
    match state
        .database
        .mark_conversation_delivered(conversation_id, &identity, message_id)
        .await
    {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::OK, Json(result.payload)).into_response()
        }
        Err(error) => interaction_error(error, "mark_delivered"),
    }
}

pub(crate) async fn mark_read(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<ReceiptRequest>,
) -> impl IntoResponse {
    if !interactions_enabled(&state) {
        return error_response(
            StatusCode::NOT_FOUND,
            "axum_message_interactions_disabled",
            "Axum message interactions are not active.",
        );
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let message_id = match optional_uuid(input.message_id.as_deref()) {
        Ok(message_id) => message_id,
        Err(response) => return response,
    };
    match state
        .database
        .mark_conversation_read(conversation_id, &identity, message_id)
        .await
    {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::OK, Json(result.payload)).into_response()
        }
        Err(error) => interaction_error(error, "mark_read"),
    }
}

pub(crate) async fn add_reaction(
    State(state): State<Arc<AppState>>,
    Path(message_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<ReactionRequest>,
) -> impl IntoResponse {
    if !interactions_enabled(&state) {
        return error_response(
            StatusCode::NOT_FOUND,
            "axum_message_interactions_disabled",
            "Axum message interactions are not active.",
        );
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let emoji = input.emoji.trim();
    if emoji.is_empty() || emoji.chars().count() > 32 {
        return error_response(
            StatusCode::BAD_REQUEST,
            "invalid_emoji",
            "Emoji must contain between 1 and 32 characters.",
        );
    }
    match state.database.add_message_reaction(message_id, &identity, emoji).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::OK, Json(result.payload)).into_response()
        }
        Err(error) => interaction_error(error, "add_reaction"),
    }
}

pub(crate) async fn remove_reaction(
    State(state): State<Arc<AppState>>,
    Path(message_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<ReactionRequest>,
) -> impl IntoResponse {
    if !interactions_enabled(&state) {
        return error_response(
            StatusCode::NOT_FOUND,
            "axum_message_interactions_disabled",
            "Axum message interactions are not active.",
        );
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let emoji = input.emoji.trim();
    if emoji.is_empty() || emoji.chars().count() > 32 {
        return error_response(
            StatusCode::BAD_REQUEST,
            "invalid_emoji",
            "Emoji must contain between 1 and 32 characters.",
        );
    }
    match state
        .database
        .remove_message_reaction(message_id, &identity, emoji)
        .await
    {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::OK, Json(result.payload)).into_response()
        }
        Err(error) => interaction_error(error, "remove_reaction"),
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

async fn lock_active_participant(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    actor_id: i64,
) -> Result<Uuid> {
    let participant = sqlx::query_as::<_, (Uuid, bool)>(
        "SELECT cp.id, cp.is_blocked FROM chat_conversationparticipant cp JOIN chat_conversation c ON c.id = cp.conversation_id WHERE cp.conversation_id = $1 AND cp.user_id = $2 AND cp.left_at IS NULL AND cp.banned_at IS NULL AND c.is_active = TRUE FOR UPDATE OF cp",
    )
    .bind(conversation_id)
    .bind(actor_id)
    .persistent(false)
    .fetch_optional(&mut **tx)
    .await?
    .context("actor is not an active participant")?;
    if participant.1 {
        anyhow::bail!("participant is blocked");
    }
    Ok(participant.0)
}

async fn resolve_receipt_target(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    actor_id: i64,
    requested_message_id: Option<Uuid>,
    exclude_actor: bool,
) -> Result<Option<Uuid>> {
    if let Some(message_id) = requested_message_id {
        let target_sequence = sqlx::query_scalar::<_, i64>(
            "SELECT sequence FROM chat_message WHERE id = $1 AND conversation_id = $2",
        )
        .bind(message_id)
        .bind(conversation_id)
        .persistent(false)
        .fetch_optional(&mut **tx)
        .await?;
        let Some(target_sequence) = target_sequence else {
            anyhow::bail!("message does not belong to this conversation");
        };
        if exclude_actor {
            return Ok(sqlx::query_scalar::<_, Uuid>(
                "SELECT id FROM chat_message WHERE conversation_id = $1 AND sender_id IS DISTINCT FROM $2 AND sequence <= $3 ORDER BY sequence DESC, id DESC LIMIT 1",
            )
            .bind(conversation_id)
            .bind(actor_id)
            .bind(target_sequence)
            .persistent(false)
            .fetch_optional(&mut **tx)
            .await?);
        }
        return Ok(Some(message_id));
    }

    if exclude_actor {
        let result = sqlx::query_scalar::<_, Uuid>(
            "SELECT id FROM chat_message WHERE conversation_id = $1 AND sender_id IS DISTINCT FROM $2 ORDER BY sequence DESC, id DESC LIMIT 1",
        )
        .bind(conversation_id)
        .bind(actor_id)
        .persistent(false)
        .fetch_optional(&mut **tx)
        .await?;
        return Ok(result);
    }
    let result = sqlx::query_scalar::<_, Uuid>(
        "SELECT id FROM chat_message WHERE conversation_id = $1 ORDER BY sequence DESC, id DESC LIMIT 1",
    )
    .bind(conversation_id)
    .persistent(false)
    .fetch_optional(&mut **tx)
    .await?;
    Ok(result)
}

async fn advance_delivery_pointer(
    tx: &mut Transaction<'_, Postgres>,
    participant_id: Uuid,
    target_message_id: Uuid,
) -> Result<bool> {
    let result = sqlx::query(
        "UPDATE chat_conversationparticipant cp SET last_delivered_message_id = $2, last_delivered_at = NOW(), updated_at = NOW() WHERE cp.id = $1 AND (cp.last_delivered_message_id IS NULL OR EXISTS (SELECT 1 FROM chat_message target JOIN chat_message current ON current.id = cp.last_delivered_message_id WHERE target.id = $2 AND target.sequence > current.sequence))",
    )
    .bind(participant_id)
    .bind(target_message_id)
    .persistent(false)
    .execute(&mut **tx)
    .await?;
    Ok(result.rows_affected() > 0)
}

async fn advance_read_pointer(
    tx: &mut Transaction<'_, Postgres>,
    participant_id: Uuid,
    target_message_id: Uuid,
) -> Result<bool> {
    let result = sqlx::query(
        "UPDATE chat_conversationparticipant cp SET last_read_message_id = $2, last_read_at = NOW(), updated_at = NOW() WHERE cp.id = $1 AND (cp.last_read_message_id IS NULL OR EXISTS (SELECT 1 FROM chat_message target JOIN chat_message current ON current.id = cp.last_read_message_id WHERE target.id = $2 AND target.sequence > current.sequence))",
    )
    .bind(participant_id)
    .bind(target_message_id)
    .persistent(false)
    .execute(&mut **tx)
    .await?;
    Ok(result.rows_affected() > 0)
}

async fn receipt_state(
    tx: &mut Transaction<'_, Postgres>,
    participant_id: Uuid,
) -> Result<(Option<Uuid>, Option<Uuid>, Value)> {
    let state = sqlx::query_as::<_, (Option<Uuid>, Option<Uuid>, Value)>(
        "SELECT cp.last_delivered_message_id, cp.last_read_message_id, jsonb_build_object('conversation_id', cp.conversation_id::text, 'user_id', cp.user_id::text, 'last_delivered_message_id', cp.last_delivered_message_id::text, 'last_delivered_at', cp.last_delivered_at, 'last_read_message_id', cp.last_read_message_id::text, 'last_read_at', cp.last_read_at) FROM chat_conversationparticipant cp WHERE cp.id = $1",
    )
    .bind(participant_id)
    .persistent(false)
    .fetch_one(&mut **tx)
    .await?;
    Ok(state)
}

fn delivered_payload(state: &Value) -> Value {
    json!({
        "conversation_id": state.get("conversation_id").cloned().unwrap_or(Value::Null),
        "user_id": state.get("user_id").cloned().unwrap_or(Value::Null),
        "last_delivered_message_id": state.get("last_delivered_message_id").cloned().unwrap_or(Value::Null),
        "last_delivered_at": state.get("last_delivered_at").cloned().unwrap_or(Value::Null),
    })
}

async fn insert_missing_deliveries(
    tx: &mut Transaction<'_, Postgres>,
    conversation_id: Uuid,
    actor_id: i64,
    target_message_id: Uuid,
) -> Result<()> {
    let message_ids = sqlx::query_scalar::<_, Uuid>(
        "SELECT m.id FROM chat_message m JOIN chat_message target ON target.id = $3 WHERE m.conversation_id = $1 AND m.sender_id IS DISTINCT FROM $2 AND m.sequence <= target.sequence AND NOT EXISTS (SELECT 1 FROM chat_messagedelivery d WHERE d.message_id = m.id AND d.user_id = $2) ORDER BY m.sequence, m.id",
    )
    .bind(conversation_id)
    .bind(actor_id)
    .bind(target_message_id)
    .persistent(false)
    .fetch_all(&mut **tx)
    .await?;

    for chunk in message_ids.chunks(4_000) {
        let mut builder = QueryBuilder::<Postgres>::new(
            "INSERT INTO chat_messagedelivery (id, created_at, updated_at, message_id, user_id, delivered_at) ",
        );
        builder.push_values(chunk, |mut row, message_id| {
            row.push_bind(Uuid::new_v4())
                .push(", NOW(), NOW(), ")
                .push_bind(*message_id)
                .push(", ")
                .push_bind(actor_id)
                .push(", NOW()");
        });
        builder.push(" ON CONFLICT (message_id, user_id) DO NOTHING");
        builder
            .build()
            .persistent(false)
            .execute(&mut **tx)
            .await?;
    }
    Ok(())
}

async fn insert_audit_event(
    tx: &mut Transaction<'_, Postgres>,
    event_type: &str,
    actor_id: i64,
    conversation_id: Uuid,
    message_id: Option<Uuid>,
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

async fn lock_message_for_actor(
    tx: &mut Transaction<'_, Postgres>,
    message_id: Uuid,
    actor_id: i64,
) -> Result<Uuid> {
    let message = sqlx::query_as::<_, (Uuid, bool)>(
        "SELECT m.conversation_id, cp.is_blocked FROM chat_message m JOIN chat_conversation c ON c.id = m.conversation_id JOIN chat_conversationparticipant cp ON cp.conversation_id = m.conversation_id AND cp.user_id = $2 WHERE m.id = $1 AND c.is_active = TRUE AND cp.left_at IS NULL AND cp.banned_at IS NULL FOR UPDATE OF m, cp",
    )
    .bind(message_id)
    .bind(actor_id)
    .persistent(false)
    .fetch_optional(&mut **tx)
    .await?
    .context("message was not found")?;
    if message.1 {
        anyhow::bail!("participant is blocked");
    }
    Ok(message.0)
}

impl Database {
    pub(crate) async fn mark_conversation_delivered(
        &self,
        conversation_id: Uuid,
        identity: &CommandIdentity,
        requested_message_id: Option<Uuid>,
    ) -> Result<InteractionResult> {
        let pool = self.pool.as_ref().context("SQLx command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let participant_id = lock_active_participant(&mut tx, conversation_id, actor_id).await?;
        let target = resolve_receipt_target(
            &mut tx,
            conversation_id,
            actor_id,
            requested_message_id,
            true,
        )
        .await?;

        let changed = match target {
            Some(message_id) => advance_delivery_pointer(&mut tx, participant_id, message_id).await?,
            None => false,
        };
        let (effective_delivery, _, state) = receipt_state(&mut tx, participant_id).await?;
        if changed {
            if let Some(message_id) = effective_delivery {
                insert_missing_deliveries(&mut tx, conversation_id, actor_id, message_id).await?;
            }
        }

        let payload = delivered_payload(&state);
        let mut events = Vec::new();
        if changed {
            insert_audit_event(
                &mut tx,
                "delivery_marked",
                actor_id,
                conversation_id,
                target,
                json!({}),
            )
            .await?;
            events.push(
                insert_conversation_event(
                    &mut tx,
                    conversation_id,
                    "message.delivered",
                    payload.clone(),
                )
                .await?,
            );
        }
        tx.commit().await?;
        Ok(InteractionResult { payload, events })
    }

    pub(crate) async fn mark_conversation_read(
        &self,
        conversation_id: Uuid,
        identity: &CommandIdentity,
        requested_message_id: Option<Uuid>,
    ) -> Result<InteractionResult> {
        let pool = self.pool.as_ref().context("SQLx command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let participant_id = lock_active_participant(&mut tx, conversation_id, actor_id).await?;
        let target = resolve_receipt_target(
            &mut tx,
            conversation_id,
            actor_id,
            requested_message_id,
            true,
        )
        .await?;

        let read_changed = match target {
            Some(message_id) => advance_read_pointer(&mut tx, participant_id, message_id).await?,
            None => false,
        };
        let (_, effective_read_before_delivery, _) = receipt_state(&mut tx, participant_id).await?;
        let delivery_changed = match effective_read_before_delivery {
            Some(message_id) => advance_delivery_pointer(&mut tx, participant_id, message_id).await?,
            None => false,
        };
        let (effective_delivery, effective_read, payload) = receipt_state(&mut tx, participant_id).await?;
        if delivery_changed {
            if let Some(message_id) = effective_delivery {
                insert_missing_deliveries(&mut tx, conversation_id, actor_id, message_id).await?;
            }
        }

        let delivered = delivered_payload(&payload);
        let mut events = Vec::new();
        if delivery_changed {
            insert_audit_event(
                &mut tx,
                "delivery_marked",
                actor_id,
                conversation_id,
                effective_delivery,
                json!({}),
            )
            .await?;
            events.push(
                insert_conversation_event(
                    &mut tx,
                    conversation_id,
                    "message.delivered",
                    delivered,
                )
                .await?,
            );
        }
        if read_changed {
            insert_audit_event(
                &mut tx,
                "read_marked",
                actor_id,
                conversation_id,
                effective_read,
                json!({}),
            )
            .await?;
            events.push(
                insert_conversation_event(
                    &mut tx,
                    conversation_id,
                    "message.read",
                    payload.clone(),
                )
                .await?,
            );
        }
        tx.commit().await?;
        Ok(InteractionResult { payload, events })
    }

    pub(crate) async fn add_message_reaction(
        &self,
        message_id: Uuid,
        identity: &CommandIdentity,
        emoji: &str,
    ) -> Result<InteractionResult> {
        let pool = self.pool.as_ref().context("SQLx command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let conversation_id = lock_message_for_actor(&mut tx, message_id, actor_id).await?;
        let changed = sqlx::query(
            "INSERT INTO chat_messagereaction (id, created_at, updated_at, message_id, user_id, emoji) VALUES ($1, NOW(), NOW(), $2, $3, $4) ON CONFLICT (message_id, user_id) DO UPDATE SET emoji = EXCLUDED.emoji, updated_at = NOW() WHERE chat_messagereaction.emoji IS DISTINCT FROM EXCLUDED.emoji",
        )
        .bind(Uuid::new_v4())
        .bind(message_id)
        .bind(actor_id)
        .bind(emoji)
        .persistent(false)
        .execute(&mut *tx)
        .await?
        .rows_affected()
            > 0;

        sqlx::query(
            "UPDATE chat_message SET edit_locked_at = COALESCE(edit_locked_at, NOW()), edit_locked_reason = CASE WHEN edit_locked_at IS NULL THEN 'message_has_reactions' ELSE edit_locked_reason END WHERE id = $1",
        )
        .bind(message_id)
        .persistent(false)
        .execute(&mut *tx)
        .await?;

        let payload = self
            .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
            .await?
            .context("message was not found after reaction update")?;
        let mut events = Vec::new();
        if changed {
            insert_audit_event(
                &mut tx,
                "reaction_added",
                actor_id,
                conversation_id,
                Some(message_id),
                json!({"emoji": emoji}),
            )
            .await?;
            events.push(
                insert_conversation_event(
                    &mut tx,
                    conversation_id,
                    "message.reaction_updated",
                    payload.clone(),
                )
                .await?,
            );
        }
        tx.commit().await?;
        Ok(InteractionResult { payload, events })
    }

    pub(crate) async fn remove_message_reaction(
        &self,
        message_id: Uuid,
        identity: &CommandIdentity,
        emoji: &str,
    ) -> Result<InteractionResult> {
        let pool = self.pool.as_ref().context("SQLx command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let conversation_id = lock_message_for_actor(&mut tx, message_id, actor_id).await?;
        let changed = sqlx::query(
            "DELETE FROM chat_messagereaction WHERE message_id = $1 AND user_id = $2 AND emoji = $3",
        )
        .bind(message_id)
        .bind(actor_id)
        .bind(emoji)
        .persistent(false)
        .execute(&mut *tx)
        .await?
        .rows_affected()
            > 0;

        let payload = self
            .get_chat_message_in_transaction(&mut tx, actor_id, message_id)
            .await?
            .context("message was not found after reaction removal")?;
        let mut events = Vec::new();
        if changed {
            insert_audit_event(
                &mut tx,
                "reaction_removed",
                actor_id,
                conversation_id,
                Some(message_id),
                json!({"emoji": emoji}),
            )
            .await?;
            events.push(
                insert_conversation_event(
                    &mut tx,
                    conversation_id,
                    "message.reaction_updated",
                    payload.clone(),
                )
                .await?,
            );
        }
        tx.commit().await?;
        Ok(InteractionResult { payload, events })
    }
}
