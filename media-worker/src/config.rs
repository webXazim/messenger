use std::{env, path::PathBuf, time::Duration};

use anyhow::{bail, Context, Result};

#[derive(Clone)]
pub enum StorageConfig {
    Local { root: PathBuf },
    S3 {
        bucket: String,
        endpoint: String,
        region: String,
        access_key: String,
        secret_key: String,
        prefix: String,
        allow_http: bool,
    },
}

#[derive(Clone)]
pub struct Config {
    pub database_url: String,
    pub worker_name: String,
    pub shadow_mode: bool,
    pub concurrency: usize,
    pub poll_interval: Duration,
    pub lease_seconds: i64,
    pub max_attempts: i32,
    pub max_source_bytes: i64,
    pub max_frame_dimension: u32,
    pub max_frame_pixels: u64,
    pub probe_timeout: Duration,
    pub process_timeout: Duration,
    pub thumbnail_dimension: u32,
    pub thumbnail_quality: u8,
    pub video_thumbnail_offset: f64,
    pub waveform_points: usize,
    pub waveform_max_seconds: u64,
    pub ffmpeg_threads: usize,
    pub temp_root: PathBuf,
    pub storage: StorageConfig,
}

fn value(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_owned()).trim().to_owned()
}

fn positive_usize(name: &str, default: usize, max: usize) -> usize {
    value(name, &default.to_string()).parse::<usize>().unwrap_or(default).clamp(1, max)
}

fn positive_i64(name: &str, default: i64, max: i64) -> i64 {
    value(name, &default.to_string()).parse::<i64>().unwrap_or(default).clamp(1, max)
}

fn bool_value(name: &str, default: bool) -> bool {
    match value(name, if default { "true" } else { "false" }).to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => true,
        "0" | "false" | "no" | "off" => false,
        _ => default,
    }
}

impl Config {
    pub fn from_env() -> Result<Self> {
        let database_url = value("SQLX_DATABASE_URL", "");
        if database_url.is_empty() {
            bail!("SQLX_DATABASE_URL is required");
        }
        let worker_name = value("MEDIA_WORKER_NAME", &value("HOSTNAME", "media-worker"));
        let media_backend = value("MEDIA_PROCESSING_BACKEND", "rust").to_ascii_lowercase();
        if !matches!(media_backend.as_str(), "rust" | "rust_shadow") {
            bail!("media worker requires MEDIA_PROCESSING_BACKEND=rust or rust_shadow");
        }
        let use_s3 = bool_value("CHAT_USE_R2_STORAGE", false)
            || value("MEDIA_WORKER_STORAGE_BACKEND", "local").eq_ignore_ascii_case("s3");
        let storage = if use_s3 {
            let bucket = value("CLOUDFLARE_R2_BUCKET_NAME", "");
            let access_key = value("CLOUDFLARE_R2_ACCESS_KEY_ID", "");
            let secret_key = value("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "");
            let account_id = value("CLOUDFLARE_R2_ACCOUNT_ID", "");
            let endpoint = {
                let explicit = value("CLOUDFLARE_R2_ENDPOINT_URL", "");
                if !explicit.is_empty() { explicit } else if !account_id.is_empty() {
                    format!("https://{account_id}.r2.cloudflarestorage.com")
                } else { String::new() }
            };
            if bucket.is_empty() || access_key.is_empty() || secret_key.is_empty() || endpoint.is_empty() {
                bail!("R2 media worker storage requires bucket, endpoint/account id, access key and secret key");
            }
            StorageConfig::S3 {
                bucket,
                endpoint: endpoint.clone(),
                region: value("AWS_S3_REGION_NAME", "auto"),
                access_key,
                secret_key,
                prefix: value("MEDIA_WORKER_STORAGE_PREFIX", "chat-private").trim_matches('/').to_owned(),
                allow_http: endpoint.starts_with("http://"),
            }
        } else {
            StorageConfig::Local {
                root: PathBuf::from(value("MEDIA_WORKER_LOCAL_STORAGE_ROOT", "/app/private_media")),
            }
        };

        Ok(Self {
            database_url,
            worker_name,
            shadow_mode: media_backend == "rust_shadow",
            concurrency: positive_usize("MEDIA_WORKER_CONCURRENCY", 1, 4),
            poll_interval: Duration::from_millis(positive_i64("MEDIA_WORKER_POLL_INTERVAL_MS", 750, 30_000) as u64),
            lease_seconds: positive_i64("MEDIA_WORKER_LEASE_SECONDS", 300, 3600),
            max_attempts: positive_i64("MEDIA_WORKER_MAX_ATTEMPTS", 4, 20) as i32,
            max_source_bytes: positive_i64("MEDIA_WORKER_MAX_SOURCE_BYTES", 262_144_000, 2_147_483_648),
            max_frame_dimension: positive_i64("MEDIA_WORKER_MAX_FRAME_DIMENSION", 32_768, 65_535) as u32,
            max_frame_pixels: positive_i64("MEDIA_WORKER_MAX_FRAME_PIXELS", 80_000_000, 500_000_000) as u64,
            probe_timeout: Duration::from_secs(positive_i64("MEDIA_WORKER_PROBE_TIMEOUT_SECONDS", 15, 120) as u64),
            process_timeout: Duration::from_secs(positive_i64("MEDIA_WORKER_PROCESS_TIMEOUT_SECONDS", 90, 900) as u64),
            thumbnail_dimension: positive_i64("MEDIA_SERVER_THUMBNAIL_DIMENSION", 960, 4096) as u32,
            thumbnail_quality: positive_i64("MEDIA_SERVER_THUMBNAIL_JPEG_QUALITY", 82, 95).clamp(30, 95) as u8,
            video_thumbnail_offset: value("MEDIA_VIDEO_THUMBNAIL_OFFSET_SECONDS", "0.25").parse::<f64>().unwrap_or(0.25).clamp(0.0, 30.0),
            waveform_points: positive_usize("MEDIA_WORKER_WAVEFORM_POINTS", 48, 256),
            waveform_max_seconds: positive_i64("MEDIA_WORKER_WAVEFORM_MAX_SECONDS", 600, 3600) as u64,
            ffmpeg_threads: positive_usize("MEDIA_WORKER_FFMPEG_THREADS", 1, 4),
            temp_root: PathBuf::from(value("MEDIA_WORKER_TEMP_ROOT", "/work")),
            storage,
        })
    }

    pub fn validate_tools(&self) -> Result<()> {
        for tool in ["ffprobe", "ffmpeg"] {
            let status = std::process::Command::new(tool)
                .arg("-version")
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
                .with_context(|| format!("failed to start {tool}"))?;
            if !status.success() {
                bail!("{tool} is unavailable");
            }
        }
        Ok(())
    }
}
