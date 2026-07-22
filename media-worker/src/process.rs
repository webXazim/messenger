use std::{path::{Path, PathBuf}, process::Stdio, time::Duration};

use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Map, Value};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use tokio::{fs, process::Command, time::timeout};

use crate::{config::Config, db::{ClaimedJob, FinalizedMedia}};

async fn command_output(program: &str, arguments: &[String], timeout_duration: Duration) -> Result<std::process::Output> {
    let mut command = Command::new(program);
    command.args(arguments).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
    let output = timeout(timeout_duration, command.output())
        .await
        .map_err(|_| anyhow!("{program} timed out"))??;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!("{program} failed: {}", stderr.chars().take(800).collect::<String>());
    }
    Ok(output)
}

fn positive_i32(value: Option<&Value>) -> Option<i32> {
    let item = value?;
    let number = if let Some(number) = item.as_i64() {
        number
    } else {
        item.as_str()?.parse::<i64>().ok()?
    };
    i32::try_from(number).ok().filter(|number| *number > 0)
}

fn positive_f64(value: Option<&Value>) -> Option<f64> {
    let item = value?;
    let number = if let Some(number) = item.as_f64() {
        number
    } else {
        item.as_str()?.parse::<f64>().ok()?
    };
    (number.is_finite() && number >= 0.0).then_some(number)
}

fn fraction(value: Option<&Value>) -> Option<f64> {
    let text = value?.as_str()?;
    let (left, right) = text.split_once('/')?;
    let numerator = left.parse::<f64>().ok()?;
    let denominator = right.parse::<f64>().ok()?;
    if denominator == 0.0 { None } else { Some(numerator / denominator) }
}

fn rotation(stream: &Value) -> i32 {
    let direct = stream.get("tags").and_then(|tags| tags.get("rotate")).and_then(|value| value.as_str()).and_then(|value| value.parse::<i32>().ok());
    let side = stream.get("side_data_list").and_then(Value::as_array).and_then(|items| {
        items.iter().find_map(|item| item.get("rotation").and_then(Value::as_i64).and_then(|value| i32::try_from(value).ok()))
    });
    let normalized = direct.or(side).unwrap_or(0).rem_euclid(360);
    match normalized { 90 | 180 | 270 => normalized, _ => 0 }
}

fn thumbnail_dimensions(width: i32, height: i32, rotation: i32, max_dimension: u32) -> (i32, i32) {
    let (display_width, display_height) = if matches!(rotation, 90 | 270) { (height, width) } else { (width, height) };
    if display_width <= 0 || display_height <= 0 { return (0, 0); }
    let scale = (max_dimension as f64 / display_width.max(display_height) as f64).min(1.0);
    (((display_width as f64 * scale).round() as i32).max(1), ((display_height as f64 * scale).round() as i32).max(1))
}

async fn probe(path: &Path, config: &Config) -> Result<Value> {
    let arguments = vec![
        "-v".into(), "error".into(), "-print_format".into(), "json".into(),
        "-show_format".into(), "-show_streams".into(), path.display().to_string(),
    ];
    let output = command_output("ffprobe", &arguments, config.probe_timeout).await?;
    serde_json::from_slice(&output.stdout).context("parse ffprobe output")
}

async fn generate_thumbnail(source: &Path, destination: &Path, media_kind: &str, config: &Config) -> Result<()> {
    let quality_scale = (((100_i32 - config.thumbnail_quality as i32) as f64 / 4.0).round() as i32 + 2).clamp(2, 20);
    let filter = format!("scale='min({},iw)':'min({},ih)':force_original_aspect_ratio=decrease:flags=lanczos,format=yuvj420p", config.thumbnail_dimension, config.thumbnail_dimension);
    let mut arguments = vec!["-v".into(), "error".into(), "-nostdin".into(), "-y".into()];
    if media_kind == "video" {
        arguments.extend(["-ss".into(), config.video_thumbnail_offset.to_string()]);
    }
    arguments.extend([
        "-i".into(), source.display().to_string(), "-threads".into(), config.ffmpeg_threads.to_string(), "-frames:v".into(), "1".into(),
        "-vf".into(), filter, "-q:v".into(), quality_scale.to_string(), destination.display().to_string(),
    ]);
    command_output("ffmpeg", &arguments, config.process_timeout).await?;
    let metadata = fs::metadata(destination).await.context("generated thumbnail missing")?;
    if metadata.len() == 0 { bail!("generated thumbnail is empty"); }
    Ok(())
}

