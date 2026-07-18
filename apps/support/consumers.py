from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from datetime import timedelta

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.chat.services import make_realtime_event, make_realtime_safe
from apps.support.conversation_services import (
    get_context_conversation,
    mark_team_delivered,
    mark_team_read,
    mark_visitor_delivered,
    mark_visitor_read,
    support_conversations_for_context,
)
from apps.support.models import SupportConversation
from apps.support.realtime import (
    publish_support_event,
    support_user_group,
    support_visitor_group,
    support_website_group,
    visitor_presence_connected,
    visitor_presence_disconnected,
    visitor_presence_heartbeat,
)
from apps.support.services import get_support_context, support_chat_enabled, visible_websites
from apps.support.widget_services import (
    WidgetAccessError,
    authenticate_widget_session,
    is_origin_allowed,
    normalize_origin,
    website_for_public_widget,
    widget_public_enabled,
)
from apps.support.widget_services import update_widget_session
from config.authentication import CentralJWTAuthentication

User = get_user_model()


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name.lower():
            return value.decode("latin-1")
    return ""


class SupportTeamConsumer(AsyncJsonWebsocketConsumer):
    """Authenticated Support Chat socket, isolated from Messenger's consumer."""

    async def connect(self):
        self.user = await self._authenticate()
        self.joined_groups: set[str] = set()
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4401)
            return

        context = await self._support_context()
        if not context or not context.account or not context.account.has_product_access:
            await self.close(code=4403)
            return

        self.account_id = str(context.account.id)
        self.role = context.role
        self.agent_id = str(context.agent.id) if context.agent else ""
        await self.accept()

        self.user_group = support_user_group(self.user.id)
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        self.joined_groups.add(self.user_group)
        website_ids = await self._sync_website_groups()
        if website_ids is None:
            await self.close(code=4403)
            return

        await self.send_json(make_realtime_safe({
            "event": "support.ready",
            "event_id": make_realtime_event("support.ready", {})["event_id"],
            "data": {
                "account_id": self.account_id,
                "role": self.role,
                "website_ids": website_ids,
            },
        }))

    async def disconnect(self, code):
        for group_name in list(getattr(self, "joined_groups", set())):
            await self.channel_layer.group_discard(group_name, self.channel_name)
        if hasattr(self, "joined_groups"):
            self.joined_groups.clear()

    async def receive_json(self, content, **kwargs):
        event = str(content.get("event") or "")
        data = content.get("data") or {}
        if event == "support.ping":
            await self.send_json(make_realtime_safe({
                "event": "support.pong",
                "event_id": make_realtime_event("support.pong", {})["event_id"],
                "data": {},
            }))
            return
        if event in {"support.message.delivered", "support.message.read"}:
            await self._record_receipt(event, data)
            return
        if event in {"support.typing.start", "support.typing.stop"}:
            await self._publish_typing(event, data)
            return
        await self.send_json({"event": "error", "data": {"message": "Unsupported Support Chat event."}})

    @database_sync_to_async
    def _record_receipt(self, event_name, data):
        context = get_support_context(self.user)
        try:
            conversation = get_context_conversation(context, data.get("conversation_id"))
        except Exception:
            return
        if event_name == "support.message.read":
            mark_team_read(
                support_conversation=conversation,
                user=self.user,
                message_id=data.get("message_id"),
            )
        else:
            mark_team_delivered(
                support_conversation=conversation,
                user=self.user,
                message_id=data.get("message_id"),
            )

    @database_sync_to_async
    def _publish_typing(self, event_name, data):
        context = get_support_context(self.user)
        try:
            conversation = get_context_conversation(context, data.get("conversation_id"))
        except Exception:
            return
        publish_support_event(
            event_name="support.typing.started" if event_name.endswith("start") else "support.typing.stopped",
            visitor_id=conversation.visitor_id,
            data={
                "conversation_id": str(conversation.id),
                "website_id": str(conversation.website_id),
                "visitor_id": str(conversation.visitor_id),
                "sender": {
                    "kind": "team",
                    "id": str(self.user.id),
                    "display_name": self.user.get_full_name() or self.user.username or "Support team",
                },
                "expires_at": (
                    timezone.now() + timedelta(seconds=7)
                ).isoformat() if event_name.endswith("start") else None,
            },
        )

    async def chat_event(self, event):
        data = event.get("data") or {}
        if event.get("event") == "support.access.updated":
            website_ids = await self._sync_website_groups()
            if website_ids is None:
                await self.close(code=4403)
                return
            data = {**data, "website_ids": website_ids}
        elif not await self._can_receive(data):
            return
        await self.send_json(make_realtime_safe({
            "event": event.get("event"),
            "event_id": event.get("event_id"),
            "occurred_at": event.get("occurred_at"),
            "data": data,
        }))

    async def _sync_website_groups(self):
        website_ids = await self._visible_website_ids()
        if website_ids is None:
            return None
        desired = {support_website_group(website_id) for website_id in website_ids}
        current = {group for group in self.joined_groups if group.startswith("support.website.")}
        for group_name in current - desired:
            await self.channel_layer.group_discard(group_name, self.channel_name)
            self.joined_groups.discard(group_name)
        for group_name in desired - current:
            await self.channel_layer.group_add(group_name, self.channel_name)
            self.joined_groups.add(group_name)
        return website_ids

    @database_sync_to_async
    def _support_context(self):
        if not support_chat_enabled():
            return None
        return get_support_context(self.user)

    @database_sync_to_async
    def _visible_website_ids(self):
        context = get_support_context(self.user)
        if not context.account or not context.account.has_product_access:
            return None
        return [str(value) for value in visible_websites(context).values_list("id", flat=True)]

    @database_sync_to_async
    def _can_receive(self, data):
        context = get_support_context(self.user)
        if not context.account or not context.account.has_product_access:
            return False
        conversation_id = data.get("conversation_id")
        if conversation_id:
            return support_conversations_for_context(context).filter(pk=conversation_id).exists()
        website_id = data.get("website_id")
        if website_id:
            return visible_websites(context).filter(pk=website_id).exists()
        return True

    async def _authenticate(self):
        try:
            token = parse_qs(self.scope.get("query_string", b"").decode()).get("token", [None])[0]
            if not token:
                return None
            return await self._get_user_from_token(token)
        except Exception:
            return None

    @database_sync_to_async
    def _get_user_from_token(self, token):
        try:
            validated_token = AccessToken(token)
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc
        if not validated_token.get("email"):
            user_id = validated_token.get("user_id")
            if not user_id:
                raise InvalidToken("Token missing user_id claim.")
            return User.objects.filter(id=user_id, is_active=True).first()
        return CentralJWTAuthentication().get_user(validated_token)


