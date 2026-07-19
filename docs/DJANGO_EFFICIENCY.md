# Django HTTP efficiency after the Axum cutover

Axum owns persistent sockets. Django remains the authoritative HTTP, permission,
transaction, admin, and background-job service. This release optimizes the
highest-volume Django read paths without duplicating business logic in Rust.

## Implemented

- Support Inbox unread counts use correlated SQL subqueries rather than one query
  per conversation.
- Last-message authors, attachments, support receipts, tags, agent assignments,
  CSAT, service settings, and visitor read positions are loaded in a fixed set of
  queries.
- Support visitor online state is resolved with one Redis pipeline/cache multi-get.
- Messenger user collections batch Redis presence and block visibility checks.
- User discovery/search responses batch Redis presence instead of reading one hash per row.
- Call lists reuse prefetched participants, batch presence, and compute participant summaries in Python without extra count queries.
- Reverse block lookups have a matching PostgreSQL index for privacy checks.
- Live-message unread scans use a partial PostgreSQL index.
- Gunicorn workers recycle after a staggered request count.
- Production rejects eager Celery execution, stored task results, and synchronous
  upload scanning.

## Query budget

The Support Inbox regression test requires query growth to remain effectively
constant between one and seven conversations, with a maximum budget of 16 SQL
queries for the full endpoint response. User discovery has a separate fixed-query
budget of six SQL queries while presence is resolved through one Redis pipeline.
Keep these tests when adding list fields.

Run the relevant checks with:

```bash
python manage.py test \
  apps.support.tests.SupportFoundationTests.test_support_inbox_query_count_does_not_scale_with_rows \
  apps.accounts.tests.UserDiscoveryEfficiencyTests \
  apps.chat.tests.ChatApiTests.test_recent_calls_filter
```

## Rules for future serializers

- Do not globally replace `ModelSerializer` with `.values()`.
- Use explicit lightweight serializers for flat high-volume lists.
- Any `SerializerMethodField` used in a list must consume annotations, selected
  relations, prefetched relations, or a batched context map.
- Never perform a per-row Redis lookup.
- Add a query-count regression test before adding a database-backed list field.
- Use `bulk_create` or `bulk_update` only where model hooks, audit records, and
  per-row validation are intentionally unnecessary.


## Runtime measurement

`QueryMetricsMiddleware` can log aggregate endpoint duration, database duration,
and query count without logging SQL or request parameters. It is enabled by
default only when Django `DEBUG` is enabled. Production activation is optional
and threshold-based.

For the Support Inbox PostgreSQL plan:

```bash
python manage.py explain_support_inbox --user owner@example.com
```

## Final measured-production gate

Upgrade 13 does not add speculative indexes. It captures PostgreSQL table health,
critical-index validity, and controlled `EXPLAIN ANALYZE` plans for the Messenger
conversation list, message page, Support Inbox, realtime outbox claim, and webhook
claim. A capacity recommendation is issued only when those plans, the mixed
HTTP/WebSocket workload, and VPS resource samples all pass against one unchanged
deployment fingerprint.

Use:

```bash
PERFORMANCE_PLAN_ACK=RUN_EXPLAIN_ANALYZE \
  ./scripts/capture-postgres-performance.sh \
  owner@example.com \
  loadtests/results/final-suite
```

Any application image, dependency lockfile, Gunicorn/Celery setting, Axum queue
capacity, or database connection setting change invalidates the report and requires
a new measured suite.
