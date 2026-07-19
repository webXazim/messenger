use std::sync::Arc;

use dashmap::{mapref::entry::Entry, DashMap};

use crate::auth::{ActorType, AuthenticatedSession};

pub struct SessionLimiter {
    actors: DashMap<String, usize>,
    devices: DashMap<String, usize>,
}

pub struct SessionPermit {
    limiter: Arc<SessionLimiter>,
    actor_key: String,
    device_key: String,
}

impl SessionLimiter {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            actors: DashMap::new(),
            devices: DashMap::new(),
        })
    }

    pub fn try_acquire(
        self: &Arc<Self>,
        session: &AuthenticatedSession,
        max_user_connections: usize,
        max_widget_connections: usize,
        max_device_connections: usize,
    ) -> Option<SessionPermit> {
        let actor_limit = match session.actor_type {
            ActorType::User => max_user_connections,
            ActorType::SupportWidget => max_widget_connections,
            ActorType::InternalTest => max_device_connections,
        };
        let actor_key = format!("{}:{}", session.actor_type.as_str(), session.actor_id);
        let normalized_device = if session.device_id.trim().is_empty() {
            "unknown"
        } else {
            session.device_id.trim()
        };
        let device_key = format!("{}:{}", actor_key, normalized_device);

        if !increment(&self.actors, &actor_key, actor_limit) {
            return None;
        }
        if !increment(&self.devices, &device_key, max_device_connections) {
            decrement(&self.actors, &actor_key);
            return None;
        }

        Some(SessionPermit {
            limiter: self.clone(),
            actor_key,
            device_key,
        })
    }

    #[cfg(test)]
    fn tracked_keys(&self) -> (usize, usize) {
        (self.actors.len(), self.devices.len())
    }
}

impl Drop for SessionPermit {
    fn drop(&mut self) {
        decrement(&self.limiter.devices, &self.device_key);
        decrement(&self.limiter.actors, &self.actor_key);
    }
}

fn increment(map: &DashMap<String, usize>, key: &str, limit: usize) -> bool {
    match map.entry(key.to_owned()) {
        Entry::Occupied(mut entry) => {
            if *entry.get() >= limit {
                return false;
            }
            *entry.get_mut() += 1;
            true
        }
        Entry::Vacant(entry) => {
            entry.insert(1);
            true
        }
    }
}

fn decrement(map: &DashMap<String, usize>, key: &str) {
    if let Entry::Occupied(mut entry) = map.entry(key.to_owned()) {
        if *entry.get() <= 1 {
            entry.remove();
        } else {
            *entry.get_mut() -= 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::AuthenticatedSession;

    #[test]
    fn releases_actor_and_device_entries() {
        let limiter = SessionLimiter::new();
        let session = AuthenticatedSession::internal_test();
        let permit = limiter.try_acquire(&session, 2, 2, 1).expect("permit");
        assert_eq!(limiter.tracked_keys(), (1, 1));
        assert!(limiter.try_acquire(&session, 2, 2, 1).is_none());
        drop(permit);
        assert_eq!(limiter.tracked_keys(), (0, 0));
    }
}
