use std::{
    sync::{
        atomic::{AtomicI64, Ordering},
        Arc,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use axum::{
    extract::{
        ws::{close_code, CloseFrame, Message, WebSocket, WebSocketUpgrade},
        Query, State,
    },
    http::{header::ORIGIN, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use futures_util::{stream::SplitSink, stream::SplitStream, SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use tokio::{sync::mpsc, time};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use crate::{
    nats_core::{self, EphemeralPriority},
    auth::{ActorType, AuthError, AuthenticatedSession},
    protocol::{
        control_message, event_message, AudienceKey, AudienceKind, ClientCommand,
        OutboundMessage, SubscriptionData,
    },
    registry::ConnectionHandle,
    state::AppState,
};

#[derive(Debug, Deserialize)]
pub struct TicketQuery {
    ticket: String,
}

#[derive(Debug, Deserialize)]
struct PresenceData {
    #[serde(default)]
    device_type: String,
    #[serde(default)]
    presence_status: String,
}

#[derive(Clone, Copy)]
enum RateClass {
    General,
    Ephemeral,
    Signaling,
}

struct InboundRateLimiter {
    window_started: Instant,
    total: u16,
    ephemeral: u16,
    signaling: u16,
    violations: u8,
}

impl InboundRateLimiter {
    const WINDOW: Duration = Duration::from_secs(10);
    const TOTAL_LIMIT: u16 = 240;
    const EPHEMERAL_LIMIT: u16 = 50;
    const SIGNALING_LIMIT: u16 = 200;

    fn new() -> Self {
        Self {
            window_started: Instant::now(),
            total: 0,
            ephemeral: 0,
            signaling: 0,
            violations: 0,
        }
    }

    fn allow(&mut self, event: &str) -> bool {
        if self.window_started.elapsed() >= Self::WINDOW {
            self.window_started = Instant::now();
            self.total = 0;
            self.ephemeral = 0;
            self.signaling = 0;
            self.violations = 0;
        }
        self.total = self.total.saturating_add(1);
        let class = match event {
            "typing.start" | "typing.stop" | "presence.ping" | "support.ping"
            | "support.typing.start" | "support.typing.stop" => RateClass::Ephemeral,
            "call.signal" => RateClass::Signaling,
            _ => RateClass::General,
        };
        let class_allowed = match class {
            RateClass::General => true,
            RateClass::Ephemeral => {
                self.ephemeral = self.ephemeral.saturating_add(1);
                self.ephemeral <= Self::EPHEMERAL_LIMIT
            }
            RateClass::Signaling => {
                self.signaling = self.signaling.saturating_add(1);
                self.signaling <= Self::SIGNALING_LIMIT
            }
        };
        let allowed = self.total <= Self::TOTAL_LIMIT && class_allowed;
        if !allowed {
            self.violations = self.violations.saturating_add(1);
        }
        allowed
    }

    fn should_disconnect(&self) -> bool {
        self.violations >= 3
    }
}

pub async fn authenticated_websocket_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    Query(query): Query<TicketQuery>,
    headers: HeaderMap,
) -> Response {
    if query.ticket.len() > 8192 {
        return (StatusCode::UNAUTHORIZED, "invalid_ticket").into_response();
    }
    let Some(auth) = state.auth.as_ref() else {
        return (StatusCode::SERVICE_UNAVAILABLE, "realtime authentication is disabled")
            .into_response();
    };
    let origin = headers.get(ORIGIN).and_then(|value| value.to_str().ok());
    let session = match auth.authenticate_ticket(&query.ticket, origin).await {
        Ok(session) => session,
        Err(error) => {
            tracing::info!(
                origin = origin.unwrap_or(""),
                error = %error,
                "websocket authentication rejected"
            );
            return auth_error_response(error);
        }
    };
    let Some(session_permit) = state.session_limiter.try_acquire(
        &session,
        state.config.max_user_connections,
        state.config.max_widget_connections,
        state.config.max_device_connections,
    ) else {
        state.connections_rejected.fetch_add(1, Ordering::Relaxed);
        return (StatusCode::TOO_MANY_REQUESTS, "realtime session limit reached")
            .into_response();
    };
    let Ok(permit) = state.connection_slots.clone().try_acquire_owned() else {
        state.connections_rejected.fetch_add(1, Ordering::Relaxed);
        return (StatusCode::SERVICE_UNAVAILABLE, "realtime connection limit reached")
            .into_response();
    };
    state.connections_accepted.fetch_add(1, Ordering::Relaxed);

    let config = state.config.clone();
    ws.read_buffer_size(config.read_buffer_size)
        .write_buffer_size(config.write_buffer_size)
        .max_write_buffer_size(config.max_write_buffer_size)
        .max_message_size(config.max_message_size)
        .max_frame_size(config.max_frame_size)
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            let _session_permit = session_permit;
            handle_socket(socket, state, session).await;
        })
}

pub async fn test_websocket_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    if !state.config.internal_test_enabled {
        return StatusCode::NOT_FOUND.into_response();
    }
    let supplied_token = headers
        .get("x-realtime-test-token")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    if supplied_token.as_bytes() != state.config.internal_test_token.as_bytes() {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    let Ok(permit) = state.connection_slots.clone().try_acquire_owned() else {
        state.connections_rejected.fetch_add(1, Ordering::Relaxed);
        return (StatusCode::SERVICE_UNAVAILABLE, "realtime connection limit reached")
            .into_response();
    };
    state.connections_accepted.fetch_add(1, Ordering::Relaxed);
    let config = state.config.clone();
    ws.read_buffer_size(config.read_buffer_size)
        .write_buffer_size(config.write_buffer_size)
        .max_write_buffer_size(config.max_write_buffer_size)
        .max_message_size(config.max_message_size)
        .max_frame_size(config.max_frame_size)
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            handle_socket(socket, state, AuthenticatedSession::internal_test()).await;
        })
}

