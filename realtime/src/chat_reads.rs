use std::{fmt::Write as _, sync::Arc};

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::{Postgres, QueryBuilder, Transaction};
use uuid::Uuid;

use crate::{
    command_auth::CommandIdentity,
    commands::error_response,
    config::ChatReadBackend,
    database::Database,
    state::AppState,
};

const PAGE_SIZE: i64 = 30;
const MAX_PAGE_SIZE: i64 = 100;

#[derive(Debug, Deserialize)]
pub(crate) struct PageParams {
    #[serde(default)]
    cursor: String,
    #[serde(default)]
    page_size: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct MediaParams {
    #[serde(default)]
    cursor: String,
    #[serde(default)]
    page_size: Option<i64>,
    #[serde(default = "default_media_kind")]
    kind: String,
}

fn default_media_kind() -> String {
    "all".to_owned()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct TimeCursor {
    at: String,
    id: Uuid,
}

#[derive(Debug)]
pub(crate) struct ReadPage {
    pub results: Vec<Value>,
    pub next_cursor: Option<String>,
}

fn page_size(value: Option<i64>) -> i64 {
    value.unwrap_or(PAGE_SIZE).clamp(1, MAX_PAGE_SIZE)
}

fn encode_cursor(cursor: &TimeCursor) -> anyhow::Result<String> {
    let bytes = serde_json::to_vec(cursor)?;
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(&mut encoded, "{byte:02x}")?;
    }
    Ok(encoded)
}

fn decode_cursor(raw: &str) -> anyhow::Result<Option<TimeCursor>> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Ok(None);
    }
    if raw.len() % 2 != 0 || !raw.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        anyhow::bail!("cursor is not valid hexadecimal");
    }
    let bytes = raw
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| -> anyhow::Result<u8> {
            let value = std::str::from_utf8(pair)?;
            Ok(u8::from_str_radix(value, 16)?)
        })
        .collect::<anyhow::Result<Vec<_>>>()?;
    Ok(Some(serde_json::from_slice(&bytes)?))
}

fn extract_cursor(value: &mut Value) -> anyhow::Result<TimeCursor> {
    let object = value
        .as_object_mut()
        .ok_or_else(|| anyhow::anyhow!("read row is not a JSON object"))?;
    let at = object
        .remove("_cursor_at")
        .and_then(|value| value.as_str().map(ToOwned::to_owned))
        .ok_or_else(|| anyhow::anyhow!("read row is missing cursor timestamp"))?;
    let id = object
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("read row is missing id"))?
        .parse::<Uuid>()?;
    Ok(TimeCursor { at, id })
}

fn page_response(path: &str, page: ReadPage) -> axum::response::Response {
    let next = page
        .next_cursor
        .map(|cursor| format!("{path}?cursor={cursor}"));
    (
        StatusCode::OK,
        Json(json!({
            "next": next,
            "previous": Value::Null,
            "results": page.results,
        })),
    )
        .into_response()
}

fn auth_identity(state: &AppState, headers: &HeaderMap) -> Result<CommandIdentity, axum::response::Response> {
    state.command_auth.authenticate(headers).map_err(|_| {
        error_response(
            StatusCode::UNAUTHORIZED,
            "authentication_failed",
            "Authentication credentials were not provided or are invalid.",
        )
    })
}

fn ensure_enabled(state: &AppState) -> Result<(), axum::response::Response> {
    if matches!(
        state.config.chat_read_backend,
        ChatReadBackend::Sqlx | ChatReadBackend::SqlxShadow
    ) {
        Ok(())
    } else {
        Err(error_response(
            StatusCode::NOT_FOUND,
            "axum_chat_reads_disabled",
            "Axum chat reads are not active.",
        ))
    }
}

fn database_error(error: anyhow::Error, operation: &'static str) -> axum::response::Response {
    tracing::error!(error = %error, operation, "Axum SQLx chat read failed");
    error_response(
        StatusCode::INTERNAL_SERVER_ERROR,
        "chat_read_failed",
        "The chat data could not be loaded.",
    )
}

