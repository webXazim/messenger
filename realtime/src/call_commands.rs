use std::sync::Arc;

use anyhow::{Context, Result};
use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::{Postgres, Transaction};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use uuid::Uuid;

use crate::{
    command_auth::CommandIdentity,
    command_delivery::deliver_committed,
    commands::{conversation_event_audiences, error_response},
    config::ChatCallRuntimeBackend,
    database::CommittedEvent,
    state::AppState,
};

#[derive(Deserialize)]
pub struct StartCallRequest {
    call_type: String,
    #[serde(default)]
    metadata: Value,
}

#[derive(Default, Deserialize)]
pub struct CallActionRequest {
    #[serde(default)]
    reason: String,
}

#[derive(Default, Deserialize)]
pub struct RecentCallQuery {
    #[serde(default)]
    status: String,
}

struct CallCommandResult {
    payload: Value,
    events: Vec<CommittedEvent>,
}

fn require_axum(state: &AppState) -> Option<axum::response::Response> {
    (!matches!(state.config.chat_call_runtime_backend, ChatCallRuntimeBackend::SqlxShadow | ChatCallRuntimeBackend::Axum)).then(|| {
        error_response(StatusCode::NOT_FOUND, "axum_call_runtime_disabled", "Axum call runtime endpoints are not active.")
    })
}

fn identity(state: &AppState, headers: &HeaderMap) -> std::result::Result<CommandIdentity, axum::response::Response> {
    state.command_auth.authenticate(headers).map_err(|_| {
        error_response(StatusCode::UNAUTHORIZED, "authentication_failed", "Authentication credentials were not provided or are invalid.")
    })
}

fn command_error(error: anyhow::Error) -> axum::response::Response {
    let detail = error.to_string();
    if detail.contains("direct conversation is blocked") {
        error_response(StatusCode::FORBIDDEN, "call_forbidden", "Calls are not allowed between these users.")
    } else if detail.contains("not an active participant") || detail.contains("call was not found") {
        error_response(StatusCode::NOT_FOUND, "call_not_found", "The call or conversation was not found.")
    } else if detail.contains("already has an active call") || detail.contains("participant is busy") {
        error_response(StatusCode::CONFLICT, "active_call_exists", "A participant already has an active call.")
    } else if detail.contains("cannot be") || detail.contains("unsupported call type") {
        error_response(StatusCode::CONFLICT, "invalid_call_state", &detail)
    } else {
        tracing::error!(error=%error, "Axum call command failed");
        error_response(StatusCode::INTERNAL_SERVER_ERROR, "call_command_failed", "The call operation could not be completed.")
    }
}

pub async fn start_call(
    State(state): State<Arc<AppState>>,
    Path(conversation_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<StartCallRequest>,
) -> impl IntoResponse {
    if let Some(response) = require_axum(&state) { return response; }
    let identity = match identity(&state, &headers) { Ok(value) => value, Err(response) => return response };
    if !matches!(input.call_type.as_str(), "voice" | "video") {
        return error_response(StatusCode::BAD_REQUEST, "invalid_call_type", "Call type must be voice or video.");
    }
    match start_call_tx(&state, conversation_id, &identity, &input).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            (StatusCode::CREATED, Json(result.payload)).into_response()
        }
        Err(error) => command_error(error),
    }
}

pub async fn get_call(
    State(state): State<Arc<AppState>>,
    Path(call_id): Path<Uuid>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Some(response) = require_axum(&state) { return response; }
    let identity = match identity(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match read_call(&state, call_id, &identity).await {
        Ok(Some(payload)) => Json(payload).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "call_not_found", "The call was not found."),
        Err(error) => command_error(error),
    }
}

