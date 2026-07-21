use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};
use uuid::Uuid;

use crate::{config::ChatCommandBackend, state::AppState};

#[derive(Debug, Deserialize)]
pub struct SendMessageRequest {
    #[serde(default)]
    text: String,
    #[serde(default = "default_message_type", rename = "type")]
    message_type: String,
    #[serde(default)]
    client_temp_id: String,
    #[serde(default)]
    reply_to_id: Option<Uuid>,
    #[serde(default)]
    attachment_ids: Vec<Uuid>,
    #[serde(default)]
    encryption: Option<Value>,
    #[serde(default)]
    entities: Vec<Value>,
}

fn default_message_type() -> String { "text".to_owned() }

pub async fn send_message(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<SendMessageRequest>,
) -> impl IntoResponse {
    if state.config.chat_command_backend != ChatCommandBackend::Axum {
        return error(StatusCode::NOT_FOUND, "axum_chat_commands_disabled", "Axum chat commands are not active.");
    }
    let identity = match state.command_auth.authenticate(&headers) {
        Ok(value) => value,
        Err(_) => return error(StatusCode::UNAUTHORIZED, "authentication_failed", "Authentication credentials were not provided or are invalid."),
    };
    if input.message_type != "text" || input.reply_to_id.is_some() || !input.attachment_ids.is_empty() || input.encryption.is_some() || !input.entities.is_empty() {
        return error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "django_fallback_required",
            "This message requires the Django compatibility path.",
        );
    }
    let text = input.text.trim().to_owned();
    if text.is_empty() || text.chars().count() > 20_000 {
        return error(StatusCode::BAD_REQUEST, "invalid_text", "Message text must contain between 1 and 20,000 characters.");
    }
    let client_temp_id = input.client_temp_id.trim().chars().take(100).collect::<String>();
    match state.database.send_text_message(conversation_id, &identity, &text, &client_temp_id).await {
        Ok(result) => {
            let status = if result.was_deduplicated { StatusCode::OK } else { StatusCode::CREATED };
            (status, Json(result.payload)).into_response()
        }
        Err(error) => {
            tracing::warn!(%conversation_id, claimed_actor_id = ?identity.claimed_user_id, error = %error, "Axum message command rejected");
            let message = error.to_string();
            if message.contains("not an active participant") {
                error_response(StatusCode::NOT_FOUND, "conversation_not_found", "Conversation was not found.")
            } else if message.contains("muted") || message.contains("blocked") {
                error_response(StatusCode::FORBIDDEN, "sending_not_allowed", "You cannot send messages in this conversation.")
            } else {
                error_response(StatusCode::INTERNAL_SERVER_ERROR, "message_send_failed", "The message could not be sent.")
            }
        }
    }
}

fn error(status: StatusCode, code: &str, detail: &str) -> axum::response::Response {
    error_response(status, code, detail)
}

fn error_response(status: StatusCode, code: &str, detail: &str) -> axum::response::Response {
    (status, Json(json!({"code": code, "detail": detail}))).into_response()
}