pub(crate) async fn list_conversations(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<PageParams>,
) -> impl IntoResponse {
    if let Err(response) = ensure_enabled(&state) {
        return response;
    }
    let identity = match auth_identity(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let cursor = match decode_cursor(&params.cursor) {
        Ok(cursor) => cursor,
        Err(_) => return error_response(StatusCode::BAD_REQUEST, "invalid_cursor", "The pagination cursor is invalid."),
    };
    let user_id = match state.database.resolve_user_id(&identity).await {
        Ok(user_id) => user_id,
        Err(error) => return database_error(error, "resolve_user"),
    };
    match state
        .database
        .list_chat_conversations(user_id, cursor, page_size(params.page_size))
        .await
    {
        Ok(page) => page_response("/api/v1/chat-fast/conversations/", page),
        Err(error) => database_error(error, "list_conversations"),
    }
}

pub(crate) async fn get_conversation(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(response) = ensure_enabled(&state) {
        return response;
    }
    let identity = match auth_identity(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let user_id = match state.database.resolve_user_id(&identity).await {
        Ok(user_id) => user_id,
        Err(error) => return database_error(error, "resolve_user"),
    };
    match state.database.get_chat_conversation(user_id, conversation_id).await {
        Ok(Some(value)) => (StatusCode::OK, Json(value)).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "conversation_not_found", "Conversation was not found."),
        Err(error) => database_error(error, "get_conversation"),
    }
}

pub(crate) async fn list_messages(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Query(params): Query<PageParams>,
) -> impl IntoResponse {
    if let Err(response) = ensure_enabled(&state) {
        return response;
    }
    let identity = match auth_identity(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let cursor = match decode_cursor(&params.cursor) {
        Ok(cursor) => cursor,
        Err(_) => return error_response(StatusCode::BAD_REQUEST, "invalid_cursor", "The pagination cursor is invalid."),
    };
    let user_id = match state.database.resolve_user_id(&identity).await {
        Ok(user_id) => user_id,
        Err(error) => return database_error(error, "resolve_user"),
    };
    match state
        .database
        .list_chat_messages(user_id, conversation_id, cursor, page_size(params.page_size))
        .await
    {
        Ok(Some(page)) => page_response(
            &format!("/api/v1/chat-fast/conversations/{conversation_id}/messages/"),
            page,
        ),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "conversation_not_found", "Conversation was not found."),
        Err(error) => database_error(error, "list_messages"),
    }
}

pub(crate) async fn get_message(
    State(state): State<Arc<AppState>>,
    Path(message_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(response) = ensure_enabled(&state) {
        return response;
    }
    let identity = match auth_identity(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let user_id = match state.database.resolve_user_id(&identity).await {
        Ok(user_id) => user_id,
        Err(error) => return database_error(error, "resolve_user"),
    };
    match state.database.get_chat_message(user_id, message_id).await {
        Ok(Some(value)) => (StatusCode::OK, Json(value)).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "message_not_found", "Message was not found."),
        Err(error) => database_error(error, "get_message"),
    }
}

pub(crate) async fn message_context(
    State(state): State<Arc<AppState>>,
    Path(message_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(response) = ensure_enabled(&state) {
        return response;
    }
    let identity = match auth_identity(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let user_id = match state.database.resolve_user_id(&identity).await {
        Ok(user_id) => user_id,
        Err(error) => return database_error(error, "resolve_user"),
    };
    match state.database.get_chat_message_context(user_id, message_id).await {
        Ok(Some(results)) => (
            StatusCode::OK,
            Json(json!({"target_id": message_id.to_string(), "results": results})),
        )
            .into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "message_not_found", "Message was not found."),
        Err(error) => database_error(error, "message_context"),
    }
}

pub(crate) async fn list_media(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Query(params): Query<MediaParams>,
) -> impl IntoResponse {
    if let Err(response) = ensure_enabled(&state) {
        return response;
    }
    let identity = match auth_identity(&state, &headers) {
        Ok(identity) => identity,
        Err(response) => return response,
    };
    let cursor = match decode_cursor(&params.cursor) {
        Ok(cursor) => cursor,
        Err(_) => return error_response(StatusCode::BAD_REQUEST, "invalid_cursor", "The pagination cursor is invalid."),
    };
    let kind = params.kind.trim().to_ascii_lowercase();
    if !matches!(kind.as_str(), "all" | "image" | "video" | "audio" | "file") {
        return error_response(StatusCode::BAD_REQUEST, "invalid_media_kind", "Media kind is invalid.");
    }
    let user_id = match state.database.resolve_user_id(&identity).await {
        Ok(user_id) => user_id,
        Err(error) => return database_error(error, "resolve_user"),
    };
    match state
        .database
        .list_chat_media(user_id, conversation_id, &kind, cursor, page_size(params.page_size))
        .await
    {
        Ok(Some(page)) => {
            let base = format!("/api/v1/chat-fast/conversations/{conversation_id}/media/");
            let next = page
                .next_cursor
                .map(|cursor| format!("{base}?kind={kind}&cursor={cursor}"));
            (
                StatusCode::OK,
                Json(json!({"next": next, "previous": Value::Null, "results": page.results})),
            )
                .into_response()
        }
        Ok(None) => error_response(StatusCode::NOT_FOUND, "conversation_not_found", "Conversation was not found."),
        Err(error) => database_error(error, "list_media"),
    }
}

const USER_COMPACT_JSON: &str = r#"
jsonb_build_object(
    'id', u.id::text,
    'username', u.username,
    'email', u.email,
    'display_name', COALESCE(NULLIF(p.display_name, ''), NULLIF(BTRIM(CONCAT_WS(' ', u.first_name, u.last_name)), ''), u.username),
    'avatar', CASE WHEN COALESCE(p.avatar, '') = '' THEN NULL ELSE '/media/' || p.avatar END
)
"#;

const USER_LITE_JSON: &str = r#"
jsonb_build_object(
    'id', u.id::text,
    'username', u.username,
    'email', u.email,
    'display_name', COALESCE(NULLIF(p.display_name, ''), NULLIF(BTRIM(CONCAT_WS(' ', u.first_name, u.last_name)), ''), u.username),
    'avatar', CASE WHEN COALESCE(p.avatar, '') = '' THEN NULL ELSE '/media/' || p.avatar END,
    'is_online', false,
    'active_devices', 0,
    'last_seen_at', CASE
        WHEN u.id = (SELECT id FROM actor) THEN u.last_seen_at
        WHEN NOT COALESCE(p.show_online_status, true) THEN NULL
        WHEN EXISTS (
            SELECT 1 FROM chat_userblock ub
            WHERE (ub.blocker_id = (SELECT id FROM actor) AND ub.blocked_id = u.id)
               OR (ub.blocker_id = u.id AND ub.blocked_id = (SELECT id FROM actor))
        ) THEN NULL
        ELSE u.last_seen_at
    END,
    'presence_label', 'offline',
    'presence_status', 'offline',
    'device_type', NULL,
    'device_types', '[]'::jsonb,
    'presence_visibility', CASE
        WHEN u.id = (SELECT id FROM actor) THEN 'public'
        WHEN NOT COALESCE(p.show_online_status, true) THEN 'hidden'
        WHEN EXISTS (
            SELECT 1 FROM chat_userblock ub
            WHERE (ub.blocker_id = (SELECT id FROM actor) AND ub.blocked_id = u.id)
               OR (ub.blocker_id = u.id AND ub.blocked_id = (SELECT id FROM actor))
        ) THEN 'hidden'
        ELSE 'public'
    END
)
"#;

const ATTACHMENT_JSON: &str = r#"
jsonb_build_object(
    'id', a.id::text,
    'media_kind', a.media_kind,
    'original_name', a.original_name,
    'mime_type', a.mime_type,
    'size', a.size,
    'width', a.width,
    'height', a.height,
    'rotation', a.rotation,
    'duration_seconds', a.duration_seconds,
    'aspect_ratio', CASE WHEN COALESCE(a.height, 0) > 0 THEN a.width::double precision / a.height::double precision ELSE NULL END,
    'metadata', COALESCE(a.metadata, '{}'::jsonb) - 'encrypted_attachment' - 'encryption',
    'thumbnail_url', CASE WHEN a.view_once OR COALESCE(a.thumbnail, '') = '' THEN NULL ELSE '/api/v1/chat/attachments/' || a.id::text || '/thumbnail/' END,
    'scan_status', a.scan_status,
    'scan_notes', a.scan_notes,
    'scanned_at', a.scanned_at,
    'file_url', CASE WHEN a.view_once THEN '' ELSE '/api/v1/chat/attachments/' || a.id::text || '/download/' END,
    'download_url', CASE WHEN a.view_once THEN '' ELSE '/api/v1/chat/attachments/' || a.id::text || '/download/' END,
    'preview_url', CASE WHEN a.view_once THEN '' ELSE '/api/v1/chat/attachments/' || a.id::text || '/preview/' END,
    'can_preview_inline', CASE WHEN a.view_once OR COALESCE(a.metadata->'encrypted_attachment' = 'true'::jsonb, false) THEN false ELSE (a.mime_type LIKE 'image/%' OR a.mime_type LIKE 'audio/%' OR a.mime_type LIKE 'video/%' OR a.mime_type = 'application/pdf') END,
    'signed_download', NULL,
    'signed_preview', NULL,
    'is_encrypted', COALESCE(a.metadata->'encrypted_attachment' = 'true'::jsonb, false),
    'encryption', CASE
        WHEN NOT COALESCE(a.metadata->'encrypted_attachment' = 'true'::jsonb, false) THEN NULL
        WHEN a.view_once THEN COALESCE(a.metadata->'encryption', '{}'::jsonb) - 'preview_ciphertext' - 'preview_nonce' - 'preview_mime_type'
        ELSE a.metadata->'encryption'
    END,
    'view_once', a.view_once,
    'view_once_opened', EXISTS(SELECT 1 FROM chat_messageattachmentviewreceipt avr WHERE avr.attachment_id = a.id AND avr.user_id = (SELECT id FROM actor)),
    'can_open_view_once', a.view_once AND m.sender_id IS DISTINCT FROM (SELECT id FROM actor) AND NOT EXISTS(SELECT 1 FROM chat_messageattachmentviewreceipt avr WHERE avr.attachment_id = a.id AND avr.user_id = (SELECT id FROM actor))
)
"#;

const MESSAGE_JSON: &str = r#"
jsonb_build_object(
    'id', m.id::text,
    'conversation', m.conversation_id::text,
    'sender', CASE WHEN m.sender_id IS NULL THEN NULL ELSE jsonb_build_object(
        'id', su.id::text,
        'username', su.username,
        'email', su.email,
        'display_name', COALESCE(NULLIF(sp.display_name, ''), NULLIF(BTRIM(CONCAT_WS(' ', su.first_name, su.last_name)), ''), su.username),
        'avatar', CASE WHEN COALESCE(sp.avatar, '') = '' THEN NULL ELSE '/media/' || sp.avatar END,
        'is_online', false,
        'active_devices', 0,
        'last_seen_at', NULL,
        'presence_label', 'offline',
        'presence_status', 'offline',
        'device_type', NULL,
        'device_types', '[]'::jsonb,
        'presence_visibility', CASE WHEN COALESCE(sp.show_online_status, true) THEN 'public' ELSE 'hidden' END
    ) END,
    'type', m.type,
    'text', m.text,
    'metadata', CASE WHEN m.is_deleted THEN '{}'::jsonb ELSE COALESCE(m.metadata, '{}'::jsonb) END,
    'reply_to', m.reply_to_id::text,
    'forwarded_from', m.forwarded_from_id::text,
    'is_edited', m.is_edited,
    'edited_at', m.edited_at,
    'is_deleted', m.is_deleted,
    'deleted_at', m.deleted_at,
    'client_temp_id', m.client_temp_id,
    'sequence', m.sequence,
    'delivery_status', CASE
        WHEN m.sender_id = (SELECT id FROM actor) AND EXISTS(
            SELECT 1
            FROM chat_conversationparticipant receipt_participant
            JOIN chat_message read_pointer ON read_pointer.id = receipt_participant.last_read_message_id
            WHERE receipt_participant.conversation_id = m.conversation_id
              AND receipt_participant.user_id <> (SELECT id FROM actor)
              AND receipt_participant.left_at IS NULL
              AND receipt_participant.banned_at IS NULL
              AND read_pointer.sequence >= m.sequence
        ) THEN 'read'
        WHEN m.sender_id = (SELECT id FROM actor) AND (
            EXISTS(
                SELECT 1
                FROM chat_conversationparticipant receipt_participant
                JOIN chat_message delivered_pointer ON delivered_pointer.id = receipt_participant.last_delivered_message_id
                WHERE receipt_participant.conversation_id = m.conversation_id
                  AND receipt_participant.user_id <> (SELECT id FROM actor)
                  AND receipt_participant.left_at IS NULL
                  AND receipt_participant.banned_at IS NULL
                  AND delivered_pointer.sequence >= m.sequence
            )
            OR EXISTS(
                SELECT 1 FROM chat_messagedelivery delivery
                WHERE delivery.message_id = m.id AND delivery.user_id <> (SELECT id FROM actor)
            )
        ) THEN 'delivered'
        ELSE m.delivery_status
    END,
    'failed_reason', NULLIF(m.failed_reason, ''),
    'retry_count', m.retry_count,
    'attachments', COALESCE((
        SELECT jsonb_agg("#;

const MESSAGE_JSON_AFTER_ATTACHMENTS: &str = r#" ORDER BY a.created_at, a.id)
        FROM chat_messageattachment a
        WHERE a.message_id = m.id AND a.scan_status = 'clean' AND NOT m.is_deleted
    ), '[]'::jsonb),
    'reactions', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'id', r.id::text,
            'emoji', r.emoji,
            'user', jsonb_build_object(
                'id', ru.id::text,
                'username', ru.username,
                'email', ru.email,
                'display_name', COALESCE(NULLIF(rp.display_name, ''), NULLIF(BTRIM(CONCAT_WS(' ', ru.first_name, ru.last_name)), ''), ru.username),
                'avatar', CASE WHEN COALESCE(rp.avatar, '') = '' THEN NULL ELSE '/media/' || rp.avatar END,
                'is_online', false,
                'active_devices', 0,
                'last_seen_at', NULL,
                'presence_label', 'offline',
                'presence_status', 'offline',
                'device_type', NULL,
                'device_types', '[]'::jsonb,
                'presence_visibility', CASE WHEN COALESCE(rp.show_online_status, true) THEN 'public' ELSE 'hidden' END
            ),
            'created_at', r.created_at
        ) ORDER BY r.created_at, r.id)
        FROM chat_messagereaction r
        JOIN accounts_user ru ON ru.id = r.user_id
        LEFT JOIN accounts_profile rp ON rp.user_id = ru.id
        WHERE r.message_id = m.id
    ), '[]'::jsonb),
    'deliveries', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'id', d.id::text,
            'user', jsonb_build_object(
                'id', du.id::text,
                'username', du.username,
                'email', du.email,
                'display_name', COALESCE(NULLIF(dp.display_name, ''), NULLIF(BTRIM(CONCAT_WS(' ', du.first_name, du.last_name)), ''), du.username),
                'avatar', CASE WHEN COALESCE(dp.avatar, '') = '' THEN NULL ELSE '/media/' || dp.avatar END,
                'is_online', false,
                'active_devices', 0,
                'last_seen_at', NULL,
                'presence_label', 'offline',
                'presence_status', 'offline',
                'device_type', NULL,
                'device_types', '[]'::jsonb,
                'presence_visibility', CASE WHEN COALESCE(dp.show_online_status, true) THEN 'public' ELSE 'hidden' END
            ),
            'delivered_at', d.delivered_at
        ) ORDER BY d.delivered_at, d.id)
        FROM chat_messagedelivery d
        JOIN accounts_user du ON du.id = d.user_id
        LEFT JOIN accounts_profile dp ON dp.user_id = du.id
        WHERE d.message_id = m.id
    ), '[]'::jsonb),
    'edit_history', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'id', eh.id::text,
            'previous_text', eh.previous_text,
            'new_text', eh.new_text,
            'edited_by', jsonb_build_object(
                'id', eu.id::text,
                'username', eu.username,
                'email', eu.email,
                'display_name', COALESCE(NULLIF(ep.display_name, ''), NULLIF(BTRIM(CONCAT_WS(' ', eu.first_name, eu.last_name)), ''), eu.username),
                'avatar', CASE WHEN COALESCE(ep.avatar, '') = '' THEN NULL ELSE '/media/' || ep.avatar END
            ),
            'created_at', eh.created_at
        ) ORDER BY eh.created_at DESC, eh.id DESC)
        FROM chat_messageedithistory eh
        JOIN accounts_user eu ON eu.id = eh.edited_by_id
        LEFT JOIN accounts_profile ep ON ep.user_id = eu.id
        WHERE eh.message_id = m.id AND NOT m.is_deleted
    ), '[]'::jsonb),
    'reaction_summary', COALESCE((
        SELECT jsonb_agg(jsonb_build_object('emoji', grouped.emoji, 'count', grouped.total) ORDER BY grouped.emoji)
        FROM (SELECT r.emoji, COUNT(*)::bigint AS total FROM chat_messagereaction r WHERE r.message_id = m.id GROUP BY r.emoji) grouped
    ), '[]'::jsonb),
    'voice_note', CASE WHEN NOT m.is_deleted AND (m.type = 'audio' OR COALESCE(m.metadata->'voice_note' = 'true'::jsonb, false)) THEN jsonb_build_object(
        'is_voice_note', COALESCE(m.metadata->'voice_note' = 'true'::jsonb, m.type = 'audio'),
        'duration_seconds', m.metadata->'duration_seconds',
        'waveform', COALESCE(m.metadata->'waveform', '[]'::jsonb),
        'transcript_available', EXISTS(SELECT 1 FROM chat_messagetranscript mt WHERE mt.message_id = m.id AND COALESCE(mt.text, '') <> '')
    ) ELSE NULL END,
    'transcript', (
        SELECT jsonb_build_object('status', mt.status, 'language_code', mt.language_code, 'text', mt.text, 'confidence', mt.confidence, 'source', mt.source, 'created_at', mt.created_at, 'updated_at', mt.updated_at)
        FROM chat_messagetranscript mt WHERE mt.message_id = m.id AND NOT m.is_deleted
    ),
    'entities', CASE WHEN m.is_deleted THEN '[]'::jsonb ELSE COALESCE(m.metadata->'entities', '[]'::jsonb) END,
    'links', CASE WHEN m.is_deleted THEN '[]'::jsonb ELSE COALESCE(m.metadata->'links', '[]'::jsonb) END,
    'mentioned_user_ids', CASE WHEN m.is_deleted THEN '[]'::jsonb ELSE COALESCE(m.metadata->'mentioned_user_ids', '[]'::jsonb) END,
    'is_encrypted', CASE WHEN m.is_deleted THEN false ELSE COALESCE(m.metadata->'encrypted' = 'true'::jsonb, false) END,
    'encryption', CASE WHEN NOT m.is_deleted AND COALESCE(m.metadata->'encrypted' = 'true'::jsonb, false) THEN m.metadata->'encryption' ELSE NULL END,
    'reply_preview', (
        SELECT jsonb_build_object(
            'id', reply.id::text,
            'text', reply.text,
            'type', reply.type,
            'sender', CASE WHEN reply.sender_id IS NULL THEN NULL ELSE jsonb_build_object(
                'id', rpu.id::text,
                'username', rpu.username,
                'email', rpu.email,
                'display_name', COALESCE(NULLIF(rpp.display_name, ''), NULLIF(BTRIM(CONCAT_WS(' ', rpu.first_name, rpu.last_name)), ''), rpu.username),
                'avatar', CASE WHEN COALESCE(rpp.avatar, '') = '' THEN NULL ELSE '/media/' || rpp.avatar END
            ) END,
            'is_deleted', reply.is_deleted,
            'created_at', reply.created_at
        )
        FROM chat_message reply
        LEFT JOIN accounts_user rpu ON rpu.id = reply.sender_id
        LEFT JOIN accounts_profile rpp ON rpp.user_id = rpu.id
        WHERE reply.id = m.reply_to_id
    ),
    'can_edit', (
        m.sender_id = (SELECT id FROM actor)
        AND NOT m.is_deleted
        AND m.delivery_status <> 'failed'
        AND m.type IN ('text', 'image', 'video', 'file')
        AND (COALESCE(m.text, '') <> '' OR COALESCE(m.metadata->'encrypted' = 'true'::jsonb, false))
        AND m.edit_locked_at IS NULL
        AND (SELECT edit_window_seconds FROM actor) > 0
        AND NOW() < m.created_at + ((SELECT edit_window_seconds FROM actor) * INTERVAL '1 second')
    ),
    'edit_locked_reason', CASE
        WHEN m.sender_id IS DISTINCT FROM (SELECT id FROM actor) THEN 'You can edit only your own messages.'
        WHEN m.is_deleted THEN 'Deleted messages cannot be edited.'
        WHEN m.delivery_status = 'failed' THEN 'Failed messages cannot be edited. Retry or delete the message instead.'
        WHEN m.type NOT IN ('text', 'image', 'video', 'file') OR (COALESCE(m.text, '') = '' AND NOT COALESCE(m.metadata->'encrypted' = 'true'::jsonb, false)) THEN 'Only text and attachment captions can be edited.'
        WHEN m.edit_locked_at IS NOT NULL THEN 'This message can no longer be edited because it has activity.'
        WHEN (SELECT edit_window_seconds FROM actor) <= 0 OR NOW() >= m.created_at + ((SELECT edit_window_seconds FROM actor) * INTERVAL '1 second') THEN 'The editing window has expired.'
        ELSE ''
    END,
    'edit_deadline', m.created_at + ((SELECT edit_window_seconds FROM actor) * INTERVAL '1 second'),
    'can_restore', (
        m.sender_id = (SELECT id FROM actor)
        AND m.is_deleted
        AND COALESCE(m.deletion_source, '') = 'sender'
    ),
    'restore_locked_reason', CASE
        WHEN m.sender_id IS DISTINCT FROM (SELECT id FROM actor) THEN 'Only the sender can restore this message.'
        WHEN NOT m.is_deleted THEN 'This message is not deleted.'
        WHEN COALESCE(m.deletion_source, '') = 'moderation' THEN 'A message hidden by moderation cannot be restored by its sender.'
        WHEN COALESCE(m.deletion_source, '') <> 'sender' THEN 'This deleted message cannot be restored.'
        ELSE ''
    END,
    'created_at', m.created_at,
    'updated_at', m.updated_at,
    '_cursor_at', m.created_at
)
"#;

