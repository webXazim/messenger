from django.contrib.auth import get_user_model
from django.conf import settings
from pathlib import Path
from decimal import Decimal
import mimetypes
from io import BytesIO
from typing import Any
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from PIL import Image, UnidentifiedImageError

from apps.chat.models import (
    CallParticipant,
    CallSession,
    ChatAuditLog,
    Conversation,
    ConversationDraft,
    ConversationInviteLink,
    ConversationNotificationSetting,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageDelivery,
    MessageEditHistory,
    MessageReaction,
    MessageReport,
    MessageTranscript,
    ModerationAction,
    NotificationPreference,
    PendingUpload,
    UserE2EEDeviceKey,
    UserBlock,
    UserDevice,
)
from apps.chat.services import conversation_has_e2ee_enabled_participants, create_media_access_payload, get_calling_config, get_presence_snapshot, is_user_online
from apps.chat.services import sanitize_chat_text
from apps.chat.services import MEDIA_THUMBNAIL_MAX_BYTES, public_media_metadata, sanitize_media_metadata

User = get_user_model()


UPLOAD_MIME_ALIASES = {
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/opus": "audio/ogg",
    "audio/mp3": "audio/mpeg",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
}

UPLOAD_MIME_EXTENSION_FALLBACKS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/csv": "csv",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/3gpp": "3gp",
    "audio/amr": "amr",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/webm": "webm",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/3gpp": "3gp",
}

EXTENSION_MIME_COMPATIBILITY = {
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "gif": {"image/gif"},
    "webp": {"image/webp"},
    "pdf": {"application/pdf"},
    "txt": {"text/plain", "application/octet-stream"},
    "csv": {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel", "application/octet-stream"},
    "rtf": {"application/rtf", "text/rtf", "application/octet-stream"},
    "doc": {"application/msword", "application/octet-stream"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    "odt": {"application/vnd.oasis.opendocument.text", "application/zip", "application/octet-stream"},
    "xls": {"application/vnd.ms-excel", "application/octet-stream"},
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
    },
    "ods": {"application/vnd.oasis.opendocument.spreadsheet", "application/zip", "application/octet-stream"},
    "ppt": {"application/vnd.ms-powerpoint", "application/octet-stream"},
    "pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "application/octet-stream",
    },
    "odp": {"application/vnd.oasis.opendocument.presentation", "application/zip", "application/octet-stream"},
    "mp3": {"audio/mpeg", "audio/mp3", "application/octet-stream"},
    "wav": {"audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"},
    "mp4": {"video/mp4", "audio/mp4", "application/octet-stream"},
    "webm": {"video/webm", "audio/webm", "application/octet-stream"},
    "m4a": {"audio/mp4", "audio/m4a", "audio/x-m4a", "application/octet-stream"},
    "aac": {"audio/aac", "application/octet-stream"},
    "3gp": {"audio/3gpp", "video/3gpp", "application/octet-stream"},
    "amr": {"audio/amr", "application/octet-stream"},
    "ogg": {"audio/ogg", "audio/opus", "application/octet-stream"},
    "opus": {"audio/ogg", "audio/opus", "application/octet-stream"},
}


def _normalize_reported_mime(raw_mime: str, extension: str) -> str:
    mime = (raw_mime or "").split(";", 1)[0].strip().lower()
    if not mime or mime == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(f"file.{extension}") if extension else (None, None)
        mime = (guessed or mime or "").lower()
    if extension in {"mp4", "m4v"} and mime.startswith("audio/"):
        return "video/mp4"
    return UPLOAD_MIME_ALIASES.get(mime, mime)


def _extension_for_upload_validation(original_name: str, file_name: str, raw_mime: str) -> str:
    extension = Path(original_name or file_name or "").suffix.lower().lstrip(".")
    if extension:
        return extension
    normalized_mime = UPLOAD_MIME_ALIASES.get((raw_mime or "").split(";", 1)[0].strip().lower(), raw_mime or "")
    return UPLOAD_MIME_EXTENSION_FALLBACKS.get(normalized_mime.lower().strip(), "")


def _is_allowed_upload_mime(extension: str, mime: str) -> bool:
    mime = (mime or "").lower().strip()
    allowed_mimes = set(getattr(settings, "ALLOWED_UPLOAD_MIME_TYPES", []) or [])
    if mime and mime not in allowed_mimes:
        return False
    compatible = EXTENSION_MIME_COMPATIBILITY.get(extension)
    if not compatible:
        return False
    if not mime:
        return True
    return mime in compatible


def _is_quarantinable_upload(extension: str, mime: str) -> bool:
    blocked_extensions = {"exe", "bat", "cmd", "com", "scr", "ps1", "sh", "msi", "jar", "dll"}
    blocked_mimes = {"application/x-msdownload", "application/x-dosexec", "application/x-sh"}
    return extension in blocked_extensions or mime in blocked_mimes or "x-msdownload" in mime or "x-sh" in mime


def _media_kind_from_mime(mime_type: str) -> str:
    mime = (mime_type or "").lower().strip()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "file"


def _attachment_aspect_ratio(width, height):
    try:
        width_value = float(width)
        height_value = float(height)
    except (TypeError, ValueError):
        return None
    if width_value <= 0 or height_value <= 0:
        return None
    return round(width_value / height_value, 6)



class UserLiteSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="pk", read_only=True)
    display_name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()
    active_devices = serializers.SerializerMethodField()
    last_seen_at = serializers.SerializerMethodField()
    presence_label = serializers.SerializerMethodField()
    presence_visibility = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "email", "display_name", "avatar", "is_online", "active_devices", "last_seen_at", "presence_label", "presence_visibility")

    def get_display_name(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "display_name", "") or obj.get_full_name() or obj.username

    def get_avatar(self, obj) -> str | None:
        profile = getattr(obj, "profile", None)
        avatar = getattr(profile, "avatar", None)
        if not avatar:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(avatar.url) if request else avatar.url

    def _actor(self):
        request = self.context.get("request")
        actor = self.context.get("actor") or getattr(request, "user", None)
        return actor if getattr(actor, "is_authenticated", False) else None

    def _presence_is_visible(self, obj) -> bool:
        actor = self._actor()
        if actor is not None and str(actor.id) == str(obj.id):
            return True
        if actor is not None:
            cache = self.context.setdefault("_presence_block_cache", {})
            pair = tuple(sorted((str(actor.id), str(obj.id))))
            blocked = cache.get(pair)
            if blocked is None:
                blocked = UserBlock.objects.filter(
                    Q(blocker_id=actor.id, blocked_id=obj.id)
                    | Q(blocker_id=obj.id, blocked_id=actor.id)
                ).exists()
                cache[pair] = blocked
            if blocked:
                return False
        profile = getattr(obj, "profile", None)
        return profile is None or bool(getattr(profile, "show_online_status", True))

    def _presence_snapshot(self, obj):
        cache_key = f"_user_presence_{obj.id}"
        if not hasattr(self, cache_key):
            setattr(self, cache_key, get_presence_snapshot(obj.id))
        return getattr(self, cache_key)

    def get_is_online(self, obj) -> bool:
        return bool(self._presence_snapshot(obj)["is_online"]) if self._presence_is_visible(obj) else False

    def get_active_devices(self, obj) -> int:
        return int(self._presence_snapshot(obj)["active_devices"]) if self._presence_is_visible(obj) else 0

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_last_seen_at(self, obj):
        return getattr(obj, "last_seen_at", None) if self._presence_is_visible(obj) else None

    def get_presence_label(self, obj) -> str:
        if not self._presence_is_visible(obj):
            return "offline"
        return "online" if self._presence_snapshot(obj)["is_online"] else "offline"

    def get_presence_visibility(self, obj) -> str:
        return "public" if self._presence_is_visible(obj) else "hidden"


class UserCompactSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="pk", read_only=True)
    display_name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "email", "display_name", "avatar")

    def get_display_name(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "display_name", "") or obj.get_full_name() or obj.username

    def get_avatar(self, obj) -> str | None:
        profile = getattr(obj, "profile", None)
        avatar = getattr(profile, "avatar", None)
        if not avatar:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(avatar.url) if request else avatar.url


class MessageEditHistorySerializer(serializers.ModelSerializer):
    edited_by = UserLiteSerializer(read_only=True)

    class Meta:
        model = MessageEditHistory
        fields = ("id", "previous_text", "new_text", "edited_by", "created_at")


class MessageAttachmentSerializer(serializers.ModelSerializer):
    media_kind = serializers.CharField(read_only=True)
    rotation = serializers.IntegerField(read_only=True)
    aspect_ratio = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    can_preview_inline = serializers.SerializerMethodField()
    signed_download = serializers.SerializerMethodField()
    signed_preview = serializers.SerializerMethodField()
    is_encrypted = serializers.SerializerMethodField()
    encryption = serializers.SerializerMethodField()

    class Meta:
        model = MessageAttachment
        fields = (
            "id",
            "media_kind",
            "original_name",
            "mime_type",
            "size",
            "width",
            "height",
            "rotation",
            "duration_seconds",
            "aspect_ratio",
            "metadata",
            "thumbnail_url",
            "scan_status",
            "scan_notes",
            "scanned_at",
            "file_url",
            "download_url",
            "preview_url",
            "can_preview_inline",
            "signed_download",
            "signed_preview",
            "is_encrypted",
            "encryption",
        )

    def get_file_url(self, obj) -> str:
        request = self.context.get("request")
        url = reverse("attachment-download", kwargs={"attachment_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_download_url(self, obj) -> str:
        return self.get_file_url(obj)

    def get_preview_url(self, obj) -> str:
        request = self.context.get("request")
        url = reverse("attachment-preview", kwargs={"attachment_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_thumbnail_url(self, obj) -> str | None:
        if not obj.thumbnail:
            return None
        request = self.context.get("request")
        url = reverse("attachment-thumbnail", kwargs={"attachment_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_aspect_ratio(self, obj) -> float | None:
        return _attachment_aspect_ratio(obj.width, obj.height)

    def get_metadata(self, obj) -> dict[str, Any]:
        return public_media_metadata(obj.metadata)

    def get_can_preview_inline(self, obj) -> bool:
        if (obj.metadata or {}).get("encrypted_attachment"):
            return False
        mime = (obj.mime_type or "").lower()
        return mime.startswith("image/") or mime.startswith("audio/") or mime.startswith("video/") or mime == "application/pdf"

    def get_is_encrypted(self, obj) -> bool:
        return bool((obj.metadata or {}).get("encrypted_attachment"))

    def get_encryption(self, obj) -> dict[str, Any] | None:
        metadata = obj.metadata or {}
        return metadata.get("encryption") if metadata.get("encrypted_attachment") else None

    def _get_media_actor(self):
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
        if getattr(request_user, "is_authenticated", False):
            return request_user
        actor = self.context.get("actor")
        if getattr(actor, "is_authenticated", False):
            return actor
        return None

    def get_signed_download(self, obj) -> dict[str, Any] | None:
        request = self.context.get("request")
        actor = self._get_media_actor()
        if not actor:
            return None
        return create_media_access_payload(actor=actor, resource_type="attachment", resource_id=obj.id, request=request, disposition="attachment")

    def get_signed_preview(self, obj) -> dict[str, Any] | None:
        request = self.context.get("request")
        actor = self._get_media_actor()
        if not actor:
            return None
        return create_media_access_payload(actor=actor, resource_type="attachment", resource_id=obj.id, request=request, disposition="inline")


class MessageAttachmentPreviewSerializer(serializers.ModelSerializer):
    media_kind = serializers.CharField(read_only=True)
    aspect_ratio = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = MessageAttachment
        fields = (
            "id",
            "media_kind",
            "original_name",
            "mime_type",
            "size",
            "width",
            "height",
            "rotation",
            "duration_seconds",
            "aspect_ratio",
            "metadata",
            "thumbnail_url",
            "download_url",
            "preview_url",
        )

    def get_download_url(self, obj) -> str:
        request = self.context.get("request")
        url = reverse("attachment-download", kwargs={"attachment_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_preview_url(self, obj) -> str:
        request = self.context.get("request")
        url = reverse("attachment-preview", kwargs={"attachment_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_thumbnail_url(self, obj) -> str | None:
        if not obj.thumbnail:
            return None
        request = self.context.get("request")
        url = reverse("attachment-thumbnail", kwargs={"attachment_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_aspect_ratio(self, obj) -> float | None:
        return _attachment_aspect_ratio(obj.width, obj.height)

    def get_metadata(self, obj) -> dict[str, Any]:
        return public_media_metadata(obj.metadata)


class PendingUploadSerializer(serializers.ModelSerializer):
    media_kind = serializers.CharField(read_only=True)
    width = serializers.IntegerField(read_only=True)
    height = serializers.IntegerField(read_only=True)
    rotation = serializers.IntegerField(read_only=True)
    duration_seconds = serializers.DecimalField(read_only=True, max_digits=10, decimal_places=2)
    aspect_ratio = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    can_preview_inline = serializers.SerializerMethodField()
    signed_download = serializers.SerializerMethodField()
    signed_preview = serializers.SerializerMethodField()

    class Meta:
        model = PendingUpload
        fields = (
            "id",
            "media_kind",
            "original_name",
            "mime_type",
            "size",
            "extension",
            "width",
            "height",
            "rotation",
            "duration_seconds",
            "aspect_ratio",
            "metadata",
            "status",
            "scan_status",
            "scan_notes",
            "scanned_at",
            "expires_at",
            "file_url",
            "download_url",
            "preview_url",
            "thumbnail_url",
            "can_preview_inline",
            "signed_download",
            "signed_preview",
            "created_at",
        )
        read_only_fields = fields

    def get_file_url(self, obj) -> str:
        request = self.context.get("request")
        url = reverse("pending-upload-download", kwargs={"upload_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_download_url(self, obj) -> str:
        return self.get_file_url(obj)

    def get_preview_url(self, obj) -> str:
        request = self.context.get("request")
        url = reverse("pending-upload-preview", kwargs={"upload_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_thumbnail_url(self, obj) -> str | None:
        if not obj.thumbnail:
            return None
        request = self.context.get("request")
        url = reverse("pending-upload-thumbnail", kwargs={"upload_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_aspect_ratio(self, obj) -> float | None:
        return _attachment_aspect_ratio(obj.width, obj.height)

    def get_metadata(self, obj) -> dict[str, Any]:
        return public_media_metadata(obj.metadata)

    def get_can_preview_inline(self, obj) -> bool:
        mime = (obj.mime_type or "").lower()
        return mime.startswith("image/") or mime.startswith("audio/") or mime.startswith("video/") or mime == "application/pdf"

    def get_signed_download(self, obj) -> dict[str, Any] | None:
        request = self.context.get("request")
        actor = getattr(getattr(request, "user", None), "is_authenticated", False) and request.user or None
        if not actor:
            return None
        return create_media_access_payload(actor=actor, resource_type="pending_upload", resource_id=obj.id, request=request, disposition="attachment")

    def get_signed_preview(self, obj) -> dict[str, Any] | None:
        request = self.context.get("request")
        actor = getattr(getattr(request, "user", None), "is_authenticated", False) and request.user or None
        if not actor:
            return None
        return create_media_access_payload(actor=actor, resource_type="pending_upload", resource_id=obj.id, request=request, disposition="inline")


class MessageReactionSerializer(serializers.ModelSerializer):
    user = UserLiteSerializer(read_only=True)

    class Meta:
        model = MessageReaction
        fields = ("id", "emoji", "user", "created_at")


class MessageDeliverySerializer(serializers.ModelSerializer):
    user = UserLiteSerializer(read_only=True)

    class Meta:
        model = MessageDelivery
        fields = ("id", "user", "delivered_at")


class MessageReplyPreviewSerializer(serializers.ModelSerializer):
    sender = UserLiteSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ("id", "text", "type", "sender", "is_deleted", "created_at")


class MessageReplyPreviewCompactSerializer(serializers.ModelSerializer):
    sender = UserCompactSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ("id", "text", "type", "sender", "is_deleted", "created_at")




class MessageTranscriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageTranscript
        fields = ("status", "language_code", "text", "confidence", "source", "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")


class MessageEntitySerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["bold", "italic", "underline", "strike", "code", "link", "mention"])
    offset = serializers.IntegerField(min_value=Decimal("0"))
    length = serializers.IntegerField(min_value=1)
    url = serializers.CharField(required=False, allow_blank=True)
    user_id = serializers.CharField(required=False)
    username = serializers.CharField(required=False, allow_blank=True)


class MessageEncryptedKeySerializer(serializers.Serializer):
    key_id = serializers.CharField(max_length=256)
    wrapped_key = serializers.CharField(max_length=getattr(settings, "MESSAGE_MAX_CIPHERTEXT_BYTES", 256 * 1024))


class AttachmentEncryptionEnvelopeSerializer(serializers.Serializer):
    upload_id = serializers.UUIDField(required=False)
    version = serializers.CharField(required=False, allow_blank=True, max_length=32)
    algorithm = serializers.CharField(max_length=80)
    nonce = serializers.CharField(max_length=256)
    sender_key_id = serializers.CharField(max_length=256)
    sender_device_id = serializers.CharField(required=False, allow_blank=True, max_length=256)
    key_version = serializers.IntegerField(required=False, min_value=1)
    recipient_key_ids = serializers.ListField(child=serializers.CharField(max_length=256), allow_empty=False, required=False)
    encrypted_keys = MessageEncryptedKeySerializer(many=True, required=False)
    metadata_ciphertext = serializers.CharField(max_length=getattr(settings, "MESSAGE_MAX_CIPHERTEXT_BYTES", 256 * 1024))
    metadata_nonce = serializers.CharField(max_length=256)
    original_sha256 = serializers.CharField(required=False, allow_blank=True, max_length=128)
    preview_ciphertext = serializers.CharField(required=False, allow_blank=True, max_length=getattr(settings, "MESSAGE_MAX_CIPHERTEXT_BYTES", 256 * 1024))
    preview_nonce = serializers.CharField(required=False, allow_blank=True, max_length=256)
    preview_mime_type = serializers.CharField(required=False, allow_blank=True, max_length=120)
    aad = serializers.JSONField(required=False)

    def validate(self, attrs):
        recipient_key_ids = attrs.get("recipient_key_ids") or []
        encrypted_keys = attrs.get("encrypted_keys") or []
        if not recipient_key_ids and not encrypted_keys:
            raise serializers.ValidationError("At least one recipient key entry is required.")
        if encrypted_keys and not recipient_key_ids:
            attrs["recipient_key_ids"] = [item["key_id"] for item in encrypted_keys if item.get("key_id")]
        preview_ciphertext = str(attrs.get("preview_ciphertext") or "").strip()
        preview_nonce = str(attrs.get("preview_nonce") or "").strip()
        if bool(preview_ciphertext) != bool(preview_nonce):
            raise serializers.ValidationError("Encrypted attachment previews require both ciphertext and nonce.")
        if preview_ciphertext and not str(attrs.get("preview_mime_type") or "").lower().startswith("image/"):
            raise serializers.ValidationError("Encrypted attachment previews must use an image MIME type.")
        return attrs


class MessageEncryptionEnvelopeSerializer(serializers.Serializer):
    version = serializers.CharField(required=False, allow_blank=True, max_length=32)
    algorithm = serializers.CharField(max_length=80)
    ciphertext = serializers.CharField(max_length=getattr(settings, "MESSAGE_MAX_CIPHERTEXT_BYTES", 256 * 1024))
    nonce = serializers.CharField(max_length=256)
    sender_key_id = serializers.CharField(max_length=256)
    sender_device_id = serializers.CharField(required=False, allow_blank=True, max_length=256)
    key_version = serializers.IntegerField(required=False, min_value=1)
    recipient_key_ids = serializers.ListField(child=serializers.CharField(max_length=256), allow_empty=False, required=False)
    encrypted_keys = MessageEncryptedKeySerializer(many=True, required=False)
    aad = serializers.JSONField(required=False)

    def validate(self, attrs):
        recipient_key_ids = attrs.get("recipient_key_ids") or []
        encrypted_keys = attrs.get("encrypted_keys") or []
        if not recipient_key_ids and not encrypted_keys:
            raise serializers.ValidationError("At least one recipient key entry is required.")
        if encrypted_keys and not recipient_key_ids:
            attrs["recipient_key_ids"] = [item["key_id"] for item in encrypted_keys if item.get("key_id")]
        return attrs


class MessageSerializer(serializers.ModelSerializer):
    sender = UserLiteSerializer(read_only=True)
    forwarded_from = serializers.SerializerMethodField()
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    reactions = MessageReactionSerializer(many=True, read_only=True)
    deliveries = MessageDeliverySerializer(many=True, read_only=True)
    edit_history = MessageEditHistorySerializer(many=True, read_only=True)
    reaction_summary = serializers.SerializerMethodField()
    voice_note = serializers.SerializerMethodField()
    transcript = MessageTranscriptSerializer(read_only=True)
    entities = serializers.SerializerMethodField()
    links = serializers.SerializerMethodField()
    mentioned_user_ids = serializers.SerializerMethodField()
    is_encrypted = serializers.SerializerMethodField()
    encryption = serializers.SerializerMethodField()
    reply_preview = MessageReplyPreviewSerializer(source="reply_to", read_only=True)
    reply_to_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Message
        fields = (
            "id",
            "conversation",
            "sender",
            "type",
            "text",
            "metadata",
            "reply_to",
            "reply_to_id",
            "forwarded_from",
            "is_edited",
            "edited_at",
            "is_deleted",
            "deleted_at",
            "client_temp_id",
            "delivery_status",
            "failed_reason",
            "retry_count",
            "attachments",
            "reactions",
            "deliveries",
            "edit_history",
            "reaction_summary",
            "voice_note",
            "transcript",
            "entities",
            "links",
            "mentioned_user_ids",
            "is_encrypted",
            "encryption",
            "reply_preview",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "conversation",
            "sender",
            "reply_to",
            "forwarded_from",
            "is_edited",
            "edited_at",
            "is_deleted",
            "deleted_at",
            "attachments",
            "reactions",
            "deliveries",
            "edit_history",
            "reaction_summary",
            "voice_note",
            "transcript",
            "entities",
            "links",
            "mentioned_user_ids",
            "is_encrypted",
            "encryption",
            "reply_preview",
            "created_at",
            "updated_at",
        )

    def get_voice_note(self, obj) -> dict[str, Any] | None:
        metadata = obj.metadata or {}
        if obj.type != Message.MessageType.AUDIO and not metadata.get("voice_note"):
            return None
        return {
            "is_voice_note": bool(metadata.get("voice_note", obj.type == Message.MessageType.AUDIO)),
            "duration_seconds": metadata.get("duration_seconds"),
            "waveform": metadata.get("waveform", []),
            "transcript_available": bool(getattr(obj, "transcript", None) and getattr(obj.transcript, "text", "")),
        }

    def get_forwarded_from(self, obj) -> str | None:
        return str(obj.forwarded_from_id) if obj.forwarded_from_id else None

    def get_reaction_summary(self, obj) -> list[dict[str, Any]]:
        counts = {}
        reaction_qs = getattr(obj, "reactions", None)
        if reaction_qs is None:
            return []
        for reaction in reaction_qs.all():
            emoji = getattr(reaction, "emoji", "")
            if not emoji:
                continue
            counts[emoji] = counts.get(emoji, 0) + 1
        return [{"emoji": emoji, "count": count} for emoji, count in sorted(counts.items())]

    def get_entities(self, obj) -> list[dict[str, Any]]:
        return (obj.metadata or {}).get("entities", [])

    def get_links(self, obj) -> list[dict[str, Any]]:
        return (obj.metadata or {}).get("links", [])

    def get_mentioned_user_ids(self, obj) -> list[str]:
        return (obj.metadata or {}).get("mentioned_user_ids", [])

    @extend_schema_field(serializers.BooleanField)
    def get_is_encrypted(self, obj):
        return bool((obj.metadata or {}).get("encrypted"))

    @extend_schema_field(MessageEncryptionEnvelopeSerializer(allow_null=True))
    def get_encryption(self, obj):
        metadata = obj.metadata or {}
        return metadata.get("encryption") if metadata.get("encrypted") else None


class ParticipantSerializer(serializers.ModelSerializer):
    user = UserLiteSerializer(read_only=True)

    class Meta:
        model = ConversationParticipant
        fields = (
            "id",
            "user",
            "role",
            "joined_at",
            "left_at",
            "is_muted",
            "is_archived",
            "is_pinned",
            "is_blocked",
            "last_read_message",
            "last_read_at",
            "last_delivered_message",
            "last_delivered_at",
            "moderation_muted_until",
            "banned_at",
            "ban_reason",
        )


class ParticipantPreviewSerializer(serializers.ModelSerializer):
    user = UserCompactSerializer(read_only=True)

    class Meta:
        model = ConversationParticipant
        fields = (
            "id",
            "user",
            "role",
            "joined_at",
            "left_at",
            "is_muted",
            "is_archived",
            "is_pinned",
            "is_blocked",
        )


class MessagePreviewSerializer(serializers.ModelSerializer):
    sender = UserCompactSerializer(read_only=True)
    forwarded_from = serializers.SerializerMethodField()
    attachments = MessageAttachmentPreviewSerializer(many=True, read_only=True)
    reaction_summary = serializers.SerializerMethodField()
    voice_note = serializers.SerializerMethodField()
    entities = serializers.SerializerMethodField()
    links = serializers.SerializerMethodField()
    mentioned_user_ids = serializers.SerializerMethodField()
    is_encrypted = serializers.SerializerMethodField()
    encryption = serializers.SerializerMethodField()
    reply_preview = MessageReplyPreviewCompactSerializer(source="reply_to", read_only=True)

    class Meta:
        model = Message
        fields = (
            "id",
            "conversation",
            "sender",
            "type",
            "text",
            "metadata",
            "reply_to",
            "forwarded_from",
            "is_edited",
            "edited_at",
            "is_deleted",
            "deleted_at",
            "client_temp_id",
            "delivery_status",
            "failed_reason",
            "retry_count",
            "attachments",
            "reaction_summary",
            "voice_note",
            "entities",
            "links",
            "mentioned_user_ids",
            "is_encrypted",
            "encryption",
            "reply_preview",
            "created_at",
            "updated_at",
        )

    def get_voice_note(self, obj) -> dict[str, Any] | None:
        metadata = obj.metadata or {}
        if obj.type != Message.MessageType.AUDIO and not metadata.get("voice_note"):
            return None
        return {
            "is_voice_note": bool(metadata.get("voice_note", obj.type == Message.MessageType.AUDIO)),
            "duration_seconds": metadata.get("duration_seconds"),
            "waveform": metadata.get("waveform", []),
            "transcript_available": bool(getattr(obj, "transcript", None) and getattr(obj.transcript, "text", "")),
        }

    def get_forwarded_from(self, obj) -> str | None:
        return str(obj.forwarded_from_id) if obj.forwarded_from_id else None

    def get_reaction_summary(self, obj) -> list[dict[str, Any]]:
        counts = {}
        reaction_qs = getattr(obj, "reactions", None)
        if reaction_qs is None:
            return []
        for reaction in reaction_qs.all():
            emoji = getattr(reaction, "emoji", "")
            if not emoji:
                continue
            counts[emoji] = counts.get(emoji, 0) + 1
        return [{"emoji": emoji, "count": count} for emoji, count in sorted(counts.items())]

    def get_entities(self, obj) -> list[dict[str, Any]]:
        return (obj.metadata or {}).get("entities", [])

    def get_links(self, obj) -> list[dict[str, Any]]:
        return (obj.metadata or {}).get("links", [])

    def get_mentioned_user_ids(self, obj) -> list[str]:
        return (obj.metadata or {}).get("mentioned_user_ids", [])

    @extend_schema_field(serializers.BooleanField)
    def get_is_encrypted(self, obj):
        return bool((obj.metadata or {}).get("encrypted"))

    @extend_schema_field(MessageEncryptionEnvelopeSerializer(allow_null=True))
    def get_encryption(self, obj):
        metadata = obj.metadata or {}
        return metadata.get("encryption") if metadata.get("encrypted") else None


class ConversationListSerializer(serializers.ModelSerializer):
    participants = ParticipantPreviewSerializer(many=True, read_only=True)
    last_message = MessagePreviewSerializer(read_only=True)
    active_participant_count = serializers.IntegerField(read_only=True)
    unread_count = serializers.IntegerField(read_only=True)
    draft = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = (
            "id",
            "type",
            "title",
            "avatar",
            "e2ee_key_version",
            "e2ee_rekey_required",
            "e2ee_last_key_rotation_at",
            "e2ee_last_security_event_at",
            "last_message",
            "last_message_at",
            "participants",
            "active_participant_count",
            "unread_count",
            "draft",
            "created_at",
        )

    def get_draft(self, obj) -> dict[str, Any] | None:
        if conversation_has_e2ee_enabled_participants(obj):
            return None
        drafts = getattr(obj, "viewer_drafts", None)
        draft = drafts[0] if drafts else None
        if draft is None:
            return None
        return ConversationDraftSerializer(draft, context=self.context).data

class ConversationDetailSerializer(serializers.ModelSerializer):
    participants = ParticipantSerializer(many=True, read_only=True)
    last_message = MessageSerializer(read_only=True)
    active_participant_count = serializers.IntegerField(read_only=True)
    unread_count = serializers.IntegerField(read_only=True)
    draft = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = (
            "id",
            "type",
            "title",
            "avatar",
            "created_by",
            "is_active",
            "e2ee_key_version",
            "e2ee_rekey_required",
            "e2ee_last_key_rotation_at",
            "e2ee_last_security_event_at",
            "last_message",
            "last_message_at",
            "participants",
            "active_participant_count",
            "unread_count",
            "draft",
            "created_at",
            "updated_at",
        )

    def get_draft(self, obj) -> dict[str, Any] | None:
        if conversation_has_e2ee_enabled_participants(obj):
            return None
        drafts = getattr(obj, "viewer_drafts", None)
        draft = drafts[0] if drafts else None
        if draft is None:
            return None
        return ConversationDraftSerializer(draft, context=self.context).data

class ConversationCreateSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Conversation.ConversationType.choices)
    title = serializers.CharField(required=False, allow_blank=True, max_length=100)
    participant_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)

    def validate(self, attrs):
        if attrs.get("title") is not None:
            attrs["title"] = sanitize_chat_text(attrs.get("title"), max_length=100)
        convo_type = attrs["type"]
        participant_ids = attrs["participant_ids"]
        if len(participant_ids) != len(set(participant_ids)):
            raise serializers.ValidationError({"participant_ids": "Choose each participant only once."})
        if convo_type == Conversation.ConversationType.DIRECT and len(participant_ids) != 1:
            raise serializers.ValidationError({"participant_ids": "Direct conversation requires exactly one other user."})
        if convo_type == Conversation.ConversationType.GROUP:
            title = attrs.get("title", "").strip()
            if not title:
                raise serializers.ValidationError({"title": "Group title is required."})
            if len(title) < 2 or not any(character.isalnum() for character in title):
                raise serializers.ValidationError({"title": "Use at least two letters or numbers in the group title."})
        return attrs


class ParticipantManageSerializer(serializers.Serializer):
    participant_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)

    def validate_participant_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Choose each participant only once.")
        return value


class ParticipantRoleUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=((ConversationParticipant.Role.MEMBER, "Member"), (ConversationParticipant.Role.ADMIN, "Admin")))


class OwnershipTransferSerializer(serializers.Serializer):
    target_user_id = serializers.IntegerField()


class MessageCreateSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Message.MessageType.choices, default=Message.MessageType.TEXT)
    text = serializers.CharField(required=False, allow_blank=True)
    reply_to_id = serializers.UUIDField(required=False, allow_null=True)
    client_temp_id = serializers.CharField(required=False, allow_blank=True)
    attachment_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True)
    attachment_encryption = AttachmentEncryptionEnvelopeSerializer(many=True, required=False)
    entities = MessageEntitySerializer(many=True, required=False)
    is_encrypted = serializers.BooleanField(required=False, default=False)
    encryption = MessageEncryptionEnvelopeSerializer(required=False)
    is_voice_note = serializers.BooleanField(required=False, default=False)
    duration_seconds = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=2)
    waveform = serializers.ListField(child=serializers.IntegerField(min_value=0, max_value=100), required=False, allow_empty=True)
    transcript_text = serializers.CharField(required=False, allow_blank=True)
    transcript_language_code = serializers.CharField(required=False, allow_blank=True, max_length=16)
    transcript_confidence = serializers.DecimalField(required=False, allow_null=True, max_digits=5, decimal_places=2)

    def validate(self, attrs):
        if attrs.get("text") is not None:
            attrs["text"] = sanitize_chat_text(attrs.get("text"), max_length=20000, multiline=True)
        if attrs.get("is_voice_note"):
            attrs["type"] = Message.MessageType.AUDIO
        if attrs.get("attachment_encryption") and not attrs.get("attachment_ids"):
            raise serializers.ValidationError({"attachment_encryption": "Encrypted attachment metadata requires attachment uploads."})
        if attrs.get("is_encrypted") or attrs.get("encryption"):
            attrs["is_encrypted"] = True
            if not attrs.get("encryption"):
                raise serializers.ValidationError({"encryption": "Encryption envelope is required when is_encrypted is true."})
            if attrs.get("text"):
                raise serializers.ValidationError({"text": "Encrypted messages must not include plaintext text."})
            if attrs.get("entities"):
                raise serializers.ValidationError({"entities": "Encrypted messages cannot include plaintext formatting entities."})
            if any(attrs.get(key) for key in ("transcript_text", "transcript_language_code", "transcript_confidence")):
                raise serializers.ValidationError({"transcript_text": "Encrypted messages cannot include plaintext transcripts."})
            attrs["text"] = ""
        return attrs


class MessageUpdateSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True)
    entities = MessageEntitySerializer(many=True, required=False)
    is_encrypted = serializers.BooleanField(required=False, default=False)
    encryption = MessageEncryptionEnvelopeSerializer(required=False)

    def validate_text(self, value):
        return sanitize_chat_text(value, max_length=20000, multiline=True)

    def validate(self, attrs):
        if attrs.get("is_encrypted") or attrs.get("encryption"):
            attrs["is_encrypted"] = True
            if not attrs.get("encryption"):
                raise serializers.ValidationError({"encryption": "Encryption envelope is required for an encrypted edit."})
            if attrs.get("text"):
                raise serializers.ValidationError({"text": "Encrypted edits must not include plaintext text."})
            if attrs.get("entities"):
                raise serializers.ValidationError({"entities": "Encrypted edits cannot include plaintext formatting entities."})
            attrs["text"] = ""
            return attrs
        if "text" not in attrs:
            raise serializers.ValidationError({"text": "Message text is required."})
        return attrs


class MessageForwardSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField()
    client_temp_id = serializers.CharField(required=False, allow_blank=True)


class MessageFailureSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class MessageTranscriptUpsertSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True)
    language_code = serializers.CharField(required=False, allow_blank=True, max_length=16)
    confidence = serializers.DecimalField(required=False, allow_null=True, max_digits=5, decimal_places=2)
    status = serializers.ChoiceField(required=False, choices=MessageTranscript.Status.choices)
    source = serializers.ChoiceField(required=False, choices=MessageTranscript.Source.choices)


class GroupParticipantMuteSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=1, max_value=60 * 24 * 30)


class GroupParticipantBanSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_reason(self, value):
        return sanitize_chat_text(value, max_length=255)


class UploadCreateSerializer(serializers.Serializer):
    file = serializers.FileField()
    original_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    mime_type = serializers.CharField(required=False, allow_blank=True, max_length=255)
    media_kind = serializers.ChoiceField(choices=PendingUpload.MediaKind.choices, required=False)
    width = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=100000)
    height = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=100000)
    rotation = serializers.IntegerField(required=False, allow_null=True)
    duration_seconds = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=2)
    thumbnail = serializers.FileField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False)

    def validate_file(self, value):
        original_name = (self.initial_data.get("original_name") or getattr(value, "name", "") or "").strip()
        raw_mime = (
            self.initial_data.get("mime_type")
            or getattr(value, "content_type", "")
            or ""
        )
        extension = _extension_for_upload_validation(original_name, value.name, raw_mime)
        normalized_mime = _normalize_reported_mime(raw_mime, extension)
        if extension not in settings.ALLOWED_UPLOAD_EXTENSIONS and not _is_quarantinable_upload(extension, normalized_mime):
            raise serializers.ValidationError("File extension is not allowed.")
        if value.size > settings.MAX_UPLOAD_BYTES:
            raise serializers.ValidationError("File exceeds the maximum allowed size.")
        if not _is_allowed_upload_mime(extension, normalized_mime) and not _is_quarantinable_upload(extension, normalized_mime):
            raise serializers.ValidationError("File MIME type is not allowed for this extension.")

        if original_name:
            value.name = original_name
        if normalized_mime:
            value.content_type = normalized_mime
        return value

    def validate_rotation(self, value):
        if value is None:
            return None
        normalized = int(value) % 360
        if normalized not in {0, 90, 180, 270}:
            raise serializers.ValidationError("Rotation must be one of 0, 90, 180, or 270 degrees.")
        return normalized

    def validate_thumbnail(self, value):
        if value is None:
            return None
        if getattr(value, "size", 0) > MEDIA_THUMBNAIL_MAX_BYTES:
            raise serializers.ValidationError("Thumbnail exceeds the maximum allowed size.")
        content_type = (getattr(value, "content_type", "") or "").lower()
        if content_type and not content_type.startswith("image/"):
            raise serializers.ValidationError("Thumbnail must use an image MIME type.")
        position = value.tell() if hasattr(value, "tell") else None
        try:
            raw_bytes = value.read()
            if not raw_bytes:
                raise serializers.ValidationError("Thumbnail file is empty.")
            image = Image.open(BytesIO(raw_bytes))
            image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise serializers.ValidationError("Thumbnail must be a valid image.") from exc
        finally:
            try:
                if hasattr(value, "seek"):
                    value.seek(position or 0)
            except Exception:
                pass
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        width = attrs.get("width")
        height = attrs.get("height")
        if (width is None) ^ (height is None):
            raise serializers.ValidationError({"width": "Width and height must be provided together."})
        if not attrs.get("media_kind"):
            attrs["media_kind"] = _media_kind_from_mime(
                self.initial_data.get("mime_type")
                or getattr(attrs.get("file"), "content_type", "")
                or ""
            )
        attrs["metadata"] = sanitize_media_metadata(attrs.get("metadata"))
        return attrs


class ReactionSerializer(serializers.Serializer):
    emoji = serializers.CharField(max_length=32)


class DeliverySerializer(serializers.Serializer):
    message_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class MessageReportSerializer(serializers.ModelSerializer):
    reporter = UserLiteSerializer(read_only=True)

    class Meta:
        model = MessageReport
        fields = ("id", "message", "reporter", "reason", "details", "created_at", "updated_at")
        read_only_fields = ("id", "message", "reporter", "created_at", "updated_at")


class E2EEDeviceKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = UserE2EEDeviceKey
        fields = ("id", "device_id", "key_id", "label", "algorithm", "fingerprint", "public_key_jwk", "is_active", "revoked_at", "last_seen_at", "created_at", "updated_at")
        read_only_fields = fields


class E2EEDeviceKeyUpsertSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=128)
    key_id = serializers.CharField(max_length=256)
    label = serializers.CharField(max_length=120, required=False, allow_blank=True)
    algorithm = serializers.CharField(max_length=80)
    public_key_jwk = serializers.JSONField()


class ConversationE2EEKeyMaterialSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField()
    key_version = serializers.IntegerField(min_value=1)
    rekey_required = serializers.BooleanField()
    last_key_rotation_at = serializers.DateTimeField(allow_null=True, required=False)
    last_security_event_at = serializers.DateTimeField(allow_null=True, required=False)
    participants = serializers.JSONField()


class UserBlockSerializer(serializers.ModelSerializer):
    blocked = UserLiteSerializer(read_only=True)
    blocked_user_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = UserBlock
        fields = ("id", "blocked", "blocked_user_id", "reason", "created_at")
        read_only_fields = ("id", "blocked", "created_at")


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = ("id", "platform", "push_token", "is_active", "last_seen_at", "created_at")
        read_only_fields = ("id", "is_active", "last_seen_at", "created_at")


class DeviceUpsertSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=(("android", "Android"), ("web", "Web"), ("ios", "iOS")))
    push_token = serializers.CharField(max_length=512)


class DeviceDeactivateSerializer(serializers.Serializer):
    push_token = serializers.CharField(max_length=512)


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = (
            "id",
            "push_enabled",
            "message_preview_enabled",
            "mute_all",
            "call_quality_preference",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class CallParticipantSerializer(serializers.ModelSerializer):
    user = UserLiteSerializer(read_only=True)

    class Meta:
        model = CallParticipant
        fields = ("id", "user", "state", "network_quality", "preferred_video_quality", "audio_enabled", "video_enabled", "is_on_hold", "reconnecting", "connection_state", "audio_route", "screen_share_enabled", "screen_share_started_at", "raised_hand_at", "is_speaking", "speaking_level", "last_spoke_at", "last_heartbeat_at", "packet_loss_pct", "jitter_ms", "round_trip_time_ms", "bitrate_kbps", "frame_rate", "quality_score", "quality_alert", "invited_at", "joined_at", "left_at")


class CallSessionSerializer(serializers.ModelSerializer):
    initiated_by = UserLiteSerializer(read_only=True)
    answered_by = UserLiteSerializer(read_only=True)
    participants = CallParticipantSerializer(many=True, read_only=True)
    duration_seconds = serializers.SerializerMethodField()
    ringing_seconds = serializers.SerializerMethodField()
    ring_timeout_seconds = serializers.SerializerMethodField()
    call_state = serializers.SerializerMethodField()
    participant_summary = serializers.SerializerMethodField()
    network_recommendation = serializers.SerializerMethodField()

    class Meta:
        model = CallSession
        fields = (
            "id",
            "conversation",
            "initiated_by",
            "answered_by",
            "call_type",
            "status",
            "room_key",
            "started_at",
            "answered_at",
            "ended_at",
            "last_signal_at",
            "ended_reason",
            "metadata",
            "participants",
            "duration_seconds",
            "ringing_seconds",
            "ring_timeout_seconds",
            "call_state",
            "participant_summary",
            "network_recommendation",
        )

    def get_duration_seconds(self, obj) -> int:
        end_time = obj.ended_at or timezone.now()
        start_time = obj.answered_at or obj.started_at
        if not start_time:
            return 0
        return max(int((end_time - start_time).total_seconds()), 0)

    def get_ringing_seconds(self, obj) -> int:
        if obj.status not in {CallSession.Status.INITIATED, CallSession.Status.RINGING}:
            return 0
        if not obj.started_at:
            return 0
        return max(int((timezone.now() - obj.started_at).total_seconds()), 0)

    def get_ring_timeout_seconds(self, obj) -> int:
        return int(getattr(settings, "CALL_OFFER_TIMEOUT_SECONDS", 45) or 45)

    def get_call_state(self, obj) -> str:
        status_value = (obj.status or "").lower()
        if status_value in {"ended", "declined", "missed", "failed"}:
            return status_value
        participants = list(obj.participants.all())
        request = self.context.get("request")
        actor_id = str(getattr(getattr(request, "user", None), "id", "") or "")
        actor_participant = next((p for p in participants if str(p.user_id) == actor_id), None)
        remote_participants = [p for p in participants if str(p.user_id) != actor_id]
        remote_ringing = [p for p in remote_participants if p.state == CallParticipant.State.RINGING]
        remote_joined = [p for p in remote_participants if p.state == CallParticipant.State.JOINED]
        remote_online = [p for p in remote_participants if is_user_online(p.user_id)]

        if status_value == CallSession.Status.ONGOING:
            return "ongoing" if remote_joined or obj.conversation.type == Conversation.ConversationType.GROUP else "connecting"
        if actor_participant and actor_participant.state == CallParticipant.State.RINGING and str(obj.initiated_by_id) != actor_id:
            return "incoming"
        if remote_ringing and not remote_online:
            return "calling_offline"
        if remote_ringing:
            return "ringing"
        if status_value in {"initiated", "ringing"}:
            return "calling"
        return status_value or "unknown"

    def get_participant_summary(self, obj) -> dict[str, int]:
        counts = obj.participants.values("state").annotate(count=Count("id"))
        summary = {item["state"]: item["count"] for item in counts}
        participant_user_ids = list(obj.participants.values_list("user_id", flat=True))
        online_count = sum(1 for user_id in participant_user_ids if is_user_online(user_id))
        summary["online"] = online_count
        summary["offline"] = max(len(participant_user_ids) - online_count, 0)
        return summary

    def get_network_recommendation(self, obj) -> dict[str, Any]:
        from apps.chat.services import get_call_network_recommendation
        return get_call_network_recommendation(obj)


class CallStartSerializer(serializers.Serializer):
    call_type = serializers.ChoiceField(choices=CallSession.CallType.choices)
    metadata = serializers.JSONField(required=False)


class CallActionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class RecentCallQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=CallSession.Status.choices, required=False)


class CallSignalSerializer(serializers.Serializer):
    signal_type = serializers.ChoiceField(choices=(
        ("offer", "offer"),
        ("answer", "answer"),
        ("ice_candidate", "ice_candidate"),
        ("renegotiate", "renegotiate"),
        ("hangup", "hangup"),
        ("busy", "busy"),
        ("ice_restart", "ice_restart"),
        ("network_state", "network_state"),
        ("quality_update", "quality_update"),
        ("media_toggle", "media_toggle"),
        ("speaker_hint", "speaker_hint"),
        ("fallback_audio_only", "fallback_audio_only"),
        ("receiver_report", "receiver_report"),
        ("request_keyframe", "request_keyframe"),
    ))
    payload = serializers.JSONField(required=False)

    def validate(self, attrs):
        payload = dict(attrs.get("payload") or {})
        signal_id = str(payload.get("signal_id") or "").strip()
        if signal_id:
            payload["signal_id"] = signal_id[:128]
        to_user_id = str(payload.get("to_user_id") or "").strip()
        if to_user_id:
            payload["to_user_id"] = to_user_id
        attrs["payload"] = payload
        return attrs


class CallHeartbeatSerializer(serializers.Serializer):
    network_quality = serializers.ChoiceField(choices=CallParticipant.NetworkQuality.choices, required=False)
    metrics = serializers.JSONField(required=False)


class CallQualityReportSerializer(serializers.Serializer):
    packet_loss_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, min_value=Decimal("0"))
    jitter_ms = serializers.IntegerField(required=False, min_value=0)
    round_trip_time_ms = serializers.IntegerField(required=False, min_value=0)
    bitrate_kbps = serializers.IntegerField(required=False, min_value=0)
    frame_rate = serializers.IntegerField(required=False, min_value=0)
    network_quality = serializers.ChoiceField(choices=CallParticipant.NetworkQuality.choices, required=False)
    preferred_video_quality = serializers.ChoiceField(choices=CallParticipant.VideoPreference.choices, required=False)
    audio_enabled = serializers.BooleanField(required=False)
    video_enabled = serializers.BooleanField(required=False)
    microphone_enabled = serializers.BooleanField(required=False, write_only=True)
    camera_enabled = serializers.BooleanField(required=False, write_only=True)
    diagnostics = serializers.JSONField(required=False)

    def validate(self, attrs):
        if "audio_enabled" not in attrs and "microphone_enabled" in attrs:
            attrs["audio_enabled"] = attrs.pop("microphone_enabled")
        else:
            attrs.pop("microphone_enabled", None)
        if "video_enabled" not in attrs and "camera_enabled" in attrs:
            attrs["video_enabled"] = attrs.pop("camera_enabled")
        else:
            attrs.pop("camera_enabled", None)
        return attrs


