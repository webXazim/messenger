use std::{
    collections::{HashMap, HashSet},
    sync::{atomic::Ordering, Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result};
use async_nats::Client;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use tokio::{sync::RwLock, time};
use tracing::{info, warn};

use crate::{
    nats_core::EphemeralPriority,
    protocol::{AudienceKey, TextFrame},
    state::AppState,
};

#[derive(Debug, Serialize, Deserialize)]
struct OwnershipSnapshot {
    version: u8,
    node_id: String,
    expires_at_epoch: u64,
    audiences: Vec<AudienceKey>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TargetedDelivery {
    pub version: u8,
    pub origin_node_id: String,
    pub event_id: String,
    pub audiences: Vec<AudienceKey>,
    pub message: String,
    pub priority: EphemeralPriority,
    #[serde(default)]
    pub target_actor_id: Option<String>,
}

#[derive(Default)]
pub struct OwnershipRouter {
    client: RwLock<Option<Client>>,
    // audience -> node -> local expiry instant
    directory: Mutex<HashMap<AudienceKey, HashMap<String, Instant>>>,
}

impl OwnershipRouter {
    pub fn new() -> Arc<Self> { Arc::new(Self::default()) }

    async fn set_client(&self, client: Option<Client>) {
        *self.client.write().await = client;
    }

    pub fn remote_nodes(&self, audiences: &[AudienceKey], local_node: &str) -> HashSet<String> {
        let now = Instant::now();
        let mut directory = self.directory.lock().expect("ownership directory mutex poisoned");
        directory.retain(|_, nodes| {
            nodes.retain(|_, expires| *expires > now);
            !nodes.is_empty()
        });
        let mut result = HashSet::new();
        for audience in audiences {
            if let Some(nodes) = directory.get(audience) {
                for node in nodes.keys() {
                    if node != local_node { result.insert(node.clone()); }
                }
            }
        }
        result
    }

    pub async fn publish_targeted(
        &self,
        state: &AppState,
        delivery: &TargetedDelivery,
    ) -> Result<usize> {
        let client = self.client.read().await.clone().context("ownership NATS client is not connected")?;
        let nodes = self.remote_nodes(&delivery.audiences, &state.config.nats_node_id);
        let payload = serde_json::to_vec(delivery).context("cannot encode targeted delivery")?;
        for node in &nodes {
            let subject = format!("{}.{}", state.config.nats_delivery_subject_prefix, node);
            client.publish(subject, payload.clone().into()).await.context("cannot publish targeted node delivery")?;
        }
        Ok(nodes.len())
    }
}

pub async fn run(state: Arc<AppState>) {
    if state.config.connection_ownership_backend.as_str() != "nats" {
        state.ownership_ready.store(true, Ordering::Release);
        return;
    }
    let mut backoff = Duration::from_secs(1);
    loop {
        if state.shutdown.is_cancelled() { return; }
        match connect_and_run(state.clone()).await {
            Ok(()) if state.shutdown.is_cancelled() => return,
            Ok(()) => {},
            Err(error) => warn!(error = %error, "distributed connection ownership stopped"),
        }
        state.ownership_ready.store(false, Ordering::Release);
        state.ownership.set_client(None).await;
        state.ownership_reconnects.fetch_add(1, Ordering::Relaxed);
        tokio::select! {
            _ = state.shutdown.cancelled() => return,
            _ = time::sleep(backoff) => {}
        }
        backoff = (backoff * 2).min(Duration::from_secs(15));
    }
}

async fn connect_and_run(state: Arc<AppState>) -> Result<()> {
    let client = time::timeout(
        state.config.nats_connect_timeout,
        crate::nats_connection::connect(&state.config),
    ).await.context("ownership NATS connection timed out")?
        .context("cannot connect ownership router to NATS")?;

    let mut ownership_sub = client.subscribe(state.config.nats_ownership_subject.clone()).await
        .context("cannot subscribe to ownership snapshots")?;
    let delivery_subject = format!("{}.{}", state.config.nats_delivery_subject_prefix, state.config.nats_node_id);
    let mut delivery_sub = client.subscribe(delivery_subject.clone()).await
        .context("cannot subscribe to targeted node deliveries")?;
    state.ownership.set_client(Some(client.clone())).await;
    state.ownership_ready.store(true, Ordering::Release);
    info!(node_id=%state.config.nats_node_id, delivery_subject=%delivery_subject, "distributed connection ownership ready");

    let mut announce = time::interval(state.config.ownership_announce_interval);
    announce.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            _ = state.shutdown.cancelled() => return Ok(()),
            _ = announce.tick() => publish_snapshot(&state, &client).await?,
            incoming = ownership_sub.next() => {
                let Some(incoming) = incoming else { anyhow::bail!("ownership subscription closed"); };
                let snapshot: OwnershipSnapshot = match serde_json::from_slice(&incoming.payload) {
                    Ok(value) => value,
                    Err(error) => { warn!(error=%error, "ignoring malformed ownership snapshot"); continue; }
                };
                if snapshot.version != 1 || snapshot.node_id == state.config.nats_node_id { continue; }
                apply_snapshot(&state, snapshot);
            }
            incoming = delivery_sub.next() => {
                let Some(incoming) = incoming else { anyhow::bail!("targeted delivery subscription closed"); };
                let delivery: TargetedDelivery = match serde_json::from_slice(&incoming.payload) {
                    Ok(value) => value,
                    Err(error) => { warn!(error=%error, "ignoring malformed targeted delivery"); continue; }
                };
                if delivery.version != 1 || delivery.origin_node_id == state.config.nats_node_id { continue; }
                let message = TextFrame::from(delivery.message);
                match delivery.priority {
                    EphemeralPriority::High => { state.registry.fanout_high_filtered(&delivery.audiences, message, None, delivery.target_actor_id.as_deref()); }
                    EphemeralPriority::Low => { state.registry.fanout_low(&delivery.audiences, message, None, delivery.target_actor_id.as_deref()); }
                }
                state.targeted_deliveries_received.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
}

async fn publish_snapshot(state: &AppState, client: &Client) -> Result<()> {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    let snapshot = OwnershipSnapshot {
        version: 1,
        node_id: state.config.nats_node_id.clone(),
        expires_at_epoch: now + state.config.ownership_lease_ttl.as_secs(),
        audiences: state.registry.audience_snapshot(),
    };
    let payload = serde_json::to_vec(&snapshot).context("cannot encode ownership snapshot")?;
    client.publish(state.config.nats_ownership_subject.clone(), payload.into()).await
        .context("cannot publish ownership snapshot")?;
    state.ownership_snapshots_published.fetch_add(1, Ordering::Relaxed);
    Ok(())
}

fn apply_snapshot(state: &AppState, snapshot: OwnershipSnapshot) {
    let now_epoch = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    if snapshot.expires_at_epoch <= now_epoch { return; }
    let ttl = Duration::from_secs(snapshot.expires_at_epoch - now_epoch);
    let expires = Instant::now() + ttl;
    let mut directory = state.ownership.directory.lock().expect("ownership directory mutex poisoned");
    for nodes in directory.values_mut() { nodes.remove(&snapshot.node_id); }
    directory.retain(|_, nodes| !nodes.is_empty());
    for audience in snapshot.audiences {
        directory.entry(audience).or_default().insert(snapshot.node_id.clone(), expires);
    }
    state.ownership_snapshots_received.fetch_add(1, Ordering::Relaxed);
}