fn push_message_json(builder: &mut QueryBuilder<'_, Postgres>) {
    builder.push(MESSAGE_JSON);
    builder.push(ATTACHMENT_JSON);
    builder.push(MESSAGE_JSON_AFTER_ATTACHMENTS);
}

fn push_actor(builder: &mut QueryBuilder<'_, Postgres>, user_id: i64, edit_window_seconds: i64) {
    builder.push("WITH actor AS (SELECT ");
    builder.push_bind(user_id);
    builder.push("::bigint AS id, ");
    builder.push_bind(edit_window_seconds);
    builder.push("::bigint AS edit_window_seconds) ");
}

fn push_message_from(builder: &mut QueryBuilder<'_, Postgres>) {
    builder.push(
        " FROM chat_message m LEFT JOIN accounts_user su ON su.id = m.sender_id LEFT JOIN accounts_profile sp ON sp.user_id = su.id ",
    );
}

fn strip_cursor_fields(values: &mut [Value]) {
    for value in values {
        if let Some(object) = value.as_object_mut() {
            object.remove("_cursor_at");
        }
    }
}

fn inject_last_messages(conversations: &mut [Value], messages: Vec<Value>) {
    let mut by_id = std::collections::HashMap::new();
    for message in messages {
        if let Some(id) = message.get("id").and_then(Value::as_str) {
            by_id.insert(id.to_owned(), message);
        }
    }
    for conversation in conversations {
        let Some(object) = conversation.as_object_mut() else { continue; };
        let id = object
            .remove("_last_message_id")
            .and_then(|value| value.as_str().map(ToOwned::to_owned));
        let message = id.as_deref().and_then(|id| by_id.get(id)).cloned().unwrap_or(Value::Null);
        object.insert("last_message".to_owned(), message);
    }
}

