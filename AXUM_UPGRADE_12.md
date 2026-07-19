# Upgrade 12 — Background Runtime Efficiency

This stage keeps Django business logic authoritative while bounding background work for a small production VPS.

## Changes

- Realtime outbox retention deletes published rows in bounded batches, reducing long PostgreSQL locks and WAL spikes.
- Expired upload cleanup handles a configured batch per run so file-storage I/O cannot monopolize the only Celery worker.
- Support webhook retry scheduling now leases due rows with `select_for_update(skip_locked=True)` before dispatching one short task per delivery.
- Stale webhook leases are reclaimed automatically after the configured lease duration.
- Failed broker dispatch restores the durable delivery to `pending` rather than stranding it in `processing`.
- Celery worker prefetch, broker pool, heartbeat, task limits and worker recycling are explicit and configurable.
- Task result storage remains disabled in production.

## Recommended 2 GB VPS defaults

```env
CELERY_WORKER_CONCURRENCY=1
CELERY_WORKER_PREFETCH_MULTIPLIER=1
CELERY_WORKER_MAX_TASKS_PER_CHILD=500
CELERY_BROKER_POOL_LIMIT=5
CELERY_BROKER_HEARTBEAT=30
CELERY_TASK_TIME_LIMIT=900
CELERY_TASK_SOFT_TIME_LIMIT=840

REALTIME_OUTBOX_DELETE_BATCH_SIZE=1000
REALTIME_OUTBOX_DELETE_MAX_BATCHES=20
UPLOAD_EXPIRY_BATCH_SIZE=500
SUPPORT_WEBHOOK_DISPATCH_BATCH_SIZE=100
SUPPORT_WEBHOOK_LEASE_SECONDS=120
```

Do not increase cleanup batches until PostgreSQL, Redis and object-storage measurements show adequate headroom.
