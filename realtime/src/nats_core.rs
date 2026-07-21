use std::{sync::{atomic::Ordering, Arc}, time::Duration};

use anyhow::{Context, Result};
use async_nats::Client;
use serde::{Deserialize, Serialize};
use tokio::{sync::RwLock, time};
use tracing::{info, warn};
use uuid::Uuid;

use crate::{ownership::TargetedDelivery, protocol::{AudienceKey, TextFrame}, state::AppState};

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EphemeralPriority {
    High,
    Low,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct EphemeralEnvelope {
    pub version: u8,
    pub origin_node_id: String,
    pub event_id: Uuid,
    pub audiences: Vec<AudienceKey>,
    pub message: String,
    pub priority: EphemeralPriority,
    #[serde(default)]
    pub exclude_connection_id: Option<Uuid>,
    #[serde(default)]
    pub target_actor_id: Option<String>,
}

#[derive(Default)]
pub struct CoreNatsBus {
    client: RwLock<Option<Client>>,
}

impl CoreNatsBus {
    pub fn new() -> Arc<Self> { Arc::new(Self::default()) }

    async fn set_client(&self, client: Option<Client>) {
        *self.client.write().await = client;
    }

    pub async fn publish(&self, subject: &str, envelope: &EphemeralEnvelope) -> Result<()> {
        let client = self.client.read().await.clone().context("Core NATS is not connected")?;
        let payload = serde_json::to_vec(envelope).context("cannot encode ephemeral NATS envelope")?;
        client.publish(subject.to_owned(), payload.into()).await.context("cannot publish Core NATS event")?;
        Ok(())
    }
}

pub async fn run(state: Arc<AppState>) {
    if state.config.ephemeral_backend.as_str() != "nats" {
        state.ephemeral_ready.store(true, Ordering::Release);
        return;
    }

    let mut delay = Duration::from_secs(1);
    loop {
        if state.shutdown.is_cancelled() { break; }
        match connect_and_consume(state.clone()).await {
            Ok(()) if state.shutdown.is_cancelled() => break,
            Ok(()) => {}
            Err(error) => warn!(error = %error, "Core NATS ephemeral transport disconnected"),
        }
        state.ephemeral_ready.store(false, Ordering::Release);
        state.core_nats.set_client(None).await;
        state.ephemeral_reconnects.fetch_add(1, Ordering::Relaxed);
        tokio::select! {
            _ = state.shutdown.cancelled() => break,
            _ = time::sleep(delay) => {}
        }
        delay = (delay * 2).min(Duration::from_secs(15));
    }
}

async fn connect_and_consume(state: Arc<AppState>) -> Result<()> {
    let client = time::timeout(
        state.config.nats_connect_timeout,
        async_nats::connect(state.config.nats_url.clone()),
    )
    .await
    .context("Core NATS connection timed out")?
    .context("cannot connect to Core NATS")?;

    let mut subscription = client
        .subscribe(state.config.nats_ephemeral_subject.clone())
        .await
        .context("cannot subscribe to Core NATS ephemeral subject")?;
    state.core_nats.set_client(Some(client)).await;
    state.ephemeral_ready.store(true, Ordering::Release);
    info!(subject = %state.config.nats_ephemeral_subject, node_id = %state.config.nats_node_id, "Core NATS ephemeral transport ready");

    use futures_util::StreamExt;
    loop {
        tokio::select! {
            _ = state.shutdown.cancelled() => return Ok(()),
            incoming = subscription.next() => {
                let Some(incoming) = incoming else { return Err(anyhow::anyhow!("Core NATS subscription closed")); };
                let envelope: EphemeralEnvelope = match serde_json::from_slice(&incoming.payload) {
                    Ok(value) => value,
                    Err(error) => {
                        state.ephemeral_errors.fetch_add(1, Ordering::Relaxed);
                        warn!(error = %error, "ignoring malformed Core NATS ephemeral event");
                        continue;
                    }
                };
                if envelope.version != 1 || envelope.origin_node_id == state.config.nats_node_id {
                    continue;
                }
                let message = TextFrame::from(envelope.message);
                match envelope.priority {
                    EphemeralPriority::High => {
                        state.registry.fanout_high_filtered(
                            &envelope.audiences,
                            message,
                            None,
                            envelope.target_actor_id.as_deref(),
                        );
                    }
                    EphemeralPriority::Low => {
                        state.registry.fanout_low(
                            &envelope.audiences,
                            message,
                            None,
                            envelope.target_actor_id.as_deref(),
                        );
                    }
                }
                state.ephemeral_events.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
}

pub async fn publish_after_local(
    state: &AppState,
    audiences: Vec<AudienceKey>,
    message: TextFrame,
    priority: EphemeralPriority,
    exclude_connection_id: Option<Uuid>,
    target_actor_id: Option<String>,
) {
    if state.config.ephemeral_backend.as_str() != "nats" { return; }
    let envelope = EphemeralEnvelope {
        version: 1,
        origin_node_id: state.config.nats_node_id.clone(),
        event_id: Uuid::new_v4(),
        audiences,
        message: message.to_string(),
        priority,
        exclude_connection_id,
        target_actor_id,
    };
    if state.config.connection_ownership_backend.as_str() == "nats" {
        let targeted = TargetedDelivery {
            version: 1,
            origin_node_id: state.config.nats_node_id.clone(),
            event_id: envelope.event_id.to_string(),
            audiences: envelope.audiences.clone(),
            message: envelope.message.clone(),
            priority,
            target_actor_id: envelope.target_actor_id.clone(),
        };
        match state.ownership.publish_targeted(state, &targeted).await {
            Ok(nodes) if nodes > 0 => {
                state.targeted_deliveries_published.fetch_add(nodes as u64, Ordering::Relaxed);
                state.ephemeral_published.fetch_add(1, Ordering::Relaxed);
                return;
            }
            Ok(_) => {}
            Err(error) => warn!(error=%error, "targeted ephemeral routing unavailable; falling back to shared Core NATS subject"),
        }
    }
    if let Err(error) = state.core_nats.publish(&state.config.nats_ephemeral_subject, &envelope).await {
        state.ephemeral_errors.fetch_add(1, Ordering::Relaxed);
        warn!(error = %error, "Core NATS ephemeral publish failed; local delivery was preserved");
    } else {
        state.ephemeral_published.fetch_add(1, Ordering::Relaxed);
    }
}