impl Database {
    pub(crate) async fn resolve_user_id(&self, identity: &CommandIdentity) -> anyhow::Result<i64> {
        let pool = self.pool.as_ref().ok_or_else(|| anyhow::anyhow!("SQLx read backend is disabled"))?;
        if let Some(user_id) = identity.claimed_user_id {
            if let Some(id) = sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE id = $1 AND is_active = TRUE")
                .bind(user_id)
                .persistent(false)
                .fetch_optional(pool)
                .await?
            {
                return Ok(id);
            }
        }
        if !identity.email.is_empty() {
            if let Some(id) = sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE LOWER(email) = $1 AND is_active = TRUE LIMIT 1")
                .bind(&identity.email)
                .persistent(false)
                .fetch_optional(pool)
                .await?
            {
                return Ok(id);
            }
        }
        anyhow::bail!("authenticated user does not exist locally")
    }

    pub(crate) async fn get_chat_message_in_transaction(
        &self,
        tx: &mut Transaction<'_, Postgres>,
        user_id: i64,
        message_id: Uuid,
    ) -> anyhow::Result<Option<Value>> {
        let mut builder = QueryBuilder::<Postgres>::new("");
        push_actor(&mut builder, user_id, self.message_edit_window_seconds);
        builder.push("SELECT ");
        push_message_json(&mut builder);
        push_message_from(&mut builder);
        builder.push(" WHERE m.id = ");
        builder.push_bind(message_id);
        builder.push(" AND EXISTS (SELECT 1 FROM chat_conversationparticipant cp WHERE cp.conversation_id = m.conversation_id AND cp.user_id = (SELECT id FROM actor) AND cp.left_at IS NULL AND cp.banned_at IS NULL) LIMIT 1");
        let mut value = builder
            .build_query_scalar::<Value>()
            .persistent(false)
            .fetch_optional(&mut **tx)
            .await?;
        if let Some(value) = value.as_mut() {
            strip_cursor_fields(std::slice::from_mut(value));
        }
        Ok(value)
    }

