from __future__ import annotations

from collections.abc import Iterable

from django.conf import settings
from django.core.mail import send_mail


def send_application_email(
    *,
    subject: str,
    body: str,
    recipients: Iterable[str],
    fail_silently: bool = False,
) -> int:
    """Send mail through the application's configured authentication mail transport.

    Authentication, password reset, and Support invitations all use Django's
    EMAIL_BACKEND/EMAIL_HOST credentials and DEFAULT_FROM_EMAIL from settings.
    """

    normalized_recipients = [str(value).strip() for value in recipients if str(value).strip()]
    if not normalized_recipients:
        return 0
    return send_mail(
        subject,
        body,
        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@localhost"),
        normalized_recipients,
        fail_silently=fail_silently,
    )