pub async fn recent_calls(
    State(state): State<Arc<AppState>>,
    Query(query): Query<RecentCallQuery>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Some(response) = require_axum(&state) { return response; }
    let identity = match identity(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match read_recent_calls(&state, &identity, &query.status).await {
        Ok(results) => Json(json!({"results": results, "next": null, "previous": null})).into_response(),
        Err(error) => command_error(error),
    }
}

pub async fn accept_call(State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap) -> impl IntoResponse {
    mutate_call(state, call_id, headers, "accept", String::new()).await
}

pub async fn decline_call(
    State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap, Json(input): Json<CallActionRequest>,
) -> impl IntoResponse {
    mutate_call(state, call_id, headers, "decline", input.reason).await
}

pub async fn end_call(
    State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap, Json(input): Json<CallActionRequest>,
) -> impl IntoResponse {
    mutate_call(state, call_id, headers, "end", input.reason).await
}

async fn mutate_call(state: Arc<AppState>, call_id: Uuid, headers: HeaderMap, action: &'static str, reason: String) -> axum::response::Response {
    if let Some(response) = require_axum(&state) { return response; }
    let identity = match identity(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match mutate_call_tx(&state, call_id, &identity, action, &reason).await {
        Ok(result) => {
            deliver_committed(&state, &result.events).await;
            Json(result.payload).into_response()
        }
        Err(error) => command_error(error),
    }
}

async fn actor_id(tx: &mut Transaction<'_, Postgres>, identity: &CommandIdentity) -> Result<i64> {
    let id = if let Some(user_id) = identity.claimed_user_id {
        sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE id=$1 AND is_active=TRUE")
            .bind(user_id).persistent(false).fetch_optional(&mut **tx).await?
    } else if !identity.email.is_empty() {
        sqlx::query_scalar::<_, i64>("SELECT id FROM accounts_user WHERE LOWER(email)=$1 AND is_active=TRUE LIMIT 1")
            .bind(&identity.email).persistent(false).fetch_optional(&mut **tx).await?
    } else { None };
    id.context("authenticated user does not exist locally")
}

async fn start_call_tx(state: &AppState, conversation_id: Uuid, identity: &CommandIdentity, input: &StartCallRequest) -> Result<CallCommandResult> {
    let pool = state.database.pool.as_ref().context("SQLx call runtime backend is disabled")?;
    let mut tx = pool.begin().await?;
    let actor = actor_id(&mut tx, identity).await?;
    let conversation_type = sqlx::query_scalar::<_, String>(
        "SELECT c.type FROM chat_conversation c JOIN chat_conversationparticipant p ON p.conversation_id=c.id WHERE c.id=$1 AND c.is_active=TRUE AND p.user_id=$2 AND p.left_at IS NULL AND p.banned_at IS NULL AND p.is_blocked=FALSE FOR UPDATE OF c"
    ).bind(conversation_id).bind(actor).persistent(false).fetch_optional(&mut *tx).await?
        .context("actor is not an active participant")?;

    if let Some(active) = sqlx::query_scalar::<_, Uuid>(
        "SELECT id FROM chat_callsession WHERE conversation_id=$1 AND status IN ('initiated','ringing','ongoing') ORDER BY started_at DESC LIMIT 1"
    ).bind(conversation_id).persistent(false).fetch_optional(&mut *tx).await? {
        let payload = call_payload_tx(&mut tx, active, actor).await?.context("call was not found")?;
        tx.commit().await?;
        return Ok(CallCommandResult { payload, events: Vec::new() });
    }

    if conversation_type == "direct" {
        let blocked = sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM chat_userblock b JOIN chat_conversationparticipant peer ON peer.conversation_id=$1 AND peer.user_id<>$2 AND peer.left_at IS NULL AND peer.banned_at IS NULL WHERE (b.blocker_id=$2 AND b.blocked_id=peer.user_id) OR (b.blocker_id=peer.user_id AND b.blocked_id=$2))",
        ).bind(conversation_id).bind(actor).persistent(false).fetch_one(&mut *tx).await?;
        if blocked { anyhow::bail!("direct conversation is blocked"); }
    }

    let participant_ids = sqlx::query_scalar::<_, i64>(
        "SELECT user_id FROM chat_conversationparticipant WHERE conversation_id=$1 AND left_at IS NULL AND banned_at IS NULL AND is_blocked=FALSE ORDER BY joined_at"
    ).bind(conversation_id).persistent(false).fetch_all(&mut *tx).await?;
    if participant_ids.len() < 2 { anyhow::bail!("call cannot be started without another participant"); }
    if conversation_type == "group" && participant_ids.len() > 8 { anyhow::bail!("call cannot exceed 8 participants"); }
    let busy = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM chat_callparticipant cp JOIN chat_callsession c ON c.id=cp.call_id WHERE cp.user_id=ANY($1) AND cp.state IN ('ringing','joined') AND c.status IN ('initiated','ringing','ongoing'))"
    ).bind(&participant_ids).persistent(false).fetch_one(&mut *tx).await?;
    if busy { anyhow::bail!("participant is busy or already has an active call"); }

    let call_id = Uuid::new_v4();
    let room_key = Uuid::new_v4().simple().to_string();
    let metadata = if input.metadata.is_object() { input.metadata.clone() } else { json!({}) };
    sqlx::query("INSERT INTO chat_callsession (id,created_at,updated_at,conversation_id,initiated_by_id,call_type,status,room_key,answered_by_id,started_at,answered_at,ended_at,last_signal_at,ended_reason,metadata) VALUES ($1,NOW(),NOW(),$2,$3,$4,'ringing',$5,NULL,NOW(),NULL,NULL,NULL,'',$6)")
        .bind(call_id).bind(conversation_id).bind(actor).bind(&input.call_type).bind(room_key).bind(metadata)
        .persistent(false).execute(&mut *tx).await?;
    for user_id in participant_ids {
        let joined = user_id == actor;
        sqlx::query("INSERT INTO chat_callparticipant (id,created_at,updated_at,call_id,user_id,state,network_quality,preferred_video_quality,audio_enabled,video_enabled,is_on_hold,reconnecting,connection_state,audio_route,screen_share_enabled,screen_share_started_at,raised_hand_at,is_speaking,speaking_level,last_spoke_at,reconnect_deadline_at,last_quality_report_at,last_seen_signal_at,last_heartbeat_at,packet_loss_pct,jitter_ms,round_trip_time_ms,bitrate_kbps,frame_rate,quality_score,quality_alert,invited_at,joined_at,left_at,diagnostics) VALUES ($1,NOW(),NOW(),$2,$3,$4,'unknown','auto',TRUE,TRUE,FALSE,FALSE,'new','auto',FALSE,NULL,NULL,FALSE,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,100,'',NOW(),CASE WHEN $5 THEN NOW() ELSE NULL END,NULL,'{}'::jsonb)")
            .bind(Uuid::new_v4()).bind(call_id).bind(user_id).bind(if joined { "joined" } else { "ringing" }).bind(joined)
            .persistent(false).execute(&mut *tx).await?;
    }
    insert_audit(&mut tx, actor, conversation_id, "call_started", json!({"call_id":call_id,"call_type":input.call_type})).await?;
    let payload = call_payload_tx(&mut tx, call_id, actor).await?.context("call was not found")?;
    let mut events = vec![record_event(&mut tx, "call.started", payload.clone(), conversation_id).await?];
    if let Some(event) = upsert_call_timeline(&mut tx, call_id, conversation_id, actor, &payload).await? { events.push(event); }
    tx.commit().await?;
    Ok(CallCommandResult { payload, events })
}