    async fn messages_by_ids(&self, user_id: i64, ids: &[Uuid]) -> anyhow::Result<Vec<Value>> {
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let pool = self.pool.as_ref().ok_or_else(|| anyhow::anyhow!("SQLx read backend is disabled"))?;
        let mut builder = QueryBuilder::<Postgres>::new("");
        push_actor(&mut builder, user_id, self.message_edit_window_seconds);
        builder.push("SELECT ");
        push_message_json(&mut builder);
        push_message_from(&mut builder);
        builder.push(" WHERE m.id = ANY(");
        builder.push_bind(ids.to_vec());
        builder.push(") AND EXISTS (SELECT 1 FROM chat_conversationparticipant cp WHERE cp.conversation_id = m.conversation_id AND cp.user_id = (SELECT id FROM actor) AND cp.left_at IS NULL AND cp.banned_at IS NULL)");
        let mut values = builder.build_query_scalar::<Value>().persistent(false).fetch_all(pool).await?;
        strip_cursor_fields(&mut values);
        Ok(values)
    }

    pub(crate) async fn list_chat_conversations(
        &self,
        user_id: i64,
        cursor: Option<TimeCursor>,
        limit: i64,
    ) -> anyhow::Result<ReadPage> {
        let pool = self.pool.as_ref().ok_or_else(|| anyhow::anyhow!("SQLx read backend is disabled"))?;
        let mut builder = QueryBuilder::<Postgres>::new("SELECT jsonb_build_object(");
        builder.push("'id', c.id::text, 'type', c.type, 'title', c.title, 'slug', c.slug, 'avatar', CASE WHEN COALESCE(c.avatar, '') = '' THEN NULL ELSE '/media/' || c.avatar END, ");
        builder.push("'e2ee_key_version', c.e2ee_key_version, 'e2ee_rekey_required', c.e2ee_rekey_required, 'e2ee_last_key_rotation_at', c.e2ee_last_key_rotation_at, 'e2ee_last_security_event_at', c.e2ee_last_security_event_at, ");
        builder.push("'_last_message_id', c.last_message_id::text, 'last_message_at', c.last_message_at, 'active_participant_count', (SELECT COUNT(*)::bigint FROM chat_conversationparticipant ap WHERE ap.conversation_id = c.id AND ap.left_at IS NULL AND ap.banned_at IS NULL), ");
        builder.push("'unread_count', (SELECT COUNT(*)::bigint FROM chat_message um LEFT JOIN chat_message rm ON rm.id = viewer.last_read_message_id WHERE um.conversation_id = c.id AND NOT um.is_deleted AND um.sender_id IS DISTINCT FROM ");
        builder.push_bind(user_id);
        builder.push(" AND (rm.id IS NULL OR (um.created_at, um.id) > (rm.created_at, rm.id))), ");
        builder.push("'participants', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', cp.id::text, 'user', ");
        builder.push(USER_COMPACT_JSON);
        builder.push(", 'role', cp.role, 'joined_at', cp.joined_at, 'left_at', cp.left_at, 'is_muted', cp.is_muted, 'is_archived', cp.is_archived, 'is_pinned', cp.is_pinned, 'is_blocked', cp.is_blocked, 'last_read_message', cp.last_read_message_id::text, 'last_read_at', cp.last_read_at, 'last_delivered_message', cp.last_delivered_message_id::text, 'last_delivered_at', cp.last_delivered_at) ORDER BY cp.joined_at, cp.id) FROM chat_conversationparticipant cp JOIN accounts_user u ON u.id = cp.user_id LEFT JOIN accounts_profile p ON p.user_id = u.id WHERE cp.conversation_id = c.id), '[]'::jsonb), ");
        builder.push("'draft', CASE WHEN EXISTS (SELECT 1 FROM chat_conversationparticipant ecp JOIN chat_usere2eedevicekey ekey ON ekey.user_id = ecp.user_id AND ekey.is_active = TRUE WHERE ecp.conversation_id = c.id AND ecp.left_at IS NULL AND ecp.banned_at IS NULL) THEN NULL ELSE (SELECT jsonb_build_object('id', d.id::text, 'conversation', d.conversation_id::text, 'text', d.text, 'reply_to', NULL, 'metadata', d.metadata, 'has_draft', true, 'created_at', d.created_at, 'updated_at', d.updated_at) FROM chat_conversationdraft d WHERE d.conversation_id = c.id AND d.user_id = ");
        builder.push_bind(user_id);
        builder.push(" ORDER BY d.updated_at DESC LIMIT 1) END, 'created_at', c.created_at, '_cursor_at', COALESCE(c.last_message_at, c.created_at)) ");
        builder.push("FROM chat_conversationparticipant viewer JOIN chat_conversation c ON c.id = viewer.conversation_id WHERE viewer.user_id = ");
        builder.push_bind(user_id);
        builder.push(" AND viewer.left_at IS NULL AND viewer.banned_at IS NULL AND c.is_active = TRUE ");
        if let Some(cursor) = cursor {
            builder.push("AND (COALESCE(c.last_message_at, c.created_at), c.id) < (");
            builder.push_bind(cursor.at);
            builder.push("::timestamptz, ");
            builder.push_bind(cursor.id);
            builder.push(") ");
        }
        builder.push("ORDER BY COALESCE(c.last_message_at, c.created_at) DESC, c.id DESC LIMIT ");
        builder.push_bind(limit + 1);
        let mut results = builder.build_query_scalar::<Value>().persistent(false).fetch_all(pool).await?;
        let has_more = results.len() as i64 > limit;
        if has_more {
            results.pop();
        }
        let next_cursor = if has_more {
            results.last_mut().map(extract_cursor).transpose()?.map(|cursor| encode_cursor(&cursor)).transpose()?
        } else {
            None
        };
        for result in &mut results {
            if let Some(object) = result.as_object_mut() {
                object.remove("_cursor_at");
            }
        }
        let last_message_ids = results
            .iter()
            .filter_map(|value| value.get("_last_message_id").and_then(Value::as_str))
            .filter_map(|value| value.parse::<Uuid>().ok())
            .collect::<Vec<_>>();
        let messages = self.messages_by_ids(user_id, &last_message_ids).await?;
        inject_last_messages(&mut results, messages);
        Ok(ReadPage { results, next_cursor })
    }

