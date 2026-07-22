use std::sync::Arc;

use anyhow::{Context, Result};
use axum::{
    extract::{Path, State},
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
    command_auth::{CommandAuthError, CommandIdentity},
    commands::error_response,
    config::ChatCallRuntimeBackend,
    nats_core::{publish_shared_after_local, EphemeralPriority},
    protocol::{event_message, AudienceKey, AudienceKind},
    state::AppState,
};

const ALLOWED_SIGNAL_TYPES: &[&str] = &[
    "offer", "answer", "ice_candidate", "renegotiate", "hangup", "busy",
    "ice_restart", "network_state", "quality_update", "media_toggle",
    "speaker_hint", "fallback_audio_only", "receiver_report", "request_keyframe",
];
const NETWORK_QUALITIES: &[&str] = &["unknown", "excellent", "good", "fair", "poor", "offline"];
const VIDEO_QUALITIES: &[&str] = &["auto", "off", "low", "medium", "high"];
const CONNECTION_STATES: &[&str] = &["new", "checking", "connected", "degraded", "disconnected", "failed", "closed"];
const AUDIO_ROUTES: &[&str] = &["auto", "speaker", "earpiece", "bluetooth", "wired"];

#[derive(Debug, Deserialize)]
pub(crate) struct SignalRequest {
    signal_type: String,
    #[serde(default)]
    payload: Value,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct HeartbeatRequest {
    #[serde(default)]
    network_quality: Option<String>,
    #[serde(default)]
    metrics: Value,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct QualityReportRequest {
    #[serde(default)] packet_loss_pct: Option<f64>,
    #[serde(default)] jitter_ms: Option<i64>,
    #[serde(default)] round_trip_time_ms: Option<i64>,
    #[serde(default)] bitrate_kbps: Option<i64>,
    #[serde(default)] frame_rate: Option<i64>,
    #[serde(default)] network_quality: Option<String>,
    #[serde(default)] preferred_video_quality: Option<String>,
    #[serde(default, alias = "microphone_enabled")] audio_enabled: Option<bool>,
    #[serde(default, alias = "camera_enabled")] video_enabled: Option<bool>,
    #[serde(default)] diagnostics: Value,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct MediaStateRequest {
    #[serde(default, alias = "microphone_enabled")] audio_enabled: Option<bool>,
    #[serde(default, alias = "camera_enabled")] video_enabled: Option<bool>,
    #[serde(default)] is_on_hold: Option<bool>,
    #[serde(default)] reconnecting: Option<bool>,
    #[serde(default, alias = "screen_sharing")] screen_share_enabled: Option<bool>,
    #[serde(default)] hand_raised: Option<bool>,
    #[serde(default)] connection_state: Option<String>,
    #[serde(default)] audio_route: Option<String>,
    #[serde(default)] preferred_video_quality: Option<String>,
    #[serde(default)] diagnostics: Value,
    #[serde(default)] bitrate_kbps: Option<i64>,
    #[serde(default)] packet_loss_ratio: Option<f64>,
    #[serde(default)] latency_ms: Option<i64>,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct SpeakerStateRequest {
    #[serde(default)] speaking_level: Option<i32>,
    #[serde(default)] is_speaking: Option<bool>,
}

fn runtime_enabled(state: &AppState) -> bool {
    matches!(
        state.config.chat_call_runtime_backend,
        ChatCallRuntimeBackend::SqlxShadow | ChatCallRuntimeBackend::Axum
    )
}

fn authenticate(state: &AppState, headers: &HeaderMap) -> std::result::Result<CommandIdentity, axum::response::Response> {
    state.command_auth.authenticate(headers).map_err(|error| {
        let detail = match error {
            CommandAuthError::Missing => "Authentication credentials were not provided.",
            _ => "Authentication credentials are invalid or expired.",
        };
        error_response(StatusCode::UNAUTHORIZED, "authentication_failed", detail)
    })
}

fn runtime_error(error: anyhow::Error) -> axum::response::Response {
    let detail = error.to_string();
    if detail.contains("authenticated user") {
        error_response(StatusCode::UNAUTHORIZED, "authentication_failed", "The authenticated account is not available.")
    } else if detail.contains("call was not found") || detail.contains("not a call participant") {
        error_response(StatusCode::NOT_FOUND, "call_not_found", "The call was not found.")
    } else if detail.contains("not active") || detail.contains("not live") {
        error_response(StatusCode::CONFLICT, "invalid_call_state", "The call is no longer active.")
    } else if detail.contains("invalid") || detail.contains("unsupported") || detail.contains("too large") {
        error_response(StatusCode::BAD_REQUEST, "invalid_call_payload", &detail)
    } else {
        tracing::error!(error=%error, "Axum call runtime operation failed");
        error_response(StatusCode::INTERNAL_SERVER_ERROR, "call_runtime_failed", "The call operation could not be completed.")
    }
}

fn require_runtime(state: &AppState) -> Option<axum::response::Response> {
    (!runtime_enabled(state)).then(|| error_response(
        StatusCode::NOT_FOUND,
        "axum_call_runtime_disabled",
        "Axum call runtime endpoints are not active.",
    ))
}

fn object_or_empty(value: Value, max_bytes: usize) -> Result<Value> {
    let value = if value.is_null() {
        json!({})
    } else if value.is_object() {
        value
    } else {
        anyhow::bail!("invalid JSON object payload");
    };
    if serde_json::to_vec(&value)?.len() > max_bytes {
        anyhow::bail!("diagnostics payload is too large");
    }
    Ok(value)
}

fn allowed(value: Option<String>, choices: &[&str], label: &str) -> Result<Option<String>> {
    match value.map(|value| value.trim().to_ascii_lowercase()).filter(|value| !value.is_empty()) {
        Some(value) if choices.contains(&value.as_str()) => Ok(Some(value)),
        Some(_) => anyhow::bail!("invalid {label}"),
        None => Ok(None),
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

async fn lock_call_participant(
    tx: &mut Transaction<'_, Postgres>,
    call_id: Uuid,
    actor: i64,
) -> Result<(Uuid, String, String)> {
    sqlx::query_as::<_, (Uuid, String, String)>(
        "SELECT c.conversation_id,c.status,p.state FROM chat_callsession c JOIN chat_conversation conv ON conv.id=c.conversation_id JOIN chat_callparticipant p ON p.call_id=c.id JOIN chat_conversationparticipant cp ON cp.conversation_id=c.conversation_id AND cp.user_id=p.user_id WHERE c.id=$1 AND p.user_id=$2 AND cp.left_at IS NULL AND cp.is_blocked=FALSE AND (conv.type<>'direct' OR NOT EXISTS(SELECT 1 FROM chat_conversationparticipant other JOIN chat_userblock ub ON ((ub.blocker_id=$2 AND ub.blocked_id=other.user_id) OR (ub.blocker_id=other.user_id AND ub.blocked_id=$2)) WHERE other.conversation_id=conv.id AND other.user_id<>$2 AND other.left_at IS NULL AND other.banned_at IS NULL)) FOR UPDATE OF p,cp"
    )
    .bind(call_id).bind(actor).persistent(false).fetch_optional(&mut **tx).await?
    .context("actor is not a call participant")
}

fn ensure_live(status: &str, participant_state: &str) -> Result<()> {
    if !matches!(status, "initiated" | "ringing" | "ongoing") {
        anyhow::bail!("call is not live");
    }
    if !matches!(participant_state, "invited" | "ringing" | "joined") {
        anyhow::bail!("call participant is not active");
    }
    Ok(())
}

async fn touch_call(tx: &mut Transaction<'_, Postgres>, call_id: Uuid) -> Result<()> {
    sqlx::query("UPDATE chat_callsession SET last_signal_at=NOW(),updated_at=NOW() WHERE id=$1 AND (last_signal_at IS NULL OR last_signal_at < NOW()-INTERVAL '3 seconds')")
        .bind(call_id).persistent(false).execute(&mut **tx).await?;
    Ok(())
}

async fn participant_ids(tx: &mut Transaction<'_, Postgres>, call_id: Uuid, exclude: i64) -> Result<Vec<i64>> {
    Ok(sqlx::query_scalar::<_, i64>(
        "SELECT p.user_id FROM chat_callparticipant p JOIN chat_callsession c ON c.id=p.call_id JOIN chat_conversationparticipant cp ON cp.conversation_id=c.conversation_id AND cp.user_id=p.user_id WHERE p.call_id=$1 AND p.user_id<>$2 AND p.state IN ('invited','ringing','joined') AND cp.left_at IS NULL AND cp.is_blocked=FALSE ORDER BY p.invited_at"
    ).bind(call_id).bind(exclude).persistent(false).fetch_all(&mut **tx).await?)
}

async fn broadcast(
    state: &AppState,
    conversation_id: Uuid,
    event: &str,
    payload: Value,
    priority: EphemeralPriority,
    target_actor_id: Option<String>,
) {
    let Ok(message) = event_message(event, payload) else { return; };
    let audiences = vec![AudienceKey { kind: AudienceKind::Conversation, identifier: conversation_id.to_string() }];
    match priority {
        EphemeralPriority::High => { state.registry.fanout_high_filtered(&audiences, message.clone(), None, target_actor_id.as_deref()); }
        EphemeralPriority::Low => { state.registry.fanout_low(&audiences, message.clone(), None, target_actor_id.as_deref()); }
    }
    publish_shared_after_local(state, audiences, message, priority, target_actor_id).await;
}

pub async fn send_signal(
    State(state): State<Arc<AppState>>,
    Path(call_id): Path<Uuid>,
    headers: HeaderMap,
    Json(input): Json<SignalRequest>,
) -> impl IntoResponse {
    if let Some(response) = require_runtime(&state) { return response; }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match send_signal_inner(&state, call_id, &identity, input).await {
        Ok(payload) => (StatusCode::ACCEPTED, Json(payload)).into_response(),
        Err(error) => runtime_error(error),
    }
}

async fn send_signal_inner(state: &AppState, call_id: Uuid, identity: &CommandIdentity, input: SignalRequest) -> Result<Value> {
    let signal_type = input.signal_type.trim().to_ascii_lowercase();
    if !ALLOWED_SIGNAL_TYPES.contains(&signal_type.as_str()) { anyhow::bail!("unsupported signaling event"); }
    let mut payload = object_or_empty(input.payload, 96 * 1024)?;
    let pool = state.database.pool.as_ref().context("SQLx call runtime backend is disabled")?;
    let mut tx = pool.begin().await?;
    let actor = actor_id(&mut tx, identity).await?;
    let (conversation_id, status, participant_state) = lock_call_participant(&mut tx, call_id, actor).await?;
    ensure_live(&status, &participant_state)?;

    let requested_target = payload.get("to_user_id").and_then(|value| {
        value.as_i64().or_else(|| value.as_str().and_then(|raw| raw.parse::<i64>().ok()))
    });
    let recipients = if let Some(target) = requested_target {
        let valid = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_callparticipant p JOIN chat_callsession c ON c.id=p.call_id JOIN chat_conversationparticipant cp ON cp.conversation_id=c.conversation_id AND cp.user_id=p.user_id WHERE p.call_id=$1 AND p.user_id=$2 AND p.state IN ('invited','ringing','joined') AND cp.left_at IS NULL AND cp.is_blocked=FALSE)")
            .bind(call_id).bind(target).persistent(false).fetch_one(&mut *tx).await?;
        if !valid || target == actor { anyhow::bail!("invalid signaling target"); }
        vec![target]
    } else {
        participant_ids(&mut tx, call_id, actor).await?
    };
    let signal_id = payload.get("signal_id").and_then(Value::as_str).map(str::trim).filter(|value| !value.is_empty())
        .map(|value| value.chars().take(128).collect::<String>()).unwrap_or_else(|| Uuid::new_v4().simple().to_string());
    payload.as_object_mut().context("signal payload is not an object")?
        .insert("signal_id".into(), Value::String(signal_id.clone()));

    if signal_type == "quality_update" {
        let network_quality = allowed(payload.get("network_quality").and_then(Value::as_str).map(ToOwned::to_owned), NETWORK_QUALITIES, "network_quality")?;
        let preferred = allowed(payload.get("preferred_video_quality").and_then(Value::as_str).map(ToOwned::to_owned), VIDEO_QUALITIES, "preferred_video_quality")?;
        let metrics = object_or_empty(payload.get("metrics").cloned().unwrap_or_else(|| json!({})), 32 * 1024)?;
        sqlx::query("UPDATE chat_callparticipant SET network_quality=COALESCE($3,network_quality),preferred_video_quality=COALESCE($4,preferred_video_quality),audio_enabled=COALESCE($5,audio_enabled),video_enabled=COALESCE($6,video_enabled),diagnostics=jsonb_set(COALESCE(diagnostics,'{}'::jsonb),'{quality_signal}',$7,TRUE),last_seen_signal_at=NOW(),last_quality_report_at=NOW(),updated_at=NOW() WHERE call_id=$1 AND user_id=$2")
            .bind(call_id).bind(actor).bind(network_quality).bind(preferred)
            .bind(payload.get("audio_enabled").and_then(Value::as_bool))
            .bind(payload.get("video_enabled").and_then(Value::as_bool))
            .bind(metrics).persistent(false).execute(&mut *tx).await?;
        let orchestration = build_orchestration_tx(&mut tx, call_id, state.config.call_grid_layout_threshold).await?;
        payload.as_object_mut().context("signal payload is not an object")?
            .insert("recommendation".into(), orchestration.get("network_recommendation").cloned().unwrap_or_else(|| json!({})));
    }
    touch_call(&mut tx, call_id).await?;
    tx.commit().await?;

    let sent_at = OffsetDateTime::now_utc().format(&Rfc3339).unwrap_or_default();
    let mut delivered_to = Vec::new();
    for recipient in recipients {
        let mut signal = json!({
            "call_id": call_id,
            "conversation_id": conversation_id,
            "signal_id": signal_id.clone(),
            "signal_type": signal_type.clone(),
            "payload": payload.clone(),
            "from_user_id": actor.to_string(),
            "sent_at": sent_at.clone(),
        });
        if requested_target.is_some() {
            signal.as_object_mut().context("signal envelope is not an object")?
                .insert("to_user_id".into(), Value::String(recipient.to_string()));
        }
        let dedupe_key = format!("{actor}:{signal_id}");
        if state.call_signals.push(call_id, recipient, &dedupe_key, signal.clone(), state.config.call_signal_ttl, state.config.call_signal_queue_capacity) {
            delivered_to.push(recipient.to_string());
            broadcast(state, conversation_id, "call.signal", signal, EphemeralPriority::High, Some(recipient.to_string())).await;
        }
    }
    let was_deduplicated = delivered_to.is_empty();
    Ok(json!({
        "call_id": call_id,
        "conversation_id": conversation_id,
        "signal_id": signal_id,
        "signal_type": signal_type,
        "payload": payload,
        "from_user_id": actor.to_string(),
        "to_user_id": requested_target.map(|value| value.to_string()),
        "recipient_user_ids": delivered_to,
        "was_deduplicated": was_deduplicated,
        "sent_at": sent_at,
    }))
}

pub async fn heartbeat(
    State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap, Json(input): Json<HeartbeatRequest>,
) -> impl IntoResponse {
    runtime_mutation(state, call_id, headers, RuntimeMutation::Heartbeat(input)).await
}

pub async fn media_state(
    State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap, Json(input): Json<MediaStateRequest>,
) -> impl IntoResponse {
    runtime_mutation(state, call_id, headers, RuntimeMutation::Media(input)).await
}

pub async fn quality_report(
    State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap, Json(input): Json<QualityReportRequest>,
) -> impl IntoResponse {
    runtime_mutation(state, call_id, headers, RuntimeMutation::Quality(input)).await
}

pub async fn speaker_state(
    State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap, Json(input): Json<SpeakerStateRequest>,
) -> impl IntoResponse {
    runtime_mutation(state, call_id, headers, RuntimeMutation::Speaker(input)).await
}

enum RuntimeMutation {
    Heartbeat(HeartbeatRequest),
    Media(MediaStateRequest),
    Quality(QualityReportRequest),
    Speaker(SpeakerStateRequest),
}

async fn runtime_mutation(state: Arc<AppState>, call_id: Uuid, headers: HeaderMap, mutation: RuntimeMutation) -> axum::response::Response {
    if let Some(response) = require_runtime(&state) { return response; }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match runtime_mutation_inner(&state, call_id, &identity, mutation).await {
        Ok((event, payload, orchestration_event)) => {
            let conversation_id = payload.get("conversation_id").and_then(Value::as_str).and_then(|value| value.parse::<Uuid>().ok());
            if let Some(conversation_id) = conversation_id {
                broadcast(&state, conversation_id, event, payload.clone(), EphemeralPriority::Low, None).await;
                if orchestration_event {
                    if let Some(orchestration) = payload.get("orchestration") {
                        broadcast(&state, conversation_id, "call.orchestration", orchestration.clone(), EphemeralPriority::Low, None).await;
                    }
                }
            }
            let status = if event == "call.speaker_state" { StatusCode::OK } else { StatusCode::ACCEPTED };
            (status, Json(payload)).into_response()
        }
        Err(error) => runtime_error(error),
    }
}

async fn runtime_mutation_inner(state: &AppState, call_id: Uuid, identity: &CommandIdentity, mutation: RuntimeMutation) -> Result<(&'static str, Value, bool)> {
    let pool = state.database.pool.as_ref().context("SQLx call runtime backend is disabled")?;
    let mut tx = pool.begin().await?;
    let actor = actor_id(&mut tx, identity).await?;
    let (conversation_id, status, participant_state) = lock_call_participant(&mut tx, call_id, actor).await?;
    ensure_live(&status, &participant_state)?;

    let (event, orchestration_event, mut response) = match mutation {
        RuntimeMutation::Heartbeat(input) => {
            let network = allowed(input.network_quality, NETWORK_QUALITIES, "network_quality")?;
            let metrics = object_or_empty(input.metrics, 32 * 1024)?;
            let row = sqlx::query_scalar::<_, Value>("UPDATE chat_callparticipant SET network_quality=COALESCE($3,network_quality),last_heartbeat_at=NOW(),last_seen_signal_at=NOW(),reconnecting=FALSE,reconnect_deadline_at=NULL,diagnostics=CASE WHEN $4='{}'::jsonb THEN diagnostics ELSE $4 END,updated_at=NOW() WHERE call_id=$1 AND user_id=$2 RETURNING jsonb_build_object('network_quality',network_quality,'last_heartbeat_at',last_heartbeat_at,'metrics',diagnostics)")
                .bind(call_id).bind(actor).bind(network).bind(metrics).persistent(false).fetch_one(&mut *tx).await?;
            ("call.heartbeat", false, row)
        }
        RuntimeMutation::Media(input) => {
            let connection = allowed(input.connection_state, CONNECTION_STATES, "connection_state")?;
            let audio_route = allowed(input.audio_route, AUDIO_ROUTES, "audio_route")?;
            let preferred = allowed(input.preferred_video_quality, VIDEO_QUALITIES, "preferred_video_quality")?;
            let mut diagnostics = object_or_empty(input.diagnostics, 32 * 1024)?;
            if let Some(map) = diagnostics.as_object_mut() {
                if let Some(value) = input.bitrate_kbps { map.insert("bitrate_kbps".into(), json!(value.clamp(0, 100000))); }
                if let Some(value) = input.packet_loss_ratio { map.insert("packet_loss_ratio".into(), json!(value.clamp(0.0, 1.0))); }
                if let Some(value) = input.latency_ms { map.insert("latency_ms".into(), json!(value.clamp(0, 60000))); }
            }
            let reconnecting = input.reconnecting;
            let reconnect_grace = state.config.call_reconnect_grace_seconds;
            let row = sqlx::query_scalar::<_, Value>("UPDATE chat_callparticipant SET audio_enabled=COALESCE($3,audio_enabled),video_enabled=COALESCE($4,video_enabled),is_on_hold=COALESCE($5,is_on_hold),reconnecting=COALESCE($6,reconnecting),reconnect_deadline_at=CASE WHEN $6=TRUE THEN NOW()+($13::text||' seconds')::interval WHEN $6=FALSE THEN NULL ELSE reconnect_deadline_at END,screen_share_enabled=COALESCE($7,screen_share_enabled),screen_share_started_at=CASE WHEN $7=TRUE AND screen_share_started_at IS NULL THEN NOW() WHEN $7=FALSE THEN NULL ELSE screen_share_started_at END,raised_hand_at=CASE WHEN $8=TRUE THEN COALESCE(raised_hand_at,NOW()) WHEN $8=FALSE THEN NULL ELSE raised_hand_at END,connection_state=COALESCE($9,connection_state),audio_route=COALESCE($10,audio_route),preferred_video_quality=COALESCE($11,preferred_video_quality),diagnostics=CASE WHEN $12='{}'::jsonb THEN diagnostics ELSE $12 END,last_seen_signal_at=NOW(),last_heartbeat_at=COALESCE(last_heartbeat_at,NOW()),updated_at=NOW() WHERE call_id=$1 AND user_id=$2 RETURNING jsonb_build_object('audio_enabled',audio_enabled,'video_enabled',video_enabled,'is_on_hold',is_on_hold,'reconnecting',reconnecting,'connection_state',connection_state,'audio_route',audio_route,'screen_share_enabled',screen_share_enabled,'screen_share_started_at',screen_share_started_at,'hand_raised',raised_hand_at IS NOT NULL,'raised_hand_at',raised_hand_at,'preferred_video_quality',preferred_video_quality,'updated_at',updated_at)")
                .bind(call_id).bind(actor).bind(input.audio_enabled).bind(input.video_enabled).bind(input.is_on_hold).bind(reconnecting)
                .bind(input.screen_share_enabled).bind(input.hand_raised).bind(connection).bind(audio_route).bind(preferred).bind(diagnostics)
                .bind(reconnect_grace).persistent(false).fetch_one(&mut *tx).await?;
            ("call.media_state", true, row)
        }
        RuntimeMutation::Quality(input) => {
            let network = allowed(input.network_quality, NETWORK_QUALITIES, "network_quality")?;
            let preferred = allowed(input.preferred_video_quality, VIDEO_QUALITIES, "preferred_video_quality")?;
            let packet_loss = input.packet_loss_pct.map(|value| value.clamp(0.0, 100.0));
            let jitter = input.jitter_ms.map(|value| value.clamp(0, 10000) as i32);
            let rtt = input.round_trip_time_ms.map(|value| value.clamp(0, 60000) as i32);
            let bitrate = input.bitrate_kbps.map(|value| value.clamp(0, 100000) as i32);
            let frame_rate = input.frame_rate.map(|value| value.clamp(0, 240) as i16);
            let quality_score = compute_quality_score(
                packet_loss,
                jitter.map(i64::from),
                rtt.map(i64::from),
                bitrate.map(i64::from),
                frame_rate.map(i64::from),
                network.as_deref(),
            );
            let quality_score_db = quality_score as i16;
            let quality_alert = quality_alert(quality_score);
            let diagnostics = object_or_empty(input.diagnostics, 32 * 1024)?;
            let report = json!({"packet_loss_pct":packet_loss,"jitter_ms":jitter,"round_trip_time_ms":rtt,"bitrate_kbps":bitrate,"frame_rate":frame_rate,"quality_score":quality_score,"quality_alert":quality_alert,"reported_at":OffsetDateTime::now_utc().format(&Rfc3339).unwrap_or_default()});
            let row = sqlx::query_scalar::<_, Value>("UPDATE chat_callparticipant SET network_quality=COALESCE($3,network_quality),preferred_video_quality=COALESCE($4,preferred_video_quality),audio_enabled=COALESCE($5,audio_enabled),video_enabled=COALESCE($6,video_enabled),packet_loss_pct=CASE WHEN $7::float8 IS NULL THEN NULL ELSE ROUND($7::numeric,2) END,jitter_ms=$8,round_trip_time_ms=$9,bitrate_kbps=$10,frame_rate=$11,quality_score=$12,quality_alert=$13,last_quality_report_at=NOW(),last_seen_signal_at=NOW(),last_heartbeat_at=COALESCE(last_heartbeat_at,NOW()),diagnostics=jsonb_set(CASE WHEN $14='{}'::jsonb THEN COALESCE(diagnostics,'{}'::jsonb) ELSE COALESCE(diagnostics,'{}'::jsonb)||$14 END,'{quality_report}',$15,TRUE),updated_at=NOW() WHERE call_id=$1 AND user_id=$2 RETURNING jsonb_build_object('quality_score',quality_score,'quality_alert',quality_alert,'network_quality',network_quality,'preferred_video_quality',preferred_video_quality,'audio_enabled',audio_enabled,'video_enabled',video_enabled,'packet_loss_pct',packet_loss_pct,'jitter_ms',jitter_ms,'round_trip_time_ms',round_trip_time_ms,'bitrate_kbps',bitrate_kbps,'frame_rate',frame_rate,'reported_at',last_quality_report_at)")
                .bind(call_id).bind(actor).bind(network).bind(preferred).bind(input.audio_enabled).bind(input.video_enabled)
                .bind(packet_loss).bind(jitter).bind(rtt).bind(bitrate).bind(frame_rate).bind(quality_score_db).bind(quality_alert).bind(diagnostics).bind(report)
                .persistent(false).fetch_one(&mut *tx).await?;
            ("call.quality_report", false, row)
        }
        RuntimeMutation::Speaker(input) => {
            let level = input.speaking_level.unwrap_or(0).clamp(0, 100);
            let speaking = input.is_speaking.unwrap_or(level >= state.config.call_speaker_level_threshold);
            let level_db = level as i16;
            let row = sqlx::query_scalar::<_, Value>("UPDATE chat_callparticipant SET speaking_level=$3,is_speaking=$4,last_spoke_at=CASE WHEN $4 THEN NOW() ELSE last_spoke_at END,last_seen_signal_at=NOW(),updated_at=NOW() WHERE call_id=$1 AND user_id=$2 RETURNING jsonb_build_object('speaking_level',speaking_level,'is_speaking',is_speaking,'last_spoke_at',last_spoke_at,'updated_at',updated_at)")
                .bind(call_id).bind(actor).bind(level_db).bind(speaking).persistent(false).fetch_one(&mut *tx).await?;
            ("call.speaker_state", true, row)
        }
    };
    touch_call(&mut tx, call_id).await?;
    let orchestration = build_orchestration_tx(&mut tx, call_id, state.config.call_grid_layout_threshold).await?;
    tx.commit().await?;
    let map = response.as_object_mut().context("runtime response is not an object")?;
    map.insert("call_id".into(), Value::String(call_id.to_string()));
    map.insert("conversation_id".into(), Value::String(conversation_id.to_string()));
    map.insert("user_id".into(), Value::String(actor.to_string()));
    map.insert("network_recommendation".into(), orchestration.get("network_recommendation").cloned().unwrap_or_else(|| json!({})));
    map.insert("orchestration".into(), orchestration.clone());
    if event == "call.quality_report" {
        map.insert("recovery_plan".into(), orchestration.get("recovery_plan").cloned().unwrap_or_else(|| json!({})));
        map.insert("aggregate_quality".into(), orchestration.get("aggregate_quality").cloned().unwrap_or_else(|| json!({})));
    }
    Ok((event, response, orchestration_event))
}

pub async fn orchestration(
    State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap,
) -> impl IntoResponse {
    if let Some(response) = require_runtime(&state) { return response; }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match orchestration_inner(&state, call_id, &identity).await {
        Ok(payload) => Json(payload).into_response(),
        Err(error) => runtime_error(error),
    }
}

async fn orchestration_inner(state: &AppState, call_id: Uuid, identity: &CommandIdentity) -> Result<Value> {
    let pool = state.database.pool.as_ref().context("SQLx call runtime backend is disabled")?;
    let mut tx = pool.begin().await?;
    let actor = actor_id(&mut tx, identity).await?;
    let exists = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_callparticipant p JOIN chat_callsession c ON c.id=p.call_id JOIN chat_conversationparticipant cp ON cp.conversation_id=c.conversation_id AND cp.user_id=p.user_id WHERE p.call_id=$1 AND p.user_id=$2 AND cp.left_at IS NULL AND cp.is_blocked=FALSE)")
        .bind(call_id).bind(actor).persistent(false).fetch_one(&mut *tx).await?;
    if !exists { anyhow::bail!("actor is not a call participant"); }
    let mut payload = build_orchestration_tx(&mut tx, call_id, state.config.call_grid_layout_threshold).await?;
    tx.commit().await?;
    let signals = state.call_signals.pop_all(call_id, actor);
    if !signals.is_empty() {
        payload.as_object_mut().context("orchestration is not an object")?.insert("signals".into(), Value::Array(signals));
    }
    Ok(payload)
}

pub async fn diagnostics(
    State(state): State<Arc<AppState>>, Path(call_id): Path<Uuid>, headers: HeaderMap,
) -> impl IntoResponse {
    if let Some(response) = require_runtime(&state) { return response; }
    let identity = match authenticate(&state, &headers) { Ok(value) => value, Err(response) => return response };
    match diagnostics_inner(&state, call_id, &identity).await {
        Ok(payload) => Json(payload).into_response(),
        Err(error) => runtime_error(error),
    }
}

async fn diagnostics_inner(state: &AppState, call_id: Uuid, identity: &CommandIdentity) -> Result<Value> {
    let pool = state.database.pool.as_ref().context("SQLx call runtime backend is disabled")?;
    let mut tx = pool.begin().await?;
    let actor = actor_id(&mut tx, identity).await?;
    let exists = sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM chat_callparticipant p JOIN chat_callsession c ON c.id=p.call_id JOIN chat_conversationparticipant cp ON cp.conversation_id=c.conversation_id AND cp.user_id=p.user_id WHERE p.call_id=$1 AND p.user_id=$2 AND cp.left_at IS NULL AND cp.is_blocked=FALSE)")
        .bind(call_id).bind(actor).persistent(false).fetch_one(&mut *tx).await?;
    if !exists { anyhow::bail!("actor is not a call participant"); }
    let snapshot = call_snapshot_tx(&mut tx, call_id).await?.context("call was not found")?;
    let orchestration = build_orchestration_from_snapshot(&snapshot, state.config.call_grid_layout_threshold)?;
    tx.commit().await?;
    let participants = snapshot.get("participants").and_then(Value::as_array).cloned().unwrap_or_default();
    let stale_seconds = state.config.call_stale_participant_seconds;
    let stale_user_ids = participants.iter().filter_map(|participant| {
        if participant.get("state").and_then(Value::as_str) != Some("joined") { return None; }
        let age = participant.get("heartbeat_age_seconds").and_then(Value::as_i64)?;
        (age > stale_seconds).then(|| participant.get("user_id").and_then(Value::as_str).unwrap_or_default().to_owned())
    }).collect::<Vec<_>>();
    let joined_count = participants.iter().filter(|p| p.get("state").and_then(Value::as_str)==Some("joined")).count();
    let active_count = participants.iter().filter(|p| {
        matches!(p.get("state").and_then(Value::as_str), Some("joined") | Some("ringing"))
    }).count();
    Ok(json!({
        "call_id": call_id,
        "status": snapshot.get("status"),
        "participant_count": participants.len(),
        "joined_count": joined_count,
        "active_count": active_count,
        "stale_participant_user_ids": stale_user_ids,
        "network_recommendation": orchestration.get("network_recommendation"),
        "recovery_plan": orchestration.get("recovery_plan"),
        "aggregate_quality": orchestration.get("aggregate_quality"),
        "orchestration": orchestration,
        "last_signal_at": snapshot.get("last_signal_at"),
        "participants": participants,
    }))
}

async fn build_orchestration_tx(tx: &mut Transaction<'_, Postgres>, call_id: Uuid, grid_threshold: i64) -> Result<Value> {
    let snapshot = call_snapshot_tx(tx, call_id).await?.context("call was not found")?;
    build_orchestration_from_snapshot(&snapshot, grid_threshold)
}

async fn call_snapshot_tx(tx: &mut Transaction<'_, Postgres>, call_id: Uuid) -> Result<Option<Value>> {
    Ok(sqlx::query_scalar::<_, Value>(r#"
SELECT jsonb_build_object(
 'call_id',c.id::text,'conversation_id',c.conversation_id::text,'status',c.status,'last_signal_at',c.last_signal_at,
 'participants',COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'user_id',p.user_id::text,'state',p.state,'network_quality',p.network_quality,'preferred_video_quality',p.preferred_video_quality,
   'audio_enabled',p.audio_enabled,'video_enabled',p.video_enabled,'is_on_hold',p.is_on_hold,'reconnecting',p.reconnecting,
   'connection_state',p.connection_state,'audio_route',p.audio_route,'screen_share_enabled',p.screen_share_enabled,
   'screen_share_started_at',p.screen_share_started_at,'raised_hand_at',p.raised_hand_at,'is_speaking',p.is_speaking,
   'speaking_level',p.speaking_level,'last_spoke_at',p.last_spoke_at,'invited_at',p.invited_at,'joined_at',p.joined_at,
   'last_heartbeat_at',p.last_heartbeat_at,'last_seen_signal_at',p.last_seen_signal_at,
   'heartbeat_age_seconds',CASE WHEN p.last_heartbeat_at IS NULL THEN NULL ELSE EXTRACT(EPOCH FROM (NOW()-p.last_heartbeat_at))::bigint END,
   'packet_loss_pct',p.packet_loss_pct,'jitter_ms',p.jitter_ms,'round_trip_time_ms',p.round_trip_time_ms,
   'bitrate_kbps',p.bitrate_kbps,'frame_rate',p.frame_rate,'quality_score',p.quality_score,'quality_alert',p.quality_alert,
   'diagnostics',p.diagnostics
 ) ORDER BY p.invited_at) FROM chat_callparticipant p WHERE p.call_id=c.id),'[]'::jsonb)
) FROM chat_callsession c WHERE c.id=$1
"#).bind(call_id).persistent(false).fetch_optional(&mut **tx).await?)
}