class CallMediaStateSerializer(serializers.Serializer):
    audio_enabled = serializers.BooleanField(required=False)
    video_enabled = serializers.BooleanField(required=False)
    microphone_enabled = serializers.BooleanField(required=False, write_only=True)
    camera_enabled = serializers.BooleanField(required=False, write_only=True)
    is_on_hold = serializers.BooleanField(required=False)
    reconnecting = serializers.BooleanField(required=False)
    screen_share_enabled = serializers.BooleanField(required=False)
    screen_sharing = serializers.BooleanField(required=False, write_only=True)
    hand_raised = serializers.BooleanField(required=False)
    connection_state = serializers.ChoiceField(choices=CallParticipant.ConnectionState.choices, required=False)
    audio_route = serializers.ChoiceField(choices=CallParticipant.AudioRoute.choices, required=False)
    preferred_video_quality = serializers.ChoiceField(choices=CallParticipant.VideoPreference.choices, required=False)
    diagnostics = serializers.JSONField(required=False)
    bitrate_kbps = serializers.IntegerField(required=False, min_value=0)
    packet_loss_ratio = serializers.DecimalField(required=False, allow_null=True, max_digits=6, decimal_places=4, min_value=Decimal("0"))
    latency_ms = serializers.IntegerField(required=False, min_value=0)

    def validate(self, attrs):
        if "audio_enabled" not in attrs and "microphone_enabled" in attrs:
            attrs["audio_enabled"] = attrs.pop("microphone_enabled")
        else:
            attrs.pop("microphone_enabled", None)
        if "video_enabled" not in attrs and "camera_enabled" in attrs:
            attrs["video_enabled"] = attrs.pop("camera_enabled")
        else:
            attrs.pop("camera_enabled", None)
        if "screen_share_enabled" not in attrs and "screen_sharing" in attrs:
            attrs["screen_share_enabled"] = attrs.pop("screen_sharing")
        else:
            attrs.pop("screen_sharing", None)
        diagnostics = dict(attrs.get("diagnostics") or {})
        for key in ("bitrate_kbps", "packet_loss_ratio", "latency_ms"):
            if key in attrs:
                diagnostics[key] = attrs[key]
        if diagnostics:
            attrs["diagnostics"] = diagnostics
        return attrs