fn auth_error_response(error: AuthError) -> Response {
    let (status, code) = match error {
        AuthError::Disabled => (StatusCode::SERVICE_UNAVAILABLE, "auth_disabled"),
        AuthError::StorageUnavailable => (StatusCode::SERVICE_UNAVAILABLE, "auth_storage_unavailable"),
        AuthError::OriginDenied => (StatusCode::FORBIDDEN, "origin_denied"),
        AuthError::TicketReplay => (StatusCode::UNAUTHORIZED, "ticket_replayed"),
        AuthError::InvalidTicket => (StatusCode::UNAUTHORIZED, "invalid_ticket"),
        AuthError::InvalidGrant => (StatusCode::FORBIDDEN, "invalid_grant"),
    };
    (status, code).into_response()
}

async fn handle_socket(socket: WebSocket, state: Arc<AppState>, session: AuthenticatedSession) {
    let connection_id = Uuid::new_v4();
    let cancellation = CancellationToken::new();
    let subscriptions = Arc::new(dashmap::DashSet::new());
    let (high_tx, high_rx) = mpsc::channel(state.config.high_queue_capacity);
    let (low_tx, low_rx) = mpsc::channel(state.config.low_queue_capacity);
    let last_seen = Arc::new(AtomicI64::new(epoch_seconds()));
    let session = Arc::new(session);
    tracing::info!(
        %connection_id,
        actor_type = session.actor_type.as_str(),
        device_type = session.device_type.as_str(),
        "websocket connection accepted"
    );

    state.registry.register(
        connection_id,
        ConnectionHandle {
            actor_id: session.actor_id.clone(),
            high_tx: high_tx.clone(),
            low_tx: low_tx.clone(),
            cancellation: cancellation.clone(),
            subscriptions,
        },
    );

    let mut initial_audiences = Vec::new();
    for audience in &session.initial_audiences {
        if state.registry.subscribe(connection_id, audience.clone()) {
            initial_audiences.push(audience.clone());
        }
    }

    if let Ok(message) = control_message(
        "connection.ready",
        None,
        json!({
            "connection_id": connection_id,
            "protocol": 1,
            "actor": {"id": session.actor_id.as_str(), "type": session.actor_type.as_str()},
            "scopes": &session.scopes,
            "device_id": session.device_id.as_str(),
            "device_type": session.device_type.as_str(),
            "initial_audiences": initial_audiences,
        }),
    ) {
        let _ = high_tx.try_send(OutboundMessage::Text(message));
    }

    publish_initial_presence(&state, &session, connection_id).await;

    let (sender, receiver) = socket.split();
    let mut reader_task = tokio::spawn(reader_loop(
        receiver,
        connection_id,
        state.clone(),
        session.clone(),
        high_tx.clone(),
        last_seen.clone(),
        cancellation.clone(),
    ));
    let mut writer_task = tokio::spawn(writer_loop(
        sender,
        high_rx,
        low_rx,
        last_seen,
        state.config.heartbeat_interval,
        state.config.client_timeout,
        state.config.send_timeout,
        cancellation.clone(),
    ));

    #[derive(Clone, Copy, Debug)]
    enum StopReason { Reader, Writer, Shutdown, CredentialsRefresh }
    let max_connection_age = state.config.max_connection_age;
    let jitter_window = state.config.connection_refresh_jitter.as_secs();
    let jitter_seconds = if jitter_window == 0 {
        0
    } else {
        (connection_id.as_u128() % (u128::from(jitter_window) + 1)) as u64
    };
    let credentials_refresh_after = max_connection_age + Duration::from_secs(jitter_seconds);
    let stop_reason = tokio::select! {
        result = &mut reader_task => {
            log_socket_task_result(connection_id, "reader", result);
            StopReason::Reader
        }
        result = &mut writer_task => {
            log_socket_task_result(connection_id, "writer", result);
            StopReason::Writer
        }
        _ = state.shutdown.cancelled() => StopReason::Shutdown,
        _ = time::sleep(credentials_refresh_after) => StopReason::CredentialsRefresh,
    };

    match stop_reason {
        StopReason::Shutdown => {
            reader_task.abort();
            let _ = high_tx.try_send(OutboundMessage::Close {
                code: close_code::RESTART,
                reason: "service restart".into(),
            });
            if time::timeout(Duration::from_secs(2), &mut writer_task).await.is_err() {
                cancellation.cancel();
                writer_task.abort();
            }
        }
        StopReason::CredentialsRefresh => {
            reader_task.abort();
            let _ = high_tx.try_send(OutboundMessage::Close {
                code: 4001,
                reason: "credentials refresh".into(),
            });
            if time::timeout(Duration::from_secs(2), &mut writer_task).await.is_err() {
                cancellation.cancel();
                writer_task.abort();
            }
        }
        StopReason::Reader => {
            cancellation.cancel();
            if time::timeout(Duration::from_secs(2), &mut writer_task).await.is_err() {
                writer_task.abort();
            }
        }
        StopReason::Writer => {
            cancellation.cancel();
            reader_task.abort();
        }
    }
    state.registry.remove(connection_id);
    tracing::info!(
        %connection_id,
        actor_type = session.actor_type.as_str(),
        reason = ?stop_reason,
        "websocket connection closed"
    );
    schedule_presence_disconnect(state, session, connection_id);
}

