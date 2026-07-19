# Messenger API Frontend Guide

Deprecated draft: use `docs/API_CONTRACT.md` instead. This file was left in place because the Windows patch runner could not remove it cleanly during the documentation pass.

This is the human API contract for building a web, Android, or iOS client against this backend.

The machine-readable schema is available at:

- Swagger UI: `/api/docs/`
- Redoc: `/api/redoc/`
- OpenAPI YAML: `/api/schema/`
- Saved schema file: `schema.yml`

Use this guide for exact client payloads, flow order, websocket events, and the response fields the frontend should depend on.

## Conventions

Base URL examples use `/api/v1`. Replace with the deployed API origin.

Authenticated REST requests use:

```http
Authorization: Bearer <access_token>
Content-Type: application/json
X-Device-Id: <stable-device-id>
```

`X-Device-Id` is optional but recommended for login/session tracking. Use one stable id per app install, browser profile, or tab cluster.

Most list endpoints are paginated by DRF:

```json
{
  "count": 123,
  "next": "https://api.example.com/api/v1/chat/conversations/?page=2",
  "previous": null,
  "results": []
}
```

Validation and permission errors use this envelope:

```json
{
  "success": false,
  "message": "Request failed.",
  "errors": {
    "field_name": ["Error text."]
  },
  "status_code": 400,
  "request_id": "req-123",
  "view": "SomeView"
}
```

IDs:

- User ids are integer values serialized as numbers in REST write payloads unless a field name says string.
- Conversation, message, upload, attachment, call, session, invite, report, and audit ids are UUID strings.
- Datetimes are ISO 8601 strings.

## Auth Flow

### Register

`POST /api/v1/auth/register/`

Public.

Request:

```json
{
  "username": "xim",
  "email": "xim@example.com",
  "password": "pass12345",
  "first_name": "Xim",
  "last_name": "Example"
}
```

Response is the created user:

```json
{
  "id": 1,
  "username": "xim",
  "email": "xim@example.com",
  "first_name": "Xim",
  "last_name": "Example"
}
```

### Login

`POST /api/v1/auth/login/`

Public. `username` accepts either username or email.

Request:

```json
{
  "username": "xim@example.com",
  "password": "pass12345"
}
```

Response:

```json
{
  "refresh": "<jwt-refresh>",
  "access": "<jwt-access>",
  "session_id": "00000000-0000-0000-0000-000000000000",
  "user": {
    "id": 1,
    "username": "xim",
    "email": "xim@example.com",
    "email_verified": false,
    "email_verified_at": null,
    "first_name": "Xim",
    "last_name": "Example",
    "last_seen_at": null,
    "profile": {
      "display_name": "",
      "avatar": null,
      "bio": "",
      "status_message": "",
      "latitude": null,
      "longitude": null,
      "location_updated_at": null
    },
    "social_accounts": []
  }
}
```

### Refresh Access Token

`POST /api/v1/auth/refresh/`

Public, but the refresh token must belong to an active, non-revoked session.

Request:

```json
{
  "refresh": "<jwt-refresh>"
}
```

Response:

```json
{
  "access": "<jwt-access>"
}
```

### Social Login

`POST /api/v1/auth/social/login/`

Public.

Request:

```json
{
  "provider": "google",
  "id_token": "<provider-id-token>"
}
```

Allowed providers: `google`, `apple`.

Response has the same shape as login: `refresh`, `access`, `session_id`, `user`.

### Current User

`GET /api/v1/auth/me/`

Authenticated.

Response is the `user` object from login.

`PATCH /api/v1/auth/me/`

Authenticated.

Request fields are optional:

```json
{
  "first_name": "Xim",
  "last_name": "Example",
  "email": "xim@example.com",
  "profile": {
    "display_name": "Xim",
    "avatar": null,
    "bio": "Builder",
    "status_message": "Online",
    "latitude": "24.713600",
    "longitude": "46.675300"
  }
}
```

Response is the updated `user` object.

### Password, Email, Sessions, Account

`POST /api/v1/auth/password/change/`

Authenticated.

