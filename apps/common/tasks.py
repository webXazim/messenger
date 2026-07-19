from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.common.models import RealtimeOutboxEvent
from apps.common.realtime_stream import publish_outbox_event_to_stream, realtime_stream_enabled
from apps.common.operational_health import realtime_pipeline_snapshot

import logging

logger = logging.getLogger(__name__)


def _claim_realtime_outbox_rows(batch_size: int) -> list[RealtimeOutboxEvent]:
    """Claim a bounded batch with a lease so overlapping workers cannot duplicate it."""

    now = timezone.now()
    lease_seconds = max(15, int(getattr(settings, "REALTIME_OUTBOX_LEASE_SECONDS", 60)))
    claim_marker = f"claim:{uuid4()}"

    with transaction.atomic():
        rows = list(
            RealtimeOutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=RealtimeOutboxEvent.Status.PENDING)
                | Q(status=RealtimeOutboxEvent.Status.FAILED)
                | Q(
                    status=RealtimeOutboxEvent.Status.PROCESSING,
                    available_at__lte=now,
                ),
                delivery_target="redis_stream",
                available_at__lte=now,
            )
            .order_by("created_at", "id")[:batch_size]
        )
        ids = [row.pk for row in rows]
        if not ids:
            return []
        RealtimeOutboxEvent.objects.filter(pk__in=ids).update(
            status=RealtimeOutboxEvent.Status.PROCESSING,
            available_at=now + timedelta(seconds=lease_seconds),
            last_error=claim_marker,
        )

    return list(RealtimeOutboxEvent.objects.filter(pk__in=ids).order_by("created_at", "id"))


@shared_task(name="apps.common.tasks.publish_realtime_outbox_events")
def publish_realtime_outbox_events() -> dict[str, int]:
    """Retry pending Django-to-Axum stream handoffs.

    The task is intentionally safe to schedule before Axum cutover. It is a
    no-op unless REALTIME_STREAM_ENABLED=true.
    """

    if not realtime_stream_enabled():
        return {"published": 0, "failed": 0, "disabled": 1}

    batch_size = max(1, min(500, int(getattr(settings, "REALTIME_OUTBOX_BATCH_SIZE", 100))))
    rows = _claim_realtime_outbox_rows(batch_size)

    published = 0
    failed = 0
    for row in rows:
        try:
            stream_entry_id = publish_outbox_event_to_stream(row)
        except Exception as exc:
            failed += 1
            attempts = int(row.attempts or 0) + 1
            retry_seconds = min(300, max(2, 2 ** min(attempts, 8)))
            RealtimeOutboxEvent.objects.filter(
                pk=row.pk,
                status=RealtimeOutboxEvent.Status.PROCESSING,
            ).update(
                status=RealtimeOutboxEvent.Status.FAILED,
                attempts=F("attempts") + 1,
                available_at=timezone.now() + timedelta(seconds=retry_seconds),
                last_error=str(exc)[:2000],
            )
            continue

        published += 1
        RealtimeOutboxEvent.objects.filter(
            pk=row.pk,
            status=RealtimeOutboxEvent.Status.PROCESSING,
        ).update(
            status=RealtimeOutboxEvent.Status.PUBLISHED,
            attempts=F("attempts") + 1,
            published_at=timezone.now(),
            published_transport="redis_stream",
            stream_entry_id=stream_entry_id,
            last_error="",
        )

    return {"published": published, "failed": failed, "disabled": 0}


@shared_task(name="apps.common.tasks.delete_old_realtime_outbox_events", ignore_result=True)
def delete_old_realtime_outbox_events() -> int:
    """Delete old published rows in bounded transactions.

    A single unbounded DELETE can hold row/index locks and create a large
    PostgreSQL WAL burst. Batching keeps the small VPS responsive.
    """
    retention_days = max(1, int(getattr(settings, "REALTIME_OUTBOX_RETENTION_DAYS", 7)))
    batch_size = max(100, min(5000, int(getattr(settings, "REALTIME_OUTBOX_DELETE_BATCH_SIZE", 1000))))
    max_batches = max(1, min(100, int(getattr(settings, "REALTIME_OUTBOX_DELETE_MAX_BATCHES", 20))))
    cutoff = timezone.now() - timedelta(days=retention_days)
    total = 0
    for _ in range(max_batches):
        ids = list(
            RealtimeOutboxEvent.objects.filter(
                status=RealtimeOutboxEvent.Status.PUBLISHED,
                published_at__lt=cutoff,
            ).order_by("published_at", "id").values_list("id", flat=True)[:batch_size]
        )
        if not ids:
            break
        deleted, _ = RealtimeOutboxEvent.objects.filter(id__in=ids).delete()
        total += int(deleted)
        if len(ids) < batch_size:
            break
    return total


@shared_task(name="apps.common.tasks.monitor_realtime_pipeline")
def monitor_realtime_pipeline() -> dict[str, object]:
    """Emit a structured warning before outbox/stream pressure becomes user-visible."""
    snapshot = realtime_pipeline_snapshot()
    payload = snapshot.to_dict()
    if snapshot.ok:
        logger.debug("realtime_pipeline_healthy", extra={"realtime_pipeline": payload})
    else:
        logger.error("realtime_pipeline_degraded", extra={"realtime_pipeline": payload})
    return payload
