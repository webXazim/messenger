use std::{collections::HashSet, fs, sync::Arc, time::{SystemTime, UNIX_EPOCH}};

use tokio::sync::OnceCell;

use anyhow::{Context, Result};
use jsonwebtoken::{decode, Algorithm, DecodingKey, Validation};
use serde::Deserialize;
use thiserror::Error;
use url::Url;

use crate::{config::Config, protocol::AudienceKey};

#[derive(Clone, Debug, Deserialize)]
pub struct TicketClaims {
    pub sub: String,
    pub actor_type: ActorType,
    #[serde(default)]
    pub username: String,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub scopes: Vec<String>,
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub device_id: String,
    #[serde(default)]
    pub device_type: String,
    #[serde(default)]
    pub origin: String,
    #[serde(default)]
    pub website_id: String,
    #[serde(default)]
    pub support_conversation_id: String,
    #[serde(default)]
    pub initial_audiences: Vec<AudienceKey>,
    #[serde(default)]
    pub presence_recipient_ids: Vec<String>,
    pub jti: String,
    pub exp: usize,
    pub token_use: String,
    pub protocol_version: u8,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ActorType {
    User,
    SupportWidget,
    InternalTest,
}

impl ActorType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::SupportWidget => "support_widget",
            Self::InternalTest => "internal_test",
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
struct GrantClaims {
    sub: String,
    actor_type: ActorType,
    #[serde(default)]
    origin: String,
    audience_key: AudienceKey,
    token_use: String,
    protocol_version: u8,
}

#[derive(Clone, Debug, Deserialize)]
struct CallGrantClaims {
    sub: String,
    actor_type: ActorType,
    #[serde(default)]
    origin: String,
    call_id: String,
    conversation_id: String,
    #[serde(default)]
    participant_ids: Vec<String>,
    token_use: String,
    protocol_version: u8,
}

#[derive(Clone, Debug)]
pub struct AuthenticatedSession {
    pub actor_id: String,
    pub actor_type: ActorType,
    pub username: String,
    pub display_name: String,
    pub scopes: Vec<String>,
    pub session_id: String,
    pub device_id: String,
    pub device_type: String,
    pub origin: String,
    pub website_id: String,
    pub support_conversation_id: String,
    pub initial_audiences: Vec<AudienceKey>,
    pub presence_recipient_ids: Vec<String>,
    pub require_grants: bool,
}

impl AuthenticatedSession {
    pub fn internal_test() -> Self {
        Self {
            actor_id: "internal-test".to_owned(),
            actor_type: ActorType::InternalTest,
            username: "internal-test".to_owned(),
            display_name: "Internal test".to_owned(),
            scopes: vec!["internal_test".to_owned()],
            session_id: String::new(),
            device_id: "internal-test".to_owned(),
            device_type: "test".to_owned(),
            origin: String::new(),
            website_id: String::new(),
            support_conversation_id: String::new(),
            initial_audiences: Vec::new(),
            presence_recipient_ids: Vec::new(),
            require_grants: false,
        }
    }

    pub fn has_scope(&self, scope: &str) -> bool {
        self.scopes.iter().any(|value| value == scope)
    }
}

#[derive(Debug, Error)]
pub enum AuthError {
    #[error("realtime authentication is disabled")]
    Disabled,
    #[error("the realtime ticket is missing or invalid")]
    InvalidTicket,
    #[error("the realtime ticket has already been used")]
    TicketReplay,
    #[error("the browser origin is not allowed")]
    OriginDenied,
    #[error("the realtime grant is missing or invalid")]
    InvalidGrant,
    #[error("realtime authentication storage is unavailable")]
    StorageUnavailable,
}

pub struct Authenticator {
    decoding_key: DecodingKey,
    ticket_validation: Validation,
    grant_validation: Validation,
    call_grant_validation: Validation,
    redis_client: redis::Client,
    redis_connection: OnceCell<redis::aio::ConnectionManager>,
    replay_prefix: String,
    require_origin: bool,
    allowed_origins: HashSet<String>,
}

impl Authenticator {
    pub fn from_config(config: &Config) -> Result<Option<Arc<Self>>> {
        if !config.auth_enabled {
            return Ok(None);
        }
        let public_key = if !config.auth_public_key.trim().is_empty() {
            config.auth_public_key.as_bytes().to_vec()
        } else {
            fs::read(&config.auth_public_key_path).with_context(|| {
                format!("cannot read REALTIME_SIGNING_PUBLIC_KEY_PATH: {}", config.auth_public_key_path)
            })?
        };
        let decoding_key = DecodingKey::from_rsa_pem(&public_key)
            .context("REALTIME signing public key is not valid RSA PEM")?;

        let mut ticket_validation = Validation::new(Algorithm::RS256);
        ticket_validation.set_issuer(&[config.token_issuer.as_str()]);
        ticket_validation.set_audience(&[config.ticket_audience.as_str()]);
        ticket_validation.validate_nbf = true;
        ticket_validation.leeway = config.auth_leeway_seconds;

        let mut grant_validation = Validation::new(Algorithm::RS256);
        grant_validation.set_issuer(&[config.token_issuer.as_str()]);
        grant_validation.set_audience(&[config.grant_audience.as_str()]);
        grant_validation.validate_nbf = true;
        grant_validation.leeway = config.auth_leeway_seconds;

        let mut call_grant_validation = Validation::new(Algorithm::RS256);
        call_grant_validation.set_issuer(&[config.token_issuer.as_str()]);
        call_grant_validation.set_audience(&[config.call_grant_audience.as_str()]);
        call_grant_validation.validate_nbf = true;
        call_grant_validation.leeway = config.auth_leeway_seconds;

        let redis_client = redis::Client::open(config.auth_redis_url.clone())
            .context("invalid REALTIME_AUTH_REDIS_URL")?;
        Ok(Some(Arc::new(Self {
            decoding_key,
            ticket_validation,
            grant_validation,
            call_grant_validation,
            redis_client,
            redis_connection: OnceCell::new(),
            replay_prefix: config.ticket_replay_prefix.clone(),
            require_origin: config.require_origin,
            allowed_origins: normalize_allowed_origins(&config.allowed_origins)?,
        })))
    }

