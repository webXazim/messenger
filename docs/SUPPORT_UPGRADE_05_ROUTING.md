# Support Chat Upgrade 05 — Routing and assignment

This upgrade adds a Support-only, per-website routing engine. It does not change the existing Inbox frame or any Messenger behavior.

## Assignment modes

- Manual
- Round robin
- Least busy

## Eligibility rules

Automatic assignment only considers agents who:

- belong to the same Support account;
- are active platform users and active Support agents;
- are currently available;
- can access the website;
- belong to the website's default team when one exists;
- remain below their active-conversation capacity, unless the configured overflow policy explicitly uses least-busy overflow.

## Concurrency and reliability

- Conversation, policy, routing cursor, and candidate agent rows are locked transactionally.
- Round-robin cursor updates are persisted and audited.
- Manual assignment now enforces capacity.
- Assignment records include team, timestamp, and trigger.
- Unassigned routing decisions are audited.
- `notify_owner` overflow creates a persistent Support service alert.
- Offline-agent reassignment runs every minute through Celery Beat.

## API

- `GET /api/support/routing-policies/`
- `PATCH /api/support/routing-policies/<website_id>/`

## Validation

Run inside the application environment:

```bash
./scripts/check-support-upgrade-05.sh
```