class CallSpeakerStateSerializer(serializers.Serializer):
    speaking_level = serializers.IntegerField(required=False, min_value=0, max_value=100)
    is_speaking = serializers.BooleanField(required=False)


class TurnCredentialsSerializer(serializers.Serializer):
    configured = serializers.BooleanField()
    ttl_seconds = serializers.IntegerField()
    username = serializers.CharField(required=False, allow_blank=True)
    credential = serializers.CharField(required=False, allow_blank=True)
    credential_type = serializers.CharField(required=False, allow_blank=True)
    ice_servers = serializers.ListField()


class CallDiagnosticsSerializer(serializers.Serializer):
    call_id = serializers.UUIDField()
    status = serializers.CharField()
    participant_count = serializers.IntegerField()
    joined_count = serializers.IntegerField()
    active_count = serializers.IntegerField()
    stale_participant_user_ids = serializers.ListField(child=serializers.CharField())
    network_recommendation = serializers.JSONField()
    recovery_plan = serializers.JSONField(required=False)
    aggregate_quality = serializers.JSONField(required=False)
    orchestration = serializers.JSONField(required=False)
    last_signal_at = serializers.CharField(allow_null=True)
    participants = serializers.ListField()


class CallOrchestrationSerializer(serializers.Serializer):
    call_id = serializers.UUIDField()
    conversation_id = serializers.UUIDField()
    active_speaker_user_id = serializers.CharField(allow_null=True)
    primary_content_user_id = serializers.CharField(allow_null=True)
    layout_mode = serializers.CharField()
    network_recommendation = serializers.JSONField()
    recommended_video_quality = serializers.CharField()
    recommended_max_video_streams = serializers.IntegerField()
    recommend_audio_only = serializers.BooleanField()
    recovery_plan = serializers.JSONField(required=False)
    participant_speaking_user_ids = serializers.ListField(child=serializers.CharField())
    raised_hand_user_ids = serializers.ListField(child=serializers.CharField(), required=False)
    signals = serializers.ListField(required=False)
    generated_at = serializers.CharField()
    participants = serializers.ListField()


