# Upgrade 13 — measured production performance

This final optimization stage replaces guessed capacity and speculative indexing with a deployment-bound evidence chain.

## Added

- Mixed authenticated WebSocket + HTTP read/write k6 workload
- PostgreSQL critical-index and table-health audit command
- Critical `EXPLAIN ANALYZE` capture for Messenger, Support Inbox, outbox, and webhook claims
- Expanded VPS capture for PostgreSQL deadlocks/temp files/transactions and Redis evictions/operations
- Deployment fingerprint based on container image IDs, dependency/source hashes, and fixed performance settings
- Expiring schema-v2 capacity reports
- Capacity application and final-readiness fingerprint enforcement
- Synthetic analyzer pass/fail regression test
- Persistent Django database connection health checks

## Safety decisions

- No speculative migration was added. New indexes must be justified by real PostgreSQL plans.
- SQL text, parameters, bearer tokens, request bodies, and user email addresses are not stored in performance reports.
- `REALTIME_MAX_CONNECTIONS` is recorded but excluded from the immutable fingerprint so the temporary test ceiling can be restored safely.
- Any image, dependency lockfile, worker count, queue setting, or database connection change requires a new suite.

## Completion definition

The architecture is production-measured only after `scripts/verify-load-test-suite.sh` and `scripts/final-production-readiness.sh` both pass against the same non-expired report.
