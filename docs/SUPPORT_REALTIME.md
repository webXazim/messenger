# Support Chat realtime and notification boundaries

Support Chat uses the existing Django Channels, Redis, ASGI, and frontend shell,
but it does not use Messenger conversation groups or Messenger unread state.

## WebSocket routes

- Authenticated owner/agent: `/ws/support/?token=<access-token>`
- Website visitor: `/ws/support/widget/<site-key>/?session_id=<id>&token=<visitor-token>`

The authenticated socket joins only:

- `support.user.<user-id>`
- `support.website.<website-id>` for websites currently visible to that owner or agent

The visitor socket joins only:

- `support.visitor.<visitor-id>`

Messenger continues using its existing `/ws/chat/` route and groups.

## Permission enforcement

Joining a channel group is not treated as authorization. Every outbound Support
event is rechecked against the current database permissions before it is sent to
the socket. Website assignment changes publish a user-scoped
`support.access.updated` event. An open agent socket then drops removed website
groups and subscribes to newly assigned website groups immediately.

Visitor sockets revalidate the website, origin, visitor, session, and conversation
for every delivered event. Disabling Support Chat, revoking an agent, disabling a
website, rotating its site key, or revoking a visitor session prevents further
access without changing Messenger.

## Events

- `support.ready`
- `support.widget.ready`
- `support.message.created`
- `support.conversation.updated`
- `support.website.updated`
- `support.access.updated`
- `support.ping`
- `support.pong`

Message events include the website and conversation identifiers required to
refresh the isolated Support inbox. Generic website refresh events contain no
visitor message body and allow restricted agents to safely remove stale rows after
assignment changes.

## Reconnect and fallback

The Support frontend and public website widget use exponential reconnect, heartbeat
pings, event deduplication, and access-token refresh integration. REST polling stays
active whenever the Support socket is unavailable and stops while realtime is
healthy. This keeps Support usable behind restrictive networks without changing
Messenger's socket lifecycle.

## Unread notifications

Support unread state uses `SupportConversationReadState`, not Messenger participant
receipts. The authenticated frontend receives a separate account-wide total and
per-website counts. A message from a website that is not open can produce:

- Support Chat product badge
- website-specific inbox badge
- in-app toast
- browser notification when permission has already been granted

Opening a Support conversation updates only that user's Support read position.
Messenger unread badges and receipts are unaffected.

## Production configuration

Normally the Support WebSocket URL is derived from `VITE_API_BASE_URL`. Set this
only when WebSockets are served from a different public origin:

```env
VITE_SUPPORT_WS_URL=wss://dm.example.com/ws/support/
```

Nginx or the edge proxy must pass WebSocket upgrade headers for both `/ws/chat/`
and `/ws/support/`. Redis must be shared by all ASGI instances so website and
visitor channel groups work across containers.