fn build_orchestration_from_snapshot(snapshot: &Value, grid_threshold: i64) -> Result<Value> {
    let participants = snapshot.get("participants").and_then(Value::as_array).cloned().unwrap_or_default();
    let joined = participants.iter().filter(|p| p.get("state").and_then(Value::as_str)==Some("joined")).collect::<Vec<_>>();
    let speaking = joined.iter().copied().filter(|p| p.get("is_speaking").and_then(Value::as_bool).unwrap_or(false)).collect::<Vec<_>>();
    let active_speaker = speaking.iter().copied().max_by(|left, right| {
        let left_level = left.get("speaking_level").and_then(Value::as_i64).unwrap_or(0);
        let right_level = right.get("speaking_level").and_then(Value::as_i64).unwrap_or(0);
        let left_spoke = left.get("last_spoke_at").and_then(Value::as_str).unwrap_or("");
        let right_spoke = right.get("last_spoke_at").and_then(Value::as_str).unwrap_or("");
        left_level.cmp(&right_level).then_with(|| left_spoke.cmp(right_spoke))
    });
    let screen_sharers = joined.iter().copied().filter(|p| p.get("screen_share_enabled").and_then(Value::as_bool).unwrap_or(false)).collect::<Vec<_>>();
    let recommendation = network_recommendation(snapshot.get("status").and_then(Value::as_str).unwrap_or(""), &joined);
    let mode = recommendation.get("mode").and_then(Value::as_str).unwrap_or("standard");
    let layout_mode = if mode == "audio_only" { "audio_only" }
        else if !screen_sharers.is_empty() { "presentation" }
        else if joined.len() <= 2 { "focused" }
        else if active_speaker.is_some() && (joined.len() as i64) < grid_threshold { "speaker_focus" }
        else { "grid" };
    let (quality, max_streams, audio_only) = match mode {
        "audio_only" => ("off", 0, true),
        "low_bandwidth_video" => ("low", if joined.len() > 2 {2} else {1}, false),
        "reconnect" => ("low", 1, false),
        _ => ("high", (joined.len().max(1) as i64).min(grid_threshold), false),
    };
    let recovery = recovery_plan(&participants);
    let aggregate = quality_summary(&participants);
    let raised = joined.iter().copied().filter(|p| !p.get("raised_hand_at").unwrap_or(&Value::Null).is_null()).map(|p| p.get("user_id").and_then(Value::as_str).unwrap_or_default().to_owned()).collect::<Vec<_>>();
    Ok(json!({
        "call_id": snapshot.get("call_id"),
        "conversation_id": snapshot.get("conversation_id"),
        "active_speaker_user_id": active_speaker.and_then(|p|p.get("user_id")).cloned(),
        "primary_content_user_id": screen_sharers.first().and_then(|p|p.get("user_id")).cloned(),
        "layout_mode": layout_mode,
        "network_recommendation": recommendation,
        "recommended_video_quality": quality,
        "recommended_max_video_streams": max_streams,
        "recommend_audio_only": audio_only,
        "recovery_plan": recovery,
        "aggregate_quality": aggregate,
        "participant_speaking_user_ids": speaking.iter().filter_map(|p|p.get("user_id").and_then(Value::as_str)).collect::<Vec<_>>(),
        "raised_hand_user_ids": raised,
        "generated_at": OffsetDateTime::now_utc().format(&Rfc3339).unwrap_or_default(),
        "participants": participants.iter().map(orchestration_participant).collect::<Vec<_>>(),
    }))
}

