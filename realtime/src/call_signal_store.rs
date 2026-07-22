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

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct SignalKey {
    call_id: Uuid,
    user_id: i64,
}

#[derive(Clone, Debug)]
struct QueuedSignal {
    signal_id: String,
    payload: Value,
    expires_at: Instant,
}

#[derive(Default)]
pub struct CallSignalStore {
    queues: DashMap<SignalKey, Arc<Mutex<VecDeque<QueuedSignal>>>>,
    pushes: AtomicU64,
}

impl CallSignalStore {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub fn push(
        &self,
        call_id: Uuid,
        user_id: i64,
        signal_id: &str,
        payload: Value,
        ttl: Duration,
        capacity: usize,
    ) -> bool {
        let key = SignalKey { call_id, user_id };
        let queue_arc = self
            .queues
            .entry(key)
            .or_insert_with(|| Arc::new(Mutex::new(VecDeque::new())))
            .clone();
        let Ok(mut queue) = queue_arc.lock() else { return false; };
        let now = Instant::now();
        while queue.front().is_some_and(|item| item.expires_at <= now) {
            queue.pop_front();
        }
        if queue.iter().any(|item| item.signal_id == signal_id) {
            return false;
        }
        while queue.len() >= capacity.max(1) {
            queue.pop_front();
        }
        queue.push_back(QueuedSignal {
            signal_id: signal_id.to_owned(),
            payload,
            expires_at: now + ttl,
        });
        drop(queue);
        if self.pushes.fetch_add(1, Ordering::Relaxed) % 256 == 0 {
            self.prune_expired(now);
        }
        true
    }

    pub fn pop_all(&self, call_id: Uuid, user_id: i64) -> Vec<Value> {
        let key = SignalKey { call_id, user_id };
        let Some(queue_arc) = self.queues.get(&key).map(|entry| entry.clone()) else {
            return Vec::new();
        };
        let Ok(mut queue) = queue_arc.lock() else { return Vec::new(); };
        let now = Instant::now();
        let mut result = Vec::with_capacity(queue.len());
        while let Some(item) = queue.pop_front() {
            if item.expires_at > now {
                result.push(item.payload);
            }
        }
        drop(queue);
        drop(queue_arc);
        self.queues.remove_if(&key, |_, queue| {
            Arc::strong_count(queue) == 1
                && queue.lock().map(|queue| queue.is_empty()).unwrap_or(false)
        });
        result
    }

    fn prune_expired(&self, now: Instant) {
        let stale_keys = self.queues.iter().filter_map(|entry| {
            let Ok(mut queue) = entry.value().lock() else { return Some(*entry.key()); };
            while queue.front().is_some_and(|item| item.expires_at <= now) {
                queue.pop_front();
            }
            queue.is_empty().then_some(*entry.key())
        }).collect::<Vec<_>>();
        for key in stale_keys {
            self.queues.remove_if(&key, |_, queue| {
                Arc::strong_count(queue) == 1
                    && queue.lock().map(|queue| queue.is_empty()).unwrap_or(false)
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
        let Some(user_id) = target_actor_id.and_then(|value| value.parse::<i64>().ok()) else {
            return;
        };
        let Ok(envelope) = serde_json::from_str::<Value>(message) else { return; };
        if envelope.get("event").and_then(Value::as_str) != Some("call.signal") {
            return;
        }
        let Some(data) = envelope.get("data") else { return; };
        let Some(call_id) = data
            .get("call_id")
            .and_then(Value::as_str)
            .and_then(|value| value.parse::<Uuid>().ok())
        else {
            return;
        };
        let Some(signal_id) = data.get("signal_id").and_then(Value::as_str) else {
            return;
        };
        let sender_id = data.get("from_user_id").and_then(Value::as_str).unwrap_or("unknown");
        let dedupe_key = format!("{sender_id}:{signal_id}");
        self.push(call_id, user_id, &dedupe_key, data.clone(), ttl, capacity);
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use serde_json::json;
    use uuid::Uuid;

    use super::CallSignalStore;

    #[test]
    fn deduplicates_and_consumes_signals() {
        let store = CallSignalStore::new();
        let call_id = Uuid::new_v4();
        let payload = json!({"signal_id":"signal-1"});

        assert!(store.push(call_id, 7, "3:signal-1", payload.clone(), Duration::from_secs(60), 16));
        assert!(!store.push(call_id, 7, "3:signal-1", payload, Duration::from_secs(60), 16));
        assert_eq!(store.pop_all(call_id, 7).len(), 1);
        assert!(store.pop_all(call_id, 7).is_empty());
    }

    #[test]
    fn bounded_queue_keeps_the_newest_signals() {
        let store = CallSignalStore::new();
        let call_id = Uuid::new_v4();
        for index in 0..4 {
            assert!(store.push(
                call_id,
                9,
                &format!("2:signal-{index}"),
                json!({"index":index}),
                Duration::from_secs(60),
                2,
            ));
        }
        let signals = store.pop_all(call_id, 9);
        assert_eq!(signals, vec![json!({"index":2}), json!({"index":3})]);
    }
}
