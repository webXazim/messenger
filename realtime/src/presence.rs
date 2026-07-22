use std::{
    collections::{HashMap, HashSet},
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result};
use dashmap::DashMap;
use redis::aio::ConnectionManager;
use tokio::sync::OnceCell;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use uuid::Uuid;

use crate::{
    auth::{ActorType, AuthenticatedSession},
    config::{Config, RealtimeBackend},
};

const USER_KEY_PREFIX: &str = "realtime:presence:user:";
const LAST_SEEN_KEY_PREFIX: &str = "realtime:presence:last-seen:";
const SUPPORT_VISITOR_KEY_PREFIX: &str = "realtime:presence:support-visitor:";

#[derive(Clone, Debug, Deserialize, Serialize)]
struct DeviceRecord {
    last_seen: f64,
    device_type: String,
    presence_status: String,
}

#[derive(Clone, Debug)]
struct LocalConnectionRecord {
    user_id: String,
    record: DeviceRecord,
}

pub struct PresenceStore {
    backend: RealtimeBackend,
    redis_client: Option<redis::Client>,
    redis_connection: OnceCell<ConnectionManager>,
    ttl_seconds: u64,
    local_users: DashMap<Uuid, LocalConnectionRecord>,
    local_last_seen: DashMap<String, f64>,
    local_support_visitors: DashMap<Uuid, String>,
}

impl PresenceStore {
    pub fn new(config: &Config) -> Result<Self> {
        let redis_client = if config.presence_backend == RealtimeBackend::LegacyRedis {
            Some(
                redis::Client::open(config.presence_redis_url.clone())
                    .context("invalid REALTIME_PRESENCE_REDIS_URL")?,
            )
        } else {
            None
        };
        Ok(Self {
            backend: config.presence_backend,
            redis_client,
            redis_connection: OnceCell::new(),
            ttl_seconds: config.presence_ttl_seconds,
            local_users: DashMap::new(),
            local_last_seen: DashMap::new(),
            local_support_visitors: DashMap::new(),
        })
    }