fn orchestration_participant(p: &Value) -> Value {
    json!({
        "user_id":p.get("user_id"),"state":p.get("state"),"network_quality":p.get("network_quality"),
        "is_speaking":p.get("is_speaking"),"speaking_level":p.get("speaking_level"),"video_enabled":p.get("video_enabled"),
        "audio_enabled":p.get("audio_enabled"),"preferred_video_quality":p.get("preferred_video_quality"),
        "connection_state":p.get("connection_state"),"audio_route":p.get("audio_route"),
        "screen_share_enabled":p.get("screen_share_enabled"),"raised_hand_at":p.get("raised_hand_at")
    })
}

fn network_recommendation(status: &str, joined: &[&Value]) -> Value {
    if matches!(status, "initiated" | "ringing") { return json!({"mode":"standard","reason":"awaiting_answer"}); }
    if joined.is_empty() { return json!({"mode":"standard","reason":"awaiting_participants"}); }
    if joined.iter().any(|p| p.get("network_quality").and_then(Value::as_str)==Some("offline")) { return json!({"mode":"reconnect","reason":"participant_offline"}); }
    let level = |quality: &str| match quality { "excellent"=>4,"good"=>3,"fair"=>2,"poor"=>1,_=>0 };
    let known = joined.iter().filter_map(|p| p.get("network_quality").and_then(Value::as_str).map(level)).filter(|v|*v>0).collect::<Vec<_>>();
    if known.is_empty() { return json!({"mode":"standard","reason":"awaiting_quality_signal"}); }
    match *known.iter().min().unwrap_or(&4) {
        1 if joined.iter().any(|p| p.get("network_quality").and_then(Value::as_str)==Some("poor") && !p.get("video_enabled").and_then(Value::as_bool).unwrap_or(true)) => json!({"mode":"audio_only","reason":"poor_network_video_disabled"}),
        1 if joined.len() <= 2 => json!({"mode":"low_bandwidth_video","reason":"poor_network_1to1"}),
        1 => json!({"mode":"audio_only","reason":"poor_network"}),
        2 => json!({"mode":"low_bandwidth_video","reason":"fair_network"}),
        _ => json!({"mode":"standard","reason":"healthy_network"}),
    }
}

