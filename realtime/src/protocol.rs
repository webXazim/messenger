use std::{fmt, sync::Arc};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use uuid::Uuid;

#[derive(Clone, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
pub struct AudienceKey {
    pub kind: AudienceKind,
    #[serde(rename = "id")]
    pub identifier: String,
}

impl AudienceKey {
    pub fn validate(&self) -> bool {
        !self.identifier.trim().is_empty() && self.identifier.len() <= 160
    }
}

impl fmt::Display for AudienceKey {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}:{}", self.kind, self.identifier)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AudienceKind {
    Conversation,
    User,
    SupportWebsite,
    SupportVisitor,
    SupportUser,
}

impl fmt::Display for AudienceKind {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Conversation => "conversation",
            Self::User => "user",
            Self::SupportWebsite => "support_website",
            Self::SupportVisitor => "support_visitor",
            Self::SupportUser => "support_user",
        })
    }
}

#[derive(Debug, Deserialize)]
pub struct ClientCommand {
    #[serde(default = "protocol_version", alias = "v")]
    pub version: u8,
    pub event: String,
    #[serde(default)]
    pub request_id: Option<String>,
    #[serde(default)]
    pub data: Value,
}

const fn protocol_version() -> u8 {
    1
}

#[derive(Debug, Deserialize)]
pub struct SubscriptionData {
    pub audience: AudienceKey,
    #[serde(default)]
    pub grant: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ServerControl<'a> {
    pub r#type: &'static str,
    pub version: u8,
    pub event: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<&'a str>,
    pub data: Value,
}

#[derive(Debug, Serialize)]
struct ServerEvent<'a> {
    r#type: &'static str,
    version: u8,
    event: &'a str,
    event_id: String,
    occurred_at: String,
    data: Value,
}

#[derive(Clone, Debug)]
pub enum OutboundMessage {
    Text(Arc<str>),
    Pong(Vec<u8>),
    Close { code: u16, reason: Arc<str> },
}

pub fn control_message(
    event: &str,
    request_id: Option<&str>,
    data: Value,
) -> Result<Arc<str>, serde_json::Error> {
    serde_json::to_string(&ServerControl {
        r#type: "realtime.control",
        version: 1,
        event,
        request_id,
        data,
    })
    .map(Arc::<str>::from)
}

pub fn event_message(event: &str, data: Value) -> Result<Arc<str>, serde_json::Error> {
    let occurred_at = OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_else(|_| OffsetDateTime::now_utc().unix_timestamp().to_string());
    serde_json::to_string(&ServerEvent {
        r#type: "chat.event",
        version: 1,
        event,
        event_id: Uuid::new_v4().to_string(),
        occurred_at,
        data,
    })
    .map(Arc::<str>::from)
}