async fn generate_waveform(source: &Path, raw_path: &Path, config: &Config) -> Result<Vec<i64>> {
    let arguments = vec![
        "-v".into(), "error".into(), "-nostdin".into(), "-y".into(), "-i".into(), source.display().to_string(),
        "-map".into(), "0:a:0".into(), "-ac".into(), "1".into(), "-ar".into(), "8000".into(),
        "-t".into(), config.waveform_max_seconds.to_string(), "-threads".into(), config.ffmpeg_threads.to_string(), "-f".into(), "s16le".into(), raw_path.display().to_string(),
    ];
    command_output("ffmpeg", &arguments, config.process_timeout).await?;
    let raw = fs::read(raw_path).await.context("read decoded waveform")?;
    if raw.len() < 2 { bail!("decoded waveform is empty"); }
    let sample_count = raw.len() / 2;
    let bucket_size = (sample_count / config.waveform_points).max(1);
    let mut peaks = Vec::with_capacity(config.waveform_points);
    for bucket in 0..config.waveform_points {
        let start = bucket * bucket_size;
        let end = if bucket + 1 == config.waveform_points { sample_count } else { ((bucket + 1) * bucket_size).min(sample_count) };
        let mut peak = 0_i32;
        for index in start..end {
            let offset = index * 2;
            let sample = i16::from_le_bytes([raw[offset], raw[offset + 1]]) as i32;
            peak = peak.max(sample.abs());
        }
        peaks.push(peak);
    }
    let maximum = peaks.iter().copied().max().unwrap_or(1).max(1) as f64;
    Ok(peaks.into_iter().map(|peak| ((peak as f64 / maximum) * 100.0).round().clamp(7.0, 100.0) as i64).collect())
}

fn thumbnail_path(job: &ClaimedJob) -> String {
    let name = format!("{}-thumb.jpg", job.upload_id);
    if job.purpose == "support" {
        let account = job.metadata.get("support_account_id").and_then(Value::as_str).unwrap_or("unassigned");
        let website = job.metadata.get("support_website_id").and_then(Value::as_str).unwrap_or("unassigned");
        format!("support/{account}/{website}/pending-thumbnails/{}/{name}", job.date_path)
    } else {
        format!("chat/pending_thumbnails/{}/{name}", job.date_path)
    }
}

