# Axum conversation command plane

This phase moves routine Messenger conversation mutations from Django ORM to Axum and SQLx while preserving Django as the control plane and rollback implementation.

## Axum-owned operations

- Direct conversation creation
- Group creation when central billing access checks are disabled
- Pin, mute, and archive viewer state
- Server-side drafts for non-E2EE conversations
- Add, remove, mute, ban, and unban group participants
- Member/admin role changes and ownership transfer
- Leave group
- User block and unblock enforcement

Membership, role, ownership, block, and viewer-state mutations use SQLx transactions and durable realtime events. Draft saves intentionally avoid audit/outbox fanout because they are private, high-frequency state. Disposable typing authorization is enforced directly against PostgreSQL and continues through Core NATS.

## Control-plane boundary

Group creation still returns `422 django_fallback_required` while centralized
billing access must be checked and usage recorded synchronously by Django. This
is the only remaining conversation-command fallback.

Archive, leave, remove, and ban no longer bounce to Django when the final
retained copy may need deletion. Axum commits the conversation mutation and a
durable `conversation_cleanup` job together. Celery performs private object
storage cleanup after commit and rechecks that no participant has retained the
conversation before deleting anything.

## Block enforcement

A direct block relationship is enforced in Axum for:

- Text message creation
- Approved attachment message creation
- Failed-message retry
- New call creation
- Active call runtime/signaling
- Typing events
- New direct conversation creation

Group membership policy remains conversation-role based; blocks prevent adding blocked users to a new or existing group. Pair-presence refresh checks both block directions, so unblocking one direction cannot reveal presence while the reverse block still exists.

## Rollout

```bash
./scripts/check-axum-conversation-commands.sh
./scripts/stack-profile.sh conversations-shadow
./scripts/stack-profile.sh conversations
```

Rollback:

```bash
./scripts/stack-profile.sh conversations-rollback
```

Selectors:

```env
CHAT_CONVERSATION_COMMAND_BACKEND=axum
VITE_CHAT_CONVERSATION_COMMAND_BACKEND=axum
```

Apply the additive `chat.0023_chat_data_plane_jobs` migration before enabling
this updated command plane. The rollout profiles apply it automatically.

## Concurrency guarantees

- Direct conversation creation uses a transaction advisory lock and preserves Django's lexicographically sorted direct-key format.
- Group slug allocation uses a transaction advisory lock to avoid simultaneous unique-name races.
- Participant and ownership commands lock the conversation and relevant membership rows before authorization and mutation.
