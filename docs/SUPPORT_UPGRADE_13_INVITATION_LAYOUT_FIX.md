# Support Upgrade 13 — invitation and layout fix

- Agent invitation creation no longer depends on synchronous SMTP delivery.
- Email delivery is queued through Celery with retry/backoff.
- A broker failure no longer rolls back or hides a successfully created invitation.
- The invite modal uses a balanced two-column layout and clearer owner/seat guidance.
- Website setup rows no longer compress labels and fields.
- Widget preview preserves the real 380 × 620 panel dimensions and displays it at a controlled scale.
