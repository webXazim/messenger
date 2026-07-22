use std::{collections::HashMap, fmt::Write as _, sync::Arc};

use time::OffsetDateTime;

use axum::{
    extract::{Path, Query, State},
    http::{header, HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Postgres, Transaction};
use subtle::ConstantTimeEq;
use url::Url;
use uuid::Uuid;

use crate::{
    command_delivery::deliver_committed,
    config::SupportDataBackend,
    database::CommittedEvent,
    nats_core::{publish_shared_after_local, EphemeralPriority},
    protocol::{event_message, AudienceKey, AudienceKind},
    state::AppState,
};

#[derive(Clone, Debug)]
struct TeamContext {
    user_id: i64,
    account_id: Uuid,
    role: &'static str,
    agent_id: Option<Uuid>,
    can_view_all: bool,
    can_assign: bool,
}

#[derive(Clone, Debug)]
struct WidgetContext {
    site_key: Uuid,
    website_id: Uuid,
    account_id: Uuid,
    visitor_id: Uuid,
    session_id: Uuid,
    origin: String,
    website_name: String,
    visitor_name: String,
}

#[derive(Debug)]
struct SupportError {
    status: StatusCode,
    code: &'static str,
    detail: String,
}

impl SupportError {
    fn new(status: StatusCode, code: &'static str, detail: impl Into<String>) -> Self {
        Self { status, code, detail: detail.into() }
    }
}

type SupportResult<T> = Result<T, SupportError>;

#[derive(Debug, Default, Deserialize)]
pub struct InboxQuery {
    #[serde(default)]
    website: Option<Uuid>,
    #[serde(default)]
    queue: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    priority: String,
    #[serde(default)]
    search: String,
    #[serde(default)]
    limit: Option<i64>,
    #[serde(default)]
    offset: Option<i64>,
    #[serde(default)]
    cursor: String,
    #[serde(default)]
    tag: Option<Uuid>,
}


#[derive(Debug, Clone, Serialize, Deserialize)]
struct SupportInboxCursor {
    at: String,
    id: Uuid,
}

fn encode_inbox_cursor(cursor: &SupportInboxCursor) -> SupportResult<String> {
    let bytes = serde_json::to_vec(cursor).map_err(|_| {
        SupportError::new(StatusCode::INTERNAL_SERVER_ERROR, "cursor_encode_failed", "The next inbox page could not be encoded.")
    })?;
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(&mut encoded, "{byte:02x}").map_err(|_| {
            SupportError::new(StatusCode::INTERNAL_SERVER_ERROR, "cursor_encode_failed", "The next inbox page could not be encoded.")
        })?;
    }
    Ok(encoded)
}

fn decode_inbox_cursor(raw: &str) -> SupportResult<Option<SupportInboxCursor>> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Ok(None);
    }
    if raw.len() % 2 != 0 || !raw.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(SupportError::new(StatusCode::BAD_REQUEST, "invalid_cursor", "The Support inbox cursor is invalid."));
    }
    let bytes = raw
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let value = std::str::from_utf8(pair).map_err(|_| ())?;
            u8::from_str_radix(value, 16).map_err(|_| ())
        })
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| SupportError::new(StatusCode::BAD_REQUEST, "invalid_cursor", "The Support inbox cursor is invalid."))?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|_| SupportError::new(StatusCode::BAD_REQUEST, "invalid_cursor", "The Support inbox cursor is invalid."))
}

#[derive(Debug, Default, Deserialize)]
pub struct MessageQuery {
    #[serde(default)]
    limit: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub struct MessageInput {
    #[serde(default)]
    text: String,
    #[serde(default)]
    client_temp_id: String,
    #[serde(default)]
    attachment_ids: Vec<Uuid>,
    #[serde(default)]
    voice_note: bool,
}

#[derive(Debug, Default, Deserialize)]
pub struct ReceiptInput {
    #[serde(default)]
    message_id: Option<Uuid>,
}

#[derive(Debug, Deserialize)]
pub struct CallStartInput {
    call_type: String,
}

#[derive(Debug, Default, Deserialize)]
pub struct EndCallInput {
    #[serde(default)]
    reason: String,
}

#[derive(Debug, Deserialize)]
pub struct SignalInput {
    signal_type: String,
    #[serde(default)]
    payload: Value,
    #[serde(default)]
    signal_id: String,
}

#[derive(Debug, Default, Deserialize)]
pub struct MediaStateInput {
    #[serde(default)]
    audio_enabled: Option<bool>,
    #[serde(default)]
    video_enabled: Option<bool>,
}

fn backend_enabled(state: &AppState) -> bool {
    state.config.support_data_backend != SupportDataBackend::Django
}

fn pool(state: &AppState) -> SupportResult<&PgPool> {
    state.database.pool.as_ref().ok_or_else(|| {
        SupportError::new(StatusCode::SERVICE_UNAVAILABLE, "support_sqlx_unavailable", "The Support Chat data plane is unavailable.")
    })
}

fn json_error(error: SupportError) -> Response {
    (error.status, Json(json!({"code": error.code, "detail": error.detail}))).into_response()
}

fn disabled() -> Response {
    json_error(SupportError::new(StatusCode::NOT_FOUND, "axum_support_data_disabled", "Axum Support Chat data routes are not active."))
}

fn widget_response(status: StatusCode, payload: Value, origin: Option<&str>) -> Response {
    let mut response = (status, Json(payload)).into_response();
    let headers = response.headers_mut();
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store, private"));
    headers.insert(header::PRAGMA, HeaderValue::from_static("no-cache"));
    headers.insert(header::VARY, HeaderValue::from_static("Origin"));
    if let Some(origin) = origin.and_then(|value| HeaderValue::from_str(value).ok()) {
        headers.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin);
        headers.insert(header::ACCESS_CONTROL_ALLOW_HEADERS, HeaderValue::from_static("Authorization, Content-Type, X-Support-Session-Token"));
        headers.insert(header::ACCESS_CONTROL_ALLOW_METHODS, HeaderValue::from_static("GET, POST, PATCH, OPTIONS"));
        headers.insert(header::ACCESS_CONTROL_MAX_AGE, HeaderValue::from_static("600"));
    }
    response
}

fn widget_error(error: SupportError, origin: Option<&str>) -> Response {
    widget_response(error.status, json!({"code": error.code, "detail": error.detail}), origin)
}

fn normalize_origin(value: &str) -> Option<String> {
    let url = Url::parse(value.trim()).ok()?;
    if url.scheme() != "http" && url.scheme() != "https" { return None; }
    let host = url.host_str()?.to_ascii_lowercase();
    let port = url.port().map(|value| format!(":{value}")).unwrap_or_default();
    Some(format!("{}://{}{}", url.scheme().to_ascii_lowercase(), host, port))
}

fn token_from_headers(headers: &HeaderMap) -> Option<String> {
    if let Some(value) = headers.get(header::AUTHORIZATION).and_then(|value| value.to_str().ok()) {
        if let Some(token) = value.strip_prefix("Bearer ").or_else(|| value.strip_prefix("bearer ")) {
            if !token.trim().is_empty() { return Some(token.trim().to_owned()); }
        }
    }
    headers.get("x-support-session-token").and_then(|value| value.to_str().ok()).map(str::trim).filter(|value| !value.is_empty()).map(ToOwned::to_owned)
}

fn account_access_sql(alias: &str) -> String {
    format!("({alias}.status='active' OR ({alias}.status='trialing' AND ({alias}.current_period_end IS NULL OR {alias}.current_period_end>NOW())) OR ({alias}.status='past_due' AND {alias}.grace_ends_at>NOW()))")
}

async fn resolve_local_user(pool: &PgPool, claimed_id: Option<i64>, email: &str) -> SupportResult<i64> {
    let user_id = if let Some(user_id) = claimed_id {
        sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE id=$1 AND is_active=TRUE")
            .bind(user_id).persistent(false).fetch_optional(pool).await
    } else {
        sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE LOWER(email)=$1 AND is_active=TRUE LIMIT 1")
            .bind(email).persistent(false).fetch_optional(pool).await
    }
    .map_err(internal_error)?;
    user_id.ok_or_else(|| SupportError::new(StatusCode::UNAUTHORIZED, "authentication_failed", "The authenticated user does not exist locally."))
}