```json
{
  "current_password": "pass12345",
  "new_password": "ChangedPass12345!"
}
```

Response:

```json
{
  "detail": "Password changed."
}
```

`POST /api/v1/auth/password/reset/request/`

Public.

```json
{
  "email": "xim@example.com"
}
```

Response:

```json
{
  "detail": "If this email exists, a reset link has been sent."
}
```

`POST /api/v1/auth/password/reset/confirm/`

Public.

```json
{
  "token": "<reset-token-from-email>",
  "new_password": "NewPass12345!"
}
```

Response:

```json
{
  "detail": "Password reset complete."
}
```

`POST /api/v1/auth/email/verify/request/`

Authenticated. No body required.

Response:

```json
{
  "detail": "Verification email sent."
}
```

`POST /api/v1/auth/email/verify/confirm/`

Public.

```json
{
  "token": "<email-verify-token>"
}
```

Response:

```json
{
  "detail": "Email verified."
}
```

`GET /api/v1/auth/sessions/`

Authenticated.

Each item:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "device_id": "web-main",
  "user_agent": "Mozilla/5.0",
  "ip_address": "127.0.0.1",
  "last_seen_at": "2026-04-17T00:00:00Z",
  "expires_at": "2026-04-24T00:00:00Z",
  "revoked_at": null,
  "is_current": true
}
```

`POST /api/v1/auth/sessions/{session_id}/revoke/`

Authenticated. No body required. Returns `204`.

`POST /api/v1/auth/sessions/revoke-all/`

Authenticated. No body required. Revokes every other active session.

Response:

```json
{
  "revoked": 2
}
```

`GET /api/v1/auth/me/export/`

Authenticated. Returns account export data including user, sessions, conversations, messages, and social accounts.

`POST /api/v1/auth/me/delete/`

Authenticated.

```json
{
  "password": "pass12345"
}
```

Returns `204`.

## User Discovery And Friends

`GET /api/v1/auth/users/search/?q=xim`

Authenticated. Empty `q` returns no results.

## Uploads And Media

`POST /api/v1/chat/uploads/`

Authenticated. Use `multipart/form-data`, not JSON.

Fields:

```text
file=<binary file>                 required
original_name=photo.jpg            optional
mime_type=image/jpeg               optional
```

Response:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "original_name": "photo.jpg",
  "mime_type": "image/jpeg",
  "size": 12345,
  "extension": "jpg",
  "status": "pending",
  "scan_status": "clean",
  "scan_notes": "",
  "scanned_at": "2026-04-17T00:00:00Z",
  "expires_at": "2026-04-18T00:00:00Z",
  "file_url": "https://api.example.com/api/v1/chat/uploads/{upload_id}/download/",
  "preview_url": "https://api.example.com/api/v1/chat/uploads/{upload_id}/preview/",
  "can_preview_inline": true,
  "signed_download": {
    "token": "<token>",
    "download_url": "https://api.example.com/api/v1/chat/uploads/{upload_id}/download/?token=<token>",
    "expires_at": "2026-04-17T00:05:00Z"
  },
  "signed_preview": {
    "token": "<token>",
    "download_url": "https://api.example.com/api/v1/chat/uploads/{upload_id}/preview/?token=<token>",
    "expires_at": "2026-04-17T00:05:00Z"
  },
  "created_at": "2026-04-17T00:00:00Z"
}
```

Only pending uploads with clean or fail-open scan status can be attached to a message.

Authenticated download and preview:

- `GET /api/v1/chat/uploads/{upload_id}/download/`
- `GET /api/v1/chat/uploads/{upload_id}/preview/`
- `GET /api/v1/chat/attachments/{attachment_id}/download/`
- `GET /api/v1/chat/attachments/{attachment_id}/preview/`

Signed-token download:

- Add `?token=<media-token>` to the same URL.

Mint signed tokens:

- `POST /api/v1/chat/uploads/{upload_id}/media-token/`
- `POST /api/v1/chat/attachments/{attachment_id}/media-token/`

No request body. Response:

```json
{
  "token": "<token>",
  "download_url": "https://api.example.com/api/v1/chat/attachments/{attachment_id}/download/?token=<token>",
  "preview_url": "https://api.example.com/api/v1/chat/attachments/{attachment_id}/preview/?token=<token>",
  "expires_at": "2026-04-17T00:05:00Z"
}
```

`GET /api/v1/chat/conversations/{conversation_id}/media/?kind=all`

Authenticated.

`kind`: `all`, `image`, `video`, `audio`, `file`.

Returns paginated attachment items with:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "original_name": "photo.jpg",
  "mime_type": "image/jpeg",
  "size": 12345,
  "width": null,
  "height": null,
  "duration_seconds": null,
  "scan_status": "clean",
  "scan_notes": "",
  "scanned_at": "2026-04-17T00:00:00Z",
  "file_url": "...",
  "preview_url": "...",
  "can_preview_inline": true,
  "signed_download": {},
  "signed_preview": {},
  "message": {
    "id": "00000000-0000-0000-0000-000000000000",
    "text": "Photo",
    "type": "image",
    "created_at": "2026-04-17T00:00:00Z"
  },
  "sender": {},
  "media_kind": "image",
  "created_at": "2026-04-17T00:00:00Z"
}
```

## Presence, Blocks, Devices, Notifications, Sync

`POST /api/v1/chat/presence/ping/`

```json
{
  "device_id": "web-main"
}
```

`device_id` defaults to `default`.

Response:

```json
{
  "user_id": "1",
  "is_online": true,
  "active_devices": ["web-main"],
  "last_seen_at": "2026-04-17T00:00:00Z",
  "server_time": "2026-04-17T00:00:00Z"
}
```

`POST /api/v1/chat/presence/disconnect/`

```json
{
  "device_id": "web-main"
}
```

`GET /api/v1/chat/blocks/`

Returns blocked users.

`POST /api/v1/chat/blocks/`

```json
{
  "blocked_user_id": 2,
  "reason": "spam"
}
```

`reason` is optional.

`DELETE /api/v1/chat/blocks/{user_id}/`

No body.

`GET /api/v1/chat/devices/`

Returns registered push devices.

`POST /api/v1/chat/devices/`

```json
{
  "platform": "web",
  "push_token": "<fcm-token>"
}
```

Allowed platforms: `android`, `web`, `ios`.

`POST /api/v1/chat/devices/deactivate/`

```json
{
  "push_token": "<fcm-token>"
}
```

`GET /api/v1/chat/notifications/preferences/`

`PATCH /api/v1/chat/notifications/preferences/`

All fields optional:

```json
{
  "push_enabled": true,
  "message_preview_enabled": true,
  "mute_all": false,
  "call_quality_preference": "auto"
}
```

`call_quality_preference`: `auto`, `low`, `mid`, `clear`.

`GET /api/v1/chat/conversations/{conversation_id}/notifications/`

`PATCH /api/v1/chat/conversations/{conversation_id}/notifications/`

```json
{
  "message_notifications_enabled": true,
  "call_notifications_enabled": true,
  "mentions_only": false,
  "muted_until": null
}
```

`GET /api/v1/chat/sync/?since=2026-04-17T00:00:00Z&conversation_id={conversation_id}&limit=100`

All query params are optional. `limit` range is 1 to 200.

Response includes changed conversations, messages, active calls, and server time. Use it after reconnect or app resume.

## Invite Links

`GET /api/v1/chat/conversations/{conversation_id}/invite-links/`

Authenticated group admin or owner.

`POST /api/v1/chat/conversations/{conversation_id}/invite-links/`

```json
{
  "expires_in_hours": 72,
  "max_uses": 25
}
```

Both fields are optional.

- `expires_in_hours`: 1 to 720
- `max_uses`: 0 to 10000. `0` means unlimited.

Invite item:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "conversation": "00000000-0000-0000-0000-000000000000",
  "created_by": {},
  "token": "<invite-token>",
  "expires_at": "2026-04-20T00:00:00Z",
  "revoked_at": null,
  "max_uses": 25,
  "use_count": 0,
  "is_active": true,
  "join_url": "https://api.example.com/api/v1/chat/invite-links/join/?token=<invite-token>",
  "created_at": "2026-04-17T00:00:00Z",
  "updated_at": "2026-04-17T00:00:00Z"
}
```

