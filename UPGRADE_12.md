# Upgrade 12 — Support guest calls and production hardening

This final controlled upgrade adds Support-only guest audio/video calls between an
owner or permitted agent and a signed website visitor. Personal Messenger calling
continues to use its existing `CallSession` and `CallParticipant` models, APIs,
WebSockets, notifications, and UI without Support-facing changes.

## Isolation model

Support calls use dedicated records:

- `SupportCallSettings`
- `SupportCallSession`
- `SupportCallParticipant`
- `SupportCallSignal`

A Support call belongs to one Support conversation and therefore one website and
visitor. It never creates Messenger conversation participants or Messenger call
records. Visitor access remains bound to the signed widget session and approved
origin. Team access is revalidated against current website permissions.

## Enablement order

Keep calling disabled during the first deployment:

```env
SUPPORT_CALLS_ENABLED=False
```

Then:

1. Back up PostgreSQL, private media/R2 metadata, and the deployed source.
2. Deploy the code and apply `support.0011_supportwidgetsettings_allow_audio_calls_and_more`.
3. Confirm Redis, the ASGI WebSocket path, Celery worker, Celery Beat, and coturn are healthy.
4. Run `python manage.py check_support_readiness` in the web container.
5. Enable audio/video for one test website from **Support Chat → Websites**.
6. Review account-wide duration/video controls in **Support Chat → Settings**.
7. Set `SUPPORT_CALLS_ENABLED=True` only for staging or a selected production account.
8. Test from two real external networks before broader release.

## Configuration

```env
SUPPORT_CHAT_ENABLED=True
SUPPORT_WIDGET_ENABLED=True
SUPPORT_WIDGET_REQUIRE_ORIGIN=True
SUPPORT_CALLS_ENABLED=False
SUPPORT_CALL_RING_TIMEOUT_SECONDS=45
SUPPORT_CALL_SIGNAL_MAX_BYTES=131072
SUPPORT_CALL_ACTION_RATE=30/min
SUPPORT_CALL_SIGNAL_RATE=240/min
```

Production calls also require the existing TURN settings and shared Redis channel
layer. Run both Celery worker and Celery Beat; the maintenance task expires missed
calls, enforces maximum durations, and removes stale signaling records.

## Verification

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py check_support_readiness
cd frontend && npm run check:product
```

The Support readiness command fails when calling is enabled without the widget,
origin enforcement, TURN authentication, shared Redis realtime, or required Beat
tasks.