class CallingConfigSerializer(serializers.Serializer):
    ice_servers = serializers.ListField()
    offer_timeout_seconds = serializers.IntegerField()
    max_group_call_participants = serializers.IntegerField()
    ice_transport_policy = serializers.CharField()
    ice_candidate_pool_size = serializers.IntegerField()
    enable_simulcast = serializers.BooleanField()
    prefer_audio_only_below_quality = serializers.CharField()
    reconnect_grace_seconds = serializers.IntegerField()
    quality_report_interval_seconds = serializers.IntegerField()
    dominant_speaker_hold_ms = serializers.IntegerField()
    speaker_level_threshold = serializers.IntegerField()
    grid_layout_threshold = serializers.IntegerField()
    supported_audio_routes = serializers.ListField(child=serializers.CharField())
    screen_share = serializers.JSONField()
    network_profiles = serializers.JSONField()
    codec_preferences = serializers.JSONField()
    available_quality_presets = serializers.JSONField()
    selected_quality_preset = serializers.CharField()
    applied_quality_profile = serializers.JSONField()
    quality_reporting = serializers.JSONField(required=False, default=dict)


class SyncQuerySerializer(serializers.Serializer):
    since = serializers.DateTimeField(required=False)
    conversation_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=200, default=100)


class ModerationActionSerializer(serializers.ModelSerializer):
    actor = UserLiteSerializer(read_only=True)

    class Meta:
        model = ModerationAction
        fields = ("id", "action_type", "notes", "actor", "message", "report", "created_at")


class ModerationResolveSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True)
    hide_message = serializers.BooleanField(required=False, default=False)


class ModerationDismissSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True)


class MessageRestoreSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True)


class MediaTokenSerializer(serializers.Serializer):
    resource_type = serializers.ChoiceField(choices=(("attachment", "attachment"), ("pending_upload", "pending_upload")))


class ChatAuditLogSerializer(serializers.ModelSerializer):
    actor = UserLiteSerializer(read_only=True)

    class Meta:
        model = ChatAuditLog
        fields = ("id", "event_type", "actor", "conversation", "message", "metadata", "created_at")


class IntegrationHealthSerializer(serializers.Serializer):
    antivirus = serializers.DictField()
    push = serializers.DictField()


class ChatCapabilitiesSerializer(serializers.Serializer):
    version = serializers.CharField()
    features = serializers.DictField()
    limits = serializers.DictField()
    media = serializers.DictField()
    calls = serializers.DictField()
    security = serializers.DictField()



class ConversationNotificationSettingSerializer(serializers.ModelSerializer):
    is_currently_muted = serializers.SerializerMethodField()

    class Meta:
        model = ConversationNotificationSetting
        fields = (
            "id",
            "conversation",
            "message_notifications_enabled",
            "call_notifications_enabled",
            "mentions_only",
            "muted_until",
            "is_currently_muted",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "conversation", "is_currently_muted", "created_at", "updated_at")

    def get_is_currently_muted(self, obj) -> bool:
        return bool(obj.muted_until and obj.muted_until > timezone.now())


