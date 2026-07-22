use std::sync::{atomic::Ordering, Arc};

use tokio::time;

use crate::{
    config::OutboxPublisherBackend,
    database::CommittedEvent,
    nats_durable_publish::wait_before_mark_retry,
    protocol::TextFrame,
    state::AppState,
};

/// Publish transactionally committed events directly to JetStream when enabled,
/// mark their PostgreSQL outbox rows after the server acknowledgement, and
/// preserve immediate local delivery. Celery remains the recovery path for any
/// row that stays pending or failed.
pub async fn deliver_committed(state: &Arc<AppState>, events: &[CommittedEvent]) {
    for event in events {
        // Preserve same-node responsiveness even when JetStream is slow or
        // temporarily unavailable. The shared event deduper prevents the local
        // consumer from delivering the same published event twice.
        deliver_local_once(state, event);

        if state.config.outbox_publisher_backend == OutboxPublisherBackend::Axum {
            state.direct_outbox_attempts.fetch_add(1, Ordering::Relaxed);
            match state.durable_nats.publish(state, event).await {
                Ok(ack) => {
                    if ack.duplicate {
                        state.direct_outbox_duplicates.fetch_add(1, Ordering::Relaxed);
                    }
                    let mut marked = false;
                    for attempt in 0..state.config.outbox_mark_attempts {
                        if time::timeout(
                            state.config.outbox_publish_timeout,
                            state.database.mark_outbox_published(event.event_id, ack.sequence),
                        )
                        .await
                        .is_ok_and(|result| result.is_ok())
                        {
                            marked = true;
                            break;
                        }
                        if attempt + 1 < state.config.outbox_mark_attempts {
                            wait_before_mark_retry(attempt).await;
                        }
                    }
                    if marked {
                        state.direct_outbox_published.fetch_add(1, Ordering::Relaxed);
                    } else {
                        state
                            .direct_outbox_mark_failures
                            .fetch_add(1, Ordering::Relaxed);
                        tracing::error!(
                            event_id = %event.event_id,
                            sequence = ack.sequence,
                            "JetStream acknowledged the event but the outbox row could not be marked; Celery recovery may republish the deterministic event id"
                        );
                    }
                }
                Err(error) => {
                    state.direct_outbox_failures.fetch_add(1, Ordering::Relaxed);
                    let error_text = format!("Axum direct JetStream publish failed: {error}");
                    match time::timeout(
                        state.config.outbox_publish_timeout,
                        state.database.mark_outbox_publish_failed(event.event_id, &error_text),
                    )
                    .await
                    {
                        Ok(Ok(())) => {}
                        Ok(Err(mark_error)) => tracing::error!(
                            event_id = %event.event_id,
                            error = %mark_error,
                            "could not mark failed direct outbox publication"
                        ),
                        Err(_) => tracing::error!(
                            event_id = %event.event_id,
                            "timed out while marking failed direct outbox publication"
                        ),
                    }
                    tracing::warn!(
                        event_id = %event.event_id,
                        error = %error,
                        "direct JetStream publish failed; local delivery is preserved and Celery recovery will retry"
                    );
                }
            }
        }
    }
}

fn deliver_local_once(state: &AppState, event: &CommittedEvent) {
    let event_id = event.event_id.to_string();
    if !state.event_deduper.remember(&event_id) {
        tracing::debug!(event_id = %event.event_id, "committed event already delivered by JetStream consumer");
        return;
    }
    let Ok(encoded) = serde_json::to_string(&event.payload) else {
        tracing::error!(event_id = %event.event_id, "cannot encode committed realtime event");
        return;
    };
    state
        .registry
        .fanout_high(&event.audiences, TextFrame::from(encoded));
}

#[cfg(test)]
mod tests {
    use crate::nats_jetstream::EventDeduper;

    #[test]
    fn local_and_consumer_delivery_share_the_same_event_guard() {
        let deduper = EventDeduper::new(8);
        assert!(deduper.remember("event-1"));
        assert!(!deduper.remember("event-1"));
    }
}
