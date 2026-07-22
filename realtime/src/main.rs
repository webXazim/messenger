#![recursion_limit = "256"]

mod admission;
mod auth;
mod attachments;
mod command_auth;
mod command_delivery;
mod commands;
mod conversation_commands;
mod message_interactions;
mod message_mutations;
mod call_commands;
mod call_runtime;
mod call_signal_store;
mod chat_reads;
mod config;
mod database;
mod nats_connection;
mod nats_jetstream;
mod nats_durable_publish;
mod nats_core;
mod nats_probe;
mod ownership;
mod protocol;
mod presence;
mod registry;
mod session_limit;
mod state;
mod websocket;
mod support_data;
mod support_signal_store;

use std::{
    sync::{
        atomic::Ordering,
        Arc,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use anyhow::Result;
use axum::{
    extract::{DefaultBodyLimit, State},
    middleware,
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Json, Router,
};
use serde_json::json;
use tokio::{net::TcpListener, time};
use tower_http::trace::TraceLayer;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

use crate::{config::Config, state::AppState};

#[tokio::main]
async fn main() -> Result<()> {
    init_tracing();
    let config = Config::from_env()?;
    let bind_addr = config.bind_addr;
    let state = AppState::new(config)?;

    tracing::info!(
        durable_backend = state.config.durable_backend.as_str(),
        outbox_publisher = state.config.outbox_publisher_backend.as_str(),
        ephemeral_backend = state.config.ephemeral_backend.as_str(),
        presence_backend = state.config.presence_backend.as_str(),
        "realtime transport backends selected"
    );
    let nats_probe_task = tokio::spawn(nats_probe::run(state.clone()));
    let ephemeral_task = tokio::spawn(nats_core::run(state.clone()));
    let ownership_task = tokio::spawn(ownership::run(state.clone()));
    let durable_task = tokio::spawn(nats_jetstream::run(state.clone()));
    let app = Router::new()
        .route("/health/live", get(live))
        .route("/health/ready", get(ready))
        .route("/internal/stats", get(stats))
        .route("/internal/metrics", get(metrics))
        .route("/ws", get(websocket::authenticated_websocket_handler))
        .route("/api/v1/chat-fast/conversations/", get(chat_reads::list_conversations).post(conversation_commands::create_conversation))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/", get(chat_reads::get_conversation))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/messages/", get(chat_reads::list_messages).post(commands::send_message))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/attachment-messages/", axum::routing::post(attachments::send_attachment_message))
        .route("/api/v1/chat-fast/attachments/{attachment_id}/", get(attachments::get_attachment))
        .route("/api/v1/chat-fast/attachments/{attachment_id}/media-token/", axum::routing::post(attachments::create_media_token))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/mark-delivered/", axum::routing::post(message_interactions::mark_delivered))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/mark-read/", axum::routing::post(message_interactions::mark_read))
        .route("/api/v1/chat-fast/messages/{message_id}/", get(chat_reads::get_message))
        .route("/api/v1/chat-fast/messages/{message_id}/context/", get(chat_reads::message_context))
        .route("/api/v1/chat-fast/messages/{message_id}/reactions/", axum::routing::post(message_interactions::add_reaction).delete(message_interactions::remove_reaction))
        .route("/api/v1/chat-fast/messages/{message_id}/manage/", axum::routing::patch(message_mutations::edit_message).delete(message_mutations::delete_message))
        .route("/api/v1/chat-fast/messages/{message_id}/restore/", axum::routing::post(message_mutations::restore_message))
        .route("/api/v1/chat-fast/messages/{message_id}/retry/", axum::routing::post(message_mutations::retry_message))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/media/", get(chat_reads::list_media))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/draft/", get(conversation_commands::get_draft).patch(conversation_commands::save_draft).delete(conversation_commands::delete_draft))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/mute/", axum::routing::post(conversation_commands::toggle_mute))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/archive/", axum::routing::post(conversation_commands::toggle_archive))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/pin/", axum::routing::post(conversation_commands::toggle_pin))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/participants/", axum::routing::post(conversation_commands::add_participants))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/participants/{user_id}/", axum::routing::delete(conversation_commands::remove_participant))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/participants/{user_id}/role/", axum::routing::patch(conversation_commands::update_participant_role))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/participants/{user_id}/mute/", axum::routing::post(conversation_commands::mute_participant))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/participants/{user_id}/ban/", axum::routing::post(conversation_commands::ban_participant).delete(conversation_commands::unban_participant))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/transfer-ownership/", axum::routing::post(conversation_commands::transfer_ownership))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/leave/", axum::routing::post(conversation_commands::leave_conversation))
        .route("/api/v1/chat-fast/blocks/", axum::routing::post(conversation_commands::block_user))
        .route("/api/v1/chat-fast/blocks/{user_id}/", axum::routing::delete(conversation_commands::unblock_user))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/calls/start/", axum::routing::post(call_commands::start_call))
        .route("/api/v1/chat-fast/calls/recent/", get(call_commands::recent_calls))
        .route("/api/v1/chat-fast/calls/{call_id}/", get(call_commands::get_call))
        .route("/api/v1/chat-fast/calls/{call_id}/accept/", axum::routing::post(call_commands::accept_call))
        .route("/api/v1/chat-fast/calls/{call_id}/decline/", axum::routing::post(call_commands::decline_call))
        .route("/api/v1/chat-fast/calls/{call_id}/end/", axum::routing::post(call_commands::end_call))
        .route("/api/v1/chat-fast/calls/{call_id}/signal/", axum::routing::post(call_runtime::send_signal))
        .route("/api/v1/chat-fast/calls/{call_id}/heartbeat/", axum::routing::post(call_runtime::heartbeat))
        .route("/api/v1/chat-fast/calls/{call_id}/media-state/", axum::routing::post(call_runtime::media_state))
        .route("/api/v1/chat-fast/calls/{call_id}/quality-report/", axum::routing::post(call_runtime::quality_report))
        .route("/api/v1/chat-fast/calls/{call_id}/speaker-state/", axum::routing::post(call_runtime::speaker_state))
        .route("/api/v1/chat-fast/calls/{call_id}/orchestration/", get(call_runtime::orchestration))
        .route("/api/v1/chat-fast/calls/{call_id}/diagnostics/", get(call_runtime::diagnostics))
        .route("/api/v1/support-fast/conversations/", get(support_data::list_conversations))
        .route("/api/v1/support-fast/unread-summary/", get(support_data::unread_summary))
        .route("/api/v1/support-fast/conversations/{conversation_id}/", get(support_data::get_conversation))
        .route("/api/v1/support-fast/conversations/{conversation_id}/messages/", get(support_data::list_team_messages).post(support_data::send_team_message))
        .route("/api/v1/support-fast/conversations/{conversation_id}/delivered/", axum::routing::post(support_data::team_delivered))
        .route("/api/v1/support-fast/conversations/{conversation_id}/read/", axum::routing::post(support_data::team_read))
        .route("/api/v1/support-fast/conversations/{conversation_id}/claim/", axum::routing::post(support_data::claim_conversation))
        .route("/api/v1/support-fast/calls/active/", get(support_data::team_active_call))
        .route("/api/v1/support-fast/conversations/{conversation_id}/calls/", get(support_data::team_conversation_call).post(support_data::start_team_call))
        .route("/api/v1/support-fast/calls/{call_id}/", get(support_data::get_team_call))
        .route("/api/v1/support-fast/calls/{call_id}/accept/", axum::routing::post(support_data::accept_team_call))
        .route("/api/v1/support-fast/calls/{call_id}/decline/", axum::routing::post(support_data::decline_team_call))
        .route("/api/v1/support-fast/calls/{call_id}/end/", axum::routing::post(support_data::end_team_call))
        .route("/api/v1/support-fast/calls/{call_id}/signals/", get(support_data::list_team_signals).post(support_data::send_team_signal))
        .route("/api/v1/support-fast/calls/{call_id}/media-state/", axum::routing::patch(support_data::team_media_state))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/messages/", get(support_data::list_widget_messages).post(support_data::send_widget_message).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/delivered/", axum::routing::post(support_data::widget_delivered).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/read/", axum::routing::post(support_data::widget_read).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/calls/active/", get(support_data::widget_active_call).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/calls/", axum::routing::post(support_data::start_widget_call).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/calls/{call_id}/", get(support_data::get_widget_call).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/calls/{call_id}/accept/", axum::routing::post(support_data::accept_widget_call).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/calls/{call_id}/decline/", axum::routing::post(support_data::decline_widget_call).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/calls/{call_id}/end/", axum::routing::post(support_data::end_widget_call).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/calls/{call_id}/signals/", get(support_data::list_widget_signals).post(support_data::send_widget_signal).options(support_data::widget_options))
        .route("/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/calls/{call_id}/media-state/", axum::routing::patch(support_data::widget_media_state).options(support_data::widget_options));
    let app = if state.config.internal_test_enabled {
        app.route("/internal/ws-test", get(websocket::test_websocket_handler))
    } else {
        app
    }
    .layer(DefaultBodyLimit::max(state.config.http_max_body_bytes))
    .layer(middleware::from_fn_with_state(state.clone(), admission::guard))
    .layer(TraceLayer::new_for_http())
    .with_state(state.clone());

    let listener = TcpListener::bind(bind_addr).await?;
    tracing::info!(%bind_addr, "Axum realtime foundation listening");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal(state.clone()))
        .await?;

    state.shutdown.cancel();
    let _ = time::timeout(Duration::from_secs(5), durable_task).await;
    let _ = time::timeout(Duration::from_secs(5), nats_probe_task).await;
    let _ = time::timeout(Duration::from_secs(5), ephemeral_task).await;
    let _ = time::timeout(Duration::from_secs(5), ownership_task).await;
    Ok(())
}

