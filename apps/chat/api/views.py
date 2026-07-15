from pathlib import Path
import mimetypes
from django.conf import settings
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.utils.cache import patch_vary_headers
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import generics, permissions, serializers, status, views
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework.throttling import ScopedRateThrottle

from apps.chat.models import (
    CallSession,
    ChatAuditLog,
    Conversation,
    ConversationDraft,
    ConversationInviteLink,
    Message,
    MessageAttachment,
    MessageEditHistory,
    MessageReport,
    PendingUpload,
    UserBlock,
    UserDevice,
    UserE2EEDeviceKey,
)
from apps.chat.central_access import require_product_access
from apps.chat.selectors import (
    conversation_media_qs,
    conversation_messages_qs,
    request_user_devices,
    searchable_conversations_qs,
    searchable_messages_qs,
    user_blocks_qs,
    user_conversations_qs,
)
from apps.chat.services import (
    accept_call,
    add_group_participants,
    add_reaction,
    block_user,
    clear_presence,
    cleanup_conversation_if_unretained,
    CallParticipantBusy,
    create_direct_conversation,
    create_group_conversation,
    create_media_access_payload,
    consume_view_once_attachment,
    decline_call,
    delete_conversation,
    end_call,
    create_conversation_invite_link,
    deactivate_device,
    dismiss_message_report,
    edit_message,
    expire_stale_call_participants,
    conversation_has_e2ee_enabled_participants,
    forward_message,
    get_call_diagnostics,
    get_call_orchestration,
    get_calling_config,
    get_conversation_e2ee_keys,
    get_active_call_for_conversation,
    get_turn_credentials,
    get_conversation_notification_setting,
    ensure_participant,
    get_notification_preference,
    group_route_name_is_available,
    leave_conversation,
    list_message_reports,
    list_recent_calls_for_user,
    log_chat_event,
    mark_conversation_delivered,
    mark_conversation_read,
    mark_message_failed,
    heartbeat_call_participant,
    list_e2ee_device_keys_for_actor,
    register_device,
    register_e2ee_device_key,
    revoke_e2ee_device_key,
    retry_message,
    revoke_conversation_invite_link,
    remove_group_participant,
    mute_group_participant,
    ban_group_participant,
    unban_group_participant,
    send_call_signal,
    submit_call_quality_report,
    remove_reaction,
    report_message,
    resolve_message_report,
    restore_message_by_staff,
    join_group_via_invite,
    scan_upload_file,
    secure_attachment_queryset_for_user,
    secure_pending_upload_queryset_for_user,
    send_message,
    set_presence,
    soft_delete_message,
    start_call,
    transfer_group_ownership,
    update_call_media_state,
    update_call_speaking_state,
    update_conversation_notification_setting,
    upsert_message_transcript,
    unblock_user,
    update_participant_role,
    validate_media_access_token,
    make_realtime_event,
    make_realtime_safe,
    get_public_presence_snapshot,
    presence_recipient_ids,
    dispatch_pending_upload_scan,
)
from apps.chat.tasks import integration_health_snapshot
from .serializers import (
    _media_kind_from_mime,
    CallActionSerializer,
    CallDiagnosticsSerializer,
    CallHeartbeatSerializer,
    CallMediaStateSerializer,
    CallOrchestrationSerializer,
    CallQualityReportSerializer,
    CallSessionSerializer,
    CallSignalSerializer,
    CallSpeakerStateSerializer,
    CallStartSerializer,
    CallingConfigSerializer,
    ChatCapabilitiesSerializer,
    ChatAuditLogSerializer,
    ConversationDraftSerializer,
    ConversationInviteCreateSerializer,
    ConversationInviteJoinSerializer,
    ConversationInviteLinkSerializer,
    ConversationMediaSerializer,
    ConversationNotificationSettingSerializer,
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    DeliverySerializer,
    DeviceDeactivateSerializer,
    DeviceSerializer,
    DeviceUpsertSerializer,
    E2EEDeviceKeySerializer,
    E2EEDeviceKeyUpsertSerializer,
    MediaTokenSerializer,
    MessageCreateSerializer,
    MessageFailureSerializer,
    MessageForwardSerializer,
    MessageEditHistorySerializer,
    MessageReportSerializer,
    MessageRestoreSerializer,
    MessageSerializer,
    MessageUpdateSerializer,
    MessageTranscriptUpsertSerializer,
    ModerationActionSerializer,
    ModerationDismissSerializer,
    ModerationResolveSerializer,
    NotificationPreferenceSerializer,
    OwnershipTransferSerializer,
    RecentCallQuerySerializer,
    ParticipantManageSerializer,
    ParticipantRoleUpdateSerializer,
    GroupParticipantMuteSerializer,
    GroupParticipantBanSerializer,
    IntegrationHealthSerializer,
    PendingUploadSerializer,
    ReactionSerializer,
    SyncQuerySerializer,
    TurnCredentialsSerializer,
    UploadCreateSerializer,
    UserBlockSerializer,
)

User = get_user_model()


def _schema_generation(view):
    return getattr(view, "swagger_fake_view", False)




def _can_preview_inline_mime(mime_type):
    mime = (mime_type or "").lower()
    return mime.startswith("image/") or mime.startswith("audio/") or mime.startswith("video/") or mime == "application/pdf"


def _parse_single_byte_range(range_header, size):
    if not range_header or not str(range_header).startswith("bytes=") or "," in str(range_header):
        return None
    value = str(range_header)[6:].strip()
    if "-" not in value:
        return None
    start_text, end_text = value.split("-", 1)
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                return None
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
    except (TypeError, ValueError):
        return None
    if start < 0 or start >= size or end < start:
        return "unsatisfiable"
    return start, min(end, size - 1)


def _iter_file_range(file_obj, *, start, length, chunk_size=64 * 1024):
    remaining = length
    try:
        file_obj.seek(start)
        while remaining > 0:
            chunk = file_obj.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file_obj.close()


def _build_media_file_response(file_field, *, filename, mime_type="", as_attachment=True, request=None):
    size = int(getattr(file_field, "size", 0) or 0)
    range_header = request.headers.get("Range", "") if request is not None and not as_attachment else ""
    byte_range = _parse_single_byte_range(range_header, size) if size else None

    if byte_range == "unsatisfiable":
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        response["Accept-Ranges"] = "bytes"
        return response

    if isinstance(byte_range, tuple):
        start, end = byte_range
        try:
            file_obj = file_field.open("rb")
        except FileNotFoundError as exc:
            raise Http404("Media file is no longer available.") from exc
        length = end - start + 1
        response = StreamingHttpResponse(
            _iter_file_range(file_obj, start=start, length=length),
            status=206,
            content_type=mime_type or "application/octet-stream",
        )
        response["Content-Length"] = str(length)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        try:
            response = FileResponse(file_field.open("rb"), as_attachment=as_attachment, filename=filename)
        except FileNotFoundError as exc:
            raise Http404("Media file is no longer available.") from exc
        if size:
            response["Content-Length"] = str(size)

    if mime_type:
        response["Content-Type"] = mime_type
    disposition = content_disposition_header(as_attachment=as_attachment, filename=filename)
    if disposition:
        response["Content-Disposition"] = disposition
    if not as_attachment:
        response["Accept-Ranges"] = "bytes"
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, max-age=86400, immutable"
    return response


def _build_thumbnail_file_response(file_field, *, filename, request=None):
    guessed_type, _ = mimetypes.guess_type(filename or "")
    response = _build_media_file_response(
        file_field,
        filename=filename,
        mime_type=guessed_type or "image/jpeg",
        as_attachment=False,
        request=request,
    )
    # Thumbnails are immutable for an attachment id and contain no shared-user
    # data beyond what the authenticated request already authorized.
    response["Cache-Control"] = "private, max-age=86400, immutable"
    return response

def _broadcast_to_conversation(conversation_id, event, data):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    payload = make_realtime_event(event, data)
    async_to_sync(channel_layer.group_send)(f"conversation_{conversation_id}", payload)


def _broadcast_to_user(user_id, event, data):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    payload = make_realtime_event(event, data)
    async_to_sync(channel_layer.group_send)(f"user_{user_id}", payload)


def _broadcast_presence_update(user, snapshot):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    payload = {"user_id": str(user.id), **get_public_presence_snapshot(user, snapshot)}
    event = make_realtime_event("presence.updated", payload)
    for recipient_id in presence_recipient_ids(user):
        async_to_sync(channel_layer.group_send)(f"user_{recipient_id}", event)


def _broadcast_pair_presence_refresh(user_a, user_b):
    """Refresh both clients after a block relationship changes.

    Presence privacy is recipient-specific for blocked accounts, so this sends a
    targeted hidden snapshot on block and the normal public snapshot on unblock.
    """
    blocked = UserBlock.objects.filter(
        Q(blocker=user_a, blocked=user_b) | Q(blocker=user_b, blocked=user_a)
    ).exists()
    hidden = {
        "is_online": False,
        "active_devices": 0,
        "last_seen_at": None,
        "presence_label": "offline",
        "visibility": "hidden",
    }
    for subject, recipient in ((user_a, user_b), (user_b, user_a)):
        snapshot = hidden if blocked else get_public_presence_snapshot(subject)
        _broadcast_to_user(
            recipient.id,
            "presence.updated",
            {"user_id": str(subject.id), **snapshot},
        )


def _broadcast_e2ee_key_change_for_user(user):
    conversations = (
        Conversation.objects.filter(
            participants__user=user,
            participants__left_at__isnull=True,
            participants__banned_at__isnull=True,
            is_active=True,
        )
        .values("id", "e2ee_key_version", "e2ee_rekey_required", "e2ee_last_security_event_at")
        .distinct()
    )
    for conversation in conversations:
        _broadcast_to_conversation(
            str(conversation["id"]),
            "e2ee.keys.updated",
            {
                "conversation_id": str(conversation["id"]),
                "user_id": str(user.id),
                "key_version": conversation["e2ee_key_version"],
                "rekey_required": conversation["e2ee_rekey_required"],
                "changed_at": conversation["e2ee_last_security_event_at"],
            },
        )


