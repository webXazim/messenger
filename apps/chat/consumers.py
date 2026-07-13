from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.core.cache import cache
from django.contrib.auth import get_user_model
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.chat.models import CallSession, Conversation, Message
from apps.chat.selectors import user_conversations_qs
from apps.chat.services import (
    accept_call,
    add_reaction,
    clear_presence,
    decline_call,
    edit_message,
    end_call,
    heartbeat_call_participant,
    mark_conversation_delivered,
    mark_conversation_read,
    remove_reaction,
    send_call_signal,
    update_call_media_state,
    update_call_speaking_state,
    send_message,
    set_presence,
    soft_delete_message,
    make_realtime_event,
    make_realtime_safe,
    get_public_presence_snapshot,
    presence_recipient_ids,
)
from apps.chat.central_access import get_product_access_decision
from config.authentication import CentralJWTAuthentication

User = get_user_model()


class ChatConsumer(AsyncJsonWebsocketConsumer):
    def _event_payload(self, event_name, data):
        return make_realtime_event(event_name, data)

    async def connect(self):
        self.user = None
        self.auth_token = ""
        self.device_id = "ws-default"
        self.joined_groups = set()
        self.typing_conversations = set()

        self.user = await self._authenticate_from_token()
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4401)
            return

        query_string = self.scope.get("query_string", b"").decode()
        self.device_id = parse_qs(query_string).get("device_id", ["ws-default"])[0]
        await self.accept()
        user_group = f"user_{self.user.id}"
        await self.channel_layer.group_add(user_group, self.channel_name)
        self.joined_groups.add(user_group)
        try:
            snapshot = await self._set_presence()
            await self._broadcast_presence_update(snapshot)
        except Exception:
            # Presence should never take down an otherwise valid websocket session.
            pass

    async def disconnect(self, code):
        if getattr(self, "user", None) and getattr(self.user, "is_authenticated", False):
            await self._stop_all_typing()
        for group_name in list(getattr(self, "joined_groups", [])):
            await self.channel_layer.group_discard(group_name, self.channel_name)
        if hasattr(self, "joined_groups"):
            self.joined_groups.clear()
        if getattr(self, "user", None) and getattr(self.user, "is_authenticated", False):
            snapshot = await self._clear_presence()
            await self._broadcast_presence_update(snapshot)

    async def receive_json(self, content, **kwargs):
        event = content.get("event")
        data = content.get("data", {})
        if event == "conversation.subscribe":
            await self._subscribe(str(data.get("conversation_id")))
        elif event == "conversation.unsubscribe":
            await self._unsubscribe(str(data.get("conversation_id")))
        elif event == "message.send":
            await self._message_send(data)
        elif event == "message.edit":
            await self._message_edit(data)
        elif event == "message.delete":
            await self._message_delete(data)
        elif event == "message.delivered":
            await self._message_delivered(data)
        elif event == "message.read":
            await self._message_read(data)
        elif event == "message.react":
            await self._message_react(data)
        elif event == "message.unreact":
            await self._message_unreact(data)
        elif event == "typing.start":
            await self._typing_event("typing.started", data)
        elif event == "typing.stop":
            await self._typing_event("typing.stopped", data)
        elif event == "call.accept":
            await self._call_accept(data)
        elif event == "call.decline":
            await self._call_decline(data)
        elif event == "call.end":
            await self._call_end(data)
        elif event == "call.signal":
            await self._call_signal(data)
        elif event == "call.heartbeat":
            await self._call_heartbeat(data)
        elif event == "call.media_state":
            await self._call_media_state(data)
        elif event == "call.speaker_state":
            await self._call_speaker_state(data)
        elif event == "presence.ping":
            snapshot = await self._set_presence()
            await self._broadcast_presence_update(snapshot)
            await self.send_json(make_realtime_safe({
                "event": "presence.pong",
                "event_id": self._event_payload("presence.pong", {})["event_id"],
                "occurred_at": timezone.now().isoformat(),
                "data": {"user_id": str(self.user.id), **snapshot, "server_time": timezone.now().isoformat()},
            }))
        else:
            await self.send_json({"event": "error", "data": {"message": "Unsupported event."}})

    async def chat_event(self, event):
        if event["event"] in {"typing.started", "typing.stopped"}:
            data = event.get("data") or {}
            if str(data.get("user_id") or "") == str(self.user.id):
                return
        if event["event"] == "call.signal":
            data = event.get("data") or {}
            current_user_id = str(self.user.id)
            recipient_ids = {str(user_id) for user_id in data.get("recipient_user_ids", []) if user_id}
            to_user_id = str(data.get("to_user_id") or "")
            if recipient_ids and current_user_id not in recipient_ids:
                return
            if to_user_id and current_user_id != to_user_id:
                return
        await self.send_json(make_realtime_safe({
            "event": event["event"],
            "event_id": event.get("event_id"),
            "occurred_at": event.get("occurred_at"),
            "data": event["data"],
        }))

    async def _subscribe(self, conversation_id):
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        group_name = f"conversation_{conversation_id}"
        await self.channel_layer.group_add(group_name, self.channel_name)
        self.joined_groups.add(group_name)
        payload = await self._mark_delivered_sync(conversation_id, None)
        await self.send_json({"event": "conversation.subscribed", "data": {"conversation_id": conversation_id}})
        if payload.get("changed") and payload.get("last_delivered_message_id"):
            await self.send_json(self._event_payload("message.delivered", payload))

    async def _unsubscribe(self, conversation_id):
        group_name = f"conversation_{conversation_id}"
        if group_name in self.joined_groups:
            await self.channel_layer.group_discard(group_name, self.channel_name)
            self.joined_groups.discard(group_name)
        await self.send_json({"event": "conversation.unsubscribed", "data": {"conversation_id": conversation_id}})

    async def _message_send(self, data):
        conversation_id = str(data.get("conversation_id"))
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        allowed = await self._central_access_sync(
            "send_message",
            idempotency_key=str(data.get("client_temp_id") or ""),
            metadata={"conversation_id": conversation_id, "source": "websocket"},
        )
        if not allowed.get("allowed"):
            await self.send_json({"event": "error", "data": {"message": "Central account does not allow this action.", "central_access": allowed}})
            return
        payload = await self._send_message_sync(
            conversation_id,
            data.get("text", ""),
            data.get("type", "text"),
            data.get("reply_to_id"),
            data.get("client_temp_id", ""),
            data.get("attachment_ids", []) or [],
            data.get("is_voice_note"),
            data.get("duration_seconds"),
            data.get("waveform", []) or [],
            data.get("entities", []) or [],
            data.get("encryption"),
            data.get("attachment_encryption", []) or [],
        )
        await self.channel_layer.group_send(f"conversation_{conversation_id}", self._event_payload("message.created", payload))
        await self._broadcast_conversation_update(conversation_id)

    async def _message_edit(self, data):
        conversation_id = str(data.get("conversation_id"))
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        payload = await self._edit_message_sync(
            str(data.get("message_id")),
            data.get("text", ""),
            data.get("entities", []) or [],
            data.get("encryption"),
        )
        await self.channel_layer.group_send(f"conversation_{conversation_id}", self._event_payload("message.updated", payload))
        await self._broadcast_conversation_update(conversation_id)

    async def _message_delete(self, data):
        conversation_id = str(data.get("conversation_id"))
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        payload = await self._delete_message_sync(str(data.get("message_id")))
        await self.channel_layer.group_send(f"conversation_{conversation_id}", self._event_payload("message.deleted", payload))
        await self._broadcast_conversation_update(conversation_id)

    async def _message_delivered(self, data):
        conversation_id = str(data.get("conversation_id"))
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        payload = await self._mark_delivered_sync(conversation_id, data.get("message_id"))
        if payload.get("changed"):
            await self.channel_layer.group_send(f"conversation_{conversation_id}", self._event_payload("message.delivered", payload))

    async def _message_read(self, data):
        conversation_id = str(data.get("conversation_id"))
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        payload = await self._mark_read_sync(conversation_id, data.get("message_id"))
        if payload.get("changed"):
            await self.channel_layer.group_send(f"conversation_{conversation_id}", self._event_payload("message.read", payload))

    async def _message_react(self, data):
        conversation_id = str(data.get("conversation_id"))
        message_id = str(data.get("message_id"))
        emoji = data.get("emoji", "")
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        payload = await self._react_sync(message_id, emoji)
        await self.channel_layer.group_send(f"conversation_{conversation_id}", self._event_payload("message.reaction_updated", payload))

    async def _message_unreact(self, data):
        conversation_id = str(data.get("conversation_id"))
        message_id = str(data.get("message_id"))
        emoji = data.get("emoji", "")
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        payload = await self._unreact_sync(message_id, emoji)
        await self.channel_layer.group_send(f"conversation_{conversation_id}", self._event_payload("message.reaction_updated", payload))

    async def _call_accept(self, data):
        call_id = str(data.get("call_id"))
        call = await self._accept_call_sync(call_id)
        await self.channel_layer.group_send(f"conversation_{call['conversation_id']}", self._event_payload("call.accepted", call))
        await self._broadcast_call_timeline(call)
        await self._broadcast_conversation_update(call["conversation_id"])

    async def _call_decline(self, data):
        call_id = str(data.get("call_id"))
        call = await self._decline_call_sync(call_id, data.get("reason", "declined"))
        await self.channel_layer.group_send(f"conversation_{call['conversation_id']}", self._event_payload("call.declined", call))
        await self._broadcast_call_timeline(call)
        await self._broadcast_conversation_update(call["conversation_id"])

    async def _call_end(self, data):
        call_id = str(data.get("call_id"))
        call = await self._end_call_sync(call_id, data.get("reason", "ended"))
        await self.channel_layer.group_send(f"conversation_{call['conversation_id']}", self._event_payload("call.ended", call))
        await self._broadcast_call_timeline(call)
        await self._broadcast_conversation_update(call["conversation_id"])

    async def _call_signal(self, data):
        call_id = str(data.get("call_id"))
        signal_payload = dict(data.get("payload") or {})
        for key in ("signal_id", "to_user_id", "from_user_id", "conversation_id"):
            if data.get(key) and not signal_payload.get(key):
                signal_payload[key] = data.get(key)
        payload = await self._signal_call_sync(call_id, data.get("signal_type"), signal_payload)
        await self.channel_layer.group_send(f"conversation_{payload['conversation_id']}", self._event_payload("call.signal", payload))

    async def _call_heartbeat(self, data):
        call_id = str(data.get("call_id"))
        payload = await self._heartbeat_call_sync(call_id, data.get("network_quality"), data.get("metrics") or {})
        await self.channel_layer.group_send(f"conversation_{payload['conversation_id']}", self._event_payload("call.heartbeat", payload))

    async def _call_media_state(self, data):
        call_id = str(data.get("call_id"))
        payload = await self._media_state_call_sync(call_id, data)
        await self.channel_layer.group_send(f"conversation_{payload['conversation_id']}", self._event_payload("call.media_state", payload))
        if payload.get("orchestration"):
            await self.channel_layer.group_send(f"conversation_{payload['conversation_id']}", self._event_payload("call.orchestration", payload['orchestration']))

    async def _call_speaker_state(self, data):
        call_id = str(data.get("call_id"))
        payload = await self._speaker_state_call_sync(call_id, data)
        await self.channel_layer.group_send(f"conversation_{payload['conversation_id']}", self._event_payload("call.speaker_state", payload))
        if payload.get("orchestration"):
            await self.channel_layer.group_send(f"conversation_{payload['conversation_id']}", self._event_payload("call.orchestration", payload['orchestration']))

    async def _typing_event(self, event_name, data):
        conversation_id = str(data.get("conversation_id"))
        if not await self._is_participant(conversation_id):
            await self.send_json({"event": "error", "data": {"message": "Access denied."}})
            return
        if not await self._should_emit_typing_event(conversation_id, event_name):
            return
        if event_name == "typing.started":
            self.typing_conversations.add(conversation_id)
        else:
            self.typing_conversations.discard(conversation_id)
        await self._broadcast_typing_event(conversation_id, event_name)

    async def _broadcast_typing_event(self, conversation_id, event_name):
        expires_at = timezone.now() + timedelta(seconds=7) if event_name == "typing.started" else None
        await self.channel_layer.group_send(
            f"conversation_{conversation_id}",
            self._event_payload(
                event_name,
                {
                    "conversation_id": conversation_id,
                    "user_id": str(self.user.id),
                    "username": self.user.username,
                    "display_name": getattr(getattr(self.user, "profile", None), "display_name", "") or self.user.get_full_name() or self.user.username,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
            ),
        )

    async def _stop_all_typing(self):
        for conversation_id in list(getattr(self, "typing_conversations", set())):
            await self._clear_typing_state(conversation_id)
            await self._broadcast_typing_event(conversation_id, "typing.stopped")
        if hasattr(self, "typing_conversations"):
            self.typing_conversations.clear()

    async def _broadcast_conversation_update(self, conversation_id):
        payloads = await self._conversation_update_payloads_sync(conversation_id)
        for user_id, payload in payloads:
            await self.channel_layer.group_send(f"user_{user_id}", self._event_payload("conversation.updated", payload))

    async def _authenticate_from_token(self):
        try:
            query_string = self.scope.get("query_string", b"").decode()
            token = parse_qs(query_string).get("token", [None])[0]
            if not token:
                return None
            self.auth_token = token
            return await self._get_user_from_token(token)
        except (InvalidToken, Exception):
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

    @database_sync_to_async
    def _central_access_sync(self, action, *, idempotency_key="", metadata=None):
        request = SimpleNamespace(headers={"Authorization": f"Bearer {self.auth_token}"})
        return get_product_access_decision(
            request,
            action,
            metadata=metadata or {},
            record_usage=True,
            idempotency_key=idempotency_key,
        )

    @database_sync_to_async
    def _is_participant(self, conversation_id):
        return Conversation.objects.filter(id=conversation_id, participants__user=self.user, participants__left_at__isnull=True).exists()

    @database_sync_to_async
    def _send_message_sync(
        self,
        conversation_id,
        text,
        message_type,
        reply_to_id,
        client_temp_id,
        attachment_ids,
        is_voice_note=False,
        duration_seconds=None,
        waveform=None,
        entities=None,
        encryption=None,
        attachment_encryption=None,
    ):
        from apps.chat.api.serializers import MessageSerializer
        conversation = Conversation.objects.get(id=conversation_id)
        message = send_message(
            actor=self.user,
            conversation=conversation,
            text=text,
            message_type=message_type,
            reply_to_id=reply_to_id,
            client_temp_id=client_temp_id,
            attachment_ids=attachment_ids,
            entities=entities or [],
            encryption=encryption,
            attachment_encryption=attachment_encryption or [],
        )
        if is_voice_note:
            metadata = dict(message.metadata or {})
            metadata.update(
                {
                    "voice_note": True,
                    "duration_seconds": float(duration_seconds) if duration_seconds is not None else None,
                    "waveform": waveform or [],
                }
            )
            message.metadata = metadata
            message.save(update_fields=["metadata", "updated_at"])
        was_deduplicated = bool(getattr(message, "_deduplicated_send", False))
        message = (
            Message.objects.select_related("conversation", "sender", "reply_to", "forwarded_from", "transcript")
            .prefetch_related("attachments", "reactions", "deliveries", "edit_history")
            .get(id=message.id)
        )
        payload = MessageSerializer(message, context={"actor": self.user}).data
        payload["was_deduplicated"] = was_deduplicated
        return payload

    @database_sync_to_async
    def _edit_message_sync(self, message_id, text, entities=None, encryption=None):
        from apps.chat.api.serializers import MessageSerializer
        message = Message.objects.select_related("conversation", "sender").get(id=message_id)
        message = edit_message(self.user, message, text, entities=entities or [], encryption=encryption)
        return MessageSerializer(message, context={"actor": self.user}).data

    @database_sync_to_async
    def _delete_message_sync(self, message_id):
        from apps.chat.api.serializers import MessageSerializer
        message = Message.objects.select_related("conversation", "sender").get(id=message_id)
        message = soft_delete_message(self.user, message)
        return MessageSerializer(message, context={"actor": self.user}).data

    @database_sync_to_async
    def _mark_delivered_sync(self, conversation_id, message_id):
        conversation = Conversation.objects.get(id=conversation_id)
        participant = mark_conversation_delivered(self.user, conversation, message_id=message_id)
        return {
            "conversation_id": str(conversation.id),
            "user_id": str(self.user.id),
            "changed": bool(getattr(participant, "_delivery_changed", False)),
            "last_delivered_message_id": str(participant.last_delivered_message_id) if participant.last_delivered_message_id else None,
            "last_delivered_at": participant.last_delivered_at.isoformat() if participant.last_delivered_at else None,
        }

    @database_sync_to_async
    def _mark_read_sync(self, conversation_id, message_id):
        conversation = Conversation.objects.get(id=conversation_id)
        participant = mark_conversation_read(self.user, conversation, message_id=message_id)
        return {
            "conversation_id": str(conversation.id),
            "user_id": str(self.user.id),
            "changed": bool(getattr(participant, "_read_changed", False)),
            "last_delivered_message_id": str(participant.last_delivered_message_id) if participant.last_delivered_message_id else None,
            "last_delivered_at": participant.last_delivered_at.isoformat() if participant.last_delivered_at else None,
            "last_read_message_id": str(participant.last_read_message_id) if participant.last_read_message_id else None,
            "last_read_at": participant.last_read_at.isoformat() if participant.last_read_at else None,
        }

    @database_sync_to_async
    def _conversation_update_payloads_sync(self, conversation_id):
        from apps.chat.api.serializers import ConversationListSerializer

        participant_ids = list(
            Conversation.objects.filter(id=conversation_id, participants__left_at__isnull=True)
            .values_list("participants__user_id", flat=True)
            .distinct()
        )
        payloads = []
        for participant_id in participant_ids:
            participant = User.objects.filter(id=participant_id, is_active=True).first()
            if participant is None:
                continue
            conversation = user_conversations_qs(participant).filter(id=conversation_id).first()
            if conversation is None:
                continue
            payloads.append((str(participant_id), ConversationListSerializer(conversation, context={"actor": participant}).data))
        return payloads

    @database_sync_to_async
    def _react_sync(self, message_id, emoji):
        from apps.chat.api.serializers import MessageSerializer
        message = Message.objects.select_related("conversation").get(id=message_id)
        add_reaction(self.user, message, emoji)
        return MessageSerializer(message, context={"actor": self.user}).data

    @database_sync_to_async
    def _unreact_sync(self, message_id, emoji):
        from apps.chat.api.serializers import MessageSerializer
        message = Message.objects.select_related("conversation").get(id=message_id)
        remove_reaction(self.user, message, emoji)
        return MessageSerializer(message, context={"actor": self.user}).data

    @database_sync_to_async
    def _accept_call_sync(self, call_id):
        from apps.chat.api.serializers import CallSessionSerializer
        call = CallSession.objects.select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user").get(id=call_id)
        call = accept_call(self.user, call)
        payload = CallSessionSerializer(call).data
        timeline_message = getattr(call, "_timeline_message", None)
        if timeline_message is not None:
            from apps.chat.api.serializers import MessageSerializer
            payload["_timeline_message"] = MessageSerializer(timeline_message, context={"actor": self.user}).data
            payload["_timeline_message_created"] = bool(getattr(call, "_timeline_message_created", False))
        return payload

    @database_sync_to_async
    def _decline_call_sync(self, call_id, reason):
        from apps.chat.api.serializers import CallSessionSerializer
        call = CallSession.objects.select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user").get(id=call_id)
        call = decline_call(self.user, call, reason)
        payload = CallSessionSerializer(call).data
        timeline_message = getattr(call, "_timeline_message", None)
        if timeline_message is not None:
            from apps.chat.api.serializers import MessageSerializer
            payload["_timeline_message"] = MessageSerializer(timeline_message, context={"actor": self.user}).data
            payload["_timeline_message_created"] = bool(getattr(call, "_timeline_message_created", False))
        return payload

    @database_sync_to_async
    def _end_call_sync(self, call_id, reason):
        from apps.chat.api.serializers import CallSessionSerializer
        call = CallSession.objects.select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user").get(id=call_id)
        call = end_call(self.user, call, reason)
        payload = CallSessionSerializer(call).data
        timeline_message = getattr(call, "_timeline_message", None)
        if timeline_message is not None:
            from apps.chat.api.serializers import MessageSerializer
            payload["_timeline_message"] = MessageSerializer(timeline_message, context={"actor": self.user}).data
            payload["_timeline_message_created"] = bool(getattr(call, "_timeline_message_created", False))
        return payload

    @database_sync_to_async
    def _signal_call_sync(self, call_id, signal_type, payload):
        call = CallSession.objects.select_related("conversation").get(id=call_id)
        return send_call_signal(self.user, call, signal_type, payload)


    @database_sync_to_async
    def _heartbeat_call_sync(self, call_id, network_quality, metrics):
        call = CallSession.objects.select_related("conversation").prefetch_related("participants").get(id=call_id)
        return heartbeat_call_participant(self.user, call, network_quality=network_quality, metrics=metrics)

    @database_sync_to_async
    def _media_state_call_sync(self, call_id, data):
        call = CallSession.objects.select_related("conversation").prefetch_related("participants").get(id=call_id)
        diagnostics = dict(data.get("diagnostics") or {})
        for key in ("bitrate_kbps", "packet_loss_ratio", "latency_ms"):
            if data.get(key) is not None:
                diagnostics[key] = data.get(key)
        return update_call_media_state(
            self.user,
            call,
            audio_enabled=data.get("audio_enabled", data.get("microphone_enabled")),
            video_enabled=data.get("video_enabled", data.get("camera_enabled")),
            is_on_hold=data.get("is_on_hold"),
            reconnecting=data.get("reconnecting"),
            screen_share_enabled=data.get("screen_share_enabled", data.get("screen_sharing")),
            hand_raised=data.get("hand_raised"),
            connection_state=data.get("connection_state"),
            audio_route=data.get("audio_route"),
            preferred_video_quality=data.get("preferred_video_quality"),
            diagnostics=diagnostics or None,
        )

    @database_sync_to_async
    def _speaker_state_call_sync(self, call_id, data):
        call = CallSession.objects.select_related("conversation").prefetch_related("participants").get(id=call_id)
        return update_call_speaking_state(self.user, call, speaking_level=data.get("speaking_level", 0), is_speaking=data.get("is_speaking"))

    @database_sync_to_async
    def _set_presence(self):
        return set_presence(self.user, device_id=self.device_id)

    @database_sync_to_async
    def _presence_recipient_ids(self):
        return presence_recipient_ids(self.user)

    @database_sync_to_async
    def _public_presence_snapshot(self, snapshot):
        return get_public_presence_snapshot(self.user, snapshot)

    async def _broadcast_presence_update(self, snapshot):
        payload = {"user_id": str(self.user.id), **(await self._public_presence_snapshot(snapshot))}
        event = self._event_payload("presence.updated", payload)
        for user_id in await self._presence_recipient_ids():
            await self.channel_layer.group_send(f"user_{user_id}", event)

    @database_sync_to_async
    def _should_emit_typing_event(self, conversation_id, event_name):
        key = f"chat:typing:{conversation_id}:{self.user.id}:{event_name}"
        now_ts = timezone.now().timestamp()
        if event_name == "typing.started":
            previous = cache.get(key)
            if previous and (now_ts - float(previous)) < 1.5:
                return False
            cache.set(key, now_ts, timeout=6)
            return True
        cache.delete(f"chat:typing:{conversation_id}:{self.user.id}:typing.started")
        return True

    @database_sync_to_async
    def _clear_typing_state(self, conversation_id):
        cache.delete(f"chat:typing:{conversation_id}:{self.user.id}:typing.started")

    @database_sync_to_async
    def _clear_presence(self):
        return clear_presence(self.user, device_id=self.device_id)

    async def _broadcast_call_timeline(self, call_payload):
        timeline = call_payload.get("_timeline_message")
        if not timeline:
            return
        event_name = "message.created" if call_payload.get("_timeline_message_created") else "message.updated"
        await self.channel_layer.group_send(
            f"conversation_{call_payload['conversation_id']}",
            self._event_payload(event_name, timeline),
        )
