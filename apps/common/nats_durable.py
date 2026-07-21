from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from django.conf import settings

from apps.common.models import RealtimeOutboxEvent

_STREAM_ENSURED = False


@dataclass(frozen=True, slots=True)
class PublishResult:
    event_id: str
    sequence: int


def subject_for(event_name: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_-]+", ".", event_name).strip(".").lower() or "unknown"
    return f"{settings.NATS_DURABLE_SUBJECT_PREFIX}.{token}"


def payload_for(row: RealtimeOutboxEvent) -> bytes:
    envelope = {
        "schema_version": 1,
        "event_id": str(row.event_id),
        "event_name": row.event_name,
        "occurred_at": row.created_at.isoformat(),
        "audiences": row.audiences,
        "payload": row.payload,
    }
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def _connect():
    import nats

    return await nats.connect(
        servers=[settings.NATS_URL],
        connect_timeout=settings.NATS_CONNECT_TIMEOUT_SECONDS,
        allow_reconnect=True,
        max_reconnect_attempts=3,
    )


async def _ensure_stream(js) -> None:
    global _STREAM_ENSURED
    if _STREAM_ENSURED:
        return

    from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
    from nats.js.errors import NotFoundError

    config = StreamConfig(
        name=settings.NATS_CHAT_STREAM,
        subjects=[f"{settings.NATS_DURABLE_SUBJECT_PREFIX}.>"],
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        discard=DiscardPolicy.OLD,
        max_age=settings.NATS_DURABLE_MAX_AGE_SECONDS,
        max_bytes=settings.NATS_DURABLE_MAX_BYTES,
        duplicate_window=120,
    )
    try:
        await js.stream_info(settings.NATS_CHAT_STREAM)
    except NotFoundError:
        await js.add_stream(config=config)
    else:
        # Update an existing migration-era stream so its subjects and resource
        # limits match the NATS-primary runtime. This operation is idempotent.
        await js.update_stream(config=config)
    _STREAM_ENSURED = True


async def ensure_stream() -> None:
    nc = await _connect()
    try:
        await _ensure_stream(nc.jetstream())
        await nc.flush(timeout=settings.NATS_CONNECT_TIMEOUT_SECONDS)
    finally:
        await nc.drain()


def ensure_stream_sync() -> None:
    asyncio.run(ensure_stream())


async def publish_rows(rows: list[RealtimeOutboxEvent]) -> list[PublishResult]:
    nc = await _connect()
    try:
        js = nc.jetstream()
        await _ensure_stream(js)

        results: list[PublishResult] = []
        for row in rows:
            ack = await js.publish(
                subject_for(row.event_name),
                payload_for(row),
                headers={"Nats-Msg-Id": str(row.event_id)},
            )
            results.append(PublishResult(str(row.event_id), int(ack.seq)))
        await nc.flush(timeout=settings.NATS_CONNECT_TIMEOUT_SECONDS)
        return results
    finally:
        await nc.drain()


def publish_rows_sync(rows: list[RealtimeOutboxEvent]) -> list[PublishResult]:
    if not rows:
        return []
    return asyncio.run(publish_rows(rows))
