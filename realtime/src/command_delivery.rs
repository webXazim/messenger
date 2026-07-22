use std::sync::Arc;

use crate::{database::CommittedEvent, protocol::TextFrame, state::AppState};

/// Deliver a transactionally committed event immediately on this Axum node.
/// The PostgreSQL outbox remains the durable cross-node/restart path. Remembering
/// the event id prevents this node from delivering it twice when JetStream sees it.
pub async fn deliver_committed(state: &Arc<AppState>, events: &[CommittedEvent]) {
    for event in events {
        let Ok(encoded) = serde_json::to_string(&event.payload) else {
            tracing::error!(event_id=%event.event_id, "cannot encode committed realtime event");
            continue;
        };
        state.event_deduper.remember(&event.event_id.to_string());
        state.registry.fanout_high(&event.audiences, TextFrame::from(encoded));
    }
}