async fn authenticate_team(state: &AppState, headers: &HeaderMap) -> SupportResult<TeamContext> {
    if !backend_enabled(state) {
        return Err(SupportError::new(StatusCode::NOT_FOUND, "axum_support_data_disabled", "Axum Support Chat data routes are not active."));
    }
    let identity = state.command_auth.authenticate(headers).map_err(|_| {
        SupportError::new(StatusCode::UNAUTHORIZED, "authentication_failed", "Authentication credentials were not provided or are invalid.")
    })?;
    let pool = pool(state)?;
    let user_id = resolve_local_user(pool, identity.claimed_user_id, &identity.email).await?;
    let access = account_access_sql("sa");
    let owner_sql = format!("SELECT sa.id FROM support_supportaccount sa WHERE sa.owner_id=$1 AND {access} LIMIT 1");
    if let Some(account_id) = sqlx::query_scalar::<_, Uuid>(&owner_sql).bind(user_id).persistent(false).fetch_optional(pool).await.map_err(internal_error)? {
        return Ok(TeamContext { user_id, account_id, role: "owner", agent_id: None, can_view_all: true, can_assign: true });
    }
    let agent_sql = format!(r#"
        SELECT ag.support_account_id, ag.id, ag.can_view_all_conversations, ag.can_assign_conversations
        FROM support_supportagent ag
        JOIN support_supportaccount sa ON sa.id=ag.support_account_id
        WHERE ag.user_id=$1 AND ag.is_active=TRUE AND {access}
        LIMIT 1
    "#);
    let row = sqlx::query_as::<_, (Uuid, Uuid, bool, bool)>(&agent_sql)
        .bind(user_id).persistent(false).fetch_optional(pool).await.map_err(internal_error)?;
    let Some((account_id, agent_id, can_view_all, can_assign)) = row else {
        return Err(SupportError::new(StatusCode::FORBIDDEN, "support_access_required", "Support Chat access is not active for this account."));
    };
    Ok(TeamContext { user_id, account_id, role: "agent", agent_id: Some(agent_id), can_view_all, can_assign })
}

async fn authenticate_widget(
    state: &AppState,
    headers: &HeaderMap,
    site_key: Uuid,
    session_id: Uuid,
) -> SupportResult<WidgetContext> {
    if !backend_enabled(state) {
        return Err(SupportError::new(StatusCode::NOT_FOUND, "axum_support_data_disabled", "Axum Support Chat data routes are not active."));
    }
    let origin = headers.get(header::ORIGIN).and_then(|value| value.to_str().ok()).and_then(normalize_origin)
        .ok_or_else(|| SupportError::new(StatusCode::FORBIDDEN, "origin_required", "A valid widget origin is required."))?;
    let token = token_from_headers(headers).ok_or_else(|| SupportError::new(StatusCode::UNAUTHORIZED, "widget_session_required", "A Support Chat visitor session token is required."))?;
    let mut hasher = Sha256::new();
    hasher.update(token.as_bytes());
    let token_hash = format!("{:x}", hasher.finalize());
    let pool = pool(state)?;
    let access = account_access_sql("sa");
    let sql = format!(r#"
        SELECT w.id, w.support_account_id, ws.visitor_id, ws.id, ws.token_hash,
               w.allowed_origins, w.domain, w.name, COALESCE(NULLIF(v.name,''),'Website visitor'), ws.origin
        FROM support_supportwebsite w
        JOIN support_supportaccount sa ON sa.id=w.support_account_id
        JOIN support_supportwidgetsession ws ON ws.website_id=w.id
        JOIN support_supportvisitor v ON v.id=ws.visitor_id AND v.website_id=w.id
        WHERE w.site_key=$1 AND ws.id=$2 AND w.is_active=TRUE AND w.widget_enabled=TRUE
          AND ws.status='active' AND ws.expires_at>NOW() AND v.is_blocked=FALSE AND {access}
        LIMIT 1
    "#);
    let row = sqlx::query_as::<_, (Uuid, Uuid, Uuid, Uuid, String, Value, String, String, String, String)>(&sql)
        .bind(site_key).bind(session_id).persistent(false).fetch_optional(pool).await.map_err(internal_error)?;
    let Some((website_id, account_id, visitor_id, session_id, stored_hash, allowed, domain, website_name, visitor_name, session_origin)) = row else {
        return Err(SupportError::new(StatusCode::UNAUTHORIZED, "widget_session_invalid", "The Support Chat visitor session is invalid or expired."));
    };
    if token_hash.as_bytes().ct_eq(stored_hash.as_bytes()).unwrap_u8() != 1 {
        return Err(SupportError::new(StatusCode::UNAUTHORIZED, "widget_session_invalid", "The Support Chat visitor session is invalid or expired."));
    }
    if normalize_origin(&session_origin).as_deref() != Some(origin.as_str()) {
        return Err(SupportError::new(StatusCode::FORBIDDEN, "origin_not_allowed", "This visitor session belongs to a different website origin."));
    }
    let mut allowed_origins = allowed.as_array().cloned().unwrap_or_default().into_iter().filter_map(|value| value.as_str().and_then(normalize_origin)).collect::<Vec<_>>();
    if allowed_origins.is_empty() {
        let candidate = if domain.starts_with("http://") || domain.starts_with("https://") { domain } else { format!("https://{domain}") };
        if let Some(value) = normalize_origin(&candidate) { allowed_origins.push(value); }
    }
    if !allowed_origins.iter().any(|value| value == &origin) {
        return Err(SupportError::new(StatusCode::FORBIDDEN, "origin_not_allowed", "This website origin is not allowed to use the Support Chat widget."));
    }
    sqlx::query("UPDATE support_supportwidgetsession SET last_seen_at=NOW(), updated_at=NOW() WHERE id=$1 AND last_seen_at<NOW()-INTERVAL '30 seconds'")
        .bind(session_id).persistent(false).execute(pool).await.map_err(internal_error)?;
    sqlx::query("UPDATE support_supportvisitor SET last_seen_at=NOW(), updated_at=NOW() WHERE id=$1 AND last_seen_at<NOW()-INTERVAL '30 seconds'")
        .bind(visitor_id).persistent(false).execute(pool).await.map_err(internal_error)?;
    Ok(WidgetContext { site_key, website_id, account_id, visitor_id, session_id, origin, website_name, visitor_name })
}

fn internal_error(error: sqlx::Error) -> SupportError {
    tracing::error!(error=%error, "Support Chat SQLx operation failed");
    SupportError::new(StatusCode::INTERNAL_SERVER_ERROR, "support_data_failed", "The Support Chat operation could not be completed.")
}

fn visibility_sql(context: &TeamContext, sc_alias: &str, w_alias: &str) -> String {
    if context.role == "owner" {
        return format!("{w_alias}.support_account_id=$2");
    }
    let assignment = if context.can_view_all {
        "TRUE".to_owned()
    } else {
        format!("({sc_alias}.assigned_agent_id=$3 OR {sc_alias}.assigned_agent_id IS NULL)")
    };
    format!("{w_alias}.support_account_id=$2 AND EXISTS(SELECT 1 FROM support_supportwebsiteagent wa WHERE wa.website_id={w_alias}.id AND wa.agent_id=$3) AND {assignment}")
}

async fn team_conversation_chat_id(pool: &PgPool, context: &TeamContext, support_id: Uuid) -> SupportResult<(Uuid, Uuid, Uuid, Option<Uuid>, String)> {
    let visibility = visibility_sql(context, "sc", "w");
    let sql = format!(r#"
        SELECT sc.conversation_id, sc.website_id, sc.visitor_id, sc.assigned_agent_id, sc.status
        FROM support_supportconversation sc
        JOIN support_supportwebsite w ON w.id=sc.website_id
        WHERE sc.id=$1 AND {visibility}
        LIMIT 1
    "#);
    let mut query = sqlx::query_as::<_, (Uuid, Uuid, Uuid, Option<Uuid>, String)>(&sql).bind(support_id).bind(context.account_id);
    if context.role == "agent" { query = query.bind(context.agent_id); }
    query.persistent(false).fetch_optional(pool).await.map_err(internal_error)?
        .ok_or_else(|| SupportError::new(StatusCode::NOT_FOUND, "conversation_not_found", "Support conversation was not found."))
}

async fn service_snapshot(pool: &PgPool, support_id: Uuid) -> SupportResult<Value> {
    let sql = r#"
      WITH base AS (
        SELECT sc.*,
          COALESCE(wp.due_soon_minutes,tp.due_soon_minutes,ss.due_soon_minutes,15)::int AS due_soon_minutes,
          CASE WHEN wp.id IS NOT NULL THEN 'website'
               WHEN tp.id IS NOT NULL THEN 'team'
               ELSE 'account' END AS policy_source,
          fu.id AS follow_user_id,fu.username AS follow_username,fu.email AS follow_email,
          COALESCE(NULLIF(fp.display_name,''),NULLIF(TRIM(CONCAT_WS(' ',fu.first_name,fu.last_name)),''),fu.username) AS follow_display_name,
          CASE WHEN fp.avatar IS NULL OR fp.avatar='' THEN NULL ELSE '/media/'||fp.avatar END AS follow_avatar
        FROM support_supportconversation sc
        JOIN support_supportwebsite w ON w.id=sc.website_id
        LEFT JOIN support_supportservicesettings ss ON ss.support_account_id=w.support_account_id
        LEFT JOIN support_supportslapolicy wp ON wp.support_account_id=w.support_account_id AND wp.website_id=sc.website_id AND wp.is_active=TRUE
        LEFT JOIN support_supportslapolicy tp ON tp.support_account_id=w.support_account_id AND tp.team_id=sc.assigned_team_id AND tp.is_active=TRUE
        LEFT JOIN accounts_user fu ON fu.id=sc.follow_up_created_by_id
        LEFT JOIN accounts_profile fp ON fp.user_id=fu.id
        WHERE sc.id=$1
      ), calculated AS (
        SELECT b.*, dl.active_target,dl.active_due_at,dl.is_overdue,dl.is_due_soon,
               dl.overdue_targets,dl.has_deadlines
        FROM base b
        LEFT JOIN LATERAL (
          SELECT
            (ARRAY_AGG(d.name ORDER BY d.due_at))[1] AS active_target,
            MIN(d.due_at) AS active_due_at,
            COALESCE(BOOL_OR(d.due_at<=NOW()),FALSE) AS is_overdue,
            COALESCE(BOOL_OR(d.due_at>NOW() AND d.due_at<=NOW()+make_interval(mins=>b.due_soon_minutes)),FALSE) AS is_due_soon,
            COALESCE(JSONB_AGG(d.name ORDER BY d.due_at) FILTER (WHERE d.due_at<=NOW()),'[]'::jsonb) AS overdue_targets,
            COUNT(*)>0 AS has_deadlines
          FROM (VALUES
            ('first_response'::text,CASE WHEN b.first_response_at IS NULL THEN b.first_response_due_at END),
            ('next_response'::text,CASE WHEN b.first_response_at IS NOT NULL AND b.next_response_due_at IS NOT NULL AND b.last_visitor_message_at IS NOT NULL AND (b.last_agent_message_at IS NULL OR b.last_visitor_message_at>b.last_agent_message_at) THEN b.next_response_due_at END),
            ('resolution'::text,b.resolution_due_at)
          ) AS d(name,due_at)
          WHERE d.due_at IS NOT NULL
        ) dl ON TRUE
      )
      SELECT jsonb_build_object(
        'state',CASE WHEN sla_paused_at IS NOT NULL THEN 'paused'
                     WHEN is_overdue THEN 'overdue'
                     WHEN is_due_soon THEN 'due_soon'
                     WHEN has_deadlines THEN 'on_track'
                     WHEN status IN ('resolved','closed') THEN 'complete'
                     ELSE 'none' END,
        'active_target',active_target,'active_due_at',active_due_at,
        'is_overdue',COALESCE(is_overdue,FALSE),'is_due_soon',COALESCE(is_due_soon,FALSE),
        'overdue_targets',COALESCE(overdue_targets,'[]'::jsonb),
        'first_response_due_at',first_response_due_at,'next_response_due_at',next_response_due_at,
        'resolution_due_at',resolution_due_at,'first_response_breached_at',first_response_breached_at,
        'next_response_breached_at',next_response_breached_at,'resolution_breached_at',resolution_breached_at,
        'paused_at',sla_paused_at,'pause_reason',sla_pause_reason,'total_paused_seconds',sla_total_paused_seconds,
        'last_recalculated_at',sla_last_recalculated_at,'escalated_at',sla_escalated_at,'policy_source',policy_source,
        'follow_up_at',follow_up_at,'follow_up_note',follow_up_note,
        'follow_up_due',(follow_up_at IS NOT NULL AND follow_up_completed_at IS NULL AND follow_up_at<=NOW()),
        'follow_up_completed_at',follow_up_completed_at,
        'follow_up_created_by',CASE WHEN follow_user_id IS NULL THEN NULL ELSE jsonb_build_object(
          'id',follow_user_id::text,'username',follow_username,'email',follow_email,
          'display_name',follow_display_name,'avatar',follow_avatar) END
      )
      FROM calculated
    "#;
    sqlx::query_scalar::<_, Value>(sql)
        .bind(support_id)
        .persistent(false)
        .fetch_optional(pool)
        .await
        .map_err(internal_error)?
        .ok_or_else(|| SupportError::new(StatusCode::NOT_FOUND, "conversation_not_found", "Support conversation was not found."))
}

async fn conversation_json(pool: &PgPool, support_id: Uuid, viewer_user_id: Option<i64>, visitor_view: bool, widget_ids: Option<(Uuid, Uuid)>) -> SupportResult<Value> {
    let sql = r#"
        SELECT jsonb_build_object(
          'id', sc.id::text,
          'website', jsonb_build_object('id',w.id::text,'name',w.name,'domain',w.domain),
          'visitor', jsonb_build_object(
             'id',v.id::text,'external_id',v.external_id::text,'name',v.name,'email',v.email,
             'locale',v.locale,'current_page_url',v.current_page_url,'referrer',v.referrer,
             'last_seen_at',v.last_seen_at,'is_online',(v.last_seen_at>NOW()-INTERVAL '90 seconds')
          ),
          'assigned_agent', CASE WHEN ag.id IS NULL THEN NULL ELSE jsonb_build_object(
             'id',ag.id::text,
             'user',jsonb_build_object('id',au.id::text,'username',au.username,'email',au.email,
               'display_name',COALESCE(NULLIF(ap.display_name,''),NULLIF(TRIM(CONCAT_WS(' ',au.first_name,au.last_name)),''),au.username),
               'avatar',CASE WHEN ap.avatar IS NULL OR ap.avatar='' THEN NULL ELSE '/media/'||ap.avatar END),
             'availability',ag.availability,'max_active_conversations',ag.max_active_conversations,
             'can_view_all_conversations',ag.can_view_all_conversations,'can_assign_conversations',ag.can_assign_conversations,
             'can_view_analytics',ag.can_view_analytics,'can_manage_websites',ag.can_manage_websites,
             'can_manage_knowledge',ag.can_manage_knowledge,'can_manage_teams',ag.can_manage_teams,
             'can_manage_automations',ag.can_manage_automations,'can_export_data',ag.can_export_data,
             'is_active',ag.is_active,
             'assigned_website_ids',COALESCE((SELECT jsonb_agg(wa.website_id::text ORDER BY wa.created_at) FROM support_supportwebsiteagent wa WHERE wa.agent_id=ag.id),'[]'::jsonb),
             'team_ids',COALESCE((SELECT jsonb_agg(tm.team_id::text ORDER BY tm.created_at) FROM support_supportteammembership tm WHERE tm.agent_id=ag.id),'[]'::jsonb),
             'active_conversation_count',(SELECT COUNT(*)::int FROM support_supportconversation ac WHERE ac.assigned_agent_id=ag.id AND ac.status IN ('new','open','waiting_customer','waiting_team') AND (ac.snoozed_until IS NULL OR ac.snoozed_until<=NOW())),
             'joined_at',ag.joined_at
          ) END,
          'assigned_team', CASE WHEN st.id IS NULL THEN NULL ELSE jsonb_build_object('id',st.id::text,'name',st.name) END,
          'assigned_at',sc.assigned_at,'assignment_trigger',sc.assignment_trigger,
          'status',sc.status,'priority',sc.priority,'subject',sc.subject,
          'first_response_at',sc.first_response_at,'last_visitor_message_at',sc.last_visitor_message_at,
          'last_agent_message_at',sc.last_agent_message_at,'previous_status',sc.previous_status,
          'snoozed_until',sc.snoozed_until,'resolution_reason',sc.resolution_reason,
          'closure_reason',sc.closure_reason,'reopened_at',sc.reopened_at,'reopen_count',sc.reopen_count,
          'revision_number',sc.revision_number,'resolved_at',sc.resolved_at,'closed_at',sc.closed_at,
          'followers',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',fu.id::text,'username',fu.username,'email',fu.email,'display_name',COALESCE(NULLIF(fp.display_name,''),NULLIF(TRIM(CONCAT_WS(' ',fu.first_name,fu.last_name)),''),fu.username),'avatar',CASE WHEN fp.avatar IS NULL OR fp.avatar='' THEN NULL ELSE '/media/'||fp.avatar END) ORDER BY sf.created_at) FROM support_supportconversationfollower sf JOIN accounts_user fu ON fu.id=sf.user_id LEFT JOIN accounts_profile fp ON fp.user_id=fu.id WHERE sf.support_conversation_id=sc.id),'[]'::jsonb),'created_at',sc.created_at,'updated_at',sc.updated_at,
          'unread_count',CASE WHEN $2::bigint IS NULL THEN 0 ELSE (
             SELECT COUNT(*)::int FROM chat_message m
             LEFT JOIN support_supportconversationreadstate rs ON rs.support_conversation_id=sc.id AND rs.user_id=$2
             LEFT JOIN chat_message rm ON rm.id=rs.last_read_message_id
             WHERE m.conversation_id=sc.conversation_id AND m.sender_id IS NULL AND m.is_deleted=FALSE
               AND (rm.sequence IS NULL OR m.sequence>rm.sequence)
          ) END,
          'visitor_unread_count',(
             SELECT COUNT(*)::int FROM chat_message m
             LEFT JOIN chat_message vr ON vr.id=sc.visitor_last_read_message_id
             WHERE m.conversation_id=sc.conversation_id AND m.sender_id IS NOT NULL AND m.is_deleted=FALSE
               AND (vr.sequence IS NULL OR m.sequence>vr.sequence)
          ),
          'tags',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',t.id::text,'name',t.name,'color',t.color,'is_active',t.is_active,'created_at',t.created_at,'updated_at',t.updated_at) ORDER BY t.name)
             FROM support_supportconversationtag ct JOIN support_supporttag t ON t.id=ct.tag_id WHERE ct.support_conversation_id=sc.id AND t.is_active=TRUE),'[]'::jsonb),
          'csat',(SELECT jsonb_build_object('id',cs.id::text,'status',cs.status,'source',cs.source,'rating',cs.rating,'comment',cs.comment,'available',(cs.status='pending' AND cs.expires_at>NOW()),'allow_comment',COALESCE(fs.allow_comment,TRUE),'requested_at',cs.requested_at,'expires_at',cs.expires_at,'submitted_at',cs.submitted_at) FROM support_supportcsatsurvey cs LEFT JOIN support_supportfeedbacksettings fs ON fs.support_account_id=cs.support_account_id WHERE cs.support_conversation_id=sc.id)
        )
        FROM support_supportconversation sc
        JOIN support_supportwebsite w ON w.id=sc.website_id
        JOIN support_supportvisitor v ON v.id=sc.visitor_id
        LEFT JOIN support_supportagent ag ON ag.id=sc.assigned_agent_id
        LEFT JOIN accounts_user au ON au.id=ag.user_id
        LEFT JOIN accounts_profile ap ON ap.user_id=au.id
        LEFT JOIN support_supportteam st ON st.id=sc.assigned_team_id
        WHERE sc.id=$1
    "#;
    let mut payload = sqlx::query_scalar::<_, Value>(sql).bind(support_id).bind(viewer_user_id)
        .persistent(false).fetch_optional(pool).await.map_err(internal_error)?
        .ok_or_else(|| SupportError::new(StatusCode::NOT_FOUND, "conversation_not_found", "Support conversation was not found."))?;
    if let Value::Object(ref mut object) = payload {
        object.insert("service".to_owned(), service_snapshot(pool, support_id).await?);
        let last_message = message_list_json(pool, support_id, viewer_user_id, visitor_view, widget_ids, 1, true).await?.into_iter().next().unwrap_or(Value::Null);
        object.insert("last_message".to_owned(), last_message);
        if visitor_view {
            object.remove("service");
            object.remove("csat");
            object.remove("assigned_team");
            object.remove("followers");
            object.remove("tags");
        }
    }
    Ok(payload)
}

async fn conversation_list_json(
    pool: &PgPool,
    support_ids: &[Uuid],
    viewer_user_id: i64,
) -> SupportResult<HashMap<Uuid, Value>> {
    if support_ids.is_empty() { return Ok(HashMap::new()); }
    let sql = r#"
      SELECT sc.id,jsonb_build_object(
        'id',sc.id::text,
        'website',jsonb_build_object('id',w.id::text,'name',w.name,'domain',w.domain),
        'visitor',jsonb_build_object(
          'id',v.id::text,'external_id',v.external_id::text,'name',v.name,'email',v.email,
          'locale',v.locale,'current_page_url',v.current_page_url,'referrer',v.referrer,
          'last_seen_at',v.last_seen_at,'is_online',(v.last_seen_at>NOW()-INTERVAL '90 seconds')
        ),
        'assigned_agent',CASE WHEN ag.id IS NULL THEN NULL ELSE jsonb_build_object(
          'id',ag.id::text,'user',jsonb_build_object(
            'id',au.id::text,'username',au.username,'email',au.email,
            'display_name',COALESCE(NULLIF(ap.display_name,''),NULLIF(TRIM(CONCAT_WS(' ',au.first_name,au.last_name)),''),au.username),
            'avatar',CASE WHEN ap.avatar IS NULL OR ap.avatar='' THEN NULL ELSE '/media/'||ap.avatar END
          ),'availability',ag.availability,'max_active_conversations',ag.max_active_conversations,
          'can_view_all_conversations',ag.can_view_all_conversations,'can_assign_conversations',ag.can_assign_conversations,
          'can_view_analytics',ag.can_view_analytics,'can_manage_websites',ag.can_manage_websites,
          'can_manage_knowledge',ag.can_manage_knowledge,'can_manage_teams',ag.can_manage_teams,
          'can_manage_automations',ag.can_manage_automations,'can_export_data',ag.can_export_data,
          'is_active',ag.is_active,'assigned_website_ids','[]'::jsonb,'team_ids','[]'::jsonb,
          'active_conversation_count',0,'joined_at',ag.joined_at
        ) END,
        'status',sc.status,'priority',sc.priority,'subject',sc.subject,
        'first_response_at',sc.first_response_at,'last_visitor_message_at',sc.last_visitor_message_at,
        'last_agent_message_at',sc.last_agent_message_at,'resolved_at',sc.resolved_at,'closed_at',sc.closed_at,
        'created_at',sc.created_at,'updated_at',sc.updated_at,
        'unread_count',(
          SELECT COUNT(*)::int FROM chat_message um
          LEFT JOIN support_supportconversationreadstate rs ON rs.support_conversation_id=sc.id AND rs.user_id=$2
          LEFT JOIN chat_message rm ON rm.id=rs.last_read_message_id
          WHERE um.conversation_id=sc.conversation_id AND um.sender_id IS NULL AND um.is_deleted=FALSE
            AND (rm.sequence IS NULL OR um.sequence>rm.sequence)
        ),
        'visitor_unread_count',(
          SELECT COUNT(*)::int FROM chat_message vm
          LEFT JOIN chat_message vr ON vr.id=sc.visitor_last_read_message_id
          WHERE vm.conversation_id=sc.conversation_id AND vm.sender_id IS NOT NULL AND vm.is_deleted=FALSE
            AND (vr.sequence IS NULL OR vm.sequence>vr.sequence)
        ),
        'tags',COALESCE((SELECT jsonb_agg(jsonb_build_object(
          'id',t.id::text,'name',t.name,'color',t.color,'is_active',t.is_active,'created_at',t.created_at,'updated_at',t.updated_at
        ) ORDER BY t.name) FROM support_supportconversationtag ct JOIN support_supporttag t ON t.id=ct.tag_id
          WHERE ct.support_conversation_id=sc.id AND t.is_active=TRUE),'[]'::jsonb),
        'service',jsonb_build_object(
          'state',CASE WHEN sc.sla_paused_at IS NOT NULL THEN 'paused'
            WHEN deadlines.is_overdue THEN 'overdue' WHEN deadlines.is_due_soon THEN 'due_soon'
            WHEN deadlines.has_deadlines THEN 'on_track' WHEN sc.status IN ('resolved','closed') THEN 'complete' ELSE 'none' END,
          'active_target',deadlines.active_target,'active_due_at',deadlines.active_due_at,
          'is_overdue',COALESCE(deadlines.is_overdue,FALSE),'is_due_soon',COALESCE(deadlines.is_due_soon,FALSE),
          'overdue_targets',COALESCE(deadlines.overdue_targets,'[]'::jsonb),
          'first_response_due_at',sc.first_response_due_at,'next_response_due_at',sc.next_response_due_at,
          'resolution_due_at',sc.resolution_due_at,'first_response_breached_at',sc.first_response_breached_at,
          'next_response_breached_at',sc.next_response_breached_at,'resolution_breached_at',sc.resolution_breached_at,
          'follow_up_at',sc.follow_up_at,'follow_up_note',sc.follow_up_note,
          'follow_up_due',(sc.follow_up_at IS NOT NULL AND sc.follow_up_completed_at IS NULL AND sc.follow_up_at<=NOW()),
          'follow_up_completed_at',sc.follow_up_completed_at,'follow_up_created_by',NULL
        ),
        'last_message',CASE WHEN lm.id IS NULL THEN NULL ELSE jsonb_build_object(
          'id',lm.id::text,'client_temp_id',lm.client_temp_id,'type',lm.type,'text',lm.text,
          'created_at',lm.created_at,'updated_at',lm.updated_at,'delivery_status',lm.delivery_status,
          'receipt_status',lm.delivery_status,'delivered_at',NULL,'read_at',NULL,
          'sender',CASE WHEN lm.sender_id IS NOT NULL THEN jsonb_build_object(
            'kind',CASE WHEN lm.sender_id=sa.owner_id THEN 'owner' ELSE 'agent' END,'id',lm.sender_id::text,
            'username',lu.username,'display_name',COALESCE(NULLIF(lp.display_name,''),NULLIF(TRIM(CONCAT_WS(' ',lu.first_name,lu.last_name)),''),lu.username),
            'avatar',CASE WHEN lp.avatar IS NULL OR lp.avatar='' THEN NULL ELSE '/media/'||lp.avatar END
          ) ELSE jsonb_build_object('kind','visitor','id',v.id::text,'display_name',COALESCE(NULLIF(v.name,''),'Website visitor'),'avatar',NULL) END,
          'is_own',(lm.sender_id=$2),'voice_note',COALESCE((lm.metadata->>'voice_note')::boolean,FALSE),
          'attachments','[]'::jsonb,
          'preview_text',CASE WHEN lm.text<>'' THEN lm.text WHEN COALESCE((lm.metadata->>'voice_note')::boolean,FALSE) THEN 'Voice message'
            WHEN attachments.count>1 THEN attachments.count::text||' attachments'
            ELSE COALESCE(attachments.preview,'Support message') END
        ) END,
        'csat',NULL
      )
      FROM support_supportconversation sc
      JOIN support_supportwebsite w ON w.id=sc.website_id
      JOIN support_supportaccount sa ON sa.id=w.support_account_id
      JOIN support_supportvisitor v ON v.id=sc.visitor_id
      LEFT JOIN support_supportagent ag ON ag.id=sc.assigned_agent_id
      LEFT JOIN accounts_user au ON au.id=ag.user_id
      LEFT JOIN accounts_profile ap ON ap.user_id=au.id
      LEFT JOIN LATERAL (
        SELECT m.* FROM chat_message m WHERE m.conversation_id=sc.conversation_id AND m.is_deleted=FALSE
        ORDER BY m.sequence DESC NULLS LAST,m.created_at DESC,m.id DESC LIMIT 1
      ) lm ON TRUE
      LEFT JOIN accounts_user lu ON lu.id=lm.sender_id
      LEFT JOIN accounts_profile lp ON lp.user_id=lu.id
      LEFT JOIN LATERAL (
        SELECT COUNT(*)::int AS count,
          (ARRAY_AGG(CASE a.media_kind WHEN 'image' THEN 'Photo' WHEN 'video' THEN 'Video' WHEN 'audio' THEN 'Audio' ELSE NULLIF(a.original_name,'') END ORDER BY a.created_at))[1] AS preview
        FROM chat_messageattachment a WHERE a.message_id=lm.id
      ) attachments ON TRUE
      LEFT JOIN support_supportservicesettings ss ON ss.support_account_id=w.support_account_id
      LEFT JOIN support_supportslapolicy wp ON wp.support_account_id=w.support_account_id AND wp.website_id=sc.website_id AND wp.is_active=TRUE
      LEFT JOIN support_supportslapolicy tp ON tp.support_account_id=w.support_account_id AND tp.team_id=sc.assigned_team_id AND tp.is_active=TRUE
      LEFT JOIN LATERAL (
        SELECT (ARRAY_AGG(d.name ORDER BY d.due_at))[1] AS active_target,MIN(d.due_at) AS active_due_at,
          COALESCE(BOOL_OR(d.due_at<=NOW()),FALSE) AS is_overdue,
          COALESCE(BOOL_OR(d.due_at>NOW() AND d.due_at<=NOW()+make_interval(mins=>COALESCE(wp.due_soon_minutes,tp.due_soon_minutes,ss.due_soon_minutes,15)::int)),FALSE) AS is_due_soon,
          COALESCE(JSONB_AGG(d.name ORDER BY d.due_at) FILTER (WHERE d.due_at<=NOW()),'[]'::jsonb) AS overdue_targets,
          COUNT(*)>0 AS has_deadlines
        FROM (VALUES
          ('first_response'::text,CASE WHEN sc.first_response_at IS NULL THEN sc.first_response_due_at END),
          ('next_response'::text,CASE WHEN sc.first_response_at IS NOT NULL AND sc.next_response_due_at IS NOT NULL AND sc.last_visitor_message_at IS NOT NULL AND (sc.last_agent_message_at IS NULL OR sc.last_visitor_message_at>sc.last_agent_message_at) THEN sc.next_response_due_at END),
          ('resolution'::text,sc.resolution_due_at)
        ) d(name,due_at) WHERE d.due_at IS NOT NULL
      ) deadlines ON TRUE
      WHERE sc.id=ANY($1)
    "#;
    let rows = sqlx::query_as::<_, (Uuid, Value)>(sql)
        .bind(support_ids).bind(viewer_user_id).persistent(false).fetch_all(pool).await.map_err(internal_error)?;
    Ok(rows.into_iter().collect())
}

async fn message_list_json(
    pool: &PgPool,
    support_id: Uuid,
    viewer_user_id: Option<i64>,
    visitor_view: bool,
    widget_ids: Option<(Uuid, Uuid)>,
    limit: i64,
    latest_only: bool,
) -> SupportResult<Vec<Value>> {
    let (site_key, session_id) = widget_ids.unwrap_or((Uuid::nil(), Uuid::nil()));
    let sql = r#"
      WITH selected AS (
        SELECT m.* FROM chat_message m
        JOIN support_supportconversation sc ON sc.conversation_id=m.conversation_id
        WHERE sc.id=$1 AND m.is_deleted=FALSE
        ORDER BY m.sequence DESC NULLS LAST, m.created_at DESC, m.id DESC
        LIMIT $2
      ), receipt AS (
        SELECT sc.*,
          (SELECT MAX(md.sequence) FROM support_supportconversationreadstate rs JOIN chat_message md ON md.id=rs.last_delivered_message_id WHERE rs.support_conversation_id=sc.id) AS team_delivered_seq,
          (SELECT MAX(mr.sequence) FROM support_supportconversationreadstate rs JOIN chat_message mr ON mr.id=rs.last_read_message_id WHERE rs.support_conversation_id=sc.id) AS team_read_seq,
          vd.sequence AS visitor_delivered_seq, vr.sequence AS visitor_read_seq
        FROM support_supportconversation sc
        LEFT JOIN chat_message vd ON vd.id=sc.visitor_last_delivered_message_id
        LEFT JOIN chat_message vr ON vr.id=sc.visitor_last_read_message_id
        WHERE sc.id=$1
      )
      SELECT jsonb_build_object(
        'id',m.id::text,'client_temp_id',m.client_temp_id,'type',m.type,'text',m.text,
        'created_at',m.created_at,'updated_at',m.updated_at,'delivery_status',m.delivery_status,
        'receipt_status',CASE
          WHEN m.sender_id IS NULL AND r.team_read_seq>=m.sequence THEN 'read'
          WHEN m.sender_id IS NULL AND r.team_delivered_seq>=m.sequence THEN 'delivered'
          WHEN m.sender_id IS NOT NULL AND r.visitor_read_seq>=m.sequence THEN 'read'
          WHEN m.sender_id IS NOT NULL AND r.visitor_delivered_seq>=m.sequence THEN 'delivered'
          ELSE m.delivery_status END,
        'delivered_at',CASE WHEN m.sender_id IS NULL AND r.team_delivered_seq>=m.sequence THEN (SELECT MAX(last_delivered_at) FROM support_supportconversationreadstate WHERE support_conversation_id=$1)
                            WHEN m.sender_id IS NOT NULL AND r.visitor_delivered_seq>=m.sequence THEN r.visitor_last_delivered_at ELSE NULL END,
        'read_at',CASE WHEN m.sender_id IS NULL AND r.team_read_seq>=m.sequence THEN (SELECT MAX(last_read_at) FROM support_supportconversationreadstate WHERE support_conversation_id=$1)
                      WHEN m.sender_id IS NOT NULL AND r.visitor_read_seq>=m.sequence THEN r.visitor_last_read_at ELSE NULL END,
        'sender',CASE WHEN m.sender_id IS NOT NULL THEN jsonb_build_object(
           'kind',CASE WHEN m.sender_id=sa.owner_id THEN 'owner' ELSE 'agent' END,
           'id',m.sender_id::text,'username',u.username,
           'display_name',COALESCE(NULLIF(p.display_name,''),NULLIF(TRIM(CONCAT_WS(' ',u.first_name,u.last_name)),''),u.username),
           'avatar',CASE WHEN p.avatar IS NULL OR p.avatar='' THEN NULL ELSE '/media/'||p.avatar END
        ) WHEN sma.id IS NOT NULL THEN jsonb_build_object('kind','visitor','id',sma.visitor_id::text,'display_name',COALESCE(NULLIF(sma.display_name,''),NULLIF(v.name,''),'Website visitor'),'avatar',NULL)
          ELSE jsonb_build_object('kind','system','id',NULL,'display_name','Support Chat','avatar',NULL) END,
        'is_own',CASE WHEN $3::bigint IS NOT NULL THEN m.sender_id=$3 ELSE (sma.visitor_id=r.visitor_id) END,
        'voice_note',COALESCE((m.metadata->>'voice_note')::boolean,FALSE),
        'attachments',COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'id',a.id::text,'media_kind',a.media_kind,'original_name',a.original_name,'mime_type',a.mime_type,'size',a.size,
            'width',a.width,'height',a.height,'rotation',a.rotation,'duration_seconds',a.duration_seconds,'scan_status',a.scan_status,
            'can_preview_inline',(a.mime_type LIKE 'image/%' OR a.mime_type LIKE 'audio/%' OR a.mime_type LIKE 'video/%' OR a.mime_type='application/pdf'),
            'download_url',CASE WHEN $4 THEN '/api/v1/support/widget/'||$5::text||'/sessions/'||$6::text||'/attachments/'||a.id::text||'/download/' ELSE '/api/v1/support/attachments/'||a.id::text||'/download/' END,
            'preview_url',CASE WHEN (a.mime_type LIKE 'image/%' OR a.mime_type LIKE 'audio/%' OR a.mime_type LIKE 'video/%' OR a.mime_type='application/pdf') THEN CASE WHEN $4 THEN '/api/v1/support/widget/'||$5::text||'/sessions/'||$6::text||'/attachments/'||a.id::text||'/preview/' ELSE '/api/v1/support/attachments/'||a.id::text||'/preview/' END ELSE NULL END,
            'thumbnail_url',CASE WHEN a.thumbnail IS NOT NULL AND a.thumbnail<>'' THEN CASE WHEN $4 THEN '/api/v1/support/widget/'||$5::text||'/sessions/'||$6::text||'/attachments/'||a.id::text||'/thumbnail/' ELSE '/api/v1/support/attachments/'||a.id::text||'/thumbnail/' END ELSE NULL END
        ) ORDER BY a.created_at) FROM chat_messageattachment a WHERE a.message_id=m.id),'[]'::jsonb),
        'preview_text',CASE WHEN m.text<>'' THEN m.text WHEN COALESCE((m.metadata->>'voice_note')::boolean,FALSE) THEN 'Voice message'
          WHEN (SELECT COUNT(*) FROM chat_messageattachment a WHERE a.message_id=m.id)>1 THEN (SELECT COUNT(*)::text||' attachments' FROM chat_messageattachment a WHERE a.message_id=m.id)
          ELSE COALESCE((SELECT CASE a.media_kind WHEN 'image' THEN 'Photo' WHEN 'video' THEN 'Video' WHEN 'audio' THEN 'Audio' ELSE NULLIF(a.original_name,'') END FROM chat_messageattachment a WHERE a.message_id=m.id ORDER BY a.created_at LIMIT 1),'Support message') END
      )
      FROM selected m
      JOIN receipt r ON TRUE
      JOIN support_supportwebsite w ON w.id=r.website_id
      JOIN support_supportaccount sa ON sa.id=w.support_account_id
      LEFT JOIN accounts_user u ON u.id=m.sender_id
      LEFT JOIN accounts_profile p ON p.user_id=u.id
      LEFT JOIN support_supportmessageauthor sma ON sma.message_id=m.id
      LEFT JOIN support_supportvisitor v ON v.id=sma.visitor_id
      ORDER BY CASE WHEN $7 THEN m.sequence END DESC NULLS LAST,
               CASE WHEN NOT $7 THEN m.sequence END ASC NULLS LAST,
               m.created_at ASC, m.id ASC
    "#;
    sqlx::query_scalar::<_, Value>(sql)
        .bind(support_id).bind(limit.clamp(1, 200)).bind(viewer_user_id).bind(visitor_view)
        .bind(site_key).bind(session_id).bind(latest_only)
        .persistent(false).fetch_all(pool).await.map_err(internal_error)
}

