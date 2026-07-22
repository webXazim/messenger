mod auth;
mod command_auth;
mod command_delivery;
mod commands;
mod call_commands;
mod config;
mod database;
mod nats_connection;
mod nats_jetstream;
mod nats_core;
mod nats_probe;
mod ownership;
mod protocol;
mod presence;
mod registry;
mod session_limit;
mod state;
mod websocket;

use std::{
    sync::{
        atomic::Ordering,
        Arc,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use anyhow::Result;
use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Json, Router,
};
use serde_json::json;
use tokio::{net::TcpListener, time};
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
        .route("/api/v1/chat-fast/conversations/{conversation_id}/messages/", axum::routing::post(commands::send_message))
        .route("/api/v1/chat-fast/conversations/{conversation_id}/calls/start/", axum::routing::post(call_commands::start_call))
        .route("/api/v1/chat-fast/calls/recent/", get(call_commands::recent_calls))
        .route("/api/v1/chat-fast/calls/{call_id}/", get(call_commands::get_call))
        .route("/api/v1/chat-fast/calls/{call_id}/accept/", axum::routing::post(call_commands::accept_call))
        .route("/api/v1/chat-fast/calls/{call_id}/decline/", axum::routing::post(call_commands::decline_call))
        .route("/api/v1/chat-fast/calls/{call_id}/end/", axum::routing::post(call_commands::end_call));
    let app = if state.config.internal_test_enabled {
        app.route("/internal/ws-test", get(websocket::test_websocket_handler))
    } else {
        app
    }
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
    let payload = json!({
        "status": if stream_ready && auth_ready && presence_ready && ephemeral_ready && ownership_ready && database_ready { "ready" } else { "not_ready" },
        "durable_backend": state.config.durable_backend.as_str(),
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
        "sqlx_enabled": state.database.enabled(),
        "sqlx_database": database_ready,
        "nats_probe_enabled": state.config.nats_probe_enabled,
        "nats_probe_ready": state.nats_ready.load(Ordering::Acquire),
    });
    if stream_ready && auth_ready && presence_ready && ephemeral_ready && ownership_ready && database_ready {
        (StatusCode::OK, Json(payload))
    } else {
        (StatusCode::SERVICE_UNAVAILABLE, Json(payload))
    }
}

async fn stats(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    Json(json!({
        "uptime_seconds": now.saturating_sub(state.started_at_epoch),
        "connections": state.registry.connection_count(),
        "audiences": state.registry.audience_count(),
        "available_connection_slots": state.connection_slots.available_permits(),
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
        "ephemeral_backend": state.config.ephemeral_backend.as_str(),
        "presence_backend": state.presence.backend_name(),
        "chat_read_backend": state.database.backend_name(),
        "chat_command_backend": state.config.chat_command_backend.as_str(),
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
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    let uptime = now.saturating_sub(state.started_at_epoch);
    let body = format!(
        concat!(
            "# TYPE realtime_uptime_seconds gauge\n",
            "realtime_uptime_seconds {}\n",
            "# TYPE realtime_connections gauge\n",
            "realtime_connections {}\n",
            "realtime_audiences {}\n",
            "realtime_available_connection_slots {}\n",
            "# TYPE realtime_stream_ready gauge\n",
            "realtime_stream_ready {}\n",
            "# TYPE realtime_events_total counter\n",
            "realtime_stream_events_total {}\n",
            "realtime_stream_acks_total {}\n",
            "realtime_stream_errors_total {}\n",
            "realtime_stream_reconnects_total {}\n",
            "realtime_malformed_stream_events_total {}\n",
            "# TYPE realtime_ephemeral_ready gauge\n",
            "realtime_ephemeral_ready {}\n",
            "realtime_ephemeral_events_total {}\n",
            "realtime_ephemeral_published_total {}\n",
            "realtime_ephemeral_errors_total {}\n",
            "realtime_ephemeral_reconnects_total {}\n",
            "realtime_connections_accepted_total {}\n",
            "realtime_connections_rejected_total {}\n",
            "realtime_rate_limited_events_total {}\n",
            "realtime_delivered_total {}\n",
            "realtime_dropped_ephemeral_total {}\n",
            "realtime_disconnected_slow_total {}\n"
        ),
        uptime,
        state.registry.connection_count(),
        state.registry.audience_count(),
        state.connection_slots.available_permits(),
        u8::from(state.stream_ready.load(Ordering::Acquire)),
        state.stream_events.load(Ordering::Relaxed),
        state.stream_acks.load(Ordering::Relaxed),
        state.stream_errors.load(Ordering::Relaxed),
        state.stream_reconnects.load(Ordering::Relaxed),
        state.malformed_stream_events.load(Ordering::Relaxed),
        u8::from(state.ephemeral_ready.load(Ordering::Acquire)),
        state.ephemeral_events.load(Ordering::Relaxed),
        state.ephemeral_published.load(Ordering::Relaxed),
        state.ephemeral_errors.load(Ordering::Relaxed),
        state.ephemeral_reconnects.load(Ordering::Relaxed),
        state.connections_accepted.load(Ordering::Relaxed),
        state.connections_rejected.load(Ordering::Relaxed),
        state.rate_limited_events.load(Ordering::Relaxed),
        state.registry.stats.delivered(),
        state.registry.stats.dropped_ephemeral(),
        state.registry.stats.disconnected_slow(),
    );
    ([("content-type", "text/plain; version=0.0.4; charset=utf-8")], body)
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
