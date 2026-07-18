# Support Chat visitor and widget foundation

This upgrade adds the public visitor identity and session boundary for Support Chat. It does not change personal Messenger conversations, accounts, E2EE, calls, attachments, presence, or unread state.

## Public rollout flags

Enable the public widget only after this conversation migration is deployed and website origins are configured:

```env
SUPPORT_CHAT_ENABLED=True
SUPPORT_WIDGET_ENABLED=True
SUPPORT_WIDGET_REQUIRE_ORIGIN=True
SUPPORT_WIDGET_SESSION_TTL_HOURS=720
SUPPORT_WIDGET_SCRIPT_URL=https://your-domain.example/support-widget/v1/widget.js
```

`SUPPORT_WIDGET_ENABLED` controls only the external website widget APIs. The owner can configure websites while this flag remains disabled.

## Website origin rules

Each Support website has an explicit `allowed_origins` list. Use full origins without paths:

```text
https://example.com
https://www.example.com
https://shop.example.com
```

When the list is empty, the backend defaults to the website's HTTPS domain and its `www` form. Production should not permit HTTP origins.

Dynamic CORS approval is applied only to versioned Support widget endpoints and only when the request origin belongs to the target website. Messenger APIs do not inherit these public CORS rules.

## Installation

The owner-facing Websites page generates a versioned script tag:

```html
<script async src="https://your-domain.example/support-widget/v1/widget.js" data-support-site-key="WEBSITE_SITE_KEY"></script>
```

The loader establishes the public configuration, resumable visitor session, and isolated Support conversation APIs. Text messaging is available; realtime delivery and media remain separate later upgrades, with polling retained as the fallback.

## Session security

- Website visitors never become Messenger users.
- The site key identifies the website but is not used as a visitor session secret.
- Every visitor session receives a high-entropy bearer token stored only as a SHA-256 hash on the server.
- Sessions are bound to the originating website origin.
- Refresh rotates the token and invalidates the old token immediately.
- Regenerating a website site key revokes all active visitor sessions for that website.
- Disabling a website or widget prevents new and existing public widget access.
- Public responses use no-store cache headers.

## Deployment

```bash
docker compose build
docker compose up -d
docker compose exec web python manage.py migrate
```

After migration, configure each website's allowed origins, widget identity fields, and installation code in Support Chat. Leave `SUPPORT_WIDGET_ENABLED=True` until the Support conversation and message endpoints are installed.