async fn emit_durable(
    tx: &mut Transaction<'_, Postgres>,
    event_name: &str,
    data: Value,
    audiences: Vec<AudienceKey>,
) -> SupportResult<CommittedEvent> {
    let event_id = Uuid::new_v4();
    let outbox_id = Uuid::new_v4();
    let payload = json!({
        "type":"chat.event","version":1,"event":event_name,"event_id":event_id.to_string(),
        "occurred_at":Value::Null,"data":data
    });
    let audience_json = serde_json::to_value(&audiences).unwrap_or_else(|_| json!([]));
    sqlx::query("INSERT INTO common_realtimeoutboxevent (id,created_at,updated_at,event_id,event_name,payload,audiences,status,attempts,available_at,published_at,delivery_target,published_transport,stream_entry_id,last_error) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,'pending',0,NOW(),NULL,'nats_jetstream','','','')")
        .bind(outbox_id).bind(event_id).bind(event_name).bind(&payload).bind(&audience_json)
        .persistent(false).execute(&mut **tx).await.map_err(internal_error)?;
    Ok(CommittedEvent { event_id, event_name: event_name.to_owned(), payload, audiences })
}

async fn enqueue_control_job(
    tx: &mut Transaction<'_, Postgres>,
    kind: &str,
    dedupe_key: &str,
    support_id: Uuid,
    message_id: Option<Uuid>,
    payload: Value,
) -> SupportResult<()> {
    sqlx::query(
        "INSERT INTO support_supportdataplanejob          (id,created_at,updated_at,kind,dedupe_key,support_conversation_id,message_id,payload,status,attempts,available_at,locked_at,last_error)          VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,$6,'pending',0,NOW(),NULL,'')          ON CONFLICT (dedupe_key) DO NOTHING",
    )
    .bind(Uuid::new_v4())
    .bind(kind)
    .bind(dedupe_key)
    .bind(support_id)
    .bind(message_id)
    .bind(payload)
    .persistent(false)
    .execute(&mut **tx)
    .await
    .map_err(internal_error)?;
    Ok(())
}

async fn insert_support_audit(
    tx: &mut Transaction<'_, Postgres>,
    support_id: Uuid,
    actor_id: Option<i64>,
    action: &str,
    target_type: &str,
    target_id: Option<Uuid>,
    summary: &str,
    metadata: Value,
) -> SupportResult<()> {
    sqlx::query(
        "INSERT INTO support_supportauditevent          (id,created_at,updated_at,support_account_id,website_id,support_conversation_id,actor_id,action,target_type,target_id,summary,metadata,ip_address)          SELECT $1,NOW(),NOW(),w.support_account_id,sc.website_id,sc.id,$2,$3,$4,$5,$6,$7,NULL          FROM support_supportconversation sc JOIN support_supportwebsite w ON w.id=sc.website_id WHERE sc.id=$8",
    )
    .bind(Uuid::new_v4())
    .bind(actor_id)
    .bind(action)
    .bind(target_type)
    .bind(target_id)
    .bind(summary.chars().take(255).collect::<String>())
    .bind(metadata)
    .bind(support_id)
    .persistent(false)
    .execute(&mut **tx)
    .await
    .map_err(internal_error)?;
    Ok(())
}

