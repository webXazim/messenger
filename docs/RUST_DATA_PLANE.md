# Rust chat data plane

The target architecture keeps Django as a control plane and moves latency-sensitive
Messenger work to Axum, SQLx, and NATS.

## Rust-owned now

- the only WebSocket endpoint and connection lifecycle;
- presence leases, typing, signaling, bounded fanout, and slow-client handling;
- NATS JetStream durable consumption and Core NATS multi-node delivery;
- idempotent plain-text message creation;
- call creation, recent/detail reads, accept, decline, and end;
- immediate local delivery after a SQLx transaction commits, backed by the existing
  PostgreSQL outbox for restart and multi-node recovery.

Rust writes the existing Django-managed PostgreSQL schema. This preserves rollback,
admin visibility, and one migration authority while removing Django from the hot path.

## Django control plane retained intentionally

- schema migrations and admin;
- account login, token refresh, and RSA credential issuance;
- realtime subscription and call-grant issuance;
- Cloudflare TURN credential generation;
- object-storage upload authorization, antivirus/media processing, and view-once access;
- billing, support workflow administration, and Celery background jobs.

## Remaining data-plane migration

The next SQLx endpoints should be moved in this order:

1. conversation and message timeline reads;
2. receipts, reactions, edit/delete, and call heartbeat/media state;
3. attachment finalization after Django authorizes a pending upload;
4. group membership and conversation preference commands;
5. Support Chat message reads/commands, preserving its separate authorization model.

Do not duplicate migrations in Rust. Django remains the schema owner; Rust startup and
deployment readiness must fail when the applied schema is incompatible.

## Activation

Use `./scripts/stack-profile.sh commands`. It activates:

```text
CHAT_COMMAND_BACKEND=axum
CHAT_READ_BACKEND=sqlx
VITE_CHAT_COMMAND_BACKEND=axum
```

The frontend image must be rebuilt because `VITE_CHAT_COMMAND_BACKEND` is a build-time
flag. Deployment verifies the Axum/SQLx runtime state and probes the Rust call route.