class SupportWidgetConsumer(AsyncJsonWebsocketConsumer):
    """Origin-bound visitor socket. Tokens are never shared with Messenger."""

    async def connect(self):
        self.joined_groups: set[str] = set()
        site_key = self.scope.get("url_route", {}).get("kwargs", {}).get("site_key")
        query = parse_qs(self.scope.get("query_string", b"").decode())
        session_id = query.get("session_id", [""])[0]
        raw_token = query.get("token", [""])[0]
        origin = normalize_origin(_header(self.scope, b"origin"))
        session = await self._authenticate_session(site_key, session_id, raw_token, origin)
        if not session:
            await self.close(code=4403)
            return

        self.session_id = str(session.id)
        self.visitor_id = str(session.visitor_id)
        self.website_id = str(session.website_id)
        self.session_origin = session.origin
        await self.accept()
        group_name = support_visitor_group(self.visitor_id)
        await self.channel_layer.group_add(group_name, self.channel_name)
        self.joined_groups.add(group_name)
        await self._presence_connected()
        await self.send_json(make_realtime_safe({
            "event": "support.widget.ready",
            "event_id": make_realtime_event("support.widget.ready", {})["event_id"],
            "data": {"session_id": self.session_id, "website_id": self.website_id},
        }))

    async def disconnect(self, code):
        for group_name in list(getattr(self, "joined_groups", set())):
            await self.channel_layer.group_discard(group_name, self.channel_name)
        if hasattr(self, "joined_groups"):
            self.joined_groups.clear()
        if getattr(self, "visitor_id", None):
            await self._presence_disconnected()

    async def receive_json(self, content, **kwargs):
        event = str(content.get("event") or "")
        data = content.get("data") or {}
        if event == "support.ping":
            await self._activity({})
            await self.send_json({"event": "support.pong", "data": {}})
            return
        if event == "support.visitor.activity":
            await self._activity(data)
            return
        if event in {"support.message.delivered", "support.message.read"}:
            await self._record_visitor_receipt(event, data)
            return
        if event in {"support.typing.start", "support.typing.stop"}:
            await self._publish_visitor_typing(event)
            return
        await self.send_json({"event": "error", "data": {"message": "Unsupported Support Chat event."}})

    @database_sync_to_async
    def _presence_connected(self):
        visitor_presence_connected(self.visitor_id)
        from apps.support.models import SupportWidgetSession

        session = SupportWidgetSession.objects.select_related("visitor").filter(
            pk=self.session_id,
            visitor_id=self.visitor_id,
            website_id=self.website_id,
        ).first()
        self._publish_presence(True, session)

    @database_sync_to_async
    def _presence_disconnected(self):
        remaining = visitor_presence_disconnected(self.visitor_id)
        if remaining <= 0:
            self._publish_presence(False)

    def _publish_presence(self, online, session=None):
        visitor = getattr(session, "visitor", None)
        publish_support_event(
            event_name="support.visitor.presence",
            website_id=self.website_id,
            data={
                "website_id": self.website_id,
                "visitor_id": self.visitor_id,
                "is_online": online,
                "last_seen_at": (
                    getattr(visitor, "last_seen_at", None) or timezone.now()
                ).isoformat(),
                "current_page_url": getattr(visitor, "current_page_url", "") or "",
                "referrer": getattr(visitor, "referrer", "") or "",
            },
        )

    @database_sync_to_async
    def _activity(self, data):
        from apps.support.models import SupportWidgetSession

        try:
            session = SupportWidgetSession.objects.select_related("visitor").get(
                pk=self.session_id,
                visitor_id=self.visitor_id,
                website_id=self.website_id,
            )
        except SupportWidgetSession.DoesNotExist:
            return
        current_page_url = str(data.get("current_page_url") or "")[:1000] or None
        referrer = str(data.get("referrer") or "")[:1000] or None
        if current_page_url:
            parsed = urlparse(current_page_url)
            current_origin = normalize_origin(f"{parsed.scheme}://{parsed.netloc}")
            if current_origin != session.origin:
                current_page_url = None
        if referrer:
            parsed_referrer = urlparse(referrer)
            if parsed_referrer.scheme not in {"http", "https"} or not parsed_referrer.netloc:
                referrer = None
        session = update_widget_session(
            session=session,
            current_page_url=current_page_url,
            referrer=referrer,
        )
        visitor_presence_heartbeat(self.visitor_id)
        self._publish_presence(True, session)

    @database_sync_to_async
    def _record_visitor_receipt(self, event_name, data):
        conversation = SupportConversation.objects.filter(
            visitor_id=self.visitor_id,
            website_id=self.website_id,
        ).first()
        if not conversation:
            return
        if event_name == "support.message.read":
            mark_visitor_read(
                support_conversation=conversation,
                message_id=data.get("message_id"),
            )
        else:
            mark_visitor_delivered(
                support_conversation=conversation,
                message_id=data.get("message_id"),
            )

    @database_sync_to_async
    def _publish_visitor_typing(self, event_name):
        conversation = SupportConversation.objects.filter(
            visitor_id=self.visitor_id,
            website_id=self.website_id,
        ).select_related("visitor").first()
        if not conversation:
            return
        publish_support_event(
            event_name="support.typing.started" if event_name.endswith("start") else "support.typing.stopped",
            website_id=conversation.website_id,
            data={
                "conversation_id": str(conversation.id),
                "website_id": self.website_id,
                "visitor_id": self.visitor_id,
                "sender": {
                    "kind": "visitor",
                    "id": self.visitor_id,
                    "display_name": conversation.visitor.name or "Website visitor",
                },
                "expires_at": (
                    timezone.now() + timedelta(seconds=7)
                ).isoformat() if event_name.endswith("start") else None,
            },
        )

    async def chat_event(self, event):
        data = event.get("data") or {}
        if not await self._session_can_receive(data):
            return
        await self.send_json(make_realtime_safe({
            "event": event.get("event"),
            "event_id": event.get("event_id"),
            "occurred_at": event.get("occurred_at"),
            "data": data,
        }))

    @database_sync_to_async
    def _authenticate_session(self, site_key, session_id, raw_token, origin):
        try:
            if not widget_public_enabled():
                return None
            website = website_for_public_widget(site_key)
            if not is_origin_allowed(website, origin):
                return None
            return authenticate_widget_session(
                website=website,
                session_id=session_id,
                raw_token=raw_token,
                origin=origin,
            )
        except (WidgetAccessError, ValueError, TypeError):
            return None

    @database_sync_to_async
    def _session_can_receive(self, data):
        try:
            conversation_id = data.get("conversation_id")
            if not conversation_id:
                return str(data.get("visitor_id") or "") == self.visitor_id
            return SupportConversation.objects.filter(
                pk=conversation_id,
                visitor_id=self.visitor_id,
                website_id=self.website_id,
            ).exists()
        except Exception:
            return False