async fn mutate_call_tx(state: &AppState, call_id: Uuid, identity: &CommandIdentity, action: &str, reason: &str) -> Result<CallCommandResult> {
    let pool = state.database.pool.as_ref().context("SQLx call runtime backend is disabled")?;
    let mut tx = pool.begin().await?;
    let actor = actor_id(&mut tx, identity).await?;
    let row = sqlx::query_as::<_, (Uuid, String, i64)>(
        "SELECT c.conversation_id,c.status,c.initiated_by_id FROM chat_callsession c JOIN chat_callparticipant p ON p.call_id=c.id JOIN chat_conversationparticipant cp ON cp.conversation_id=c.conversation_id AND cp.user_id=p.user_id WHERE c.id=$1 AND p.user_id=$2 AND cp.left_at IS NULL AND cp.is_blocked=FALSE FOR UPDATE OF c,p,cp"
    ).bind(call_id).bind(actor).persistent(false).fetch_optional(&mut *tx).await?.context("call was not found")?;
    let (conversation_id, status, initiator) = row;
    if !matches!(status.as_str(), "initiated" | "ringing" | "ongoing") { anyhow::bail!("call cannot be changed from its current state"); }

    let (event_name, audit_name) = match action {
        "accept" => {
            sqlx::query("UPDATE chat_callparticipant SET state='joined',joined_at=COALESCE(joined_at,NOW()),left_at=NULL,updated_at=NOW() WHERE call_id=$1 AND user_id=$2")
                .bind(call_id).bind(actor).persistent(false).execute(&mut *tx).await?;
            sqlx::query("UPDATE chat_callsession SET status='ongoing',answered_by_id=$2,answered_at=COALESCE(answered_at,NOW()),updated_at=NOW() WHERE id=$1")
                .bind(call_id).bind(actor).persistent(false).execute(&mut *tx).await?;
            ("call.accepted", "call_joined")
        }
        "decline" => {
            sqlx::query("UPDATE chat_callparticipant SET state='declined',left_at=NOW(),updated_at=NOW() WHERE call_id=$1 AND user_id=$2")
                .bind(call_id).bind(actor).persistent(false).execute(&mut *tx).await?;
            sqlx::query("UPDATE chat_callparticipant SET state='left',left_at=NOW(),updated_at=NOW() WHERE call_id=$1 AND user_id<>$2 AND state IN ('ringing','joined')")
                .bind(call_id).bind(actor).persistent(false).execute(&mut *tx).await?;
            sqlx::query("UPDATE chat_callsession SET status='declined',ended_at=NOW(),ended_reason=$2,updated_at=NOW() WHERE id=$1")
                .bind(call_id).bind(if reason.trim().is_empty() { "declined" } else { reason.trim() }).persistent(false).execute(&mut *tx).await?;
            ("call.declined", "call_declined")
        }
        "end" => {
            sqlx::query("UPDATE chat_callparticipant SET state='left',left_at=COALESCE(left_at,NOW()),updated_at=NOW() WHERE call_id=$1 AND state IN ('ringing','joined')")
                .bind(call_id).persistent(false).execute(&mut *tx).await?;
            sqlx::query("UPDATE chat_callsession SET status='ended',ended_at=NOW(),ended_reason=$2,updated_at=NOW() WHERE id=$1")
                .bind(call_id).bind(if reason.trim().is_empty() { "ended" } else { reason.trim() }).persistent(false).execute(&mut *tx).await?;
            ("call.ended", "call_ended")
        }
        _ => anyhow::bail!("unsupported call action"),
    };
    insert_audit(&mut tx, actor, conversation_id, audit_name, json!({"call_id":call_id,"initiated_by_id":initiator})).await?;
    let payload = call_payload_tx(&mut tx, call_id, actor).await?.context("call was not found")?;
    let mut events = vec![record_event(&mut tx, event_name, payload.clone(), conversation_id).await?];
    if let Some(event) = upsert_call_timeline(&mut tx, call_id, conversation_id, actor, &payload).await? { events.push(event); }
    tx.commit().await?;
    Ok(CallCommandResult { payload, events })
}

