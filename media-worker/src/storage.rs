use std::{path::{Component, Path, PathBuf}, sync::Arc};

use anyhow::{bail, Context, Result};
use bytes::Bytes;
use futures_util::StreamExt;
use object_store::{aws::AmazonS3Builder, path::Path as ObjectPath, ObjectStore, PutPayload};
use tokio::{fs, io::AsyncWriteExt};

use crate::config::StorageConfig;

#[derive(Clone)]
pub enum MediaStorage {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore>, prefix: String },
}

fn safe_relative_path(value: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    if path.is_absolute() || path.components().any(|part| !matches!(part, Component::Normal(_))) {
        bail!("unsafe storage path");
    }
    Ok(path.to_path_buf())
}

fn object_key(prefix: &str, key: &str) -> Result<ObjectPath> {
    let key = key.trim_matches('/');
    if key.is_empty() || key.split('/').any(|part| part.is_empty() || part == "." || part == "..") {
        bail!("unsafe object key");
    }
    let joined = if prefix.is_empty() { key.to_owned() } else { format!("{}/{}", prefix.trim_matches('/'), key) };
    ObjectPath::parse(joined).context("invalid object-store path")
}

impl MediaStorage {
    pub fn new(config: &StorageConfig) -> Result<Self> {
        match config {
            StorageConfig::Local { root } => Ok(Self::Local { root: root.clone() }),
            StorageConfig::S3 { bucket, endpoint, region, access_key, secret_key, prefix, allow_http } => {
                let store = AmazonS3Builder::new()
                    .with_bucket_name(bucket.clone())
                    .with_endpoint(endpoint.clone())
                    .with_region(region.clone())
                    .with_access_key_id(access_key.clone())
                    .with_secret_access_key(secret_key.clone())
                    .with_virtual_hosted_style_request(false)
                    .with_allow_http(*allow_http)
                    .build()
                    .context("build R2/S3 media storage")?;
                Ok(Self::S3 { store: Arc::new(store), prefix: prefix.clone() })
            }
        }
    }

    pub async fn materialize(&self, key: &str, target: &Path) -> Result<PathBuf> {
        match self {
            Self::Local { root } => {
                let relative = safe_relative_path(key)?;
                let path = root.join(relative);
                let metadata = fs::metadata(&path).await.with_context(|| format!("source object missing: {key}"))?;
                if !metadata.is_file() { bail!("source object is not a regular file"); }
                Ok(path)
            }
            Self::S3 { store, prefix } => {
                let object_path = object_key(prefix, key)?;
                let result = store.get(&object_path).await.context("download source object")?;
                let mut stream = result.into_stream();
                let mut file = fs::File::create(target).await.context("create materialized source")?;
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk.context("stream source object")?;
                    file.write_all(&chunk).await.context("write materialized source")?;
                }
                file.flush().await.context("flush materialized source")?;
                Ok(target.to_path_buf())
            }
        }
    }

    pub async fn put_file(&self, key: &str, source: &Path) -> Result<()> {
        let bytes = fs::read(source).await.context("read generated media object")?;
        match self {
            Self::Local { root } => {
                let relative = safe_relative_path(key)?;
                let destination = root.join(relative);
                if let Some(parent) = destination.parent() { fs::create_dir_all(parent).await?; }
                let temporary = destination.with_extension("uploading");
                fs::write(&temporary, &bytes).await.context("write generated media object")?;
                fs::rename(&temporary, &destination).await.context("publish generated media object")?;
                Ok(())
            }
            Self::S3 { store, prefix } => {
                let object_path = object_key(prefix, key)?;
                store.put(&object_path, PutPayload::from_bytes(Bytes::from(bytes))).await.context("upload generated media object")?;
                Ok(())
            }
        }
    }

    pub async fn delete(&self, key: &str) -> Result<()> {
        if key.trim().is_empty() { return Ok(()); }
        match self {
            Self::Local { root } => {
                let path = root.join(safe_relative_path(key)?);
                match fs::remove_file(path).await {
                    Ok(()) => Ok(()),
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                    Err(error) => Err(error).context("delete old generated media object"),
                }
            }
            Self::S3 { store, prefix } => {
                let path = object_key(prefix, key)?;
                store.delete(&path).await.context("delete old generated media object")
            }
        }
    }
}
