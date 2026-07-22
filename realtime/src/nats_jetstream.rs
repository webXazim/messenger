use std::{
    collections::{HashSet, VecDeque},
    sync::{atomic::Ordering, Arc, Mutex},
    time::Duration,
};

use anyhow::{Context, Result};
use async_nats::jetstream::{self, consumer};
use futures_util::TryStreamExt;
use serde::Deserialize;
use serde_json::Value;
use tokio::time;

use crate::{
    nats_core::EphemeralPriority,
    ownership::TargetedDelivery,
    protocol::{AudienceKey, TextFrame},
    state::AppState,
};

#[derive(Debug, Deserialize)]
struct DurableEnvelope {
    event_id: String,
    audiences: Vec<AudienceKey>,
    payload: Value,
}

pub struct EventDeduper {
    capacity: usize,
    inner: Mutex<DeduperState>,
}

struct DeduperState {
    order: VecDeque<String>,
    ids: HashSet<String>,
}

impl EventDeduper {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity: capacity.max(1),
            inner: Mutex::new(DeduperState {
                order: VecDeque::with_capacity(capacity.min(16_384)),
                ids: HashSet::with_capacity(capacity.min(16_384)),
            }),
        }
    }

    /// Returns true only for the first observation of an event ID.
    pub fn remember(&self, event_id: &str) -> bool {
        let mut state = self.inner.lock().expect("event deduper mutex poisoned");
        if state.ids.contains(event_id) {
            return false;
        }
        let owned = event_id.to_owned();
        state.ids.insert(owned.clone());
        state.order.push_back(owned);
        while state.order.len() > self.capacity {
            if let Some(expired) = state.order.pop_front() {
                state.ids.remove(&expired);
            }
        }
        true
    }
}

pub async fn run(state: Arc<AppState>) {
    let mut backoff = Duration::from_secs(1);

    loop {
        if state.shutdown.is_cancelled() {
            return;
        }

        match consume(state.clone()).await {
            Ok(()) => return,
            Err(error) => {
                state.stream_ready.store(false, Ordering::Release);
                state.durable_nats.set_client(None).await;
                state.stream_errors.fetch_add(1, Ordering::Relaxed);
                state.stream_reconnects.fetch_add(1, Ordering::Relaxed);
                tracing::error!(error = ?error, "JetStream durable consumer stopped");
                tokio::select! {
                    _ = state.shutdown.cancelled() => return,
                    _ = time::sleep(backoff) => {}
                }
                backoff = (backoff * 2).min(Duration::from_secs(30));
            }
        }
    }
}

async fn consume(state: Arc<AppState>) -> Result<()> {
    let client = tokio::time::timeout(
        state.config.nats_connect_timeout,
        crate::nats_connection::connect(&state.config),
    )
    .await
    .context("NATS connection timed out")?
    .context("cannot connect to NATS")?;

    let jetstream = jetstream::new(client.clone());
    let stream = jetstream
        .get_stream(&state.config.nats_stream_name)
        .await
        .with_context(|| format!("JetStream stream {} is unavailable", state.config.nats_stream_name))?;

    let consumer = stream
        .get_or_create_consumer(
            &state.config.nats_consumer_name,
            consumer::pull::Config {
                durable_name: Some(state.config.nats_consumer_name.clone()),
                filter_subject: state.config.nats_subject_filter.clone(),
                ack_policy: consumer::AckPolicy::Explicit,
                ack_wait: state.config.nats_ack_wait,
                max_deliver: state.config.nats_max_deliver,
                max_ack_pending: state.config.nats_max_ack_pending,
                ..Default::default()
            },
        )
        .await
        .context("cannot create or open JetStream pull consumer")?;

    let mut messages = consumer
        .messages()
        .await
        .context("cannot start JetStream message stream")?;

    state.durable_nats.set_client(Some(client)).await;
    state.stream_ready.store(true, Ordering::Release);
    state.nats_ready.store(true, Ordering::Release);
    tracing::info!(
        stream = %state.config.nats_stream_name,
        consumer = %state.config.nats_consumer_name,
        filter = %state.config.nats_subject_filter,
        "JetStream durable consumer ready"
    );

    loop {
        tokio::select! {
            _ = state.shutdown.cancelled() => return Ok(()),
            result = messages.try_next() => {
                let Some(message) = result.context("JetStream consumer read failed")? else {
                    return Err(anyhow::anyhow!("JetStream consumer ended unexpectedly"));
                };

                match process_message(&state, &message.payload).await {
                    Ok(()) => {}
                    Err(error) => {
                        state.malformed_stream_events.fetch_add(1, Ordering::Relaxed);
                        tracing::error!(error = %error, "dropping malformed JetStream event");
                    }
                }

                message
                    .ack()
                    .await
                    .map_err(|error| anyhow::anyhow!("JetStream ACK failed: {error}"))?;
                state.stream_acks.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
}

async fn process_message(state: &AppState, bytes: &[u8]) -> Result<()> {
    let envelope: DurableEnvelope =
        serde_json::from_slice(bytes).context("invalid JetStream event envelope")?;

    if envelope.event_id.trim().is_empty() {
        anyhow::bail!("JetStream event_id is empty");
    }

    let first_local_observation = state.event_deduper.remember(&envelope.event_id);
    if !first_local_observation {
        tracing::debug!(
            event_id = %envelope.event_id,
            "JetStream event was already delivered locally; remote ownership routing is still evaluated"
        );
    }

    let payload = serde_json::to_string(&envelope.payload)
        .context("cannot serialize JetStream client payload")?;
    let targeted_payload = if state.config.connection_ownership_backend.as_str() == "nats" {
        Some(payload.clone())
    } else {
        None
    };
    let delivered = if first_local_observation {
        state
            .registry
            .fanout_high(&envelope.audiences, TextFrame::from(payload))
    } else {
        0
    };
    if let Some(targeted_payload) = targeted_payload {
        let targeted = TargetedDelivery {
            version: 1,
            origin_node_id: state.config.nats_node_id.clone(),
            event_id: envelope.event_id.clone(),
            audiences: envelope.audiences.clone(),
            message: targeted_payload,
            priority: EphemeralPriority::High,
            target_actor_id: None,
        };
        match state.ownership.publish_targeted(state, &targeted).await {
            Ok(nodes) => { state.targeted_deliveries_published.fetch_add(nodes as u64, Ordering::Relaxed); }
            Err(error) => { tracing::warn!(error=%error, event_id=%envelope.event_id, "targeted durable routing failed; local delivery was preserved"); }
        }
    }
    state.stream_events.fetch_add(1, Ordering::Relaxed);
    tracing::debug!(
        event_id = %envelope.event_id,
        delivered,
        audience_count = envelope.audiences.len(),
        "JetStream event dispatched"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::EventDeduper;

    #[test]
    fn deduper_rejects_recent_duplicates_and_evicts_old_ids() {
        let deduper = EventDeduper::new(2);
        assert!(deduper.remember("one"));
        assert!(!deduper.remember("one"));
        assert!(deduper.remember("two"));
        assert!(deduper.remember("three"));
        assert!(deduper.remember("one"));
    }
}