async fn live() -> impl IntoResponse {
    (StatusCode::OK, Json(json!({"status": "live"})))
}

async fn ready(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let stream_ready = state.stream_ready.load(Ordering::Acquire);
    let auth_ready = match state.auth.as_ref() {
        Some(auth) => auth.check_storage().await,
        None => !state.config.auth_enabled,
    };
    let presence_ready = state.presence.check().await;
    let database_ready = state.database.check().await;
    let ephemeral_ready = state.ephemeral_ready.load(Ordering::Acquire);
    let ownership_ready = state.ownership_ready.load(Ordering::Acquire);
    let direct_outbox_ready = state.config.outbox_publisher_backend.as_str() != "axum"
        || (stream_ready && database_ready);
    let database_pool = state.database.pool_snapshot();
    let http_admission = state.http_admission.snapshot();
    let ready = stream_ready
        && auth_ready
        && presence_ready
        && ephemeral_ready
        && ownership_ready
        && database_ready
        && direct_outbox_ready;
    let payload = json!({
        "status": if ready { "ready" } else { "not_ready" },
        "durable_backend": state.config.durable_backend.as_str(),
        "outbox_publisher": state.config.outbox_publisher_backend.as_str(),
        "direct_outbox_ready": direct_outbox_ready,
        "ephemeral_backend": state.config.ephemeral_backend.as_str(),
        "presence_backend": state.config.presence_backend.as_str(),
        "durable_transport": stream_ready,
        "ephemeral_transport": ephemeral_ready,
        "connection_ownership_backend": state.config.connection_ownership_backend.as_str(),
        "connection_ownership": ownership_ready,
        "jetstream": stream_ready && state.config.durable_backend.as_str() == "nats",
        "auth": auth_ready,
        "presence": presence_ready,
        "chat_read_backend": state.database.backend_name(),
        "chat_command_backend": state.config.chat_command_backend.as_str(),
        "chat_interaction_backend": state.config.chat_interaction_backend.as_str(),
        "chat_message_mutation_backend": state.config.chat_message_mutation_backend.as_str(),
        "chat_call_runtime_backend": state.config.chat_call_runtime_backend.as_str(),
        "chat_attachment_backend": state.config.chat_attachment_backend.as_str(),
        "chat_conversation_command_backend": state.config.chat_conversation_command_backend.as_str(),
        "support_data_backend": state.config.support_data_backend.as_str(),
        "sqlx_enabled": state.database.enabled(),
        "sqlx_database": database_ready,
        "sqlx_pool": database_pool,
        "http_admission": http_admission,
        "nats_probe_enabled": state.config.nats_probe_enabled,
        "nats_probe_ready": state.nats_ready.load(Ordering::Acquire),
    });
    if ready {
        (StatusCode::OK, Json(payload))
    } else {
        (StatusCode::SERVICE_UNAVAILABLE, Json(payload))
    }
}

