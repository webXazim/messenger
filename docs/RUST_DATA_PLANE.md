# Rust chat data plane

The target architecture keeps Django as a control plane and moves latency-sensitive
Messenger work to Axum, SQLx, and NATS.

## Rust-owned now

- the only WebSocket endpoint and connection lifecycle;
- presence leases, typing, signaling, bounded fanout, and slow-client handling;
- NATS JetStream durable consumption and Core NATS multi-node delivery;
- idempotent plain-text message creation;
- idempotent delivered/read receipts and one-reaction-per-user message reactions;
- owner-authorized message edit, sender delete/restore, and failed-message retry;
- SQLx conversation lists, conversation details, latest-first message timelines, message context, unread counts, and shared-media reads;
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

1. call heartbeat/media state, quality reports, speaker state, and signaling fallback;
2. direct JetStream publication with Celery limited to outbox recovery;
3. attachment finalization after Django authorizes a pending upload;
4. group membership and conversation preference commands;
5. Support Chat message reads/commands, preserving its separate authorization model.

Do not duplicate migrations in Rust. Django remains the schema owner; Rust startup and
deployment readiness must fail when the applied schema is incompatible.

For a safe pre-cutover probe, set `CHAT_READ_BACKEND=sqlx_shadow` while keeping
`VITE_CHAT_READ_BACKEND=django`. The Rust read routes remain available for authenticated
contract comparisons, but normal frontend traffic continues to use Django.

## Activation

Prepare the pinned Rust dependency lockfile, then apply the Django-owned hot-read indexes:

```text
./scripts/generate-realtime-lockfile.sh
docker compose exec -T web python manage.py migrate --noinput
```

Probe the Rust routes without changing normal frontend traffic:

```text
./scripts/stack-profile.sh reads-shadow
```

After authenticated contract checks pass, cut only the read plane over:

```text
./scripts/stack-profile.sh reads
```


Probe receipts and reactions without changing frontend traffic:

```text
./scripts/stack-profile.sh interactions-shadow
./scripts/check-axum-interactions.sh
```

Cut over only delivered/read receipts and reactions:

```text
./scripts/stack-profile.sh interactions
```

`CHAT_INTERACTION_BACKEND` is independent from `CHAT_COMMAND_BACKEND`, so this
profile does not activate Axum message creation or call commands.

Apply migration `0022_message_sender_restore_state`, then probe message mutations
without changing frontend traffic:

```text
docker compose exec -T web python manage.py migrate --noinput
./scripts/check-axum-message-mutations.sh
./scripts/stack-profile.sh mutations-shadow
```

Cut over only edit, delete, sender restore, and failed-message retry:

```text
./scripts/stack-profile.sh mutations
```

`CHAT_MESSAGE_MUTATION_BACKEND` is independent from `CHAT_COMMAND_BACKEND` and
`CHAT_INTERACTION_BACKEND`. Only messages deleted by their sender after migration
are sender-restorable; moderation and legacy deletions remain protected.

The existing combined command profile remains available through
`./scripts/stack-profile.sh commands`. It activates both Axum commands and SQLx reads.
The frontend image is rebuilt because both frontend backend selectors are build-time
flags. Deployment verifies the Axum/SQLx runtime state and probes the Rust routes.
