use std::{
    collections::VecDeque,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};

use dashmap::DashMap;
use serde_json::Value;
use uuid::Uuid;

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct SignalKey {
    call_id: Uuid,
    actor_id: String,
}

#[derive(Clone, Debug)]
struct QueuedSignal {
    dedupe_key: String,
    payload: Value,
    expires_at: Instant,
}

#[derive(Debug)]
struct RateWindow {
    started_at: Instant,
    count: u32,
}

#[derive(Default)]
pub struct SupportSignalStore {
    queues: DashMap<SignalKey, Arc<Mutex<VecDeque<QueuedSignal>>>>,
    rate_windows: DashMap<String, Arc<Mutex<RateWindow>>>,
    pushes: AtomicU64,
}

impl SupportSignalStore {
    pub fn new() -> Arc<Self> { Arc::new(Self::default()) }

    pub fn allow_rate(&self, actor_id: &str, limit: u32, window: Duration) -> bool {
        if actor_id.trim().is_empty() || limit == 0 { return false; }
        let now = Instant::now();
        let entry = self.rate_windows.entry(actor_id.to_owned()).or_insert_with(|| {
            Arc::new(Mutex::new(RateWindow { started_at: now, count: 0 }))
        }).clone();
        let Ok(mut state) = entry.lock() else { return false; };
        if now.duration_since(state.started_at) >= window {
            state.started_at = now;
            state.count = 0;
        }
        if state.count >= limit { return false; }
        state.count += 1;
        true
    }

    pub fn push(
        &self,
        call_id: Uuid,
        actor_id: &str,
        dedupe_key: &str,
        payload: Value,
        ttl: Duration,
        capacity: usize,
    ) -> bool {
        if actor_id.trim().is_empty() { return false; }
        let key = SignalKey { call_id, actor_id: actor_id.to_owned() };
        let queue_arc = self.queues.entry(key).or_insert_with(|| Arc::new(Mutex::new(VecDeque::new()))).clone();
        let Ok(mut queue) = queue_arc.lock() else { return false; };
        let now = Instant::now();
        while queue.front().is_some_and(|item| item.expires_at <= now) { queue.pop_front(); }
        if queue.iter().any(|item| item.dedupe_key == dedupe_key) { return false; }
        while queue.len() >= capacity.max(1) { queue.pop_front(); }
        queue.push_back(QueuedSignal {
            dedupe_key: dedupe_key.to_owned(),
            payload,
            expires_at: now + ttl,
        });
        drop(queue);
        if self.pushes.fetch_add(1, Ordering::Relaxed) % 256 == 0 { self.prune_expired(now); }
        true
    }

    pub fn pop_all(&self, call_id: Uuid, actor_id: &str) -> Vec<Value> {
        let key = SignalKey { call_id, actor_id: actor_id.to_owned() };
        let Some(queue_arc) = self.queues.get(&key).map(|entry| entry.clone()) else { return Vec::new(); };
        let Ok(mut queue) = queue_arc.lock() else { return Vec::new(); };
        let now = Instant::now();
        let mut result = Vec::with_capacity(queue.len());
        while let Some(item) = queue.pop_front() {
            if item.expires_at > now { result.push(item.payload); }
        }
        drop(queue);
        drop(queue_arc);
        self.queues.remove_if(&key, |_, queue| {
            Arc::strong_count(queue) == 1 && queue.lock().map(|queue| queue.is_empty()).unwrap_or(false)
        });
        result
    }

    fn prune_expired(&self, now: Instant) {
        let stale = self.queues.iter().filter_map(|entry| {
            let Ok(mut queue) = entry.value().lock() else { return Some(entry.key().clone()); };
            while queue.front().is_some_and(|item| item.expires_at <= now) { queue.pop_front(); }
            queue.is_empty().then(|| entry.key().clone())
        }).collect::<Vec<_>>();
        for key in stale {
            self.queues.remove_if(&key, |_, queue| {
                Arc::strong_count(queue) == 1 && queue.lock().map(|queue| queue.is_empty()).unwrap_or(false)
            });
        }
    }

    pub fn ingest_event_message(
        &self,
        message: &str,
        target_actor_id: Option<&str>,
        ttl: Duration,
        capacity: usize,
    ) {
        let Some(actor_id) = target_actor_id.filter(|value| !value.trim().is_empty()) else { return; };
        let Ok(envelope) = serde_json::from_str::<Value>(message) else { return; };
        if envelope.get("event").and_then(Value::as_str) != Some("support.call.signal") { return; }
        let Some(data) = envelope.get("data") else { return; };
        let Some(call_id) = data.get("call_id").and_then(Value::as_str).and_then(|value| value.parse::<Uuid>().ok()) else { return; };
        let Some(signal_id) = data.get("signal_id").and_then(Value::as_str) else { return; };
        let sender = data.get("sender_actor_id").and_then(Value::as_str).unwrap_or("unknown");
        self.push(call_id, actor_id, &format!("{sender}:{signal_id}"), data.clone(), ttl, capacity);
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;
    use serde_json::json;
    use uuid::Uuid;
    use super::SupportSignalStore;

    #[test]
    fn supports_uuid_and_numeric_actor_keys() {
        let store = SupportSignalStore::new();
        let call_id = Uuid::new_v4();
        assert!(store.push(call_id, "visitor:abc", "team:1", json!({"signal_id":"1"}), Duration::from_secs(60), 2));
        assert!(!store.push(call_id, "visitor:abc", "team:1", json!({"signal_id":"1"}), Duration::from_secs(60), 2));
        assert_eq!(store.pop_all(call_id, "visitor:abc").len(), 1);
    }

    #[test]
    fn rate_window_rejects_excess_signals() {
        let store = SupportSignalStore::new();
        assert!(store.allow_rate("visitor:1", 2, Duration::from_secs(60)));
        assert!(store.allow_rate("visitor:1", 2, Duration::from_secs(60)));
        assert!(!store.allow_rate("visitor:1", 2, Duration::from_secs(60)));
        assert!(store.allow_rate("visitor:2", 2, Duration::from_secs(60)));
    }

    #[test]
    fn bounded_queue_keeps_newest() {
        let store = SupportSignalStore::new();
        let call_id = Uuid::new_v4();
        for index in 0..4 {
            assert!(store.push(call_id, "team:9", &format!("visitor:{index}"), json!({"index":index}), Duration::from_secs(60), 2));
        }
        assert_eq!(store.pop_all(call_id, "team:9"), vec![json!({"index":2}), json!({"index":3})]);
    }
}
