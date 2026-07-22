from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.common.models import RealtimeOutboxEvent
from apps.common.operational_health import realtime_pipeline_snapshot
from apps.common.nats_durable import publish_rows_sync

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
                delivery_target="nats_jetstream",
                available_at__lte=now,
            )
            .order_by("created_at", "id")[:batch_size]
        )
        ids = [row.pk for row in rows]
        if not ids:
            return []
        lease_until = now + timedelta(seconds=lease_seconds)
        RealtimeOutboxEvent.objects.filter(pk__in=ids).update(
            status=RealtimeOutboxEvent.Status.PROCESSING,
            available_at=lease_until,
            last_error=claim_marker,
        )
        for row in rows:
            row.status = RealtimeOutboxEvent.Status.PROCESSING
            row.available_at = lease_until
            row.last_error = claim_marker

    return rows


def _update_claimed_outbox_row(row: RealtimeOutboxEvent, **changes) -> bool:
    """Update only the lease still owned by this recovery worker.

    Axum can publish and mark the same row while a periodic Celery sweep is in
    flight. Matching the unique claim marker prevents the recovery worker from
    downgrading an already-published row to failed or overwriting Axum's stream
    acknowledgement.
    """

    claim_marker = str(row.last_error or "")
    if not claim_marker.startswith("claim:"):
        return False
    changes["attempts"] = F("attempts") + 1
    return (
        RealtimeOutboxEvent.objects.filter(
            pk=row.pk,
            status=RealtimeOutboxEvent.Status.PROCESSING,
            last_error=claim_marker,
        ).update(**changes)
        == 1
    )


def schedule_realtime_outbox_publish() -> bool:
    """Coalesce request-side wakeups into one Celery task.

    A busy conversation can commit many messages in a short interval. Enqueuing
    one Celery task per message creates avoidable Redis traffic and worker queue
    pressure. The shared cache key allows only one immediate publisher wakeup;
    the periodic beat sweep remains the durable fallback.
    """

    if str(getattr(settings, "REALTIME_OUTBOX_PUBLISHER", "celery")).lower() == "axum":
        return False

    wake_key = str(getattr(settings, "REALTIME_OUTBOX_WAKE_KEY", "realtime:outbox:wake"))
    wake_ttl = max(3, int(getattr(settings, "REALTIME_OUTBOX_WAKE_TTL_SECONDS", 10)))
    try:
        if not cache.add(wake_key, "1", timeout=wake_ttl):
            return False
        publish_realtime_outbox_events.delay()
        return True
    except Exception:
        try:
            cache.delete(wake_key)
        except Exception:
            pass
        logger.exception("Unable to enqueue the realtime outbox publisher")
        return False


@shared_task(name="apps.common.tasks.publish_realtime_outbox_events", ignore_result=True)
def publish_realtime_outbox_events() -> dict[str, int]:
    """Publish one claimed recovery batch with lease-aware status updates."""

    batch_size = max(1, min(500, int(getattr(settings, "REALTIME_OUTBOX_BATCH_SIZE", 100))))
    wake_key = str(getattr(settings, "REALTIME_OUTBOX_WAKE_KEY", "realtime:outbox:wake"))
    rows: list[RealtimeOutboxEvent] = []
    published = 0
    failed = 0
    try:
        rows = _claim_realtime_outbox_rows(batch_size)
        if not rows:
            return {"published": 0, "failed": 0, "disabled": 0}

        try:
            results = publish_rows_sync(rows)
            sequences = {result.event_id: result.sequence for result in results}
        except Exception as exc:
            now = timezone.now()
            error_text = str(exc)[:2000]
            failed_updates = 0
            for row in rows:
                next_attempt = int(row.attempts or 0) + 1
                retry_seconds = min(300, max(2, 2 ** min(next_attempt, 8)))
                if _update_claimed_outbox_row(
                    row,
                    status=RealtimeOutboxEvent.Status.FAILED,
                    available_at=now + timedelta(seconds=retry_seconds),
                    last_error=error_text,
                    updated_at=now,
                ):
                    failed_updates += 1
            return {"published": 0, "failed": failed_updates, "disabled": 0}

        now = timezone.now()
        for row in rows:
            sequence = sequences.get(str(row.event_id))
            if sequence is None:
                if _update_claimed_outbox_row(
                    row,
                    status=RealtimeOutboxEvent.Status.FAILED,
                    available_at=now + timedelta(seconds=2),
                    last_error="JetStream publish returned no acknowledgement for this event.",
                    updated_at=now,
                ):
                    failed += 1
                continue
            if _update_claimed_outbox_row(
                row,
                status=RealtimeOutboxEvent.Status.PUBLISHED,
                available_at=now,
                published_at=now,
                published_transport="nats_jetstream",
                stream_entry_id=str(sequence),
                last_error="",
                updated_at=now,
            ):
                published += 1
        return {"published": published, "failed": failed, "disabled": 0}
    finally:
        try:
            cache.delete(wake_key)
        except Exception:
            pass

        # Messages committed while this batch was in flight may have skipped
        # their wakeup because the coalescing key was held. Schedule one more
        # bounded pass when work remains; cache.add prevents duplicate tasks.
        try:
            has_more = RealtimeOutboxEvent.objects.filter(
                delivery_target="nats_jetstream",
                status__in=[
                    RealtimeOutboxEvent.Status.PENDING,
                    RealtimeOutboxEvent.Status.FAILED,
                ],
                available_at__lte=timezone.now(),
            ).exists()
            if has_more:
                if str(getattr(settings, "REALTIME_OUTBOX_PUBLISHER", "celery")).lower() == "axum":
                    publish_realtime_outbox_events.apply_async(countdown=1)
                else:
                    schedule_realtime_outbox_publish()
        except Exception:
            logger.exception("Unable to inspect remaining realtime outbox work")


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
    """Emit a structured warning before outbox/JetStream pressure becomes user-visible."""
    snapshot = realtime_pipeline_snapshot()
    payload = snapshot.to_dict()
    if snapshot.ok:
        logger.debug("realtime_pipeline_healthy", extra={"realtime_pipeline": payload})
    else:
        logger.error("realtime_pipeline_degraded", extra={"realtime_pipeline": payload})
    return payload

