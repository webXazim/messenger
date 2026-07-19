from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db.models import Count, Min, Q
from django.utils import timezone

from apps.common.models import RealtimeOutboxEvent
from apps.common.realtime_stream import RealtimeStreamUnavailable, _redis_client


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
    stream_enabled = bool(getattr(settings, "REALTIME_STREAM_ENABLED", False))
    stream_name = str(getattr(settings, "REALTIME_STREAM_NAME", "realtime:durable:v1") or "")
    group_name = str(getattr(settings, "REALTIME_STREAM_GROUP", "axum-single-v1") or "")
    max_outbox_age = max(10, int(getattr(settings, "REALTIME_OUTBOX_MAX_AGE_SECONDS", 120)))
    max_failed = max(0, int(getattr(settings, "REALTIME_OUTBOX_MAX_FAILED", 25)))
    max_pending = max(0, int(getattr(settings, "REALTIME_STREAM_MAX_PENDING", 250)))

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

    redis_ok = not stream_enabled
    redis_detail = "disabled" if not stream_enabled else "not checked"
    stream_length = 0
    group_exists = False
    group_pending = 0
    group_lag: int | None = None

    if stream_enabled:
        stream_url = str(getattr(settings, "REALTIME_STREAM_URL", "") or "").strip()
        try:
            if not stream_url:
                raise RealtimeStreamUnavailable("REALTIME_STREAM_URL is not configured")
            client = _redis_client(stream_url)
            client.ping()
            stream_length = int(client.xlen(stream_name))
            groups = client.xinfo_groups(stream_name)
            for group in groups:
                name = group.get("name")
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "replace")
                if str(name) != group_name:
                    continue
                group_exists = True
                group_pending = int(group.get("pending") or 0)
                raw_lag = group.get("lag")
                group_lag = None if raw_lag is None else int(raw_lag)
                break
            redis_ok = True
            redis_detail = "ok"
        except Exception as exc:
            redis_ok = False
            redis_detail = str(exc)[:500]

    pending = counts.get(RealtimeOutboxEvent.Status.PENDING, 0)
    processing = counts.get(RealtimeOutboxEvent.Status.PROCESSING, 0)
    failed = counts.get(RealtimeOutboxEvent.Status.FAILED, 0)
    published = counts.get(RealtimeOutboxEvent.Status.PUBLISHED, 0)

    ok = (
        (not stream_enabled or redis_ok)
        and (not stream_enabled or group_exists)
        and failed <= max_failed
        and group_pending <= max_pending
        and (oldest_age is None or oldest_age <= max_outbox_age)
    )

    return RealtimePipelineSnapshot(
        ok=ok,
        stream_enabled=stream_enabled,
        redis_ok=redis_ok,
        redis_detail=redis_detail,
        stream_name=stream_name,
        stream_length=stream_length,
        consumer_group=group_name,
        consumer_group_exists=group_exists,
        consumer_pending=group_pending,
        consumer_lag=group_lag,
        outbox_pending=pending,
        outbox_processing=processing,
        outbox_failed=failed,
        outbox_published=published,
        oldest_unpublished_age_seconds=oldest_age,
        thresholds={
            "max_outbox_age_seconds": max_outbox_age,
            "max_failed": max_failed,
            "max_stream_pending": max_pending,
        },
    )
