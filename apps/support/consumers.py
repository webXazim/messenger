from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.chat.services import make_realtime_event, make_realtime_safe
from apps.support.conversation_services import support_conversations_for_context
from apps.support.models import SupportConversation
from apps.support.realtime import support_user_group, support_visitor_group, support_website_group
from apps.support.services import get_support_context, support_chat_enabled, visible_websites
from apps.support.widget_services import (
    WidgetAccessError,
    authenticate_widget_session,
    is_origin_allowed,
    normalize_origin,
    website_for_public_widget,
    widget_public_enabled,
)
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
        if event == "support.ping":
            await self.send_json(make_realtime_safe({
                "event": "support.pong",
                "event_id": make_realtime_event("support.pong", {})["event_id"],
                "data": {},
            }))
            return
        await self.send_json({"event": "error", "data": {"message": "Unsupported Support Chat event."}})

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
        await self.accept()
        group_name = support_visitor_group(self.visitor_id)
        await self.channel_layer.group_add(group_name, self.channel_name)
        self.joined_groups.add(group_name)
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

    async def receive_json(self, content, **kwargs):
        if str(content.get("event") or "") == "support.ping":
            await self.send_json({"event": "support.pong", "data": {}})
            return
        await self.send_json({"event": "error", "data": {"message": "Unsupported Support Chat event."}})

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
