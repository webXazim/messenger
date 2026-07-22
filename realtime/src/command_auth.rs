use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use axum::http::{header::AUTHORIZATION, HeaderMap};
use jsonwebtoken::{decode, Algorithm, DecodingKey, Validation};
use serde::Deserialize;
use serde_json::Value;
use thiserror::Error;

use crate::config::Config;

#[derive(Debug, Deserialize)]
struct AccessClaims {
    #[serde(default)]
    user_id: Value,
    #[serde(default)]
    sub: Value,
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
    pub claimed_user_id: Option<i64>,
    pub email: String,
}

pub struct CommandAuthenticator {
    key: DecodingKey,
    validation: Validation,
}

impl CommandAuthenticator {
    pub fn from_config(config: &Config) -> Result<Arc<Self>> {
        let algorithm = match config.chat_command_jwt_algorithm.as_str() {
            "HS256" => Algorithm::HS256,
            "HS384" => Algorithm::HS384,
            "HS512" => Algorithm::HS512,
            "RS256" => Algorithm::RS256,
            "RS384" => Algorithm::RS384,
            "RS512" => Algorithm::RS512,
            "ES256" => Algorithm::ES256,
            "ES384" => Algorithm::ES384,
            unsupported => return Err(anyhow!("unsupported CHAT_COMMAND_JWT_ALGORITHM: {unsupported}")),
        };
        let key = match algorithm {
            Algorithm::HS256 | Algorithm::HS384 | Algorithm::HS512 => {
                let secret = config.chat_command_jwt_signing_key.as_bytes();
                if secret.is_empty() {
                    return Err(anyhow!("CHAT_COMMAND_JWT_SIGNING_KEY is required for HMAC access tokens"));
                }
                DecodingKey::from_secret(secret)
            }
            Algorithm::RS256 | Algorithm::RS384 | Algorithm::RS512 => {
                DecodingKey::from_rsa_pem(config.chat_command_jwt_public_key.as_bytes())
                    .context("CHAT_COMMAND_JWT_PUBLIC_KEY is not valid RSA PEM")?
            }
            Algorithm::ES256 | Algorithm::ES384 => {
                DecodingKey::from_ec_pem(config.chat_command_jwt_public_key.as_bytes())
                    .context("CHAT_COMMAND_JWT_PUBLIC_KEY is not valid EC PEM")?
            }
            _ => unreachable!("command JWT algorithms are restricted above"),
        };
        let mut validation = Validation::new(algorithm);
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
        let raw_id = match &claims.user_id {
            Value::Number(value) => value.to_string(),
            Value::String(value) if !value.trim().is_empty() => value.trim().to_owned(),
            _ => match &claims.sub {
                Value::Number(value) => value.to_string(),
                Value::String(value) => value.trim().to_owned(),
                _ => String::new(),
            },
        };
        let claimed_user_id = raw_id.parse::<i64>().ok().filter(|value| *value > 0);
        let email = claims.email.trim().to_ascii_lowercase();
        if claimed_user_id.is_none() && email.is_empty() { return Err(CommandAuthError::Invalid); }
        Ok(CommandIdentity { claimed_user_id, email })
    }
}
