# Messenger API Contract

Frontend-facing contract for the Django Messenger backend.

Live docs:

- Swagger UI: `/api/docs/`
- Redoc: `/api/redoc/`
- OpenAPI schema: `/api/schema/`
- Saved schema: `schema.yml`

Verification for this contract:

```bash
docker compose exec -T web python manage.py check
docker compose exec -T web python manage.py test apps.chat.tests --keepdb
cd frontend
npm run build
```

## Basics

Base path: `/api/v1`

Authenticated JSON requests:

```http
Authorization: Bearer <access_token>
Content-Type: application/json
X-Device-Id: <stable-device-id>
```

Use one stable `X-Device-Id` per browser profile, app install, or tab cluster.

List endpoints use DRF pagination:

```json
{
  "count": 0,
  "next": null,
  "previous": null,
  "results": []
}
```

Error envelope:

```json
{
  "success": false,
  "message": "Request failed.",
  "errors": {"field": ["Error text."]},
  "status_code": 400,
  "request_id": "req-123",
  "view": "ViewName"
}
```

ID rules:

- User ids are integers in REST write payloads.
- Conversation, message, upload, attachment, call, invite, report, audit, and session ids are UUID strings.
- Datetimes are ISO 8601 strings.

## Auth

### `POST /api/v1/auth/register/`

Public.

```json
{
  "username": "xim",
  "email": "xim@example.com",
  "password": "pass12345",
  "first_name": "Xim",
  "last_name": "Example"
}
```

Returns created user.

### `POST /api/v1/auth/login/`

Public. `username` accepts username or email.

```json
{
  "username": "xim@example.com",
  "password": "pass12345"
}
```

Returns:

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

### `POST /api/v1/auth/refresh/`

```json
{
  "refresh": "<jwt-refresh>"
}
```

Returns:

```json
{
  "access": "<jwt-access>"
}
```

### Account endpoints

`GET /api/v1/auth/me/`: current user.

`PATCH /api/v1/auth/me/`:

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

`POST /api/v1/auth/social/login/`:

```json
{
  "provider": "google",
  "id_token": "<provider-id-token>"
}
```

Allowed `provider`: `google`, `apple`.

`POST /api/v1/auth/password/change/`:

```json
{
  "current_password": "pass12345",
  "new_password": "ChangedPass12345!"
}
```

`POST /api/v1/auth/password/reset/request/`:

```json
{
  "email": "xim@example.com"
}
```

`POST /api/v1/auth/password/reset/confirm/`:

```json
{
  "token": "<reset-token>",
  "new_password": "NewPass12345!"
}
```

`POST /api/v1/auth/email/verify/request/`: no body.

`POST /api/v1/auth/email/verify/confirm/`:

```json
{
  "token": "<email-verify-token>"
}
```

`GET /api/v1/auth/sessions/`: active sessions.

`POST /api/v1/auth/sessions/{session_id}/revoke/`: no body, returns `204`.

`POST /api/v1/auth/sessions/revoke-all/`: no body.

`GET /api/v1/auth/me/export/`: account export.

`POST /api/v1/auth/me/delete/`:

```json
{
  "password": "pass12345"
}
```

Returns `204`.

## Users And Friends

`GET /api/v1/auth/users/search/?q=xim`

`GET /api/v1/auth/users/nearby/?latitude=24.7136&longitude=46.6753&radius_km=25&limit=20`

Nearby query:

- `latitude`: required, -90 to 90
- `longitude`: required, -180 to 180
- `radius_km`: optional, default `25`
- `limit`: optional, default `20`

Discovery item:

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

`GET /api/v1/auth/friends/requests/?scope=all`

Allowed `scope`: `all`, `incoming`, `outgoing`, `pending`.

`POST /api/v1/auth/friends/requests/`:

```json
{
  "user_id": 2,
  "message": "Hi"
}
```

`POST /api/v1/auth/friends/requests/{request_id}/respond/`:

```json
{
  "action": "accept"
}
```

Allowed `action`: `accept`, `reject`, `cancel`.

## Conversations

Conversation type values: `direct`, `group`.

Participant roles: `member`, `admin`, `owner`. Role update accepts `member` or `admin`.

Conversation item:

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

`GET /api/v1/chat/conversations/`: paginated conversations.

`POST /api/v1/chat/conversations/` direct:

```json
{
  "type": "direct",
  "participant_ids": [2]
}
```

`POST /api/v1/chat/conversations/` group:

```json
{
  "type": "group",
  "title": "Builders",
  "participant_ids": [2, 3]
}
```