    pub async fn authenticate_ticket(
        &self,
        token: &str,
        request_origin: Option<&str>,
    ) -> Result<AuthenticatedSession, AuthError> {
        let claims = decode::<TicketClaims>(token, &self.decoding_key, &self.ticket_validation)
            .map_err(|_| AuthError::InvalidTicket)?
            .claims;
        let normalized_origin = self.validate_request_origin(request_origin)?;
        if claims.token_use != "realtime_ticket"
            || claims.protocol_version != 1
            || claims.sub.trim().is_empty()
            || claims.jti.trim().is_empty()
        {
            return Err(AuthError::InvalidTicket);
        }
        let ticket_origin = normalize_claim_origin(&claims.origin, self.require_origin)
            .map_err(|_| AuthError::OriginDenied)?;
        if ticket_origin != normalized_origin {
            return Err(AuthError::OriginDenied);
        }
        if claims.actor_type == ActorType::User
            && !self.allowed_origins.is_empty()
            && !self.allowed_origins.contains(&normalized_origin)
        {
            return Err(AuthError::OriginDenied);
        }
        validate_initial_audiences(&claims)?;
        self.consume_ticket(&claims.jti, claims.exp).await?;

        Ok(AuthenticatedSession {
            actor_id: claims.sub,
            actor_type: claims.actor_type,
            username: claims.username,
            display_name: claims.display_name,
            scopes: claims.scopes,
            session_id: claims.session_id,
            device_id: claims.device_id,
            device_type: claims.device_type,
            origin: ticket_origin,
            website_id: claims.website_id,
            support_conversation_id: claims.support_conversation_id,
            initial_audiences: claims.initial_audiences,
            presence_recipient_ids: claims.presence_recipient_ids,
            require_grants: true,
        })
    }

    pub fn validate_grant(
        &self,
        token: &str,
        session: &AuthenticatedSession,
        requested_audience: &AudienceKey,
    ) -> Result<(), AuthError> {
        let claims = decode::<GrantClaims>(token, &self.decoding_key, &self.grant_validation)
            .map_err(|_| AuthError::InvalidGrant)?
            .claims;
        if claims.token_use != "realtime_grant"
            || claims.protocol_version != 1
            || claims.actor_type != session.actor_type
            || claims.sub != session.actor_id
            || claims.audience_key != *requested_audience
        {
            return Err(AuthError::InvalidGrant);
        }
        let grant_origin = normalize_claim_origin(&claims.origin, self.require_origin)
            .map_err(|_| AuthError::InvalidGrant)?;
        if grant_origin != session.origin {
            return Err(AuthError::InvalidGrant);
        }
        Ok(())
    }