async fn publish_initial_presence(
    state: &Arc<AppState>,
    session: &Arc<AuthenticatedSession>,
    connection_id: Uuid,
) {
    match session.actor_type {
        ActorType::User => {
            if let Ok(snapshot) = state
                .presence
                .touch_user(session, connection_id, &session.device_type, "active")
                .await
            {
                persist_user_last_seen(state, session, true);
                fanout_user_presence(state, session, snapshot);
            }
        }
        ActorType::SupportWidget => {
            if state.presence.touch_support_visitor(session, connection_id).await.is_ok() {
                fanout_support_visitor_presence(state, session, true);
                if let Ok(message) = event_message(
                    "support.widget.ready",
                    json!({
                        "session_id": session.session_id.as_str(),
                        "website_id": session.website_id.as_str(),
                        "conversation_id": session.support_conversation_id.as_str(),
                    }),
                ) {
                    state.registry.fanout_low(
                        &[AudienceKey { kind: AudienceKind::SupportVisitor, identifier: session.actor_id.clone() }],
                        message,
                        None,
                        Some(&session.actor_id),
                    );
                }
            }
        }
        ActorType::InternalTest => {}
    }
}

fn schedule_presence_disconnect(
    state: Arc<AppState>,
    session: Arc<AuthenticatedSession>,
    connection_id: Uuid,
) {
    tokio::spawn(async move {
        time::sleep(state.config.presence_disconnect_grace).await;
        match session.actor_type {
            ActorType::User => {
                if let Ok(snapshot) = state.presence.remove_user(&session, connection_id).await {
                    persist_user_last_seen(&state, &session, true);
                    fanout_user_presence(&state, &session, snapshot);
                }
            }
            ActorType::SupportWidget => {
                if let Ok(still_online) = state
                    .presence
                    .remove_support_visitor(&session, connection_id)
                    .await
                {
                    fanout_support_visitor_presence(&state, &session, still_online);
                }
            }
            ActorType::InternalTest => {}
        }
    });
}