fn recovery_plan(participants: &[Value]) -> Value {
    if participants.is_empty() { return json!({"action":"keep","reason":"no_participants","target_quality":"high","max_remote_streams":1,"audio_only":false}); }
    let worst = participants.iter().filter_map(|p|p.get("quality_score").and_then(Value::as_i64)).min().unwrap_or(100);
    let offline = participants.iter().filter(|p|p.get("network_quality").and_then(Value::as_str)==Some("offline")).count();
    let poor = participants.iter().filter(|p|matches!(p.get("network_quality").and_then(Value::as_str), Some("poor") | Some("offline"))).count();
    let joined = participants.iter().filter(|p|p.get("state").and_then(Value::as_str)==Some("joined")).count();
    if offline > 0 { json!({"action":"restart_ice","reason":"participant_offline","target_quality":"low","max_remote_streams":1,"audio_only":joined>2}) }
    else if worst < 25 { json!({"action":"audio_only","reason":"critical_quality","target_quality":"off","max_remote_streams":0,"audio_only":true}) }
    else if worst < 45 || poor > 0 { json!({"action":"reduce_video","reason":"degraded_quality","target_quality":"low","max_remote_streams":if joined>2{1}else{joined.max(1)},"audio_only":false}) }
    else if worst < 70 { json!({"action":"limit_streams","reason":"fair_quality","target_quality":"medium","max_remote_streams":joined.max(1).min(2),"audio_only":false}) }
    else { json!({"action":"keep","reason":"healthy_quality","target_quality":"high","max_remote_streams":joined.max(1).min(4),"audio_only":false}) }
}

