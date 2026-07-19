# Support Chat realtime and notification boundaries

Support Chat and personal Messenger share the Axum `/ws` transport, but their audiences, grants, message models, unread state, and business workflows remain isolated.

## Connection and authorization

Both authenticated owners/agents and public website visitors first request a short-lived Django-issued realtime ticket. The browser then connects to:

```text
/ws?ticket=<single-use-ticket>
```

Django validates business access before signing tickets or audience grants. Axum validates the RSA signature, issuer, audience, expiry, protocol version, actor identity, and browser Origin without querying PostgreSQL.

An authenticated owner or agent receives only its own self audience automatically. Each `support_website` subscription requires a short-lived grant for that exact actor, Origin, and website.

A public widget ticket is bound to its visitor session, website, support conversation, and the website-specific allowed Origin. The widget receives only its own `support_visitor` audience automatically.

Messenger conversation grants cannot authorize Support Chat audiences, and Support Chat grants cannot authorize Messenger conversations.

## Event delivery

Durable Support Chat actions remain Django HTTP operations:

- Visitor and agent messages
- Delivery/read positions
- Agent assignment and workflow changes
- Call lifecycle and signaling records
- Website and subscription changes

Django commits the business state and its realtime outbox row in PostgreSQL, publishes the event to the Redis Stream, and Axum delivers it to matching local sockets.

Disposable events use Axum directly:

- `support.ping` / `support.pong`
- Visitor and agent typing
- Visitor presence heartbeats

## Main events

- `support.widget.ready`
- `support.message.created`
- `support.conversation.updated`
- `support.website.updated`
- `support.access.updated`
- `support.visitor.presence`
- `support.typing.started`
- `support.typing.stopped`
- `support.call.signal`
- `support.call.accepted`
- `support.call.media_updated`
- `support.call.ended`

## Access changes

Assignment, role, website, plan, and session changes are enforced by Django when a new ticket or grant is requested. Access-changing durable events tell the frontend to refresh its authorized website list and unsubscribe stale audiences. Tickets and grants are deliberately short-lived so an old browser session cannot retain access indefinitely.

## Reconnect and fallback

The Support frontend and widget use exponential reconnect with jitter, ticket renewal, grant replay, heartbeats, and `event_id` deduplication. REST synchronization remains authoritative and recovers durable events missed during a disconnect or Axum restart.

## Unread notifications

Support unread state uses `SupportConversationReadState`, not Messenger participant receipts. Opening a Support conversation updates only that user's Support read position. Messenger unread badges and receipts remain independent.

## Production configuration

Axum is normally served from the same public origin, so the frontend can derive the WebSocket URL automatically. Set an explicit URL only when realtime uses a different public hostname:

```env
VITE_SUPPORT_WS_URL=wss://realtime.example.com/ws
```

Nginx must proxy the exact `/ws` path to Axum. `/ws/*` is intentionally rejected. Redis remains private and is used for the Django-to-Axum stream, ticket replay protection, presence, Celery, and cache—not as a Django Channels layer.