async fn attach_uploads(
    tx: &mut Transaction<'_, Postgres>,
    support_id: Uuid,
    message_id: Uuid,
    attachment_ids: &[Uuid],
    team_user_id: Option<i64>,
    widget: Option<&WidgetContext>,
    voice_note: bool,
) -> SupportResult<()> {
    if attachment_ids.is_empty() { return Ok(()); }
    if attachment_ids.len() > 8 || (voice_note && attachment_ids.len() != 1) {
        return Err(SupportError::new(StatusCode::BAD_REQUEST, "invalid_attachments", "The attachment selection is invalid."));
    }
    for upload_id in attachment_ids {
        let sql = r#"
          SELECT pu.id, pu.file, pu.original_name, pu.media_kind, pu.mime_type, pu.size, pu.width, pu.height,
                 pu.rotation, pu.duration_seconds::text, pu.thumbnail, pu.scan_status, pu.scan_notes, pu.scanned_at::text, pu.metadata
          FROM chat_pendingupload pu
          JOIN support_supportpendingupload su ON su.pending_upload_id=pu.id
          WHERE pu.id=$1 AND pu.purpose='support' AND pu.status='pending' AND pu.scan_status='clean' AND pu.expires_at>NOW()
            AND su.support_conversation_id=$2
            AND (($3::bigint IS NOT NULL AND su.source='team' AND su.uploaded_by_id=$3)
              OR ($4::uuid IS NOT NULL AND su.source='visitor' AND su.widget_session_id=$4 AND su.visitor_id=$5))
          FOR UPDATE OF pu,su
        "#;
        let row = sqlx::query_as::<_, (Uuid,String,String,String,String,i64,Option<i32>,Option<i32>,Option<i32>,Option<String>,Option<String>,String,String,Option<String>,Value)>(sql)
            .bind(upload_id).bind(support_id).bind(team_user_id).bind(widget.map(|v|v.session_id)).bind(widget.map(|v|v.visitor_id))
            .persistent(false).fetch_optional(&mut **tx).await.map_err(internal_error)?;
        let Some((_id,file,original_name,media_kind,mime_type,size,width,height,rotation,duration,thumbnail,scan_status,scan_notes,scanned_at,mut metadata)) = row else {
            return Err(SupportError::new(StatusCode::CONFLICT, "upload_not_approved", "One or more uploads are not approved for this conversation."));
        };
        if voice_note && media_kind != "audio" {
            return Err(SupportError::new(StatusCode::BAD_REQUEST, "voice_upload_invalid", "A voice message requires one approved audio upload."));
        }
        if let Some(object) = metadata.as_object_mut() {
            object.insert("source_pending_upload_id".to_owned(), Value::String(upload_id.to_string()));
        }
        let attachment_id=Uuid::new_v4();
        sqlx::query("INSERT INTO chat_messageattachment (id,created_at,updated_at,message_id,file,original_name,media_kind,mime_type,size,width,height,rotation,duration_seconds,thumbnail,scan_status,scan_notes,scanned_at,metadata,view_once) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::numeric,$12,$13,$14,$15::timestamptz,$16,FALSE)")
            .bind(attachment_id).bind(message_id).bind(file).bind(original_name).bind(media_kind).bind(mime_type).bind(size)
            .bind(width).bind(height).bind(rotation).bind(duration).bind(thumbnail).bind(scan_status).bind(scan_notes).bind(scanned_at).bind(metadata)
            .persistent(false).execute(&mut **tx).await.map_err(internal_error)?;
        sqlx::query("UPDATE chat_pendingupload SET status='attached',updated_at=NOW() WHERE id=$1")
            .bind(upload_id).persistent(false).execute(&mut **tx).await.map_err(internal_error)?;
    }
    Ok(())
}