`POST /api/v1/chat/conversations/{conversation_id}/invite-links/{invite_id}/revoke/`

No body.

`POST /api/v1/chat/invite-links/join/`

```json
{
  "token": "<invite-token>"
}
```

Also accepts `?token=<invite-token>`.

## Calling And WebRTC

Call type values: `voice`, `video`.

Call status values: `initiated`, `ringing`, `ongoing`, `declined`, `missed`, `ended`, `failed`.

Participant state values: `invited`, `ringing`, `joined`, `left`, `declined`, `missed`, `failed`.

Network quality values: `unknown`, `excellent`, `good`, `fair`, `poor`, `offline`.

Video preference values: `auto`, `off`, `low`, `medium`, `high`.

Connection state values: `new`, `checking`, `connected`, `degraded`, `disconnected`, `failed`, `closed`.

Audio route values: `auto`, `speaker`, `earpiece`, `bluetooth`, `wired`.

`GET /api/v1/chat/calls/config/?quality=auto`

Authenticated. `quality` is optional and can override saved call quality preference.

`GET /api/v1/chat/calls/turn-credentials/`

Authenticated.

Response:

```json
{
  "configured": true,
  "ttl_seconds": 3600,
  "username": "turn-user",
  "credential": "turn-credential",
  "credential_type": "password",
  "ice_servers": [
    {
      "urls": ["stun:stun.cloudflare.com:3478"]
    }
  ]
}
```

`GET /api/v1/chat/calls/`

Authenticated. Returns paginated calls involving current user.

`GET /api/v1/chat/calls/recent/?status=ongoing`

Authenticated. `status` is optional.

`GET /api/v1/chat/calls/{call_id}/`

Authenticated.

`POST /api/v1/chat/conversations/{conversation_id}/calls/start/`

```json
{
  "call_type": "video",
  "metadata": {
    "client": "web"
  }
}
```

`metadata` is optional.

`POST /api/v1/chat/calls/{call_id}/accept/`

No body.

`POST /api/v1/chat/calls/{call_id}/decline/`

```json
{
  "reason": "busy"
}
```

`reason` is optional.

`POST /api/v1/chat/calls/{call_id}/end/`

```json
{
  "reason": "ended"
}
```

`reason` is optional.

`POST /api/v1/chat/calls/{call_id}/signal/`

```json
{
  "signal_type": "offer",
  "payload": {
    "sdp": "...",
    "to_user_id": "2",
    "signal_id": "client-signal-id-1"
  }
}
```

Allowed `signal_type` values: `offer`, `answer`, `ice_candidate`, `renegotiate`, `hangup`, `ice_restart`, `network_state`, `quality_update`, `media_toggle`, `speaker_hint`, `fallback_audio_only`, `receiver_report`, `request_keyframe`.

`payload.to_user_id` is optional. If present, the signal is targeted only to that call participant. If omitted, it is delivered to other call participants.

Quality update signal:

```json
{
  "signal_type": "quality_update",
  "payload": {
    "network_quality": "poor",
    "preferred_video_quality": "low",
    "audio_enabled": true,
    "video_enabled": false,
    "metrics": {
      "packet_loss": 0.18,
      "rtt_ms": 520
    }
  }
}
```

Quality update responses include:

```json
{
  "payload": {
    "network_quality": "poor",
    "preferred_video_quality": "low",
    "audio_enabled": true,
    "video_enabled": false,
    "metrics": {},
    "signal_id": "generated-or-client-id",
    "recommendation": {
      "mode": "audio_only",
      "reason": "poor_network"
    }
  }
}
```

`POST /api/v1/chat/calls/{call_id}/heartbeat/`

```json
{
  "network_quality": "good",
  "metrics": {
    "rtt_ms": 90
  }
}
```

Both fields are optional.