class ConversationDraftSerializer(serializers.ModelSerializer):
    conversation = serializers.SerializerMethodField()
    reply_to = MessageReplyPreviewSerializer(read_only=True)
    reply_to_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    has_draft = serializers.SerializerMethodField()

    class Meta:
        model = ConversationDraft
        fields = (
            "id",
            "conversation",
            "text",
            "reply_to",
            "reply_to_id",
            "metadata",
            "has_draft",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "conversation", "reply_to", "has_draft", "created_at", "updated_at")

    def get_conversation(self, obj) -> str:
        return str(obj.conversation_id)

    def get_has_draft(self, obj) -> bool:
        return bool(getattr(obj, "pk", None)) and not obj._state.adding

    def validate_text(self, value):
        return sanitize_chat_text(value, max_length=10000, multiline=True)

    def validate(self, attrs):
        conversation = self.context.get("conversation") or getattr(self.instance, "conversation", None)
        reply_to_id = attrs.get("reply_to_id", serializers.empty)
        if conversation and reply_to_id not in (serializers.empty, None):
            try:
                reply_to = Message.objects.get(id=reply_to_id, conversation=conversation, is_deleted=False)
            except Message.DoesNotExist as exc:
                raise serializers.ValidationError({"reply_to_id": "Reply target must belong to this conversation."}) from exc
            attrs["reply_to"] = reply_to
        elif reply_to_id is None:
            attrs["reply_to"] = None
        attrs.pop("reply_to_id", None)
        metadata = attrs.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise serializers.ValidationError({"metadata": "Draft metadata must be an object."})
        return attrs


