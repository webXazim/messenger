# Upgrade 11 — Support integrations and data governance

This release adds owner-only Support webhooks, private Support data exports,
retention controls, and visitor-data deletion. Personal Messenger records,
attachments, conversations, notifications, and E2EE data remain outside every
Support governance query.

## Deploy

1. Back up PostgreSQL, private media/R2 metadata, and the deployed source.
2. Build and restart the existing Messenger deployment.
3. Run `python manage.py migrate` inside the web container.
4. Confirm the Celery worker and Celery Beat are healthy.
5. Open **Support Chat → Settings** and review retention before enabling it.
6. Create a test webhook and verify its HMAC signature before subscribing to live events.
7. Generate and download a test Support-only export, then confirm its expiry policy.

Migration: `support.0010_integrations_data_governance`

## New configuration

```env
SUPPORT_WEBHOOK_TIMEOUT_SECONDS=10
SUPPORT_WEBHOOK_MAX_ATTEMPTS=6
SUPPORT_EXPORT_MAX_ATTACHMENT_BYTES=262144000
```

Production webhook endpoints must use HTTPS. Destinations resolving to local,
private, link-local, multicast, reserved, or unspecified addresses are rejected.
Webhook consumers should deduplicate deliveries using `X-Support-Delivery`.