fn fanout_user_presence(state: &Arc<AppState>, session: &AuthenticatedSession, snapshot: Value) {
    let state = state.clone();
    let session = session.clone();
    tokio::spawn(async move {
        let audiences: Vec<AudienceKey> = session.presence_recipient_ids.clone()
            .into_iter()
            .map(|identifier| AudienceKey { kind: AudienceKind::User, identifier })
            .collect();
        if audiences.is_empty() { return; }
        let mut data = value_object(snapshot);
        data.insert("user_id".to_owned(), json!(session.actor_id.as_str()));
        data.insert("username".to_owned(), json!(session.username.as_str()));
        data.insert("display_name".to_owned(), json!(session.display_name.as_str()));
        if !data.contains_key("last_seen_at") {
            data.insert("last_seen_at".to_owned(), Value::Null);
        }
        data.insert("visibility".to_owned(), json!("public"));
        if let Ok(message) = event_message("presence.updated", Value::Object(data)) {
            state.registry.fanout_low(&audiences, message.clone(), None, None);
            nats_core::publish_after_local(&state, audiences, message, EphemeralPriority::Low, None, None).await;
        }
    });
}

fn persist_user_last_seen(state: &Arc<AppState>, session: &AuthenticatedSession, force: bool) {
    if session.actor_type != ActorType::User
        || !state.presence.claim_last_seen_persistence(&session.actor_id, force)
    {
        return;
    }
    let database = state.database.clone();
    let user_id = session.actor_id.clone();
    tokio::spawn(async move {
        if let Err(error) = database.persist_user_last_seen(&user_id).await {
            tracing::warn!(error = %error, user_id, "could not persist realtime last-seen timestamp");
        }
    });
}

fn fanout_support_visitor_presence(
    state: &Arc<AppState>,
    session: &AuthenticatedSession,
    online: bool,
) {
    if session.website_id.is_empty() { return; }
    if let Ok(message) = event_message(
        "support.visitor.presence",
        json!({
            "website_id": session.website_id.as_str(),
            "conversation_id": session.support_conversation_id.as_str(),
            "visitor_id": session.actor_id.as_str(),
            "is_online": online,
            "last_seen_at": Value::Null,
            "current_page_url": "",
            "referrer": "",
        }),
    ) {
        let audience = AudienceKey { kind: AudienceKind::SupportWebsite, identifier: session.website_id.clone() };
        state.registry.fanout_low(&[audience.clone()], message.clone(), None, None);
        let state = state.clone();
        tokio::spawn(async move {
            nats_core::publish_after_local(&state, vec![audience], message, EphemeralPriority::Low, None, None).await;
        });
    }
}

fn value_object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => Map::new(),
    }
}

fn log_socket_task_result(
    connection_id: Uuid,
    task: &str,
    result: Result<anyhow::Result<()>, tokio::task::JoinError>,
) {
    match result {
        Ok(Ok(())) => {}
        Ok(Err(error)) => tracing::debug!(%connection_id, task, error = %error, "websocket task stopped"),
        Err(error) => tracing::warn!(%connection_id, task, error = %error, "websocket task join failed"),
    }
}

async fn reader_loop(
    mut receiver: SplitStream<WebSocket>,
    connection_id: Uuid,
    state: Arc<AppState>,
    session: Arc<AuthenticatedSession>,
    high_tx: mpsc::Sender<OutboundMessage>,
    last_seen: Arc<AtomicI64>,
    cancellation: CancellationToken,
) -> anyhow::Result<()> {
    let mut rate_limiter = InboundRateLimiter::new();
    loop {
        tokio::select! {
            _ = cancellation.cancelled() => return Ok(()),
            next = receiver.next() => {
                let Some(message) = next else { return Ok(()); };
                match message? {
                    Message::Text(text) => {
                        last_seen.store(epoch_seconds(), Ordering::Relaxed);
                        if handle_command(
                            text.as_str(),
                            connection_id,
                            state.clone(),
                            session.as_ref(),
                            &high_tx,
                            &mut rate_limiter,
                        ).await {
                            return Ok(());
                        }
                    }
                    Message::Ping(payload) => {
                        last_seen.store(epoch_seconds(), Ordering::Relaxed);
                        let _ = high_tx.try_send(OutboundMessage::Pong(payload.to_vec()));
                    }
                    Message::Pong(_) => { last_seen.store(epoch_seconds(), Ordering::Relaxed); }
                    Message::Close(_) => return Ok(()),
                    Message::Binary(_) => send_control(&high_tx, "error", None, json!({"code": "binary_not_supported"})),
                }
            }
        }
    }
}

