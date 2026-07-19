# Axum Direct Cutover

The application now has one realtime implementation only:

- Django/Gunicorn owns HTTP APIs, business rules, PostgreSQL transactions, admin, billing, and durable receipts.
- Axum owns `/ws`, connection lifecycle, `socket.split()`, bounded queues, presence, typing, and signaling transport.
- Redis Stream is the committed Django-to-Axum event bridge.
- Redis remains the Celery broker, cache/security store, ticket replay store, and presence store.

Daphne, Django Channels, `channels_redis`, the Messenger consumer, and the Support Chat consumers are removed from runtime.

## Required production values

```env
REALTIME_TRANSPORT=axum
REALTIME_OUTBOX_ENABLED=true
REALTIME_STREAM_ENABLED=true
REALTIME_AUTH_ENABLED=true
REALTIME_ALLOWED_ORIGINS=https://your-domain.example
REALTIME_INTERNAL_TEST_ENABLED=false
# Set a 32+ character token only when internal socket diagnostics are intentionally enabled.
```

Generate the RS256 signing pair once and resolve the Rust dependency lockfile:

```bash
./scripts/generate-realtime-keys.sh
./scripts/generate-realtime-lockfile.sh
```

## Cutover

Keep the previous complete release directory unchanged. Extract this version into a new release directory and copy the existing `.env` and secret files before running the cutover.

```bash
# Detects production mode, preserves the production Compose override, builds
# sequentially, validates before replacement, and probes Axum/Cloudflare TURN.
./scripts/deploy-axum-cutover.sh
```

Open browser sessions must refresh after the deployment because the previous Channels URL and access-token query protocol no longer exist.

## Rollback

Rollback must use the previous complete application image and its matching Nginx/frontend configuration. Do not route the new frontend to the old Channels server or the old frontend to Axum.
