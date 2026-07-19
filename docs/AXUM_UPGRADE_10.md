# Upgrade 10 — Batching and Support Inbox pagination

This stage keeps the Django/Axum boundary unchanged and removes remaining avoidable work from high-volume Support reads.

## Changes

- Added signed, expiring keyset cursors for the Support Inbox.
- Preserved legacy `offset`/`next_offset` fields while exposing `next_cursor` for scalable clients.
- Cursor ordering uses `(last_message_at or created_at, conversation_id)` for stable deterministic pages.
- Invalid or expired cursors return `400 invalid_cursor` rather than producing inconsistent pages.
- Removed database writes from `SupportWebsiteSerializer`.
- Reused prefetched invitation and knowledge-article website assignments across serializer fields.
- Kept all authorization, unread annotations, response fields and realtime behavior intact.

## Client migration

Clients may continue using offsets. New clients should pass the returned `next_cursor` as `cursor` and keep the same filters. A cursor is signed and valid for 24 hours.
