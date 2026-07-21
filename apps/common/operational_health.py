from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db.models import Count, Min, Q
from django.utils import timezone

from apps.common.models import RealtimeOutboxEvent


@dataclass(frozen=True)
class RealtimePipelineSnapshot:
    ok: bool
    stream_enabled: bool
    redis_ok: bool
    redis_detail: str
    stream_name: str
    stream_length: int
    consumer_group: str
    consumer_group_exists: bool
    consumer_pending: int
    consumer_lag: int | None
    outbox_pending: int
    outbox_processing: int
    outbox_failed: int
    outbox_published: int
    oldest_unpublished_age_seconds: int | None
    thresholds: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _age_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((timezone.now() - value).total_seconds()))


def realtime_pipeline_snapshot() -> RealtimePipelineSnapshot:
    max_outbox_age = max(10, int(getattr(settings, "REALTIME_OUTBOX_MAX_AGE_SECONDS", 120)))
    max_failed = max(0, int(getattr(settings, "REALTIME_OUTBOX_MAX_FAILED", 25)))
    counts = {
        row["status"]: int(row["total"])
        for row in RealtimeOutboxEvent.objects.values("status").annotate(total=Count("id"))
    }
    unpublished = RealtimeOutboxEvent.objects.filter(
        Q(status=RealtimeOutboxEvent.Status.PENDING)
        | Q(status=RealtimeOutboxEvent.Status.PROCESSING)
        | Q(status=RealtimeOutboxEvent.Status.FAILED)
    ).aggregate(oldest=Min("created_at"))["oldest"]
    oldest_age = _age_seconds(unpublished)
    pending = counts.get(RealtimeOutboxEvent.Status.PENDING, 0)
    processing = counts.get(RealtimeOutboxEvent.Status.PROCESSING, 0)
    failed = counts.get(RealtimeOutboxEvent.Status.FAILED, 0)
    published = counts.get(RealtimeOutboxEvent.Status.PUBLISHED, 0)
    ok = failed <= max_failed and (oldest_age is None or oldest_age <= max_outbox_age)
    return RealtimePipelineSnapshot(
        ok=ok,
        stream_enabled=False,
        redis_ok=True,
        redis_detail="not used for realtime",
        stream_name=str(getattr(settings, "NATS_CHAT_STREAM", "CHAT_EVENTS")),
        stream_length=0,
        consumer_group=str(getattr(settings, "NATS_DURABLE_CONSUMER", "realtime-axum-v1")),
        consumer_group_exists=True,
        consumer_pending=0,
        consumer_lag=None,
        outbox_pending=pending,
        outbox_processing=processing,
        outbox_failed=failed,
        outbox_published=published,
        oldest_unpublished_age_seconds=oldest_age,
        thresholds={
            "max_outbox_age_seconds": max_outbox_age,
            "max_failed": max_failed,
            "max_stream_pending": 0,
        },
    )