    pub fn backend_name(&self) -> &'static str {
        self.backend.as_str()
    }

    pub fn local_user_connection_count(&self) -> usize {
        self.local_users.len()
    }

    pub fn local_support_connection_count(&self) -> usize {
        self.local_support_visitors.len()
    }

    async fn connection(&self) -> Result<ConnectionManager> {
        let client = self
            .redis_client
            .as_ref()
            .context("Redis presence backend is not active")?;
        let connection = self
            .redis_connection
            .get_or_try_init(|| async {
                client
                    .get_connection_manager()
                    .await
                    .context("cannot connect to realtime presence Redis")
            })
            .await?;
        Ok(connection.clone())
    }

    fn user_key(user_id: &str) -> String {
        format!("{USER_KEY_PREFIX}{user_id}")
    }

    fn support_visitor_key(visitor_id: &str) -> String {
        format!("{SUPPORT_VISITOR_KEY_PREFIX}{visitor_id}")
    }

    fn last_seen_key(user_id: &str) -> String {
        format!("{LAST_SEEN_KEY_PREFIX}{user_id}")
    }

    fn connection_field(session: &AuthenticatedSession, connection_id: Uuid) -> String {
        format!("{}:{}", session.device_id, connection_id)
    }

    pub async fn user_snapshot(&self, user_id: &str) -> Result<Value> {
        if self.backend == RealtimeBackend::Local {
            return Ok(self.local_user_snapshot(user_id));
        }
        let mut connection = self.connection().await?;
        self.redis_user_snapshot_with(&mut connection, user_id).await
    }

    pub async fn touch_user(
        &self,
        session: &AuthenticatedSession,
        connection_id: Uuid,
        device_type: &str,
        status: &str,
    ) -> Result<Value> {
        if session.actor_type != ActorType::User {
            return Ok(json!({}));
        }
        let touched_at = now_seconds();
        let record = DeviceRecord {
            last_seen: touched_at,
            device_type: normalize_device_type(device_type),
            presence_status: normalize_status(status),
        };
        if self.backend == RealtimeBackend::Local {
            self.local_users.insert(
                connection_id,
                LocalConnectionRecord {
                    user_id: session.actor_id.clone(),
                    record,
                },
            );
            self.local_last_seen
                .insert(session.actor_id.clone(), touched_at);
            return Ok(self.local_user_snapshot(&session.actor_id));
        }

        let key = Self::user_key(&session.actor_id);
        let field = Self::connection_field(session, connection_id);
        let encoded = serde_json::to_string(&record)?;
        let mut connection = self.connection().await?;
        let _: () = redis::pipe()
            .cmd("HSET").arg(&key).arg(&field).arg(encoded).ignore()
            .cmd("EXPIRE").arg(&key).arg(self.ttl_seconds * 2).ignore()
            .cmd("SETEX").arg(Self::last_seen_key(&session.actor_id)).arg(180 * 86_400).arg(touched_at).ignore()
            .query_async(&mut connection)
            .await?;
        self.redis_user_snapshot_with(&mut connection, &session.actor_id).await
    }

    pub async fn remove_user(
        &self,
        session: &AuthenticatedSession,
        connection_id: Uuid,
    ) -> Result<Value> {
        if session.actor_type != ActorType::User {
            return Ok(json!({}));
        }
        if self.backend == RealtimeBackend::Local {
            self.local_users.remove(&connection_id);
            self.local_last_seen
                .insert(session.actor_id.clone(), now_seconds());
            return Ok(self.local_user_snapshot(&session.actor_id));
        }

        let key = Self::user_key(&session.actor_id);
        let field = Self::connection_field(session, connection_id);
        let mut connection = self.connection().await?;
        let disconnected_at = now_seconds();
        let _: () = redis::pipe()
            .cmd("HDEL").arg(&key).arg(field).ignore()
            .cmd("SETEX").arg(Self::last_seen_key(&session.actor_id)).arg(180 * 86_400).arg(disconnected_at).ignore()
            .query_async(&mut connection)
            .await?;
        self.redis_user_snapshot_with(&mut connection, &session.actor_id).await
    }

    fn local_user_snapshot(&self, user_id: &str) -> Value {
        let now = now_seconds();
        let mut active = Vec::new();
        let mut stale = Vec::new();
        for entry in self.local_users.iter() {
            if entry.user_id != user_id {
                continue;
            }
            if now - entry.record.last_seen < self.ttl_seconds as f64 {
                active.push(entry.record.clone());
            } else {
                stale.push(*entry.key());
            }
        }
        for id in stale {
            self.local_users.remove(&id);
        }
        let last_seen = self.local_last_seen.get(user_id).map(|value| *value);
        snapshot(active, last_seen)
    }

    async fn redis_user_snapshot_with(
        &self,
        connection: &mut ConnectionManager,
        user_id: &str,
    ) -> Result<Value> {
        let key = Self::user_key(user_id);
        let (raw, last_seen): (HashMap<String, String>, Option<f64>) = redis::pipe()
            .cmd("HGETALL")
            .arg(&key)
            .cmd("GET")
            .arg(Self::last_seen_key(user_id))
            .query_async(connection)
            .await?;
        let now = now_seconds();
        let mut active = Vec::new();
        let mut stale = Vec::new();
        for (field, encoded) in raw {
            let Ok(record) = serde_json::from_str::<DeviceRecord>(&encoded) else {
                stale.push(field);
                continue;
            };
            if now - record.last_seen < self.ttl_seconds as f64 {
                active.push(record);
            } else {
                stale.push(field);
            }
        }
        if !stale.is_empty() {
            let mut command = redis::cmd("HDEL");
            command.arg(&key);
            for field in stale {
                command.arg(field);
            }
            let _: usize = command.query_async(connection).await?;
        }
        Ok(snapshot(active, last_seen))
    }

    pub async fn touch_support_visitor(
        &self,
        session: &AuthenticatedSession,
        connection_id: Uuid,
    ) -> Result<bool> {
        if session.actor_type != ActorType::SupportWidget {
            return Ok(false);
        }
        if self.backend == RealtimeBackend::Local {
            self.local_support_visitors
                .insert(connection_id, session.actor_id.clone());
            return Ok(true);
        }
        let key = Self::support_visitor_key(&session.actor_id);
        let mut connection = self.connection().await?;
        let _: usize = redis::cmd("HSET")
            .arg(&key)
            .arg(connection_id.to_string())
            .arg(now_seconds().to_string())
            .query_async(&mut connection)
            .await?;
        let _: bool = redis::cmd("EXPIRE")
            .arg(&key)
            .arg(self.ttl_seconds * 2)
            .query_async(&mut connection)
            .await?;
        Ok(true)
    }

    pub async fn remove_support_visitor(
        &self,
        session: &AuthenticatedSession,
        connection_id: Uuid,
    ) -> Result<bool> {
        if session.actor_type != ActorType::SupportWidget {
            return Ok(false);
        }
        if self.backend == RealtimeBackend::Local {
            self.local_support_visitors.remove(&connection_id);
            return Ok(self
                .local_support_visitors
                .iter()
                .any(|entry| entry.value() == &session.actor_id));
        }
        let key = Self::support_visitor_key(&session.actor_id);
        let mut connection = self.connection().await?;
        let _: usize = redis::cmd("HDEL")
            .arg(&key)
            .arg(connection_id.to_string())
            .query_async(&mut connection)
            .await?;
        let count: usize = redis::cmd("HLEN")
            .arg(&key)
            .query_async(&mut connection)
            .await?;
        Ok(count > 0)
    }

    pub async fn check(&self) -> bool {
        if self.backend == RealtimeBackend::Local {
            return true;
        }
        let Ok(mut connection) = self.connection().await else {
            return false;
        };
        let response: redis::RedisResult<String> = redis::cmd("PING")
            .query_async(&mut connection)
            .await;
        matches!(response.as_deref(), Ok("PONG"))
    }
}

