use std::{str::FromStr, sync::Arc};

use anyhow::{Context, Result};
use sqlx::{postgres::{PgConnectOptions, PgPoolOptions}, PgPool};
use serde::Serialize;
use serde_json::Value;
use uuid::Uuid;

use crate::{config::{ChatAttachmentBackend, ChatCallRuntimeBackend, ChatCommandBackend, ChatConversationCommandBackend, ChatInteractionBackend, ChatMessageMutationBackend, ChatReadBackend, Config, OutboxPublisherBackend, SupportDataBackend}, protocol::AudienceKey};

#[derive(Clone)]
pub struct CommittedEvent {
    pub event_id: Uuid,
    pub event_name: String,
    pub payload: Value,
    pub audiences: Vec<AudienceKey>,
}

#[derive(Clone)]
pub struct SendMessageResult {
    pub payload: Value,
    pub was_deduplicated: bool,
    pub events: Vec<CommittedEvent>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PoolSnapshot {
    pub enabled: bool,
    pub size: u32,
    pub idle: usize,
    pub in_use: usize,
    pub min_connections: u32,
    pub max_connections: u32,
}

pub struct Database {
    pub(crate) pool: Option<PgPool>,
    backend: ChatReadBackend,
    min_connections: u32,
    max_connections: u32,
    pub(crate) message_edit_window_seconds: i64,
}

impl Database {
    pub fn from_config(config: &Config) -> Result<Arc<Self>> {
        if config.chat_read_backend == ChatReadBackend::Django
            && config.chat_command_backend == ChatCommandBackend::Django
            && config.chat_interaction_backend == ChatInteractionBackend::Django
            && config.chat_message_mutation_backend == ChatMessageMutationBackend::Django
            && config.chat_call_runtime_backend == ChatCallRuntimeBackend::Django
            && config.chat_attachment_backend == ChatAttachmentBackend::Django
            && config.chat_conversation_command_backend == ChatConversationCommandBackend::Django
            && config.support_data_backend == SupportDataBackend::Django
            && config.outbox_publisher_backend == OutboxPublisherBackend::Celery
        {
            return Ok(Arc::new(Self {
                pool: None,
                backend: config.chat_read_backend,
                min_connections: 0,
                max_connections: 0,
                message_edit_window_seconds: config.message_edit_window_seconds,
            }));
        }

        let options = PgConnectOptions::from_str(&config.sqlx_database_url)
            .context("SQLX_DATABASE_URL must be a valid PostgreSQL URL")?
            .application_name("crescentsphere-realtime");
        let pool = PgPoolOptions::new()
            .max_connections(config.sqlx_max_connections)
            .min_connections(config.sqlx_min_connections)
            .acquire_timeout(config.sqlx_acquire_timeout)
            .idle_timeout(Some(config.sqlx_idle_timeout))
            .max_lifetime(Some(config.sqlx_max_lifetime))
            .test_before_acquire(true)
            .connect_lazy_with(options);
        Ok(Arc::new(Self {
            pool: Some(pool),
            backend: config.chat_read_backend,
            min_connections: config.sqlx_min_connections,
            max_connections: config.sqlx_max_connections,
            message_edit_window_seconds: config.message_edit_window_seconds,
        }))
    }

    pub const fn backend_name(&self) -> &'static str { self.backend.as_str() }
    pub fn enabled(&self) -> bool { self.pool.is_some() }

    pub fn pool_snapshot(&self) -> PoolSnapshot {
        let Some(pool) = &self.pool else {
            return PoolSnapshot {
                enabled: false,
                size: 0,
                idle: 0,
                in_use: 0,
                min_connections: 0,
                max_connections: 0,
            };
        };
        let size = pool.size();
        let idle = pool.num_idle();
        PoolSnapshot {
            enabled: true,
            size,
            idle,
            in_use: (size as usize).saturating_sub(idle),
            min_connections: self.min_connections,
            max_connections: self.max_connections,
        }
    }

    pub async fn check(&self) -> bool {
        let Some(pool) = &self.pool else { return true; };
        sqlx::query_scalar::<_, i32>("SELECT 1")
            .persistent(false)
            .fetch_one(pool)
            .await
            .is_ok()
    }

    pub async fn persist_user_last_seen(&self, user_id: &str) -> Result<()> {
        let Some(pool) = &self.pool else {
            return Ok(());
        };
        let user_id = user_id.parse::<i64>().context("presence user id is invalid")?;
        sqlx::query("UPDATE accounts_user SET last_seen_at=NOW() WHERE id=$1")
            .bind(user_id)
            .persistent(false)
            .execute(pool)
            .await?;
        Ok(())
    }

    pub async fn is_active_participant(&self, conversation_id: Uuid, user_id: i64) -> Result<bool> {
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

    pub async fn can_emit_messenger_ephemeral(&self, conversation_id: Uuid, user_id: i64) -> Result<bool> {
        let Some(pool) = &self.pool else { return Ok(true); };
        let allowed = sqlx::query_scalar::<_, bool>(r#"
            SELECT EXISTS(
                SELECT 1
                FROM chat_conversation c
                JOIN chat_conversationparticipant cp
                  ON cp.conversation_id=c.id
                 AND cp.user_id=$2
                 AND cp.left_at IS NULL
                 AND cp.banned_at IS NULL
                 AND cp.is_blocked=FALSE
                WHERE c.id=$1
                  AND c.is_active=TRUE
                  AND (
                    c.type <> 'direct'
                    OR NOT EXISTS(
                        SELECT 1
                        FROM chat_conversationparticipant other
                        JOIN chat_userblock ub ON (
                            (ub.blocker_id=$2 AND ub.blocked_id=other.user_id)
                            OR (ub.blocker_id=other.user_id AND ub.blocked_id=$2)
                        )
                        WHERE other.conversation_id=c.id
                          AND other.user_id<>$2
                          AND other.left_at IS NULL
                          AND other.banned_at IS NULL
                    )
                  )
            )
        "#)
        .bind(conversation_id)
        .bind(user_id)
        .persistent(false)
        .fetch_one(pool)
        .await?;
        Ok(allowed)
    }


    pub async fn mark_outbox_published(
        &self,
        event_id: Uuid,
        stream_sequence: u64,
    ) -> Result<()> {
        let pool = self.pool.as_ref().context("SQLx outbox publisher is disabled")?;
        sqlx::query(
            "UPDATE common_realtimeoutboxevent SET status = 'published', attempts = attempts + 1, published_at = COALESCE(published_at, NOW()), available_at = NOW(), published_transport = 'nats_jetstream_axum', stream_entry_id = $2, last_error = '', updated_at = NOW() WHERE event_id = $1 AND status <> 'published'",
        )
        .bind(event_id)
        .bind(stream_sequence.to_string())
        .persistent(false)
        .execute(pool)
        .await?;
        Ok(())
    }

    pub async fn mark_outbox_publish_failed(&self, event_id: Uuid, error: &str) -> Result<()> {
        let pool = self.pool.as_ref().context("SQLx outbox publisher is disabled")?;
        sqlx::query(
            "UPDATE common_realtimeoutboxevent SET status = 'failed', attempts = attempts + 1, available_at = NOW() + INTERVAL '2 seconds', last_error = $2, updated_at = NOW() WHERE event_id = $1 AND status IN ('pending', 'failed')",
        )
        .bind(event_id)
        .bind(error.chars().take(2000).collect::<String>())
        .persistent(false)
        .execute(pool)
        .await?;
        Ok(())
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
