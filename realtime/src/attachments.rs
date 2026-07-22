use std::sync::Arc;

use anyhow::{Context, Result};
use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use serde::Serialize;
use serde_json::{json, Value};
use sqlx::{Postgres, Transaction};
use time::OffsetDateTime;
use uuid::Uuid;

use crate::{
    command_auth::{CommandAuthError, CommandIdentity},
    command_delivery::deliver_committed,
    commands::{error_response, SendMessageRequest},
    config::ChatAttachmentBackend,
    database::Database,
    state::AppState,
};

#[derive(Debug, Serialize)]
struct MediaClaims {
    iss: String,
    aud: String,
    sub: String,
    iat: usize,
    exp: usize,
    jti: String,
    token_type: &'static str,
    resource_type: &'static str,
    resource_id: String,
    purpose: &'static str,
}

fn enabled(state: &AppState) -> bool {
    matches!(
        state.config.chat_attachment_backend,
        ChatAttachmentBackend::SqlxShadow | ChatAttachmentBackend::Axum
    )
}

fn authenticate(
    state: &AppState,
    headers: &HeaderMap,
) -> Result<CommandIdentity, axum::response::Response> {
    state.command_auth.authenticate(headers).map_err(|error| {
        let detail = match error {
            CommandAuthError::Missing => "Authentication credentials were not provided.",
            _ => "Authentication credentials are invalid or expired.",
        };
        error_response(StatusCode::UNAUTHORIZED, "authentication_failed", detail)
    })
}

fn attachment_error(error: anyhow::Error, operation: &'static str) -> axum::response::Response {
    let message = error.to_string();
    tracing::warn!(error = %error, operation, "Axum attachment operation rejected");
    if let Some(rest) = message.strip_prefix("validation|") {
        let mut parts = rest.splitn(2, '|');
        let code = parts.next().unwrap_or("invalid_attachment_operation");
        let status = if code == "message_rate_limited" { StatusCode::TOO_MANY_REQUESTS } else { StatusCode::BAD_REQUEST };
        return error_response(
            status,
            code,
            parts.next().unwrap_or("The attachment operation is invalid."),
        );
    }
    if message.contains("authenticated user does not exist") {
        error_response(StatusCode::UNAUTHORIZED, "authentication_failed", "The authenticated account is not available.")
    } else if message.contains("not an active participant") || message.contains("attachment was not found") {
        error_response(StatusCode::NOT_FOUND, "attachment_not_found", "Attachment was not found.")
    } else if message.contains("participant is blocked") || message.contains("direct conversation is blocked") || message.contains("participant is muted") {
        error_response(StatusCode::FORBIDDEN, "attachment_forbidden", "Your participation in this conversation is restricted.")
    } else if message.contains("upload") {
        error_response(StatusCode::BAD_REQUEST, "upload_unavailable", &message)
    } else {
        tracing::error!(error = %error, operation, "Axum SQLx attachment operation failed");
        error_response(StatusCode::INTERNAL_SERVER_ERROR, "attachment_operation_failed", "The attachment operation could not be completed.")
    }
}

pub(crate) async fn send_attachment_message(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<SendMessageRequest>,
) -> impl IntoResponse {
    if !enabled(&state) {
        return error_response(StatusCode::NOT_FOUND, "axum_attachments_disabled", "Axum attachment operations are not active.");
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    match state.database.send_chat_message(conversation_id, &identity, input).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            let status = if result.was_deduplicated { StatusCode::OK } else { StatusCode::CREATED };
            (status, Json(result.payload)).into_response()
        }
        Err(error) => attachment_error(error, "send_attachment_message"),
    }
}

