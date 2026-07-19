from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from django.conf import settings

from apps.common.models import RealtimeOutboxEvent

logger = logging.getLogger(__name__)


class RealtimeStreamUnavailable(RuntimeError):
    """Raised when the configured Redis realtime stream cannot be reached."""


@lru_cache(maxsize=4)
def _redis_client(url: str):
    try:
        import redis
    except ImportError as exc:  # pragma: no cover - dependency is required in production.
        raise RealtimeStreamUnavailable("The redis package is not installed.") from exc
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=float(getattr(settings, "REALTIME_STREAM_CONNECT_TIMEOUT_SECONDS", 2.0)),
        socket_timeout=float(getattr(settings, "REALTIME_STREAM_SOCKET_TIMEOUT_SECONDS", 2.0)),
        health_check_interval=30,
    )


def realtime_stream_enabled() -> bool:
    return bool(getattr(settings, "REALTIME_STREAM_ENABLED", False))



def publish_event_to_stream(*, event: dict[str, Any], audiences: list[dict[str, str]] | tuple[dict[str, str], ...]) -> str:
    """Append a transport event that does not need a PostgreSQL outbox row.

    This is used only for disposable Django-originated notifications. Durable
    business events always use ``publish_outbox_event_to_stream`` so a Redis
    interruption cannot lose the committed notification.
    """
    stream_url = str(getattr(settings, "REALTIME_STREAM_URL", "") or "").strip()
    if not stream_url:
        raise RealtimeStreamUnavailable("REALTIME_STREAM_URL is not configured.")
    stream_name = str(getattr(settings, "REALTIME_STREAM_NAME", "realtime:durable:v1") or "").strip()
    if not stream_name:
        raise RealtimeStreamUnavailable("REALTIME_STREAM_NAME is empty.")
    maxlen = max(1_000, int(getattr(settings, "REALTIME_STREAM_MAXLEN", 25_000)))
    payload = {
        "event_id": str(event.get("event_id") or ""),
        "event_name": str(event.get("event") or ""),
        "payload": json.dumps(event, separators=(",", ":"), ensure_ascii=False),
        "audiences": json.dumps(list(audiences), separators=(",", ":"), ensure_ascii=False),
        "created_at": str(event.get("occurred_at") or ""),
        "durable": "0",
    }
    try:
        stream_id = _redis_client(stream_url).xadd(
            stream_name, payload, maxlen=maxlen, approximate=True
        )
    except Exception as exc:
        raise RealtimeStreamUnavailable(str(exc)) from exc
    if not stream_id:
        raise RealtimeStreamUnavailable("Redis returned an empty stream entry id.")
    return str(stream_id)

def publish_outbox_event_to_stream(outbox_event: RealtimeOutboxEvent) -> str:
    """Append one committed outbox event to the Axum Redis Stream.

    PostgreSQL remains authoritative. The stream is only the durable handoff
    between Django and the single Axum realtime process.
    """

    stream_url = str(getattr(settings, "REALTIME_STREAM_URL", "") or "").strip()
    if not stream_url:
        raise RealtimeStreamUnavailable("REALTIME_STREAM_URL is not configured.")

    stream_name = str(getattr(settings, "REALTIME_STREAM_NAME", "realtime:durable:v1") or "").strip()
    if not stream_name:
        raise RealtimeStreamUnavailable("REALTIME_STREAM_NAME is empty.")

    maxlen = max(1_000, int(getattr(settings, "REALTIME_STREAM_MAXLEN", 25_000)))
    payload: dict[str, Any] = {
        "event_id": str(outbox_event.event_id),
        "event_name": outbox_event.event_name,
        "payload": json.dumps(outbox_event.payload, separators=(",", ":"), ensure_ascii=False),
        "audiences": json.dumps(outbox_event.audiences, separators=(",", ":"), ensure_ascii=False),
        "created_at": outbox_event.created_at.isoformat(),
    }

    try:
        stream_id = _redis_client(stream_url).xadd(
            stream_name,
            payload,
            maxlen=maxlen,
            approximate=True,
        )
    except Exception as exc:
        raise RealtimeStreamUnavailable(str(exc)) from exc

    if not stream_id:
        raise RealtimeStreamUnavailable("Redis returned an empty stream entry id.")
    return str(stream_id)
