from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.common.models import RealtimeOutboxEvent
from apps.common.realtime_stream import (
    publish_event_to_stream,
    publish_outbox_event_to_stream,
)

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
        delivery_target="redis_stream",
    )


def _claim_outbox_for_immediate_delivery(outbox_event: RealtimeOutboxEvent) -> bool:
    lease_seconds = max(15, int(getattr(settings, "REALTIME_OUTBOX_LEASE_SECONDS", 60)))
    return bool(
        RealtimeOutboxEvent.objects.filter(
            pk=outbox_event.pk,
            status__in=[RealtimeOutboxEvent.Status.PENDING, RealtimeOutboxEvent.Status.FAILED],
        ).update(
            status=RealtimeOutboxEvent.Status.PROCESSING,
            available_at=timezone.now() + timedelta(seconds=lease_seconds),
        )
    )


def _mark_outbox_published(outbox_event: RealtimeOutboxEvent, stream_entry_id: str) -> None:
    RealtimeOutboxEvent.objects.filter(pk=outbox_event.pk).update(
        status=RealtimeOutboxEvent.Status.PUBLISHED,
        attempts=F("attempts") + 1,
        published_at=timezone.now(),
        published_transport="redis_stream",
        stream_entry_id=stream_entry_id,
        last_error="",
    )


def _mark_outbox_failed(outbox_event: RealtimeOutboxEvent, exc: Exception) -> None:
    attempts = int(outbox_event.attempts or 0) + 1
    retry_seconds = min(300, max(2, 2 ** min(attempts, 8)))
    RealtimeOutboxEvent.objects.filter(pk=outbox_event.pk).update(
        status=RealtimeOutboxEvent.Status.FAILED,
        attempts=F("attempts") + 1,
        available_at=timezone.now() + timedelta(seconds=retry_seconds),
        last_error=str(exc)[:2000],
    )


def publish_realtime_event(
    *,
    event_name: str,
    data: Mapping[str, Any] | None,
    audiences: Iterable[RealtimeAudience],
    durable: bool = True,
    defer_until_commit: bool = True,
) -> dict[str, Any] | None:
    """Publish one event to the Axum Redis Stream.

    Durable events are written to the PostgreSQL outbox in the same business
    transaction and retried by Celery if Redis is unavailable. Disposable
    events skip the outbox but still use the stream so every live socket is
    served exclusively by Axum.
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
        try:
            if outbox_event is not None:
                if not _claim_outbox_for_immediate_delivery(outbox_event):
                    return
                stream_id = publish_outbox_event_to_stream(outbox_event)
                _mark_outbox_published(outbox_event, stream_id)
            else:
                publish_event_to_stream(
                    event=event,
                    audiences=[audience.as_dict() for audience in normalized],
                )
        except Exception as exc:  # Business work stays committed; retry durable events.
            logger.exception("Axum realtime event delivery failed", extra={"event": event_name})
            if outbox_event is not None:
                _mark_outbox_failed(outbox_event, exc)

    if defer_until_commit:
        transaction.on_commit(deliver, robust=True)
    else:
        deliver()
    return event