async fn read_call(state: &AppState, call_id: Uuid, identity: &CommandIdentity) -> Result<Option<Value>> {
    let pool = state.database.pool.as_ref().context("SQLx call runtime backend is disabled")?;
    let mut tx = pool.begin().await?;
    let actor = actor_id(&mut tx, identity).await?;
    let payload = call_payload_tx(&mut tx, call_id, actor).await?;
    tx.commit().await?;
    Ok(payload)
}

async fn read_recent_calls(state: &AppState, identity: &CommandIdentity, status: &str) -> Result<Vec<Value>> {
    let pool = state.database.pool.as_ref().context("SQLx call runtime backend is disabled")?;
    let mut tx = pool.begin().await?;
    let actor = actor_id(&mut tx, identity).await?;
    let ids = sqlx::query_scalar::<_, Uuid>(
        "SELECT DISTINCT c.id FROM chat_callsession c JOIN chat_callparticipant p ON p.call_id=c.id JOIN chat_conversationparticipant cp ON cp.conversation_id=c.conversation_id AND cp.user_id=p.user_id WHERE p.user_id=$1 AND cp.left_at IS NULL AND cp.is_blocked=FALSE AND ($2='' OR c.status=$2) ORDER BY c.id LIMIT 100"
    ).bind(actor).bind(status).persistent(false).fetch_all(&mut *tx).await?;
    let mut results = Vec::with_capacity(ids.len());
    for id in ids { if let Some(payload) = call_payload_tx(&mut tx, id, actor).await? { results.push(payload); } }
    results.sort_by(|a,b| b.get("started_at").and_then(Value::as_str).cmp(&a.get("started_at").and_then(Value::as_str)));
    tx.commit().await?;
    Ok(results)
}