`GET /api/v1/chat/conversations/{conversation_id}/`: conversation detail.

`GET /api/v1/chat/conversations/search/?q=builders`

State toggles, no body:

- `POST /api/v1/chat/conversations/{conversation_id}/archive/`
- `POST /api/v1/chat/conversations/{conversation_id}/mute/`
- `POST /api/v1/chat/conversations/{conversation_id}/pin/`
- `POST /api/v1/chat/conversations/{conversation_id}/leave/`

Delivery and read:

`POST /api/v1/chat/conversations/{conversation_id}/mark-delivered/`

```json
{
  "message_id": "00000000-0000-0000-0000-000000000000"
}
```

`message_id` is optional.

`POST /api/v1/chat/conversations/{conversation_id}/mark-read/`

```json
{
  "message_id": "00000000-0000-0000-0000-000000000000"
}
```

`message_id` is optional.

Group management:

`POST /api/v1/chat/conversations/{conversation_id}/participants/`

```json
{
  "participant_ids": [4, 5]
}
```

`DELETE /api/v1/chat/conversations/{conversation_id}/participants/{user_id}/`: no body.

`PATCH /api/v1/chat/conversations/{conversation_id}/participants/{user_id}/role/`

```json
{
  "role": "admin"
}
```

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

`DELETE /api/v1/chat/conversations/{conversation_id}/participants/{user_id}/ban/`: no body.

`POST /api/v1/chat/conversations/{conversation_id}/transfer-ownership/`

```json
{
  "target_user_id": 2
}
```

## Messages

Message type values: `text`, `image`, `video`, `audio`, `file`, `system`.

Entity type values: `bold`, `italic`, `underline`, `strike`, `code`, `link`, `mention`.

Message object:

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
  "voice_note": {"is_voice_note": false, "duration_seconds": null, "waveform": [], "transcript_available": false},
  "transcript": null,
  "entities": [],
  "links": [],
  "mentioned_user_ids": [],
  "reply_preview": null,
  "created_at": "2026-04-17T00:00:00Z",
  "updated_at": "2026-04-17T00:00:00Z"
}
```

`GET /api/v1/chat/conversations/{conversation_id}/messages/`: paginated messages.

`POST /api/v1/chat/conversations/{conversation_id}/messages/` text:

```json
{
  "type": "text",
  "text": "Hello @other visit https://example.com",
  "client_temp_id": "tmp-1",
  "reply_to_id": null,
  "entities": [
    {"type": "mention", "offset": 6, "length": 6, "user_id": "2", "username": "other"},
    {"type": "link", "offset": 19, "length": 19, "url": "https://example.com"}
  ]
}
```

With attachments:

```json
{
  "type": "image",
  "text": "Photo",
  "client_temp_id": "tmp-photo-1",
  "attachment_ids": ["00000000-0000-0000-0000-000000000000"]
}
```

Voice note:

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

Message actions:

- `GET /api/v1/chat/messages/{message_id}/`
- `DELETE /api/v1/chat/messages/{message_id}/manage/`: no body
- `POST /api/v1/chat/messages/{message_id}/retry/`: no body
- `GET /api/v1/chat/messages/search/?q=hello`

`PATCH /api/v1/chat/messages/{message_id}/manage/`

```json
{
  "text": "Edited message",
  "entities": []
}
```

`POST /api/v1/chat/messages/{message_id}/reactions/`

```json
{
  "emoji": "like"
}
```

`DELETE /api/v1/chat/messages/{message_id}/reactions/`

```json
{
  "emoji": "like"
}
```

`POST /api/v1/chat/messages/{message_id}/report/`

```json
{
  "reason": "spam",
  "details": "Repeated links"
}
```

Allowed `reason`: `spam`, `harassment`, `hate`, `violence`, `impersonation`, `other`.

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

## Uploads And Media

`POST /api/v1/chat/uploads/`

Authenticated multipart form data:

```text
file=<binary file>       required
original_name=photo.jpg  optional
mime_type=image/jpeg     optional
```

Upload response:

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
  "signed_download": {},
  "signed_preview": {},
  "created_at": "2026-04-17T00:00:00Z"
}
```

Use clean pending upload ids in `attachment_ids` when sending a message.

Download and preview:

- `GET /api/v1/chat/uploads/{upload_id}/download/`
- `GET /api/v1/chat/uploads/{upload_id}/preview/`
- `GET /api/v1/chat/attachments/{attachment_id}/download/`
- `GET /api/v1/chat/attachments/{attachment_id}/preview/`

Media tokens, no body:

- `POST /api/v1/chat/uploads/{upload_id}/media-token/`
- `POST /api/v1/chat/attachments/{attachment_id}/media-token/`

Response:

```json
{
  "token": "<token>",
  "download_url": "https://api.example.com/api/v1/chat/attachments/{attachment_id}/download/?token=<token>",
  "preview_url": "https://api.example.com/api/v1/chat/attachments/{attachment_id}/preview/?token=<token>",
  "expires_at": "2026-04-17T00:05:00Z"
}
```

`GET /api/v1/chat/conversations/{conversation_id}/media/?kind=all`

Allowed `kind`: `all`, `image`, `video`, `audio`, `file`.

## Presence, Blocks, Devices, Notifications, Sync

`POST /api/v1/chat/presence/ping/`

```json
{
  "device_id": "web-main"
}
```

`POST /api/v1/chat/presence/disconnect/`

```json
{
  "device_id": "web-main"
}
```

`GET /api/v1/chat/blocks/`

`POST /api/v1/chat/blocks/`

```json
{
  "blocked_user_id": 2,
  "reason": "spam"
}
```

`DELETE /api/v1/chat/blocks/{user_id}/`

`GET /api/v1/chat/devices/`

`POST /api/v1/chat/devices/`

```json
{
  "platform": "web",
  "push_token": "<fcm-token>"
}
```

Allowed platform: `android`, `web`, `ios`.

`POST /api/v1/chat/devices/deactivate/`

```json
{
  "push_token": "<fcm-token>"
}
```

`GET /api/v1/chat/notifications/preferences/`

`PATCH /api/v1/chat/notifications/preferences/`

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

All query params are optional. `limit` range: 1 to 200.

## Invite Links

`GET /api/v1/chat/conversations/{conversation_id}/invite-links/`

`POST /api/v1/chat/conversations/{conversation_id}/invite-links/`

```json
{
  "expires_in_hours": 72,
  "max_uses": 25
}
```

Both fields are optional. `expires_in_hours` range: 1 to 720. `max_uses` range: 0 to 10000.

`POST /api/v1/chat/conversations/{conversation_id}/invite-links/{invite_id}/revoke/`: no body.

`POST /api/v1/chat/invite-links/join/`

```json
{
  "token": "<invite-token>"
}
```

Also accepts `?token=<invite-token>`.

## Calling And WebRTC

Call type: `voice`, `video`.

Call status: `initiated`, `ringing`, `ongoing`, `declined`, `missed`, `ended`, `failed`.

Participant state: `invited`, `ringing`, `joined`, `left`, `declined`, `missed`, `failed`.

Network quality: `unknown`, `excellent`, `good`, `fair`, `poor`, `offline`.

Video preference: `auto`, `off`, `low`, `medium`, `high`.

Connection state: `new`, `checking`, `connected`, `degraded`, `disconnected`, `failed`, `closed`.

Audio route: `auto`, `speaker`, `earpiece`, `bluetooth`, `wired`.

`GET /api/v1/chat/calls/config/?quality=auto`

`GET /api/v1/chat/calls/turn-credentials/`

`GET /api/v1/chat/calls/`

`GET /api/v1/chat/calls/recent/?status=ongoing`

`GET /api/v1/chat/calls/{call_id}/`

`POST /api/v1/chat/conversations/{conversation_id}/calls/start/`

```json
{
  "call_type": "video",
  "metadata": {"client": "web"}
}
```

`POST /api/v1/chat/calls/{call_id}/accept/`: no body.

`POST /api/v1/chat/calls/{call_id}/decline/`

```json
{
  "reason": "busy"
}
```

`POST /api/v1/chat/calls/{call_id}/end/`

```json
{
  "reason": "ended"
}
```

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

Allowed `signal_type`: `offer`, `answer`, `ice_candidate`, `renegotiate`, `hangup`, `ice_restart`, `network_state`, `quality_update`, `media_toggle`, `speaker_hint`, `fallback_audio_only`, `receiver_report`, `request_keyframe`.

`payload.to_user_id` is optional and targets one call participant.

Quality update:

```json
{
  "signal_type": "quality_update",
  "payload": {
    "network_quality": "poor",
    "preferred_video_quality": "low",
    "audio_enabled": true,
    "video_enabled": false,
    "metrics": {"packet_loss": 0.18, "rtt_ms": 520}
  }
}
```

`quality_update` responses include `payload.recommendation`, for example:

```json
{
  "mode": "audio_only",
  "reason": "poor_network"
}
```

`POST /api/v1/chat/calls/{call_id}/heartbeat/`

