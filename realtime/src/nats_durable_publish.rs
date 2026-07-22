use std::{sync::Arc, time::Duration};

use anyhow::{Context, Result};
use async_nats::{jetstream, Client};
use serde_json::json;
use tokio::{sync::RwLock, time};

use crate::{database::CommittedEvent, state::AppState};

#[derive(Default)]
pub struct DurableNatsBus {
    client: RwLock<Option<Client>>,
}

#[derive(Clone, Copy, Debug)]
pub struct DurablePublishAck {
    pub sequence: u64,
    pub duplicate: bool,
}

impl DurableNatsBus {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub async fn set_client(&self, client: Option<Client>) {
        *self.client.write().await = client;
    }

    async fn client(&self) -> Result<Client> {
        self.client
            .read()
            .await
            .clone()
            .context("durable NATS transport is not connected")
    }

    pub async fn publish(
        &self,
        state: &AppState,
        event: &CommittedEvent,
    ) -> Result<DurablePublishAck> {
        let client = self.client().await?;
        let context = jetstream::new(client);
        let subject = subject_for(
            &state.config.nats_durable_subject_prefix,
            &event.event_name,
        );
        let occurred_at = event
            .payload
            .get("occurred_at")
            .cloned()
            .unwrap_or(serde_json::Value::Null);
        let envelope = json!({
            "schema_version": 1,
            "event_id": event.event_id.to_string(),
            "event_name": event.event_name.clone(),
            "occurred_at": occurred_at,
            "audiences": event.audiences.clone(),
            "payload": event.payload.clone(),
        });
        let bytes = serde_json::to_vec(&envelope)
            .context("cannot encode durable JetStream envelope")?;

        let publish = async_nats::jetstream::context::Publish::build()
            .payload(bytes.into())
            .message_id(event.event_id.to_string());
        let ack_future = time::timeout(
            state.config.outbox_publish_timeout,
            context.send_publish(subject, publish),
        )
        .await
        .context("JetStream publish request timed out")?
        .context("JetStream publish request failed")?;
        let ack = time::timeout(state.config.outbox_publish_timeout, ack_future)
            .await
            .context("JetStream publish acknowledgement timed out")?
            .context("JetStream publish acknowledgement failed")?;

        Ok(DurablePublishAck {
            sequence: ack.sequence,
            duplicate: ack.duplicate,
        })
    }
}

pub fn subject_for(prefix: &str, event_name: &str) -> String {
    let mut token = String::with_capacity(event_name.len());
    let mut previous_was_separator = false;
    for character in event_name.chars() {
        if character.is_ascii_alphanumeric() || character == '_' || character == '-' {
            token.push(character.to_ascii_lowercase());
            previous_was_separator = false;
        } else if !previous_was_separator && !token.is_empty() {
            token.push('.');
            previous_was_separator = true;
        }
    }
    while token.ends_with('.') {
        token.pop();
    }
    if token.is_empty() {
        token.push_str("unknown");
    }
    format!("{}.{}", prefix.trim_end_matches('.'), token)
}

pub async fn wait_before_mark_retry(attempt: usize) {
    let millis = match attempt {
        0 => 20,
        1 => 75,
        _ => 200,
    };
    time::sleep(Duration::from_millis(millis)).await;
}

#[cfg(test)]
mod tests {
    use super::subject_for;

    #[test]
    fn durable_subject_matches_django_normalization() {
        assert_eq!(subject_for("event.chat", "message.created"), "event.chat.message.created");
        assert_eq!(subject_for("event.chat.", "Call / Ended"), "event.chat.call.ended");
        assert_eq!(subject_for("event.chat", "***"), "event.chat.unknown");
        assert_eq!(subject_for("event.chat", "read_receipt-updated"), "event.chat.read_receipt-updated");
    }
}