def _broadcast_conversation_update(conversation_id, *, request=None):
    participant_ids = list(
        Conversation.objects.filter(id=conversation_id, participants__left_at__isnull=True)
        .values_list("participants__user_id", flat=True)
        .distinct()
    )
    users_by_id = User.objects.in_bulk(participant_ids)
    for participant_id in participant_ids:
        user = users_by_id.get(participant_id)
        if user is None:
            continue
        conversation = user_conversations_qs(user, lightweight=True).filter(id=conversation_id).first()
        if not conversation:
            continue
        payload = ConversationListSerializer(conversation, context={"request": request}).data
        _broadcast_to_user(str(participant_id), "conversation.updated", payload)


def _broadcast_call_timeline_message(call, *, request=None):
    timeline_message = getattr(call, "_timeline_message", None)
    if timeline_message is None:
        return
    payload = MessageSerializer(timeline_message, context={"request": request}).data
    event_name = "message.created" if getattr(call, "_timeline_message_created", False) else "message.updated"
    _broadcast_to_conversation(str(call.conversation_id), event_name, payload)


class ConversationListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if _schema_generation(self):
            return Conversation.objects.none()
        return user_conversations_qs(self.request.user, lightweight=True)

    def get_serializer_class(self):
        return ConversationCreateSerializer if self.request.method == "POST" else ConversationListSerializer

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response["Cache-Control"] = "private, max-age=15, stale-while-revalidate=300"
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if payload["type"] == Conversation.ConversationType.DIRECT:
            conversation = create_direct_conversation(request.user, payload["participant_ids"][0])
            status_code = status.HTTP_200_OK
        else:
            require_product_access(
                request,
                "create_room",
                record_usage=True,
                idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
                metadata={"participant_count": len(payload["participant_ids"])},
            )
            conversation = create_group_conversation(request.user, payload.get("title", ""), payload["participant_ids"], payload.get("slug", ""))
            status_code = status.HTTP_201_CREATED
        output = ConversationDetailSerializer(conversation, context={"request": request}).data
        return Response(output, status=status_code)


class ConversationSearchView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationListSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return Conversation.objects.none()
        query = " ".join((self.request.query_params.get("q", "") or "").split())[:120]
        if not query:
            return user_conversations_qs(self.request.user, lightweight=True).none()
        return searchable_conversations_qs(self.request.user, query)


class GroupRouteNameAvailabilityView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        available, normalized, message = group_route_name_is_available(request.query_params.get("name", ""))
        return Response({"available": available, "normalized": normalized, "message": message})


class ConversationDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationDetailSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return Conversation.objects.none()
        return user_conversations_qs(self.request.user)

    def perform_destroy(self, instance):
        conversation_id = str(instance.id)
        participant_ids = list(instance.participants.values_list("user_id", flat=True))
        delete_conversation(self.request.user, instance)
        payload = {"conversation_id": conversation_id}
        for participant_id in participant_ids:
            _broadcast_to_user(str(participant_id), "conversation.deleted", payload)


class DirectConversationByUsernameView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationDetailSerializer

    def get_object(self):
        username = str(self.kwargs.get("username") or "").lstrip("@").strip()
        if username.lower() == str(self.request.user.username or "").lower():
            raise Http404
        queryset = user_conversations_qs(self.request.user).filter(
            type=Conversation.ConversationType.DIRECT,
            participants__left_at__isnull=True,
            participants__user__username__iexact=username,
        ).distinct()
        return get_object_or_404(queryset)


class ConversationByRouteView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationDetailSerializer

    def get_object(self):
        route_key = str(self.kwargs.get("route_key") or "").lstrip("@").strip()
        if not route_key:
            raise Http404
        queryset = user_conversations_qs(self.request.user)
        group = queryset.filter(type=Conversation.ConversationType.GROUP, slug__iexact=route_key).first()
        if group:
            return group
        if route_key.lower() == str(self.request.user.username or "").lower():
            raise Http404
        direct = queryset.filter(
            type=Conversation.ConversationType.DIRECT,
            participants__left_at__isnull=True,
            participants__user__username__iexact=route_key,
        ).distinct()
        return get_object_or_404(direct)


class ConversationDraftView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _conversation(self, request, conversation_id):
        return get_object_or_404(user_conversations_qs(request.user), id=conversation_id)

    def _empty_payload(self, *, request, conversation):
        draft = ConversationDraft(conversation=conversation, user=request.user, text="", metadata={})
        return ConversationDraftSerializer(draft, context={"request": request, "conversation": conversation}).data

    def get(self, request, conversation_id):
        conversation = self._conversation(request, conversation_id)
        if conversation_has_e2ee_enabled_participants(conversation):
            return Response(self._empty_payload(request=request, conversation=conversation))
        draft = ConversationDraft.objects.filter(conversation=conversation, user=request.user).select_related("reply_to").first()
        if draft is None:
            return Response(self._empty_payload(request=request, conversation=conversation))
        return Response(ConversationDraftSerializer(draft, context={"request": request, "conversation": conversation}).data)

    def patch(self, request, conversation_id):
        conversation = self._conversation(request, conversation_id)
        draft = ConversationDraft.objects.filter(conversation=conversation, user=request.user).select_related("reply_to").first()
        serializer = ConversationDraftSerializer(
            draft,
            data=request.data,
            partial=True,
            context={"request": request, "conversation": conversation},
        )
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        text = payload.get("text", draft.text if draft else "")
        reply_to = payload.get("reply_to", getattr(draft, "reply_to", None))
        metadata = payload.get("metadata", draft.metadata if draft else {}) or {}
        if conversation_has_e2ee_enabled_participants(conversation):
            if draft is not None:
                draft.delete()
            if not text and reply_to is None and not metadata:
                return Response(self._empty_payload(request=request, conversation=conversation))
            return Response(
                {
                    "detail": "Server-side drafts are disabled for end-to-end encrypted conversations. Keep drafts on this device.",
                    "code": "e2ee_local_drafts_only",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not text and reply_to is None and not metadata:
            if draft is not None:
                draft.delete()
            return Response(self._empty_payload(request=request, conversation=conversation))
        if draft is None:
            draft = ConversationDraft.objects.create(
                conversation=conversation,
                user=request.user,
                text=text,
                reply_to=reply_to,
                metadata=metadata,
            )
        else:
            draft.text = text
            draft.reply_to = reply_to
            draft.metadata = metadata
            draft.save(update_fields=["text", "reply_to", "metadata", "updated_at"])
        return Response(ConversationDraftSerializer(draft, context={"request": request, "conversation": conversation}).data)

    def delete(self, request, conversation_id):
        conversation = self._conversation(request, conversation_id)
        ConversationDraft.objects.filter(conversation=conversation, user=request.user).delete()
        return Response(self._empty_payload(request=request, conversation=conversation))


class E2EEDeviceKeyListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = E2EEDeviceKeySerializer

    def get_queryset(self):
        if _schema_generation(self):
            return UserE2EEDeviceKey.objects.none()
        return list_e2ee_device_keys_for_actor(self.request.user)

    def get_serializer_class(self):
        return E2EEDeviceKeyUpsertSerializer if self.request.method == "POST" else E2EEDeviceKeySerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = register_e2ee_device_key(request.user, **serializer.validated_data)
        security_changed = bool(getattr(key, "_security_changed", False))
        if security_changed:
            _broadcast_e2ee_key_change_for_user(request.user)
        output = dict(E2EEDeviceKeySerializer(key, context={"request": request}).data)
        output["security_changed"] = security_changed
        return Response(output, status=status.HTTP_201_CREATED)


class E2EEDeviceKeyRevokeView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, key_id):
        key = revoke_e2ee_device_key(request.user, key_uuid=key_id)
        security_changed = bool(getattr(key, "_security_changed", False))
        if security_changed:
            _broadcast_e2ee_key_change_for_user(request.user)
        output = dict(E2EEDeviceKeySerializer(key, context={"request": request}).data)
        output["security_changed"] = security_changed
        return Response(output)


class ConversationE2EEKeysView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, conversation_id):
        conversation = get_object_or_404(
            user_conversations_qs(request.user).prefetch_related("participants"),
            id=conversation_id,
        )
        material = get_conversation_e2ee_keys(request.user, conversation)
        payload = {
            "conversation_id": str(conversation.id),
            "key_version": material["key_version"],
            "rekey_required": material["rekey_required"],
            "last_key_rotation_at": material["last_key_rotation_at"],
            "last_security_event_at": material["last_security_event_at"],
            "participants": {
                user_id: E2EEDeviceKeySerializer(keys, many=True, context={"request": request}).data
                for user_id, keys in material["participants"].items()
            },
        }
        return Response(payload)


class MessageListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer
    throttle_classes = [ScopedRateThrottle]

    def get_throttles(self):
        if self.request.method == "POST":
            self.throttle_scope = "message_send"
        return super().get_throttles()

    def get_queryset(self):
        if _schema_generation(self):
            return Message.objects.none()
        return conversation_messages_qs(self.request.user, self.kwargs["conversation_id"])

    def list(self, request, *args, **kwargs):
        conversation = get_object_or_404(user_conversations_qs(request.user), id=self.kwargs["conversation_id"])
        participant = mark_conversation_delivered(request.user, conversation)
        if getattr(participant, "_delivery_changed", False) and participant.last_delivered_message_id:
            payload = {
                "conversation_id": str(conversation.id),
                "user_id": str(request.user.id),
                "last_delivered_message_id": str(participant.last_delivered_message_id),
                "last_delivered_at": participant.last_delivered_at.isoformat() if participant.last_delivered_at else None,
            }
            _broadcast_to_conversation(str(conversation.id), "message.delivered", payload)
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        conversation = get_object_or_404(user_conversations_qs(request.user), id=self.kwargs["conversation_id"])
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        require_product_access(
            request,
            "send_message",
            record_usage=True,
            idempotency_key=str(serializer.validated_data.get("client_temp_id") or request.headers.get("Idempotency-Key") or ""),
            metadata={
                "conversation_id": str(conversation.id),
                "message_type": serializer.validated_data.get("type", Message.MessageType.TEXT),
                "attachment_count": len(serializer.validated_data.get("attachment_ids", [])),
            },
        )
        transcript_payload = None
        if any(key in serializer.validated_data for key in ["transcript_text", "transcript_language_code", "transcript_confidence"]):
            transcript_payload = {
                "text": serializer.validated_data.get("transcript_text", ""),
                "language_code": serializer.validated_data.get("transcript_language_code", ""),
                "confidence": serializer.validated_data.get("transcript_confidence"),
            }
        message = send_message(
            actor=request.user,
            conversation=conversation,
            text=serializer.validated_data.get("text", ""),
            message_type=serializer.validated_data.get("type", Message.MessageType.TEXT),
            reply_to_id=serializer.validated_data.get("reply_to_id"),
            client_temp_id=serializer.validated_data.get("client_temp_id", ""),
            attachment_ids=serializer.validated_data.get("attachment_ids", []),
            attachment_encryption=serializer.validated_data.get("attachment_encryption", []),
            view_once_attachment_ids=serializer.validated_data.get("view_once_attachment_ids", []),
            entities=serializer.validated_data.get("entities", []),
            transcript_payload=transcript_payload,
            encryption=serializer.validated_data.get("encryption"),
        )
        if serializer.validated_data.get("is_voice_note"):
            metadata = dict(message.metadata or {})
            metadata.update({
                "voice_note": True,
                "duration_seconds": float(serializer.validated_data.get("duration_seconds")) if serializer.validated_data.get("duration_seconds") is not None else None,
                "waveform": serializer.validated_data.get("waveform", []),
            })
            message.metadata = metadata
            message.save(update_fields=["metadata", "updated_at"])
        if transcript_payload:
            transcript = upsert_message_transcript(
                actor=request.user,
                message=message,
                text=transcript_payload.get("text", ""),
                language_code=transcript_payload.get("language_code", ""),
                confidence=transcript_payload.get("confidence"),
            )
            message.transcript = transcript
        output = MessageSerializer(message, context={"request": request}).data
        output["was_deduplicated"] = bool(getattr(message, "_deduplicated_send", False))
        if not output["was_deduplicated"]:
            _broadcast_to_conversation(str(conversation.id), "message.created", output)
            locked_reply_target = getattr(message, "_edit_locked_reply_target", None)
            if locked_reply_target is not None:
                reply_target_output = MessageSerializer(locked_reply_target, context={"request": request}).data
                _broadcast_to_conversation(str(conversation.id), "message.updated", reply_target_output)
            _broadcast_conversation_update(str(conversation.id), request=request)
        return Response(output, status=status.HTTP_201_CREATED if not output["was_deduplicated"] else status.HTTP_200_OK)


class MessageSearchView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return Message.objects.none()
        query = " ".join((self.request.query_params.get("q", "") or "").split())[:120]
        if not query:
            return searchable_messages_qs(self.request.user, "__empty__").none()
        return searchable_messages_qs(self.request.user, query)


class MessageDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return Message.objects.none()
        return Message.objects.filter(conversation__participants__user=self.request.user, conversation__participants__left_at__isnull=True).distinct()


class MessageContextView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        responses=inline_serializer(
            name="MessageContextResponse",
            fields={
                "target_id": serializers.CharField(),
                "results": MessageSerializer(many=True),
            },
        )
    )
    def get(self, request, message_id):
        target = get_object_or_404(
            Message.objects.select_related("conversation", "sender", "sender__profile").filter(
                conversation__participants__user=request.user,
                conversation__participants__left_at__isnull=True,
            ).distinct(),
            id=message_id,
        )
        queryset = conversation_messages_qs(request.user, target.conversation_id)
        older = list(
            queryset.filter(
                Q(created_at__lt=target.created_at)
                | Q(created_at=target.created_at, id__lt=target.id)
            ).order_by("-created_at", "-id")[:15]
        )
        newer = list(
            queryset.filter(
                Q(created_at__gt=target.created_at)
                | Q(created_at=target.created_at, id__gt=target.id)
            ).order_by("created_at", "id")[:15]
        )
        messages = [*reversed(older), target, *newer]
        return Response({
            "target_id": str(target.id),
            "results": MessageSerializer(messages, many=True, context={"request": request}).data,
        })


class MessageEditHistoryView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageEditHistorySerializer
    pagination_class = None

    def get_queryset(self):
        if _schema_generation(self):
            return MessageEditHistory.objects.none()
        message = get_object_or_404(
            Message.objects.filter(
                conversation__participants__user=self.request.user,
                conversation__participants__left_at__isnull=True,
            ).distinct(),
            id=self.kwargs["message_id"],
        )
        return message.edit_history.select_related("edited_by", "edited_by__profile").order_by("-created_at")


class MessageUpdateDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, request, message_id):
        return get_object_or_404(Message.objects.select_related("conversation", "sender"), id=message_id, conversation__participants__user=request.user)

    def patch(self, request, message_id):
        message = self.get_object(request, message_id)
        serializer = MessageUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = edit_message(
            request.user,
            message,
            serializer.validated_data.get("text", ""),
            serializer.validated_data.get("entities", []),
            encryption=serializer.validated_data.get("encryption"),
        )
        output = MessageSerializer(message, context={"request": request}).data
        _broadcast_to_conversation(str(message.conversation_id), "message.updated", output)
        return Response(output)

    def delete(self, request, message_id):
        message = self.get_object(request, message_id)
        message = soft_delete_message(request.user, message)
        output = MessageSerializer(message, context={"request": request}).data
        _broadcast_to_conversation(str(message.conversation_id), "message.deleted", output)
        return Response(output, status=status.HTTP_200_OK)


class MessageForwardView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, message_id):
        source_message = get_object_or_404(Message.objects.select_related("conversation", "sender", "forwarded_from"), id=message_id)
        serializer = MessageForwardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_conversation = get_object_or_404(user_conversations_qs(request.user), id=serializer.validated_data["conversation_id"])
        message = forward_message(request.user, source_message, target_conversation, serializer.validated_data.get("client_temp_id", ""))
        output = MessageSerializer(message, context={"request": request}).data
        _broadcast_to_conversation(str(target_conversation.id), "message.created", output)
        locked_source = getattr(message, "_edit_locked_source", None)
        if locked_source is not None:
            source_output = MessageSerializer(locked_source, context={"request": request}).data
            _broadcast_to_conversation(str(locked_source.conversation_id), "message.updated", source_output)
        _broadcast_conversation_update(str(target_conversation.id), request=request)
        return Response(output, status=status.HTTP_201_CREATED)


class MessageTranscriptView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, message_id):
        message = get_object_or_404(Message.objects.select_related("conversation", "sender"), id=message_id, conversation__participants__user=request.user)
        serializer = MessageTranscriptUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transcript = upsert_message_transcript(
            actor=request.user,
            message=message,
            text=serializer.validated_data.get("text", ""),
            language_code=serializer.validated_data.get("language_code", ""),
            confidence=serializer.validated_data.get("confidence"),
            status=serializer.validated_data.get("status"),
            source=serializer.validated_data.get("source"),
        )
        refreshed = Message.objects.select_related("conversation", "sender", "transcript").prefetch_related("attachments", "reactions", "deliveries", "edit_history").get(id=message.id)
        output = MessageSerializer(refreshed, context={"request": request}).data
        _broadcast_to_conversation(str(message.conversation_id), "message.transcript", output)
        return Response(output)


class MessageReportView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "report_write"

    def post(self, request, message_id):
        message = get_object_or_404(Message.objects.select_related("conversation"), id=message_id)
        serializer = MessageReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = report_message(actor=request.user, message=message, reason=serializer.validated_data["reason"], details=serializer.validated_data.get("details", ""))
        return Response(MessageReportSerializer(report, context={"request": request}).data, status=status.HTTP_201_CREATED)


class MarkConversationDeliveredView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        serializer = DeliverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant = mark_conversation_delivered(actor=request.user, conversation=conversation, message_id=serializer.validated_data.get("message_id"))
        payload = {
            "conversation_id": str(conversation.id),
            "user_id": str(request.user.id),
            "last_delivered_message_id": str(participant.last_delivered_message_id) if participant.last_delivered_message_id else None,
            "last_delivered_at": participant.last_delivered_at.isoformat() if participant.last_delivered_at else None,
        }
        if getattr(participant, "_delivery_changed", False):
            _broadcast_to_conversation(str(conversation.id), "message.delivered", payload)
        return Response(payload)


class MarkConversationReadView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        participant = mark_conversation_read(actor=request.user, conversation=conversation, message_id=request.data.get("message_id"))
        delivered_payload = {
            "conversation_id": str(conversation.id),
            "user_id": str(request.user.id),
            "last_delivered_message_id": str(participant.last_delivered_message_id) if participant.last_delivered_message_id else None,
            "last_delivered_at": participant.last_delivered_at.isoformat() if participant.last_delivered_at else None,
        }
        read_payload = {
            "conversation_id": str(conversation.id),
            "user_id": str(request.user.id),
            "last_read_message_id": str(participant.last_read_message_id) if participant.last_read_message_id else None,
            "last_read_at": participant.last_read_at.isoformat() if participant.last_read_at else None,
        }
        if getattr(participant, "_delivery_changed", False):
            _broadcast_to_conversation(str(conversation.id), "message.delivered", delivered_payload)
        if getattr(participant, "_read_changed", False):
            _broadcast_to_conversation(str(conversation.id), "message.read", {**delivered_payload, **read_payload})
        return Response({**delivered_payload, **read_payload})


class ConversationToggleStateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    field_name = None

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        participant = ensure_participant(conversation, request.user)
        current_value = getattr(participant, self.field_name)
        setattr(participant, self.field_name, not current_value)
        participant.save(update_fields=[self.field_name, "updated_at"])
        if self.field_name == "is_archived" and getattr(participant, self.field_name):
            cleanup_conversation_if_unretained(conversation)
        return Response({"conversation_id": str(conversation.id), self.field_name: getattr(participant, self.field_name)})


class MuteConversationView(ConversationToggleStateView):
    field_name = "is_muted"


class ArchiveConversationView(ConversationToggleStateView):
    field_name = "is_archived"


class PinConversationView(ConversationToggleStateView):
    field_name = "is_pinned"


class GroupParticipantManageView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        serializer = ParticipantManageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = add_group_participants(request.user, conversation, serializer.validated_data["participant_ids"])
        output = ConversationDetailSerializer(conversation, context={"request": request}).data
        _broadcast_to_conversation(str(conversation.id), "conversation.updated", output)
        _broadcast_conversation_update(str(conversation.id), request=request)
        return Response(output)


class GroupParticipantRemoveView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, conversation_id, user_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        removed = remove_group_participant(request.user, conversation, user_id)
        payload = {"conversation_id": str(conversation.id), "removed_user_id": str(removed.user_id)}
        _broadcast_to_conversation(str(conversation.id), "conversation.participant_removed", payload)
        return Response(payload)


class GroupParticipantRoleUpdateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, conversation_id, user_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        serializer = ParticipantRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant = update_participant_role(request.user, conversation, user_id, serializer.validated_data["role"])
        payload = {"conversation_id": str(conversation.id), "user_id": str(participant.user_id), "role": participant.role}
        _broadcast_to_conversation(str(conversation.id), "conversation.participant_role_updated", payload)
        return Response(payload)


class GroupParticipantMuteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id, user_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        serializer = GroupParticipantMuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant = mute_group_participant(request.user, conversation, user_id, serializer.validated_data["minutes"])
        payload = {
            "conversation_id": str(conversation.id),
            "user_id": str(participant.user_id),
            "moderation_muted_until": participant.moderation_muted_until.isoformat() if participant.moderation_muted_until else None,
        }
        _broadcast_to_conversation(str(conversation.id), "conversation.participant_muted", payload)
        return Response(payload)