    pub fn validate_call_grant(
        &self,
        token: &str,
        session: &AuthenticatedSession,
        call_id: &str,
        conversation_id: &str,
        target_user_id: Option<&str>,
    ) -> Result<(), AuthError> {
        let claims = decode::<CallGrantClaims>(
            token,
            &self.decoding_key,
            &self.call_grant_validation,
        )
        .map_err(|_| AuthError::InvalidGrant)?
        .claims;
        if claims.token_use != "realtime_call_grant"
            || claims.protocol_version != 1
            || claims.actor_type != ActorType::User
            || claims.actor_type != session.actor_type
            || claims.sub != session.actor_id
            || claims.call_id != call_id
            || claims.conversation_id != conversation_id
            || !claims.participant_ids.iter().any(|value| value == &session.actor_id)
        {
            return Err(AuthError::InvalidGrant);
        }
        let grant_origin = normalize_claim_origin(&claims.origin, self.require_origin)
            .map_err(|_| AuthError::InvalidGrant)?;
        if grant_origin != session.origin {
            return Err(AuthError::InvalidGrant);
        }
        if let Some(target) = target_user_id.filter(|value| !value.is_empty()) {
            if !claims.participant_ids.iter().any(|value| value == target) {
                return Err(AuthError::InvalidGrant);
            }
        }
        Ok(())
    }

    async fn connection(&self) -> Result<redis::aio::ConnectionManager, AuthError> {
        let connection = self
            .redis_connection
            .get_or_try_init(|| async {
                self.redis_client
                    .get_connection_manager()
                    .await
                    .map_err(|_| AuthError::StorageUnavailable)
            })
            .await?;
        Ok(connection.clone())
    }

    pub async fn check_storage(&self) -> bool {
        let Ok(mut connection) = self.connection().await else {
            return false;
        };
        let response: redis::RedisResult<String> = redis::cmd("PING")
            .query_async(&mut connection)
            .await;
        matches!(response.as_deref(), Ok("PONG"))
    }

    fn validate_request_origin(&self, request_origin: Option<&str>) -> Result<String, AuthError> {
        match request_origin.map(str::trim).filter(|value| !value.is_empty()) {
            Some(raw) => normalize_origin(raw).ok_or(AuthError::OriginDenied),
            None if self.require_origin => Err(AuthError::OriginDenied),
            None => Ok(String::new()),
        }
    }

    async fn consume_ticket(&self, jti: &str, exp: usize) -> Result<(), AuthError> {
        let now = epoch_seconds();
        if exp <= now {
            return Err(AuthError::InvalidTicket);
        }
        let ttl = exp.saturating_sub(now).saturating_add(30).max(30);
        let key = format!("{}{}", self.replay_prefix, jti);
        let mut connection = self.connection().await?;
        let result: Option<String> = redis::cmd("SET")
            .arg(key)
            .arg("1")
            .arg("NX")
            .arg("EX")
            .arg(ttl)
            .query_async(&mut connection)
            .await
            .map_err(|_| AuthError::StorageUnavailable)?;
        if result.as_deref() != Some("OK") {
            return Err(AuthError::TicketReplay);
        }
        Ok(())
    }
}

fn normalize_allowed_origins(values: &[String]) -> Result<HashSet<String>> {
    let mut normalized = HashSet::new();
    for raw in values {
        let origin = normalize_origin(raw)
            .with_context(|| format!("invalid origin in REALTIME_ALLOWED_ORIGINS: {raw}"))?;
        normalized.insert(origin);
    }
    Ok(normalized)
}

fn normalize_claim_origin(raw: &str, required: bool) -> Result<String, AuthError> {
    if raw.trim().is_empty() {
        return if required { Err(AuthError::OriginDenied) } else { Ok(String::new()) };
    }
    normalize_origin(raw).ok_or(AuthError::OriginDenied)
}

fn validate_initial_audiences(claims: &TicketClaims) -> Result<(), AuthError> {
    for audience in &claims.initial_audiences {
        if !audience.validate() {
            return Err(AuthError::InvalidTicket);
        }
        let permitted = match claims.actor_type {
            ActorType::User => matches!(
                audience.kind,
                crate::protocol::AudienceKind::User | crate::protocol::AudienceKind::SupportUser
            ) && audience.identifier == claims.sub,
            ActorType::SupportWidget => {
                audience.kind == crate::protocol::AudienceKind::SupportVisitor
                    && audience.identifier == claims.sub
            }
            ActorType::InternalTest => false,
        };
        if !permitted {
            return Err(AuthError::InvalidTicket);
        }
    }
    Ok(())
}

pub fn normalize_origin(raw: &str) -> Option<String> {
    let parsed = Url::parse(raw.trim()).ok()?;
    if parsed.scheme() != "http" && parsed.scheme() != "https" {
        return None;
    }
    let host = parsed.host_str()?.trim_end_matches('.').to_ascii_lowercase();
    let port = parsed.port();
    let default_port = (parsed.scheme() == "https" && port == Some(443))
        || (parsed.scheme() == "http" && port == Some(80));
    let authority = if port.is_none() || default_port { host } else { format!("{}:{}", host, port?) };
    Some(format!("{}://{}", parsed.scheme(), authority))
}

fn epoch_seconds() -> usize {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as usize
}