fn quality_summary(participants: &[Value]) -> Value {
    if participants.is_empty() { return json!({"min_score":100,"avg_score":100,"degraded_user_ids":[],"offline_user_ids":[]}); }
    let scores = participants.iter().map(|p|p.get("quality_score").and_then(Value::as_i64).unwrap_or(0)).collect::<Vec<_>>();
    let avg = (scores.iter().sum::<i64>() as f64 / scores.len() as f64).round() as i64;
    json!({
        "min_score":scores.iter().min().copied().unwrap_or(100),"avg_score":avg,
        "degraded_user_ids":participants.iter().filter(|p|p.get("quality_score").and_then(Value::as_i64).unwrap_or(0)<70).filter_map(|p|p.get("user_id").and_then(Value::as_str)).collect::<Vec<_>>(),
        "offline_user_ids":participants.iter().filter(|p|p.get("network_quality").and_then(Value::as_str)==Some("offline")).filter_map(|p|p.get("user_id").and_then(Value::as_str)).collect::<Vec<_>>()
    })
}

fn compute_quality_score(packet_loss: Option<f64>, jitter: Option<i64>, rtt: Option<i64>, bitrate: Option<i64>, frame_rate: Option<i64>, network: Option<&str>) -> i32 {
    let mut score = 100.0;
    if let Some(value)=packet_loss { score -= (value*2.5).min(45.0); }
    if let Some(value)=jitter { score -= (((value as f64)-20.0).max(0.0)*0.4).min(20.0); }
    if let Some(value)=rtt { score -= (((value as f64)-120.0).max(0.0)*0.06).min(18.0); }
    if let Some(value)=bitrate.filter(|v|*v>0) { score -= if value<120 {15.0} else if value<300 {8.0} else {0.0}; }
    if frame_rate.is_some_and(|value| value>0 && value<12) { score -= 8.0; }
    score -= match network { Some("good")=>3.0,Some("fair")=>10.0,Some("poor")=>22.0,Some("offline")=>40.0,Some("unknown")=>4.0,_=>0.0 };
    score.round().clamp(0.0,100.0) as i32
}

