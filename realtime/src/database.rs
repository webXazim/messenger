use std::{str::FromStr, sync::Arc, time::Duration};

use anyhow::{Context, Result};
use sqlx::{postgres::{PgConnectOptions, PgPoolOptions}, PgPool};
use serde_json::{json, Value};
use uuid::Uuid;

use crate::{command_auth::CommandIdentity, config::{ChatReadBackend, Config}};

#[derive(Clone)]
pub struct SendMessageResult {
    pub payload: Value,
    pub was_deduplicated: bool,
}

pub struct Database {
    pool: Option<PgPool>,
    backend: ChatReadBackend,
}

impl Database {
    pub fn from_config(config: &Config) -> Result<Arc<Self>> {
        if config.chat_read_backend == ChatReadBackend::Django && config.chat_command_backend == crate::config::ChatCommandBackend::Django {
            return Ok(Arc::new(Self { pool: None, backend: config.chat_read_backend }));
        }

        let options = PgConnectOptions::from_str(&config.sqlx_database_url)
            .context("SQLX_DATABASE_URL must be a valid PostgreSQL URL")?
            .application_name("crescentsphere-realtime");
        let pool = PgPoolOptions::new()
            .max_connections(config.sqlx_max_connections)
            .min_connections(0)
            .acquire_timeout(config.sqlx_acquire_timeout)
            .idle_timeout(Some(Duration::from_secs(60)))
            .max_lifetime(Some(Duration::from_secs(900)))
            .connect_lazy_with(options);
        Ok(Arc::new(Self { pool: Some(pool), backend: config.chat_read_backend }))
    }

    pub const fn backend_name(&self) -> &'static str { self.backend.as_str() }
    pub fn enabled(&self) -> bool { self.pool.is_some() }

    pub async fn check(&self) -> bool {
        let Some(pool) = &self.pool else { return true; };
        sqlx::query_scalar::<_, i32>("SELECT 1")
            .persistent(false)
            .fetch_one(pool)
            .await
            .is_ok()
    }