pub async fn process(job: &ClaimedJob, source: &Path, work_dir: &Path, config: &Config) -> Result<(FinalizedMedia, Option<PathBuf>)> {
    let probe = probe(source, config).await?;
    let streams = probe.get("streams").and_then(Value::as_array).cloned().unwrap_or_default();
    let format = probe.get("format").cloned().unwrap_or_else(|| json!({}));
    let video = streams.iter().find(|stream| stream.get("codec_type").and_then(Value::as_str) == Some("video"));
    let audio = streams.iter().find(|stream| stream.get("codec_type").and_then(Value::as_str) == Some("audio"));
    let duration = positive_f64(format.get("duration"))
        .or_else(|| video.and_then(|stream| positive_f64(stream.get("duration"))))
        .or_else(|| audio.and_then(|stream| positive_f64(stream.get("duration"))));

    let mut metadata = Map::new();
    metadata.insert("server_probe_status".into(), json!("ffprobe_verified"));
    metadata.insert("server_metadata_verified".into(), json!(true));
    metadata.insert("server_metadata_verified_at".into(), json!(OffsetDateTime::now_utc().format(&Rfc3339)?));
    metadata.insert("media_processor".into(), json!("rust-media-worker"));
    metadata.insert("media_processing_version".into(), json!(1));
    if let Some(bit_rate) = positive_i32(format.get("bit_rate")) { metadata.insert("bit_rate".into(), json!(bit_rate)); }

    let mut width = None;
    let mut height = None;
    let mut media_rotation = None;
    let mut generated_thumbnail = None;
    let mut thumbnail_name = None;

    if matches!(job.media_kind.as_str(), "image" | "video") {
        let stream = video.ok_or_else(|| anyhow!("no video/image stream found"))?;
        let raw_width = positive_i32(stream.get("width")).ok_or_else(|| anyhow!("invalid media width"))?;
        let raw_height = positive_i32(stream.get("height")).ok_or_else(|| anyhow!("invalid media height"))?;
        let width_u64 = raw_width as u64;
        let height_u64 = raw_height as u64;
        if width_u64 > config.max_frame_dimension as u64 || height_u64 > config.max_frame_dimension as u64 {
            bail!("media dimensions exceed worker policy");
        }
        if width_u64.saturating_mul(height_u64) > config.max_frame_pixels {
            bail!("media pixel count exceeds worker policy");
        }
        let rotate = rotation(stream);
        width = Some(raw_width);
        height = Some(raw_height);
        media_rotation = Some(rotate);
        let (display_width, display_height) = if matches!(rotate, 90 | 270) { (raw_height, raw_width) } else { (raw_width, raw_height) };
        metadata.insert("display_width".into(), json!(display_width));
        metadata.insert("display_height".into(), json!(display_height));
        metadata.insert("aspect_ratio".into(), json!((display_width as f64 / display_height as f64 * 1_000_000.0).round() / 1_000_000.0));
        metadata.insert("codec_name".into(), stream.get("codec_name").cloned().unwrap_or(Value::Null));
        if job.media_kind == "video" {
            metadata.insert("frame_rate".into(), fraction(stream.get("avg_frame_rate")).map(|v| json!((v * 1000.0).round() / 1000.0)).unwrap_or(Value::Null));
            metadata.insert("has_audio_stream".into(), json!(audio.is_some()));
        }
        let thumbnail = work_dir.join("thumbnail.jpg");
        generate_thumbnail(source, &thumbnail, &job.media_kind, config).await?;
        let (thumb_width, thumb_height) = thumbnail_dimensions(raw_width, raw_height, rotate, config.thumbnail_dimension);
        metadata.insert("thumbnail_source".into(), json!("rust_worker"));
        metadata.insert("thumbnail_content_type".into(), json!("image/jpeg"));
        metadata.insert("thumbnail_width".into(), json!(thumb_width));
        metadata.insert("thumbnail_height".into(), json!(thumb_height));
        metadata.insert("thumbnail_generation_status".into(), json!("generated"));
        thumbnail_name = Some(thumbnail_path(job));
        generated_thumbnail = Some(thumbnail);
    } else if job.media_kind == "audio" {
        let stream = audio.ok_or_else(|| anyhow!("no audio stream found"))?;
        metadata.insert("codec_name".into(), stream.get("codec_name").cloned().unwrap_or(Value::Null));
        if let Some(sample_rate) = positive_i32(stream.get("sample_rate")) { metadata.insert("sample_rate".into(), json!(sample_rate)); }
        if let Some(channels) = positive_i32(stream.get("channels")) { metadata.insert("channels".into(), json!(channels)); }
        let waveform_path = work_dir.join("waveform.raw");
        let waveform = generate_waveform(source, &waveform_path, config).await?;
        metadata.insert("waveform".into(), json!(waveform));
        metadata.insert("waveform_generation_status".into(), json!("generated"));
        if duration.unwrap_or(0.0) > config.waveform_max_seconds as f64 {
            metadata.insert("waveform_truncated_seconds".into(), json!(config.waveform_max_seconds));
        }
    }

    let duration_text = duration.map(|value| format!("{:.2}", value.max(0.0)));
    let result = json!({
        "processor": "rust-media-worker",
        "upload_id": job.upload_id,
        "media_kind": job.media_kind.clone(),
        "mime_type": job.mime_type.clone(),
        "source_bytes": job.size,
        "thumbnail": thumbnail_name.clone(),
    });
    Ok((FinalizedMedia {
        width,
        height,
        rotation: media_rotation,
        duration_seconds: duration_text,
        thumbnail: thumbnail_name,
        metadata_updates: Value::Object(metadata),
        result,
    }, generated_thumbnail))
}
