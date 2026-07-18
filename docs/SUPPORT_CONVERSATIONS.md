# Support Chat conversation isolation

Support Chat reuses the proven `chat.Conversation` and `chat.Message` storage layer without making website visitors Messenger users or participants. Personal Messenger URLs, participant queries, E2EE, calls, presence, and unread state remain unchanged.

## Data boundary

- A Support conversation has one `SupportConversation` record linked one-to-one to a chat conversation.
- Every Support conversation belongs to exactly one Support website and one external visitor.
- Support conversations have no `ConversationParticipant` rows, so Messenger conversation selectors cannot return them.
- Visitor messages use `Message.sender = NULL` plus a required `SupportMessageAuthor` record tied to the visitor and signed widget session.
- Owner and agent replies use their existing platform user identities but do not create Messenger friendships or participants.
- Team read state is stored in `SupportConversationReadState`; visitor read state is stored on the Support conversation. Neither affects Messenger receipts.

## Access boundary

Owners can access every website in their Support account. Agents can access only websites assigned through `SupportWebsiteAgent`. Agents without `can_view_all_conversations` see their assigned conversations and unassigned conversations on those websites. Every detail, message, claim, and update endpoint resolves through the same scoped selector.

## Assignment

- Owners can assign or unassign any eligible agent.
- Permitted agents can reassign within their assigned website.
- Agents can atomically take an unassigned conversation.
- Claims enforce each agent's active-conversation capacity.
- Assignment always references a Support agent, never a generic Messenger user.

## Public widget

The widget uses the existing origin-bound signed visitor session. A visitor can start or resume one conversation per visitor identity, send text messages, read the team response, and retain the session after refresh. Public responses remain `no-store`. Polling is the delivery fallback until Support-specific realtime events are enabled.

## Responsive inbox

The Support inbox uses the Messenger application shell and design tokens. Desktop shows queue, conversation, and details panels. Tablet reduces to two panels with collapsible details. Mobile switches between the queue and the selected conversation, preserving safe-area spacing and full-width touch actions.

## Deployment flags

```env
SUPPORT_CHAT_ENABLED=True
SUPPORT_WIDGET_ENABLED=True
SUPPORT_WIDGET_REQUIRE_ORIGIN=True
SUPPORT_WIDGET_MESSAGE_RATE=60/min
```

Do not enable the public widget before applying migrations and validating every website's allowed origins.
