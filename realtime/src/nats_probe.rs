use std::{sync::{atomic::Ordering, Arc}, time::Duration};

use crate::state::AppState;

pub async fn run(state: Arc<AppState>) {
    if !state.config.nats_probe_enabled {
        tracing::info!("NATS connectivity probe disabled; active transport readiness is handled by the configured realtime backend");
        return;
    }

    let mut delay = Duration::from_secs(1);
    loop {
        if state.shutdown.is_cancelled() {
            return;
        }

        let connect = tokio::time::timeout(
            state.config.nats_connect_timeout,
            async_nats::connect(state.config.nats_url.clone()),
        ).await;

        match connect {
            Ok(Ok(client)) => {
                if client.flush().await.is_ok() {
                    state.nats_ready.store(true, Ordering::Release);
                    tracing::info!(url = %state.config.nats_url, "NATS connectivity probe ready");
                    delay = Duration::from_secs(1);
                    tokio::select! {
                        _ = state.shutdown.cancelled() => return,
                        _ = tokio::time::sleep(Duration::from_secs(15)) => {}
                    }
                    continue;
                }
            }
            Ok(Err(error)) => tracing::warn!(error = ?error, "NATS connectivity probe failed"),
            Err(_) => tracing::warn!("NATS connectivity probe timed out"),
        }

        state.nats_ready.store(false, Ordering::Release);
        tokio::select! {
            _ = state.shutdown.cancelled() => return,
            _ = tokio::time::sleep(delay) => {}
        }
        delay = (delay * 2).min(Duration::from_secs(30));
    }
}