async fn handle_command(
    text: &str,
    connection_id: Uuid,
    state: Arc<AppState>,
    session: &AuthenticatedSession,
    high_tx: &mpsc::Sender<OutboundMessage>,
    rate_limiter: &mut InboundRateLimiter,
) -> bool {
    let command: ClientCommand = match serde_json::from_str(text) {
        Ok(command) => command,
        Err(_) => {
            send_control(high_tx, "error", None, json!({"code": "invalid_json"}));
            return false;
        }
    };
    if command.version != 1 {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "unsupported_protocol_version"}));
        return false;
    }
    if !rate_limiter.allow(&command.event) {
        state.rate_limited_events.fetch_add(1, Ordering::Relaxed);
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "rate_limited"}));
        return rate_limiter.should_disconnect();
    }

    match command.event.as_str() {
        "ping" => send_control(high_tx, "pong", command.request_id.as_deref(), json!({})),
        "audience.subscribe" => handle_subscribe(command, connection_id, state, session, high_tx),
        "audience.unsubscribe" => handle_unsubscribe(command, connection_id, state, high_tx),
        "presence.ping" => handle_presence_ping(command, connection_id, state, session, high_tx).await,
        "typing.start" | "typing.stop" => handle_messenger_typing(command, connection_id, state, session, high_tx).await,
        "call.signal" => handle_call_signal(command, connection_id, state, session, high_tx).await,
        "support.ping" => handle_support_ping(command, connection_id, state, session, high_tx).await,
        "support.typing.start" | "support.typing.stop" => {
            handle_support_typing(command, connection_id, state, session, high_tx).await
        }
        "message.send" | "message.edit" | "message.delete" | "message.react"
        | "message.unreact" | "message.delivered" | "message.read" | "call.accept"
        | "call.decline" | "call.end" | "call.heartbeat" | "call.media_state"
        | "call.speaker_state" | "support.message.delivered" | "support.message.read"
        | "support.visitor.activity" => send_control(
            high_tx,
            "error",
            command.request_id.as_deref(),
            json!({"code": "http_required"}),
        ),
        _ => send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "unknown_event"})),
    }
    false
}

fn handle_subscribe(
    command: ClientCommand,
    connection_id: Uuid,
    state: Arc<AppState>,
    session: &AuthenticatedSession,
    high_tx: &mpsc::Sender<OutboundMessage>,
) {
    let data = match serde_json::from_value::<SubscriptionData>(command.data) {
        Ok(data) if data.audience.validate() => data,
        _ => {
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "invalid_audience"}));
            return;
        }
    };
    if session.require_grants {
        let Some(grant) = data.grant.as_deref() else {
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "grant_required"}));
            return;
        };
        let Some(auth) = state.auth.as_ref() else {
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "auth_unavailable"}));
            return;
        };
        if auth.validate_grant(grant, session, &data.audience).is_err() {
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "invalid_grant"}));
            return;
        }
    }
    let subscribed = state.registry.subscribe(connection_id, data.audience.clone());
    send_control(
        high_tx,
        "audience.subscribed",
        command.request_id.as_deref(),
        json!({"audience": data.audience, "subscribed": subscribed}),
    );
}

fn handle_unsubscribe(
    command: ClientCommand,
    connection_id: Uuid,
    state: Arc<AppState>,
    high_tx: &mpsc::Sender<OutboundMessage>,
) {
    let data = match serde_json::from_value::<SubscriptionData>(command.data) {
        Ok(data) if data.audience.validate() => data,
        _ => {
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "invalid_audience"}));
            return;
        }
    };
    let removed = state.registry.unsubscribe(connection_id, &data.audience);
    send_control(
        high_tx,
        "audience.unsubscribed",
        command.request_id.as_deref(),
        json!({"audience": data.audience, "removed": removed}),
    );
}