    pub(crate) async fn get_chat_conversation(&self, user_id: i64, conversation_id: Uuid) -> anyhow::Result<Option<Value>> {
        let pool = self.pool.as_ref().ok_or_else(|| anyhow::anyhow!("SQLx read backend is disabled"))?;
        let mut builder = QueryBuilder::<Postgres>::new("");
        push_actor(&mut builder, user_id, self.message_edit_window_seconds);
        builder.push("SELECT jsonb_build_object(");
        builder.push("'id', c.id::text, 'type', c.type, 'title', c.title, 'slug', c.slug, 'avatar', CASE WHEN COALESCE(c.avatar, '') = '' THEN NULL ELSE '/media/' || c.avatar END, 'created_by', c.created_by_id::text, 'is_active', c.is_active, ");
        builder.push("'e2ee_key_version', c.e2ee_key_version, 'e2ee_rekey_required', c.e2ee_rekey_required, 'e2ee_last_key_rotation_at', c.e2ee_last_key_rotation_at, 'e2ee_last_security_event_at', c.e2ee_last_security_event_at, '_last_message_id', c.last_message_id::text, 'last_message_at', c.last_message_at, ");
        builder.push("'active_participant_count', (SELECT COUNT(*)::bigint FROM chat_conversationparticipant ap WHERE ap.conversation_id = c.id AND ap.left_at IS NULL AND ap.banned_at IS NULL), 'unread_count', (SELECT COUNT(*)::bigint FROM chat_message um LEFT JOIN chat_message rm ON rm.id = viewer.last_read_message_id WHERE um.conversation_id = c.id AND NOT um.is_deleted AND um.sender_id IS DISTINCT FROM ");
        builder.push_bind(user_id);
        builder.push(" AND (rm.id IS NULL OR (um.created_at, um.id) > (rm.created_at, rm.id))), ");
        builder.push("'participants', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', cp.id::text, 'user', ");
        builder.push(USER_LITE_JSON);
        builder.push(", 'role', cp.role, 'joined_at', cp.joined_at, 'left_at', cp.left_at, 'is_muted', cp.is_muted, 'is_archived', cp.is_archived, 'is_pinned', cp.is_pinned, 'is_blocked', cp.is_blocked, 'last_read_message', cp.last_read_message_id::text, 'last_read_at', cp.last_read_at, 'last_delivered_message', cp.last_delivered_message_id::text, 'last_delivered_at', cp.last_delivered_at, 'moderation_muted_until', cp.moderation_muted_until, 'banned_at', cp.banned_at, 'ban_reason', cp.ban_reason) ORDER BY cp.joined_at, cp.id) FROM chat_conversationparticipant cp JOIN accounts_user u ON u.id = cp.user_id LEFT JOIN accounts_profile p ON p.user_id = u.id WHERE cp.conversation_id = c.id), '[]'::jsonb), ");
        builder.push("'draft', CASE WHEN EXISTS (SELECT 1 FROM chat_conversationparticipant ecp JOIN chat_usere2eedevicekey ekey ON ekey.user_id = ecp.user_id AND ekey.is_active = TRUE WHERE ecp.conversation_id = c.id AND ecp.left_at IS NULL AND ecp.banned_at IS NULL) THEN NULL ELSE (SELECT jsonb_build_object('id', d.id::text, 'conversation', d.conversation_id::text, 'text', d.text, 'reply_to', NULL, 'metadata', d.metadata, 'has_draft', true, 'created_at', d.created_at, 'updated_at', d.updated_at) FROM chat_conversationdraft d WHERE d.conversation_id = c.id AND d.user_id = ");
        builder.push_bind(user_id);
        builder.push(" ORDER BY d.updated_at DESC LIMIT 1) END, 'created_at', c.created_at, 'updated_at', c.updated_at) FROM chat_conversationparticipant viewer JOIN chat_conversation c ON c.id = viewer.conversation_id WHERE viewer.user_id = ");
        builder.push_bind(user_id);
        builder.push(" AND viewer.conversation_id = ");
        builder.push_bind(conversation_id);
        builder.push(" AND viewer.left_at IS NULL AND viewer.banned_at IS NULL LIMIT 1");
        let mut conversation = builder.build_query_scalar::<Value>().persistent(false).fetch_optional(pool).await?;
        if let Some(value) = conversation.as_mut() {
            let last_message_id = value
                .get("_last_message_id")
                .and_then(Value::as_str)
                .and_then(|value| value.parse::<Uuid>().ok());
            let messages = self.messages_by_ids(user_id, &last_message_id.into_iter().collect::<Vec<_>>()).await?;
            inject_last_messages(std::slice::from_mut(value), messages);
        }
        Ok(conversation)
    }

