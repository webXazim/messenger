use std::{
    sync::{atomic::{AtomicBool, AtomicU64}, Arc},
    time::{SystemTime, UNIX_EPOCH},
};

use tokio::sync::Semaphore;
use tokio_util::sync::CancellationToken;

use crate::{
    auth::Authenticator,
    config::Config,
    presence::PresenceStore,
    registry::Registry,
    session_limit::SessionLimiter,
};

pub struct AppState {
    pub config: Arc<Config>,
    pub registry: Arc<Registry>,
    pub auth: Option<Arc<Authenticator>>,
    pub presence: Arc<PresenceStore>,
    pub connection_slots: Arc<Semaphore>,
    pub session_limiter: Arc<SessionLimiter>,
    pub shutdown: CancellationToken,
    pub stream_ready: AtomicBool,
    pub stream_events: AtomicU64,
    pub stream_acks: AtomicU64,
    pub stream_errors: AtomicU64,
    pub stream_reconnects: AtomicU64,
    pub malformed_stream_events: AtomicU64,
    pub connections_accepted: AtomicU64,
    pub connections_rejected: AtomicU64,
    pub rate_limited_events: AtomicU64,
    pub started_at_epoch: u64,
}

impl AppState {
    pub fn new(config: Config) -> anyhow::Result<Arc<Self>> {
        let auth = Authenticator::from_config(&config)?;
        let presence = Arc::new(PresenceStore::new(&config)?);
        Ok(Arc::new(Self {
            connection_slots: Arc::new(Semaphore::new(config.max_connections)),
            config: Arc::new(config),
            registry: Arc::new(Registry::new()),
            session_limiter: SessionLimiter::new(),
            auth,
            presence,
            shutdown: CancellationToken::new(),
            stream_ready: AtomicBool::new(false),
            stream_events: AtomicU64::new(0),
            stream_acks: AtomicU64::new(0),
            stream_errors: AtomicU64::new(0),
            stream_reconnects: AtomicU64::new(0),
            malformed_stream_events: AtomicU64::new(0),
            connections_accepted: AtomicU64::new(0),
            connections_rejected: AtomicU64::new(0),
            rate_limited_events: AtomicU64::new(0),
            started_at_epoch: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
        }))
    }
}