async fn handle_presence_ping(
    command: ClientCommand,
    connection_id: Uuid,
    state: Arc<AppState>,
    session: &AuthenticatedSession,
    high_tx: &mpsc::Sender<OutboundMessage>,
) {
    if session.actor_type != ActorType::User {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "scope_denied"}));
        return;
    }
    let data: PresenceData = serde_json::from_value(command.data).unwrap_or(PresenceData {
        device_type: session.device_type.clone(),
        presence_status: "active".to_owned(),
    });
    match state
        .presence
        .touch_user(
            session,
            connection_id,
            if data.device_type.is_empty() { &session.device_type } else { &data.device_type },
            &data.presence_status,
        )
        .await
    {
        Ok(snapshot) => {
            persist_user_last_seen(&state, session, false);
            fanout_user_presence(&state, session, snapshot.clone());
            let mut payload = value_object(snapshot);
            payload.insert("user_id".to_owned(), json!(session.actor_id.as_str()));
            payload.insert("server_time".to_owned(), json!(epoch_seconds()));
            if let Ok(message) = event_message("presence.pong", Value::Object(payload)) {
                let _ = high_tx.try_send(OutboundMessage::Text(message));
            }
        }
        Err(_) => send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "presence_unavailable"})),
    }
}

async fn handle_messenger_typing(
    command: ClientCommand,
    connection_id: Uuid,
    state: Arc<AppState>,
    session: &AuthenticatedSession,
    high_tx: &mpsc::Sender<OutboundMessage>,
) {
    if !session.has_scope("messenger") {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "scope_denied"}));
        return;
    }
    let conversation_id = command
        .data
        .get("conversation_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    let audience = AudienceKey { kind: AudienceKind::Conversation, identifier: conversation_id.to_owned() };
    if !audience.validate() || !state.registry.is_subscribed(connection_id, &audience) {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "conversation_not_subscribed"}));
        return;
    }
    let conversation_uuid = match Uuid::parse_str(conversation_id) {
        Ok(value) => value,
        Err(_) => {
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "invalid_conversation"}));
            return;
        }
    };
    let actor_id = match session.actor_id.parse::<i64>() {
        Ok(value) => value,
        Err(_) => {
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "invalid_actor"}));
            return;
        }
    };
    match state.database.can_emit_messenger_ephemeral(conversation_uuid, actor_id).await {
        Ok(true) => {}
        Ok(false) => {
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "conversation_forbidden"}));
            return;
        }
        Err(error) => {
            tracing::warn!(error=%error, %conversation_uuid, actor_id, "typing authorization check failed");
            send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "authorization_unavailable"}));
            return;
        }
    }
    let started = command.event.ends_with("start");
    if let Ok(message) = event_message(
        if started { "typing.started" } else { "typing.stopped" },
        json!({
            "conversation_id": conversation_id,
            "user_id": session.actor_id.as_str(),
            "username": session.username.as_str(),
            "display_name": session.display_name.as_str(),
            "expires_at": if started { Some(epoch_seconds() + 7) } else { None },
        }),
    ) {
        state.registry.fanout_low(&[audience.clone()], message.clone(), Some(connection_id), None);
        nats_core::publish_after_local(
            &state,
            vec![audience],
            message,
            EphemeralPriority::Low,
            Some(connection_id),
            None,
        ).await;
    }
}

async fn handle_call_signal(
    command: ClientCommand,
    connection_id: Uuid,
    state: Arc<AppState>,
    session: &AuthenticatedSession,
    high_tx: &mpsc::Sender<OutboundMessage>,
) {
    if !session.has_scope("messenger") {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "scope_denied"}));
        return;
    }
    let conversation_id = command.data.get("conversation_id").and_then(Value::as_str).unwrap_or_default().trim();
    let call_id = command.data.get("call_id").and_then(Value::as_str).unwrap_or_default().trim();
    let signal_type = command.data.get("signal_type").and_then(Value::as_str).unwrap_or_default().trim();
    let signal_grant = command.data.get("call_grant").and_then(Value::as_str).unwrap_or_default();
    const ALLOWED_SIGNAL_TYPES: &[&str] = &[
        "offer", "answer", "ice_candidate", "renegotiate", "hangup", "busy",
        "ice_restart", "network_state", "quality_update", "media_toggle",
        "speaker_hint", "fallback_audio_only", "receiver_report", "request_keyframe",
    ];
    if call_id.is_empty() || !ALLOWED_SIGNAL_TYPES.contains(&signal_type) {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "invalid_call_signal"}));
        return;
    }
    let conversation = AudienceKey { kind: AudienceKind::Conversation, identifier: conversation_id.to_owned() };
    if !conversation.validate() || !state.registry.is_subscribed(connection_id, &conversation) {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "conversation_not_subscribed"}));
        return;
    }
    let target = command
        .data
        .get("to_user_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned);
    let Some(auth) = state.auth.as_ref() else {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "auth_unavailable"}));
        return;
    };
    if signal_grant.is_empty()
        || auth
            .validate_call_grant(
                signal_grant,
                session,
                call_id,
                conversation_id,
                target.as_deref(),
            )
            .is_err()
    {
        send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "invalid_call_grant"}));
        return;
    }
    let mut data = value_object(command.data);
    data.remove("call_grant");
    data.insert("from_user_id".to_owned(), json!(session.actor_id.as_str()));
    if let Ok(message) = event_message("call.signal", Value::Object(data)) {
        if let Some(user_id) = target.as_deref() {
            // Target only connections that are both the requested actor and subscribed
            // to this authorized conversation. A client cannot signal an arbitrary user.
            state.registry.fanout_high_filtered(
                &[conversation.clone()],
                message.clone(),
                Some(connection_id),
                Some(user_id),
            );
            nats_core::publish_after_local(
                &state,
                vec![conversation],
                message,
                EphemeralPriority::High,
                Some(connection_id),
                Some(user_id.to_owned()),
            ).await;
        } else {
            state.registry.fanout_high_filtered(&[conversation.clone()], message.clone(), Some(connection_id), None);
            nats_core::publish_after_local(
                &state,
                vec![conversation],
                message,
                EphemeralPriority::High,
                Some(connection_id),
                None,
            ).await;
        }
    }
}

