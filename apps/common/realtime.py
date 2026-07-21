from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.models import RealtimeOutboxEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RealtimeAudience:
    kind: str
    identifier: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.identifier}


SUPPORTED_AUDIENCE_KINDS = {
    "conversation",
    "user",
    "support_website",
    "support_visitor",
    "support_user",
}


def conversation_audience(conversation_id: Any) -> RealtimeAudience:
    return RealtimeAudience("conversation", str(conversation_id))


def user_audience(user_id: Any) -> RealtimeAudience:
    return RealtimeAudience("user", str(user_id))


def support_website_audience(website_id: Any) -> RealtimeAudience:
    return RealtimeAudience("support_website", str(website_id))


def support_visitor_audience(visitor_id: Any) -> RealtimeAudience:
    return RealtimeAudience("support_visitor", str(visitor_id))


def support_user_audience(user_id: Any) -> RealtimeAudience:
    return RealtimeAudience("support_user", str(user_id))


def make_realtime_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): make_realtime_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_realtime_safe(item) for item in value]
    return value


def make_realtime_event(
    event_name: str,
    data: Mapping[str, Any] | None,
    *,
    event_id: UUID | str | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    return make_realtime_safe(
        {
            "type": "chat.event",
            "version": 1,
            "event": str(event_name),
            "event_id": str(event_id or uuid4()),
            "occurred_at": occurred_at or timezone.now().isoformat(),
            "data": dict(data or {}),
        }
    )


def _normalize_audiences(audiences: Iterable[RealtimeAudience]) -> tuple[RealtimeAudience, ...]:
    normalized: list[RealtimeAudience] = []
    seen: set[tuple[str, str]] = set()
    for audience in audiences:
        if audience.kind not in SUPPORTED_AUDIENCE_KINDS:
            raise ValueError(f"Unsupported realtime audience kind: {audience.kind}")
        identifier = str(audience.identifier or "").strip()
        if not identifier:
            continue
        key = (audience.kind, identifier)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(RealtimeAudience(*key))
    return tuple(normalized)


def _record_outbox_event(
    *, event: dict[str, Any], event_name: str, audiences: tuple[RealtimeAudience, ...]
) -> RealtimeOutboxEvent:
    return RealtimeOutboxEvent.objects.create(
        event_id=event["event_id"],
        event_name=event_name,
        payload=event,
        audiences=[audience.as_dict() for audience in audiences],
        delivery_target="nats_jetstream",
    )


def publish_realtime_event(
    *,
    event_name: str,
    data: Mapping[str, Any] | None,
    audiences: Iterable[RealtimeAudience],
    durable: bool = True,
    defer_until_commit: bool = True,
) -> dict[str, Any] | None:
    """Persist durable events to the outbox and publish them to JetStream after commit.

    Disposable realtime events are handled directly by Axum through Core NATS.
    """
    normalized = _normalize_audiences(audiences)
    if not normalized:
        return None
    event = make_realtime_event(event_name, data)
    outbox_event = (
        _record_outbox_event(event=event, event_name=event_name, audiences=normalized)
        if durable
        else None
    )

    def deliver() -> None:
        if outbox_event is None:
            logger.debug(
                "Disposable event skipped by Django; Axum/Core NATS owns ephemeral delivery",
                extra={"event": event_name},
            )
            return
        try:
            # Wake the existing Celery worker instead of opening a new NATS
            # connection inside the HTTP request. The outbox row stays pending
            # if Redis/Celery is temporarily unavailable, and the periodic beat
            # sweep retries it.
            from apps.common.tasks import schedule_realtime_outbox_publish

            schedule_realtime_outbox_publish()
        except Exception:
            logger.exception(
                "Unable to wake realtime outbox worker; periodic recovery will retry",
                extra={"event": event_name, "outbox_event_id": str(outbox_event.event_id)},
            )

    if defer_until_commit:
        transaction.on_commit(deliver, robust=True)
    else:
        deliver()
    return event