async fn call_payload_tx(tx: &mut Transaction<'_, Postgres>, call_id: Uuid, actor: i64) -> Result<Option<Value>> {
    let payload = sqlx::query_scalar::<_, Value>(r#"
SELECT jsonb_build_object(
 'id',c.id::text,'conversation',c.conversation_id::text,'call_type',c.call_type,'status',c.status,'room_key',c.room_key,
 'started_at',c.started_at,'answered_at',c.answered_at,'ended_at',c.ended_at,'ended_reason',c.ended_reason,'metadata',c.metadata,
 'initiated_by',jsonb_build_object('id',iu.id::text,'username',iu.username,'email',iu.email,'display_name',COALESCE(NULLIF(ip.display_name,''),NULLIF(TRIM(iu.first_name||' '||iu.last_name),''),iu.username),'avatar',NULLIF(ip.avatar,'')),
 'answered_by',CASE WHEN au.id IS NULL THEN NULL ELSE jsonb_build_object('id',au.id::text,'username',au.username,'email',au.email,'display_name',COALESCE(NULLIF(ap.display_name,''),NULLIF(TRIM(au.first_name||' '||au.last_name),''),au.username),'avatar',NULLIF(ap.avatar,'')) END,
 'participants',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',cp.id::text,'state',cp.state,'network_quality',cp.network_quality,'preferred_video_quality',cp.preferred_video_quality,'audio_enabled',cp.audio_enabled,'video_enabled',cp.video_enabled,'is_on_hold',cp.is_on_hold,'reconnecting',cp.reconnecting,'connection_state',cp.connection_state,'audio_route',cp.audio_route,'screen_share_enabled',cp.screen_share_enabled,'hand_raised',cp.raised_hand_at IS NOT NULL,'is_speaking',cp.is_speaking,'speaking_level',cp.speaking_level,'quality_score',cp.quality_score,'quality_alert',cp.quality_alert,'user',jsonb_build_object('id',u.id::text,'username',u.username,'email',u.email,'display_name',COALESCE(NULLIF(p.display_name,''),NULLIF(TRIM(u.first_name||' '||u.last_name),''),u.username),'avatar',NULLIF(p.avatar,''))) ORDER BY cp.invited_at) FROM chat_callparticipant cp JOIN accounts_user u ON u.id=cp.user_id LEFT JOIN accounts_profile p ON p.user_id=u.id WHERE cp.call_id=c.id),'[]'::jsonb),
 'duration_seconds',CASE WHEN c.answered_at IS NULL THEN 0 ELSE GREATEST(EXTRACT(EPOCH FROM (COALESCE(c.ended_at,NOW())-c.answered_at))::int,0) END,
 'ringing_seconds',CASE WHEN c.status IN ('initiated','ringing') THEN GREATEST(EXTRACT(EPOCH FROM (NOW()-c.started_at))::int,0) ELSE 0 END,
 'ring_timeout_seconds',45,
 'call_state',CASE WHEN c.status='ongoing' THEN 'ongoing' WHEN c.status IN ('initiated','ringing') AND c.initiated_by_id=$2 THEN 'ringing' WHEN c.status IN ('initiated','ringing') THEN 'incoming' ELSE c.status END,
 'participant_summary',jsonb_build_object('joined',(SELECT COUNT(*) FROM chat_callparticipant x WHERE x.call_id=c.id AND x.state='joined'),'ringing',(SELECT COUNT(*) FROM chat_callparticipant x WHERE x.call_id=c.id AND x.state='ringing')),
 'network_recommendation',jsonb_build_object('mode','standard','reason',CASE WHEN c.status='ongoing' THEN 'healthy_network' ELSE 'awaiting_answer' END)
)
FROM chat_callsession c
JOIN accounts_user iu ON iu.id=c.initiated_by_id LEFT JOIN accounts_profile ip ON ip.user_id=iu.id
LEFT JOIN accounts_user au ON au.id=c.answered_by_id LEFT JOIN accounts_profile ap ON ap.user_id=au.id
WHERE c.id=$1
  AND EXISTS(SELECT 1 FROM chat_callparticipant mine WHERE mine.call_id=c.id AND mine.user_id=$2)
  AND EXISTS(SELECT 1 FROM chat_conversationparticipant membership WHERE membership.conversation_id=c.conversation_id AND membership.user_id=$2 AND membership.left_at IS NULL AND membership.is_blocked=FALSE)
"#).bind(call_id).bind(actor).persistent(false).fetch_optional(&mut **tx).await?;
    Ok(payload)
}

async fn record_event(tx: &mut Transaction<'_, Postgres>, name: &str, data: Value, conversation_id: Uuid) -> Result<CommittedEvent> {
    let event_id = Uuid::new_v4();
    let occurred_at = OffsetDateTime::now_utc().format(&Rfc3339).unwrap_or_default();
    let payload = json!({"type":"chat.event","version":1,"event":name,"event_id":event_id,"occurred_at":occurred_at,"data":data});
    let audiences = conversation_event_audiences(tx, conversation_id).await?;
    let audiences_json = json!(&audiences);
    sqlx::query("INSERT INTO common_realtimeoutboxevent (id,created_at,updated_at,event_id,event_name,payload,audiences,status,attempts,available_at,published_at,delivery_target,published_transport,stream_entry_id,last_error) VALUES ($1,NOW(),NOW(),$2,$3,$4,$5,'pending',0,NOW(),NULL,'nats_jetstream','','','')")
        .bind(Uuid::new_v4()).bind(event_id).bind(name).bind(&payload).bind(audiences_json).persistent(false).execute(&mut **tx).await?;
    Ok(CommittedEvent { event_id, event_name: name.to_owned(), payload, audiences })
}

async fn insert_audit(tx: &mut Transaction<'_, Postgres>, actor: i64, conversation: Uuid, event_type: &str, metadata: Value) -> Result<()> {
    sqlx::query("INSERT INTO chat_chatauditlog (id,created_at,updated_at,actor_id,conversation_id,message_id,event_type,metadata) VALUES ($1,NOW(),NOW(),$2,$3,NULL,$4,$5)")
        .bind(Uuid::new_v4()).bind(actor).bind(conversation).bind(event_type).bind(metadata).persistent(false).execute(&mut **tx).await?;
    Ok(())
}

async fn upsert_call_timeline(tx: &mut Transaction<'_, Postgres>, call_id: Uuid, conversation_id: Uuid, actor: i64, call: &Value) -> Result<Option<CommittedEvent>> {
    let status = call.get("status").and_then(Value::as_str).unwrap_or("ringing");
    let duration = call.get("duration_seconds").and_then(Value::as_i64).unwrap_or(0);
    let (outcome, text) = match status {
        "ringing" | "initiated" => ("ringing", "Outgoing call".to_owned()),
        "ongoing" => ("received", "Call connected".to_owned()),
        "declined" => ("declined", "Call declined".to_owned()),
        "missed" => ("missed", "Missed call".to_owned()),
        _ if duration > 0 => ("completed", format!("Call ended · {}s", duration)),
        _ => ("cancelled", "Call cancelled".to_owned()),
    };
    let metadata = json!({
        "system_event":"call","call_id":call_id,"call_type":call.get("call_type"),"call_status":status,"call_outcome":outcome,
        "summary_text":text,"reason":call.get("ended_reason"),"duration_seconds":duration,"ringing_duration_seconds":call.get("ringing_seconds"),
        "started_at":call.get("started_at"),"answered_at":call.get("answered_at"),"ended_at":call.get("ended_at"),
        "initiated_by_id":call.get("initiated_by").and_then(|u|u.get("id")),"answered_by_id":call.get("answered_by").and_then(|u|u.get("id")),"actor_id":actor
    });
    let existing = sqlx::query_scalar::<_, Uuid>("SELECT id FROM chat_message WHERE conversation_id=$1 AND type='system' AND metadata->>'system_event'='call' AND metadata->>'call_id'=$2 ORDER BY created_at DESC LIMIT 1")
        .bind(conversation_id).bind(call_id.to_string()).persistent(false).fetch_optional(&mut **tx).await?;
    let (message_id, event_name) = if let Some(message_id) = existing {
        sqlx::query("UPDATE chat_message SET sender_id=$2,text=$3,metadata=$4,is_deleted=FALSE,deleted_at=NULL,updated_at=NOW() WHERE id=$1")
            .bind(message_id).bind(call.get("initiated_by").and_then(|u|u.get("id")).and_then(Value::as_str).and_then(|v|v.parse::<i64>().ok()).unwrap_or(actor)).bind(&text).bind(&metadata).persistent(false).execute(&mut **tx).await?;
        (message_id, "message.updated")
    } else {
        let sequence = sqlx::query_scalar::<_, i64>("UPDATE chat_conversation SET next_message_sequence=next_message_sequence+1,updated_at=NOW() WHERE id=$1 RETURNING next_message_sequence::bigint")
            .bind(conversation_id).persistent(false).fetch_one(&mut **tx).await?;
        let message_id = Uuid::new_v4();
        sqlx::query("INSERT INTO chat_message (id,created_at,updated_at,conversation_id,sender_id,type,text,metadata,reply_to_id,forwarded_from_id,is_edited,edited_at,edit_locked_at,edit_locked_reason,is_deleted,deleted_at,client_temp_id,sequence,delivery_status,failed_reason,retry_count) VALUES ($1,NOW(),NOW(),$2,$3,'system',$4,$5,NULL,NULL,FALSE,NULL,NULL,'',FALSE,NULL,'',$6,'sent','',0)")
            .bind(message_id).bind(conversation_id).bind(actor).bind(&text).bind(&metadata).bind(sequence).persistent(false).execute(&mut **tx).await?;
        (message_id, "message.created")
    };
    sqlx::query("UPDATE chat_conversation SET last_message_id=$2,last_message_at=(SELECT created_at FROM chat_message WHERE id=$2),updated_at=NOW() WHERE id=$1")
        .bind(conversation_id).bind(message_id).persistent(false).execute(&mut **tx).await?;
    let message_payload = sqlx::query_scalar::<_, Value>("SELECT jsonb_build_object('id',m.id::text,'conversation_id',m.conversation_id::text,'type',m.type,'text',m.text,'sender',jsonb_build_object('id',m.sender_id::text),'created_at',m.created_at,'updated_at',m.updated_at,'attachments','[]'::jsonb,'delivery_status',m.delivery_status,'is_deleted',m.is_deleted,'metadata',m.metadata,'client_temp_id',m.client_temp_id,'sequence',m.sequence) FROM chat_message m WHERE m.id=$1")
        .bind(message_id).persistent(false).fetch_one(&mut **tx).await?;
    Ok(Some(record_event(tx, event_name, message_payload, conversation_id).await?))
}