async fn handle_support_ping(
    command: ClientCommand,
    connection_id: Uuid,
    state: Arc<AppState>,
    session: &AuthenticatedSession,
    high_tx: &mpsc::Sender<OutboundMessage>,
) {
    if session.actor_type == ActorType::SupportWidget {
        let _ = state.presence.touch_support_visitor(session, connection_id).await;
        fanout_support_visitor_presence(&state, session, true);
    }
    if let Ok(message) = event_message("support.pong", json!({})) {
        let _ = high_tx.try_send(OutboundMessage::Text(message));
    }
    send_control(high_tx, "support.pong", command.request_id.as_deref(), json!({}));
}

async fn handle_support_typing(
    command: ClientCommand,
    connection_id: Uuid,
    state: Arc<AppState>,
    session: &AuthenticatedSession,
    high_tx: &mpsc::Sender<OutboundMessage>,
) {
    let started = command.event.ends_with("start");
    match session.actor_type {
        ActorType::SupportWidget => {
            if session.website_id.is_empty() || session.support_conversation_id.is_empty() {
                send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "support_conversation_unavailable"}));
                return;
            }
            if let Ok(message) = event_message(
                if started { "support.typing.started" } else { "support.typing.stopped" },
                json!({
                    "conversation_id": session.support_conversation_id.as_str(),
                    "website_id": session.website_id.as_str(),
                    "visitor_id": session.actor_id.as_str(),
                    "sender": {"kind": "visitor", "id": session.actor_id.as_str(), "display_name": session.display_name.as_str()},
                    "expires_at": if started { Some(epoch_seconds() + 7) } else { None },
                }),
            ) {
                let audience = AudienceKey { kind: AudienceKind::SupportWebsite, identifier: session.website_id.clone() };
                state.registry.fanout_low(&[audience.clone()], message.clone(), Some(connection_id), None);
                nats_core::publish_after_local(
                    &state, vec![audience], message, EphemeralPriority::Low, Some(connection_id), None,
                ).await;
            }
        }
        ActorType::User if session.has_scope("support_team") => {
            let website_id = command.data.get("website_id").and_then(Value::as_str).unwrap_or_default();
            let visitor_id = command.data.get("visitor_id").and_then(Value::as_str).unwrap_or_default();
            let conversation_id = command.data.get("conversation_id").and_then(Value::as_str).unwrap_or_default();
            let website_audience = AudienceKey { kind: AudienceKind::SupportWebsite, identifier: website_id.to_owned() };
            if visitor_id.is_empty() || conversation_id.is_empty() || !state.registry.is_subscribed(connection_id, &website_audience) {
                send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "support_website_not_subscribed"}));
                return;
            }
            if let Ok(message) = event_message(
                if started { "support.typing.started" } else { "support.typing.stopped" },
                json!({
                    "conversation_id": conversation_id,
                    "website_id": website_id,
                    "visitor_id": visitor_id,
                    "sender": {"kind": "agent", "id": session.actor_id.as_str(), "display_name": session.display_name.as_str()},
                    "expires_at": if started { Some(epoch_seconds() + 7) } else { None },
                }),
            ) {
                let audience = AudienceKey { kind: AudienceKind::SupportVisitor, identifier: visitor_id.to_owned() };
                state.registry.fanout_low(&[audience.clone()], message.clone(), Some(connection_id), None);
                nats_core::publish_after_local(
                    &state, vec![audience], message, EphemeralPriority::Low, Some(connection_id), None,
                ).await;
            }
        }
        _ => send_control(high_tx, "error", command.request_id.as_deref(), json!({"code": "scope_denied"})),
    }
}

