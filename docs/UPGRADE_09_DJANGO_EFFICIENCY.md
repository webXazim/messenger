# Upgrade 09 — Django HTTP efficiency

This release optimizes the Django side after Axum took ownership of persistent
WebSockets.

## Main changes

- Fixed-query Support Inbox with SQL unread annotations and targeted prefetches.
- Batched Redis presence reads for Messenger users and Support visitors.
- Batched block-visibility lookup for Messenger user collections.
- Batched user-discovery and call-participant presence reads.
- In-memory call participant summaries from prefetched rows instead of extra SQL counts.
- Reverse UserBlock index for bidirectional privacy checks.
- Prefetched call/support receipt, tag, CSAT, assignment, and attachment data.
- Partial PostgreSQL index for live conversation-message scans.
- Staggered Gunicorn worker recycling.
- Production-safe Celery and asynchronous upload-scan requirements.
- Query-growth regression coverage and Redis Stream outage durability coverage.
- Optional aggregate request/query timing middleware that never logs SQL text or parameters.
- A PostgreSQL Support Inbox EXPLAIN command for controlled query-plan review.

## Deploy

```bash
docker compose build web worker beat
docker compose run --rm web python manage.py migrate
docker compose up -d web worker beat
docker compose exec web python manage.py check --deploy
```

Keep these production values:

```env
CELERY_TASK_ALWAYS_EAGER=False
CELERY_TASK_IGNORE_RESULT=True
UPLOAD_SCAN_ASYNC=True
GUNICORN_MAX_REQUESTS=1000
GUNICORN_MAX_REQUESTS_JITTER=100
```

## Validation targets

```bash
python manage.py test \
  apps.support.tests.SupportFoundationTests.test_support_inbox_query_count_does_not_scale_with_rows \
  apps.accounts.tests.UserDiscoveryEfficiencyTests \
  apps.chat.tests.ChatApiTests.test_recent_calls_filter \
  apps.chat.tests.ChatApiTests.test_recent_calls_expires_stale_ringing_before_response
```

The response contracts are unchanged. These optimizations alter query preparation,
prefetching, and serializer context only.

## Measure safely

Enable aggregate performance logging temporarily:

```env
DJANGO_QUERY_METRICS_ENABLED=True
DJANGO_QUERY_METRICS_LOG_ALL=False
DJANGO_QUERY_METRICS_REQUEST_MS=250
DJANGO_QUERY_METRICS_DB_MS=100
DJANGO_QUERY_METRICS_MAX_QUERIES=20
```

Only method, path, status, total duration, aggregate database time, and query count
are logged. SQL text and parameters are intentionally excluded.

Inspect the optimized Support Inbox plan:

```bash
python manage.py explain_support_inbox --user owner@example.com
```

Use `--analyze` only during a controlled diagnostic window because it executes
the read query.
