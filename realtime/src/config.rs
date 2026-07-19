use std::{env, net::SocketAddr, time::Duration};

use anyhow::{anyhow, Context, Result};

#[derive(Clone, Debug)]
pub struct Config {
    pub bind_addr: SocketAddr,
    pub redis_url: String,
    pub stream_name: String,
    pub stream_group: String,
    pub stream_consumer: String,
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
    pub stream_block_ms: usize,
    pub stream_batch_size: usize,
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
            bind_addr: value("REALTIME_BIND", "0.0.0.0:9000")
                .parse()
                .context("REALTIME_BIND must be a valid socket address")?,
            redis_url: value("REALTIME_STREAM_URL", "redis://redis:6379/3"),
            stream_name: value("REALTIME_STREAM_NAME", "realtime:durable:v1"),
            stream_group: value("REALTIME_STREAM_GROUP", "axum-single-v1"),
            stream_consumer: value("REALTIME_STREAM_CONSUMER", "axum-1"),
            internal_test_enabled,
            internal_test_token,
            auth_enabled: boolean("REALTIME_AUTH_ENABLED", true)?,
            auth_redis_url: value(
                "REALTIME_AUTH_REDIS_URL",
                &value("REALTIME_STREAM_URL", "redis://redis:6379/3"),
            ),
            presence_redis_url: value(
                "REALTIME_PRESENCE_REDIS_URL",
                &value("REALTIME_AUTH_REDIS_URL", &value("REALTIME_STREAM_URL", "redis://redis:6379/3")),
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
            read_buffer_size: number("REALTIME_READ_BUFFER_SIZE", 16_384)?,
            write_buffer_size: number("REALTIME_WRITE_BUFFER_SIZE", 8_192)?,
            max_write_buffer_size: number("REALTIME_MAX_WRITE_BUFFER_SIZE", 131_072)?,
            heartbeat_interval: Duration::from_secs(number(
                "REALTIME_HEARTBEAT_SECONDS",
                25,
            )?),
            client_timeout: Duration::from_secs(number("REALTIME_CLIENT_TIMEOUT_SECONDS", 75)?),
            max_connection_age: Duration::from_secs(number("REALTIME_MAX_CONNECTION_AGE_SECONDS", 600)?),
            connection_refresh_jitter: Duration::from_secs(number(
                "REALTIME_CONNECTION_REFRESH_JITTER_SECONDS",
                60,
            )?),
            send_timeout: Duration::from_secs(number("REALTIME_SEND_TIMEOUT_SECONDS", 5)?),
            stream_block_ms: number("REALTIME_STREAM_BLOCK_MS", 5_000)?,
            stream_batch_size: number("REALTIME_STREAM_BATCH_SIZE", 100)?,
        };
        config.validate()?;
        Ok(config)
    }

    fn validate(&self) -> Result<()> {
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
            || self.stream_block_ms == 0
            || self.stream_batch_size == 0
        {
            return Err(anyhow!("realtime capacities and limits must be greater than zero"));
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
                || self.presence_redis_url.trim().is_empty()
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