class ConversationInviteLinkSerializer(serializers.ModelSerializer):
    is_active = serializers.SerializerMethodField()
    created_by = UserLiteSerializer(read_only=True)
    join_url = serializers.SerializerMethodField()

    class Meta:
        model = ConversationInviteLink
        fields = (
            "id",
            "conversation",
            "created_by",
            "token",
            "expires_at",
            "revoked_at",
            "max_uses",
            "use_count",
            "is_active",
            "join_url",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "conversation", "created_by", "token", "revoked_at", "use_count", "is_active", "join_url", "created_at", "updated_at")

    def get_is_active(self, obj) -> bool:
        return obj.is_active

    def get_join_url(self, obj) -> str:
        request = self.context.get("request")
        path = reverse("conversation-invite-join") + f"?token={obj.token}"
        return request.build_absolute_uri(path) if request else path


class ConversationInviteCreateSerializer(serializers.Serializer):
    expires_in_hours = serializers.IntegerField(required=False, min_value=1, max_value=24 * 30)
    max_uses = serializers.IntegerField(required=False, min_value=0, max_value=10000)


class ConversationInviteJoinSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=128)


class ConversationMediaSerializer(MessageAttachmentSerializer):
    message = serializers.SerializerMethodField()
    sender = serializers.SerializerMethodField()
    media_kind = serializers.SerializerMethodField()

    class Meta(MessageAttachmentSerializer.Meta):
        fields = MessageAttachmentSerializer.Meta.fields + ("message", "sender", "media_kind", "created_at")

    def get_message(self, obj) -> dict[str, Any]:
        return {"id": str(obj.message_id), "text": obj.message.text, "type": obj.message.type, "created_at": obj.message.created_at}

    def get_sender(self, obj) -> dict[str, Any] | None:
        return UserLiteSerializer(obj.message.sender, context=self.context).data if getattr(obj.message, "sender", None) else None

    def get_media_kind(self, obj) -> str:
        mime = (obj.mime_type or "").lower()
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
        return "file"
