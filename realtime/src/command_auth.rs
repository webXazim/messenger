use std::{fs, sync::Arc};

use anyhow::{Context, Result};
use axum::http::{header::AUTHORIZATION, HeaderMap};
use jsonwebtoken::{decode, Algorithm, DecodingKey, Validation};
use serde::Deserialize;
use thiserror::Error;
use uuid::Uuid;

use crate::config::Config;

#[derive(Debug, Deserialize)]
struct AccessClaims {
    #[serde(default)]
    user_id: String,
    #[serde(default)]
    sub: String,
    #[serde(default)]
    token_type: String,
    #[serde(default)]
    email: String,
}

#[derive(Debug, Error)]
pub enum CommandAuthError {
    #[error("authorization token is missing")]
    Missing,
    #[error("authorization token is invalid")]
    Invalid,
}

#[derive(Clone, Debug)]
pub struct CommandIdentity {
    pub claimed_user_id: Option<Uuid>,
    pub email: String,
}

pub struct CommandAuthenticator {
    key: DecodingKey,
    validation: Validation,
}

impl CommandAuthenticator {
    pub fn from_config(config: &Config) -> Result<Arc<Self>> {
        let public_key = if !config.auth_public_key.trim().is_empty() {
            config.auth_public_key.as_bytes().to_vec()
        } else {
            fs::read(&config.auth_public_key_path).with_context(|| {
                format!("cannot read command JWT public key: {}", config.auth_public_key_path)
            })?
        };
        let key = DecodingKey::from_rsa_pem(&public_key).context("command JWT public key is invalid")?;
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_issuer(&[config.chat_command_jwt_issuer.as_str()]);
        if !config.chat_command_jwt_audience.trim().is_empty() {
            validation.set_audience(&[config.chat_command_jwt_audience.as_str()]);
        } else {
            validation.validate_aud = false;
        }
        validation.validate_nbf = true;
        validation.leeway = config.auth_leeway_seconds;
        Ok(Arc::new(Self { key, validation }))
    }

    pub fn authenticate(&self, headers: &HeaderMap) -> Result<CommandIdentity, CommandAuthError> {
        let value = headers.get(AUTHORIZATION).and_then(|v| v.to_str().ok()).ok_or(CommandAuthError::Missing)?;
        let token = value.strip_prefix("Bearer ").or_else(|| value.strip_prefix("bearer ")).ok_or(CommandAuthError::Invalid)?;
        let claims = decode::<AccessClaims>(token, &self.key, &self.validation).map_err(|_| CommandAuthError::Invalid)?.claims;
        if !claims.token_type.is_empty() && claims.token_type != "access" { return Err(CommandAuthError::Invalid); }
        let raw_id = if claims.user_id.trim().is_empty() { &claims.sub } else { &claims.user_id };
        let claimed_user_id = Uuid::parse_str(raw_id).ok();
        let email = claims.email.trim().to_ascii_lowercase();
        if claimed_user_id.is_none() && email.is_empty() { return Err(CommandAuthError::Invalid); }
        Ok(CommandIdentity { claimed_user_id, email })
    }
}