fn snapshot(records: Vec<DeviceRecord>, stored_last_seen: Option<f64>) -> Value {
    let latest_record_seen = records
        .iter()
        .map(|record| record.last_seen)
        .fold(0.0_f64, f64::max);
    let last_seen_at = format_timestamp(stored_last_seen.unwrap_or(0.0).max(latest_record_seen));
    if records.is_empty() {
        return json!({
            "is_online": false,
            "active_devices": 0,
            "last_seen_at": last_seen_at,
            "presence_status": "offline",
            "presence_label": "offline",
            "device_type": Value::Null,
            "device_types": [],
        });
    }
    let actively_used: Vec<&DeviceRecord> = records
        .iter()
        .filter(|record| record.presence_status == "active")
        .collect();
    let status = if actively_used.is_empty() { "idle" } else { "active" };
    let primary = actively_used.first().copied().unwrap_or(&records[0]);
    let mut types = Vec::new();
    let mut seen = HashSet::new();
    for record in &records {
        if record.device_type != "unknown" && seen.insert(record.device_type.clone()) {
            types.push(record.device_type.clone());
        }
    }
    json!({
        "is_online": true,
        "active_devices": records.len(),
        "last_seen_at": last_seen_at,
        "presence_status": status,
        "presence_label": if status == "active" { "online" } else { "idle" },
        "device_type": if primary.device_type == "unknown" { Value::Null } else { json!(primary.device_type) },
        "device_types": types,
    })
}

fn normalize_device_type(value: &str) -> String {
    match value.trim().to_ascii_lowercase().as_str() {
        "desktop" | "mobile" | "tablet" | "widget" => value.trim().to_ascii_lowercase(),
        _ => "unknown".to_owned(),
    }
}

fn normalize_status(value: &str) -> String {
    if value.eq_ignore_ascii_case("idle") { "idle".to_owned() } else { "active".to_owned() }
}

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn format_timestamp(value: f64) -> Value {
    if value <= 0.0 {
        return Value::Null;
    }
    let seconds = value.floor() as i64;
    let nanos = ((value - seconds as f64) * 1_000_000_000.0).max(0.0) as u32;
    match OffsetDateTime::from_unix_timestamp(seconds)
        .ok()
        .and_then(|time| time.replace_nanosecond(nanos.min(999_999_999)).ok())
        .and_then(|time| time.format(&Rfc3339).ok())
    {
        Some(formatted) => json!(formatted),
        None => Value::Null,
    }
}
