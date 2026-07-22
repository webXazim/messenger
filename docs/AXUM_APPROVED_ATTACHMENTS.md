# Axum approved-attachment data plane

## Scope

This phase moves only low-latency operations that happen after Django/Celery has
accepted and scanned an upload:

- attach a clean pending Messenger upload to a new message;
- read authorized attachment metadata;
- authorize and issue short-lived attachment download/preview tokens.

Django and Celery permanently retain upload policy, multipart upload handling,
antivirus scanning, thumbnail/transcode work, Cloudflare R2 configuration,
private-file serving, view-once consumption, and media background jobs.

## Storage ownership

Axum does not upload, copy, delete, or transform objects. After locking a clean
`PendingUpload`, it creates the immutable message-attachment database record
using the approved private-storage object key and marks the pending row
`attached` in the same PostgreSQL transaction. Attached pending rows are not
processed by the stale-pending cleanup task. This avoids an R2 copy on the chat
request path while preserving the existing private bucket and Django download
handlers.

Do not add a cleanup job that deletes files belonging to `attached` pending
rows unless object-reference tracking is introduced first.

## Universal message compatibility

The attachment endpoint now delegates to the same universal SQLx message
transaction used by normal Axum message creation. It supports:

- message and attachment E2EE envelopes;
- replies and reply edit-locking;
- rich entities and mentions;
- voice-note duration and waveform metadata;
- manual or pending transcript metadata;
- view-once creation for clean images and videos.

There is no automatic Axum-to-Django retry in the attachment message path.
View-once opening and token consumption remain in Django, and Axum still refuses
standard media tokens for view-once attachments.

## Shared media token

Axum and Django use the same dedicated HS256 secret, issuer, audience, TTL, and
claim contract. This secret must be independent from `SECRET_KEY`, randomly
generated, and at least 32 characters. Django accepts the new JWT and legacy
Django-signer tokens during rollback.

Generate a production secret, for example:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

Configure both services through the shared environment:

```env
MEDIA_TOKEN_SHARED_SECRET=<random-secret>
MEDIA_TOKEN_ISSUER=crescentsphere-media
MEDIA_TOKEN_AUDIENCE=crescentsphere-private-media
MEDIA_TOKEN_TTL_SECONDS=300
```

## Rollout

Run source contracts first:

```bash
./scripts/check-axum-attachments.sh
```

Expose authenticated routes while frontend traffic remains on Django:

```bash
./scripts/stack-profile.sh attachments-shadow
```

Test clean/expired/rejected/already-attached uploads, duplicate client IDs,
blocked and muted participants, E2EE envelopes, replies, voice metadata,
transcripts, metadata access, and media tokens. Then cut over only this phase:

```bash
./scripts/stack-profile.sh attachments
./scripts/verify-axum-runtime.sh
```

Rollback without changing other data planes:

```bash
./scripts/stack-profile.sh attachments-rollback
```

## Database and event behavior

Apply `chat.0023_chat_data_plane_jobs` before cutover. The rollout profile runs
Django migrations automatically. Message, attachment, pending-upload state,
audit log, durable follow-up job, and durable outbox event are committed in one
SQLx transaction. The existing
direct JetStream publisher handles the committed `message.created` event; the
Celery sweep remains the recovery path for pending or failed outbox records.
