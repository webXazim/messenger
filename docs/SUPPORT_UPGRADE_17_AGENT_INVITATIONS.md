# Support Upgrade 17 — Agent invitations and page structure

## User interface
- Removed the duplicate inner Agents heading. The route-level heading is now the only page title.
- Added a visible **Agent invitations** section above the agent workspace.
- Pending and expired invitations show website access, expiry, send count, and delivery state.
- Owners can resend or revoke an invitation from the Agents page.
- Accepted agents remain in the agent table; pending invitations are intentionally separate.

## Delivery reliability
Invitation delivery now defaults to direct SMTP delivery after the database transaction commits. This avoids invitations remaining silently queued when a Celery worker is unavailable.

Set the following on the VPS:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.your-provider.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-smtp-user
EMAIL_HOST_PASSWORD=your-smtp-password
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=Support <support@your-domain.com>
FRONTEND_BASE_URL=https://crescentsphere.com
SUPPORT_INVITATION_EMAIL_ASYNC=False
```

Use `SUPPORT_INVITATION_EMAIL_ASYNC=True` only after confirming the Celery worker is always available. A broker submission failure automatically falls back to direct SMTP delivery.

## Delivery states
- `queued`: waiting for delivery
- `sent`: accepted by the configured email backend
- `failed`: delivery raised an error; the UI shows the error and allows resend

## Migration

```text
apps/support/migrations/0022_invitation_delivery_status.py
```
