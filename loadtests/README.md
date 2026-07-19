# Production-like load testing

Run k6 from a separate machine in the same broad geography as expected users. Do not generate load from the 2 GB application VPS.

The final suite uses real Django bearer authentication, short-lived realtime tickets, scoped grants, the public `/ws` endpoint, normal conversation/message APIs, VPS resource capture, PostgreSQL audits, and critical `EXPLAIN ANALYZE` plans.

Required sequence:

1. Temporarily raise Axum admission capacity with `scripts/begin-capacity-test.sh`.
2. Create isolated users with `scripts/prepare-load-test-data.sh`.
3. Start `scripts/capture-load-test-metrics.sh` on the VPS.
4. Run `smoke`, `capacity`, `api`, `reconnect`, and `mixed` modes externally.
5. Run `scripts/capture-postgres-performance.sh` in a controlled low-traffic window.
6. Restore the normal admission ceiling with `scripts/end-capacity-test.sh`.
7. Run `scripts/analyze-load-test.py` with all five evidence files.
8. Run `scripts/verify-load-test-suite.sh`.
9. Remove temporary users and bearer-token files.

Capacity reports expire and are bound to the tested release. See `docs/LOAD_TESTING.md` for exact commands and thresholds.
