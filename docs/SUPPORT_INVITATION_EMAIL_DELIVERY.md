# Support invitation email delivery

Support agent invitations and authentication emails use the same Django mail transport:

- `EMAIL_BACKEND`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS` / `EMAIL_USE_SSL`
- `DEFAULT_FROM_EMAIL`

For reliable immediate delivery without depending on a Celery worker:

```env
SUPPORT_INVITATION_EMAIL_ASYNC=False
```

Each resend and revoke endpoint targets one invitation UUID. The Agents UI also owns loading state per invitation row, so clicking one row never changes or submits the other rows.
