from __future__ import annotations

from asgiref.sync import async_to_sync
from django.core.cache import cache
from channels.layers import get_channel_layer
from django.db import transaction

from apps.chat.services import make_realtime_event


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
    try:
        return int(cache.get(_visitor_presence_key(visitor_id), 0) or 0) > 0
    except (ValueError, TypeError):
        return False


def _send(groups: set[str], event_name: str, data: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    payload = make_realtime_event(event_name, data)
    for group_name in groups:
        async_to_sync(channel_layer.group_send)(group_name, payload)


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

    groups: set[str] = set()
    if website_id:
        groups.add(support_website_group(website_id))
    if visitor_id:
        groups.add(support_visitor_group(visitor_id))
    for user_id in user_ids or []:
        if user_id:
            groups.add(support_user_group(user_id))
    if not groups:
        return
    # Realtime delivery is a best-effort enhancement. A temporary Channels or
    # Redis outage must never make an already-committed support reply look like
    # it failed to the agent or visitor.
    transaction.on_commit(
        lambda: _send(groups, event_name, data),
        robust=True,
    )
