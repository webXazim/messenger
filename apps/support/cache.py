from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.core.cache import cache


DEFAULT_PUBLIC_KB_TTL = int(getattr(settings, "SUPPORT_PUBLIC_KB_CACHE_TTL_SECONDS", 60))


def _version_key(account_id) -> str:
    return f"support:kb:version:{account_id}"


def public_kb_version(account_id) -> int:
    key = _version_key(account_id)
    value = cache.get(key)
    if value is None:
        cache.add(key, 1, timeout=None)
        value = cache.get(key) or 1
    return int(value)


def invalidate_public_kb(account_id) -> None:
    key = _version_key(account_id)
    cache.add(key, 1, timeout=None)
    try:
        cache.incr(key)
    except (ValueError, TypeError):
        cache.set(key, 2, timeout=None)


def public_kb_list_cache_key(*, account_id, website_id, query, category_id, limit) -> str:
    payload = json.dumps(
        {
            "v": public_kb_version(account_id),
            "website": str(website_id),
            "query": (query or "").strip().lower(),
            "category": str(category_id or ""),
            "limit": int(limit),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"support:kb:list:{digest}"
