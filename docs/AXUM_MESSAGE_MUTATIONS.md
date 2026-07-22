# Axum message mutations

This release moves the latency-sensitive message lifecycle commands to Axum and
SQLx while Django remains the schema, moderation, and administration authority.

## Routes

Authenticated Axum routes under `/api/v1/chat-fast`:

- `PATCH /messages/{message_id}/manage/` — edit text or encrypted envelope;
- `DELETE /messages/{message_id}/manage/` — sender soft delete;
- `POST /messages/{message_id}/restore/` — restore a sender-deleted message;
- `POST /messages/{message_id}/retry/` — retry a failed message.

The existing Django routes remain available for immediate rollback.

## Authorization and concurrency

Every operation resolves the authenticated local user, locks the message and active
conversation participant rows, and enforces ownership, bans, block state, moderation
mute state, edit-window policy, reaction/activity locks, and E2EE key coverage.
Mutations, edit history, audit log, and outbox event are committed in one PostgreSQL
transaction. Local fanout happens only after commit.

## Restore safety

Migration `0022_message_sender_restore_state` adds a private text backup and deletion
source. Sender deletion stores the original text and marks the source as `sender`.
Only that exact state can be restored by the sender. Moderation deletions and legacy
deletions with no source cannot be restored through the sender API. Staff moderation
restore remains in Django.

The text backup and deletion source are never serialized to clients. Clients receive
only `can_restore` and `restore_locked_reason`.

## Rollout

```bash
docker compose exec -T web python manage.py migrate --noinput
./scripts/check-axum-message-mutations.sh
./scripts/stack-profile.sh mutations-shadow
```

Compare authenticated Django and Axum responses and inspect audit/outbox rows. Then:

```bash
./scripts/stack-profile.sh mutations
```

Rollback by restoring the `.env` snapshot created by `stack-profile.sh` or setting:

```env
CHAT_MESSAGE_MUTATION_BACKEND=django
VITE_CHAT_MESSAGE_MUTATION_BACKEND=django
```

Rebuild the frontend after changing its build-time selector.