`POST /api/v1/chat/calls/{call_id}/media-state/`

```json
{
  "audio_enabled": true,
  "video_enabled": false,
  "is_on_hold": false,
  "reconnecting": false,
  "screen_share_enabled": false,
  "hand_raised": false,
  "connection_state": "connected",
  "audio_route": "speaker",
  "preferred_video_quality": "medium",
  "diagnostics": {
    "browser": "chrome"
  },
  "bitrate_kbps": 900,
  "packet_loss_ratio": "0.0100",
  "latency_ms": 80
}
```

Aliases accepted: `microphone_enabled` to `audio_enabled`, `camera_enabled` to `video_enabled`, `screen_sharing` to `screen_share_enabled`.

`POST /api/v1/chat/calls/{call_id}/quality-report/`

```json
{
  "packet_loss_pct": "1.25",
  "jitter_ms": 12,
  "round_trip_time_ms": 90,
  "bitrate_kbps": 1200,
  "frame_rate": 30,
  "network_quality": "good",
  "preferred_video_quality": "high",
  "audio_enabled": true,
  "video_enabled": true,
  "diagnostics": {
    "codec": "vp8"
  }
}
```

Aliases accepted: `microphone_enabled` to `audio_enabled`, `camera_enabled` to `video_enabled`.

`POST /api/v1/chat/calls/{call_id}/speaker-state/`

```json
{
  "speaking_level": 65,
  "is_speaking": true
}
```

`speaking_level` range: 0 to 100.

`GET /api/v1/chat/calls/{call_id}/orchestration/`

Returns current layout, network recommendation, participants, active speaker, raised hands, recovery plan, and queued signals.

`GET /api/v1/chat/calls/{call_id}/diagnostics/`

Returns participant counts, stale participant ids, network recommendation, recovery plan, quality summary, orchestration, and participant diagnostics.

## WebSocket API

Connect:

```text
ws://<host>/ws/chat/?token=<jwt-access-token>&device_id=<stable-device-id>
```

Use `wss://` in production.

Every client event uses this envelope:

```json
{
  "event": "event.name",
  "data": {}
}
```

Client events:

