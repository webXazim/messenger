from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any, Iterable

from django.conf import settings
from django.core.cache import cache


USER_KEY_PREFIX = "realtime:presence:user:"
RECIPIENT_KEY_PREFIX = "realtime:presence:recipients:"
PROFILE_KEY_PREFIX = "realtime:presence:profile:"
SUPPORT_VISITOR_KEY_PREFIX = "realtime:presence:support-visitor:"


@lru_cache(maxsize=2)
def _client(url: str):
    import redis

    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=1.5,
        socket_timeout=1.5,
        health_check_interval=30,
    )


def _redis_url() -> str:
    return str(getattr(settings, "REALTIME_PRESENCE_REDIS_URL", "") or "").strip()


def _ttl() -> int:
    return max(45, int(getattr(settings, "REALTIME_PRESENCE_TTL_SECONDS", 90)))


def _metadata_ttl() -> int:
    return max(300, int(getattr(settings, "REALTIME_PRESENCE_METADATA_TTL_SECONDS", 3600)))


def _normalize_device_record(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    try:
        last_seen = float(value.get("last_seen") or 0)
    except (TypeError, ValueError):
        return None
    if last_seen <= 0:
        return None
    device_type = str(value.get("device_type") or "unknown").lower()
    if device_type not in {"desktop", "mobile", "tablet", "widget"}:
        device_type = "unknown"
    presence_status = "idle" if str(value.get("presence_status") or "").lower() == "idle" else "active"
    return {
        "last_seen": last_seen,
        "device_type": device_type,
        "presence_status": presence_status,
    }


def _fallback_registry_key(user_id: Any) -> str:
    return f"{USER_KEY_PREFIX}{user_id}"


def read_user_presence(user_id: Any) -> dict[str, dict[str, Any]]:
    now = time.time()
    ttl = _ttl()
    url = _redis_url()
    active: dict[str, dict[str, Any]] = {}
    if url:
        try:
            client = _client(url)
            key = f"{USER_KEY_PREFIX}{user_id}"
            raw = client.hgetall(key)
            active, stale = _active_presence_records(raw, now=now, ttl=ttl)
            if stale:
                client.hdel(key, *stale)
            if active:
                client.expire(key, ttl * 2)
            return active
        except Exception:
            pass

    raw = cache.get(_fallback_registry_key(user_id)) or {}
    if isinstance(raw, dict):
        for device_id, payload in raw.items():
            record = _normalize_device_record(payload)
            if record and now - record["last_seen"] < ttl:
                active[str(device_id)] = record
    if active:
        cache.set(_fallback_registry_key(user_id), active, timeout=ttl * 2)
    else:
        cache.delete(_fallback_registry_key(user_id))
    return active


def _active_presence_records(raw: dict[str, Any], *, now: float, ttl: int) -> tuple[dict[str, dict[str, Any]], list[str]]:
    active: dict[str, dict[str, Any]] = {}
    stale: list[str] = []
    for device_id, payload in (raw or {}).items():
        record = _normalize_device_record(payload)
        if record and now - record["last_seen"] < ttl:
            active[str(device_id)] = record
        else:
            stale.append(str(device_id))
    return active, stale


def read_many_user_presence(user_ids: Iterable[Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Read many presence hashes with one Redis pipeline/cache multi-get.

    The returned mapping always contains every requested user id. Stale device
    fields are cleaned best-effort without turning a read endpoint into an N+1
    Redis workload.
    """

    ids = list(dict.fromkeys(str(value) for value in user_ids if value is not None))
    result: dict[str, dict[str, dict[str, Any]]] = {user_id: {} for user_id in ids}
    if not ids:
        return result

    now = time.time()
    ttl = _ttl()
    url = _redis_url()
    if url:
        try:
            client = _client(url)
            pipe = client.pipeline(transaction=False)
            for user_id in ids:
                pipe.hgetall(f"{USER_KEY_PREFIX}{user_id}")
            rows = pipe.execute()

            cleanup = client.pipeline(transaction=False)
            cleanup_needed = False
            for user_id, raw in zip(ids, rows):
                active, stale = _active_presence_records(raw or {}, now=now, ttl=ttl)
                result[user_id] = active
                key = f"{USER_KEY_PREFIX}{user_id}"
                if stale:
                    cleanup.hdel(key, *stale)
                    cleanup_needed = True
                if active:
                    cleanup.expire(key, ttl * 2)
                    cleanup_needed = True
            if cleanup_needed:
                cleanup.execute()
            return result
        except Exception:
            pass

    keys = {f"{USER_KEY_PREFIX}{user_id}": user_id for user_id in ids}
    cached = cache.get_many(keys.keys())
    refresh: dict[str, dict[str, dict[str, Any]]] = {}
    delete_keys: list[str] = []
    for key, user_id in keys.items():
        raw = cached.get(key) or {}
        active, _ = _active_presence_records(raw if isinstance(raw, dict) else {}, now=now, ttl=ttl)
        result[user_id] = active
        if active:
            refresh[key] = active
        elif key in cached:
            delete_keys.append(key)
    if refresh:
        cache.set_many(refresh, timeout=ttl * 2)
    if delete_keys:
        cache.delete_many(delete_keys)
    return result


def support_visitors_online(visitor_ids: Iterable[Any]) -> dict[str, bool]:
    """Resolve support visitor presence in one Redis/cache round trip."""

    ids = list(dict.fromkeys(str(value) for value in visitor_ids if value is not None))
    result = {visitor_id: False for visitor_id in ids}
    if not ids:
        return result

    url = _redis_url()
    if url:
        try:
            client = _client(url)
            pipe = client.pipeline(transaction=False)
            for visitor_id in ids:
                pipe.exists(f"{SUPPORT_VISITOR_KEY_PREFIX}{visitor_id}")
            for visitor_id, online in zip(ids, pipe.execute()):
                result[visitor_id] = bool(online)
            return result
        except Exception:
            pass

    keys = {f"support:visitor-presence:{visitor_id}": visitor_id for visitor_id in ids}
    cached = cache.get_many(keys.keys())
    for key, visitor_id in keys.items():
        result[visitor_id] = bool(cached.get(key))
    return result


def upsert_user_presence(
    user_id: Any,
    device_id: str,
    *,
    device_type: str = "unknown",
    presence_status: str = "active",
    last_seen: float | None = None,
) -> dict[str, dict[str, Any]]:
    record = {
        "last_seen": float(last_seen or time.time()),
        "device_type": str(device_type or "unknown")[:32],
        "presence_status": "idle" if str(presence_status).lower() == "idle" else "active",
    }
    key = f"{USER_KEY_PREFIX}{user_id}"
    url = _redis_url()
    if url:
        try:
            client = _client(url)
            pipe = client.pipeline(transaction=False)
            pipe.hset(key, str(device_id)[:200], json.dumps(record, separators=(",", ":")))
            pipe.expire(key, _ttl() * 2)
            pipe.execute()
            return read_user_presence(user_id)
        except Exception:
            pass
    registry = read_user_presence(user_id)
    registry[str(device_id)[:200]] = record
    cache.set(key, registry, timeout=_ttl() * 2)
    return registry


def remove_user_presence(user_id: Any, device_id: str, *, prefix: bool = False) -> dict[str, dict[str, Any]]:
    key = f"{USER_KEY_PREFIX}{user_id}"
    url = _redis_url()
    if url:
        try:
            client = _client(url)
            if prefix:
                fields = [field for field in client.hkeys(key) if field == device_id or field.startswith(f"{device_id}:")]
                if fields:
                    client.hdel(key, *fields)
            else:
                client.hdel(key, device_id)
            return read_user_presence(user_id)
        except Exception:
            pass
    registry = read_user_presence(user_id)
    if prefix:
        registry = {
            key_id: record
            for key_id, record in registry.items()
            if key_id != device_id and not key_id.startswith(f"{device_id}:")
        }
    else:
        registry.pop(device_id, None)
    if registry:
        cache.set(key, registry, timeout=_ttl() * 2)
    else:
        cache.delete(key)
    return registry


def cache_user_presence_metadata(*, user_id: Any, recipient_ids: list[str], profile: dict[str, Any]) -> None:
    url = _redis_url()
    if not url:
        return
    try:
        client = _client(url)
        ttl = _metadata_ttl()
        pipe = client.pipeline(transaction=False)
        pipe.setex(
            f"{RECIPIENT_KEY_PREFIX}{user_id}",
            ttl,
            json.dumps(list(dict.fromkeys(str(value) for value in recipient_ids if value)), separators=(",", ":")),
        )
        pipe.setex(
            f"{PROFILE_KEY_PREFIX}{user_id}",
            ttl,
            json.dumps(profile, separators=(",", ":"), ensure_ascii=False),
        )
        pipe.execute()
    except Exception:
        return


def support_visitor_online(visitor_id: Any) -> bool:
    url = _redis_url()
    if url:
        try:
            return bool(_client(url).exists(f"{SUPPORT_VISITOR_KEY_PREFIX}{visitor_id}"))
        except Exception:
            pass
    return bool(cache.get(f"support:visitor-presence:{visitor_id}"))