    pub(crate) async fn list_chat_messages(
        &self,
        user_id: i64,
        conversation_id: Uuid,
        cursor: Option<TimeCursor>,
        limit: i64,
    ) -> anyhow::Result<Option<ReadPage>> {
        if !self.is_active_participant(conversation_id, user_id).await? {
            return Ok(None);
        }
        let pool = self.pool.as_ref().ok_or_else(|| anyhow::anyhow!("SQLx read backend is disabled"))?;
        let mut builder = QueryBuilder::<Postgres>::new("");
        push_actor(&mut builder, user_id, self.message_edit_window_seconds);
        builder.push("SELECT ");
        push_message_json(&mut builder);
        push_message_from(&mut builder);
        builder.push(" WHERE m.conversation_id = ");
        builder.push_bind(conversation_id);
        if let Some(cursor) = cursor {
            builder.push(" AND (m.created_at, m.id) < (");
            builder.push_bind(cursor.at);
            builder.push("::timestamptz, ");
            builder.push_bind(cursor.id);
            builder.push(")");
        }
        builder.push(" ORDER BY m.created_at DESC, m.id DESC LIMIT ");
        builder.push_bind(limit + 1);
        let mut results = builder.build_query_scalar::<Value>().persistent(false).fetch_all(pool).await?;
        let has_more = results.len() as i64 > limit;
        if has_more {
            results.pop();
        }
        let next_cursor = if has_more {
            results.last_mut().map(extract_cursor).transpose()?.map(|cursor| encode_cursor(&cursor)).transpose()?
        } else {
            None
        };
        strip_cursor_fields(&mut results);
        Ok(Some(ReadPage { results, next_cursor }))
    }

