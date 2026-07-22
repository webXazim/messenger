# Final Axum efficiency profile

This release completes the high-frequency migration from Django to Axum/SQLx and adds bounded resource controls for the 2 vCPU / 2 GB VPS profile.

## Runtime ownership

**Axum** owns Messenger and Support Chat reads, writes, receipts, reactions, conversation commands, call coordination, presence, typing, WebSockets, Core NATS events, and direct JetStream publishing.

**The Rust media worker** owns approved-media probing, thumbnail generation, waveform generation, and FFmpeg/ffprobe orchestration. It is a separate process so CPU-heavy files cannot block realtime work.

**Django/Celery** retain migrations, admin, authentication/recovery, billing, TURN credentials, upload authorization, antivirus, private-file serving, push/email, knowledge-base/workflow administration, and durable control-plane recovery jobs.

Low-frequency group creation may still make a synchronous Django entitlement check when centralized billing is enabled. This does not return message, read, receipt, presence, or call traffic to Django.

Redis remains deliberately small and is not the realtime transport. It is retained for cache, token/ticket replay protection, Celery broker duties, and low-frequency compatibility services.

## Overload policy

The service does not accept unbounded queued work:

- Axum read and write requests use separate semaphores.
- Saturated requests receive HTTP 503, `Retry-After: 1`, and `code=realtime_overloaded` immediately.
- Executing requests have a hard deadline and return `realtime_timeout` on expiry.
- SQLx uses a four-connection maximum behind PgBouncer for the 2 GB VPS default.
- WebSocket high- and low-priority queues expose pressure metrics.
- Nginx buffers small API responses, limits fast-route bodies, and caps concurrent WebSockets per client IP.
- FFmpeg work is limited to one media job and one FFmpeg thread by default.

## Production activation

Copy and complete `.env.production.example`, then run:

```bash
./scripts/stack-profile.sh final
```

`efficient` is an equivalent alias. The profile applies migrations before exposing Rust routes, starts the Rust media worker, verifies source contracts, and checks the running Axum selectors.

The strict gate is:

```env
AXUM_DATA_PLANE_REQUIRED=True
```

With this enabled, Django refuses to start if a high-frequency selector has fallen back to Django/Redis or if PgBouncer, direct JetStream, or the Rust media worker is not selected.

## Default 2 GB VPS tuning

```env
GRANIAN_WORKERS=1
SQLX_MIN_CONNECTIONS=1
SQLX_MAX_CONNECTIONS=4
SQLX_ACQUIRE_TIMEOUT_MS=1500
SQLX_IDLE_TIMEOUT_SECONDS=60
SQLX_MAX_LIFETIME_SECONDS=900
REALTIME_HTTP_READ_CONCURRENCY=24
REALTIME_HTTP_WRITE_CONCURRENCY=12
REALTIME_HTTP_REQUEST_TIMEOUT_MS=10000
REALTIME_HTTP_MAX_BODY_BYTES=1048576
MEDIA_WORKER_CONCURRENCY=1
MEDIA_WORKER_FFMPEG_THREADS=1
```

Do not raise these values based only on CPU count. Use the measured capacity suite and preserve at least 20% headroom.

## Monitoring

Axum exposes:

- `/health/live`
- `/health/ready`
- `/internal/stats`
- `/internal/metrics`

The internal endpoints include HTTP admission, SQLx pool, WebSocket queue, NATS, outbox, connection, and slow-client metrics. Run:

```bash
./scripts/operational-health.sh
```

The check validates all containers, the Rust media worker, PostgreSQL/PgBouncer, Redis, NATS/JetStream, Axum readiness, SQLx pool limits, and WebSocket queue pressure.

## Required final load suite

Run externally:

```bash
./scripts/run-load-test.sh smoke 10
./scripts/run-load-test.sh capacity 650
./scripts/run-load-test.sh api 10
./scripts/run-load-test.sh reconnect 250
./scripts/run-load-test.sh mixed 500
./scripts/run-load-test.sh overload 300
./scripts/run-load-test.sh soak 150
```

The overload test accepts only successful responses or deliberate 503 load shedding. The soak test runs the mixed data plane for 30 minutes by default to expose memory growth, pool starvation, queue accumulation, and worker instability.

## Removed obsolete runtime code

The release removes the unused Django Channels Messenger/Support consumers, their WebSocket routing modules, and the old Rust Redis-stream module. Historical migrations remain unchanged.
