# Support integrations and data governance

Upgrade 11 adds owner-only operational privacy controls to Support Chat without
changing personal Messenger. Every query begins from a `SupportAccount`, website,
Support conversation, or signed widget visitor; no export, retention, webhook, or
deletion service selects personal Messenger conversations.

## Access model

Only the Support owner can:

- change retention and visitor-deletion settings
- create, disable, test, rotate, or remove webhook endpoints
- view webhook delivery history
- request and download Support exports
- request manual visitor-data deletion
- review deletion history

Agents cannot access these endpoints. A website visitor can request deletion only
for the visitor identity authenticated by that website's signed, origin-bound
widget session, and only when the owner has enabled visitor deletion requests.

## Privacy settings

Authenticated owner endpoint:

```text
GET   /api/v1/support/privacy/settings/
PATCH /api/v1/support/privacy/settings/
```

Settings cover:

- resolved/closed Support conversation retention
- inactive widget-session retention
- generated-export retention
- visitor self-deletion availability
- whether exports include private attachment files

Retention is disabled by default. Enabling it is an explicit owner action.

## Private Support exports

Owner endpoints:

```text
GET  /api/v1/support/exports/
POST /api/v1/support/exports/
GET  /api/v1/support/exports/<export UUID>/download/
```

Exports are generated asynchronously and stored privately for the configured
retention period. Downloads require the current Support owner and use private,
no-store responses. The ZIP includes a manifest declaring `scope: support_only`
and `includes_personal_messenger: false`.

CSV files cover Support websites, agents, visitors, conversations, messages,
attachments, CSAT, knowledge articles, and audit events. Visitor-controlled CSV
cells are neutralized before export so spreadsheet applications do not interpret
text beginning with formula characters as executable formulas.

Attachment inclusion is optional and capped by
`SUPPORT_EXPORT_MAX_ATTACHMENT_BYTES`. The metadata CSV remains complete even when
the attachment file cap is reached.

## Visitor-data deletion

Owner endpoint:

```text
POST /api/v1/support/privacy/visitors/<visitor UUID>/delete/
```

Signed visitor endpoint:

```text
POST /api/v1/support/widget/<site key>/sessions/<session UUID>/privacy/delete/
```

A deletion request removes the visitor's Support conversations, Support messages,
Support attachments and thumbnails, pending Support uploads, widget sessions, and
visitor identity. It never selects personal Messenger participants, messages,
media, friends, devices, calls, or E2EE records. A durable request record retains
the website, pseudonymous external visitor ID, request source, status, timestamps,
and failure details for operational auditing.

## Outbound webhooks

Owner endpoints:

```text
GET    /api/v1/support/webhooks/
POST   /api/v1/support/webhooks/
PATCH  /api/v1/support/webhooks/<endpoint UUID>/
DELETE /api/v1/support/webhooks/<endpoint UUID>/
POST   /api/v1/support/webhooks/<endpoint UUID>/rotate-secret/
POST   /api/v1/support/webhooks/<endpoint UUID>/test/
GET    /api/v1/support/webhooks/deliveries/
```

Supported events:

```text
webhook.test
conversation.created
conversation.updated
message.created
csat.submitted
visitor.deletion_completed
export.ready
```

The signing secret is shown only at creation or rotation. Each request includes:

```text
X-Support-Event
X-Support-Delivery
X-Support-Timestamp
X-Support-Signature
```

Signature input is `<timestamp>.<raw request body>` using HMAC-SHA256. Consumers
should verify the signature against the raw bytes, reject stale timestamps, and
deduplicate by `X-Support-Delivery` because delivery is at least once.

Production endpoints must use HTTPS. URLs containing credentials are rejected.
The destination hostname is resolved and blocked when it maps to local, private,
loopback, link-local, multicast, reserved, or unspecified addresses. Redirects are
disabled, and the destination is revalidated for every retry.

Failed deliveries are durable and retried by Celery with increasing delays until
`SUPPORT_WEBHOOK_MAX_ATTEMPTS` is reached.

## Scheduled operations

Celery Beat runs:

- pending webhook retry once per minute
- Support retention and expired-export cleanup daily

Both the Celery worker and Celery Beat must share the production Redis broker and
the same deployed source version.

## Responsive interface

Governance controls remain inside **Support Chat → Settings** rather than adding a
new top-level navigation destination. They reuse Messenger's application shell,
breakpoints, spacing, focus states, touch targets, and safe-area handling.

- Desktop uses compact settings grids and operational rows.
- Tablet collapses wide forms and actions without horizontal overflow.
- Mobile uses one-column controls and full-width destructive confirmations.
- Visitor deletion stays in the conversation details surface and is owner-only.

## Deployment

Apply the committed migration:

```bash
docker compose exec web python manage.py migrate
```

Migration:

```text
support.0010_integrations_data_governance
```

Recommended configuration:

```env
SUPPORT_WEBHOOK_TIMEOUT_SECONDS=10
SUPPORT_WEBHOOK_MAX_ATTEMPTS=6
SUPPORT_EXPORT_MAX_ATTACHMENT_BYTES=262144000
```
