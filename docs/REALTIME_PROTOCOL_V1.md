# Realtime Protocol v1

This document defines the event envelope used by the Axum realtime service.
Django remains authoritative for durable business actions; Axum is the only production WebSocket transport.

## Server event envelope

```json
{
  "type": "chat.event",
  "version": 1,
  "event": "message.created",
  "event_id": "4b9e18c2-c056-4dcc-993d-9ad2e40bc233",
  "occurred_at": "2026-07-19T10:15:30.000000+00:00",
  "data": {}
}
```

Clients must deduplicate by `event_id`. Unknown fields must be ignored so the protocol can evolve safely.

## Audience kinds

- `conversation`: participants currently subscribed to a Messenger or shared chat conversation.
- `user`: every active device belonging to one Messenger user.
- `support_website`: authenticated Support Chat team sockets subscribed to one website.
- `support_visitor`: public widget sockets belonging to one visitor.
- `support_user`: every Support Chat socket belonging to one owner or agent.

Audience descriptors are internal transport metadata and are not sent to clients.

## Durability classes

Durable events represent committed state and must be recoverable from PostgreSQL:

- Messages and message mutations
- Delivery/read receipts
- Conversation/workflow changes
- Call lifecycle records
- Agent assignment changes

Ephemeral events may be dropped under backpressure:

- Typing
- Presence heartbeat/update
- Temporary media indicators
- WebRTC negotiation signals after the client can renegotiate

## Required production transport

```env
REALTIME_TRANSPORT=axum
REALTIME_OUTBOX_ENABLED=true
REALTIME_STREAM_ENABLED=true
REALTIME_AUTH_ENABLED=true
```

Durable state is committed in PostgreSQL before its outbox event is published to the Redis Stream.
Clients recover any missed durable events through the Django HTTP synchronization APIs.

## Authenticated Axum connection

Django issues a short-lived RS256 ticket for `aud=crescentsphere-realtime`. The browser presents it only during the Axum WebSocket upgrade:

```text
/ws?ticket=<single-use-ticket>
```

The ticket is bound to the actor, browser Origin, protocol version, device metadata, and initial self audiences. It must not be reused after a successful or capacity-rejected authentication attempt.

## Client control envelope

```json
{
  "version": 1,
  "event": "audience.subscribe",
  "request_id": "client-generated-id",
  "data": {}
}
```

## Authorized subscription

Non-self audiences require a Django-issued grant:

```json
{
  "version": 1,
  "event": "audience.subscribe",
  "request_id": "subscribe-1",
  "data": {
    "audience": {"kind": "conversation", "id": "conversation-uuid"},
    "grant": "signed-grant"
  }
}
```

Grant validation is exact. A grant for one actor, Origin, or audience cannot authorize another.

## Initial audiences

The following audiences may be embedded in a ticket and subscribed automatically:

- Authenticated user: own `user` audience and, when applicable, own `support_user` audience.
- Public widget: its own `support_visitor` audience.

Conversation and `support_website` audiences are never accepted as automatic ticket audiences.