class GroupParticipantBanView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id, user_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        serializer = GroupParticipantBanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant = ban_group_participant(request.user, conversation, user_id, serializer.validated_data.get("reason", ""))
        payload = {
            "conversation_id": str(conversation.id),
            "user_id": str(participant.user_id),
            "banned_at": participant.banned_at.isoformat() if participant.banned_at else None,
            "ban_reason": participant.ban_reason,
        }
        _broadcast_to_conversation(str(conversation.id), "conversation.participant_banned", payload)
        return Response(payload)

    def delete(self, request, conversation_id, user_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        participant = unban_group_participant(request.user, conversation, user_id)
        payload = {"conversation_id": str(conversation.id), "user_id": str(participant.user_id), "unbanned": True}
        _broadcast_to_conversation(str(conversation.id), "conversation.participant_unbanned", payload)
        return Response(payload)


class GroupOwnershipTransferView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        serializer = OwnershipTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = transfer_group_ownership(request.user, conversation, serializer.validated_data["target_user_id"])
        payload = {"conversation_id": str(conversation.id), "owner_user_id": str(target.user_id)}
        _broadcast_to_conversation(str(conversation.id), "conversation.ownership_transferred", payload)
        return Response(payload)


class LeaveConversationView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        participant = leave_conversation(request.user, conversation)
        payload = {"conversation_id": str(conversation.id), "user_id": str(participant.user_id), "left_at": participant.left_at.isoformat()}
        _broadcast_to_conversation(str(conversation.id), "conversation.participant_left", payload)
        return Response(payload)


ANDROID_VOICE_MIME_ALIASES = {
    "audio/mp4": ("audio/mp4", "m4a"),
    "audio/m4a": ("audio/mp4", "m4a"),
    "audio/x-m4a": ("audio/mp4", "m4a"),
    "audio/aac": ("audio/aac", "aac"),
    "audio/3gpp": ("audio/3gpp", "3gp"),
    "audio/amr": ("audio/amr", "amr"),
    "audio/ogg": ("audio/ogg", "ogg"),
    "audio/opus": ("audio/ogg", "ogg"),
    "audio/webm": ("audio/webm", "webm"),
}

VOICE_EXTENSIONS = {"m4a", "aac", "3gp", "amr", "ogg", "opus", "webm"}
VIDEO_EXTENSIONS = {"mp4", "mov", "m4v", "webm", "3gp", "3gpp"}


def _normalize_upload_media(file_obj, supplied_original_name="", supplied_mime_type=""):
    original_name = (supplied_original_name or getattr(file_obj, "name", "") or "upload").strip() or "upload"
    raw_mime = (
        supplied_mime_type
        or getattr(file_obj, "content_type", "")
        or "application/octet-stream"
    ).lower().strip()
    extension = Path(original_name).suffix.lower().lstrip(".")

    normalized_mime = raw_mime
    normalized_ext = extension

    if extension in {"mp4", "mov", "m4v"} and raw_mime.startswith("audio/"):
        normalized_mime = "video/mp4" if extension in {"mp4", "m4v"} else "video/quicktime"
    elif extension in VIDEO_EXTENSIONS and not raw_mime.startswith(("audio/", "video/")):
        normalized_mime = "video/mp4" if extension in {"mp4", "m4v"} else "video/quicktime"
    elif raw_mime in ANDROID_VOICE_MIME_ALIASES:
        normalized_mime, fallback_ext = ANDROID_VOICE_MIME_ALIASES[raw_mime]
        normalized_ext = extension or fallback_ext
    elif extension in VOICE_EXTENSIONS and not raw_mime.startswith("audio/"):
        normalized_mime = "audio/ogg" if extension in {
            "ogg", "opus"} else "audio/mp4"

    if not normalized_ext:
        if normalized_mime == "audio/ogg":
            normalized_ext = "ogg"
        elif normalized_mime == "audio/webm":
            normalized_ext = "webm"
        elif normalized_mime == "audio/aac":
            normalized_ext = "aac"
        elif normalized_mime == "audio/3gpp":
            normalized_ext = "3gp"
        elif normalized_mime == "audio/amr":
            normalized_ext = "amr"
        elif normalized_mime == "audio/mp4":
            normalized_ext = "m4a"

    return original_name, normalized_mime, normalized_ext


def _read_upload_initial_bytes(file_obj, limit=1024 * 1024):
    position = None
    try:
        if hasattr(file_obj, "tell"):
            position = file_obj.tell()
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        return file_obj.read(limit) or b""
    except Exception:
        return None
    finally:
        try:
            if hasattr(file_obj, "seek"):
                file_obj.seek(position or 0)
        except Exception:
            pass


class UploadCreateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "upload_create"

    def post(self, request):
        serializer = UploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file_obj = serializer.validated_data["file"]
        require_product_access(
            request,
            "upload_file",
            record_usage=True,
            idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
            metadata={
                "original_name": serializer.validated_data.get("original_name", ""),
                "size": getattr(file_obj, "size", 0),
                "mime_type": serializer.validated_data.get("mime_type", ""),
            },
        )
        initial_bytes = _read_upload_initial_bytes(file_obj)

        original_name, normalized_mime, extension = _normalize_upload_media(
            file_obj,
            supplied_original_name=serializer.validated_data.get("original_name", ""),
            supplied_mime_type=serializer.validated_data.get("mime_type", ""),
        )

        pending = PendingUpload.objects.create(
            user=request.user,
            file=file_obj,
            original_name=original_name,
            media_kind=serializer.validated_data.get("media_kind") or _media_kind_from_mime(normalized_mime),
            mime_type=normalized_mime,
            size=file_obj.size,
            extension=extension,
            width=serializer.validated_data.get("width"),
            height=serializer.validated_data.get("height"),
            rotation=serializer.validated_data.get("rotation"),
            duration_seconds=serializer.validated_data.get("duration_seconds"),
            thumbnail=serializer.validated_data.get("thumbnail"),
            metadata=serializer.validated_data.get("metadata") or {},
        )

        if getattr(settings, "UPLOAD_SCAN_ASYNC", True):
            dispatch_pending_upload_scan(pending)
        else:
            scan_upload_file(pending, initial_bytes=initial_bytes)

        pending.refresh_from_db()
        return Response(
            PendingUploadSerializer(
                pending, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class PendingUploadDownloadView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get_object(self, request, upload_id):
        token = request.query_params.get("token")
        if token:
            validate_media_access_token(
                token,
                resource_type="pending_upload",
                resource_id=upload_id,
                user=request.user if getattr(request.user, "is_authenticated", False) else None,
            )
            return get_object_or_404(
                PendingUpload,
                id=upload_id,
                scan_status=PendingUpload.ScanStatus.CLEAN,
                expires_at__gt=timezone.now(),
            )
        if not request.user.is_authenticated:
            raise PermissionDenied("Authentication or media token is required.")
        return get_object_or_404(secure_pending_upload_queryset_for_user(request.user), id=upload_id)

    def get(self, request, upload_id):
        upload = self.get_object(request, upload_id)
        response = _build_media_file_response(
            upload.file,
            filename=upload.original_name,
            mime_type=upload.mime_type,
            as_attachment=True,
            request=request,
        )
        log_chat_event(
            ChatAuditLog.EventType.MEDIA_ACCESSED,
            actor=request.user if request.user.is_authenticated else None,
            metadata={"resource_type": "pending_upload", "resource_id": str(upload.id), "disposition": "attachment"},
        )
        return response


class PendingUploadPreviewView(PendingUploadDownloadView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, upload_id):
        upload = self.get_object(request, upload_id)
        if not _can_preview_inline_mime(upload.mime_type):
            raise PermissionDenied("This upload type cannot be previewed inline.")
        response = _build_media_file_response(
            upload.file,
            filename=upload.original_name,
            mime_type=upload.mime_type,
            as_attachment=False,
            request=request,
        )
        log_chat_event(
            ChatAuditLog.EventType.MEDIA_ACCESSED,
            actor=request.user if request.user.is_authenticated else None,
            metadata={"resource_type": "pending_upload", "resource_id": str(upload.id), "disposition": "inline"},
        )
        return response


class PendingUploadThumbnailView(PendingUploadDownloadView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, upload_id):
        upload = self.get_object(request, upload_id)
        if not upload.thumbnail:
            raise Http404("Thumbnail is not available for this upload.")
        response = _build_thumbnail_file_response(upload.thumbnail, filename=Path(upload.thumbnail.name).name, request=request)
        log_chat_event(
            ChatAuditLog.EventType.MEDIA_ACCESSED,
            actor=request.user if request.user.is_authenticated else None,
            metadata={"resource_type": "pending_upload", "resource_id": str(upload.id), "disposition": "thumbnail"},
        )
        return response


class AttachmentDownloadView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get_object(self, request, attachment_id):
        token = request.query_params.get("token")
        if token:
            token_payload = validate_media_access_token(
                token,
                resource_type="attachment",
                resource_id=attachment_id,
                user=request.user if getattr(request.user, "is_authenticated", False) else None,
            )
            from apps.chat.models import MessageAttachment
            attachment = get_object_or_404(MessageAttachment, id=attachment_id, scan_status=MessageAttachment.ScanStatus.CLEAN)
            attachment._media_token_payload = token_payload
            return attachment
        if not request.user.is_authenticated:
            raise PermissionDenied("Authentication or media token is required.")
        return get_object_or_404(secure_attachment_queryset_for_user(request.user), id=attachment_id)

    def get(self, request, attachment_id):
        attachment = self.get_object(request, attachment_id)
        if attachment.view_once:
            raise PermissionDenied("View-once media cannot be downloaded.")
        response = _build_media_file_response(
            attachment.file,
            filename=attachment.original_name,
            mime_type=attachment.mime_type,
            as_attachment=True,
            request=request,
        )
        log_chat_event(
            ChatAuditLog.EventType.MEDIA_ACCESSED,
            actor=request.user if request.user.is_authenticated else None,
            conversation=attachment.message.conversation,
            message=attachment.message,
            metadata={"resource_type": "attachment", "resource_id": str(attachment.id), "disposition": "attachment"},
        )
        return response


class AttachmentPreviewView(AttachmentDownloadView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, attachment_id):
        attachment = self.get_object(request, attachment_id)
        if attachment.view_once and getattr(attachment, "_media_token_payload", {}).get("purpose") != "view_once":
            raise PermissionDenied("A one-time viewing session is required.")
        if not _can_preview_inline_mime(attachment.mime_type):
            raise PermissionDenied("This attachment type cannot be previewed inline.")
        response = _build_media_file_response(
            attachment.file,
            filename=attachment.original_name,
            mime_type=attachment.mime_type,
            as_attachment=False,
            request=request,
        )
        if attachment.view_once:
            response["Cache-Control"] = "no-store, private"
            response["Pragma"] = "no-cache"
        log_chat_event(
            ChatAuditLog.EventType.MEDIA_ACCESSED,
            actor=request.user if request.user.is_authenticated else None,
            conversation=attachment.message.conversation,
            message=attachment.message,
            metadata={"resource_type": "attachment", "resource_id": str(attachment.id), "disposition": "inline"},
        )
        return response


class AttachmentThumbnailView(AttachmentDownloadView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, attachment_id):
        attachment = self.get_object(request, attachment_id)
        if attachment.view_once:
            raise PermissionDenied("View-once media does not expose thumbnails.")
        if not attachment.thumbnail:
            raise Http404("Thumbnail is not available for this attachment.")
        response = _build_thumbnail_file_response(attachment.thumbnail, filename=Path(attachment.thumbnail.name).name, request=request)
        log_chat_event(
            ChatAuditLog.EventType.MEDIA_ACCESSED,
            actor=request.user if request.user.is_authenticated else None,
            conversation=attachment.message.conversation,
            message=attachment.message,
            metadata={"resource_type": "attachment", "resource_id": str(attachment.id), "disposition": "thumbnail"},
        )
        return response


class MediaTokenCreateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "media_token"

    def post(self, request, resource_type, resource_id):
        serializer = MediaTokenSerializer(data={"resource_type": resource_type})
        serializer.is_valid(raise_exception=True)
        if resource_type == "attachment":
            attachment = get_object_or_404(secure_attachment_queryset_for_user(request.user), id=resource_id)
            if attachment.view_once:
                raise PermissionDenied("Use the one-time open action for this attachment.")
        else:
            get_object_or_404(secure_pending_upload_queryset_for_user(request.user), id=resource_id)
        payload = create_media_access_payload(actor=request.user, resource_type=resource_type, resource_id=resource_id, request=request)
        return Response(payload)


class ViewOnceAttachmentOpenView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, attachment_id):
        payload = consume_view_once_attachment(actor=request.user, attachment_id=attachment_id, request=request)
        return Response(payload)


class CallingConfigView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = CallingConfigSerializer(get_calling_config(actor=request.user, requested_preset=request.query_params.get("quality")))
        return Response(serializer.data)


class CallListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CallSessionSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return CallSession.objects.none()
        return CallSession.objects.filter(conversation__participants__user=self.request.user, conversation__participants__left_at__isnull=True).select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user").distinct()


class RecentCallListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CallSessionSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return CallSession.objects.none()
        serializer = RecentCallQuerySerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return list_recent_calls_for_user(self.request.user, status_filter=serializer.validated_data.get("status"))


class CallStartView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        serializer = CallStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            call = start_call(request.user, conversation, serializer.validated_data["call_type"], serializer.validated_data.get("metadata"))
        except CallParticipantBusy as exc:
            payload = {
                "code": "active_call_exists" if exc.actor_busy else "callee_busy",
                "detail": "You already have another call in progress." if exc.actor_busy else "This person is already in another call.",
                "busy_user_ids": exc.busy_user_ids,
            }
            if exc.active_call_id:
                payload["active_call_id"] = exc.active_call_id
            return Response(payload, status=status.HTTP_409_CONFLICT)
        output = CallSessionSerializer(call, context={"request": request}).data
        _broadcast_to_conversation(str(conversation.id), "call.started", output)
        _broadcast_call_timeline_message(call, request=request)
        _broadcast_conversation_update(str(conversation.id), request=request)
        return Response(output, status=status.HTTP_201_CREATED)


class CallDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CallSessionSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return CallSession.objects.none()
        return CallSession.objects.filter(conversation__participants__user=self.request.user, conversation__participants__left_at__isnull=True).select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user").distinct()


class CallAcceptView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, call_id):
        call = get_object_or_404(CallSession.objects.select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user"), id=call_id)
        call = accept_call(request.user, call)
        output = CallSessionSerializer(call, context={"request": request}).data
        _broadcast_to_conversation(str(call.conversation_id), "call.accepted", output)
        _broadcast_call_timeline_message(call, request=request)
        _broadcast_conversation_update(str(call.conversation_id), request=request)
        return Response(output)


class CallDeclineView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, call_id):
        call = get_object_or_404(CallSession.objects.select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user"), id=call_id)
        serializer = CallActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        call = decline_call(request.user, call, serializer.validated_data.get("reason") or "declined")
        output = CallSessionSerializer(call, context={"request": request}).data
        _broadcast_to_conversation(str(call.conversation_id), "call.declined", output)
        _broadcast_call_timeline_message(call, request=request)
        _broadcast_conversation_update(str(call.conversation_id), request=request)
        return Response(output)


class CallEndView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, call_id):
        call = get_object_or_404(CallSession.objects.select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user"), id=call_id)
        serializer = CallActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        call = end_call(request.user, call, serializer.validated_data.get("reason") or "ended")
        output = CallSessionSerializer(call, context={"request": request}).data
        _broadcast_to_conversation(str(call.conversation_id), "call.ended", output)
        _broadcast_call_timeline_message(call, request=request)
        _broadcast_conversation_update(str(call.conversation_id), request=request)
        return Response(output)


class CallSignalView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, call_id):
        call = get_object_or_404(CallSession.objects.select_related("conversation"), id=call_id)
        serializer = CallSignalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = send_call_signal(request.user, call, serializer.validated_data["signal_type"], serializer.validated_data.get("payload") or {})
        _broadcast_to_conversation(str(call.conversation_id), "call.signal", payload)
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class CallHeartbeatView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, call_id):
        call = get_object_or_404(CallSession.objects.select_related("conversation"), id=call_id)
        serializer = CallHeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = heartbeat_call_participant(
            request.user,
            call,
            metrics=serializer.validated_data.get("metrics") or {},
            network_quality=serializer.validated_data.get("network_quality"),
        )
        _broadcast_to_conversation(str(call.conversation_id), "call.heartbeat", payload)
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class CallMediaStateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, call_id):
        call = get_object_or_404(CallSession.objects.select_related("conversation"), id=call_id)
        serializer = CallMediaStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = update_call_media_state(request.user, call, **serializer.validated_data)
        _broadcast_to_conversation(str(call.conversation_id), "call.media_state", payload)
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class CallQualityReportView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, call_id):
        call = get_object_or_404(CallSession.objects.select_related("conversation"), id=call_id)
        serializer = CallQualityReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = submit_call_quality_report(request.user, call, **serializer.validated_data)
        _broadcast_to_conversation(str(call.conversation_id), "call.quality_report", payload)
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class CallSpeakerStateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, call_id):
        call = get_object_or_404(CallSession.objects.select_related("conversation"), id=call_id)
        serializer = CallSpeakerStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = update_call_speaking_state(request.user, call, **serializer.validated_data)
        _broadcast_to_conversation(str(call.conversation_id), "call.speaker_state", payload)
        orchestration = payload.get("orchestration")
        if orchestration:
            _broadcast_to_conversation(str(call.conversation_id), "call.orchestration", orchestration)
        return Response(payload)