async fn route_conversation(
    tx: &mut Transaction<'_, Postgres>,
    support_id: Uuid,
    trigger: &str,
) -> SupportResult<()> {
    let row = sqlx::query_as::<_, (Uuid, Uuid, Option<Uuid>, Option<Uuid>, String, bool, String)>(r#"
      SELECT sc.website_id,w.support_account_id,sc.assigned_agent_id,rp.id,
             COALESCE(rp.mode,'manual'),COALESCE(rp.enabled,FALSE),
             COALESCE(rp.overflow_behavior,'leave_unassigned')
      FROM support_supportconversation sc
      JOIN support_supportwebsite w ON w.id=sc.website_id
      LEFT JOIN support_supportroutingpolicy rp ON rp.website_id=w.id
      WHERE sc.id=$1
      FOR UPDATE OF sc
    "#)
        .bind(support_id)
        .persistent(false)
        .fetch_one(&mut **tx)
        .await
        .map_err(internal_error)?;
    let (website_id, account_id, assigned, policy_id, mode, enabled, overflow) = row;
    if assigned.is_some() || !enabled || mode == "manual" {
        return Ok(());
    }

    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
        .bind(format!("support-route:{website_id}"))
        .persistent(false)
        .execute(&mut **tx)
        .await
        .map_err(internal_error)?;

    let team_id = sqlx::query_scalar::<_, Uuid>(r#"
      SELECT wt.team_id
      FROM support_supportwebsiteteam wt
      JOIN support_supportteam t ON t.id=wt.team_id AND t.is_active=TRUE
      WHERE wt.website_id=$1
      ORDER BY wt.is_default DESC,wt.created_at,wt.id
      LIMIT 1
    "#)
        .bind(website_id)
        .persistent(false)
        .fetch_optional(&mut **tx)
        .await
        .map_err(internal_error)?;

    async fn candidates(
        tx: &mut Transaction<'_, Postgres>,
        website_id: Uuid,
        team_id: Option<Uuid>,
        account_id: Uuid,
        enforce_capacity: bool,
    ) -> SupportResult<Vec<(Uuid, i64)>> {
        sqlx::query_as::<_, (Uuid, i64)>(r#"
          SELECT ag.id,counts.active_count
          FROM support_supportagent ag
          JOIN accounts_user u ON u.id=ag.user_id AND u.is_active=TRUE
          JOIN support_supportwebsiteagent wa ON wa.agent_id=ag.id AND wa.website_id=$1
          LEFT JOIN support_supportteammembership tm ON tm.agent_id=ag.id AND tm.team_id=$2
          LEFT JOIN LATERAL (
            SELECT COUNT(*)::bigint AS active_count
            FROM support_supportconversation c
            WHERE c.assigned_agent_id=ag.id
              AND c.status IN ('new','open','waiting_customer','waiting_team')
              AND (c.snoozed_until IS NULL OR c.snoozed_until<=NOW())
          ) counts ON TRUE
          WHERE ag.support_account_id=$3 AND ag.is_active=TRUE AND ag.availability='available'
            AND ($2::uuid IS NULL OR tm.id IS NOT NULL)
            AND (NOT $4 OR counts.active_count<ag.max_active_conversations)
          ORDER BY ag.joined_at,ag.id
          FOR UPDATE OF ag
        "#)
            .bind(website_id)
            .bind(team_id)
            .bind(account_id)
            .bind(enforce_capacity)
            .persistent(false)
            .fetch_all(&mut **tx)
            .await
            .map_err(internal_error)
    }

    let mut eligible = candidates(tx, website_id, team_id, account_id, true).await?;
    if eligible.is_empty() && overflow == "least_busy" {
        eligible = candidates(tx, website_id, team_id, account_id, false).await?;
    }
    if eligible.is_empty() {
        return Ok(());
    }

    let chosen = if mode == "least_busy" {
        eligible
            .iter()
            .min_by_key(|(agent_id, active_count)| (*active_count, *agent_id))
            .map(|(agent_id, _)| *agent_id)
            .expect("eligible routing candidate")
    } else {
        let policy_id = policy_id.ok_or_else(|| {
            SupportError::new(
                StatusCode::CONFLICT,
                "routing_policy_missing",
                "The automatic routing policy is not available.",
            )
        })?;
        sqlx::query(r#"
          INSERT INTO support_supportroutingcursor
            (id,created_at,updated_at,policy_id,last_assigned_agent_id,assignment_count)
          VALUES ($1,NOW(),NOW(),$2,NULL,0)
          ON CONFLICT (policy_id) DO NOTHING
        "#)
            .bind(Uuid::new_v4())
            .bind(policy_id)
            .persistent(false)
            .execute(&mut **tx)
            .await
            .map_err(internal_error)?;
        let last = sqlx::query_scalar::<_, Option<Uuid>>(r#"
          SELECT last_assigned_agent_id
          FROM support_supportroutingcursor
          WHERE policy_id=$1
          FOR UPDATE
        "#)
            .bind(policy_id)
            .persistent(false)
            .fetch_optional(&mut **tx)
            .await
            .map_err(internal_error)?
            .flatten();
        let index = last
            .and_then(|last_id| eligible.iter().position(|(agent_id, _)| *agent_id == last_id))
            .map(|index| (index + 1) % eligible.len())
            .unwrap_or(0);
        let chosen = eligible[index].0;
        sqlx::query(r#"
          UPDATE support_supportroutingcursor
          SET last_assigned_agent_id=$2,assignment_count=assignment_count+1,updated_at=NOW()
          WHERE policy_id=$1
        "#)
            .bind(policy_id)
            .bind(chosen)
            .persistent(false)
            .execute(&mut **tx)
            .await
            .map_err(internal_error)?;
        chosen
    };

    sqlx::query(r#"
      UPDATE support_supportconversation
      SET assigned_agent_id=$2,assigned_team_id=$3,assigned_at=NOW(),assignment_trigger=$4,
          status=CASE WHEN status='new' THEN 'open' ELSE status END,
          revision_number=revision_number+1,updated_at=NOW()
      WHERE id=$1 AND assigned_agent_id IS NULL
    "#)
        .bind(support_id)
        .bind(chosen)
        .bind(team_id)
        .bind(trigger)
        .persistent(false)
        .execute(&mut **tx)
        .await
        .map_err(internal_error)?;
    Ok(())
}

async fn create_message(
    state: &Arc<AppState>,
    support_id: Uuid,
    team: Option<&TeamContext>,
    widget: Option<&WidgetContext>,
    input: MessageInput,
) -> SupportResult<(Value, Vec<CommittedEvent>)> {
    let text=input.text.trim().to_owned();
    if text.chars().count()>10_000 { return Err(SupportError::new(StatusCode::BAD_REQUEST,"message_too_long","Messages can contain at most 10,000 characters.")); }
    if text.is_empty() && input.attachment_ids.is_empty() { return Err(SupportError::new(StatusCode::BAD_REQUEST,"empty_message","Write a message or add an attachment before sending.")); }
    let client_temp_id=input.client_temp_id.trim().chars().take(100).collect::<String>();
    let pool=pool(state)?;
    let mut tx=pool.begin().await.map_err(internal_error)?;
    let (chat_id,website_id,visitor_id,assigned_agent,status)=if let Some(team)=team {
        let visibility=visibility_sql(team,"sc","w");
        let sql=format!("SELECT sc.conversation_id,sc.website_id,sc.visitor_id,sc.assigned_agent_id,sc.status FROM support_supportconversation sc JOIN support_supportwebsite w ON w.id=sc.website_id WHERE sc.id=$1 AND {visibility} FOR UPDATE OF sc");
        let mut q=sqlx::query_as::<_,(Uuid,Uuid,Uuid,Option<Uuid>,String)>(&sql).bind(support_id).bind(team.account_id);
        if team.role=="agent" { q=q.bind(team.agent_id); }
        let row=q.persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?.ok_or_else(||SupportError::new(StatusCode::NOT_FOUND,"conversation_not_found","Support conversation was not found."))?;
        if row.4=="closed" { return Err(SupportError::new(StatusCode::CONFLICT,"conversation_closed","Closed conversations cannot receive new replies.")); }
        if team.role=="agent" && row.3.is_some() && row.3!=team.agent_id && !team.can_assign { return Err(SupportError::new(StatusCode::FORBIDDEN,"conversation_assigned_elsewhere","This conversation is assigned to another agent.")); }
        row
    } else {
        let widget=widget.expect("widget identity required");
        sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))").bind(format!("support-visitor:{}",widget.visitor_id)).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
        let existing=sqlx::query_as::<_,(Uuid,Uuid,Uuid,Option<Uuid>,String)>("SELECT sc.conversation_id,sc.website_id,sc.visitor_id,sc.assigned_agent_id,sc.status FROM support_supportconversation sc WHERE sc.visitor_id=$1 AND sc.website_id=$2 FOR UPDATE")
            .bind(widget.visitor_id).bind(widget.website_id).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?;
        if let Some(row)=existing { row } else {
            let chat_id=Uuid::new_v4(); let support_id_new=Uuid::new_v4();
            sqlx::query("INSERT INTO chat_conversation (id,created_at,updated_at,type,title,slug,avatar,created_by_id,is_active,direct_key,last_message_id,last_message_at,e2ee_key_version,e2ee_rekey_required,e2ee_last_key_rotation_at,e2ee_last_security_event_at,next_message_sequence) VALUES ($1,NOW(),NOW(),'direct',$2,NULL,'',NULL,TRUE,NULL,NULL,NULL,1,FALSE,NULL,NULL,0)")
                .bind(chat_id).bind(format!("{} · {}",widget.website_name,widget.visitor_name).chars().take(255).collect::<String>()).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
            sqlx::query("INSERT INTO support_supportconversation (id,created_at,updated_at,conversation_id,website_id,visitor_id,assigned_agent_id,assigned_team_id,assigned_at,assignment_trigger,status,priority,subject,first_response_at,last_visitor_message_at,last_agent_message_at,visitor_last_read_message_id,visitor_last_read_at,visitor_last_delivered_message_id,visitor_last_delivered_at,first_response_due_at,next_response_due_at,resolution_due_at,first_response_breached_at,next_response_breached_at,resolution_breached_at,sla_paused_at,sla_pause_reason,sla_total_paused_seconds,sla_last_recalculated_at,sla_escalated_at,follow_up_at,follow_up_note,follow_up_created_by_id,follow_up_completed_at,follow_up_assignee_id,previous_status,snoozed_until,snoozed_by_id,resolution_reason,closure_reason,reopened_at,reopen_count,revision_number,resolved_at,closed_at) VALUES ($1,NOW(),NOW(),$2,$3,$4,NULL,NULL,NULL,'','new','normal',$5,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'',0,NULL,NULL,NULL,'',NULL,NULL,NULL,'',NULL,NULL,'','',NULL,0,1,NULL,NULL)")
                .bind(support_id_new).bind(chat_id).bind(widget.website_id).bind(widget.visitor_id).bind(format!("Conversation with {}",widget.visitor_name).chars().take(255).collect::<String>()).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
            route_conversation(&mut tx,support_id_new,"conversation_created").await?;
            enqueue_control_job(
                &mut tx,
                "conversation_created",
                &format!("conversation-created:{support_id_new}"),
                support_id_new,
                None,
                json!({"website_id":widget.website_id.to_string(),"visitor_id":widget.visitor_id.to_string()}),
            ).await?;
            (chat_id,widget.website_id,widget.visitor_id,None,"new".to_owned())
        }
    };
    let actual_support_id=sqlx::query_scalar::<_,Uuid>("SELECT id FROM support_supportconversation WHERE conversation_id=$1").bind(chat_id).persistent(false).fetch_one(&mut *tx).await.map_err(internal_error)?;
    if status=="closed" { return Err(SupportError::new(StatusCode::GONE,"conversation_closed","This support conversation is closed.")); }
    if let Some(widget) = widget {
        let recent = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*)::bigint FROM support_supportmessageauthor sma              JOIN chat_message m ON m.id=sma.message_id              WHERE sma.visitor_id=$1 AND m.created_at>=NOW()-INTERVAL '1 minute'",
        )
        .bind(widget.visitor_id).persistent(false).fetch_one(&mut *tx).await.map_err(internal_error)?;
        if recent >= state.config.support_widget_message_rate_per_minute {
            return Err(SupportError::new(StatusCode::TOO_MANY_REQUESTS,"message_rate_limited","Too many messages were sent. Try again shortly."));
        }
    }
    if !client_temp_id.is_empty() {
        let existing=if let Some(team)=team {
            sqlx::query_scalar::<_,Uuid>("SELECT id FROM chat_message WHERE conversation_id=$1 AND sender_id=$2 AND client_temp_id=$3 LIMIT 1").bind(chat_id).bind(team.user_id).bind(&client_temp_id).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?
        } else {
            sqlx::query_scalar::<_,Uuid>("SELECT id FROM chat_message WHERE conversation_id=$1 AND sender_id IS NULL AND client_temp_id=$2 LIMIT 1").bind(chat_id).bind(&client_temp_id).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?
        };
        if let Some(existing_id)=existing {
            tx.commit().await.map_err(internal_error)?;
            let messages=message_list_json(pool,actual_support_id,team.map(|v|v.user_id),widget.is_some(),widget.map(|v|(v.site_key,v.session_id)),200,false).await?;
            let found=messages.into_iter().find(|m|m.get("id").and_then(Value::as_str)==Some(&existing_id.to_string())).unwrap_or(Value::Null);
            return Ok((found,Vec::new()));
        }
    }
    let sequence=sqlx::query_scalar::<_,i64>("UPDATE chat_conversation SET next_message_sequence=next_message_sequence+1,updated_at=NOW() WHERE id=$1 RETURNING next_message_sequence::bigint")
        .bind(chat_id).persistent(false).fetch_one(&mut *tx).await.map_err(internal_error)?;
    let message_id=Uuid::new_v4();
    let message_type=if input.voice_note {"audio".to_owned()} else if text.is_empty() && input.attachment_ids.len()==1 {
        sqlx::query_scalar::<_,String>("SELECT pu.media_kind FROM chat_pendingupload pu WHERE pu.id=$1").bind(input.attachment_ids[0]).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?.unwrap_or_else(||"file".to_owned())
    } else {"text".to_owned()};
    let metadata=if input.voice_note {json!({"voice_note":true})} else {json!({})};
    let sender_id=team.map(|v|v.user_id);
    sqlx::query("INSERT INTO chat_message (id,created_at,updated_at,conversation_id,sender_id,type,text,metadata,reply_to_id,forwarded_from_id,is_edited,edited_at,edit_locked_at,edit_locked_reason,is_deleted,deleted_at,deleted_text_backup,deletion_source,client_temp_id,sequence,delivery_status,failed_reason,retry_count) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,$6,NULL,NULL,FALSE,NULL,NULL,'',FALSE,NULL,'','',$7,$8,'sent','',0)")
        .bind(message_id).bind(chat_id).bind(sender_id).bind(message_type).bind(&text).bind(&metadata).bind(&client_temp_id).bind(sequence)
        .persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
    if let Some(widget)=widget {
        sqlx::query("INSERT INTO support_supportmessageauthor (id,created_at,updated_at,message_id,visitor_id,session_id,display_name) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5)")
            .bind(Uuid::new_v4()).bind(message_id).bind(widget.visitor_id).bind(widget.session_id).bind(&widget.visitor_name).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
    }
    attach_uploads(&mut tx,actual_support_id,message_id,&input.attachment_ids,team.map(|v|v.user_id),widget,input.voice_note).await?;
    sqlx::query("UPDATE chat_conversation SET last_message_id=$2,last_message_at=NOW(),updated_at=NOW() WHERE id=$1").bind(chat_id).bind(message_id).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
    if let Some(team)=team {
        sqlx::query("UPDATE support_supportconversation SET assigned_agent_id=COALESCE(assigned_agent_id,$2),assigned_at=CASE WHEN assigned_agent_id IS NULL AND $2 IS NOT NULL THEN NOW() ELSE assigned_at END,last_agent_message_at=NOW(),first_response_at=COALESCE(first_response_at,NOW()),status=CASE WHEN status IN ('new','open','waiting_team','resolved') THEN 'waiting_customer' ELSE status END,resolved_at=NULL,revision_number=revision_number+1,updated_at=NOW() WHERE id=$1")
            .bind(actual_support_id).bind(team.agent_id).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
    } else {
        sqlx::query("UPDATE support_supportconversation SET last_visitor_message_at=NOW(),status=CASE WHEN status IN ('new','open','waiting_customer','waiting_team','resolved') THEN 'open' ELSE status END,resolved_at=NULL,revision_number=revision_number+1,updated_at=NOW() WHERE id=$1")
            .bind(actual_support_id).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
    }
    let sender_kind=if let Some(team)=team {team.role} else {"visitor"};
    enqueue_control_job(
        &mut tx,
        "message_created",
        &format!("message-created:{message_id}"),
        actual_support_id,
        Some(message_id),
        json!({"sender_kind":sender_kind,"website_id":website_id.to_string(),"visitor_id":visitor_id.to_string()}),
    ).await?;
    let event_data=json!({"conversation_id":actual_support_id.to_string(),"website_id":website_id.to_string(),"visitor_id":visitor_id.to_string(),"message_id":message_id.to_string(),"client_temp_id":client_temp_id,"message_type":message_type,"text":text,"attachment_count":input.attachment_ids.len(),"sender":{"kind":sender_kind}});
    let audiences=vec![AudienceKey{kind:AudienceKind::SupportWebsite,identifier:website_id.to_string()},AudienceKey{kind:AudienceKind::SupportVisitor,identifier:visitor_id.to_string()}];
    let event=emit_durable(&mut tx,"support.message.created",event_data,audiences).await?;
    tx.commit().await.map_err(internal_error)?;
    let payload=message_list_json(pool,actual_support_id,team.map(|v|v.user_id),widget.is_some(),widget.map(|v|(v.site_key,v.session_id)),200,false).await?.into_iter().find(|m|m.get("id").and_then(Value::as_str)==Some(&message_id.to_string())).unwrap_or(Value::Null);
    Ok((payload,vec![event]))
}

async fn update_receipt(
    state:&Arc<AppState>,support_id:Uuid,team:Option<&TeamContext>,widget:Option<&WidgetContext>,input:ReceiptInput,read:bool
)->SupportResult<Vec<CommittedEvent>>{
    let pool=pool(state)?; let mut tx=pool.begin().await.map_err(internal_error)?;
    let (chat_id,website_id,visitor_id)=if let Some(team)=team {
        let visibility=visibility_sql(team,"sc","w");
        let sql=format!("SELECT sc.conversation_id,sc.website_id,sc.visitor_id FROM support_supportconversation sc JOIN support_supportwebsite w ON w.id=sc.website_id WHERE sc.id=$1 AND {visibility} FOR SHARE OF sc");
        let mut query=sqlx::query_as::<_,(Uuid,Uuid,Uuid)>(&sql).bind(support_id).bind(team.account_id);
        if team.role=="agent" { query=query.bind(team.agent_id); }
        query.persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?
            .ok_or_else(||SupportError::new(StatusCode::NOT_FOUND,"conversation_not_found","Support conversation was not found."))?
    } else { let widget=widget.ok_or_else(||SupportError::new(StatusCode::UNAUTHORIZED,"widget_session_required","A valid widget session is required."))?; let row=sqlx::query_as::<_,(Uuid,Uuid,Uuid)>("SELECT sc.conversation_id,sc.website_id,sc.visitor_id FROM support_supportconversation sc WHERE sc.website_id=$1 AND sc.visitor_id=$2 FOR SHARE OF sc").bind(widget.website_id).bind(widget.visitor_id).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?; match row{Some(v)=>v,None=>{tx.commit().await.map_err(internal_error)?;return Ok(Vec::new())}} };
    let sender_filter=if team.is_some(){"sender_id IS NULL"}else{"sender_id IS NOT NULL"};
    let target_sql=if input.message_id.is_some(){format!("SELECT id,sequence FROM chat_message WHERE conversation_id=$1 AND id=$2 AND {sender_filter} AND is_deleted=FALSE")}else{format!("SELECT id,sequence FROM chat_message WHERE conversation_id=$1 AND {sender_filter} AND is_deleted=FALSE ORDER BY sequence DESC NULLS LAST,created_at DESC LIMIT 1")};
    let target=if let Some(id)=input.message_id {sqlx::query_as::<_,(Uuid,Option<i64>)>(&target_sql).bind(chat_id).bind(id).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?}else{sqlx::query_as::<_,(Uuid,Option<i64>)>(&target_sql).bind(chat_id).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?};
    let Some((message_id,sequence))=target else{tx.commit().await.map_err(internal_error)?;return Ok(Vec::new())};
    let mut changed=false;
    if let Some(team)=team {
        let state_id=sqlx::query_scalar::<_,Uuid>("INSERT INTO support_supportconversationreadstate (id,created_at,updated_at,support_conversation_id,user_id,last_read_message_id,last_read_at,last_delivered_message_id,last_delivered_at) VALUES ($1,NOW(),NOW(),$2,$3,NULL,NULL,NULL,NULL) ON CONFLICT (support_conversation_id,user_id) DO UPDATE SET updated_at=support_supportconversationreadstate.updated_at RETURNING id")
            .bind(Uuid::new_v4()).bind(support_id).bind(team.user_id).persistent(false).fetch_one(&mut *tx).await.map_err(internal_error)?;
        let column=if read{"last_read_message_id"}else{"last_delivered_message_id"}; let at=if read{"last_read_at"}else{"last_delivered_at"};
        let sql=format!("UPDATE support_supportconversationreadstate rs SET {column}=$2,{at}=NOW(),last_delivered_message_id=CASE WHEN $3 AND (rs.last_delivered_message_id IS NULL OR (SELECT sequence FROM chat_message WHERE id=rs.last_delivered_message_id)<$4) THEN $2 ELSE last_delivered_message_id END,last_delivered_at=CASE WHEN $3 AND (rs.last_delivered_message_id IS NULL OR (SELECT sequence FROM chat_message WHERE id=rs.last_delivered_message_id)<$4) THEN NOW() ELSE last_delivered_at END,updated_at=NOW() WHERE id=$1 AND (rs.{column} IS NULL OR (SELECT sequence FROM chat_message WHERE id=rs.{column})<$4)");
        changed=sqlx::query(&sql).bind(state_id).bind(message_id).bind(read).bind(sequence.unwrap_or(0)).persistent(false).execute(&mut *tx).await.map_err(internal_error)?.rows_affected()>0;
    }else{
        let column=if read{"visitor_last_read_message_id"}else{"visitor_last_delivered_message_id"}; let at=if read{"visitor_last_read_at"}else{"visitor_last_delivered_at"};
        let sql=format!("UPDATE support_supportconversation sc SET {column}=$2,{at}=NOW(),visitor_last_delivered_message_id=CASE WHEN $3 AND (visitor_last_delivered_message_id IS NULL OR (SELECT sequence FROM chat_message WHERE id=visitor_last_delivered_message_id)<$4) THEN $2 ELSE visitor_last_delivered_message_id END,visitor_last_delivered_at=CASE WHEN $3 AND (visitor_last_delivered_message_id IS NULL OR (SELECT sequence FROM chat_message WHERE id=visitor_last_delivered_message_id)<$4) THEN NOW() ELSE visitor_last_delivered_at END,updated_at=NOW() WHERE id=$1 AND (sc.{column} IS NULL OR (SELECT sequence FROM chat_message WHERE id=sc.{column})<$4)");
        changed=sqlx::query(&sql).bind(support_id).bind(message_id).bind(read).bind(sequence.unwrap_or(0)).persistent(false).execute(&mut *tx).await.map_err(internal_error)?.rows_affected()>0;
    }
    let mut events=Vec::new(); if changed{let event_name=if read{"support.message.read"}else{"support.message.delivered"};let actor_kind=if team.is_some(){"team"}else{"visitor"};let data=json!({"conversation_id":support_id.to_string(),"website_id":website_id.to_string(),"visitor_id":visitor_id.to_string(),"message_id":message_id.to_string(),"actor_kind":actor_kind,"actor_id":team.map(|v|v.user_id.to_string()).unwrap_or_else(||visitor_id.to_string())});let audiences=vec![AudienceKey{kind:AudienceKind::SupportWebsite,identifier:website_id.to_string()},AudienceKey{kind:AudienceKind::SupportVisitor,identifier:visitor_id.to_string()}];events.push(emit_durable(&mut tx,event_name,data,audiences).await?)}
    tx.commit().await.map_err(internal_error)?;Ok(events)
}

pub async fn list_conversations(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<InboxQuery>,
) -> Response {
    let context = match authenticate_team(&state, &headers).await {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let pool = match pool(&state) {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let cursor = match decode_inbox_cursor(&params.cursor) {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let limit = params.limit.unwrap_or(50).clamp(1, 100);
    let offset = if cursor.is_some() { 0 } else { params.offset.unwrap_or(0).max(0) };
    let visibility = visibility_sql(&context, "sc", "w");
    let filter_sql = format!(r#"
      FROM support_supportconversation sc
      JOIN support_supportwebsite w ON w.id=sc.website_id
      JOIN support_supportvisitor v ON v.id=sc.visitor_id
      JOIN chat_conversation c ON c.id=sc.conversation_id
      WHERE {visibility}
        AND ($4::uuid IS NULL OR w.id=$4)
        AND ($5='' OR sc.status=$5)
        AND ($6='' OR sc.priority=$6)
        AND ($7='' OR sc.subject ILIKE '%'||$7||'%' OR v.name ILIKE '%'||$7||'%' OR v.email ILIKE '%'||$7||'%'
          OR EXISTS(SELECT 1 FROM chat_message sm WHERE sm.conversation_id=sc.conversation_id AND sm.is_deleted=FALSE AND sm.text ILIKE '%'||$7||'%'))
        AND ($8::uuid IS NULL OR EXISTS(
          SELECT 1 FROM support_supportconversationtag sct
          JOIN support_supporttag st ON st.id=sct.tag_id AND st.is_active=TRUE
          WHERE sct.support_conversation_id=sc.id AND st.id=$8))
        AND ($9='' OR $9<>'')
        AND ($10::uuid IS NULL OR $10::uuid IS NOT NULL)
        AND ($5<>'' OR CASE $11
          WHEN 'mine' THEN (NOT $12 OR (sc.assigned_agent_id=$3 AND sc.status<>'closed'))
          WHEN 'unassigned' THEN sc.assigned_agent_id IS NULL AND sc.status<>'closed'
          WHEN 'overdue' THEN sc.status NOT IN ('resolved','closed') AND (
            (sc.first_response_at IS NULL AND sc.first_response_due_at<=NOW())
            OR sc.next_response_due_at<=NOW() OR sc.resolution_due_at<=NOW())
          WHEN 'follow_up' THEN sc.status NOT IN ('resolved','closed') AND sc.follow_up_at<=NOW() AND sc.follow_up_completed_at IS NULL
          WHEN 'resolved' THEN sc.status='resolved'
          WHEN 'closed' THEN sc.status='closed'
          ELSE sc.status NOT IN ('resolved','closed')
        END)
    "#);
    let queue = if params.queue.trim().is_empty() { "open" } else { params.queue.trim() };
    let cursor_at = cursor.as_ref().map(|value| value.at.as_str()).unwrap_or("");
    let cursor_id = cursor.as_ref().map(|value| value.id).unwrap_or_else(Uuid::nil);
    let is_agent = context.role == "agent";

    let count_sql = format!("SELECT COUNT(*)::bigint {filter_sql}");
    let mut count_query = sqlx::query_scalar::<_, i64>(&count_sql)
        .bind(context.user_id)
        .bind(context.account_id);
    count_query = count_query.bind(context.agent_id);
    let total = match count_query
        .bind(params.website)
        .bind(&params.status)
        .bind(&params.priority)
        .bind(&params.search)
        .bind(params.tag)
        .bind(cursor_at)
        .bind(cursor_id)
        .bind(queue)
        .bind(is_agent)
        .persistent(false)
        .fetch_one(pool)
        .await
    {
        Ok(value) => value,
        Err(error) => return json_error(internal_error(error)),
    };

    let page_sql = format!(r#"
      SELECT sc.id,COALESCE(c.last_message_at,sc.created_at)::text AS cursor_at
      {filter_sql}
        AND ($9='' OR COALESCE(c.last_message_at,sc.created_at)<$9::timestamptz
          OR (COALESCE(c.last_message_at,sc.created_at)=$9::timestamptz AND sc.id<$10))
      ORDER BY COALESCE(c.last_message_at,sc.created_at) DESC,sc.id DESC
      LIMIT $13 OFFSET $14
    "#);
    let mut page_query = sqlx::query_as::<_, (Uuid, String)>(&page_sql)
        .bind(context.user_id)
        .bind(context.account_id)
        .bind(context.agent_id)
        .bind(params.website)
        .bind(&params.status)
        .bind(&params.priority)
        .bind(&params.search)
        .bind(params.tag)
        .bind(cursor_at)
        .bind(cursor_id)
        .bind(queue)
        .bind(is_agent)
        .bind(limit + 1)
        .bind(offset);
    let mut rows = match page_query.persistent(false).fetch_all(pool).await {
        Ok(value) => value,
        Err(error) => return json_error(internal_error(error)),
    };
    let has_more = rows.len() as i64 > limit;
    if has_more {
        rows.truncate(limit as usize);
    }
    let next_cursor = if has_more {
        rows.last().and_then(|(id, at)| {
            encode_inbox_cursor(&SupportInboxCursor { at: at.clone(), id: *id }).ok()
        })
    } else {
        None
    };

    let ordered_ids = rows.iter().map(|(id, _)| *id).collect::<Vec<_>>();
    let mut payloads = match conversation_list_json(pool, &ordered_ids, context.user_id).await {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let mut results = Vec::with_capacity(rows.len());
    for id in ordered_ids {
        if let Some(value) = payloads.remove(&id) { results.push(value); }
    }
    let summary = match unread_summary_values(pool, &context).await {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let next_offset = if cursor.is_none() && offset + limit < total {
        Some(offset + limit)
    } else {
        None
    };
    Json(json!({
        "results":results,
        "count":total,
        "next_offset":next_offset,
        "next_cursor":next_cursor,
        "unread_total":summary.0,
        "website_unread":summary.1
    }))
    .into_response()
}

async fn unread_summary_values(pool:&PgPool,context:&TeamContext)->SupportResult<(i64,Map<String,Value>)>{
    let visibility=visibility_sql(context,"sc","w");let sql=format!(r#"SELECT sc.website_id,COUNT(m.id)::bigint FROM support_supportconversation sc JOIN support_supportwebsite w ON w.id=sc.website_id JOIN chat_message m ON m.conversation_id=sc.conversation_id AND m.sender_id IS NULL AND m.is_deleted=FALSE LEFT JOIN support_supportconversationreadstate rs ON rs.support_conversation_id=sc.id AND rs.user_id=$1 LEFT JOIN chat_message rm ON rm.id=rs.last_read_message_id WHERE {visibility} AND (rm.sequence IS NULL OR m.sequence>rm.sequence) GROUP BY sc.website_id"#);let mut q=sqlx::query_as::<_,(Uuid,i64)>(&sql).bind(context.user_id).bind(context.account_id);if context.role=="agent"{q=q.bind(context.agent_id)};let rows=q.persistent(false).fetch_all(pool).await.map_err(internal_error)?;let mut total=0;let mut map=Map::new();for(id,count)in rows{total+=count;map.insert(id.to_string(),json!(count));}Ok((total,map))
}

pub async fn unread_summary(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    let context = match authenticate_team(&state, &headers).await {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let pool = match pool(&state) {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let (total, website_unread) = match unread_summary_values(pool, &context).await {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let alert_unread = match sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*)::bigint FROM support_supportservicealert WHERE support_account_id=$1 AND recipient_id=$2 AND status='unread'",
    )
    .bind(context.account_id)
    .bind(context.user_id)
    .persistent(false)
    .fetch_one(pool)
    .await
    {
        Ok(value) => value,
        Err(error) => return json_error(internal_error(error)),
    };
    Json(json!({
        "unread_total":total,
        "website_unread":website_unread,
        "alert_unread":alert_unread
    }))
    .into_response()
}

pub async fn get_conversation(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap)->Response{let context=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};let pool=match pool(&state){Ok(v)=>v,Err(e)=>return json_error(e)};if let Err(e)=team_conversation_chat_id(pool,&context,id).await{return json_error(e)};match conversation_json(pool,id,Some(context.user_id),false,None).await{Ok(v)=>Json(v).into_response(),Err(e)=>json_error(e)}}

pub async fn list_team_messages(
    State(state): State<Arc<AppState>>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
    Query(params): Query<MessageQuery>,
) -> Response {
    let context = match authenticate_team(&state, &headers).await {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    let pool = match pool(&state) {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    if let Err(error) = team_conversation_chat_id(pool, &context, id).await {
        return json_error(error);
    }
    let messages = match message_list_json(
        pool,
        id,
        Some(context.user_id),
        false,
        None,
        params.limit.unwrap_or(100),
        false,
    )
    .await
    {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    match update_receipt(
        &state,
        id,
        Some(&context),
        None,
        ReceiptInput::default(),
        true,
    )
    .await
    {
        Ok(read_events) => deliver_committed(&state, &read_events).await,
        Err(error) => tracing::warn!(
            conversation_id = %id,
            user_id = context.user_id,
            error = ?error,
            "Support message list succeeded but automatic read acknowledgement failed"
        ),
    }
    let conversation = match conversation_json(pool, id, Some(context.user_id), false, None).await {
        Ok(value) => value,
        Err(error) => return json_error(error),
    };
    Json(json!({"conversation":conversation,"messages":messages})).into_response()
}

pub async fn send_team_message(
    State(state):State<Arc<AppState>>,
    Path(id):Path<Uuid>,
    headers:HeaderMap,
    Json(input):Json<MessageInput>,
)->Response{
    let context=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};
    match create_message(&state,id,Some(&context),None,input).await{
        Ok((payload,mut events))=>{
            match update_receipt(&state,id,Some(&context),None,ReceiptInput::default(),true).await{
                Ok(mut read_events)=>events.append(&mut read_events),
                Err(error)=>tracing::warn!(
                    conversation_id = %id,
                    user_id = context.user_id,
                    error = ?error,
                    "Support message committed but automatic sender read acknowledgement failed"
                ),
            }
            deliver_committed(&state,&events).await;
            (StatusCode::CREATED,Json(payload)).into_response()
        },
        Err(e)=>json_error(e)
    }
}

pub async fn team_delivered(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap,Json(input):Json<ReceiptInput>)->Response{let context=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};match update_receipt(&state,id,Some(&context),None,input,false).await{Ok(events)=>{deliver_committed(&state,&events).await;StatusCode::NO_CONTENT.into_response()},Err(e)=>json_error(e)}}
pub async fn team_read(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap,Json(input):Json<ReceiptInput>)->Response{let context=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};match update_receipt(&state,id,Some(&context),None,input,true).await{Ok(events)=>{deliver_committed(&state,&events).await;StatusCode::NO_CONTENT.into_response()},Err(e)=>json_error(e)}}

pub async fn claim_conversation(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap)->Response{let context=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};let Some(agent_id)=context.agent_id else{return json_error(SupportError::new(StatusCode::BAD_REQUEST,"owner_cannot_claim","The Support Chat owner does not need to claim conversations."))};let pool=match pool(&state){Ok(v)=>v,Err(e)=>return json_error(e)};if let Err(e)=team_conversation_chat_id(pool,&context,id).await{return json_error(e)};let mut tx=match pool.begin().await{Ok(v)=>v,Err(e)=>return json_error(internal_error(e))};let changed=match sqlx::query("UPDATE support_supportconversation SET assigned_agent_id=$2,assigned_at=NOW(),assignment_trigger='manual_claim',status=CASE WHEN status='new' THEN 'open' ELSE status END,revision_number=revision_number+1,updated_at=NOW() WHERE id=$1 AND (assigned_agent_id IS NULL OR assigned_agent_id=$2)").bind(id).bind(agent_id).persistent(false).execute(&mut *tx).await{Ok(v)=>v.rows_affected()>0,Err(e)=>return json_error(internal_error(e))};if !changed{return json_error(SupportError::new(StatusCode::CONFLICT,"already_assigned","This conversation is already assigned to another agent."))}let info=sqlx::query_as::<_,(Uuid,Uuid)>("SELECT website_id,visitor_id FROM support_supportconversation WHERE id=$1").bind(id).persistent(false).fetch_one(&mut *tx).await.map_err(internal_error);let(website,visitor)=match info{Ok(v)=>v,Err(e)=>return json_error(e)};let event=match emit_durable(&mut tx,"support.conversation.updated",json!({"conversation_id":id.to_string(),"website_id":website.to_string(),"visitor_id":visitor.to_string(),"reason":"manual_claim","assigned_agent_id":agent_id.to_string()}),vec![AudienceKey{kind:AudienceKind::SupportWebsite,identifier:website.to_string()},AudienceKey{kind:AudienceKind::SupportVisitor,identifier:visitor.to_string()}]).await{Ok(v)=>v,Err(e)=>return json_error(e)};if let Err(e)=tx.commit().await{return json_error(internal_error(e))}deliver_committed(&state,&[event]).await;match conversation_json(pool,id,Some(context.user_id),false,None).await{Ok(v)=>Json(v).into_response(),Err(e)=>json_error(e)}}

pub async fn widget_options(headers:HeaderMap)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);widget_response(StatusCode::NO_CONTENT,Value::Null,origin.as_deref())}

pub async fn list_widget_messages(
    State(state): State<Arc<AppState>>,
    Path((site_key, session_id)): Path<(Uuid, Uuid)>,
    headers: HeaderMap,
    Query(params): Query<MessageQuery>,
) -> Response {
    let request_origin = headers
        .get(header::ORIGIN)
        .and_then(|value| value.to_str().ok())
        .and_then(normalize_origin);
    let context = match authenticate_widget(&state, &headers, site_key, session_id).await {
        Ok(value) => value,
        Err(error) => return widget_error(error, request_origin.as_deref()),
    };
    let pool = match pool(&state) {
        Ok(value) => value,
        Err(error) => return widget_error(error, Some(&context.origin)),
    };
    let support_id = match sqlx::query_scalar::<_, Uuid>(
        "SELECT id FROM support_supportconversation WHERE website_id=$1 AND visitor_id=$2",
    )
    .bind(context.website_id)
    .bind(context.visitor_id)
    .persistent(false)
    .fetch_optional(pool)
    .await
    {
        Ok(value) => value,
        Err(error) => return widget_error(internal_error(error), Some(&context.origin)),
    };
    let Some(id) = support_id else {
        return widget_response(
            StatusCode::OK,
            json!({"conversation":Value::Null,"messages":[]}),
            Some(&context.origin),
        );
    };
    let messages = match message_list_json(
        pool,
        id,
        None,
        true,
        Some((site_key, session_id)),
        params.limit.unwrap_or(100),
        false,
    )
    .await
    {
        Ok(value) => value,
        Err(error) => return widget_error(error, Some(&context.origin)),
    };
    match update_receipt(
        &state,
        id,
        None,
        Some(&context),
        ReceiptInput::default(),
        true,
    )
    .await
    {
        Ok(read_events) => deliver_committed(&state, &read_events).await,
        Err(error) => tracing::warn!(
            conversation_id = %id,
            visitor_id = %context.visitor_id,
            error = ?error,
            "Support widget message list succeeded but automatic read acknowledgement failed"
        ),
    }
    let conversation = match conversation_json(pool, id, None, true, Some((site_key, session_id))).await {
        Ok(value) => value,
        Err(error) => return widget_error(error, Some(&context.origin)),
    };
    widget_response(
        StatusCode::OK,
        json!({"conversation":conversation,"messages":messages}),
        Some(&context.origin),
    )
}

pub async fn send_widget_message(State(state):State<Arc<AppState>>,Path((site_key,session_id)):Path<(Uuid,Uuid)>,headers:HeaderMap,Json(input):Json<MessageInput>)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let context=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};let support_id=match ensure_widget_conversation_id(&state,&context).await{Ok(v)=>v,Err(e)=>return widget_error(e,Some(&context.origin))};match create_message(&state,support_id,None,Some(&context),input).await{Ok((message,events))=>{deliver_committed(&state,&events).await;let pool=pool(&state).unwrap();let conversation=conversation_json(pool,support_id,None,true,Some((site_key,session_id))).await.unwrap_or(Value::Null);widget_response(StatusCode::CREATED,json!({"conversation":conversation,"message":message}),Some(&context.origin))},Err(e)=>widget_error(e,Some(&context.origin))}}

async fn ensure_widget_conversation_id(state:&Arc<AppState>,context:&WidgetContext)->SupportResult<Uuid>{let pool=pool(state)?;if let Some(id)=sqlx::query_scalar::<_,Uuid>("SELECT id FROM support_supportconversation WHERE website_id=$1 AND visitor_id=$2").bind(context.website_id).bind(context.visitor_id).persistent(false).fetch_optional(pool).await.map_err(internal_error)?{return Ok(id)};let mut tx=pool.begin().await.map_err(internal_error)?;sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))").bind(format!("support-visitor:{}",context.visitor_id)).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;if let Some(id)=sqlx::query_scalar::<_,Uuid>("SELECT id FROM support_supportconversation WHERE website_id=$1 AND visitor_id=$2").bind(context.website_id).bind(context.visitor_id).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?{tx.commit().await.map_err(internal_error)?;return Ok(id)}let chat=Uuid::new_v4();let support=Uuid::new_v4();sqlx::query("INSERT INTO chat_conversation (id,created_at,updated_at,type,title,slug,avatar,created_by_id,is_active,direct_key,last_message_id,last_message_at,e2ee_key_version,e2ee_rekey_required,e2ee_last_key_rotation_at,e2ee_last_security_event_at,next_message_sequence) VALUES ($1,NOW(),NOW(),'direct',$2,NULL,'',NULL,TRUE,NULL,NULL,NULL,1,FALSE,NULL,NULL,0)").bind(chat).bind(format!("{} · {}",context.website_name,context.visitor_name).chars().take(255).collect::<String>()).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;sqlx::query("INSERT INTO support_supportconversation (id,created_at,updated_at,conversation_id,website_id,visitor_id,assigned_agent_id,assigned_team_id,assigned_at,assignment_trigger,status,priority,subject,first_response_at,last_visitor_message_at,last_agent_message_at,visitor_last_read_message_id,visitor_last_read_at,visitor_last_delivered_message_id,visitor_last_delivered_at,first_response_due_at,next_response_due_at,resolution_due_at,first_response_breached_at,next_response_breached_at,resolution_breached_at,sla_paused_at,sla_pause_reason,sla_total_paused_seconds,sla_last_recalculated_at,sla_escalated_at,follow_up_at,follow_up_note,follow_up_created_by_id,follow_up_completed_at,follow_up_assignee_id,previous_status,snoozed_until,snoozed_by_id,resolution_reason,closure_reason,reopened_at,reopen_count,revision_number,resolved_at,closed_at) VALUES ($1,NOW(),NOW(),$2,$3,$4,NULL,NULL,NULL,'','new','normal',$5,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'',0,NULL,NULL,NULL,'',NULL,NULL,NULL,'',NULL,NULL,'','',NULL,0,1,NULL,NULL)").bind(support).bind(chat).bind(context.website_id).bind(context.visitor_id).bind(format!("Conversation with {}",context.visitor_name).chars().take(255).collect::<String>()).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;route_conversation(&mut tx,support,"conversation_created").await?;enqueue_control_job(&mut tx,"conversation_created",&format!("conversation-created:{support}"),support,None,json!({"website_id":context.website_id.to_string(),"visitor_id":context.visitor_id.to_string()})).await?;let event=emit_durable(&mut tx,"support.conversation.created",json!({"conversation_id":support.to_string(),"website_id":context.website_id.to_string(),"visitor_id":context.visitor_id.to_string()}),vec![AudienceKey{kind:AudienceKind::SupportWebsite,identifier:context.website_id.to_string()},AudienceKey{kind:AudienceKind::SupportVisitor,identifier:context.visitor_id.to_string()}]).await?;tx.commit().await.map_err(internal_error)?;deliver_committed(state,&[event]).await;Ok(support)}

pub async fn widget_delivered(State(state):State<Arc<AppState>>,Path((site_key,session_id)):Path<(Uuid,Uuid)>,headers:HeaderMap,Json(input):Json<ReceiptInput>)->Response{widget_receipt_handler(state,site_key,session_id,headers,input,false).await}
pub async fn widget_read(State(state):State<Arc<AppState>>,Path((site_key,session_id)):Path<(Uuid,Uuid)>,headers:HeaderMap,Json(input):Json<ReceiptInput>)->Response{widget_receipt_handler(state,site_key,session_id,headers,input,true).await}
async fn widget_receipt_handler(state:Arc<AppState>,site_key:Uuid,session_id:Uuid,headers:HeaderMap,input:ReceiptInput,read:bool)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let context=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};let pool=match pool(&state){Ok(v)=>v,Err(e)=>return widget_error(e,Some(&context.origin))};let id=match sqlx::query_scalar::<_,Uuid>("SELECT id FROM support_supportconversation WHERE website_id=$1 AND visitor_id=$2").bind(context.website_id).bind(context.visitor_id).persistent(false).fetch_optional(pool).await{Ok(Some(v))=>v,Ok(None)=>return widget_response(StatusCode::NO_CONTENT,Value::Null,Some(&context.origin)),Err(e)=>return widget_error(internal_error(e),Some(&context.origin))};match update_receipt(&state,id,None,Some(&context),input,read).await{Ok(events)=>{deliver_committed(&state,&events).await;widget_response(StatusCode::NO_CONTENT,Value::Null,Some(&context.origin))},Err(e)=>widget_error(e,Some(&context.origin))}}

// Support call lifecycle. Media remains browser WebRTC; these endpoints only coordinate durable state and ephemeral signaling.
async fn call_json(pool:&PgPool,call_id:Uuid)->SupportResult<Value>{let sql=r#"SELECT jsonb_build_object('id',c.id::text,'conversation_id',sc.id::text,'website_id',w.id::text,'website_name',w.name,'visitor_id',v.id::text,'visitor_name',COALESCE(NULLIF(v.name,''),'Website visitor'),'initiated_by',jsonb_build_object('id',u.id::text,'username',u.username,'display_name',COALESCE(NULLIF(p.display_name,''),u.username),'avatar',CASE WHEN p.avatar IS NULL OR p.avatar='' THEN NULL ELSE '/media/'||p.avatar END),'initiator_kind',c.initiator_kind,'call_type',c.call_type,'status',c.status,'started_at',c.started_at,'answered_at',c.answered_at,'ended_at',c.ended_at,'ended_reason',c.ended_reason,'participants',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',cp.id::text,'kind',cp.kind,'state',cp.state,'audio_enabled',cp.audio_enabled,'video_enabled',cp.video_enabled,'joined_at',cp.joined_at,'left_at',cp.left_at,'last_seen_at',cp.last_seen_at) ORDER BY cp.kind) FROM support_supportcallparticipant cp WHERE cp.call_id=c.id),'[]'::jsonb)) FROM support_supportcallsession c JOIN support_supportconversation sc ON sc.id=c.support_conversation_id JOIN support_supportwebsite w ON w.id=sc.website_id JOIN support_supportvisitor v ON v.id=sc.visitor_id JOIN accounts_user u ON u.id=c.initiated_by_id LEFT JOIN accounts_profile p ON p.user_id=u.id WHERE c.id=$1"#;sqlx::query_scalar::<_,Value>(sql).bind(call_id).persistent(false).fetch_optional(pool).await.map_err(internal_error)?.ok_or_else(||SupportError::new(StatusCode::NOT_FOUND,"call_not_found","Support call was not found."))}

async fn team_call_access(
    pool: &PgPool,
    context: &TeamContext,
    call_id: Uuid,
) -> SupportResult<(Uuid, Uuid, Uuid, String, String, i64)> {
    let visibility = if context.role == "owner" {
        "w.support_account_id=$3".to_owned()
    } else {
        let assignment = if context.can_view_all {
            "TRUE".to_owned()
        } else {
            "(sc.assigned_agent_id=$4 OR sc.assigned_agent_id IS NULL)".to_owned()
        };
        format!(
            "w.support_account_id=$3 AND EXISTS(SELECT 1 FROM support_supportwebsiteagent wa WHERE wa.website_id=w.id AND wa.agent_id=$4) AND {assignment}"
        )
    };
    let sql = format!(
        "SELECT c.support_conversation_id,sc.website_id,sc.visitor_id,c.status,c.initiator_kind,c.initiated_by_id          FROM support_supportcallsession c          JOIN support_supportconversation sc ON sc.id=c.support_conversation_id          JOIN support_supportwebsite w ON w.id=sc.website_id          WHERE c.id=$1 AND c.initiated_by_id=$2 AND {visibility}"
    );
    let mut query = sqlx::query_as::<_, (Uuid, Uuid, Uuid, String, String, i64)>(&sql)
        .bind(call_id)
        .bind(context.user_id)
        .bind(context.account_id);
    if context.role == "agent" {
        query = query.bind(context.agent_id);
    }
    query
        .persistent(false)
        .fetch_optional(pool)
        .await
        .map_err(internal_error)?
        .ok_or_else(|| SupportError::new(StatusCode::NOT_FOUND, "call_not_found", "Support call was not found."))
}
async fn widget_call_access(pool:&PgPool,context:&WidgetContext,call_id:Uuid)->SupportResult<(Uuid,Uuid,Uuid,String,String,i64)>{sqlx::query_as::<_,(Uuid,Uuid,Uuid,String,String,i64)>("SELECT c.support_conversation_id,sc.website_id,sc.visitor_id,c.status,c.initiator_kind,c.initiated_by_id FROM support_supportcallsession c JOIN support_supportconversation sc ON sc.id=c.support_conversation_id WHERE c.id=$1 AND sc.website_id=$2 AND sc.visitor_id=$3").bind(call_id).bind(context.website_id).bind(context.visitor_id).persistent(false).fetch_optional(pool).await.map_err(internal_error)?.ok_or_else(||SupportError::new(StatusCode::NOT_FOUND,"call_not_found","Support call was not found."))}

async fn start_call(
    state: &Arc<AppState>,
    support_id: Uuid,
    team: Option<&TeamContext>,
    widget: Option<&WidgetContext>,
    call_type: &str,
) -> SupportResult<(Value, Vec<CommittedEvent>)> {
    if call_type != "voice" && call_type != "video" {
        return Err(SupportError::new(StatusCode::BAD_REQUEST, "invalid_call_type", "Choose an audio or video call."));
    }
    if !state.config.support_calls_enabled {
        return Err(SupportError::new(StatusCode::FORBIDDEN, "calls_disabled", "Support calls are not enabled on this deployment."));
    }

    let pool = pool(state)?;
    let mut tx = pool.begin().await.map_err(internal_error)?;
    let (website, visitor, handler, initiator_kind) = if let Some(team) = team {
        let visibility=visibility_sql(team,"sc","w");
        let sql=format!("SELECT sc.website_id,sc.visitor_id,sc.status FROM support_supportconversation sc JOIN support_supportwebsite w ON w.id=sc.website_id WHERE sc.id=$1 AND {visibility} FOR UPDATE OF sc");
        let mut query=sqlx::query_as::<_,(Uuid,Uuid,String)>(&sql).bind(support_id).bind(team.account_id);
        if team.role=="agent" { query=query.bind(team.agent_id); }
        let (website,visitor,status)=query.persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?
            .ok_or_else(||SupportError::new(StatusCode::NOT_FOUND,"conversation_not_found","Support conversation was not found."))?;
        if status == "closed" {
            return Err(SupportError::new(StatusCode::CONFLICT, "conversation_closed", "This support conversation is closed."));
        }
        (website, visitor, team.user_id, "team")
    } else {
        let widget = widget.ok_or_else(|| SupportError::new(StatusCode::UNAUTHORIZED, "widget_session_required", "A valid widget session is required."))?;
        let row = sqlx::query_as::<_, (Uuid, Uuid, Option<i64>, i64, String)>(
            "SELECT sc.website_id,sc.visitor_id,ag.user_id,sa.owner_id,sc.status \
             FROM support_supportconversation sc \
             JOIN support_supportwebsite w ON w.id=sc.website_id \
             JOIN support_supportaccount sa ON sa.id=w.support_account_id \
             LEFT JOIN support_supportagent ag ON ag.id=sc.assigned_agent_id AND ag.is_active=TRUE \
             JOIN accounts_user owner ON owner.id=sa.owner_id AND owner.is_active=TRUE \
             WHERE sc.id=$1 AND sc.website_id=$2 AND sc.visitor_id=$3 FOR UPDATE OF sc",
        )
        .bind(support_id).bind(widget.website_id).bind(widget.visitor_id)
        .persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?
        .ok_or_else(|| SupportError::new(StatusCode::NOT_FOUND, "conversation_not_found", "Support conversation was not found."))?;
        if row.4 == "closed" {
            return Err(SupportError::new(StatusCode::CONFLICT, "conversation_closed", "This support conversation is closed."));
        }
        (row.0, row.1, row.2.unwrap_or(row.3), "visitor")
    };

    let feature = sqlx::query_as::<_, (bool, bool, bool, bool)>(
        "SELECT COALESCE(cs.enabled,TRUE),COALESCE(cs.allow_video,TRUE),\
                COALESCE(ws.allow_audio_calls,TRUE),COALESCE(ws.allow_video_calls,TRUE) \
         FROM support_supportwebsite w \
         LEFT JOIN support_supportcallsettings cs ON cs.support_account_id=w.support_account_id \
         LEFT JOIN support_supportwidgetsettings ws ON ws.website_id=w.id \
         WHERE w.id=$1",
    )
    .bind(website).persistent(false).fetch_one(&mut *tx).await.map_err(internal_error)?;
    if !feature.0 {
        return Err(SupportError::new(StatusCode::FORBIDDEN, "calls_disabled", "Support calls are disabled for this account."));
    }
    if call_type == "voice" && !feature.2 {
        return Err(SupportError::new(StatusCode::FORBIDDEN, "voice_disabled", "Audio calls are disabled for this website."));
    }
    if call_type == "video" && (!feature.1 || !feature.3) {
        return Err(SupportError::new(StatusCode::FORBIDDEN, "video_disabled", "Video calls are disabled for this website."));
    }

    let timeout_seconds = state.config.support_call_ring_timeout.as_secs() as i64;
    sqlx::query(
        "WITH expired AS (\
           UPDATE support_supportcallsession SET status='missed',ended_at=NOW(),ended_reason='missed',updated_at=NOW() \
           WHERE status='ringing' AND started_at < NOW()-($3 * INTERVAL '1 second') \
             AND (support_conversation_id=$1 OR initiated_by_id=$2) RETURNING id\
         ) UPDATE support_supportcallparticipant SET state='missed',left_at=NOW(),updated_at=NOW() \
           WHERE call_id IN (SELECT id FROM expired) AND state='ringing'",
    )
    .bind(support_id).bind(handler).bind(timeout_seconds)
    .persistent(false).execute(&mut *tx).await.map_err(internal_error)?;

    let active = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM support_supportcallsession WHERE support_conversation_id=$1 AND status IN ('ringing','ongoing')) \
             OR EXISTS(SELECT 1 FROM support_supportcallsession WHERE initiated_by_id=$2 AND status IN ('ringing','ongoing'))",
    )
    .bind(support_id).bind(handler).persistent(false).fetch_one(&mut *tx).await.map_err(internal_error)?;
    if active {
        let (code, detail) = if initiator_kind == "visitor" {
            ("team_busy", "The support team is currently on another call. Please try again shortly.")
        } else {
            ("active_call_exists", "This conversation or team member already has an active call.")
        };
        return Err(SupportError::new(StatusCode::CONFLICT, code, detail));
    }

    let call_id = Uuid::new_v4();
    let room = Uuid::new_v4().simple().to_string();
    sqlx::query(
        "INSERT INTO support_supportcallsession \
         (id,created_at,updated_at,support_conversation_id,initiated_by_id,initiator_kind,call_type,status,room_key,started_at,answered_at,ended_at,ended_reason,last_signal_at,metadata) \
         VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,'ringing',$6,NOW(),NULL,NULL,'',NULL,$7)",
    )
    .bind(call_id).bind(support_id).bind(handler).bind(initiator_kind).bind(call_type).bind(room)
    .bind(json!({"product_scope":"support","website_id":website.to_string(),"initiator_kind":initiator_kind}))
    .persistent(false).execute(&mut *tx).await.map_err(internal_error)?;

    let video = call_type == "video";
    let team_state = if initiator_kind == "team" { "joined" } else { "ringing" };
    let visitor_state = if initiator_kind == "visitor" { "joined" } else { "ringing" };
    sqlx::query(
        "INSERT INTO support_supportcallparticipant \
         (id,created_at,updated_at,call_id,kind,user_id,visitor_id,state,audio_enabled,video_enabled,joined_at,left_at,last_seen_at) \
         VALUES ($1,NOW(),NOW(),$2,'team',$3,NULL,$4,TRUE,$5,CASE WHEN $4='joined' THEN NOW() ELSE NULL END,NULL,NOW()),\
                ($6,NOW(),NOW(),$2,'visitor',NULL,$7,$8,TRUE,$5,CASE WHEN $8='joined' THEN NOW() ELSE NULL END,NULL,NOW())",
    )
    .bind(Uuid::new_v4()).bind(call_id).bind(handler).bind(team_state).bind(video)
    .bind(Uuid::new_v4()).bind(visitor).bind(visitor_state)
    .persistent(false).execute(&mut *tx).await.map_err(internal_error)?;

    insert_support_audit(
        &mut tx,
        support_id,
        if initiator_kind == "team" { Some(handler) } else { None },
        if initiator_kind == "team" { "call.started" } else { "call.requested_by_visitor" },
        "support_call",
        Some(call_id),
        if initiator_kind == "team" { "A support team member started a support call." } else { "A website visitor requested a support call." },
        json!({"call_type":call_type,"initiator_kind":initiator_kind,"source":"axum_data_plane"}),
    ).await?;

    let event = emit_durable(
        &mut tx,
        "support.call.ringing",
        json!({"call_id":call_id.to_string(),"conversation_id":support_id.to_string(),"website_id":website.to_string(),"visitor_id":visitor.to_string(),"initiator_kind":initiator_kind,"call_type":call_type}),
        vec![
            AudienceKey{kind:AudienceKind::SupportWebsite,identifier:website.to_string()},
            AudienceKey{kind:AudienceKind::SupportVisitor,identifier:visitor.to_string()},
            AudienceKey{kind:AudienceKind::SupportUser,identifier:handler.to_string()},
        ],
    ).await?;
    tx.commit().await.map_err(internal_error)?;
    Ok((call_json(pool, call_id).await?, vec![event]))
}


async fn transition_call(
    state: &Arc<AppState>,
    call_id: Uuid,
    actor_kind: &str,
    team: Option<&TeamContext>,
    widget: Option<&WidgetContext>,
    action: &str,
    reason: &str,
) -> SupportResult<(Value, Vec<CommittedEvent>)> {
    let pool = pool(state)?;
    let access = if let Some(team) = team {
        team_call_access(pool, team, call_id).await?
    } else {
        widget_call_access(pool, widget.ok_or_else(|| SupportError::new(StatusCode::UNAUTHORIZED, "widget_session_required", "A valid widget session is required."))?, call_id).await?
    };
    let (support_id, website, visitor, _, _, handler) = access;
    let mut tx = pool.begin().await.map_err(internal_error)?;
    let (status, initiator_kind) = sqlx::query_as::<_, (String, String)>(
        "SELECT status,initiator_kind FROM support_supportcallsession WHERE id=$1 FOR UPDATE",
    )
    .bind(call_id).persistent(false).fetch_optional(&mut *tx).await.map_err(internal_error)?
    .ok_or_else(|| SupportError::new(StatusCode::NOT_FOUND, "call_not_found", "Support call was not found."))?;

    if !matches!(status.as_str(), "ringing" | "ongoing") {
        if action == "end" || action == "decline" {
            tx.commit().await.map_err(internal_error)?;
            return Ok((call_json(pool, call_id).await?, Vec::new()));
        }
        return Err(SupportError::new(StatusCode::CONFLICT, "call_closed", "This call is no longer active."));
    }
    if action == "accept" && ((initiator_kind == "team" && actor_kind != "visitor") || (initiator_kind == "visitor" && actor_kind != "team")) {
        return Err(SupportError::new(StatusCode::CONFLICT, "invalid_call_direction", "The other call participant must accept this call."));
    }

    let final_status = match action { "accept" => "ongoing", "decline" => "declined", _ => "ended" };
    let clean_reason: String = reason.chars().take(64).collect();
    sqlx::query(
        "UPDATE support_supportcallsession SET status=$2,\
         answered_at=CASE WHEN $2='ongoing' THEN COALESCE(answered_at,NOW()) ELSE answered_at END,\
         ended_at=CASE WHEN $2 IN ('declined','ended') THEN NOW() ELSE ended_at END,\
         ended_reason=CASE WHEN $2 IN ('declined','ended') THEN $3 ELSE ended_reason END,updated_at=NOW() WHERE id=$1",
    )
    .bind(call_id).bind(final_status).bind(&clean_reason)
    .persistent(false).execute(&mut *tx).await.map_err(internal_error)?;

    match action {
        "accept" => {
            sqlx::query("UPDATE support_supportcallparticipant SET state='joined',joined_at=COALESCE(joined_at,NOW()),left_at=NULL,last_seen_at=NOW(),updated_at=NOW() WHERE call_id=$1 AND kind=$2")
                .bind(call_id).bind(actor_kind).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
        }
        "decline" => {
            sqlx::query("UPDATE support_supportcallparticipant SET state=CASE WHEN kind=$2 THEN 'declined' ELSE 'left' END,left_at=NOW(),last_seen_at=CASE WHEN kind=$2 THEN NOW() ELSE last_seen_at END,updated_at=NOW() WHERE call_id=$1")
                .bind(call_id).bind(actor_kind).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
        }
        _ => {
            sqlx::query("UPDATE support_supportcallparticipant SET state=CASE WHEN state IN ('declined','missed') THEN state ELSE 'left' END,left_at=COALESCE(left_at,NOW()),updated_at=NOW() WHERE call_id=$1")
                .bind(call_id).persistent(false).execute(&mut *tx).await.map_err(internal_error)?;
        }
    }

    if actor_kind == "team" && action != "accept" {
        insert_support_audit(
            &mut tx,
            support_id,
            Some(handler),
            "call.ended",
            "support_call",
            Some(call_id),
            "A support team member ended a support call.",
            json!({"reason":clean_reason,"status":final_status,"source":"axum_data_plane"}),
        ).await?;
    }
    let event_name = if action == "accept" { "support.call.accepted" } else { "support.call.ended" };
    let event = emit_durable(
        &mut tx,
        event_name,
        json!({"call_id":call_id.to_string(),"conversation_id":support_id.to_string(),"website_id":website.to_string(),"visitor_id":visitor.to_string(),"status":final_status,"reason":clean_reason}),
        vec![
            AudienceKey{kind:AudienceKind::SupportWebsite,identifier:website.to_string()},
            AudienceKey{kind:AudienceKind::SupportVisitor,identifier:visitor.to_string()},
            AudienceKey{kind:AudienceKind::SupportUser,identifier:handler.to_string()},
        ],
    ).await?;
    tx.commit().await.map_err(internal_error)?;
    Ok((call_json(pool, call_id).await?, vec![event]))
}


pub async fn team_active_call(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{let c=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};let pool=pool(&state).unwrap();let id=sqlx::query_scalar::<_,Uuid>("SELECT id FROM support_supportcallsession WHERE initiated_by_id=$1 AND status IN ('ringing','ongoing') ORDER BY started_at DESC LIMIT 1").bind(c.user_id).persistent(false).fetch_optional(pool).await;match id{Ok(Some(id))=>match call_json(pool,id).await{Ok(v)=>Json(json!({"call":v})).into_response(),Err(e)=>json_error(e)},Ok(None)=>Json(json!({"call":Value::Null})).into_response(),Err(e)=>json_error(internal_error(e))}}
pub async fn team_conversation_call(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap)->Response{let c=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};let pool=pool(&state).unwrap();if let Err(e)=team_conversation_chat_id(pool,&c,id).await{return json_error(e)};let call=sqlx::query_scalar::<_,Uuid>("SELECT id FROM support_supportcallsession WHERE support_conversation_id=$1 AND initiated_by_id=$2 AND status IN ('ringing','ongoing') ORDER BY started_at DESC LIMIT 1").bind(id).bind(c.user_id).persistent(false).fetch_optional(pool).await;match call{Ok(Some(v))=>match call_json(pool,v).await{Ok(v)=>Json(json!({"call":v})).into_response(),Err(e)=>json_error(e)},Ok(None)=>Json(json!({"call":Value::Null})).into_response(),Err(e)=>json_error(internal_error(e))}}
pub async fn start_team_call(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap,Json(input):Json<CallStartInput>)->Response{let c=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};match start_call(&state,id,Some(&c),None,&input.call_type).await{Ok((v,e))=>{deliver_committed(&state,&e).await;(StatusCode::CREATED,Json(v)).into_response()},Err(e)=>json_error(e)}}
pub async fn get_team_call(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap)->Response{let c=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};let pool=pool(&state).unwrap();if let Err(e)=team_call_access(pool,&c,id).await{return json_error(e)};match call_json(pool,id).await{Ok(v)=>Json(v).into_response(),Err(e)=>json_error(e)}}
pub async fn accept_team_call(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap)->Response{team_transition(state,id,headers,"accept","").await}
pub async fn decline_team_call(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap,Json(input):Json<EndCallInput>)->Response{team_transition(state,id,headers,"decline",if input.reason.is_empty(){"declined"}else{&input.reason}).await}
pub async fn end_team_call(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap,Json(input):Json<EndCallInput>)->Response{team_transition(state,id,headers,"end",if input.reason.is_empty(){"ended"}else{&input.reason}).await}
async fn team_transition(state:Arc<AppState>,id:Uuid,headers:HeaderMap,action:&str,reason:&str)->Response{let c=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};match transition_call(&state,id,"team",Some(&c),None,action,reason).await{Ok((v,e))=>{deliver_committed(&state,&e).await;Json(v).into_response()},Err(e)=>json_error(e)}}

