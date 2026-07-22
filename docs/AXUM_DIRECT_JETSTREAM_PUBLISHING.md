# Axum direct JetStream publishing

Durable SQLx commands now use this path:

```text
PostgreSQL transaction
  -> business mutation + outbox row commit
  -> immediate same-node websocket delivery
  -> Axum publishes the committed event to JetStream
  -> JetStream acknowledgement
  -> Axum marks the outbox row published
  -> the durable consumer routes the event to owning Axum nodes
```

`Nats-Msg-Id` is the stable outbox `event_id`. A crash after JetStream accepts an event but before PostgreSQL is marked can therefore be retried safely. The consumer and immediate local delivery share the same in-process event guard. Celery recovery updates are lease-aware, so a recovery sweep cannot overwrite an outbox row that Axum has already marked published.

## Modes

- `REALTIME_OUTBOX_PUBLISHER=celery`: rollback mode. Axum delivers locally and Celery publishes pending rows.
- `REALTIME_OUTBOX_PUBLISHER=axum`: Axum is the primary publisher for committed SQLx events. Celery does not receive request-side wakeups and only sweeps pending or failed rows.

Django-owned background and administrative mutations still write the same outbox table. In Axum mode, the periodic Celery recovery sweep publishes those lower-frequency rows.

## Rollout

```bash
./scripts/check-axum-direct-outbox.sh
./scripts/stack-profile.sh outbox
./scripts/stack-profile.sh status
```

Rollback:

```bash
./scripts/stack-profile.sh outbox-rollback
```

No schema migration is required.
