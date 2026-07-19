# Final production performance verification

This is the final measurement layer for the single-VPS Django + Axum deployment. It does not assume that a successful socket-only test proves production capacity. A recommendation is issued only when all of the following pass against the same release:

- Authenticated WebSocket capacity test
- Reconnect test
- Message API test
- Mixed WebSocket/read/write workload
- VPS resource capture
- PostgreSQL index and table-health audit
- `EXPLAIN ANALYZE` for critical Messenger, Support, outbox, and webhook queries

The generated report keeps 20% connection headroom, expires after seven days by default, and is bound to the tested container image IDs, Compose source files, and fixed performance settings. Changing the release, worker counts, queue sizes, or database connection configuration invalidates the report.

`REALTIME_MAX_CONNECTIONS` is deliberately excluded from the deployment fingerprint because the test temporarily raises that admission ceiling. The measured report still records the temporary ceiling and requires it to be at least the requested test target.

## Safety rules

- Generate k6 traffic from a separate machine near expected users.
- Do not run k6 on the 2 GB application VPS.
- Use only the generated `loadtest_*` users.
- Run `EXPLAIN ANALYZE` and failure drills in a controlled low-traffic window.
- Delete all bearer-token files after the suite.
- Restore the normal Axum admission ceiling even when a test fails.
- Never apply the largest successful socket count directly; use the report's 80% recommendation.

## 1. Prepare the VPS

To verify a production ceiling near 500 sockets, test about 650:

```bash
CAPACITY_TEST_ACK=TEMPORARILY_RAISE_SOCKET_LIMIT \
  ./scripts/begin-capacity-test.sh 650

./scripts/prepare-load-test-data.sh 650 final-650
```

Start resource capture in a separate VPS terminal before launching k6. Allow enough time for all scenarios:

```bash
./scripts/capture-load-test-metrics.sh \
  1800 5 loadtests/results/final-suite/vps-final-650.jsonl
```

Securely copy `loadtests/data/users.json` to the external generator.

## 2. Run k6 externally

```bash
export LOADTEST_BASE_URL=https://your-domain.example
export LOADTEST_WS_URL=wss://your-domain.example/ws
export LOADTEST_ORIGIN=https://your-domain.example
export LOADTEST_DATA_FILE=loadtests/data/users.json
export LOADTEST_RESULT_DIR=loadtests/results/final-suite

./scripts/run-load-test.sh smoke 10
./scripts/run-load-test.sh capacity 650
./scripts/run-load-test.sh api 10
./scripts/run-load-test.sh reconnect 250

MIXED_READ_RATE=20 \
MIXED_WRITE_RATE=5 \
  ./scripts/run-load-test.sh mixed 500
```

The mixed scenario keeps authenticated sockets open while concurrently loading:

- Conversation lists
- Message pages
- Message creation and durable outbox publication
- Redis Stream consumption and Axum fanout
- Presence, typing, pings, grants, and tickets

Copy the k6 result directory back to the VPS under `loadtests/results/final-suite`.

## 3. Capture PostgreSQL evidence

Choose an owner or agent who has both Messenger conversations and an active Support Chat context. The command stores only internal user IDs and plan metadata, not SQL text or query parameters.

```bash
PERFORMANCE_PLAN_ACK=RUN_EXPLAIN_ANALYZE \
  ./scripts/capture-postgres-performance.sh \
  owner@example.com \
  <representative-messenger-conversation-id> \
  loadtests/results/final-suite
```

This performs:

- Critical index presence, validity, readiness, and scan audit
- Hot-table dead-row and autovacuum review
- Connection-pressure and long-transaction checks
- `EXPLAIN ANALYZE` for:
  - Messenger conversation list
  - Messenger message page
  - Support Inbox
  - Realtime outbox claim
  - Support webhook claim

The plan gate fails for large-table sequential scans, disk sort spills, incomplete plan coverage, or diagnostic latency thresholds.

## 4. Restore the normal ceiling

Always restore the normal admission limit after the measurements:

```bash
./scripts/end-capacity-test.sh
```

The capacity report remains valid after this restoration because the mutable admission ceiling is not part of the deployment fingerprint.

## 5. Analyze the complete suite

```bash
python3 scripts/analyze-load-test.py \
  --summary loadtests/results/final-suite/realtime-capacity-650.json \
  --mixed-summary loadtests/results/final-suite/mixed-production-500.json \
  --server-metrics loadtests/results/final-suite/vps-final-650.jsonl \
  --postgres-audit loadtests/results/final-suite/postgres-audit.json \
  --query-plans loadtests/results/final-suite/critical-query-plans.json \
  --target-connections 650 \
  --mixed-read-rps 20 \
  --mixed-write-rps 5 \
  --output loadtests/results/final-suite/capacity-report.json

./scripts/verify-load-test-suite.sh loadtests/results/final-suite
```

A passing report requires, among other checks:

- At least 99% successful checks
- Less than 1% WebSocket, read, and write failures
- Capacity WebSocket p95 below 1.5 seconds
- Mixed list/read/write p95 below 500 ms
- Realtime control p95 below 300–350 ms
- Actual realtime events received during the mixed workload
- No dropped arrival-rate iterations
- At least 95% of target sockets observed concurrently
- At least 256 MB host memory remaining
- Container memory below 85%
- Aggregate container CPU below 80% of host CPU capacity at p95
- PostgreSQL connection usage below 85%
- No PostgreSQL deadlocks and fewer than five new temp files
- Redis below 85%, with no evictions or rejected connections
- No Axum Stream errors, malformed events, connection rejection, or container restart
- Healthy outbox and Redis Stream state for every sample
- Passing PostgreSQL audit and complete strict `EXPLAIN ANALYZE` coverage
- One unchanged deployment fingerprint for the complete measurement window

## 6. Apply only a verified recommendation

```bash
cp loadtests/results/final-suite/capacity-report.json \
   loadtests/results/capacity-report.json

CAPACITY_CHANGE_ACK=APPLY_VERIFIED_CAPACITY \
  ./scripts/apply-capacity-limit.sh \
  loadtests/results/capacity-report.json
```

The apply script rejects:

- Failed or incomplete reports
- Expired reports
- A recommendation below the requested value
- Any deployed image or fixed performance-setting drift

After the first successful suite, set:

```env
REQUIRE_VERIFIED_CAPACITY_REPORT=True
```

This makes the final production-readiness command require a current measured report.

## 7. Cleanup and final gate

```bash
./scripts/cleanup-load-test-data.sh final-650 loadtests/data/users.json
rm -f /path/on/load-generator/users.json

./scripts/final-production-readiness.sh \
  loadtests/results/capacity-report.json
```

Run a new complete suite after changing any application image, dependency lockfile, Compose performance setting, Gunicorn worker/thread count, Celery worker configuration, Axum queue capacity, or database connection configuration.