pub async fn widget_active_call(State(state):State<Arc<AppState>>,Path((site_key,session_id)):Path<(Uuid,Uuid)>,headers:HeaderMap)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let c=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};let pool=pool(&state).unwrap();let id=sqlx::query_scalar::<_,Uuid>("SELECT cs.id FROM support_supportcallsession cs JOIN support_supportconversation sc ON sc.id=cs.support_conversation_id WHERE sc.website_id=$1 AND sc.visitor_id=$2 AND cs.status IN ('ringing','ongoing') ORDER BY cs.started_at DESC LIMIT 1").bind(c.website_id).bind(c.visitor_id).persistent(false).fetch_optional(pool).await;match id{Ok(Some(id))=>match call_json(pool,id).await{Ok(v)=>widget_response(StatusCode::OK,json!({"call":v}),Some(&c.origin)),Err(e)=>widget_error(e,Some(&c.origin))},Ok(None)=>widget_response(StatusCode::OK,json!({"call":Value::Null}),Some(&c.origin)),Err(e)=>widget_error(internal_error(e),Some(&c.origin))}}
pub async fn start_widget_call(State(state):State<Arc<AppState>>,Path((site_key,session_id)):Path<(Uuid,Uuid)>,headers:HeaderMap,Json(input):Json<CallStartInput>)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let c=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};let id=match ensure_widget_conversation_id(&state,&c).await{Ok(v)=>v,Err(e)=>return widget_error(e,Some(&c.origin))};match start_call(&state,id,None,Some(&c),&input.call_type).await{Ok((v,e))=>{deliver_committed(&state,&e).await;widget_response(StatusCode::CREATED,v,Some(&c.origin))},Err(e)=>widget_error(e,Some(&c.origin))}}
pub async fn get_widget_call(State(state):State<Arc<AppState>>,Path((site_key,session_id,id)):Path<(Uuid,Uuid,Uuid)>,headers:HeaderMap)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let c=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};let pool=pool(&state).unwrap();if let Err(e)=widget_call_access(pool,&c,id).await{return widget_error(e,Some(&c.origin))};match call_json(pool,id).await{Ok(v)=>widget_response(StatusCode::OK,v,Some(&c.origin)),Err(e)=>widget_error(e,Some(&c.origin))}}
pub async fn accept_widget_call(State(state):State<Arc<AppState>>,Path((site_key,session_id,id)):Path<(Uuid,Uuid,Uuid)>,headers:HeaderMap)->Response{widget_transition(state,site_key,session_id,id,headers,"accept","").await}
pub async fn decline_widget_call(State(state):State<Arc<AppState>>,Path((site_key,session_id,id)):Path<(Uuid,Uuid,Uuid)>,headers:HeaderMap,Json(input):Json<EndCallInput>)->Response{widget_transition(state,site_key,session_id,id,headers,"decline",if input.reason.is_empty(){"declined"}else{&input.reason}).await}
pub async fn end_widget_call(State(state):State<Arc<AppState>>,Path((site_key,session_id,id)):Path<(Uuid,Uuid,Uuid)>,headers:HeaderMap,Json(input):Json<EndCallInput>)->Response{widget_transition(state,site_key,session_id,id,headers,"end",if input.reason.is_empty(){"ended"}else{&input.reason}).await}
async fn widget_transition(state:Arc<AppState>,site_key:Uuid,session_id:Uuid,id:Uuid,headers:HeaderMap,action:&str,reason:&str)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let c=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};match transition_call(&state,id,"visitor",None,Some(&c),action,reason).await{Ok((v,e))=>{deliver_committed(&state,&e).await;widget_response(StatusCode::OK,v,Some(&c.origin))},Err(e)=>widget_error(e,Some(&c.origin))}}

