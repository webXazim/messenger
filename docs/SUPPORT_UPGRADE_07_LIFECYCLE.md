# Support Chat Project Upgrade 07 — Lifecycle and collaboration

## Added
- Controlled lifecycle transition service
- Snooze and automatic wake
- Resolution and closure reasons
- Reopen count and revision number
- Follow-up assignee field
- Conversation followers
- Private note mention model
- Immutable transfer history
- Viewer heartbeat and collision-presence API
- Versioned Support-only realtime events
- Celery task for waking due snoozed conversations

## New endpoints
- POST `/api/support/conversations/<id>/lifecycle/`
- POST/DELETE `/api/support/conversations/<id>/snooze/`
- PUT `/api/support/conversations/<id>/follow/`
- POST `/api/support/conversations/<id>/transfer/`
- GET/POST `/api/support/conversations/<id>/viewers/`

## Safety
The Inbox component and stylesheet are intentionally unchanged. These APIs can be connected to the existing UI later without replacing its frame.
