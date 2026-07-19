use std::{collections::{HashMap, HashSet}, time::{SystemTime, UNIX_EPOCH}};

use anyhow::{Context, Result};
use redis::aio::MultiplexedConnection;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use uuid::Uuid;

use crate::{auth::{ActorType, AuthenticatedSession}, config::Config};

const USER_KEY_PREFIX: &str = "realtime:presence:user:";
const RECIPIENT_KEY_PREFIX: &str = "realtime:presence:recipients:";
const SUPPORT_VISITOR_KEY_PREFIX: &str = "realtime:presence:support-visitor:";

#[derive(Clone, Debug, Deserialize, Serialize)]
struct DeviceRecord {
    last_seen: f64,
    device_type: String,
    presence_status: String,
}

#[derive(Clone, Debug)]
pub struct PresenceStore {
    client: redis::Client,
    ttl_seconds: u64,
}

impl PresenceStore {
    pub fn new(config: &Config) -> Result<Self> {
        Ok(Self {
            client: redis::Client::open(config.presence_redis_url.clone())
                .context("invalid REALTIME_PRESENCE_REDIS_URL")?,
            ttl_seconds: config.presence_ttl_seconds,
        })
    }

    async fn connection(&self) -> Result<MultiplexedConnection> {
        self.client
            .get_multiplexed_async_connection()
            .await
            .context("cannot connect to realtime presence Redis")
    }

    fn user_key(user_id: &str) -> String {
        format!("{USER_KEY_PREFIX}{user_id}")
    }

    fn support_visitor_key(visitor_id: &str) -> String {
        format!("{SUPPORT_VISITOR_KEY_PREFIX}{visitor_id}")
    }

    fn connection_field(session: &AuthenticatedSession, connection_id: Uuid) -> String {
        format!("{}:{}", session.device_id, connection_id)
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
        let key = Self::user_key(&session.actor_id);
        let field = Self::connection_field(session, connection_id);
        let record = DeviceRecord {
            last_seen: now_seconds(),
            device_type: normalize_device_type(device_type),
            presence_status: normalize_status(status),
        };
        let encoded = serde_json::to_string(&record)?;
        let mut connection = self.connection().await?;
        let _: usize = redis::cmd("HSET")
            .arg(&key)
            .arg(&field)
            .arg(encoded)
            .query_async(&mut connection)
            .await?;
        let _: bool = redis::cmd("EXPIRE")
            .arg(&key)
            .arg(self.ttl_seconds * 2)
            .query_async(&mut connection)
            .await?;
        self.user_snapshot_with(&mut connection, &session.actor_id).await
    }

    pub async fn remove_user(
        &self,
        session: &AuthenticatedSession,
        connection_id: Uuid,
    ) -> Result<Value> {
        if session.actor_type != ActorType::User {
            return Ok(json!({}));
        }
        let key = Self::user_key(&session.actor_id);
        let field = Self::connection_field(session, connection_id);
        let mut connection = self.connection().await?;
        let _: usize = redis::cmd("HDEL")
            .arg(&key)
            .arg(field)
            .query_async(&mut connection)
            .await?;
        self.user_snapshot_with(&mut connection, &session.actor_id).await
    }

    async fn user_snapshot_with(
        &self,
        connection: &mut MultiplexedConnection,
        user_id: &str,
    ) -> Result<Value> {
        let key = Self::user_key(user_id);
        let raw: HashMap<String, String> = redis::cmd("HGETALL")
            .arg(&key)
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
        Ok(snapshot(active))
    }

    pub async fn recipient_ids(&self, user_id: &str) -> Result<Vec<String>> {
        let mut connection = self.connection().await?;
        let value: Option<String> = redis::cmd("GET")
            .arg(format!("{RECIPIENT_KEY_PREFIX}{user_id}"))
            .query_async(&mut connection)
            .await?;
        Ok(value
            .and_then(|encoded| serde_json::from_str::<Vec<String>>(&encoded).ok())
            .unwrap_or_default())
    }

    pub async fn touch_support_visitor(
        &self,
        session: &AuthenticatedSession,
        connection_id: Uuid,
    ) -> Result<bool> {
        if session.actor_type != ActorType::SupportWidget {
            return Ok(false);
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
        let Ok(mut connection) = self.connection().await else {
            return false;
        };
        let response: redis::RedisResult<String> = redis::cmd("PING")
            .query_async(&mut connection)
            .await;
        matches!(response.as_deref(), Ok("PONG"))
    }
}

fn snapshot(records: Vec<DeviceRecord>) -> Value {
    if records.is_empty() {
        return json!({
            "is_online": false,
            "active_devices": 0,
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
    let primary = actively_used
        .first()
        .copied()
        .unwrap_or(&records[0]);
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
    if value.trim().eq_ignore_ascii_case("idle") {
        "idle".to_owned()
    } else {
        "active".to_owned()
    }
}

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
