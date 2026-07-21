# Single-VPS operations runbook

This deployment intentionally avoids a permanent Prometheus/Grafana stack on the
2 GB VPS. Health data is available through lightweight scripts, Django checks,
and Axum's container-internal metrics endpoint.

## Durable transport initialization

A fresh NATS volume must contain the durable stream before Axum can become ready. The web entrypoint and production preflight perform this idempotently:

```bash
python manage.py ensure_nats_stream
```

## Daily operating commands

```bash
./scripts/production-status.sh
./scripts/operational-health.sh
```

`operational-health.sh` checks:

- required container state and Docker health;
- host disk and available memory;
- PostgreSQL connection pressure;
- Redis memory and rejected connections;
- PostgreSQL realtime outbox age and failures;
- NATS JetStream consumer state, slow consumers, memory, and storage;
- Axum connection, delivery, queue, and restart counters;
- encrypted system-backup freshness and checksum.

Axum exposes `/internal/stats` and `/internal/metrics` only on its private Docker
network. Nginx does not proxy these routes publicly.

## Encrypted backups

Generate the encryption key once:

```bash
./scripts/generate-backup-key.sh
```

Copy `secrets/backup-passphrase` into an offline password manager immediately.
Do not rely on the VPS copy as the only copy.

Create a complete encrypted backup:

```bash
./scripts/backup-production.sh
```

The bundle includes a verified PostgreSQL custom dump, local media when enabled,
and encrypted copies of `.env`, realtime signing keys, and origin TLS files. The
plaintext components are removed by default after bundle verification.

For off-VPS storage, create a separate private R2 bucket and configure:

```env
BACKUP_R2_ENABLED=true
BACKUP_R2_BUCKET_NAME=your-private-backup-bucket
BACKUP_R2_PREFIX=system-backups
BACKUP_R2_RETENTION_DAYS=30
BACKUP_R2_KEEP_LATEST=7
```

The browser never receives backup credentials. Django streams the encrypted file
to R2 and verifies its size, SHA-256, and keyed HMAC metadata after upload.

Test extraction without changing production:

```bash
./scripts/restore-production-bundle.sh backups/messenger-system-*.tar.gz.enc --extract-only
```

A destructive restore requires `--confirm`. Configuration is never installed
automatically; it is copied out for manual review.

## Scheduling

Review `ops/crontab.example`, replace the release path, then install equivalent
entries for the deployment user. The default example creates one encrypted backup
each night and runs the lightweight health watch every five minutes.

`OPS_ALERT_WEBHOOK_URL` is optional. When configured, failed health checks send a
small generic JSON message to that webhook. Do not put credentials into alert text.

## Deployment and rollback

`deploy-production.sh` now creates a verified PostgreSQL backup after all new
images and migrations validate but before containers are replaced.

Keep the previous complete release directory. To roll application code back:

```bash
./scripts/rollback-release.sh /srv/crescentsphere/releases/previous --confirm
```

This does not reverse database migrations. Schema migrations must remain backward
compatible for one release. A database restore is a separate, destructive action.

## Alert thresholds

The defaults are deliberately conservative for a 2 GB VPS:

```env
REALTIME_OUTBOX_MAX_AGE_SECONDS=120
REALTIME_OUTBOX_MAX_FAILED=25
OPS_NATS_MAX_SLOW_CONSUMERS=0
OPS_JETSTREAM_MAX_STORAGE_BYTES=1610612736
OPS_DISK_MAX_PERCENT=85
OPS_MIN_AVAILABLE_MEMORY_MB=256
OPS_BACKUP_MAX_AGE_HOURS=30
```

Adjust only after measuring normal production behavior. A growing outbox normally means NATS, JetStream, or Axum is unavailable; a growing JetStream consumer backlog means the Axum consumer is connected but not acknowledging fast enough.

## Incident order

1. Run `./scripts/operational-health.sh`.
2. Check `docker compose ... ps` and the affected container logs.
3. Confirm PostgreSQL, PgBouncer, NATS, and Redis are healthy before restarting application services.
4. Restart only the failed service when possible.
5. If Axum restarted, clients reconnect and durable events recover through the
   JetStream and Django message APIs.
6. If the release itself is faulty, use the previous release directory rollback.
7. Restore the database only when data corruption or loss is confirmed.
