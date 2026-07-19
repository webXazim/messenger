# Axum realtime setup status

The production realtime architecture is complete:

- Django and Gunicorn own HTTP business operations.
- Axum is the sole WebSocket runtime.
- Every socket uses independent split read/write tasks.
- Bounded priority queues protect memory and calls from typing bursts.
- Django signs short-lived tickets and scoped audience/call grants.
- PostgreSQL outbox plus Redis Stream protects durable event handoff.
- R2 handles attachments and Cloudflare TURN handles relayed media.
- Redis remains for Celery, cache, security state, presence, and the Stream bridge.
- Monitoring, encrypted off-VPS backups, rollback, canaries, load tests, and restart drills are included.

The production connection limit is intentionally a measured setting. Keep the conservative default until a capacity report passes on the actual VPS. Do not treat registered users, daily users, and simultaneous sockets as the same capacity measurement.

## Final verification tooling

Upgrade 8 adds authenticated k6 WebSocket/API tests, controlled temporary capacity ceilings, VPS resource capture, reconnect/failure drills, and a report-enforced production limit with 20% headroom.