pub(crate) async fn get_attachment(
    State(state): State<Arc<AppState>>,
    Path(attachment_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !enabled(&state) {
        return error_response(StatusCode::NOT_FOUND, "axum_attachments_disabled", "Axum attachment operations are not active.");
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    match state.database.get_attachment_metadata(attachment_id, &identity).await {
        Ok(payload) => (StatusCode::OK, Json(payload)).into_response(),
        Err(error) => attachment_error(error, "get_attachment"),
    }
}

pub(crate) async fn create_media_token(
    State(state): State<Arc<AppState>>,
    Path(attachment_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !enabled(&state) {
        return error_response(StatusCode::NOT_FOUND, "axum_attachments_disabled", "Axum attachment operations are not active.");
    }
    let identity = match authenticate(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let actor_id = match state.database.authorize_attachment_token(attachment_id, &identity).await {
        Ok(actor_id) => actor_id,
        Err(error) => return attachment_error(error, "create_media_token"),
    };
    let now = OffsetDateTime::now_utc().unix_timestamp().max(0) as usize;
    let expires = now.saturating_add(state.config.media_token_ttl_seconds as usize);
    let claims = MediaClaims {
        iss: state.config.media_token_issuer.clone(),
        aud: state.config.media_token_audience.clone(),
        sub: actor_id.to_string(),
        iat: now,
        exp: expires,
        jti: Uuid::new_v4().to_string(),
        token_type: "media_access",
        resource_type: "attachment",
        resource_id: attachment_id.to_string(),
        purpose: "standard",
    };
    let token = match encode(
        &Header::new(Algorithm::HS256),
        &claims,
        &EncodingKey::from_secret(state.config.media_token_signing_key.as_bytes()),
    ) {
        Ok(token) => token,
        Err(error) => {
            tracing::error!(error = %error, "Could not issue Axum media token");
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, "media_token_failed", "A media access token could not be created.");
        }
    };
    if let Err(error) = state.database.audit_media_token(actor_id, attachment_id).await {
        tracing::warn!(error = %error, %attachment_id, "Media token audit write failed");
    }
    let base = format!("/api/v1/chat/attachments/{attachment_id}");
    let payload = json!({
        "token": token,
        "expires_in": state.config.media_token_ttl_seconds,
        "url": format!("{base}/download/?token={token}"),
        "download_url": format!("{base}/download/?token={token}"),
        "preview_url": format!("{base}/preview/?token={token}"),
        "disposition": "attachment",
        "resource_type": "attachment",
        "resource_id": attachment_id.to_string(),
    });
    (StatusCode::OK, Json(payload)).into_response()
}

async fn resolve_actor_id(
    tx: &mut Transaction<'_, Postgres>,
    identity: &CommandIdentity,
) -> Result<i64> {
    if let Some(user_id) = identity.claimed_user_id {
        if let Some(id) = sqlx::query_scalar::<_, i64>(
            "SELECT id FROM accounts_user WHERE id = $1 AND is_active = TRUE",
        )
        .bind(user_id)
        .persistent(false)
        .fetch_optional(&mut **tx)
        .await?
        {
            return Ok(id);
        }
    }
    if !identity.email.is_empty() {
        if let Some(id) = sqlx::query_scalar::<_, i64>(
            "SELECT id FROM accounts_user WHERE LOWER(email) = $1 AND is_active = TRUE LIMIT 1",
        )
        .bind(&identity.email)
        .persistent(false)
        .fetch_optional(&mut **tx)
        .await?
        {
            return Ok(id);
        }
    }
    anyhow::bail!("authenticated user does not exist locally")
}

fn attachment_json_expression() -> &'static str {
    r#"jsonb_build_object(
        'id', a.id::text,
        'media_kind', a.media_kind,
        'original_name', a.original_name,
        'mime_type', a.mime_type,
        'size', a.size,
        'width', a.width,
        'height', a.height,
        'rotation', a.rotation,
        'duration_seconds', a.duration_seconds,
        'aspect_ratio', CASE WHEN a.width IS NOT NULL AND a.height IS NOT NULL AND a.height > 0 THEN a.width::double precision / a.height::double precision ELSE NULL END,
        'metadata', COALESCE(a.metadata, '{}'::jsonb) - 'encrypted_attachment' - 'encryption',
        'thumbnail_url', CASE WHEN a.view_once OR COALESCE(a.thumbnail, '') = '' THEN NULL ELSE '/api/v1/chat/attachments/' || a.id::text || '/thumbnail/' END,
        'scan_status', a.scan_status,
        'scan_notes', a.scan_notes,
        'scanned_at', a.scanned_at,
        'file_url', CASE WHEN a.view_once THEN '' ELSE '/api/v1/chat/attachments/' || a.id::text || '/download/' END,
        'download_url', CASE WHEN a.view_once THEN '' ELSE '/api/v1/chat/attachments/' || a.id::text || '/download/' END,
        'preview_url', CASE WHEN a.view_once THEN '' ELSE '/api/v1/chat/attachments/' || a.id::text || '/preview/' END,
        'can_preview_inline', CASE WHEN a.view_once THEN false ELSE (a.mime_type LIKE 'image/%' OR a.mime_type LIKE 'audio/%' OR a.mime_type LIKE 'video/%' OR a.mime_type = 'application/pdf') END,
        'signed_download', NULL,
        'signed_preview', NULL,
        'is_encrypted', COALESCE(a.metadata->'encrypted_attachment' = 'true'::jsonb, false),
        'encryption', COALESCE(a.metadata->'encryption', '{}'::jsonb),
        'view_once', a.view_once,
        'view_once_opened', EXISTS(SELECT 1 FROM chat_messageattachmentviewreceipt avr WHERE avr.attachment_id = a.id AND avr.user_id = $2),
        'can_open_view_once', a.view_once AND m.sender_id IS DISTINCT FROM $2 AND NOT EXISTS(SELECT 1 FROM chat_messageattachmentviewreceipt avr WHERE avr.attachment_id = a.id AND avr.user_id = $2)
    )"#
}

impl Database {
    pub(crate) async fn get_attachment_metadata(
        &self,
        attachment_id: Uuid,
        identity: &CommandIdentity,
    ) -> Result<Value> {
        let pool = self.pool.as_ref().context("SQLx attachment backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let sql = format!(
            "SELECT {attachment} FROM chat_messageattachment a JOIN chat_message m ON m.id = a.message_id JOIN chat_conversationparticipant cp ON cp.conversation_id = m.conversation_id AND cp.user_id = $2 AND cp.left_at IS NULL AND cp.banned_at IS NULL WHERE a.id = $1 AND a.scan_status = 'clean' AND NOT m.is_deleted",
            attachment = attachment_json_expression(),
        );
        let payload = sqlx::query_scalar::<_, Value>(&sql)
            .bind(attachment_id)
            .bind(actor_id)
            .persistent(false)
            .fetch_optional(&mut *tx)
            .await?
            .context("attachment was not found")?;
        tx.commit().await?;
        Ok(payload)
    }

    pub(crate) async fn authorize_attachment_token(
        &self,
        attachment_id: Uuid,
        identity: &CommandIdentity,
    ) -> Result<i64> {
        let pool = self.pool.as_ref().context("SQLx attachment backend is disabled")?;
        let mut tx = pool.begin().await?;
        let actor_id = resolve_actor_id(&mut tx, identity).await?;
        let allowed = sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM chat_messageattachment a JOIN chat_message m ON m.id = a.message_id JOIN chat_conversationparticipant cp ON cp.conversation_id = m.conversation_id AND cp.user_id = $2 AND cp.left_at IS NULL AND cp.banned_at IS NULL WHERE a.id = $1 AND a.scan_status = 'clean' AND NOT m.is_deleted AND NOT a.view_once)",
        )
        .bind(attachment_id)
        .bind(actor_id)
        .persistent(false)
        .fetch_one(&mut *tx)
        .await?;
        if !allowed { anyhow::bail!("attachment was not found"); }
        tx.commit().await?;
        Ok(actor_id)
    }

    pub(crate) async fn audit_media_token(&self, actor_id: i64, attachment_id: Uuid) -> Result<()> {
        let pool = self.pool.as_ref().context("SQLx attachment backend is disabled")?;
        sqlx::query(
            "INSERT INTO chat_chatauditlog (id, created_at, updated_at, actor_id, conversation_id, message_id, event_type, metadata) SELECT $1, NOW(), NOW(), $2, m.conversation_id, m.id, 'media_token_issued', jsonb_build_object('resource_type', 'attachment', 'resource_id', a.id::text, 'source', 'axum_sqlx') FROM chat_messageattachment a JOIN chat_message m ON m.id = a.message_id WHERE a.id = $3",
        )
        .bind(Uuid::new_v4())
        .bind(actor_id)
        .bind(attachment_id)
        .persistent(false)
        .execute(pool)
        .await?;
        Ok(())
    }
}