    pub async fn is_active_participant(&self, conversation_id: Uuid, user_id: Uuid) -> Result<bool> {
        let pool = self.pool.as_ref().context("SQLx read backend is disabled")?;
        let exists = sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM chat_conversationparticipant WHERE conversation_id = $1 AND user_id = $2 AND left_at IS NULL AND banned_at IS NULL)"
        )
        .bind(conversation_id)
        .bind(user_id)
        .persistent(false)
        .fetch_one(pool)
        .await?;
        Ok(exists)
    }


    pub async fn send_text_message(
        &self,
        conversation_id: Uuid,
        identity: &CommandIdentity,
        text: &str,
        client_temp_id: &str,
    ) -> Result<SendMessageResult> {
        let pool = self.pool.as_ref().context("SQLx command backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = if let Some(user_id) = identity.claimed_user_id {
            sqlx::query_scalar::<_, Uuid>("SELECT id FROM accounts_user WHERE id = $1 AND is_active = TRUE")
                .bind(user_id).persistent(false).fetch_optional(&mut *tx).await?
        } else { None };
        let actor_id = match actor_id {
            Some(value) => value,
            None if !identity.email.is_empty() => sqlx::query_scalar::<_, Uuid>("SELECT id FROM accounts_user WHERE LOWER(email) = $1 AND is_active = TRUE LIMIT 1")
                .bind(&identity.email).persistent(false).fetch_optional(&mut *tx).await?
                .context("authenticated user does not exist locally")?,
            None => anyhow::bail!("authenticated user does not exist locally"),
        };

        let participant = sqlx::query_as::<_, (bool, bool)>(
            "SELECT is_blocked, (moderation_muted_until IS NOT NULL AND moderation_muted_until > NOW()) FROM chat_conversationparticipant WHERE conversation_id = $1 AND user_id = $2 AND left_at IS NULL AND banned_at IS NULL FOR UPDATE"
        )
        .bind(conversation_id)
        .bind(actor_id)
        .persistent(false)
        .fetch_optional(&mut *tx)
        .await?;
        let Some((is_blocked, is_muted)) = participant else {
            anyhow::bail!("actor is not an active participant");
        };
        if is_blocked { anyhow::bail!("participant is blocked"); }
        if is_muted { anyhow::bail!("participant is muted"); }

        if !client_temp_id.is_empty() {
            if let Some(existing) = sqlx::query_scalar::<_, Value>(
                "SELECT jsonb_build_object('id', id::text, 'conversation_id', conversation_id::text, 'type', type, 'text', text, 'sender', jsonb_build_object('id', sender_id::text), 'created_at', created_at, 'updated_at', updated_at, 'attachments', '[]'::jsonb, 'delivery_status', delivery_status, 'is_deleted', is_deleted, 'metadata', metadata, 'client_temp_id', client_temp_id, 'sequence', sequence, 'was_deduplicated', true) FROM chat_message WHERE conversation_id = $1 AND sender_id = $2 AND client_temp_id = $3 LIMIT 1"
            )
            .bind(conversation_id).bind(actor_id).bind(client_temp_id)
            .persistent(false).fetch_optional(&mut *tx).await? {
                tx.commit().await?;
                return Ok(SendMessageResult { payload: existing, was_deduplicated: true });
            }
        }

        let sequence = sqlx::query_scalar::<_, i64>(
            "UPDATE chat_conversation SET next_message_sequence = next_message_sequence + 1, updated_at = NOW() WHERE id = $1 AND is_active = TRUE RETURNING next_message_sequence::bigint"
        )
        .bind(conversation_id)
        .persistent(false)
        .fetch_optional(&mut *tx)
        .await?
        .context("conversation is unavailable")?;

        let message_id = Uuid::new_v4();
        let metadata = json!({"raw_text": text});
        let payload = sqlx::query_scalar::<_, Value>(
            "INSERT INTO chat_message (id, created_at, updated_at, conversation_id, sender_id, type, text, metadata, reply_to_id, forwarded_from_id, is_edited, edited_at, edit_locked_at, edit_locked_reason, is_deleted, deleted_at, client_temp_id, sequence, delivery_status, failed_reason, retry_count) VALUES ($1, NOW(), NOW(), $2, $3, 'text', $4, $5, NULL, NULL, FALSE, NULL, NULL, '', FALSE, NULL, $6, $7, 'sent', '', 0) RETURNING jsonb_build_object('id', id::text, 'conversation_id', conversation_id::text, 'type', type, 'text', text, 'sender', jsonb_build_object('id', sender_id::text), 'created_at', created_at, 'updated_at', updated_at, 'attachments', '[]'::jsonb, 'delivery_status', delivery_status, 'failed_reason', NULL, 'retry_count', retry_count, 'is_deleted', is_deleted, 'metadata', metadata, 'client_temp_id', client_temp_id, 'sequence', sequence, 'was_deduplicated', false)"
        )
        .bind(message_id).bind(conversation_id).bind(actor_id).bind(text).bind(&metadata).bind(client_temp_id).bind(sequence)
        .persistent(false).fetch_one(&mut *tx).await?;

        sqlx::query("UPDATE chat_conversation SET last_message_id = $2, last_message_at = NOW(), updated_at = NOW() WHERE id = $1")
            .bind(conversation_id).bind(message_id).persistent(false).execute(&mut *tx).await?;

        let event_id = Uuid::new_v4();
        let outbox_id = Uuid::new_v4();
        let event_payload = json!({
            "type": "chat.event", "version": 1, "event": "message.created",
            "event_id": event_id.to_string(), "occurred_at": payload.get("created_at").cloned().unwrap_or(Value::Null), "data": payload.clone()
        });
        let audiences = json!([{"kind": "conversation", "id": conversation_id.to_string()}]);
        sqlx::query(
            "INSERT INTO common_realtimeoutboxevent (id, created_at, updated_at, event_id, event_name, payload, audiences, status, attempts, available_at, published_at, delivery_target, published_transport, stream_entry_id, last_error) VALUES ($1, NOW(), NOW(), $2, 'message.created', $3, $4, 'pending', 0, NOW(), NULL, 'nats_jetstream', '', '', '')"
        )
        .bind(outbox_id).bind(event_id).bind(&event_payload).bind(&audiences)
        .persistent(false).execute(&mut *tx).await?;

        tx.commit().await?;
        Ok(SendMessageResult { payload, was_deduplicated: false })
    }

    pub async fn latest_conversation_sequence(&self, conversation_id: Uuid) -> Result<i64> {
        let pool = self.pool.as_ref().context("SQLx read backend is disabled")?;
        let sequence = sqlx::query_scalar::<_, i64>(
            "SELECT next_message_sequence::bigint FROM chat_conversation WHERE id = $1"
        )
        .bind(conversation_id)
        .persistent(false)
        .fetch_optional(pool)
        .await?
        .unwrap_or(0);
        Ok(sequence)
    }
}
