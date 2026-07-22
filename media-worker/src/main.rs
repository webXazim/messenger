mod config;
mod db;
mod process;
mod storage;

use std::{sync::Arc, time::Duration};

use anyhow::{Context, Result};
use config::Config;
use db::ClaimedJob;
use sqlx::PgPool;
use storage::MediaStorage;
use tokio::{fs, task::JoinSet, time::sleep};
use tokio_util::sync::CancellationToken;
use tracing::{error, info, warn};

async fn process_one(pool: &PgPool, storage: &MediaStorage, config: &Config, job: ClaimedJob) -> Result<()> {
    if job.size <= 0 || job.size > config.max_source_bytes {
        anyhow::bail!("source size {} exceeds worker policy", job.size);
    }
    let work_dir = config.temp_root.join(job.job_id.to_string());
    if fs::try_exists(&work_dir).await.unwrap_or(false) { fs::remove_dir_all(&work_dir).await.ok(); }
    fs::create_dir_all(&work_dir).await.context("create media job work directory")?;
    let materialized_target = work_dir.join("source.bin");
    let source = storage.materialize(&job.file_name, &materialized_target).await?;
    let actual_size = fs::metadata(&source).await?.len();
    if actual_size > config.max_source_bytes as u64 { anyhow::bail!("materialized source exceeds worker policy"); }

    let (output, generated_thumbnail) = process::process(&job, &source, &work_dir, config).await?;
    if config.shadow_mode {
        if !db::complete_shadow_job(pool, &job, &output).await? {
            anyhow::bail!("media-processing shadow lease was lost before completion");
        }
    } else {
        if let (Some(name), Some(path)) = (output.thumbnail.as_deref(), generated_thumbnail.as_deref()) {
            storage.put_file(name, path).await?;
        }
        let finalized = db::finalize_job(pool, &job, &output).await?;
        if !finalized {
            anyhow::bail!("media-processing lease was lost before finalization");
        }
        if !job.current_thumbnail.is_empty() && output.thumbnail.as_deref() != Some(job.current_thumbnail.as_str()) {
            if let Err(error) = storage.delete(&job.current_thumbnail).await {
                warn!(job_id=%job.job_id, error=%error, "failed to remove replaced thumbnail");
            }
        }
    }
    fs::remove_dir_all(&work_dir).await.ok();
    info!(job_id=%job.job_id, upload_id=%job.upload_id, media_kind=%job.media_kind, "media job completed");
    Ok(())
}

async fn worker_loop(slot: usize, pool: PgPool, storage: MediaStorage, config: Arc<Config>, shutdown: CancellationToken) {
    loop {
        if shutdown.is_cancelled() { break; }
        match db::claim_job(&pool, &config).await {
            Ok(Some(job)) => {
                let job_copy = job.clone();
                if let Err(error) = process_one(&pool, &storage, &config, job).await {
                    error!(worker_slot=slot, job_id=%job_copy.job_id, upload_id=%job_copy.upload_id, error=%error, "media job failed");
                    if let Err(mark_error) = db::fail_job(&pool, &job_copy, &error.to_string(), config.max_attempts).await {
                        error!(job_id=%job_copy.job_id, error=%mark_error, "failed to persist media job failure");
                    }
                    fs::remove_dir_all(config.temp_root.join(job_copy.job_id.to_string())).await.ok();
                }
            }
            Ok(None) => tokio::select! {
                _ = shutdown.cancelled() => break,
                _ = sleep(config.poll_interval) => {},
            },
            Err(error) => {
                error!(worker_slot=slot, error=%error, "media job claim failed");
                tokio::select! {
                    _ = shutdown.cancelled() => break,
                    _ = sleep(Duration::from_secs(2)) => {},
                }
            }
        }
    }
}

fn init_tracing() {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();
}

#[tokio::main]
async fn main() -> Result<()> {
    init_tracing();
    let config = Arc::new(Config::from_env()?);
    config.validate_tools()?;
    fs::create_dir_all(&config.temp_root).await.context("create media worker temp root")?;
    let pool = db::connect(&config).await?;

    if std::env::args().any(|argument| argument == "--healthcheck") {
        db::healthcheck(&pool).await?;
        return Ok(());
    }

    let storage = MediaStorage::new(&config.storage)?;
    let shutdown = CancellationToken::new();
    let mut workers = JoinSet::new();
    for slot in 0..config.concurrency {
        workers.spawn(worker_loop(slot, pool.clone(), storage.clone(), config.clone(), shutdown.clone()));
    }
    info!(concurrency=config.concurrency, worker=%config.worker_name, shadow_mode=config.shadow_mode, "Rust media worker started");

    tokio::select! {
        signal = tokio::signal::ctrl_c() => { signal.context("listen for shutdown signal")?; },
        result = workers.join_next() => {
            if let Some(Err(error)) = result { return Err(error).context("media worker task stopped unexpectedly"); }
        }
    }
    shutdown.cancel();
    while workers.join_next().await.is_some() {}
    pool.close().await;
    info!("Rust media worker stopped");
    Ok(())
}
