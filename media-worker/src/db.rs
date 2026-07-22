use anyhow::{Context, Result};
use serde_json::Value;
use sqlx::{postgres::PgPoolOptions, PgPool, Row};
use uuid::Uuid;

use crate::config::Config;

#[derive(Clone, Debug)]
pub struct ClaimedJob {
    pub job_id: Uuid,
    pub lease_token: Uuid,
    pub upload_id: Uuid,
    pub attempts: i32,
    pub file_name: String,
    pub original_name: String,
    pub media_kind: String,
    pub mime_type: String,
    pub size: i64,
    pub purpose: String,
    pub metadata: Value,
    pub current_thumbnail: String,
    pub date_path: String,
}

#[derive(Debug)]
pub struct FinalizedMedia {
    pub width: Option<i32>,
    pub height: Option<i32>,
    pub rotation: Option<i32>,
    pub duration_seconds: Option<String>,
    pub thumbnail: Option<String>,
    pub metadata_updates: Value,
    pub result: Value,
}

pub async fn connect(config: &Config) -> Result<PgPool> {
    PgPoolOptions::new()
        .max_connections((config.concurrency as u32).saturating_add(2).min(8))
        .min_connections(1)
        .acquire_timeout(std::time::Duration::from_secs(5))
        .connect(&config.database_url)
        .await
        .context("connect media worker database")
}

pub async fn claim_job(pool: &PgPool, config: &Config) -> Result<Option<ClaimedJob>> {
    let lease_token = Uuid::new_v4();
    let row = sqlx::query(
        r#"
        WITH candidate AS (
            SELECT j.id
            FROM chat_mediaprocessingjob j
            JOIN chat_pendingupload u ON u.id = j.upload_id
            WHERE (
                (j.status IN ('pending','failed') AND j.available_at <= NOW())
                OR (j.status = 'processing' AND j.locked_at <= NOW() - ($3::double precision * INTERVAL '1 second'))
            )
              AND j.attempts < $4
              AND u.scan_status = 'clean'
              AND u.status IN ('pending','attached')
              AND u.media_kind IN ('image','video','audio')
            ORDER BY j.available_at, j.created_at, j.id
            FOR UPDATE OF j SKIP LOCKED
            LIMIT 1
        )
        UPDATE chat_mediaprocessingjob j
        SET status = 'processing', attempts = j.attempts + 1, locked_at = NOW(),
            lease_token = $1, worker_name = $2, last_error = '', updated_at = NOW()
        FROM candidate c, chat_pendingupload u
        WHERE j.id = c.id AND u.id = j.upload_id
        RETURNING j.id AS job_id, j.lease_token, j.attempts, u.id AS upload_id,
                  u.file, u.original_name, u.media_kind, u.mime_type, u.size,
                  u.purpose, COALESCE(u.metadata, '{}'::jsonb) AS metadata,
                  COALESCE(u.thumbnail, '') AS current_thumbnail,
                  to_char(u.created_at, 'YYYY/MM') AS date_path
        "#,
    )
    .bind(lease_token)
    .bind(&config.worker_name)
    .bind(config.lease_seconds as f64)
    .bind(config.max_attempts)
    .fetch_optional(pool)
    .await
    .context("claim media-processing job")?;

    row.map(|row| {
        Ok(ClaimedJob {
            job_id: row.try_get("job_id")?,
            lease_token: row.try_get("lease_token")?,
            upload_id: row.try_get("upload_id")?,
            attempts: row.try_get("attempts")?,
            file_name: row.try_get("file")?,
            original_name: row.try_get("original_name")?,
            media_kind: row.try_get("media_kind")?,
            mime_type: row.try_get("mime_type")?,
            size: row.try_get("size")?,
            purpose: row.try_get("purpose")?,
            metadata: row.try_get("metadata")?,
            current_thumbnail: row.try_get("current_thumbnail")?,
            date_path: row.try_get("date_path")?,
        })
    }).transpose().context("decode media-processing job")
}


pub async fn complete_shadow_job(pool: &PgPool, job: &ClaimedJob, output: &FinalizedMedia) -> Result<bool> {
    let result = serde_json::json!({
        "mode": "shadow",
        "processor": "rust-media-worker",
        "observed": output.result.clone(),
        "width": output.width,
        "height": output.height,
        "rotation": output.rotation,
        "duration_seconds": output.duration_seconds.clone(),
        "thumbnail_candidate": output.thumbnail.clone(),
        "metadata_updates": output.metadata_updates.clone(),
    });
    let updated = sqlx::query(
        r#"
        UPDATE chat_mediaprocessingjob
        SET status = 'completed', completed_at = NOW(), locked_at = NULL, lease_token = NULL,
            last_error = '', result = $3, updated_at = NOW()
        WHERE id = $1 AND lease_token = $2 AND status = 'processing'
        "#,
    )
    .bind(job.job_id)
    .bind(job.lease_token)
    .bind(result)
    .execute(pool)
    .await
    .context("complete shadow media-processing job")?;
    Ok(updated.rows_affected() == 1)
}

