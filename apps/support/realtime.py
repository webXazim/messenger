from __future__ import annotations

from django.core.cache import cache

from apps.common.realtime_presence import support_visitor_online

from apps.common.realtime import (
    publish_realtime_event,
    support_user_audience,
    support_visitor_audience,
    support_website_audience,
)


def support_website_group(website_id) -> str:
    return f"support.website.{website_id}"


def support_visitor_group(visitor_id) -> str:
    return f"support.visitor.{visitor_id}"


def support_user_group(user_id) -> str:
    return f"support.user.{user_id}"


def _visitor_presence_key(visitor_id) -> str:
    return f"support:visitor-presence:{visitor_id}"


def visitor_presence_connected(visitor_id) -> int:
    key = _visitor_presence_key(visitor_id)
    cache.add(key, 0, timeout=120)
    try:
        count = int(cache.incr(key))
    except (ValueError, TypeError):
        count = 1
        cache.set(key, count, timeout=120)
    cache.touch(key, timeout=120)
    return count


def visitor_presence_heartbeat(visitor_id) -> None:
    cache.touch(_visitor_presence_key(visitor_id), timeout=120)


def visitor_presence_disconnected(visitor_id) -> int:
    key = _visitor_presence_key(visitor_id)
    try:
        count = max(0, int(cache.decr(key)))
    except (ValueError, TypeError):
        count = 0
    if count <= 0:
        cache.delete(key)
    else:
        cache.touch(key, timeout=120)
    return count


def visitor_is_online(visitor_id) -> bool:
    return support_visitor_online(visitor_id)


def publish_support_event(
    *,
    event_name: str,
    data: dict,
    website_id=None,
    visitor_id=None,
    user_ids: list | tuple | set | None = None,
) -> None:
    """Publish only after the surrounding database transaction commits.

    Support Chat uses its own channel namespaces. Personal Messenger groups and
    event contracts are never changed by these notifications.
    """

    audiences = []
    if website_id:
        audiences.append(support_website_audience(website_id))
    if visitor_id:
        audiences.append(support_visitor_audience(visitor_id))
    audiences.extend(
        support_user_audience(user_id)
        for user_id in (user_ids or [])
        if user_id
    )
    if not audiences:
        return
    publish_realtime_event(
        event_name=event_name,
        data=data,
        audiences=audiences,
        defer_until_commit=True,
    )
