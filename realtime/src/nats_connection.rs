use anyhow::{Context, Result};
use async_nats::{Client, ConnectOptions};

use crate::config::Config;

pub async fn connect(config: &Config) -> Result<Client> {
    ConnectOptions::with_user_and_password(
        config.nats_user.clone(),
        config.nats_password.clone(),
    )
    .connect(config.nats_url.clone())
    .await
    .context("NATS authenticated connection failed")
}
