use std::{env, net::SocketAddr, time::Duration};

use anyhow::{anyhow, Context, Result};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RealtimeBackend {
    LegacyRedis,
    Nats,
    Local,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OutboxPublisherBackend {
    Celery,
    Axum,
}

impl OutboxPublisherBackend {
    pub fn from_env() -> Result<Self> {
        match value("REALTIME_OUTBOX_PUBLISHER", "celery").to_ascii_lowercase().as_str() {
            "celery" | "worker" | "recovery" => Ok(Self::Celery),
            "axum" | "direct" | "rust" => Ok(Self::Axum),
            other => Err(anyhow!(
                "REALTIME_OUTBOX_PUBLISHER={other} is invalid; expected celery or axum"
            )),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Celery => "celery",
            Self::Axum => "axum",
        }
    }
}

impl RealtimeBackend {
    fn from_env(name: &str) -> Result<Self> {
        Self::from_env_with_default(name, "nats")
    }

    fn from_env_with_default(name: &str, default: &str) -> Result<Self> {
        match value(name, default).to_ascii_lowercase().as_str() {
            "legacy_redis" | "redis" => Ok(Self::LegacyRedis),
            "nats" | "jetstream" => Ok(Self::Nats),
            "local" | "memory" => Ok(Self::Local),
            other => Err(anyhow!(
                "{name}={other} is invalid; expected legacy_redis, nats, or local"
            )),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::LegacyRedis => "legacy_redis",
            Self::Nats => "nats",
            Self::Local => "local",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChatCommandBackend {
    Django,
    SqlxShadow,
    Axum,
}

impl ChatCommandBackend {
    pub fn from_env() -> Result<Self> {
        let raw = value("CHAT_COMMAND_BACKEND", "django").to_ascii_lowercase();
        match raw.as_str() {
            "django" => Ok(Self::Django),
            "sqlx_shadow" | "shadow" => Ok(Self::SqlxShadow),
            "axum" | "sqlx" => Ok(Self::Axum),
            other => Err(anyhow!("CHAT_COMMAND_BACKEND={other} is invalid; expected django, sqlx_shadow, or axum")),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self { Self::Django => "django", Self::SqlxShadow => "sqlx_shadow", Self::Axum => "axum" }
    }
}


#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChatInteractionBackend {
    Django,
    SqlxShadow,
    Axum,
}

impl ChatInteractionBackend {
    fn from_env() -> Result<Self> {
        match value("CHAT_INTERACTION_BACKEND", "django")
            .to_ascii_lowercase()
            .as_str()
        {
            "django" => Ok(Self::Django),
            "sqlx_shadow" | "shadow" => Ok(Self::SqlxShadow),
            "axum" | "sqlx" => Ok(Self::Axum),
            other => Err(anyhow!(
                "CHAT_INTERACTION_BACKEND={other} is invalid; expected django, sqlx_shadow, or axum"
            )),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Django => "django",
            Self::SqlxShadow => "sqlx_shadow",
            Self::Axum => "axum",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChatMessageMutationBackend {
    Django,
    SqlxShadow,
    Axum,
}

impl ChatMessageMutationBackend {
    fn from_env() -> Result<Self> {
        match value("CHAT_MESSAGE_MUTATION_BACKEND", "django")
            .to_ascii_lowercase()
            .as_str()
        {
            "django" => Ok(Self::Django),
            "sqlx_shadow" | "shadow" => Ok(Self::SqlxShadow),
            "axum" | "sqlx" => Ok(Self::Axum),
            other => Err(anyhow!(
                "CHAT_MESSAGE_MUTATION_BACKEND={other} is invalid; expected django, sqlx_shadow, or axum"
            )),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Django => "django",
            Self::SqlxShadow => "sqlx_shadow",
            Self::Axum => "axum",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChatCallRuntimeBackend {
    Django,
    SqlxShadow,
    Axum,
}

impl ChatCallRuntimeBackend {
    fn from_env() -> Result<Self> {
        match value("CHAT_CALL_RUNTIME_BACKEND", "django")
            .to_ascii_lowercase()
            .as_str()
        {
            "django" => Ok(Self::Django),
            "sqlx_shadow" | "shadow" => Ok(Self::SqlxShadow),
            "axum" | "sqlx" => Ok(Self::Axum),
            other => Err(anyhow!(
                "CHAT_CALL_RUNTIME_BACKEND={other} is invalid; expected django, sqlx_shadow, or axum"
            )),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Django => "django",
            Self::SqlxShadow => "sqlx_shadow",
            Self::Axum => "axum",
        }
    }
}


#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChatAttachmentBackend {
    Django,
    SqlxShadow,
    Axum,
}

impl ChatAttachmentBackend {
    fn from_env() -> Result<Self> {
        match value("CHAT_ATTACHMENT_BACKEND", "django")
            .to_ascii_lowercase()
            .as_str()
        {
            "django" => Ok(Self::Django),
            "sqlx_shadow" | "shadow" => Ok(Self::SqlxShadow),
            "axum" | "sqlx" => Ok(Self::Axum),
            other => Err(anyhow!(
                "CHAT_ATTACHMENT_BACKEND={other} is invalid; expected django, sqlx_shadow, or axum"
            )),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Django => "django",
            Self::SqlxShadow => "sqlx_shadow",
            Self::Axum => "axum",
        }
    }
}


#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChatConversationCommandBackend {
    Django,
    SqlxShadow,
    Axum,
}

impl ChatConversationCommandBackend {
    fn from_env() -> Result<Self> {
        match value("CHAT_CONVERSATION_COMMAND_BACKEND", "django")
            .to_ascii_lowercase()
            .as_str()
        {
            "django" => Ok(Self::Django),
            "sqlx_shadow" | "shadow" => Ok(Self::SqlxShadow),
            "axum" | "sqlx" => Ok(Self::Axum),
            other => Err(anyhow!(
                "CHAT_CONVERSATION_COMMAND_BACKEND={other} is invalid; expected django, sqlx_shadow, or axum"
            )),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Django => "django",
            Self::SqlxShadow => "sqlx_shadow",
            Self::Axum => "axum",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SupportDataBackend {
    Django,
    SqlxShadow,
    Axum,
}

impl SupportDataBackend {
    fn from_env() -> Result<Self> {
        match value("SUPPORT_DATA_BACKEND", "django").to_ascii_lowercase().as_str() {
            "django" => Ok(Self::Django),
            "sqlx_shadow" | "shadow" => Ok(Self::SqlxShadow),
            "axum" | "sqlx" => Ok(Self::Axum),
            other => Err(anyhow!("SUPPORT_DATA_BACKEND={other} is invalid; expected django, sqlx_shadow, or axum")),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Django => "django",
            Self::SqlxShadow => "sqlx_shadow",
            Self::Axum => "axum",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChatReadBackend {
    Django,
    SqlxShadow,
    Sqlx,
}

impl ChatReadBackend {
    fn from_env() -> Result<Self> {
        match value("CHAT_READ_BACKEND", "django").to_ascii_lowercase().as_str() {
            "django" => Ok(Self::Django),
            "sqlx_shadow" | "shadow" => Ok(Self::SqlxShadow),
            "sqlx" => Ok(Self::Sqlx),
            other => Err(anyhow!("CHAT_READ_BACKEND={other} is invalid; expected django, sqlx_shadow, or sqlx")),
        }
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Django => "django",
            Self::SqlxShadow => "sqlx_shadow",
            Self::Sqlx => "sqlx",
        }
    }
}

#[derive(Clone, Debug)]
pub struct Config {
    pub durable_backend: RealtimeBackend,
    pub outbox_publisher_backend: OutboxPublisherBackend,
    pub chat_command_backend: ChatCommandBackend,
    pub chat_interaction_backend: ChatInteractionBackend,
    pub chat_message_mutation_backend: ChatMessageMutationBackend,
    pub chat_call_runtime_backend: ChatCallRuntimeBackend,
    pub chat_attachment_backend: ChatAttachmentBackend,
    pub chat_conversation_command_backend: ChatConversationCommandBackend,
    pub support_data_backend: SupportDataBackend,
    pub central_payments_enabled: bool,
    pub chat_command_jwt_issuer: String,
    pub chat_command_jwt_audience: String,
    pub chat_read_backend: ChatReadBackend,
    pub media_token_signing_key: String,
    pub media_token_issuer: String,
    pub media_token_audience: String,
    pub media_token_ttl_seconds: u64,
    pub sqlx_database_url: String,
    pub sqlx_min_connections: u32,
    pub sqlx_max_connections: u32,
    pub sqlx_acquire_timeout: Duration,
    pub sqlx_idle_timeout: Duration,
    pub sqlx_max_lifetime: Duration,
    pub http_read_concurrency: usize,
    pub http_write_concurrency: usize,
    pub http_request_timeout: Duration,
    pub http_max_body_bytes: usize,
    pub message_edit_window_seconds: i64,
    pub call_reconnect_grace_seconds: i64,
    pub call_speaker_level_threshold: i32,
    pub call_grid_layout_threshold: i64,
    pub call_stale_participant_seconds: i64,
    pub call_signal_queue_capacity: usize,
    pub call_signal_ttl: Duration,
    pub support_signal_queue_capacity: usize,
    pub support_signal_ttl: Duration,
    pub support_calls_enabled: bool,
    pub support_call_ring_timeout: Duration,
    pub support_call_signal_max_bytes: usize,
    pub support_widget_message_rate_per_minute: i64,
    pub support_signal_rate_per_second: u32,
    pub ephemeral_backend: RealtimeBackend,
    pub presence_backend: RealtimeBackend,
    pub bind_addr: SocketAddr,
    pub nats_probe_enabled: bool,
    pub nats_url: String,
    pub nats_user: String,
    pub nats_password: String,
    pub nats_connect_timeout: Duration,
    pub nats_stream_name: String,
    pub nats_consumer_name: String,
    pub nats_subject_filter: String,
    pub nats_durable_subject_prefix: String,
    pub outbox_publish_timeout: Duration,
    pub outbox_mark_attempts: usize,
    pub nats_ephemeral_subject: String,
    pub nats_node_id: String,
    pub connection_ownership_backend: RealtimeBackend,
    pub nats_ownership_subject: String,
    pub nats_delivery_subject_prefix: String,
    pub ownership_announce_interval: Duration,
    pub ownership_lease_ttl: Duration,
    pub nats_ack_wait: Duration,
    pub nats_max_deliver: i64,
    pub nats_max_ack_pending: i64,
    pub event_dedupe_capacity: usize,
    pub internal_test_enabled: bool,
    pub internal_test_token: String,
    pub auth_enabled: bool,
    pub auth_redis_url: String,
    pub presence_redis_url: String,
    pub presence_ttl_seconds: u64,
    pub presence_disconnect_grace: Duration,
    pub auth_public_key: String,
    pub auth_public_key_path: String,
    pub token_issuer: String,
    pub ticket_audience: String,
    pub grant_audience: String,
    pub call_grant_audience: String,
    pub ticket_replay_prefix: String,
    pub auth_leeway_seconds: u64,
    pub require_origin: bool,
    pub allowed_origins: Vec<String>,
    pub max_connections: usize,
    pub max_user_connections: usize,
    pub max_widget_connections: usize,
    pub max_device_connections: usize,
    pub high_queue_capacity: usize,
    pub low_queue_capacity: usize,
    pub max_message_size: usize,
    pub max_frame_size: usize,
    pub read_buffer_size: usize,
    pub write_buffer_size: usize,
    pub max_write_buffer_size: usize,
    pub heartbeat_interval: Duration,
    pub client_timeout: Duration,
    pub max_connection_age: Duration,
    pub connection_refresh_jitter: Duration,
    pub send_timeout: Duration,
}

impl Config {
    pub fn from_env() -> Result<Self> {
        let internal_test_enabled = boolean("REALTIME_INTERNAL_TEST_ENABLED", false)?;
        let internal_test_token = env::var("REALTIME_INTERNAL_TEST_TOKEN")
            .unwrap_or_default()
            .trim()
            .to_owned();
        if internal_test_enabled && internal_test_token.len() < 32 {
            return Err(anyhow!(
                "REALTIME_INTERNAL_TEST_TOKEN must contain at least 32 characters when internal tests are enabled"
            ));
        }

        let config = Self {
            durable_backend: RealtimeBackend::from_env_with_default("REALTIME_DURABLE_BACKEND", "nats")?,
            outbox_publisher_backend: OutboxPublisherBackend::from_env()?,
            chat_command_backend: ChatCommandBackend::from_env()?,
            chat_interaction_backend: ChatInteractionBackend::from_env()?,
            chat_message_mutation_backend: ChatMessageMutationBackend::from_env()?,
            chat_call_runtime_backend: ChatCallRuntimeBackend::from_env()?,
            chat_attachment_backend: ChatAttachmentBackend::from_env()?,
            chat_conversation_command_backend: ChatConversationCommandBackend::from_env()?,
            support_data_backend: SupportDataBackend::from_env()?,
            central_payments_enabled: boolean("CENTRAL_PAYMENTS_ENABLED", true)?,
            chat_command_jwt_issuer: value("CHAT_COMMAND_JWT_ISSUER", &value("REALTIME_TOKEN_ISSUER", "crescentsphere-django")),
            chat_command_jwt_audience: value("CHAT_COMMAND_JWT_AUDIENCE", ""),
            chat_read_backend: ChatReadBackend::from_env()?,
            media_token_signing_key: value("MEDIA_TOKEN_SHARED_SECRET", ""),
            media_token_issuer: value("MEDIA_TOKEN_ISSUER", "crescentsphere-media"),
            media_token_audience: value("MEDIA_TOKEN_AUDIENCE", "crescentsphere-private-media"),
            media_token_ttl_seconds: number("MEDIA_TOKEN_TTL_SECONDS", 300u64)?.clamp(30, 3600),
            sqlx_database_url: value("SQLX_DATABASE_URL", "postgres://messenger_user:messenger_password@pgbouncer:6432/messenger_api"),
            sqlx_min_connections: number("SQLX_MIN_CONNECTIONS", 1u32)?,
            sqlx_max_connections: number("SQLX_MAX_CONNECTIONS", 4u32)?,
            sqlx_acquire_timeout: Duration::from_millis(number("SQLX_ACQUIRE_TIMEOUT_MS", number("SQLX_ACQUIRE_TIMEOUT_SECONDS", 3u64)?.saturating_mul(1_000))?.clamp(100, 10_000)),
            sqlx_idle_timeout: Duration::from_secs(number("SQLX_IDLE_TIMEOUT_SECONDS", 60u64)?.clamp(15, 600)),
            sqlx_max_lifetime: Duration::from_secs(number("SQLX_MAX_LIFETIME_SECONDS", 900u64)?.clamp(60, 3_600)),
            http_read_concurrency: number("REALTIME_HTTP_READ_CONCURRENCY", 24usize)?.clamp(1, 256),
            http_write_concurrency: number("REALTIME_HTTP_WRITE_CONCURRENCY", 12usize)?.clamp(1, 128),
            http_request_timeout: Duration::from_millis(number("REALTIME_HTTP_REQUEST_TIMEOUT_MS", 10_000u64)?.clamp(500, 60_000)),
            http_max_body_bytes: number("REALTIME_HTTP_MAX_BODY_BYTES", 1_048_576usize)?.clamp(16_384, 8_388_608),
            message_edit_window_seconds: number("MESSAGE_EDIT_WINDOW_SECONDS", 900i64)?.max(0),
            call_reconnect_grace_seconds: number("CALL_RECONNECT_GRACE_SECONDS", 20i64)?.clamp(5, 120),
            call_speaker_level_threshold: number("CALL_SPEAKER_LEVEL_THRESHOLD", 35i32)?.clamp(1, 100),
            call_grid_layout_threshold: number("CALL_GRID_LAYOUT_THRESHOLD", 4i64)?.clamp(2, 16),
            call_stale_participant_seconds: number("CALL_STALE_PARTICIPANT_SECONDS", 35i64)?.clamp(10, 300),
            call_signal_queue_capacity: number("CALL_SIGNAL_QUEUE_CAPACITY", 256usize)?.clamp(16, 2048),
            call_signal_ttl: Duration::from_secs(number("CALL_SIGNAL_QUEUE_TTL_SECONDS", 180u64)?.clamp(5, 600)),
            support_signal_queue_capacity: number("SUPPORT_SIGNAL_QUEUE_CAPACITY", 256usize)?.clamp(16, 2048),
            support_signal_ttl: Duration::from_secs(number("SUPPORT_SIGNAL_QUEUE_TTL_SECONDS", 180u64)?.clamp(5, 600)),
            support_calls_enabled: boolean("SUPPORT_CALLS_ENABLED", false)?,
            support_call_ring_timeout: Duration::from_secs(number("SUPPORT_CALL_RING_TIMEOUT_SECONDS", 45u64)?.clamp(15, 300)),
            support_call_signal_max_bytes: number("SUPPORT_CALL_SIGNAL_MAX_BYTES", 131_072usize)?.clamp(16_384, 1_048_576),
            support_widget_message_rate_per_minute: number("SUPPORT_WIDGET_MESSAGE_RATE_PER_MINUTE", 30i64)?.clamp(5, 300),
            support_signal_rate_per_second: number("SUPPORT_SIGNAL_RATE_PER_SECOND", 30u32)?.clamp(5, 200),
            ephemeral_backend: RealtimeBackend::from_env_with_default("REALTIME_EPHEMERAL_BACKEND", "local")?,
            presence_backend: RealtimeBackend::from_env_with_default("REALTIME_PRESENCE_BACKEND", "legacy_redis")?,
            bind_addr: value("REALTIME_BIND", "0.0.0.0:9000")
                .parse()
                .context("REALTIME_BIND must be a valid socket address")?,
            nats_probe_enabled: boolean("NATS_PROBE_ENABLED", false)?,
            nats_url: value("NATS_URL", "nats://nats:4222"),
            nats_user: value("NATS_USER", "realtime"),
            nats_password: value("NATS_PASSWORD", ""),
            nats_connect_timeout: Duration::from_secs(number("NATS_CONNECT_TIMEOUT_SECONDS", 3u64)?),
            nats_stream_name: value("NATS_CHAT_STREAM", "CHAT_EVENTS"),
            nats_consumer_name: value("NATS_DURABLE_CONSUMER", "realtime-axum-v1"),
            nats_subject_filter: value("NATS_DURABLE_SUBJECT_FILTER", "event.chat.>"),
            nats_durable_subject_prefix: value("NATS_DURABLE_SUBJECT_PREFIX", "event.chat"),
            outbox_publish_timeout: Duration::from_millis(number("REALTIME_OUTBOX_PUBLISH_TIMEOUT_MS", 1500u64)?.clamp(250, 10_000)),
            outbox_mark_attempts: number("REALTIME_OUTBOX_MARK_ATTEMPTS", 3usize)?.clamp(1, 5),
            nats_ephemeral_subject: value("NATS_EPHEMERAL_SUBJECT", "rt.ephemeral.v1"),
            nats_node_id: value("NATS_NODE_ID", "axum-01"),
            connection_ownership_backend: RealtimeBackend::from_env_with_default("REALTIME_CONNECTION_OWNERSHIP_BACKEND", "local")?,
            nats_ownership_subject: value("NATS_OWNERSHIP_SUBJECT", "rt.ownership.v1"),
            nats_delivery_subject_prefix: value("NATS_DELIVERY_SUBJECT_PREFIX", "rt.deliver"),
            ownership_announce_interval: Duration::from_secs(number("REALTIME_OWNERSHIP_ANNOUNCE_SECONDS", 5u64)?),
            ownership_lease_ttl: Duration::from_secs(number("REALTIME_OWNERSHIP_LEASE_TTL_SECONDS", 20u64)?),
            nats_ack_wait: Duration::from_secs(number("NATS_ACK_WAIT_SECONDS", 30u64)?),
            nats_max_deliver: number("NATS_MAX_DELIVER", 5i64)?,
            nats_max_ack_pending: number("NATS_MAX_ACK_PENDING", 128i64)?,
            event_dedupe_capacity: number("REALTIME_EVENT_DEDUPE_CAPACITY", 10_000usize)?,
            internal_test_enabled,
            internal_test_token,
            auth_enabled: boolean("REALTIME_AUTH_ENABLED", true)?,
            auth_redis_url: value("REALTIME_AUTH_REDIS_URL", "redis://redis:6379/3"),
            presence_redis_url: value(
                "REALTIME_PRESENCE_REDIS_URL",
                &value("REALTIME_AUTH_REDIS_URL", "redis://redis:6379/3"),
            ),
            presence_ttl_seconds: number("REALTIME_PRESENCE_TTL_SECONDS", 90u64)?,
            presence_disconnect_grace: Duration::from_secs(number(
                "REALTIME_PRESENCE_DISCONNECT_GRACE_SECONDS",
                12u64,
            )?),
            auth_public_key: env::var("REALTIME_SIGNING_PUBLIC_KEY")
                .unwrap_or_default()
                .replace("\\n", "\n")
                .trim()
                .to_owned(),
            auth_public_key_path: value(
                "REALTIME_SIGNING_PUBLIC_KEY_PATH",
                "/run/secrets/realtime-public.pem",
            ),
            token_issuer: value("REALTIME_TOKEN_ISSUER", "crescentsphere-django"),
            ticket_audience: value("REALTIME_TICKET_AUDIENCE", "crescentsphere-realtime"),
            grant_audience: value(
                "REALTIME_GRANT_AUDIENCE",
                "crescentsphere-realtime-grant",
            ),
            call_grant_audience: value(
                "REALTIME_CALL_GRANT_AUDIENCE",
                "crescentsphere-realtime-call-grant",
            ),
            ticket_replay_prefix: value(
                "REALTIME_TICKET_REPLAY_PREFIX",
                "realtime:ticket-used:",
            ),
            auth_leeway_seconds: number("REALTIME_AUTH_LEEWAY_SECONDS", 5u64)?,
            require_origin: boolean("REALTIME_REQUIRE_ORIGIN", true)?,
            allowed_origins: csv("REALTIME_ALLOWED_ORIGINS"),
            max_connections: number("REALTIME_MAX_CONNECTIONS", 500)?,
            max_user_connections: number("REALTIME_MAX_USER_CONNECTIONS", 5)?,
            max_widget_connections: number("REALTIME_MAX_WIDGET_CONNECTIONS", 2)?,
            max_device_connections: number("REALTIME_MAX_DEVICE_CONNECTIONS", 2)?,
            high_queue_capacity: number("REALTIME_HIGH_QUEUE_CAPACITY", 32)?,
            low_queue_capacity: number("REALTIME_LOW_QUEUE_CAPACITY", 8)?,
            max_message_size: number("REALTIME_MAX_MESSAGE_SIZE", 65_536)?,
            max_frame_size: number("REALTIME_MAX_FRAME_SIZE", 65_536)?,
            read_buffer_size: number("REALTIME_READ_BUFFER_SIZE", 4_096)?,
            write_buffer_size: number("REALTIME_WRITE_BUFFER_SIZE", 4_096)?,
            max_write_buffer_size: number("REALTIME_MAX_WRITE_BUFFER_SIZE", 131_072)?,
            heartbeat_interval: Duration::from_secs(number(
                "REALTIME_HEARTBEAT_SECONDS",
                30,
            )?),
            client_timeout: Duration::from_secs(number("REALTIME_CLIENT_TIMEOUT_SECONDS", 90)?),
            max_connection_age: Duration::from_secs(number("REALTIME_MAX_CONNECTION_AGE_SECONDS", 3_600)?),
            connection_refresh_jitter: Duration::from_secs(number(
                "REALTIME_CONNECTION_REFRESH_JITTER_SECONDS",
                300,
            )?),
            send_timeout: Duration::from_secs(number("REALTIME_SEND_TIMEOUT_SECONDS", 5)?),
        };
        config.validate()?;
        Ok(config)
    }

    fn validate(&self) -> Result<()> {
        if self.sqlx_max_connections == 0 || self.sqlx_max_connections > 8 {
            return Err(anyhow!("SQLX_MAX_CONNECTIONS must be between 1 and 8 on this deployment"));
        }
        if self.sqlx_min_connections > self.sqlx_max_connections {
            return Err(anyhow!("SQLX_MIN_CONNECTIONS cannot exceed SQLX_MAX_CONNECTIONS"));
        }
        if self.http_read_concurrency + self.http_write_concurrency > 384 {
            return Err(anyhow!("combined realtime HTTP concurrency is too high for this deployment"));
        }
        if (self.chat_read_backend != ChatReadBackend::Django
            || self.chat_command_backend != ChatCommandBackend::Django
            || self.chat_interaction_backend != ChatInteractionBackend::Django
            || self.chat_message_mutation_backend != ChatMessageMutationBackend::Django
            || self.chat_call_runtime_backend != ChatCallRuntimeBackend::Django
            || self.chat_attachment_backend != ChatAttachmentBackend::Django
            || self.chat_conversation_command_backend != ChatConversationCommandBackend::Django
            || self.support_data_backend != SupportDataBackend::Django
            || self.outbox_publisher_backend == OutboxPublisherBackend::Axum)
            && !self.sqlx_database_url.starts_with("postgres")
        {
            return Err(anyhow!(
                "SQLX_DATABASE_URL must use PostgreSQL when SQLx chat paths or direct Axum outbox publishing are enabled"
            ));
        }
        if self.chat_attachment_backend != ChatAttachmentBackend::Django {
            if self.media_token_signing_key.len() < 32
                || self.media_token_signing_key == "replace-with-at-least-32-random-characters"
            {
                return Err(anyhow!(
                    "MEDIA_TOKEN_SHARED_SECRET must contain at least 32 random characters when Axum attachment paths are enabled"
                ));
            }
            if self.media_token_issuer.trim().is_empty() || self.media_token_audience.trim().is_empty() {
                return Err(anyhow!(
                    "MEDIA_TOKEN_ISSUER and MEDIA_TOKEN_AUDIENCE are required when Axum attachment paths are enabled"
                ));
            }
        }
        if self.max_connections == 0
            || self.max_user_connections == 0
            || self.max_widget_connections == 0
            || self.max_device_connections == 0
            || self.high_queue_capacity == 0
            || self.low_queue_capacity == 0
            || self.max_message_size == 0
            || self.max_frame_size == 0
            || self.read_buffer_size == 0
            || self.write_buffer_size == 0
            || self.send_timeout.is_zero()
            || self.max_connection_age.is_zero()
            || self.presence_ttl_seconds == 0
            || self.nats_connect_timeout.is_zero()
            || self.nats_ack_wait.is_zero()
            || self.nats_max_deliver <= 0
            || self.nats_max_ack_pending <= 0
            || self.event_dedupe_capacity == 0
            || self.outbox_publish_timeout.is_zero()
            || self.outbox_mark_attempts == 0
            || self.ownership_announce_interval.is_zero()
            || self.ownership_lease_ttl.is_zero()
        {
            return Err(anyhow!("realtime capacities and limits must be greater than zero"));
        }
        if self.nats_probe_enabled && self.nats_url.trim().is_empty() {
            return Err(anyhow!("NATS_URL is required when NATS_PROBE_ENABLED=true"));
        }
        if self.durable_backend != RealtimeBackend::Nats {
            return Err(anyhow!(
                "REALTIME_DURABLE_BACKEND must be nats; the retired Redis Streams delivery path is no longer included"
            ));
        }
        if self.durable_backend == RealtimeBackend::Nats {
            if self.nats_url.trim().is_empty()
                || self.nats_user.trim().is_empty()
                || self.nats_password.is_empty()
                || self.nats_stream_name.trim().is_empty()
                || self.nats_consumer_name.trim().is_empty()
                || self.nats_subject_filter.trim().is_empty()
                || self.nats_durable_subject_prefix.trim().is_empty()
            {
                return Err(anyhow!(
                    "NATS_URL, NATS_USER, NATS_PASSWORD, NATS_CHAT_STREAM, NATS_DURABLE_CONSUMER, NATS_DURABLE_SUBJECT_FILTER and NATS_DURABLE_SUBJECT_PREFIX are required when REALTIME_DURABLE_BACKEND=nats"
                ));
            }
        }
        if self.ephemeral_backend == RealtimeBackend::LegacyRedis {
            return Err(anyhow!(
                "REALTIME_EPHEMERAL_BACKEND=legacy_redis is not supported; use local for one Axum node or nats for multiple nodes"
            ));
        }
        if self.support_data_backend == SupportDataBackend::Axum
            && self.ephemeral_backend != RealtimeBackend::Nats
        {
            return Err(anyhow!(
                "REALTIME_EPHEMERAL_BACKEND must be nats when SUPPORT_DATA_BACKEND=axum so visitor/team call signaling works across Axum nodes"
            ));
        }
        if self.ephemeral_backend == RealtimeBackend::Nats
            && (self.nats_url.trim().is_empty()
                || self.nats_ephemeral_subject.trim().is_empty()
                || self.nats_node_id.trim().is_empty())
        {
            return Err(anyhow!(
                "NATS_URL, NATS_EPHEMERAL_SUBJECT and NATS_NODE_ID are required when REALTIME_EPHEMERAL_BACKEND=nats"
            ));
        }
        if self.connection_ownership_backend == RealtimeBackend::Nats
            && (self.nats_url.trim().is_empty()
                || self.nats_node_id.trim().is_empty()
                || self.nats_ownership_subject.trim().is_empty()
                || self.nats_delivery_subject_prefix.trim().is_empty())
        {
            return Err(anyhow!(
                "NATS_URL, NATS_NODE_ID, NATS_OWNERSHIP_SUBJECT and NATS_DELIVERY_SUBJECT_PREFIX are required when REALTIME_CONNECTION_OWNERSHIP_BACKEND=nats"
            ));
        }
        if self.connection_ownership_backend == RealtimeBackend::LegacyRedis {
            return Err(anyhow!(
                "REALTIME_CONNECTION_OWNERSHIP_BACKEND=legacy_redis is not supported; use local or nats"
            ));
        }
        if self.ownership_lease_ttl <= self.ownership_announce_interval {
            return Err(anyhow!(
                "REALTIME_OWNERSHIP_LEASE_TTL_SECONDS must exceed REALTIME_OWNERSHIP_ANNOUNCE_SECONDS"
            ));
        }
        if self.presence_backend == RealtimeBackend::Nats {
            return Err(anyhow!(
                "REALTIME_PRESENCE_BACKEND=nats is not implemented; use local for Axum-owned leases or legacy_redis for rollback"
            ));
        }
        if self.max_write_buffer_size <= self.write_buffer_size + self.max_message_size {
            return Err(anyhow!(
                "REALTIME_MAX_WRITE_BUFFER_SIZE must exceed write buffer plus maximum message size"
            ));
        }
        if self.client_timeout <= self.heartbeat_interval {
            return Err(anyhow!(
                "REALTIME_CLIENT_TIMEOUT_SECONDS must exceed REALTIME_HEARTBEAT_SECONDS"
            ));
        }
        if self.max_connection_age <= self.client_timeout {
            return Err(anyhow!(
                "REALTIME_MAX_CONNECTION_AGE_SECONDS must exceed REALTIME_CLIENT_TIMEOUT_SECONDS"
            ));
        }
        if self.connection_refresh_jitter > Duration::from_secs(300) {
            return Err(anyhow!(
                "REALTIME_CONNECTION_REFRESH_JITTER_SECONDS must not exceed 300"
            ));
        }
        if self.auth_enabled {
            if self.auth_public_key.is_empty() && self.auth_public_key_path.trim().is_empty() {
                return Err(anyhow!(
                    "REALTIME_SIGNING_PUBLIC_KEY or REALTIME_SIGNING_PUBLIC_KEY_PATH is required"
                ));
            }
            if self.auth_redis_url.trim().is_empty()
                || (self.presence_backend == RealtimeBackend::LegacyRedis && self.presence_redis_url.trim().is_empty())
                || self.token_issuer.trim().is_empty()
                || self.ticket_audience.trim().is_empty()
                || self.grant_audience.trim().is_empty()
                || self.call_grant_audience.trim().is_empty()
                || self.ticket_replay_prefix.trim().is_empty()
            {
                return Err(anyhow!("realtime authentication settings cannot be empty"));
            }
            if self.require_origin && self.allowed_origins.is_empty() {
                return Err(anyhow!(
                    "REALTIME_ALLOWED_ORIGINS is required when REALTIME_REQUIRE_ORIGIN=true"
                ));
            }
        }
        Ok(())
    }
}

fn value(name: &str, default: &str) -> String {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| default.to_owned())
}

fn number<T>(name: &str, default: T) -> Result<T>
where
    T: std::str::FromStr + Copy,
    T::Err: std::error::Error + Send + Sync + 'static,
{
    match env::var(name) {
        Ok(value) if !value.trim().is_empty() => value
            .trim()
            .parse::<T>()
            .with_context(|| format!("{name} is invalid")),
        _ => Ok(default),
    }
}

fn boolean(name: &str, default: bool) -> Result<bool> {
    match env::var(name) {
        Ok(value) if !value.trim().is_empty() => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" => Ok(false),
            _ => Err(anyhow!("{name} must be a boolean")),
        },
        _ => Ok(default),
    }
}

fn csv(name: &str) -> Vec<String> {
    env::var(name)
        .unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
        .collect()
}