async fn send_signal(
    state: &Arc<AppState>,
    call_id: Uuid,
    actor_kind: &str,
    actor_id: String,
    target_actor_id: String,
    website: Uuid,
    visitor: Uuid,
    input: SignalInput,
) -> SupportResult<Value> {
    if !matches!(input.signal_type.as_str(), "offer" | "answer" | "ice_candidate" | "renegotiate" | "ice_restart" | "hangup" | "media_toggle" | "network_state") {
        return Err(SupportError::new(StatusCode::BAD_REQUEST, "invalid_signal", "The signaling type is invalid."));
    }
    let encoded = serde_json::to_vec(&input.payload).map_err(|_| SupportError::new(StatusCode::BAD_REQUEST, "invalid_signal", "The call signal payload is invalid."))?;
    if encoded.len() > state.config.support_call_signal_max_bytes {
        return Err(SupportError::new(StatusCode::PAYLOAD_TOO_LARGE, "signal_too_large", "The call signal is too large."));
    }
    if !state.support_signals.allow_rate(&actor_id, state.config.support_signal_rate_per_second, std::time::Duration::from_secs(1)) {
        return Err(SupportError::new(StatusCode::TOO_MANY_REQUESTS, "signal_rate_limited", "Too many call signals were sent. Try again shortly."));
    }
    let signal_id = if input.signal_id.trim().is_empty() { Uuid::new_v4().to_string() } else { input.signal_id.trim().chars().take(64).collect() };
    let payload = json!({
        "id":Uuid::new_v4().to_string(),"call_id":call_id.to_string(),"signal_id":signal_id,
        "signal_type":input.signal_type,"payload":input.payload,"sender_kind":actor_kind,
        "sender_actor_id":actor_id,"created_at":OffsetDateTime::now_utc().format(&time::format_description::well_known::Rfc3339).unwrap_or_default()
    });
    let dedupe = format!("{}:{}", actor_id, payload.get("signal_id").and_then(Value::as_str).unwrap_or(""));
    let inserted = state.support_signals.push(
        call_id,&target_actor_id,&dedupe,payload.clone(),state.config.support_signal_ttl,state.config.support_signal_queue_capacity,
    );
    if !inserted { return Ok(payload); }

    if let Ok(pool) = pool(state) {
        let _ = sqlx::query("UPDATE support_supportcallsession SET last_signal_at=NOW(),updated_at=NOW() WHERE id=$1 AND (last_signal_at IS NULL OR last_signal_at<NOW()-INTERVAL '10 seconds')")
            .bind(call_id).persistent(false).execute(pool).await;
    }
    let frame = event_message("support.call.signal", payload.clone())
        .map_err(|_| SupportError::new(StatusCode::INTERNAL_SERVER_ERROR, "signal_encode_failed", "The call signal could not be encoded."))?;
    let audiences = vec![
        AudienceKey{kind:AudienceKind::SupportWebsite,identifier:website.to_string()},
        AudienceKey{kind:AudienceKind::SupportVisitor,identifier:visitor.to_string()},
    ];
    state.registry.fanout_high_filtered(&audiences,frame.clone(),None,Some(&target_actor_id));
    publish_shared_after_local(state,audiences,frame,EphemeralPriority::High,Some(target_actor_id)).await;
    Ok(payload)
}