pub async fn finalize_job(pool: &PgPool, job: &ClaimedJob, output: &FinalizedMedia) -> Result<bool> {
    let mut transaction = pool.begin().await.context("begin media finalize transaction")?;
    let metadata = serde_json::to_string(&output.metadata_updates)?;
    let result = serde_json::to_string(&output.result)?;
    let thumbnail = output.thumbnail.as_deref().unwrap_or("");

    let upload_result = sqlx::query(
        r#"
        UPDATE chat_pendingupload
        SET width = COALESCE($2, width), height = COALESCE($3, height),
            rotation = COALESCE($4, rotation), duration_seconds = COALESCE($5::numeric, duration_seconds),
            thumbnail = COALESCE(NULLIF($6, ''), thumbnail),
            metadata = COALESCE(metadata, '{}'::jsonb) || $7::jsonb,
            updated_at = NOW()
        WHERE id = $1 AND scan_status = 'clean' AND status IN ('pending','attached')
        "#,
    )
    .bind(job.upload_id)
    .bind(output.width)
    .bind(output.height)
    .bind(output.rotation)
    .bind(output.duration_seconds.as_deref())
    .bind(thumbnail)
    .bind(&metadata)
    .execute(&mut *transaction)
    .await
    .context("update processed pending upload")?;

    if upload_result.rows_affected() == 0 {
        transaction.rollback().await.ok();
        return Ok(false);
    }

    sqlx::query(
        r#"
        UPDATE chat_messageattachment
        SET width = COALESCE($2, width), height = COALESCE($3, height),
            rotation = COALESCE($4, rotation), duration_seconds = COALESCE($5::numeric, duration_seconds),
            thumbnail = COALESCE(NULLIF($6, ''), thumbnail),
            metadata = COALESCE(metadata, '{}'::jsonb) || $7::jsonb,
            updated_at = NOW()
        WHERE (file = $1 OR metadata->>'source_pending_upload_id' = $8) AND scan_status = 'clean'
        "#,
    )
    .bind(&job.file_name)
    .bind(output.width)
    .bind(output.height)
    .bind(output.rotation)
    .bind(output.duration_seconds.as_deref())
    .bind(thumbnail)
    .bind(&metadata)
    .bind(job.upload_id.to_string())
    .execute(&mut *transaction)
    .await
    .context("update already-attached media metadata")?;

    let claimed = sqlx::query(
        r#"
        UPDATE chat_mediaprocessingjob
        SET status = 'completed', completed_at = NOW(), locked_at = NULL, lease_token = NULL,
            last_error = '', result = $3::jsonb, updated_at = NOW()
        WHERE id = $1 AND lease_token = $2 AND status = 'processing'
        "#,
    )
    .bind(job.job_id)
    .bind(job.lease_token)
    .bind(&result)
    .execute(&mut *transaction)
    .await
    .context("complete media-processing job")?;

    if claimed.rows_affected() != 1 {
        transaction.rollback().await.ok();
        return Ok(false);
    }
    transaction.commit().await.context("commit media finalize transaction")?;
    Ok(true)
}

pub async fn fail_job(pool: &PgPool, job: &ClaimedJob, error: &str, max_attempts: i32) -> Result<()> {
    let exponent = job.attempts.clamp(1, 8) as u32;
    let delay = if job.attempts >= max_attempts { 86_400 } else { (2_i64.pow(exponent) * 5).min(300) };
    sqlx::query(
        r#"
        UPDATE chat_mediaprocessingjob
        SET status = 'failed', locked_at = NULL, lease_token = NULL,
            available_at = NOW() + ($3::double precision * INTERVAL '1 second'),
            last_error = $4, updated_at = NOW()
        WHERE id = $1 AND lease_token = $2 AND status = 'processing'
        "#,
    )
    .bind(job.job_id)
    .bind(job.lease_token)
    .bind(delay as f64)
    .bind(error.chars().take(2000).collect::<String>())
    .execute(pool)
    .await
    .context("fail media-processing job")?;
    Ok(())
}

pub async fn healthcheck(pool: &PgPool) -> Result<()> {
    sqlx::query("SELECT 1").execute(pool).await.context("media worker database healthcheck")?;
    Ok(())
}