fn quality_alert(score: i32) -> &'static str {
    if score<25 {"critical"} else if score<45 {"poor"} else if score<70 {"degraded"} else {""}
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{build_orchestration_from_snapshot, compute_quality_score};

    #[test]
    fn quality_score_degrades_for_bad_network_metrics() {
        let healthy = compute_quality_score(Some(0.2), Some(8), Some(55), Some(1_200), Some(30), Some("excellent"));
        let poor = compute_quality_score(Some(18.0), Some(140), Some(1_100), Some(90), Some(8), Some("poor"));
        assert!(healthy >= 90);
        assert!(poor < 45);
    }

    #[test]
    fn orchestration_recommends_audio_only_for_group_with_poor_network() {
        let snapshot = json!({
            "call_id":"00000000-0000-0000-0000-000000000001",
            "conversation_id":"00000000-0000-0000-0000-000000000002",
            "status":"ongoing",
            "participants":[
                {"user_id":"1","state":"joined","network_quality":"good","video_enabled":true,"audio_enabled":true,"quality_score":90},
                {"user_id":"2","state":"joined","network_quality":"poor","video_enabled":true,"audio_enabled":true,"quality_score":35},
                {"user_id":"3","state":"joined","network_quality":"good","video_enabled":true,"audio_enabled":true,"quality_score":88}
            ]
        });
        let orchestration = build_orchestration_from_snapshot(&snapshot, 4).expect("valid orchestration");
        assert_eq!(orchestration["network_recommendation"]["mode"], "audio_only");
        assert_eq!(orchestration["recommend_audio_only"], true);
        assert_eq!(orchestration["recommended_max_video_streams"], 0);
    }
}