```json
{
  "network_quality": "good",
  "metrics": {"rtt_ms": 90}
}
```

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
  "diagnostics": {"browser": "chrome"},
  "bitrate_kbps": 900,
  "packet_loss_ratio": "0.0100",
  "latency_ms": 80
}
```

Aliases: `microphone_enabled` to `audio_enabled`, `camera_enabled` to `video_enabled`, `screen_sharing` to `screen_share_enabled`.

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
  "diagnostics": {"codec": "vp8"}
}
```

Aliases: `microphone_enabled` to `audio_enabled`, `camera_enabled` to `video_enabled`.

`POST /api/v1/chat/calls/{call_id}/speaker-state/`

```json
{
  "speaking_level": 65,
  "is_speaking": true
}
```

`speaking_level` range: 0 to 100.

`GET /api/v1/chat/calls/{call_id}/orchestration/`

`GET /api/v1/chat/calls/{call_id}/diagnostics/`

## WebSocket

Connect:

```text
wss://<host>/ws/chat/?token=<jwt-access-token>&device_id=<stable-device-id>
```

Client envelope:

```json
{
  "event": "event.name",
  "data": {}
}
```

Client events and payloads:

```json
{"event": "conversation.subscribe", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000"}}
```

```json
{"event": "conversation.unsubscribe", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000"}}
```

```json
{"event": "message.send", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000", "text": "Hello", "client_temp_id": "tmp-1", "attachment_ids": [], "reply_to_id": null, "entities": []}}
```

```json
{"event": "message.edit", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000", "message_id": "00000000-0000-0000-0000-000000000000", "text": "Edited", "entities": []}}
```

```json
{"event": "message.delete", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000", "message_id": "00000000-0000-0000-0000-000000000000"}}
```

```json
{"event": "message.delivered", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000", "message_id": "00000000-0000-0000-0000-000000000000"}}
```

```json
{"event": "message.read", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000", "message_id": "00000000-0000-0000-0000-000000000000"}}
```

```json
{"event": "message.react", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000", "message_id": "00000000-0000-0000-0000-000000000000", "emoji": "like"}}
```

```json
{"event": "message.unreact", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000", "message_id": "00000000-0000-0000-0000-000000000000", "emoji": "like"}}
```

```json
{"event": "typing.start", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000"}}
```

```json
{"event": "typing.stop", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000"}}
```

```json
{"event": "presence.ping", "data": {"device_id": "web-main"}}
```

```json
{"event": "call.accept", "data": {"call_id": "00000000-0000-0000-0000-000000000000"}}
```

```json
{"event": "call.decline", "data": {"call_id": "00000000-0000-0000-0000-000000000000", "reason": "busy"}}
```

```json
{"event": "call.end", "data": {"call_id": "00000000-0000-0000-0000-000000000000", "reason": "ended"}}
```

```json
{"event": "call.signal", "data": {"call_id": "00000000-0000-0000-0000-000000000000", "signal_type": "offer", "payload": {"sdp": "...", "to_user_id": "2"}}}
```

Server events: `connection.ready`, `conversation.subscribed`, `conversation.unsubscribed`, `message.created`, `message.edited`, `message.deleted`, `message.delivered`, `message.read`, `message.reaction_updated`, `typing.start`, `typing.stop`, `presence.updated`, `call.started`, `call.accepted`, `call.declined`, `call.ended`, `call.signal`, `error`.

## Moderation, Audit, Health

Staff only:

- `GET /api/v1/chat/moderation/reports/`
- `GET /api/v1/chat/audit-logs/?conversation_id={conversation_id}&event_type=message_sent`
- `GET /api/v1/chat/integrations/health/`
- `GET /api/v1/health/deep/`

`POST /api/v1/chat/moderation/reports/{report_id}/resolve/`

```json
{
  "notes": "Handled",
  "hide_message": true
}
```

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

Public health:

- `GET /api/v1/health/live/`
- `GET /api/v1/health/ready/`

## Frontend Build Order

1. Implement auth: register, login, refresh, session revoke.
2. Store `access`, `refresh`, `session_id`, and a stable `device_id`.
3. Add automatic access refresh on `401`.
4. On app boot, call `/auth/me/`, `/chat/sync/`, and `/chat/conversations/`.
5. Connect websocket with the access token and subscribe to visible conversations.
6. Upload files first, then send messages with `attachment_ids`.
7. Use `client_temp_id` to reconcile optimistic messages with `message.created`.
8. After websocket reconnect, call `/chat/sync/` to fill missed events.
9. For calls, load `/chat/calls/config/` and `/chat/calls/turn-credentials/`, then exchange `call.signal` events over websocket or REST.

