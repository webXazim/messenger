use std::{
    sync::{
        atomic::Ordering,
        Arc,
    },
    time::Duration,
};

use anyhow::{Context, Result};
use redis::{
    streams::{StreamId, StreamReadOptions, StreamReadReply},
    AsyncCommands, RedisError,
};
use tokio::time;

use crate::{protocol::AudienceKey, state::AppState};

pub async fn run(state: Arc<AppState>) {
    let mut backoff = Duration::from_secs(1);

    loop {
        if state.shutdown.is_cancelled() {
            return;
        }

        match consume(state.clone()).await {
            Ok(()) => return,
            Err(error) => {
                state.stream_ready.store(false, Ordering::Release);
                state.stream_errors.fetch_add(1, Ordering::Relaxed);
                state.stream_reconnects.fetch_add(1, Ordering::Relaxed);
                tracing::error!(error = %error, "Redis Stream consumer stopped");
                tokio::select! {
                    _ = state.shutdown.cancelled() => return,
                    _ = time::sleep(backoff) => {}
                }
                backoff = (backoff * 2).min(Duration::from_secs(30));
            }
        }
    }
}

async fn consume(state: Arc<AppState>) -> Result<()> {
    let client = redis::Client::open(state.config.redis_url.clone())
        .context("invalid REALTIME_STREAM_URL")?;
    let mut connection = client
        .get_multiplexed_async_connection()
        .await
        .context("cannot connect to Redis")?;

    ensure_group(&mut connection, &state).await?;
    state.stream_ready.store(true, Ordering::Release);
    tracing::info!(
        stream = %state.config.stream_name,
        group = %state.config.stream_group,
        consumer = %state.config.stream_consumer,
        "Redis Stream consumer ready"
    );

    drain_pending(&mut connection, state.clone()).await?;

    loop {
        tokio::select! {
            _ = state.shutdown.cancelled() => return Ok(()),
            result = read_once(&mut connection, state.clone(), ">") => {
                result?;
            }
        }
    }
}

async fn ensure_group<C>(connection: &mut C, state: &AppState) -> Result<()>
where
    C: redis::aio::ConnectionLike + Send + Unpin,
{
    let result: redis::RedisResult<String> = redis::cmd("XGROUP")
        .arg("CREATE")
        .arg(&state.config.stream_name)
        .arg(&state.config.stream_group)
        .arg("0")
        .arg("MKSTREAM")
        .query_async(connection)
        .await;

    match result {
        Ok(_) => Ok(()),
        Err(error) if is_busy_group(&error) => Ok(()),
        Err(error) => Err(error).context("cannot create Redis Stream consumer group"),
    }
}

fn is_busy_group(error: &RedisError) -> bool {
    error.to_string().contains("BUSYGROUP")
}

async fn drain_pending<C>(connection: &mut C, state: Arc<AppState>) -> Result<()>
where
    C: redis::aio::ConnectionLike + Send + Unpin,
{
    loop {
        let processed = read_once(connection, state.clone(), "0").await?;
        if processed == 0 {
            return Ok(());
        }
    }
}

async fn read_once<C>(connection: &mut C, state: Arc<AppState>, id: &str) -> Result<usize>
where
    C: redis::aio::ConnectionLike + Send + Unpin,
{
    let options = StreamReadOptions::default()
        .group(&state.config.stream_group, &state.config.stream_consumer)
        .count(state.config.stream_batch_size)
        .block(state.config.stream_block_ms);

    let reply: Option<StreamReadReply> = connection
        .xread_options(&[&state.config.stream_name], &[id], &options)
        .await
        .context("XREADGROUP failed")?;
    let Some(reply) = reply else {
        return Ok(0);
    };

    let mut processed = 0;
    for stream_key in reply.keys {
        for entry in stream_key.ids {
            processed += 1;
            if let Err(error) = process_entry(&state, &entry) {
                state.malformed_stream_events.fetch_add(1, Ordering::Relaxed);
                tracing::error!(stream_id = %entry.id, error = %error, "dropping malformed stream event");
            }
            let _: usize = connection
                .xack(
                    &state.config.stream_name,
                    &state.config.stream_group,
                    &[entry.id.as_str()],
                )
                .await
                .context("XACK failed")?;
            state.stream_acks.fetch_add(1, Ordering::Relaxed);
        }
    }
    Ok(processed)
}

fn process_entry(state: &AppState, entry: &StreamId) -> Result<()> {
    let payload: String = entry
        .get("payload")
        .context("missing or invalid stream field: payload")?;
    let audiences_json: String = entry
        .get("audiences")
        .context("missing or invalid stream field: audiences")?;
    let audiences: Vec<AudienceKey> =
        serde_json::from_str(&audiences_json).context("invalid audiences JSON")?;
    let message: Arc<str> = Arc::from(payload);

    let delivered = state.registry.fanout_high(&audiences, message);
    state.stream_events.fetch_add(1, Ordering::Relaxed);
    tracing::debug!(delivered, audience_count = audiences.len(), "stream event dispatched");
    Ok(())
}
