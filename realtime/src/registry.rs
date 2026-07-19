use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};

use dashmap::{DashMap, DashSet};
use tokio::sync::mpsc::{self, error::TrySendError};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use crate::protocol::{AudienceKey, OutboundMessage};

#[derive(Clone)]
pub struct ConnectionHandle {
    pub actor_id: String,
    pub high_tx: mpsc::Sender<OutboundMessage>,
    pub low_tx: mpsc::Sender<OutboundMessage>,
    pub cancellation: CancellationToken,
    pub subscriptions: Arc<DashSet<AudienceKey>>,
}

#[derive(Default)]
pub struct RegistryStats {
    delivered: AtomicU64,
    dropped_ephemeral: AtomicU64,
    disconnected_slow: AtomicU64,
}

impl RegistryStats {
    pub fn delivered(&self) -> u64 { self.delivered.load(Ordering::Relaxed) }
    pub fn dropped_ephemeral(&self) -> u64 { self.dropped_ephemeral.load(Ordering::Relaxed) }
    pub fn disconnected_slow(&self) -> u64 { self.disconnected_slow.load(Ordering::Relaxed) }
}

pub struct Registry {
    connections: DashMap<Uuid, ConnectionHandle>,
    audiences: DashMap<AudienceKey, Arc<DashSet<Uuid>>>,
    pub stats: RegistryStats,
}

impl Registry {
    pub fn new() -> Self {
        Self {
            connections: DashMap::new(),
            audiences: DashMap::new(),
            stats: RegistryStats::default(),
        }
    }

    pub fn connection_count(&self) -> usize { self.connections.len() }
    pub fn audience_count(&self) -> usize { self.audiences.len() }

    pub fn register(&self, id: Uuid, handle: ConnectionHandle) {
        self.connections.insert(id, handle);
    }

    pub fn subscribe(&self, connection_id: Uuid, audience: AudienceKey) -> bool {
        let Some(connection) = self.connections.get(&connection_id) else { return false; };
        connection.subscriptions.insert(audience.clone());
        drop(connection);
        self.audiences
            .entry(audience.clone())
            .or_insert_with(|| Arc::new(DashSet::new()))
            .insert(connection_id);
        if !self.connections.contains_key(&connection_id) {
            if let Some(members) = self.audiences.get(&audience) {
                members.remove(&connection_id);
            }
            self.audiences.remove_if(&audience, |_, members| members.is_empty());
            return false;
        }
        true
    }

    pub fn is_subscribed(&self, connection_id: Uuid, audience: &AudienceKey) -> bool {
        self.connections
            .get(&connection_id)
            .map(|connection| connection.subscriptions.contains(audience))
            .unwrap_or(false)
    }

    pub fn unsubscribe(&self, connection_id: Uuid, audience: &AudienceKey) -> bool {
        if let Some(connection) = self.connections.get(&connection_id) {
            connection.subscriptions.remove(audience);
        }
        let removed = self
            .audiences
            .get(audience)
            .map(|members| members.remove(&connection_id).is_some())
            .unwrap_or(false);
        if removed {
            self.audiences.remove_if(audience, |_, members| members.is_empty());
        }
        removed
    }

    pub fn remove(&self, connection_id: Uuid) {
        let Some((_, connection)) = self.connections.remove(&connection_id) else { return; };
        let subscriptions: Vec<AudienceKey> = connection
            .subscriptions
            .iter()
            .map(|entry| entry.key().clone())
            .collect();
        for audience in subscriptions {
            let removed = self
                .audiences
                .get(&audience)
                .map(|members| members.remove(&connection_id).is_some())
                .unwrap_or(false);
            if removed {
                self.audiences.remove_if(&audience, |_, members| members.is_empty());
            }
        }
    }

    pub fn send_high(&self, connection_id: Uuid, message: OutboundMessage) -> bool {
        let Some(connection) = self.connections.get(&connection_id) else { return false; };
        match connection.high_tx.try_send(message) {
            Ok(()) => {
                self.stats.delivered.fetch_add(1, Ordering::Relaxed);
                true
            }
            Err(TrySendError::Full(_)) => {
                self.stats.disconnected_slow.fetch_add(1, Ordering::Relaxed);
                connection.cancellation.cancel();
                false
            }
            Err(TrySendError::Closed(_)) => false,
        }
    }

    pub fn send_low(&self, connection_id: Uuid, message: OutboundMessage) -> bool {
        let Some(connection) = self.connections.get(&connection_id) else { return false; };
        match connection.low_tx.try_send(message) {
            Ok(()) => true,
            Err(TrySendError::Full(_)) => {
                self.stats.dropped_ephemeral.fetch_add(1, Ordering::Relaxed);
                false
            }
            Err(TrySendError::Closed(_)) => false,
        }
    }

    fn recipient_ids(
        &self,
        audiences: &[AudienceKey],
        exclude: Option<Uuid>,
        target_actor_id: Option<&str>,
    ) -> Vec<Uuid> {
        let recipients = DashSet::new();
        for audience in audiences {
            if let Some(members) = self.audiences.get(audience) {
                for member in members.iter() {
                    let id = *member.key();
                    if exclude == Some(id) { continue; }
                    if let Some(target) = target_actor_id {
                        let matches = self
                            .connections
                            .get(&id)
                            .map(|connection| connection.actor_id == target)
                            .unwrap_or(false);
                        if !matches { continue; }
                    }
                    recipients.insert(id);
                }
            }
        }
        recipients.iter().map(|value| *value.key()).collect()
    }

    pub fn fanout_high(&self, audiences: &[AudienceKey], message: Arc<str>) -> usize {
        self.fanout_high_filtered(audiences, message, None, None)
    }

    pub fn fanout_high_filtered(
        &self,
        audiences: &[AudienceKey],
        message: Arc<str>,
        exclude: Option<Uuid>,
        target_actor_id: Option<&str>,
    ) -> usize {
        let recipients = self.recipient_ids(audiences, exclude, target_actor_id);
        recipients
            .into_iter()
            .filter(|id| self.send_high(*id, OutboundMessage::Text(message.clone())))
            .count()
    }

    pub fn fanout_low(
        &self,
        audiences: &[AudienceKey],
        message: Arc<str>,
        exclude: Option<Uuid>,
        target_actor_id: Option<&str>,
    ) -> usize {
        let recipients = self.recipient_ids(audiences, exclude, target_actor_id);
        recipients
            .into_iter()
            .filter(|id| self.send_low(*id, OutboundMessage::Text(message.clone())))
            .count()
    }
}