    pub(crate) async fn get_chat_message(&self, user_id: i64, message_id: Uuid) -> anyhow::Result<Option<Value>> {
        let mut values = self.messages_by_ids(user_id, &[message_id]).await?;
        Ok(values.pop())
    }

    async fn context_messages(
        &self,
        user_id: i64,
        conversation_id: Uuid,
        target_at: &str,
        target_id: Uuid,
        older: bool,
    ) -> anyhow::Result<Vec<Value>> {
        let pool = self.pool.as_ref().ok_or_else(|| anyhow::anyhow!("SQLx read backend is disabled"))?;
        let mut builder = QueryBuilder::<Postgres>::new("");
        push_actor(&mut builder, user_id, self.message_edit_window_seconds);
        builder.push("SELECT ");
        push_message_json(&mut builder);
        push_message_from(&mut builder);
        builder.push(" WHERE m.conversation_id = ");
        builder.push_bind(conversation_id);
        if older {
            builder.push(" AND (m.created_at, m.id) < (");
        } else {
            builder.push(" AND (m.created_at, m.id) > (");
        }
        builder.push_bind(target_at.to_owned());
        builder.push("::timestamptz, ");
        builder.push_bind(target_id);
        builder.push(") ORDER BY m.created_at ");
        builder.push(if older { "DESC" } else { "ASC" });
        builder.push(", m.id ");
        builder.push(if older { "DESC" } else { "ASC" });
        builder.push(" LIMIT 15");
        let mut values = builder.build_query_scalar::<Value>().persistent(false).fetch_all(pool).await?;
        strip_cursor_fields(&mut values);
        Ok(values)
    }

    pub(crate) async fn get_chat_message_context(&self, user_id: i64, message_id: Uuid) -> anyhow::Result<Option<Vec<Value>>> {
        let pool = self.pool.as_ref().ok_or_else(|| anyhow::anyhow!("SQLx read backend is disabled"))?;
        let target = sqlx::query_as::<_, (Uuid, String)>(
            "SELECT m.conversation_id, to_char(m.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') FROM chat_message m WHERE m.id = $1 AND EXISTS (SELECT 1 FROM chat_conversationparticipant cp WHERE cp.conversation_id = m.conversation_id AND cp.user_id = $2 AND cp.left_at IS NULL AND cp.banned_at IS NULL)",
        )
        .bind(message_id)
        .bind(user_id)
        .persistent(false)
        .fetch_optional(pool)
        .await?;
        let Some((conversation_id, target_at)) = target else {
            return Ok(None);
        };
        let mut older = self.context_messages(user_id, conversation_id, &target_at, message_id, true).await?;
        older.reverse();
        let target_message = self.get_chat_message(user_id, message_id).await?.ok_or_else(|| anyhow::anyhow!("message disappeared during context read"))?;
        let newer = self.context_messages(user_id, conversation_id, &target_at, message_id, false).await?;
        older.push(target_message);
        older.extend(newer);
        Ok(Some(older))
    }

    pub(crate) async fn list_chat_media(
        &self,
        user_id: i64,
        conversation_id: Uuid,
        kind: &str,
        cursor: Option<TimeCursor>,
        limit: i64,
    ) -> anyhow::Result<Option<ReadPage>> {
        if !self.is_active_participant(conversation_id, user_id).await? {
            return Ok(None);
        }
        let pool = self.pool.as_ref().ok_or_else(|| anyhow::anyhow!("SQLx read backend is disabled"))?;
        let mut builder = QueryBuilder::<Postgres>::new("WITH actor AS (SELECT ");
        builder.push_bind(user_id);
        builder.push("::bigint AS id) SELECT jsonb_build_object('message_id', m.id::text, 'message_text', m.text, 'created_at', a.created_at, 'sender', CASE WHEN m.sender_id IS NULL THEN NULL ELSE ");
        builder.push(USER_LITE_JSON);
        builder.push(" END, 'attachment', ");
        builder.push(ATTACHMENT_JSON);
        builder.push(", 'id', a.id::text, '_cursor_at', a.created_at) FROM chat_messageattachment a JOIN chat_message m ON m.id = a.message_id LEFT JOIN accounts_user u ON u.id = m.sender_id LEFT JOIN accounts_profile p ON p.user_id = u.id WHERE m.conversation_id = ");
        builder.push_bind(conversation_id);
        builder.push(" AND a.scan_status = 'clean' AND NOT m.is_deleted ");
        match kind {
            "image" => builder.push("AND a.mime_type LIKE 'image/%' "),
            "video" => builder.push("AND a.mime_type LIKE 'video/%' "),
            "audio" => builder.push("AND a.mime_type LIKE 'audio/%' "),
            "file" => builder.push("AND a.mime_type NOT LIKE 'image/%' AND a.mime_type NOT LIKE 'video/%' AND a.mime_type NOT LIKE 'audio/%' "),
            _ => &mut builder,
        };
        if let Some(cursor) = cursor {
            builder.push("AND (a.created_at, a.id) < (");
            builder.push_bind(cursor.at);
            builder.push("::timestamptz, ");
            builder.push_bind(cursor.id);
            builder.push(") ");
        }
        builder.push("ORDER BY a.created_at DESC, a.id DESC LIMIT ");
        builder.push_bind(limit + 1);
        let mut results = builder.build_query_scalar::<Value>().persistent(false).fetch_all(pool).await?;
        let has_more = results.len() as i64 > limit;
        if has_more {
            results.pop();
        }
        let next_cursor = if has_more {
            results.last_mut().map(extract_cursor).transpose()?.map(|cursor| encode_cursor(&cursor)).transpose()?
        } else {
            None
        };
        strip_cursor_fields(&mut results);
        Ok(Some(ReadPage { results, next_cursor }))
    }
}