async fn stats(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let database_pool = state.database.pool_snapshot();
    let http_admission = state.http_admission.snapshot();
    let queues = state.registry.queue_snapshot();
    Json(json!({
        "uptime_seconds": now.saturating_sub(state.started_at_epoch),
        "connections": state.registry.connection_count(),
        "audiences": state.registry.audience_count(),
        "available_connection_slots": state.connection_slots.available_permits(),
        "websocket_high_queued": queues.high_queued,
        "websocket_high_capacity": queues.high_capacity,
        "websocket_low_queued": queues.low_queued,
        "websocket_low_capacity": queues.low_capacity,
        "http_read_requests": http_admission.read_requests,
        "http_write_requests": http_admission.write_requests,
        "http_rejected_read": http_admission.rejected_read,
        "http_rejected_write": http_admission.rejected_write,
        "http_timed_out": http_admission.timed_out,
        "http_server_errors": http_admission.server_errors,
        "sqlx_pool_size": database_pool.size,
        "sqlx_pool_idle": database_pool.idle,
        "sqlx_pool_in_use": database_pool.in_use,
        "sqlx_pool_max": database_pool.max_connections,
        "websocket_queues": queues,
        "http_admission": http_admission,
        "sqlx_pool": database_pool,
        "stream_ready": state.stream_ready.load(Ordering::Acquire),
        "ephemeral_ready": state.ephemeral_ready.load(Ordering::Acquire),
        "ephemeral_events": state.ephemeral_events.load(Ordering::Relaxed),
        "ephemeral_published": state.ephemeral_published.load(Ordering::Relaxed),
        "ephemeral_errors": state.ephemeral_errors.load(Ordering::Relaxed),
        "ephemeral_reconnects": state.ephemeral_reconnects.load(Ordering::Relaxed),
        "connection_ownership_backend": state.config.connection_ownership_backend.as_str(),
        "ownership_ready": state.ownership_ready.load(Ordering::Acquire),
        "ownership_snapshots_published": state.ownership_snapshots_published.load(Ordering::Relaxed),
        "ownership_snapshots_received": state.ownership_snapshots_received.load(Ordering::Relaxed),
        "ownership_reconnects": state.ownership_reconnects.load(Ordering::Relaxed),
        "targeted_deliveries_published": state.targeted_deliveries_published.load(Ordering::Relaxed),
        "targeted_deliveries_received": state.targeted_deliveries_received.load(Ordering::Relaxed),
        "durable_backend": state.config.durable_backend.as_str(),
        "outbox_publisher": state.config.outbox_publisher_backend.as_str(),
        "direct_outbox_attempts": state.direct_outbox_attempts.load(Ordering::Relaxed),
        "direct_outbox_published": state.direct_outbox_published.load(Ordering::Relaxed),
        "direct_outbox_duplicates": state.direct_outbox_duplicates.load(Ordering::Relaxed),
        "direct_outbox_failures": state.direct_outbox_failures.load(Ordering::Relaxed),
        "direct_outbox_mark_failures": state.direct_outbox_mark_failures.load(Ordering::Relaxed),
        "ephemeral_backend": state.config.ephemeral_backend.as_str(),
        "presence_backend": state.presence.backend_name(),
        "chat_read_backend": state.database.backend_name(),
        "chat_command_backend": state.config.chat_command_backend.as_str(),
        "chat_interaction_backend": state.config.chat_interaction_backend.as_str(),
        "chat_message_mutation_backend": state.config.chat_message_mutation_backend.as_str(),
        "chat_call_runtime_backend": state.config.chat_call_runtime_backend.as_str(),
        "chat_attachment_backend": state.config.chat_attachment_backend.as_str(),
        "chat_conversation_command_backend": state.config.chat_conversation_command_backend.as_str(),
        "support_data_backend": state.config.support_data_backend.as_str(),
        "sqlx_enabled": state.database.enabled(),
        "local_presence_user_connections": state.presence.local_user_connection_count(),
        "local_presence_support_connections": state.presence.local_support_connection_count(),
        "nats_probe_enabled": state.config.nats_probe_enabled,
        "nats_probe_ready": state.nats_ready.load(Ordering::Acquire),
        "auth_enabled": state.config.auth_enabled,
        "auth_configured": state.auth.is_some(),
        "internal_test_enabled": state.config.internal_test_enabled,
        "stream_events": state.stream_events.load(Ordering::Relaxed),
        "stream_acks": state.stream_acks.load(Ordering::Relaxed),
        "stream_errors": state.stream_errors.load(Ordering::Relaxed),
        "stream_reconnects": state.stream_reconnects.load(Ordering::Relaxed),
        "malformed_stream_events": state.malformed_stream_events.load(Ordering::Relaxed),
        "connections_accepted": state.connections_accepted.load(Ordering::Relaxed),
        "connections_rejected": state.connections_rejected.load(Ordering::Relaxed),
        "rate_limited_events": state.rate_limited_events.load(Ordering::Relaxed),
        "delivered": state.registry.stats.delivered(),
        "dropped_ephemeral": state.registry.stats.dropped_ephemeral(),
        "disconnected_slow": state.registry.stats.disconnected_slow(),
    }))
}

