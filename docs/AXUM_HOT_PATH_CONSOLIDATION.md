# Axum hot-path consolidation

This phase removes the remaining automatic Django bounce from normal Messenger message creation and from conversation operations that previously needed synchronous private-storage cleanup.

## Runtime ownership

### Axum and SQLx

Axum now owns one universal message transaction for:

- text messages;
- replies and reply edit-locking;
- formatting entities, mentions, and extracted links;
- end-to-end encrypted message envelopes;
- approved encrypted and unencrypted attachments;
- view-once attachment flags;
- voice-note duration and waveform metadata;
- manual or pending transcript records;
- idempotency through `client_temp_id`;
- audit records, durable outbox records, and immediate realtime delivery.

The transaction locks the active participant and conversation, enforces blocks and moderation mutes, validates approved uploads, allocates the conversation sequence, creates all message-related rows, and commits the event records together.

### Django and Celery

Django remains the schema owner and processes durable control-plane jobs that should not delay the chat request:

- push-notification fanout;
- final private-object cleanup after every participant has archived or left a conversation.

`ChatDataPlaneJob` is inserted in the same PostgreSQL transaction as the Axum mutation. Celery Beat wakes the recovery worker every five seconds. Workers claim rows with `SELECT ... FOR UPDATE SKIP LOCKED`, retry failures with bounded backoff, and remove old completed jobs after the retention period.

Django continues to own upload authorization, antivirus, media processing, billing, account recovery, administration, and complex moderation workflows.

## Deliberate remaining control-plane fallback

Group creation may still return `django_fallback_required` when centralized billing/access enforcement must run synchronously. This is not a message data-plane fallback. Normal messages, replies, encrypted messages, approved attachments, voice notes, transcripts, archive/remove/ban/leave cleanup, receipts, reactions, reads, and calls no longer require a Django retry when their Axum selector is enabled.

The complete `commands`/`hot-paths` cutover remains blocked while `CENTRAL_PAYMENTS_ENABLED` is active. A later production-hardening phase can replace this with a signed short-lived entitlement grant and asynchronous usage accounting without putting a billing network request in the message path.

## Database migration

Apply the Django-owned migration before starting the new Rust route:

```bash
python manage.py migrate --noinput
```

Migration added:

```text
chat.0023_chat_data_plane_jobs
```

## Configuration

```env
CHAT_DATA_PLANE_JOB_BATCH_SIZE=150
CHAT_DATA_PLANE_JOB_LEASE_SECONDS=120
CHAT_DATA_PLANE_JOB_RETENTION_DAYS=7
```

## Validation

```bash
./scripts/check-axum-hot-paths.sh
```

The check verifies the universal transaction, upload and E2EE fields, transcript persistence, durable control jobs, frontend fallback removal, migration state, Compose YAML, Python syntax, and deployment scripts.

## Cutover

The alias below activates the complete previously migrated Messenger and Support data planes after applying migrations:

```bash
./scripts/stack-profile.sh hot-paths
```

`commands` remains an equivalent alias:

```bash
./scripts/stack-profile.sh commands
```

The profile starts Django/Celery first, applies migrations, then rebuilds the frontend and Axum services. It also runs the runtime verifier after activation.

For a staged rollout, continue enabling the existing individual selectors first (`reads`, `interactions`, `mutations`, `calls`, `attachments`, `conversations`, `support`, and `outbox`), then use `hot-paths` only after their checks pass.

## Rollback

Selector rollback remains available for the independently migrated planes. Because this phase changes the internal Axum message implementation and introduces durable cleanup behavior without a new selector, a full source rollback should deploy the immediately previous retained release while leaving migration `0023` applied. The new table is additive and safe to retain.

Do not reverse the migration while pending jobs exist. Check first:

```bash
python manage.py shell -c "from apps.chat.models import ChatDataPlaneJob; print(ChatDataPlaneJob.objects.exclude(status='completed').count())"
```

## Operational checks

Watch the pending/failed job count and oldest due age. A growing queue means push fanout or object-storage cleanup is failing, but it must not affect message creation latency.

```bash
python manage.py shell -c "from apps.chat.models import ChatDataPlaneJob; from django.utils import timezone; q=ChatDataPlaneJob.objects.exclude(status='completed'); print({'pending': q.count(), 'oldest': q.order_by('available_at').values_list('available_at', flat=True).first(), 'now': timezone.now()})"
```

The next phase should use a separate Rust media worker for CPU-heavy media inspection and processing. Those jobs must not execute inside the Axum realtime process.