pub async fn list_team_signals(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap)->Response{let c=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};let pool=pool(&state).unwrap();if let Err(e)=team_call_access(pool,&c,id).await{return json_error(e)};Json(json!({"signals":state.support_signals.pop_all(id,&format!("team:{}",c.user_id))})).into_response()}
pub async fn send_team_signal(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap,Json(input):Json<SignalInput>)->Response{let c=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};let pool=pool(&state).unwrap();let(_,website,visitor,status,_,_)=match team_call_access(pool,&c,id).await{Ok(v)=>v,Err(e)=>return json_error(e)};if !matches!(status.as_str(),"ringing"|"ongoing"){return json_error(SupportError::new(StatusCode::CONFLICT,"call_closed","This call is no longer active."))}match send_signal(&state,id,"team",format!("team:{}",c.user_id),format!("visitor:{visitor}"),website,visitor,input).await{Ok(v)=>(StatusCode::CREATED,Json(v)).into_response(),Err(e)=>json_error(e)}}
pub async fn list_widget_signals(State(state):State<Arc<AppState>>,Path((site_key,session_id,id)):Path<(Uuid,Uuid,Uuid)>,headers:HeaderMap)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let c=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};let pool=pool(&state).unwrap();if let Err(e)=widget_call_access(pool,&c,id).await{return widget_error(e,Some(&c.origin))};widget_response(StatusCode::OK,json!({"signals":state.support_signals.pop_all(id,&format!("visitor:{}",c.visitor_id))}),Some(&c.origin))}
pub async fn send_widget_signal(State(state):State<Arc<AppState>>,Path((site_key,session_id,id)):Path<(Uuid,Uuid,Uuid)>,headers:HeaderMap,Json(input):Json<SignalInput>)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let c=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};let pool=pool(&state).unwrap();let(_,website,visitor,status,_,handler)=match widget_call_access(pool,&c,id).await{Ok(v)=>v,Err(e)=>return widget_error(e,Some(&c.origin))};if !matches!(status.as_str(),"ringing"|"ongoing"){return widget_error(SupportError::new(StatusCode::CONFLICT,"call_closed","This call is no longer active."),Some(&c.origin))}match send_signal(&state,id,"visitor",format!("visitor:{}",c.visitor_id),format!("team:{handler}"),website,visitor,input).await{Ok(v)=>widget_response(StatusCode::CREATED,v,Some(&c.origin)),Err(e)=>widget_error(e,Some(&c.origin))}}

async fn media_state(
    state: &Arc<AppState>,
    call_id: Uuid,
    kind: &str,
    input: MediaStateInput,
) -> SupportResult<Value> {
    let pool = pool(state)?;
    let row = sqlx::query_as::<_, (Uuid, Uuid)>(
        "UPDATE support_supportcallparticipant cp SET \
         audio_enabled=COALESCE($3,cp.audio_enabled),\
         video_enabled=CASE WHEN c.call_type='video' THEN COALESCE($4,cp.video_enabled) ELSE FALSE END,\
         last_seen_at=NOW(),updated_at=NOW() \
         FROM support_supportcallsession c \
         JOIN support_supportconversation sc ON sc.id=c.support_conversation_id \
         WHERE cp.call_id=$1 AND cp.kind=$2 AND c.id=cp.call_id AND c.status IN ('ringing','ongoing') \
         RETURNING sc.website_id,sc.visitor_id",
    )
    .bind(call_id).bind(kind).bind(input.audio_enabled).bind(input.video_enabled)
    .persistent(false).fetch_optional(pool).await.map_err(internal_error)?
    .ok_or_else(|| SupportError::new(StatusCode::CONFLICT, "call_closed", "This call is no longer active."))?;
    let data = json!({"call_id":call_id.to_string(),"participant_kind":kind,"audio_enabled":input.audio_enabled,"video_enabled":input.video_enabled});
    let frame = event_message("support.call.media_updated", data).map_err(|_| SupportError::new(StatusCode::INTERNAL_SERVER_ERROR,"media_event_failed","The media state could not be delivered."))?;
    let audiences = vec![
        AudienceKey{kind:AudienceKind::SupportWebsite,identifier:row.0.to_string()},
        AudienceKey{kind:AudienceKind::SupportVisitor,identifier:row.1.to_string()},
    ];
    state.registry.fanout_low(&audiences,frame.clone(),None,None);
    publish_shared_after_local(state,audiences,frame,EphemeralPriority::Low,None).await;
    call_json(pool,call_id).await
}

pub async fn team_media_state(State(state):State<Arc<AppState>>,Path(id):Path<Uuid>,headers:HeaderMap,Json(input):Json<MediaStateInput>)->Response{let c=match authenticate_team(&state,&headers).await{Ok(v)=>v,Err(e)=>return json_error(e)};let pool=pool(&state).unwrap();if let Err(e)=team_call_access(pool,&c,id).await{return json_error(e)};match media_state(&state,id,"team",input).await{Ok(v)=>Json(v).into_response(),Err(e)=>json_error(e)}}
pub async fn widget_media_state(State(state):State<Arc<AppState>>,Path((site_key,session_id,id)):Path<(Uuid,Uuid,Uuid)>,headers:HeaderMap,Json(input):Json<MediaStateInput>)->Response{let origin=headers.get(header::ORIGIN).and_then(|v|v.to_str().ok()).and_then(normalize_origin);let c=match authenticate_widget(&state,&headers,site_key,session_id).await{Ok(v)=>v,Err(e)=>return widget_error(e,origin.as_deref())};let pool=pool(&state).unwrap();if let Err(e)=widget_call_access(pool,&c,id).await{return widget_error(e,Some(&c.origin))};match media_state(&state,id,"visitor",input).await{Ok(v)=>widget_response(StatusCode::OK,v,Some(&c.origin)),Err(e)=>widget_error(e,Some(&c.origin))}}

#[cfg(test)]
mod tests {
    use super::normalize_origin;
    #[test]
    fn origin_normalization_removes_paths_and_default_noise() {
        assert_eq!(normalize_origin("https://Example.com/path"),Some("https://example.com".to_owned()));
        assert!(normalize_origin("javascript:alert(1)").is_none());
    }
}