class CallOrchestrationView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, call_id):
        call = get_object_or_404(
            CallSession.objects.prefetch_related("participants__user"),
            id=call_id,
            conversation__participants__user=request.user,
        )
        payload = get_call_orchestration(call, recipient=request.user)
        # Return the raw orchestration payload so signaling fields like `signals`
        # are not accidentally stripped by a serializer that lags behind the
        # backend payload shape.
        return Response(payload)


class TurnCredentialsView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        payload = get_turn_credentials(actor=request.user)
        serializer = TurnCredentialsSerializer(payload)
        return Response(serializer.data)


class CallDiagnosticsView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, call_id):
        call = get_object_or_404(CallSession.objects.prefetch_related("participants__user"), id=call_id, conversation__participants__user=request.user)
        expire_stale_call_participants()
        payload = get_call_diagnostics(call)
        serializer = CallDiagnosticsSerializer(payload)
        return Response(serializer.data)


class IntegrationHealthView(views.APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        payload = integration_health_snapshot()
        serializer = IntegrationHealthSerializer(payload)
        return Response(serializer.data)


class ChatCapabilitiesView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        payload = {
            "version": "2026-04-17",
            "features": {
                "text_messages": True,
                "attachments": True,
                "voice_notes": True,
                "message_search": True,
                "message_edit_history": True,
                "end_to_end_encryption": True,
                "device_key_registry": True,
                "conversation_key_material": True,
                "conversation_rekey_signals": True,
                "device_fingerprints": True,
                "device_verification_ui": True,
                "presence": True,
                "push_notifications": True,
                "server_drafts": False,
                "client_encryption_envelopes": True,
                "encrypted_attachments": True,
                "calls": True,
                "group_calls": True,
                "call_quality_reports": True,
            },
            "limits": {
                "max_upload_bytes": int(getattr(settings, "MAX_UPLOAD_BYTES", 0) or 0),
                "message_max_links": int(getattr(settings, "MESSAGE_MAX_LINKS", 0) or 0),
                "message_burst_window_seconds": int(getattr(settings, "MESSAGE_BURST_WINDOW_SECONDS", 0) or 0),
                "message_burst_threshold": int(getattr(settings, "MESSAGE_BURST_THRESHOLD", 0) or 0),
                "max_ciphertext_bytes": int(getattr(settings, "MESSAGE_MAX_CIPHERTEXT_BYTES", 0) or 0),
                "max_encryption_envelope_bytes": int(getattr(settings, "MESSAGE_MAX_ENCRYPTION_ENVELOPE_BYTES", 0) or 0),
            },
            "media": {
                "allowed_extensions": list(getattr(settings, "ALLOWED_UPLOAD_EXTENSIONS", [])),
                "allowed_mime_types": list(getattr(settings, "ALLOWED_UPLOAD_MIME_TYPES", [])),
                "pending_upload_ttl_seconds": int(getattr(settings, "PENDING_UPLOAD_TTL_SECONDS", 0) or 0),
                "media_token_ttl_seconds": int(getattr(settings, "MEDIA_TOKEN_TTL_SECONDS", 0) or 0),
                "inline_preview_mime_prefixes": ["image/", "audio/", "video/"],
                "inline_preview_mime_types": ["application/pdf"],
            },
            "calls": {
                "offer_timeout_seconds": int(getattr(settings, "CALL_OFFER_TIMEOUT_SECONDS", 0) or 0),
                "max_group_call_participants": int(getattr(settings, "MAX_GROUP_CALL_PARTICIPANTS", 0) or 0),
                "heartbeat_interval_seconds": int(getattr(settings, "CALL_HEARTBEAT_INTERVAL_SECONDS", 0) or 0),
                "stale_participant_seconds": int(getattr(settings, "CALL_STALE_PARTICIPANT_SECONDS", 0) or 0),
                "allow_simultaneous_screen_shares": bool(getattr(settings, "CALL_ALLOW_SIMULTANEOUS_SCREEN_SHARES", False)),
            },
            "security": {
                "media_requires_signed_tokens": True,
                "uploads_require_antivirus_clean_status": True,
                "encrypted_messages_store_plaintext": False,
                "encrypted_messages_editable": False,
                "encrypted_attachments_store_plaintext": False,
                "device_private_keys_server_side": False,
                "e2ee_conversations_use_local_drafts_only": True,
                "encrypted_message_forwarding_requires_client_reencryption": True,
                "call_signaling_persisted_server_side": False,
            },
        }
        serializer = ChatCapabilitiesSerializer(payload)
        return Response(serializer.data)


class MessageFailView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, message_id):
        message = get_object_or_404(Message.objects.select_related("conversation", "sender"), id=message_id)
        serializer = MessageFailureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = mark_message_failed(request.user, message, serializer.validated_data.get("reason", ""))
        output = MessageSerializer(message, context={"request": request}).data
        _broadcast_to_conversation(str(message.conversation_id), "message.failed", output)
        return Response(output)


class MessageRetryView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, message_id):
        message = get_object_or_404(Message.objects.select_related("conversation", "sender"), id=message_id)
        message = retry_message(request.user, message)
        output = MessageSerializer(message, context={"request": request}).data
        _broadcast_to_conversation(str(message.conversation_id), "message.retried", output)
        return Response(output)


class ReactionCreateDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "reaction_write"

    def get_message(self, request, message_id):
        return get_object_or_404(Message.objects.select_related("conversation"), id=message_id, conversation__participants__user=request.user)

    def post(self, request, message_id):
        message = self.get_message(request, message_id)
        serializer = ReactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = add_reaction(request.user, message, serializer.validated_data["emoji"])
        payload = MessageSerializer(message, context={"request": request}).data
        _broadcast_to_conversation(str(message.conversation_id), "message.reaction_updated", payload)
        return Response(payload)

    def delete(self, request, message_id):
        message = self.get_message(request, message_id)
        serializer = ReactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remove_reaction(request.user, message, serializer.validated_data["emoji"])
        payload = MessageSerializer(message, context={"request": request}).data
        _broadcast_to_conversation(str(message.conversation_id), "message.reaction_updated", payload)
        return Response(payload)


class PresencePingView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        device_id = request.data.get("device_id", "default")
        snapshot = set_presence(request.user, device_id=device_id)
        _broadcast_presence_update(request.user, snapshot)
        return Response({"user_id": str(request.user.id), **snapshot, "server_time": timezone.now().isoformat()})


class PresenceDisconnectView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        device_id = request.data.get("device_id", "default")
        snapshot = clear_presence(request.user, device_id=device_id)
        _broadcast_presence_update(request.user, snapshot)
        return Response({"user_id": str(request.user.id), **snapshot, "server_time": timezone.now().isoformat()})


class BlockListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserBlockSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "block_write"

    def get_queryset(self):
        if _schema_generation(self):
            return UserBlock.objects.none()
        return user_blocks_qs(self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_user = get_object_or_404(User, id=serializer.validated_data["blocked_user_id"], is_active=True)
        block = block_user(request.user, target_user, serializer.validated_data.get("reason", ""))
        _broadcast_pair_presence_refresh(request.user, target_user)
        return Response(UserBlockSerializer(block, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BlockDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, user_id):
        target_user = get_object_or_404(User, id=user_id, is_active=True)
        unblock_user(request.user, target_user)
        _broadcast_pair_presence_refresh(request.user, target_user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DeviceSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "device_write"

    def get_queryset(self):
        if _schema_generation(self):
            return UserDevice.objects.none()
        return request_user_devices(self.request.user)

    def get_serializer_class(self):
        return DeviceUpsertSerializer if self.request.method == "POST" else DeviceSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = register_device(request.user, serializer.validated_data["platform"], serializer.validated_data["push_token"])
        return Response(DeviceSerializer(device, context={"request": request}).data, status=status.HTTP_201_CREATED)


class DeviceDeactivateView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "device_write"

    def post(self, request):
        serializer = DeviceDeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = deactivate_device(request.user, serializer.validated_data["push_token"])
        return Response(DeviceSerializer(device, context={"request": request}).data)


class NotificationPreferenceView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        preference = get_notification_preference(request.user)
        return Response(NotificationPreferenceSerializer(preference, context={"request": request}).data)

    def patch(self, request):
        preference = get_notification_preference(request.user)
        serializer = NotificationPreferenceSerializer(preference, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class SyncView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = SyncQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        since = serializer.validated_data.get("since")
        conversation_id = serializer.validated_data.get("conversation_id")
        limit = serializer.validated_data.get("limit", 100)
        conversations = user_conversations_qs(request.user)
        if since:
            conversations = conversations.filter(updated_at__gte=since)
        if conversation_id:
            conversations = conversations.filter(id=conversation_id)
        conversations = list(conversations.order_by("updated_at", "id")[: limit + 1])
        has_more_conversations = len(conversations) > limit
        conversations = conversations[:limit]
        conversation_ids = [conversation.id for conversation in conversations]
        messages = Message.objects.filter(conversation_id__in=conversation_ids, conversation__participants__user=request.user).select_related("sender", "sender__profile", "conversation").prefetch_related("attachments", "reactions__user__profile", "deliveries__user__profile", "edit_history__edited_by__profile").distinct()
        if since:
            messages = messages.filter(updated_at__gte=since)
        messages = list(messages.order_by("updated_at", "id")[: limit + 1])
        has_more_messages = len(messages) > limit
        messages = messages[:limit]
        draft_qs = ConversationDraft.objects.filter(user=request.user).select_related("conversation", "reply_to", "reply_to__sender", "reply_to__sender__profile")
        if since:
            draft_qs = draft_qs.filter(updated_at__gte=since)
        if conversation_id:
            draft_qs = draft_qs.filter(conversation_id=conversation_id)
        visible_drafts = [
            draft
            for draft in draft_qs.order_by("updated_at", "id")
            if not conversation_has_e2ee_enabled_participants(draft.conversation)
        ]
        has_more_drafts = len(visible_drafts) > limit
        drafts = visible_drafts[:limit]
        active_calls = []
        seen_call_ids = set()
        for conversation in conversations:
            call = get_active_call_for_conversation(conversation)
            if call and call.id not in seen_call_ids:
                active_calls.append(call)
                seen_call_ids.add(call.id)
        next_markers = [item.updated_at for item in conversations + messages + drafts if getattr(item, "updated_at", None)]
        next_since = max(next_markers).isoformat() if next_markers else timezone.now().isoformat()
        return Response({
            "conversations": ConversationListSerializer(conversations, many=True, context={"request": request}).data,
            "messages": MessageSerializer(messages, many=True, context={"request": request}).data,
            "drafts": ConversationDraftSerializer(drafts, many=True, context={"request": request}).data,
            "active_calls": CallSessionSerializer(active_calls, many=True, context={"request": request}).data,
            "has_more_conversations": has_more_conversations,
            "has_more_messages": has_more_messages,
            "has_more_drafts": has_more_drafts,
            "next_since": next_since,
            "server_time": timezone.now().isoformat(),
        })


class ModerationReportListView(generics.ListAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = MessageReportSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return MessageReport.objects.none()
        return list_message_reports(self.request.user)


class ModerationReportResolveView(views.APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, report_id):
        report = get_object_or_404(MessageReport.objects.select_related("message"), id=report_id)
        serializer = ModerationResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = resolve_message_report(request.user, report, serializer.validated_data.get("notes", ""), serializer.validated_data.get("hide_message", False))
        return Response(ModerationActionSerializer(action, context={"request": request}).data)


class ModerationReportDismissView(views.APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, report_id):
        report = get_object_or_404(MessageReport.objects.select_related("message"), id=report_id)
        serializer = ModerationDismissSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = dismiss_message_report(request.user, report, serializer.validated_data.get("notes", ""))
        return Response(ModerationActionSerializer(action, context={"request": request}).data)


class ModerationMessageRestoreView(views.APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, message_id):
        message = get_object_or_404(Message, id=message_id)
        serializer = MessageRestoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = restore_message_by_staff(request.user, message, serializer.validated_data.get("notes", ""))
        return Response(ModerationActionSerializer(action, context={"request": request}).data)


class ChatAuditLogListView(generics.ListAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = ChatAuditLogSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return ChatAuditLog.objects.none()
        qs = ChatAuditLog.objects.select_related("actor", "actor__profile", "conversation", "message").order_by("-created_at")
        conversation_id = self.request.query_params.get("conversation_id")
        event_type = self.request.query_params.get("event_type")
        if conversation_id:
            qs = qs.filter(conversation_id=conversation_id)
        if event_type:
            qs = qs.filter(event_type=event_type)
        return qs



class ConversationNotificationSettingView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        setting = get_conversation_notification_setting(request.user, conversation)
        return Response(ConversationNotificationSettingSerializer(setting, context={"request": request}).data)

    def patch(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        setting = get_conversation_notification_setting(request.user, conversation)
        serializer = ConversationNotificationSettingSerializer(setting, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        setting = update_conversation_notification_setting(request.user, conversation, **serializer.validated_data)
        return Response(ConversationNotificationSettingSerializer(setting, context={"request": request}).data)


class ConversationInviteLinkListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if _schema_generation(self):
            return ConversationInviteLink.objects.none()
        conversation = get_object_or_404(Conversation, id=self.kwargs["conversation_id"], is_active=True)
        from apps.chat.services import ensure_group_admin

        ensure_group_admin(conversation, self.request.user)
        return ConversationInviteLink.objects.filter(conversation=conversation).select_related("created_by", "created_by__profile")

    def get_serializer_class(self):
        return ConversationInviteCreateSerializer if self.request.method == "POST" else ConversationInviteLinkSerializer

    def create(self, request, *args, **kwargs):
        conversation = get_object_or_404(Conversation, id=self.kwargs["conversation_id"], is_active=True)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invite = create_conversation_invite_link(request.user, conversation, **serializer.validated_data)
        return Response(ConversationInviteLinkSerializer(invite, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ConversationInviteLinkRevokeView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id, invite_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, is_active=True)
        invite = get_object_or_404(ConversationInviteLink, id=invite_id, conversation=conversation)
        invite = revoke_conversation_invite_link(request.user, invite)
        return Response(ConversationInviteLinkSerializer(invite, context={"request": request}).data)


class ConversationInviteJoinView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ConversationInviteJoinSerializer(data=request.data or request.query_params)
        serializer.is_valid(raise_exception=True)
        conversation = join_group_via_invite(request.user, serializer.validated_data["token"])
        output = ConversationDetailSerializer(conversation, context={"request": request}).data
        _broadcast_to_conversation(str(conversation.id), "conversation.updated", output)
        _broadcast_conversation_update(str(conversation.id), request=request)
        return Response(output, status=status.HTTP_200_OK)


class ConversationMediaGalleryView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationMediaSerializer

    def get_queryset(self):
        if _schema_generation(self):
            return MessageAttachment.objects.none()
        media_kind = self.request.query_params.get("kind", "all").strip().lower() or "all"
        return conversation_media_qs(self.request.user, self.kwargs["conversation_id"], media_kind=media_kind)


EmptyRequestSerializer = inline_serializer(name="EmptyRequest", fields={})
DetailResponseSerializer = inline_serializer(name="ChatDetailResponse", fields={"detail": serializers.CharField()})
DeliveryResponseSerializer = inline_serializer(
    name="DeliveryResponse",
    fields={
        "conversation_id": serializers.UUIDField(),
        "user_id": serializers.CharField(),
        "last_delivered_message_id": serializers.CharField(allow_null=True),
        "last_delivered_at": serializers.CharField(allow_null=True),
    },
)
ReadResponseSerializer = inline_serializer(
    name="ReadResponse",
    fields={
        "conversation_id": serializers.UUIDField(),
        "user_id": serializers.CharField(),
        "last_delivered_message_id": serializers.CharField(allow_null=True),
        "last_delivered_at": serializers.CharField(allow_null=True),
        "last_read_message_id": serializers.CharField(allow_null=True),
        "last_read_at": serializers.CharField(allow_null=True),
    },
)
ToggleConversationStateResponseSerializer = inline_serializer(
    name="ToggleConversationStateResponse",
    fields={"conversation_id": serializers.UUIDField(), "is_muted": serializers.BooleanField(required=False), "is_archived": serializers.BooleanField(required=False), "is_pinned": serializers.BooleanField(required=False)},
)
PresenceRequestSerializer = inline_serializer(name="PresenceRequest", fields={"device_id": serializers.CharField(required=False)})
PresenceResponseSerializer = inline_serializer(
    name="PresenceResponse",
    fields={
        "user_id": serializers.CharField(),
        "is_online": serializers.BooleanField(),
        "active_devices": serializers.IntegerField(),
        "last_seen_at": serializers.CharField(allow_null=True, required=False),
        "server_time": serializers.CharField(),
    },
)
ParticipantRoleResponseSerializer = inline_serializer(name="ParticipantRoleResponse", fields={"conversation_id": serializers.UUIDField(), "user_id": serializers.CharField(), "role": serializers.CharField()})
ParticipantRemovedResponseSerializer = inline_serializer(name="ParticipantRemovedResponse", fields={"conversation_id": serializers.UUIDField(), "removed_user_id": serializers.CharField()})
ParticipantMutedResponseSerializer = inline_serializer(name="ParticipantMutedResponse", fields={"conversation_id": serializers.UUIDField(), "user_id": serializers.CharField(), "moderation_muted_until": serializers.CharField(allow_null=True)})
ParticipantBannedResponseSerializer = inline_serializer(name="ParticipantBannedResponse", fields={"conversation_id": serializers.UUIDField(), "user_id": serializers.CharField(), "banned_at": serializers.CharField(allow_null=True), "ban_reason": serializers.CharField()})
OwnershipTransferResponseSerializer = inline_serializer(name="OwnershipTransferResponse", fields={"conversation_id": serializers.UUIDField(), "owner_user_id": serializers.CharField()})
LeaveConversationResponseSerializer = inline_serializer(name="LeaveConversationResponse", fields={"conversation_id": serializers.UUIDField(), "user_id": serializers.CharField(), "left_at": serializers.CharField()})
MediaTokenResponseSerializer = inline_serializer(name="MediaTokenResponse", fields={"token": serializers.CharField(), "download_url": serializers.CharField(), "preview_url": serializers.CharField(required=False), "expires_at": serializers.CharField()})
ConversationE2EEKeysResponseSerializer = inline_serializer(
    name="ConversationE2EEKeysResponse",
    fields={
        "conversation_id": serializers.UUIDField(),
        "key_version": serializers.IntegerField(),
        "rekey_required": serializers.BooleanField(),
        "last_key_rotation_at": serializers.DateTimeField(allow_null=True),
        "last_security_event_at": serializers.DateTimeField(allow_null=True),
        "participants": serializers.JSONField(),
    },
)
SyncResponseSerializer = inline_serializer(
    name="SyncResponse",
    fields={
        "conversations": ConversationListSerializer(many=True),
        "messages": MessageSerializer(many=True),
        "drafts": ConversationDraftSerializer(many=True),
        "active_calls": CallSessionSerializer(many=True),
        "has_more_conversations": serializers.BooleanField(),
        "has_more_messages": serializers.BooleanField(),
        "has_more_drafts": serializers.BooleanField(),
        "next_since": serializers.CharField(),
        "server_time": serializers.CharField(),
    },
)


E2EEDeviceKeyRevokeView.post = extend_schema(request=None, responses=E2EEDeviceKeySerializer)(E2EEDeviceKeyRevokeView.post)
ConversationE2EEKeysView.get = extend_schema(responses=ConversationE2EEKeysResponseSerializer)(ConversationE2EEKeysView.get)
MessageUpdateDeleteView.patch = extend_schema(request=MessageUpdateSerializer, responses=MessageSerializer)(MessageUpdateDeleteView.patch)
MessageUpdateDeleteView.delete = extend_schema(request=None, responses=MessageSerializer)(MessageUpdateDeleteView.delete)
MessageForwardView.post = extend_schema(request=MessageForwardSerializer, responses={201: MessageSerializer})(MessageForwardView.post)
MessageTranscriptView.post = extend_schema(request=MessageTranscriptUpsertSerializer, responses=MessageSerializer)(MessageTranscriptView.post)
MessageReportView.post = extend_schema(request=MessageReportSerializer, responses={201: MessageReportSerializer})(MessageReportView.post)
MarkConversationDeliveredView.post = extend_schema(request=DeliverySerializer, responses=DeliveryResponseSerializer)(MarkConversationDeliveredView.post)
MarkConversationReadView.post = extend_schema(request=DeliverySerializer, responses=ReadResponseSerializer)(MarkConversationReadView.post)
MuteConversationView.post = extend_schema(request=None, responses=ToggleConversationStateResponseSerializer)(MuteConversationView.post)
ArchiveConversationView.post = extend_schema(request=None, responses=ToggleConversationStateResponseSerializer)(ArchiveConversationView.post)
PinConversationView.post = extend_schema(request=None, responses=ToggleConversationStateResponseSerializer)(PinConversationView.post)
GroupParticipantManageView.post = extend_schema(request=ParticipantManageSerializer, responses=ConversationDetailSerializer)(GroupParticipantManageView.post)
GroupParticipantRemoveView.delete = extend_schema(request=None, responses=ParticipantRemovedResponseSerializer)(GroupParticipantRemoveView.delete)
GroupParticipantRoleUpdateView.patch = extend_schema(request=ParticipantRoleUpdateSerializer, responses=ParticipantRoleResponseSerializer)(GroupParticipantRoleUpdateView.patch)
GroupParticipantMuteView.post = extend_schema(request=GroupParticipantMuteSerializer, responses=ParticipantMutedResponseSerializer)(GroupParticipantMuteView.post)
GroupParticipantBanView.post = extend_schema(request=GroupParticipantBanSerializer, responses=ParticipantBannedResponseSerializer)(GroupParticipantBanView.post)
GroupParticipantBanView.delete = extend_schema(request=None, responses=OpenApiTypes.OBJECT)(GroupParticipantBanView.delete)
GroupOwnershipTransferView.post = extend_schema(request=OwnershipTransferSerializer, responses=OwnershipTransferResponseSerializer)(GroupOwnershipTransferView.post)
LeaveConversationView.post = extend_schema(request=None, responses=LeaveConversationResponseSerializer)(LeaveConversationView.post)
UploadCreateView.post = extend_schema(request=UploadCreateSerializer, responses={201: PendingUploadSerializer})(UploadCreateView.post)
PendingUploadDownloadView.get = extend_schema(parameters=[OpenApiParameter("token", OpenApiTypes.STR, OpenApiParameter.QUERY)], responses=OpenApiTypes.BINARY)(PendingUploadDownloadView.get)
PendingUploadPreviewView.get = extend_schema(parameters=[OpenApiParameter("token", OpenApiTypes.STR, OpenApiParameter.QUERY)], responses=OpenApiTypes.BINARY)(PendingUploadPreviewView.get)
PendingUploadThumbnailView.get = extend_schema(parameters=[OpenApiParameter("token", OpenApiTypes.STR, OpenApiParameter.QUERY)], responses=OpenApiTypes.BINARY)(PendingUploadThumbnailView.get)
AttachmentDownloadView.get = extend_schema(parameters=[OpenApiParameter("token", OpenApiTypes.STR, OpenApiParameter.QUERY)], responses=OpenApiTypes.BINARY)(AttachmentDownloadView.get)
AttachmentPreviewView.get = extend_schema(parameters=[OpenApiParameter("token", OpenApiTypes.STR, OpenApiParameter.QUERY)], responses=OpenApiTypes.BINARY)(AttachmentPreviewView.get)
AttachmentThumbnailView.get = extend_schema(parameters=[OpenApiParameter("token", OpenApiTypes.STR, OpenApiParameter.QUERY)], responses=OpenApiTypes.BINARY)(AttachmentThumbnailView.get)
MediaTokenCreateView.post = extend_schema(request=None, responses=MediaTokenResponseSerializer)(MediaTokenCreateView.post)
CallingConfigView.get = extend_schema(parameters=[OpenApiParameter("quality", OpenApiTypes.STR, OpenApiParameter.QUERY)], responses=CallingConfigSerializer)(CallingConfigView.get)
CallStartView.post = extend_schema(request=CallStartSerializer, responses={201: CallSessionSerializer})(CallStartView.post)
CallAcceptView.post = extend_schema(request=None, responses=CallSessionSerializer)(CallAcceptView.post)
CallDeclineView.post = extend_schema(request=CallActionSerializer, responses=CallSessionSerializer)(CallDeclineView.post)
CallEndView.post = extend_schema(request=CallActionSerializer, responses=CallSessionSerializer)(CallEndView.post)
CallSignalView.post = extend_schema(request=CallSignalSerializer, responses=OpenApiTypes.OBJECT)(CallSignalView.post)
CallHeartbeatView.post = extend_schema(request=CallHeartbeatSerializer, responses=OpenApiTypes.OBJECT)(CallHeartbeatView.post)
CallMediaStateView.post = extend_schema(request=CallMediaStateSerializer, responses=OpenApiTypes.OBJECT)(CallMediaStateView.post)
CallQualityReportView.post = extend_schema(request=CallQualityReportSerializer, responses=OpenApiTypes.OBJECT)(CallQualityReportView.post)
CallSpeakerStateView.post = extend_schema(request=CallSpeakerStateSerializer, responses=OpenApiTypes.OBJECT)(CallSpeakerStateView.post)
CallOrchestrationView.get = extend_schema(responses=CallOrchestrationSerializer)(CallOrchestrationView.get)
TurnCredentialsView.get = extend_schema(responses=TurnCredentialsSerializer)(TurnCredentialsView.get)
CallDiagnosticsView.get = extend_schema(responses=CallDiagnosticsSerializer)(CallDiagnosticsView.get)
IntegrationHealthView.get = extend_schema(responses=IntegrationHealthSerializer)(IntegrationHealthView.get)
ChatCapabilitiesView.get = extend_schema(responses=ChatCapabilitiesSerializer)(ChatCapabilitiesView.get)
MessageFailView.post = extend_schema(request=MessageFailureSerializer, responses=MessageSerializer)(MessageFailView.post)
MessageRetryView.post = extend_schema(request=None, responses=MessageSerializer)(MessageRetryView.post)
ReactionCreateDeleteView.post = extend_schema(request=ReactionSerializer, responses=MessageSerializer)(ReactionCreateDeleteView.post)
ReactionCreateDeleteView.delete = extend_schema(request=ReactionSerializer, responses=MessageSerializer)(ReactionCreateDeleteView.delete)
PresencePingView.post = extend_schema(request=PresenceRequestSerializer, responses=PresenceResponseSerializer)(PresencePingView.post)
PresenceDisconnectView.post = extend_schema(request=PresenceRequestSerializer, responses=PresenceResponseSerializer)(PresenceDisconnectView.post)
BlockDeleteView.delete = extend_schema(request=None, responses={204: OpenApiResponse(description="User unblocked.")})(BlockDeleteView.delete)
DeviceDeactivateView.post = extend_schema(request=DeviceDeactivateSerializer, responses=DeviceSerializer)(DeviceDeactivateView.post)
NotificationPreferenceView.get = extend_schema(responses=NotificationPreferenceSerializer)(NotificationPreferenceView.get)
NotificationPreferenceView.patch = extend_schema(request=NotificationPreferenceSerializer, responses=NotificationPreferenceSerializer)(NotificationPreferenceView.patch)
SyncView.get = extend_schema(
    parameters=[
        OpenApiParameter("since", OpenApiTypes.DATETIME, OpenApiParameter.QUERY),
        OpenApiParameter("conversation_id", OpenApiTypes.UUID, OpenApiParameter.QUERY),
        OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY),
    ],
    responses=SyncResponseSerializer,
)(SyncView.get)
ModerationReportResolveView.post = extend_schema(request=ModerationResolveSerializer, responses=ModerationActionSerializer)(ModerationReportResolveView.post)
ModerationReportDismissView.post = extend_schema(request=ModerationDismissSerializer, responses=ModerationActionSerializer)(ModerationReportDismissView.post)
ModerationMessageRestoreView.post = extend_schema(request=MessageRestoreSerializer, responses=ModerationActionSerializer)(ModerationMessageRestoreView.post)
ConversationNotificationSettingView.get = extend_schema(responses=ConversationNotificationSettingSerializer)(ConversationNotificationSettingView.get)
ConversationNotificationSettingView.patch = extend_schema(request=ConversationNotificationSettingSerializer, responses=ConversationNotificationSettingSerializer)(ConversationNotificationSettingView.patch)
ConversationInviteLinkRevokeView.post = extend_schema(request=None, responses=ConversationInviteLinkSerializer)(ConversationInviteLinkRevokeView.post)
ConversationInviteJoinView.post = extend_schema(request=ConversationInviteJoinSerializer, responses=ConversationDetailSerializer)(ConversationInviteJoinView.post)
ConversationMediaGalleryView.get = extend_schema(parameters=[OpenApiParameter("kind", OpenApiTypes.STR, OpenApiParameter.QUERY, enum=["all", "image", "video", "audio", "file"])], responses=ConversationMediaSerializer(many=True))(ConversationMediaGalleryView.get)
ConversationDraftView.get = extend_schema(responses=ConversationDraftSerializer)(ConversationDraftView.get)
ConversationDraftView.patch = extend_schema(request=ConversationDraftSerializer, responses=ConversationDraftSerializer)(ConversationDraftView.patch)
ConversationDraftView.delete = extend_schema(request=None, responses=ConversationDraftSerializer)(ConversationDraftView.delete)
MessageEditHistoryView.get = extend_schema(responses=MessageEditHistorySerializer(many=True))(MessageEditHistoryView.get)
