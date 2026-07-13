# Messenger UI architecture

## Layout contract

Desktop messenger routes use the shared application shell with a compact navigation rail. The active conversation route owns a conversation list, a resizable chat surface, and an optional details panel. On mobile, list routes use the shared bottom navigation; active chat and call-room routes enter focus mode and hide global navigation.

The active chat always follows this structure:

1. fixed conversation header
2. independently scrollable message timeline
3. fixed composer dock
4. optional responsive details drawer

Only the timeline scrolls inside the active conversation viewport.

## Style structure

```text
frontend/src/styles/
├── foundation/
│   ├── tokens.css
│   ├── reset.css
│   ├── typography.css
│   ├── primitives.css
│   └── layout.css
├── components/
│   ├── navigation.css
│   ├── page-shell.css
│   ├── messages.css
│   ├── composer.css
│   ├── conversation-details.css
│   └── overlays.css
├── pages/
│   ├── auth.css
│   ├── conversations.css
│   ├── conversation.css
│   ├── calls.css
│   ├── call-room.css
│   ├── contacts.css
│   ├── groups.css
│   ├── saved.css
│   └── settings.css
└── index.css
```

`index.css` is the only stylesheet imported by `main.tsx`. No legacy or late override stylesheet is used.

## Component boundaries

- `components/navigation/` owns route navigation.
- `components/conversations/` owns inbox and sidebar rows.
- `components/conversation/` owns the chat header and details sections.
- `components/messages/` owns text, media, attachment, voice, call-event, metadata, reply, reaction, and action rendering.
- `components/composer/` owns reply/edit context and upload queue presentation.
- route components own data loading and business interactions, not reusable visual primitives.

## Voice recording contract

Voice recording is rendered inside the existing composer surface rather than in a second floating bar. The microphone starts a tap-to-record session, the live waveform is driven by `AudioContext`/`AnalyserNode` microphone amplitude, and the composer swaps in discard, timer, waveform, preview, stop, and send controls without resizing the chat dock. Stopping creates an in-place preview; sending while recording finalizes and uploads immediately.

The recorder must always release microphone tracks, animation frames, object URLs, and audio contexts after send, discard, error, or unmount. Voice-note upload payloads and backend duration metadata remain unchanged.

## Safety rules

- Do not change API payloads, WebSocket event names, E2EE contracts, or WebRTC behavior for visual work.
- Do not reintroduce global overrides or `!important`.
- Use design tokens and `ms-` prefixed BEM classes.
- Keep authenticated media and downloads behind the existing token-aware media helpers.
- Preserve keyboard focus, reduced-motion handling, mobile safe-area spacing, and independent chat scrolling.

## Validation

Before release:

```bash
cd frontend
npm ci
npm run build
```

Then run the backend checks in the configured Python environment and test at 320, 375, 430, 768, 1024, 1280, and 1440 pixel widths.

## Media storage and authenticated previews

Message media is private and is served through authenticated API preview/download endpoints. The frontend loads same-origin/API media as authenticated blobs and refreshes expired signed URLs automatically. Do not expose `private_media` through Nginx.

Docker stores attachments in the named `private_media` volume. Normal rebuilds preserve that volume. Avoid `docker compose down -v` unless deleting all local data is intentional.

Check database records against storage with:

```bash
python manage.py check_chat_media --fail-on-missing
```

A missing database-referenced file must be restored from the `private_media` volume backup; it cannot be reconstructed from message metadata.
