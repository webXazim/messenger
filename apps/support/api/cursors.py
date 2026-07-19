from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.core import signing
from django.utils.dateparse import parse_datetime

_CURSOR_SALT = "support.inbox.cursor.v1"


class InvalidSupportInboxCursor(ValueError):
    pass


@dataclass(frozen=True)
class SupportInboxCursor:
    ordered_at: datetime
    conversation_id: UUID


def encode_support_inbox_cursor(*, ordered_at: datetime, conversation_id) -> str:
    return signing.dumps(
        {"ordered_at": ordered_at.isoformat(), "conversation_id": str(conversation_id)},
        salt=_CURSOR_SALT,
        compress=True,
    )


def decode_support_inbox_cursor(value: str, *, max_age_seconds: int = 86400) -> SupportInboxCursor:
    try:
        payload = signing.loads(value, salt=_CURSOR_SALT, max_age=max_age_seconds)
        ordered_at = parse_datetime(str(payload["ordered_at"]))
        conversation_id = UUID(str(payload["conversation_id"]))
    except (KeyError, TypeError, ValueError, signing.BadSignature, signing.SignatureExpired) as exc:
        raise InvalidSupportInboxCursor("The Support Inbox cursor is invalid or expired.") from exc
    if ordered_at is None:
        raise InvalidSupportInboxCursor("The Support Inbox cursor is invalid or expired.")
    return SupportInboxCursor(ordered_at=ordered_at, conversation_id=conversation_id)
