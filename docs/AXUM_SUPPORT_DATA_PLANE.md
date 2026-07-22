# Axum Support Chat data plane

This phase moves high-frequency Support Chat traffic from Django ORM to Axum and SQLx. Django remains the Support Chat control plane.

## Axum-owned traffic

- Team inbox, unread summary, conversation detail, and message timeline reads
- Visitor and team message creation
- Delivery and read receipts
- Manual claim and automatic assignment routing
- Approved attachment association after Django/Celery scanning
- Support call lifecycle, media state, and HTTP signaling fallback
- Origin-bound widget authorization and visitor rate limiting

The inbox list uses batched SQLx reads rather than one ORM or SQL query per conversation. Durable message, assignment, receipt, and call lifecycle events use the transactional outbox and direct JetStream publisher. Disposable call signaling uses Core NATS and a bounded, expiring local fallback queue.

## Django-owned control plane

- Support account, websites, agents, teams, routing configuration, and billing
- Widget session creation, uploads, antivirus, object storage, and private media serving
- Knowledge base, workflows, automations, analytics administration, and CSAT administration
- SLA calculations, webhook enqueueing, routing audits, and owner alerts

Axum inserts a `SupportDataPlaneJob` in the same PostgreSQL transaction as each new conversation or message. Celery processes those jobs asynchronously so Django-owned SLA, webhook, and audit work does not return to the request path. Completed handoff jobs are retained for seven days by default and cleaned in bounded daily batches.

## Required migrations

- `0023_support_data_plane_indexes`
- `0024_support_data_plane_jobs`

## Rollout

```bash
./scripts/generate-realtime-lockfile.sh
./scripts/check-axum-support-data.sh
./scripts/stack-profile.sh support-shadow
# Compare authenticated team and widget responses while the frontend remains on Django.
./scripts/stack-profile.sh support
./scripts/verify-axum-runtime.sh
```

Rollback only this plane:

```bash
./scripts/stack-profile.sh support-rollback
```

Selectors:

```env
REALTIME_EPHEMERAL_BACKEND=nats
SUPPORT_DATA_BACKEND=axum
VITE_SUPPORT_DATA_BACKEND=axum
SUPPORT_DATA_PLANE_JOB_BATCH_SIZE=100
SUPPORT_DATA_PLANE_JOB_LEASE_SECONDS=120
SUPPORT_DATA_PLANE_JOB_RETENTION_DAYS=7
```

## Safety properties

- Widget tokens are compared in constant time and bound to site key, visitor session, active account, approved origin, expiry, and block state.
- Conversation creation and automatic routing use PostgreSQL advisory locks.
- Team visibility and assignment permissions are enforced before reads, writes, receipts, claims, and calls.
- Receipt cursors are monotonic and a read acknowledgement also advances delivered state.
- Public messages and call signals are rate and size limited.
- The Axum process never scans files, relays WebRTC media, handles billing webhooks, or runs knowledge/workflow administration.
