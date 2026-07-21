use std::{
    sync::{atomic::{AtomicBool, AtomicU64}, Arc},
    time::{SystemTime, UNIX_EPOCH},
};

use tokio::sync::Semaphore;
use tokio_util::sync::CancellationToken;

use crate::{
    auth::Authenticator,
    command_auth::CommandAuthenticator,
    config::Config,
    database::Database,
    nats_jetstream::EventDeduper,
    nats_core::CoreNatsBus,
    ownership::OwnershipRouter,
    presence::PresenceStore,
    registry::Registry,
    session_limit::SessionLimiter,
};

pub struct AppState {
    pub config: Arc<Config>,
    pub registry: Arc<Registry>,
    pub auth: Option<Arc<Authenticator>>,
    pub command_auth: Arc<CommandAuthenticator>,
    pub presence: Arc<PresenceStore>,
    pub database: Arc<Database>,
    pub connection_slots: Arc<Semaphore>,
    pub session_limiter: Arc<SessionLimiter>,
    pub shutdown: CancellationToken,
    pub stream_ready: AtomicBool,
    pub nats_ready: AtomicBool,
    pub ephemeral_ready: AtomicBool,
    pub core_nats: Arc<CoreNatsBus>,
    pub ownership: Arc<OwnershipRouter>,
    pub ownership_ready: AtomicBool,
    pub event_deduper: EventDeduper,
    pub stream_events: AtomicU64,
    pub stream_acks: AtomicU64,
    pub stream_errors: AtomicU64,
    pub stream_reconnects: AtomicU64,
    pub malformed_stream_events: AtomicU64,
    pub ephemeral_events: AtomicU64,
    pub ephemeral_published: AtomicU64,
    pub ephemeral_errors: AtomicU64,
    pub ephemeral_reconnects: AtomicU64,
    pub ownership_snapshots_published: AtomicU64,
    pub ownership_snapshots_received: AtomicU64,
    pub ownership_reconnects: AtomicU64,
    pub targeted_deliveries_published: AtomicU64,
    pub targeted_deliveries_received: AtomicU64,
    pub connections_accepted: AtomicU64,
    pub connections_rejected: AtomicU64,
    pub rate_limited_events: AtomicU64,
    pub started_at_epoch: u64,
}

impl AppState {
    pub fn new(config: Config) -> anyhow::Result<Arc<Self>> {
        let auth = Authenticator::from_config(&config)?;
        let command_auth = CommandAuthenticator::from_config(&config)?;
        let presence = Arc::new(PresenceStore::new(&config)?);
        let database = Database::from_config(&config)?;
        let event_dedupe_capacity = config.event_dedupe_capacity;
        Ok(Arc::new(Self {
            connection_slots: Arc::new(Semaphore::new(config.max_connections)),
            config: Arc::new(config),
            registry: Arc::new(Registry::new()),
            session_limiter: SessionLimiter::new(),
            auth,
            command_auth,
            presence,
            database,
            shutdown: CancellationToken::new(),
            stream_ready: AtomicBool::new(false),
            nats_ready: AtomicBool::new(false),
            ephemeral_ready: AtomicBool::new(false),
            core_nats: CoreNatsBus::new(),
            ownership: OwnershipRouter::new(),
            ownership_ready: AtomicBool::new(false),
            event_deduper: EventDeduper::new(event_dedupe_capacity),
            stream_events: AtomicU64::new(0),
            stream_acks: AtomicU64::new(0),
            stream_errors: AtomicU64::new(0),
            stream_reconnects: AtomicU64::new(0),
            malformed_stream_events: AtomicU64::new(0),
            ephemeral_events: AtomicU64::new(0),
            ephemeral_published: AtomicU64::new(0),
            ephemeral_errors: AtomicU64::new(0),
            ephemeral_reconnects: AtomicU64::new(0),
            ownership_snapshots_published: AtomicU64::new(0),
            ownership_snapshots_received: AtomicU64::new(0),
            ownership_reconnects: AtomicU64::new(0),
            targeted_deliveries_published: AtomicU64::new(0),
            targeted_deliveries_received: AtomicU64::new(0),
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
