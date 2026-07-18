# Support guest audio and video calls

## Purpose

Support guest calls let an authorized Support owner or agent call the visitor in
an active website conversation. They share the deployment's WebRTC and TURN
infrastructure, but all business records, access checks, signaling, settings, and
UI remain Support-only.

## Lifecycle

1. A team member starts an audio or video call from a Support conversation.
2. The backend verifies Support entitlement, website access, call settings, the
   visitor session, and active-call constraints.
3. The widget receives an incoming-call event and the visitor accepts or declines.
4. Both sides request short-lived TURN credentials using their authenticated
   Support identity.
5. SDP/ICE signaling is persisted briefly and also delivered through isolated
   Support realtime events. Polling remains available during socket interruption.
6. Ending, declining, timeout, maximum duration, or maintenance closes the call and
   records an audited Support event.

## Authorization

- Team users must currently have access to the conversation's website.
- Visitor endpoints require the matching signed widget session and approved origin.
- The same visitor identity that owns the conversation must accept and signal.
- One active call is allowed per Support conversation.
- One active initiated Support call is allowed per team user.
- Signaling IDs are globally unique and safe to replay only for the exact same call,
  sender, type, and payload.

## Responsive behavior

- Desktop uses a centered, bounded call overlay.
- Small screens use a full-height call surface with safe-area-aware controls.
- Remote video fills the stage and local video stays as a compact overlay.
- Audio calls use a simplified identity stage.
- The public widget provides incoming-call, voice/video, mute/camera, and end-call
  controls within its existing mobile-safe panel.

## Operations

The scheduled task `apps.support.tasks.maintain_support_calls` runs every minute.
It marks unanswered calls missed, ends calls over the configured account duration,
and removes stale signaling data.

Before enabling calls, run:

```bash
python manage.py check_support_readiness --fail-on-warning
```

Test UDP and TCP TURN paths from two external networks. Do not validate only from
localhost or two devices on the same Wi-Fi network.