async fn metrics(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let uptime = now.saturating_sub(state.started_at_epoch);
    let pool = state.database.pool_snapshot();
    let http = state.http_admission.snapshot();
    let queues = state.registry.queue_snapshot();
    let http_count = http.read_requests.saturating_add(http.write_requests);

    let mut body = String::new();
    macro_rules! metric {
        ($name:expr, $value:expr) => {
            body.push_str($name);
            body.push(' ');
            body.push_str(&$value.to_string());
            body.push('\n');
        };
    }

    body.push_str("# TYPE realtime_uptime_seconds gauge\n");
    metric!("realtime_uptime_seconds", uptime);
    body.push_str("# TYPE realtime_connections gauge\n");
    metric!("realtime_connections", state.registry.connection_count());
    metric!("realtime_audiences", state.registry.audience_count());
    metric!("realtime_available_connection_slots", state.connection_slots.available_permits());
    metric!("realtime_websocket_high_queue_messages", queues.high_queued);
    metric!("realtime_websocket_high_queue_capacity", queues.high_capacity);
    metric!("realtime_websocket_low_queue_messages", queues.low_queued);
    metric!("realtime_websocket_low_queue_capacity", queues.low_capacity);

    body.push_str("# TYPE realtime_http_in_flight gauge\n");
    metric!("realtime_http_in_flight", http.in_flight);
    metric!("realtime_http_max_in_flight", http.max_in_flight);
    metric!("realtime_http_read_limit", http.read_limit);
    metric!("realtime_http_write_limit", http.write_limit);
    metric!("realtime_http_available_read_slots", http.available_read);
    metric!("realtime_http_available_write_slots", http.available_write);
    body.push_str("# TYPE realtime_http_requests_total counter\n");
    metric!("realtime_http_read_requests_total", http.read_requests);
    metric!("realtime_http_write_requests_total", http.write_requests);
    metric!("realtime_http_rejected_read_total", http.rejected_read);
    metric!("realtime_http_rejected_write_total", http.rejected_write);
    metric!("realtime_http_timed_out_total", http.timed_out);
    metric!("realtime_http_server_errors_total", http.server_errors);
    for (upper, count) in &http.latency_buckets {
        body.push_str(&format!(
            "realtime_http_request_duration_ms_bucket{{le=\"{}\"}} {}\n",
            upper, count
        ));
    }
    body.push_str(&format!(
        "realtime_http_request_duration_ms_bucket{{le=\"+Inf\"}} {}\n",
        http_count
    ));
    metric!("realtime_http_request_duration_ms_sum", http.total_duration_ms);
    metric!("realtime_http_request_duration_ms_count", http_count);

    body.push_str("# TYPE realtime_sqlx_pool_connections gauge\n");
    metric!("realtime_sqlx_pool_connections", pool.size);
    metric!("realtime_sqlx_pool_idle_connections", pool.idle);
    metric!("realtime_sqlx_pool_in_use_connections", pool.in_use);
    metric!("realtime_sqlx_pool_min_connections", pool.min_connections);
    metric!("realtime_sqlx_pool_max_connections", pool.max_connections);

    body.push_str("# TYPE realtime_stream_ready gauge\n");
    metric!("realtime_stream_ready", u8::from(state.stream_ready.load(Ordering::Acquire)));
    body.push_str("# TYPE realtime_events_total counter\n");
    metric!("realtime_stream_events_total", state.stream_events.load(Ordering::Relaxed));
    metric!("realtime_stream_acks_total", state.stream_acks.load(Ordering::Relaxed));
    metric!("realtime_stream_errors_total", state.stream_errors.load(Ordering::Relaxed));
    metric!("realtime_stream_reconnects_total", state.stream_reconnects.load(Ordering::Relaxed));
    metric!("realtime_malformed_stream_events_total", state.malformed_stream_events.load(Ordering::Relaxed));
    metric!("realtime_direct_outbox_attempts_total", state.direct_outbox_attempts.load(Ordering::Relaxed));
    metric!("realtime_direct_outbox_published_total", state.direct_outbox_published.load(Ordering::Relaxed));
    metric!("realtime_direct_outbox_duplicates_total", state.direct_outbox_duplicates.load(Ordering::Relaxed));
    metric!("realtime_direct_outbox_failures_total", state.direct_outbox_failures.load(Ordering::Relaxed));
    metric!("realtime_direct_outbox_mark_failures_total", state.direct_outbox_mark_failures.load(Ordering::Relaxed));
    body.push_str("# TYPE realtime_ephemeral_ready gauge\n");
    metric!("realtime_ephemeral_ready", u8::from(state.ephemeral_ready.load(Ordering::Acquire)));
    metric!("realtime_ephemeral_events_total", state.ephemeral_events.load(Ordering::Relaxed));
    metric!("realtime_ephemeral_published_total", state.ephemeral_published.load(Ordering::Relaxed));
    metric!("realtime_ephemeral_errors_total", state.ephemeral_errors.load(Ordering::Relaxed));
    metric!("realtime_ephemeral_reconnects_total", state.ephemeral_reconnects.load(Ordering::Relaxed));
    metric!("realtime_connections_accepted_total", state.connections_accepted.load(Ordering::Relaxed));
    metric!("realtime_connections_rejected_total", state.connections_rejected.load(Ordering::Relaxed));
    metric!("realtime_rate_limited_events_total", state.rate_limited_events.load(Ordering::Relaxed));
    metric!("realtime_delivered_total", state.registry.stats.delivered());
    metric!("realtime_dropped_ephemeral_total", state.registry.stats.dropped_ephemeral());
    metric!("realtime_disconnected_slow_total", state.registry.stats.disconnected_slow());

    ([
        ("content-type", "text/plain; version=0.0.4; charset=utf-8"),
    ], body)
}

async fn shutdown_signal(state: Arc<AppState>) {
    let ctrl_c = async {
        if let Err(error) = tokio::signal::ctrl_c().await {
            tracing::error!(error = %error, "cannot install Ctrl+C handler");
        }
    };

    #[cfg(unix)]
    let terminate = async {
        use tokio::signal::unix::{signal, SignalKind};
        match signal(SignalKind::terminate()) {
            Ok(mut signal) => {
                signal.recv().await;
            }
            Err(error) => tracing::error!(error = %error, "cannot install SIGTERM handler"),
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {}
        _ = terminate => {}
    }
    tracing::info!("shutdown requested");
    state.shutdown.cancel();
}

fn init_tracing() {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| "crescentsphere_realtime=info,tower_http=info".into());
    tracing_subscriber::registry()
        .with(filter)
        .with(tracing_subscriber::fmt::layer().json())
        .init();
}