```json
{
  "event": "conversation.subscribe",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

```json
{
  "event": "conversation.unsubscribe",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

```json
{
  "event": "message.send",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000",
    "text": "Hello",
    "client_temp_id": "tmp-1",
    "attachment_ids": [],
    "reply_to_id": null,
    "entities": []
  }
}
```

```json
{
  "event": "message.edit",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000",
    "message_id": "00000000-0000-0000-0000-000000000000",
    "text": "Edited",
    "entities": []
  }
}
```

```json
{
  "event": "message.delete",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000",
    "message_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

```json
{
  "event": "message.delivered",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000",
    "message_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

```json
{
  "event": "message.read",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000",
    "message_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

```json
{
  "event": "message.react",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000",
    "message_id": "00000000-0000-0000-0000-000000000000",
    "emoji": "🔥"
  }
}
```

```json
{
  "event": "message.unreact",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000",
    "message_id": "00000000-0000-0000-0000-000000000000",
    "emoji": "🔥"
  }
}
```

```json
{
  "event": "typing.start",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

```json
{
  "event": "typing.stop",
  "data": {
    "conversation_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

```json
{
  "event": "presence.ping",
  "data": {
    "device_id": "web-main"
  }
}
```

Call controls:

```json
{
  "event": "call.accept",
  "data": {
    "call_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

```json
{
  "event": "call.decline",
  "data": {
    "call_id": "00000000-0000-0000-0000-000000000000",
    "reason": "busy"
  }
}
```

```json
{
  "event": "call.end",
  "data": {
    "call_id": "00000000-0000-0000-0000-000000000000",
    "reason": "ended"
  }
}
```

```json
{
  "event": "call.signal",
  "data": {
    "call_id": "00000000-0000-0000-0000-000000000000",
    "signal_type": "offer",
    "payload": {
      "sdp": "...",
      "to_user_id": "2"
    }
  }
}
```

Server events:

- `connection.ready`
- `conversation.subscribed`
- `conversation.unsubscribed`
- `message.created`
- `message.edited`
- `message.deleted`
- `message.delivered`
- `message.read`
- `message.reaction_updated`
- `typing.start`
- `typing.stop`
- `presence.updated`
- `call.started`
- `call.accepted`
- `call.declined`
- `call.ended`
- `call.signal`
- `error`

Message event data uses the same message object shape as REST. Call event data uses the same call or signal payload shape as REST.

Error event:

```json
{
  "event": "error",
  "data": {
    "message": "Error text"
  }
}
```

## Moderation And Audit

Staff only.

`GET /api/v1/chat/moderation/reports/`

Returns paginated message reports.

`POST /api/v1/chat/moderation/reports/{report_id}/resolve/`

```json
{
  "notes": "Handled",
  "hide_message": true
}
```

`hide_message` defaults to `false`.

`POST /api/v1/chat/moderation/reports/{report_id}/dismiss/`

```json
{
  "notes": "No violation"
}
```

`POST /api/v1/chat/moderation/messages/{message_id}/restore/`

```json
{
  "notes": "Restored after appeal"
}
```

`GET /api/v1/chat/audit-logs/?conversation_id={conversation_id}&event_type=message_sent`

Optional filters: `conversation_id`, `event_type`.

## Health And Integration

`GET /api/v1/health/live/`

Public liveness probe.

`GET /api/v1/health/ready/`

Public readiness probe. Checks database and cache.

`GET /api/v1/health/deep/`

Staff only. Checks deeper integrations.

`GET /api/v1/chat/integrations/health/`

Staff only. Returns antivirus and push integration snapshots.

## Frontend Implementation Order

1. Implement auth: register, login, refresh, logout by session revoke.
2. Store `access`, `refresh`, `session_id`, and a stable `device_id`.
3. Add an API client that refreshes access tokens on `401`.
4. Load `/auth/me/`, `/chat/sync/`, and `/chat/conversations/` after app start.
5. Connect websocket with the access token and subscribe to visible conversations.
6. Upload files first, then send messages with `attachment_ids`.
7. Use `client_temp_id` to reconcile optimistic messages with `message.created`.
8. Use `/chat/sync/` after reconnect to fill missed websocket events.
9. For calls, load `/chat/calls/config/` and `/chat/calls/turn-credentials/`, then start or accept a call and exchange `call.signal` events over websocket or REST.

## Verification Commands

Backend contract and behavior:

```bash
docker compose exec -T web python manage.py check
docker compose exec -T web python manage.py test apps.chat.tests --keepdb
```

Frontend production build:

```bash
cd frontend
npm run build
```

Regenerate the saved OpenAPI schema:

```bash
python manage.py spectacular --file schema.yml --validate
```



User discovery item:

```json
{
  "id": 2,
  "username": "other",
  "first_name": "Other",
  "last_name": "User",
  "display_name": "Other User",
  "avatar": null,
  "bio": "",
  "status_message": "",
  "is_online": false,
  "friendship_status": "none",
  "proximity_km": null
}
```

`GET /api/v1/auth/users/nearby/?latitude=24.7136&longitude=46.6753&radius_km=25&limit=20`

Authenticated.

Query:

- `latitude`: required decimal, -90 to 90
- `longitude`: required decimal, -180 to 180
- `radius_km`: optional number, default `25`
- `limit`: optional integer, default `20`

Response is a list of user discovery items.

`GET /api/v1/auth/friends/requests/?scope=all`

Authenticated. `scope` can be `all`, `incoming`, `outgoing`, or `pending`.

Friend request item:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "sender": {},
  "receiver": {},
  "status": "pending",
  "message": "Hi",
  "responded_at": null,
  "created_at": "2026-04-17T00:00:00Z",
  "updated_at": "2026-04-17T00:00:00Z"
}
```

`POST /api/v1/auth/friends/requests/`

Authenticated.

```json
{
  "user_id": 2,
  "message": "Hi"
}
```

`POST /api/v1/auth/friends/requests/{request_id}/respond/`

Authenticated.

```json
{
  "action": "accept"
}
```

Allowed actions: `accept`, `reject`, `cancel`.

## Conversation Objects

Conversation list item:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "type": "direct",
  "title": "",
  "avatar": null,
  "last_message": null,
  "last_message_at": null,
  "participants": [],
  "active_participant_count": 2,
  "unread_count": 0,
  "created_at": "2026-04-17T00:00:00Z"
}
```

Conversation detail adds:

```json
{
  "created_by": 1,
  "is_active": true,
  "updated_at": "2026-04-17T00:00:00Z"
}
```

Participant item:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "user": {
    "id": "2",
    "username": "other",
    "display_name": "Other",
    "avatar": null,
    "is_online": false,
    "active_devices": []
  },
  "role": "member",
  "joined_at": "2026-04-17T00:00:00Z",
  "left_at": null,
  "is_muted": false,
  "is_archived": false,
  "is_pinned": false,
  "is_blocked": false,
  "last_read_message": null,
  "last_read_at": null,
  "last_delivered_message": null,
  "last_delivered_at": null,
  "moderation_muted_until": null,
  "banned_at": null,
  "ban_reason": ""
}
```

Conversation types: `direct`, `group`.

Participant roles: `member`, `admin`, `owner`. The role update endpoint accepts only `member` or `admin`.

## Conversations

`GET /api/v1/chat/conversations/`

Authenticated. Returns paginated conversation list items.

`POST /api/v1/chat/conversations/`

Authenticated.

Direct conversation request:

```json
{
  "type": "direct",
  "participant_ids": [2]
}
```

Group conversation request:

```json
{
  "type": "group",
  "title": "Builders",
  "participant_ids": [2, 3]
}
```

Direct conversations return `200` if an existing direct thread is reused. Groups return `201`.

`GET /api/v1/chat/conversations/{conversation_id}/`

Authenticated. Returns conversation detail.

`GET /api/v1/chat/conversations/search/?q=builders`

Authenticated. Empty `q` returns no results.

`POST /api/v1/chat/conversations/{conversation_id}/archive/`

Authenticated. No body required. Toggles archived state for current user.

`POST /api/v1/chat/conversations/{conversation_id}/mute/`

Authenticated. No body required. Toggles muted state for current user.

`POST /api/v1/chat/conversations/{conversation_id}/pin/`

Authenticated. No body required. Toggles pinned state for current user.

`POST /api/v1/chat/conversations/{conversation_id}/leave/`

Authenticated. No body required.

`POST /api/v1/chat/conversations/{conversation_id}/mark-delivered/`

Authenticated.

```json
{
  "message_id": "00000000-0000-0000-0000-000000000000"
}
```

`message_id` is optional. Omit it to mark latest visible message delivered.

`POST /api/v1/chat/conversations/{conversation_id}/mark-read/`

Authenticated.

```json
{
  "message_id": "00000000-0000-0000-0000-000000000000"
}
```

`message_id` is optional. Omit it to mark latest visible message read.

## Group Management

Admin or owner permissions are required unless noted by backend permissions.

`POST /api/v1/chat/conversations/{conversation_id}/participants/`

```json
{
  "participant_ids": [4, 5]
}
```

`DELETE /api/v1/chat/conversations/{conversation_id}/participants/{user_id}/`

No body.

`PATCH /api/v1/chat/conversations/{conversation_id}/participants/{user_id}/role/`

```json
{
  "role": "admin"
}
```

Allowed values: `member`, `admin`.

`POST /api/v1/chat/conversations/{conversation_id}/participants/{user_id}/mute/`

```json
{
  "minutes": 60
}
```

`minutes` range: 1 to 43200.

`POST /api/v1/chat/conversations/{conversation_id}/participants/{user_id}/ban/`

```json
{
  "reason": "spam"
}
```

`reason` is optional.

`DELETE /api/v1/chat/conversations/{conversation_id}/participants/{user_id}/ban/`

No body.

`POST /api/v1/chat/conversations/{conversation_id}/transfer-ownership/`

```json
{
  "target_user_id": 2
}
```

## Messages

Message type values: `text`, `image`, `video`, `audio`, `file`, `system`.

Message response:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "conversation": "00000000-0000-0000-0000-000000000000",
  "sender": {},
  "type": "text",
  "text": "Hello",
  "metadata": {},
  "reply_to": null,
  "forwarded_from": null,
  "is_edited": false,
  "edited_at": null,
  "is_deleted": false,
  "deleted_at": null,
  "client_temp_id": "tmp-1",
  "delivery_status": "sent",
  "failed_reason": "",
  "retry_count": 0,
  "attachments": [],
  "reactions": [],
  "deliveries": [],
  "edit_history": [],
  "reaction_summary": {},
  "voice_note": {
    "is_voice_note": false,
    "duration_seconds": null,
    "waveform": [],
    "transcript_available": false
  },
  "transcript": null,
  "entities": [],
  "links": [],
  "mentioned_user_ids": [],
  "reply_preview": null,
  "created_at": "2026-04-17T00:00:00Z",
  "updated_at": "2026-04-17T00:00:00Z"
}
```

`GET /api/v1/chat/conversations/{conversation_id}/messages/`

Authenticated. Marks the conversation delivered for the current user and returns paginated messages.

`POST /api/v1/chat/conversations/{conversation_id}/messages/`

Authenticated.

Text request:

```json
{
  "type": "text",
  "text": "Hello @other visit https://example.com",
  "client_temp_id": "tmp-1700000000000",
  "reply_to_id": null,
  "entities": [
    {
      "type": "mention",
      "offset": 6,
      "length": 6,
      "user_id": "2",
      "username": "other"
    },
    {
      "type": "link",
      "offset": 19,
      "length": 19,
      "url": "https://example.com"
    }
  ]
}
```

Attachment message request:

```json
{
  "type": "image",
  "text": "Photo",
  "client_temp_id": "tmp-photo-1",
  "attachment_ids": ["00000000-0000-0000-0000-000000000000"]
}
```

Voice note request:

```json
{
  "is_voice_note": true,
  "attachment_ids": ["00000000-0000-0000-0000-000000000000"],
  "duration_seconds": "3.50",
  "waveform": [5, 10, 20],
  "transcript_text": "hello there",
  "transcript_language_code": "en",
  "transcript_confidence": "0.92"
}
```

Entity types: `bold`, `italic`, `underline`, `strike`, `code`, `link`, `mention`.

`GET /api/v1/chat/messages/{message_id}/`

Returns one message.

`PATCH /api/v1/chat/messages/{message_id}/manage/`

```json
{
  "text": "Edited message",
  "entities": []
}
```

`DELETE /api/v1/chat/messages/{message_id}/manage/`

No body. Soft deletes the message.

`POST /api/v1/chat/messages/{message_id}/reactions/`

```json
{
  "emoji": "🔥"
}
```

`DELETE /api/v1/chat/messages/{message_id}/reactions/`

```json
{
  "emoji": "🔥"
}
```

`POST /api/v1/chat/messages/{message_id}/report/`

```json
{
  "reason": "spam",
  "details": "Repeated links"
}
```

Allowed reasons: `spam`, `harassment`, `hate`, `violence`, `impersonation`, `other`.

`POST /api/v1/chat/messages/{message_id}/forward/`

```json
{
  "conversation_id": "00000000-0000-0000-0000-000000000000",
  "client_temp_id": "tmp-forward-1"
}
```

`POST /api/v1/chat/messages/{message_id}/fail/`

```json
{
  "reason": "local send failed"
}
```

`POST /api/v1/chat/messages/{message_id}/retry/`

No body.

`POST /api/v1/chat/messages/{message_id}/transcript/`

```json
{
  "text": "hello there",
  "language_code": "en",
  "confidence": "0.95",
  "status": "completed",
  "source": "auto"
}
```

Transcript status: `pending`, `completed`, `failed`.

Transcript source: `manual`, `auto`.

`GET /api/v1/chat/messages/search/?q=hello`

Authenticated. Empty `q` returns no results.