fn send_control(
    sender: &mpsc::Sender<OutboundMessage>,
    event: &str,
    request_id: Option<&str>,
    data: Value,
) {
    if let Ok(message) = control_message(event, request_id, data) {
        let _ = sender.try_send(OutboundMessage::Text(message));
    }
}

async fn writer_loop(
    mut sender: SplitSink<WebSocket, Message>,
    mut high_rx: mpsc::Receiver<OutboundMessage>,
    mut low_rx: mpsc::Receiver<OutboundMessage>,
    last_seen: Arc<AtomicI64>,
    heartbeat_interval: Duration,
    client_timeout: Duration,
    send_timeout: Duration,
    cancellation: CancellationToken,
) -> anyhow::Result<()> {
    let mut heartbeat = time::interval(heartbeat_interval);
    heartbeat.set_missed_tick_behavior(time::MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            biased;
            _ = cancellation.cancelled() => return Ok(()),
            _ = heartbeat.tick() => {
                let age = epoch_seconds().saturating_sub(last_seen.load(Ordering::Relaxed));
                if age > client_timeout.as_secs() as i64 {
                    send_with_timeout(
                        &mut sender,
                        OutboundMessage::Close { code: close_code::AWAY, reason: "heartbeat timeout".into() },
                        send_timeout,
                    ).await?;
                    return Ok(());
                }
                time::timeout(send_timeout, sender.send(Message::Ping(Vec::new().into())))
                    .await
                    .map_err(|_| anyhow::anyhow!("websocket ping timed out"))??;
            }
            message = high_rx.recv() => {
                let Some(message) = message else { return Ok(()); };
                let is_close = matches!(&message, OutboundMessage::Close { .. });
                send_with_timeout(&mut sender, message, send_timeout).await?;
                if is_close { return Ok(()); }
            }
            message = low_rx.recv() => {
                let Some(message) = message else { return Ok(()); };
                let is_close = matches!(&message, OutboundMessage::Close { .. });
                send_with_timeout(&mut sender, message, send_timeout).await?;
                if is_close { return Ok(()); }
            }
        }
    }
}

async fn send_with_timeout(
    sender: &mut SplitSink<WebSocket, Message>,
    message: OutboundMessage,
    timeout: Duration,
) -> anyhow::Result<()> {
    time::timeout(timeout, send_outbound(sender, message))
        .await
        .map_err(|_| anyhow::anyhow!("websocket send timed out"))??;
    Ok(())
}

async fn send_outbound(
    sender: &mut SplitSink<WebSocket, Message>,
    message: OutboundMessage,
) -> anyhow::Result<()> {
    match message {
        OutboundMessage::Text(text) => sender.send(Message::Text(text)).await?,
        OutboundMessage::Pong(payload) => sender.send(Message::Pong(payload.into())).await?,
        OutboundMessage::Close { code, reason } => {
            sender.send(Message::Close(Some(CloseFrame { code, reason: reason.to_string().into() }))).await?;
        }
    }
    Ok(())
}

fn epoch_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

#[cfg(test)]
mod tests {
    use super::InboundRateLimiter;

    #[test]
    fn inbound_limiter_disconnects_after_repeated_overflow() {
        let mut limiter = InboundRateLimiter::new();
        for _ in 0..50 {
            assert!(limiter.allow("typing.start"));
        }
        assert!(!limiter.allow("typing.start"));
        assert!(!limiter.allow("typing.start"));
        assert!(!limiter.allow("typing.start"));
        assert!(limiter.should_disconnect());
    }

    #[test]
    fn normal_signaling_burst_is_allowed() {
        let mut limiter = InboundRateLimiter::new();
        for _ in 0..100 {
            assert!(limiter.allow("call.signal"));
        }
        assert!(!limiter.should_disconnect());
    }
}
