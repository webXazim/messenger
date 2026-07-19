from __future__ import annotations

from urllib.parse import urlsplit
import re

from django.conf import settings
from django.urls import reverse

from rest_framework import serializers

from apps.chat.models import Message, MessageAttachment, PendingUpload
from apps.support.conversation_services import team_unread_count, visitor_unread_count
from apps.support.realtime import visitor_is_online
from apps.support.feedback_services import feedback_settings_for, survey_for_conversation
from apps.support.service_operations import (
    SupportServiceConfigurationError,
    normalize_service_settings_payload,
    service_snapshot,
)
from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportAgentInvitation,
    SupportConversation,
    SupportMessageAuthor,
    SupportPendingUpload,
    SupportTag,
    SupportConversationTag,
    SupportInternalNote,
    SupportCannedReply,
    SupportSavedInboxView,
    SupportAuditEvent,
    SupportServiceAlert,
    SupportServiceSettings,
    SupportFeedbackSettings,
    SupportCSATSurvey,
    SupportKnowledgeSettings,
    SupportKnowledgeCategory,
    SupportKnowledgeArticle,
    SupportPrivacySettings,
    SupportWebhookEndpoint,
    SupportWebhookDelivery,
    SupportDataExport,
    SupportVisitorDeletionRequest,
    SupportVisitor,
    SupportWebsite,
    SupportWidgetSession,
    SupportWidgetSettings,
    SupportCallSettings,
)


def normalize_domain(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    return host


def normalize_origins(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, list):
        raise serializers.ValidationError("Allowed origins must be a list.")
    normalized: list[str] = []
    for value in values:
        raw = str(value or "").strip().rstrip("/")
        if not raw:
            continue
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise serializers.ValidationError(f"Invalid allowed origin: {raw}")
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        if origin not in normalized:
            normalized.append(origin)
    return normalized


def user_summary(user, *, include_email: bool = False):
    profile = getattr(user, "profile", None)
    payload = {
        "id": str(user.id),
        "username": user.username,
        "display_name": getattr(profile, "display_name", "") or user.get_full_name() or user.username,
        "avatar": profile.avatar.url if profile and profile.avatar else None,
    }
    if include_email:
        payload["email"] = user.email
    return payload


def mask_email(value: str) -> str:
    local, separator, domain = (value or "").partition("@")
    if not separator:
        return ""
    visible = local[:1]
    return f"{visible}{'*' * max(3, len(local) - 1)}@{domain}"


class SupportWebsiteSerializer(serializers.ModelSerializer):
    widget_settings = serializers.SerializerMethodField()
    install_code = serializers.SerializerMethodField()

    class Meta:
        model = SupportWebsite
        fields = (
            "id",
            "name",
            "domain",
            "site_key",
            "allowed_origins",
            "widget_enabled",
            "widget_settings",
            "install_code",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "site_key", "widget_settings", "install_code", "created_at", "updated_at")

    def get_widget_settings(self, obj):
        # Serializers must remain read-only. Website creation/bootstrap ensures
        # this relation exists; a defensive in-memory default avoids a hidden
        # write and an N+1 get_or_create during list serialization.
        try:
            widget_settings = obj.widget_settings
        except SupportWidgetSettings.DoesNotExist:
            widget_settings = SupportWidgetSettings(
                website=obj,
                brand_name=f"{obj.name} Support",
            )
        return SupportWidgetSettingsSerializer(widget_settings).data

    def get_install_code(self, obj):
        script_url = (getattr(settings, "SUPPORT_WIDGET_SCRIPT_URL", "") or "").strip()
        if not script_url:
            return ""
        return f'<script async src="{script_url}" data-support-site-key="{obj.site_key}"></script>'

    def validate_domain(self, value):
        domain = normalize_domain(value)
        if not domain or "." not in domain:
            raise serializers.ValidationError("Enter a valid website domain.")
        return domain

    def validate_allowed_origins(self, value):
        return normalize_origins(value)


class SupportAgentSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    assigned_website_ids = serializers.SerializerMethodField()

    class Meta:
        model = SupportAgent
        fields = (
            "id",
            "user",
            "availability",
            "max_active_conversations",
            "can_view_all_conversations",
            "can_assign_conversations",
            "can_view_analytics",
            "is_active",
            "assigned_website_ids",
            "joined_at",
        )

    def get_user(self, obj):
        return user_summary(obj.user, include_email=True)

    def get_assigned_website_ids(self, obj):
        assignments = getattr(obj, "prefetched_website_assignments", None)
        if assignments is None:
            assignments = obj.website_assignments.all()
        return [str(assignment.website_id) for assignment in assignments]


class SupportAgentInvitationSerializer(serializers.ModelSerializer):
    invited_by = serializers.SerializerMethodField()
    assigned_website_ids = serializers.SerializerMethodField()
    assigned_websites = serializers.SerializerMethodField()

    class Meta:
        model = SupportAgentInvitation
        fields = (
            "id",
            "email",
            "status",
            "expires_at",
            "last_sent_at",
            "send_count",
            "max_active_conversations",
            "can_view_all_conversations",
            "can_assign_conversations",
            "can_view_analytics",
            "invited_by",
            "assigned_website_ids",
            "assigned_websites",
            "created_at",
            "updated_at",
        )

    def get_invited_by(self, obj):
        return user_summary(obj.invited_by) if obj.invited_by else None

    def _website_assignments(self, obj):
        cache = getattr(obj, "_support_assignment_cache", None)
        if cache is None:
            cache = list(obj.website_assignments.all())
            obj._support_assignment_cache = cache
        return cache

    def get_assigned_website_ids(self, obj):
        return [str(assignment.website_id) for assignment in self._website_assignments(obj)]

    def get_assigned_websites(self, obj):
        return [
            {"id": str(assignment.website_id), "name": assignment.website.name, "domain": assignment.website.domain}
            for assignment in self._website_assignments(obj)
        ]


class SupportAgentInvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    website_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    max_active_conversations = serializers.IntegerField(min_value=1, max_value=100, default=5)
    can_view_all_conversations = serializers.BooleanField(default=False)
    can_assign_conversations = serializers.BooleanField(default=False)
    can_view_analytics = serializers.BooleanField(default=False)


class SupportAgentUpdateSerializer(serializers.Serializer):
    website_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=True, required=False, default=list)
    max_active_conversations = serializers.IntegerField(min_value=1, max_value=100)
    can_view_all_conversations = serializers.BooleanField()
    can_assign_conversations = serializers.BooleanField()
    can_view_analytics = serializers.BooleanField()


class SupportAgentAvailabilitySerializer(serializers.Serializer):
    availability = serializers.ChoiceField(choices=SupportAgent.Availability.choices)


class SupportInvitationTokenSerializer(serializers.Serializer):
    token = serializers.CharField(min_length=20, max_length=512, trim_whitespace=True)


class SupportAccountSerializer(serializers.ModelSerializer):
    owner = serializers.SerializerMethodField()
    access_active = serializers.BooleanField(source="has_product_access", read_only=True)

    class Meta:
        model = SupportAccount
        fields = (
            "id",
            "owner",
            "status",
            "plan_code",
            "website_limit",
            "agent_limit",
            "current_period_end",
            "grace_ends_at",
            "access_active",
            "created_at",
            "updated_at",
        )

    def get_owner(self, obj):
        return user_summary(obj.owner)


def invitation_preview_payload(invitation: SupportAgentInvitation) -> dict:
    return {
        "valid": invitation.is_active and invitation.support_account.has_product_access,
        "status": invitation.status,
        "invited_email": mask_email(invitation.email),
        "inviter": user_summary(invitation.invited_by) if invitation.invited_by else None,
        "websites": [
            {"id": str(assignment.website_id), "name": assignment.website.name, "domain": assignment.website.domain}
            for assignment in invitation.website_assignments.all()
        ],
        "expires_at": invitation.expires_at,
        "account_access_active": invitation.support_account.has_product_access,
    }


class SupportWidgetSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportWidgetSettings
        fields = (
            "brand_name",
            "primary_color",
            "welcome_text",
            "offline_text",
            "launcher_text",
            "privacy_note",
            "position",
            "theme",
            "require_name",
            "require_email",
            "allow_attachments",
            "allow_audio_calls",
            "allow_video_calls",
            "updated_at",
        )
        read_only_fields = ("updated_at",)

    def validate_primary_color(self, value):
        color = (value or "").strip().lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", color):
            raise serializers.ValidationError("Use a six-digit hex color such as #111111.")
        return color


class PublicWidgetConfigSerializer(serializers.Serializer):
    site_key = serializers.UUIDField()
    website_name = serializers.CharField()
    brand_name = serializers.CharField()
    primary_color = serializers.CharField()
    welcome_text = serializers.CharField()
    offline_text = serializers.CharField()
    launcher_text = serializers.CharField()
    privacy_note = serializers.CharField(allow_blank=True)
    position = serializers.ChoiceField(choices=SupportWidgetSettings.Position.choices)
    theme = serializers.ChoiceField(choices=SupportWidgetSettings.Theme.choices)
    require_name = serializers.BooleanField()
    require_email = serializers.BooleanField()
    allow_attachments = serializers.BooleanField()
    allow_audio_calls = serializers.BooleanField()
    allow_video_calls = serializers.BooleanField()
    calls_enabled = serializers.BooleanField()
    session_enabled = serializers.BooleanField()
    messaging_enabled = serializers.BooleanField()
    knowledge_enabled = serializers.BooleanField()
    knowledge_suggestions_enabled = serializers.BooleanField()
    visitor_deletion_enabled = serializers.BooleanField()


class SupportWidgetSessionCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False, allow_blank=True, trim_whitespace=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    locale = serializers.CharField(max_length=32, required=False, allow_blank=True, trim_whitespace=True)
    current_page_url = serializers.URLField(max_length=1000, required=False, allow_blank=True)
    referrer = serializers.URLField(max_length=1000, required=False, allow_blank=True)


class SupportWidgetSessionUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False, allow_blank=True, trim_whitespace=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    locale = serializers.CharField(max_length=32, required=False, allow_blank=True, trim_whitespace=True)
    current_page_url = serializers.URLField(max_length=1000, required=False, allow_blank=True)
    referrer = serializers.URLField(max_length=1000, required=False, allow_blank=True)


class SupportVisitorSerializer(serializers.ModelSerializer):
    is_online = serializers.SerializerMethodField()

    def get_is_online(self, obj):
        online_map = self.context.get("support_visitor_online_map")
        if online_map is not None:
            return bool(online_map.get(str(obj.id), False))
        return visitor_is_online(obj.id)

    class Meta:
        model = SupportVisitor
        fields = ("id", "external_id", "name", "email", "locale", "current_page_url", "referrer", "last_seen_at", "is_online")
        read_only_fields = fields


class SupportWidgetSessionSerializer(serializers.ModelSerializer):
    visitor = SupportVisitorSerializer(read_only=True)
    token = serializers.CharField(write_only=False, required=False)

    class Meta:
        model = SupportWidgetSession
        fields = (
            "id",
            "visitor",
            "status",
            "origin",
            "expires_at",
            "last_seen_at",
            "current_page_url",
            "referrer",
            "token_version",
            "token",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        raw_token = self.context.get("raw_token")
        if raw_token:
            representation["token"] = raw_token
        else:
            representation.pop("token", None)
        return representation


class SupportWebsiteWidgetConfigurationSerializer(serializers.Serializer):
    allowed_origins = serializers.ListField(child=serializers.CharField(max_length=500), allow_empty=True)
    widget_enabled = serializers.BooleanField()
    settings = SupportWidgetSettingsSerializer()

    def validate_allowed_origins(self, value):
        return normalize_origins(value)


class SupportMessageSendSerializer(serializers.Serializer):
    client_temp_id = serializers.CharField(max_length=100, required=False, allow_blank=True, trim_whitespace=True)
    text = serializers.CharField(max_length=10000, required=False, allow_blank=True, trim_whitespace=True)
    attachment_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        max_length=max(1, int(getattr(settings, "SUPPORT_MAX_ATTACHMENTS_PER_MESSAGE", 8) or 8)),
    )
    voice_note = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        text = (attrs.get("text") or "").strip()
        attachment_ids = attrs.get("attachment_ids") or []
        voice_note = bool(attrs.get("voice_note"))
        if not text and not attachment_ids:
            raise serializers.ValidationError("Write a message or add an attachment before sending.")
        if voice_note and len(attachment_ids) != 1:
            raise serializers.ValidationError({"attachment_ids": "A voice message requires exactly one audio upload."})
        attrs["text"] = text
        attrs["attachment_ids"] = attachment_ids
        return attrs


class SupportConversationUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SupportConversation.Status.choices, required=False)
    priority = serializers.ChoiceField(choices=SupportConversation.Priority.choices, required=False)
    assigned_agent_id = serializers.UUIDField(required=False, allow_null=True)
    follow_up_at = serializers.DateTimeField(required=False, allow_null=True)
    follow_up_note = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")


class SupportPendingUploadSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    media_kind = serializers.CharField(read_only=True)
    original_name = serializers.CharField(read_only=True)
    mime_type = serializers.CharField(read_only=True)
    size = serializers.IntegerField(read_only=True)
    width = serializers.IntegerField(read_only=True, allow_null=True)
    height = serializers.IntegerField(read_only=True, allow_null=True)
    rotation = serializers.IntegerField(read_only=True, allow_null=True)
    duration_seconds = serializers.DecimalField(read_only=True, allow_null=True, max_digits=10, decimal_places=2)
    status = serializers.CharField(read_only=True)
    scan_status = serializers.CharField(read_only=True)
    scan_notes = serializers.CharField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)


class SupportAttachmentSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    media_kind = serializers.CharField(read_only=True)
    original_name = serializers.CharField(read_only=True)
    mime_type = serializers.CharField(read_only=True)
    size = serializers.IntegerField(read_only=True)
    width = serializers.IntegerField(read_only=True, allow_null=True)
    height = serializers.IntegerField(read_only=True, allow_null=True)
    rotation = serializers.IntegerField(read_only=True, allow_null=True)
    duration_seconds = serializers.DecimalField(read_only=True, allow_null=True, max_digits=10, decimal_places=2)
    scan_status = serializers.CharField(read_only=True)
    can_preview_inline = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    def _route(self, kind: str, obj: MessageAttachment) -> str:
        request = self.context.get("request")
        site_key = self.context.get("widget_site_key")
        session_id = self.context.get("widget_session_id")
        if site_key and session_id:
            route_name = {
                "download": "support:support-widget-attachment-download",
                "preview": "support:support-widget-attachment-preview",
                "thumbnail": "support:support-widget-attachment-thumbnail",
            }[kind]
            url = reverse(route_name, kwargs={
                "site_key": site_key,
                "session_id": session_id,
                "attachment_id": obj.id,
            })
        else:
            route_name = {
                "download": "support:support-attachment-download",
                "preview": "support:support-attachment-preview",
                "thumbnail": "support:support-attachment-thumbnail",
            }[kind]
            url = reverse(route_name, kwargs={"attachment_id": obj.id})
        return request.build_absolute_uri(url) if request else url

    def get_download_url(self, obj):
        return self._route("download", obj)

    def get_preview_url(self, obj):
        return self._route("preview", obj) if self.get_can_preview_inline(obj) else None

    def get_thumbnail_url(self, obj):
        return self._route("thumbnail", obj) if obj.thumbnail else None

    def get_can_preview_inline(self, obj):
        mime = (obj.mime_type or "").lower()
        return mime.startswith(("image/", "audio/", "video/")) or mime == "application/pdf"


class SupportMessageSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    client_temp_id = serializers.CharField(read_only=True)
    type = serializers.CharField(read_only=True)
    text = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    delivery_status = serializers.CharField(read_only=True)
    receipt_status = serializers.SerializerMethodField()
    delivered_at = serializers.SerializerMethodField()
    read_at = serializers.SerializerMethodField()
    sender = serializers.SerializerMethodField()
    is_own = serializers.SerializerMethodField()
    voice_note = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    preview_text = serializers.SerializerMethodField()

    def _receipt_snapshot(self):
        cached = self.context.get("_support_receipt_snapshot")
        if cached is not None:
            return cached
        conversation = self.context.get("support_conversation")
        if not conversation:
            cached = {}
        else:
            states = getattr(conversation, "prefetched_read_states", None)
            if states is None:
                states = list(
                    conversation.read_states.select_related(
                        "last_delivered_message",
                        "last_read_message",
                    )
                )
            team_delivered = [
                state
                for state in states
                if state.last_delivered_message_id and state.last_delivered_at
            ]
            team_read = [
                state
                for state in states
                if state.last_read_message_id and state.last_read_at
            ]
            cached = {
                "visitor_delivered_message": getattr(conversation, "visitor_last_delivered_message", None),
                "visitor_delivered_at": getattr(conversation, "visitor_last_delivered_at", None),
                "visitor_read_message": getattr(conversation, "visitor_last_read_message", None),
                "visitor_read_at": getattr(conversation, "visitor_last_read_at", None),
                "team_delivered_message": max(
                    (state.last_delivered_message for state in team_delivered),
                    key=lambda item: item.created_at,
                    default=None,
                ),
                "team_delivered_at": max(
                    (state.last_delivered_at for state in team_delivered),
                    default=None,
                ),
                "team_read_message": max(
                    (state.last_read_message for state in team_read),
                    key=lambda item: item.created_at,
                    default=None,
                ),
                "team_read_at": max(
                    (state.last_read_at for state in team_read),
                    default=None,
                ),
            }
        self.context["_support_receipt_snapshot"] = cached
        return cached

    def _receipt_values(self, obj):
        snapshot = self._receipt_snapshot()
        prefix = "visitor" if obj.sender_id else "team"
        delivered_message = snapshot.get(f"{prefix}_delivered_message")
        read_message = snapshot.get(f"{prefix}_read_message")
        delivered = bool(delivered_message and delivered_message.created_at >= obj.created_at)
        read = bool(read_message and read_message.created_at >= obj.created_at)
        return {
            "status": "read" if read else "delivered" if delivered else obj.delivery_status,
            "delivered_at": snapshot.get(f"{prefix}_delivered_at") if delivered else None,
            "read_at": snapshot.get(f"{prefix}_read_at") if read else None,
        }

    def get_receipt_status(self, obj):
        return self._receipt_values(obj)["status"]

    def get_delivered_at(self, obj):
        return self._receipt_values(obj)["delivered_at"]

    def get_read_at(self, obj):
        return self._receipt_values(obj)["read_at"]

    def get_voice_note(self, obj: Message):
        return bool((obj.metadata or {}).get("voice_note"))

    def get_attachments(self, obj: Message):
        return SupportAttachmentSerializer(
            obj.attachments.all(),
            many=True,
            context=self.context,
        ).data

    def get_preview_text(self, obj: Message):
        if obj.text:
            return obj.text
        attachments = list(obj.attachments.all())
        if not attachments:
            return "Support message"
        if (obj.metadata or {}).get("voice_note"):
            return "Voice message"
        if len(attachments) > 1:
            return f"{len(attachments)} attachments"
        attachment = attachments[0]
        return {
            MessageAttachment.MediaKind.IMAGE: "Photo",
            MessageAttachment.MediaKind.VIDEO: "Video",
            MessageAttachment.MediaKind.AUDIO: "Audio",
        }.get(attachment.media_kind, attachment.original_name or "File")

    def get_sender(self, obj: Message):
        if obj.sender_id:
            role = "agent"
            support_conversation = self.context.get("support_conversation")
            if support_conversation and obj.sender_id == support_conversation.website.support_account.owner_id:
                role = "owner"
            payload = user_summary(obj.sender)
            payload["kind"] = role
            return payload

        try:
            author = obj.support_author
        except SupportMessageAuthor.DoesNotExist:
            return {"kind": "system", "id": None, "display_name": "Support Chat", "avatar": None}
        return {
            "kind": "visitor",
            "id": str(author.visitor_id),
            "display_name": author.display_name or author.visitor.name or "Website visitor",
            "avatar": None,
        }

    def get_is_own(self, obj: Message):
        user = self.context.get("user")
        visitor = self.context.get("visitor")
        if user is not None:
            return bool(obj.sender_id and str(obj.sender_id) == str(user.id))
        if visitor is not None:
            try:
                return str(obj.support_author.visitor_id) == str(visitor.id)
            except SupportMessageAuthor.DoesNotExist:
                return False
        return False


class SupportConversationSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    website = serializers.SerializerMethodField()
    visitor = serializers.SerializerMethodField()
    assigned_agent = serializers.SerializerMethodField()
    status = serializers.CharField(read_only=True)
    priority = serializers.CharField(read_only=True)
    subject = serializers.CharField(read_only=True)
    first_response_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_visitor_message_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_agent_message_at = serializers.DateTimeField(read_only=True, allow_null=True)
    resolved_at = serializers.DateTimeField(read_only=True, allow_null=True)
    closed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    visitor_unread_count = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    service = serializers.SerializerMethodField()
    csat = serializers.SerializerMethodField()

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if self.context.get("visitor") is not None:
            representation.pop("service", None)
            representation.pop("csat", None)
        return representation

    def get_website(self, obj):
        return {"id": str(obj.website_id), "name": obj.website.name, "domain": obj.website.domain}

    def get_visitor(self, obj):
        return SupportVisitorSerializer(obj.visitor, context=self.context).data

    def get_assigned_agent(self, obj):
        return SupportAgentSerializer(obj.assigned_agent, context=self.context).data if obj.assigned_agent else None

    def get_last_message(self, obj):
        message = obj.conversation.last_message
        if not message:
            return None
        return SupportMessageSerializer(
            message,
            context={**self.context, "support_conversation": obj},
        ).data

    def get_unread_count(self, obj):
        prefetched = getattr(obj, "prefetched_team_unread_count", None)
        if prefetched is not None:
            return int(prefetched)
        user = self.context.get("user")
        return team_unread_count(obj, user) if user is not None else 0

    def get_visitor_unread_count(self, obj):
        prefetched = getattr(obj, "prefetched_visitor_unread_count", None)
        return int(prefetched) if prefetched is not None else visitor_unread_count(obj)

    def get_tags(self, obj):
        # Tags are private Support-team workflow metadata and must never be
        # disclosed to a website visitor through the public widget API.
        if self.context.get("visitor") is not None:
            return []
        assignments = getattr(obj, "prefetched_tag_assignments", None)
        if assignments is None:
            assignments = obj.tag_assignments.select_related("tag").filter(tag__is_active=True).order_by("tag__name")
        return SupportTagSerializer([item.tag for item in assignments], many=True).data

    def get_csat(self, obj):
        survey = survey_for_conversation(obj)
        return SupportCSATSurveySerializer(survey).data if survey else None

    def get_service(self, obj):
        settings_map = self.context.get("support_service_settings_map") or {}
        snapshot = service_snapshot(
            obj,
            settings_obj=settings_map.get(str(obj.website.support_account_id)),
        )
        date_field = serializers.DateTimeField(allow_null=True)
        for key in (
            "active_due_at", "first_response_due_at", "next_response_due_at",
            "resolution_due_at", "first_response_breached_at",
            "next_response_breached_at", "resolution_breached_at",
            "follow_up_at", "follow_up_completed_at",
        ):
            snapshot[key] = date_field.to_representation(snapshot.get(key)) if snapshot.get(key) else None
        snapshot["follow_up_created_by"] = (
            user_summary(obj.follow_up_created_by) if obj.follow_up_created_by_id else None
        )
        return snapshot


class SupportTagReadSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    color = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class SupportTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportTag
        fields = ("id", "name", "color", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("A tag name is required.")
        return value

    def validate_color(self, value):
        value = (value or "").strip().lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", value):
            raise serializers.ValidationError("Enter a valid six-digit hex color.")
        return value


class SupportInternalNoteSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()

    class Meta:
        model = SupportInternalNote
        fields = ("id", "body", "author", "created_at")
        read_only_fields = ("id", "author", "created_at")

    def validate_body(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Write a note before saving.")
        return value

    def get_author(self, obj):
        return user_summary(obj.author)


class SupportCannedReplyReadSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    website_id = serializers.UUIDField(read_only=True, allow_null=True)
    website_name = serializers.CharField(read_only=True, allow_null=True)
    shortcut = serializers.CharField(read_only=True)
    title = serializers.CharField(read_only=True)
    body = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class SupportCannedReplySerializer(serializers.ModelSerializer):
    website_id = serializers.UUIDField(source="website.id", read_only=True, allow_null=True)
    website_name = serializers.CharField(source="website.name", read_only=True, allow_null=True)

    class Meta:
        model = SupportCannedReply
        fields = (
            "id", "website_id", "website_name", "shortcut", "title", "body",
            "is_active", "created_at", "updated_at",
        )
        read_only_fields = ("id", "website_id", "website_name", "created_at", "updated_at")

    def validate_shortcut(self, value):
        value = (value or "").strip().lower()
        if not value.startswith("/"):
            value = f"/{value}"
        if not re.fullmatch(r"/[a-z0-9_-]{1,39}", value):
            raise serializers.ValidationError("Use a shortcut such as /hello without spaces.")
        return value

    def validate_title(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("A title is required.")
        return value

    def validate_body(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Reply text is required.")
        return value


class SupportCannedReplyWriteSerializer(serializers.Serializer):
    website_id = serializers.UUIDField(required=False, allow_null=True)
    shortcut = serializers.CharField(max_length=40)
    title = serializers.CharField(max_length=120)
    body = serializers.CharField(max_length=10000)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_shortcut(self, value):
        value = (value or "").strip().lower()
        if not value.startswith("/"):
            value = f"/{value}"
        if not re.fullmatch(r"/[a-z0-9_-]{1,39}", value):
            raise serializers.ValidationError("Use a shortcut such as /hello without spaces.")
        return value

    def validate_title(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("A title is required.")
        return value

    def validate_body(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Reply text is required.")
        return value


class SupportSavedInboxViewSerializer(serializers.ModelSerializer):
    website_id = serializers.UUIDField(source="website.id", read_only=True, allow_null=True)
    tag_id = serializers.UUIDField(source="tag.id", read_only=True, allow_null=True)

    class Meta:
        model = SupportSavedInboxView
        fields = (
            "id", "name", "website_id", "queue", "status", "priority", "tag_id",
            "search", "is_default", "created_at", "updated_at",
        )
        read_only_fields = ("id", "website_id", "tag_id", "created_at", "updated_at")


class SupportSavedInboxViewWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80)
    website_id = serializers.UUIDField(required=False, allow_null=True)
    queue = serializers.ChoiceField(
        choices=["", "open", "mine", "unassigned", "overdue", "follow_up", "resolved", "closed"],
        required=False,
        allow_blank=True,
        default="",
    )
    status = serializers.ChoiceField(
        choices=[""] + [value for value, _ in SupportConversation.Status.choices],
        required=False,
        allow_blank=True,
        default="",
    )
    priority = serializers.ChoiceField(
        choices=[""] + [value for value, _ in SupportConversation.Priority.choices],
        required=False,
        allow_blank=True,
        default="",
    )
    tag_id = serializers.UUIDField(required=False, allow_null=True)
    search = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    is_default = serializers.BooleanField(required=False, default=False)

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("A saved-view name is required.")
        return value

    def validate_search(self, value):
        return (value or "").strip()


class SupportConversationTagsUpdateSerializer(serializers.Serializer):
    tag_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=True,
        max_length=25,
    )


class SupportAuditEventSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()

    class Meta:
        model = SupportAuditEvent
        fields = (
            "id", "action", "summary", "target_type", "target_id", "metadata",
            "actor", "created_at",
        )
        read_only_fields = fields

    def get_actor(self, obj):
        return user_summary(obj.actor) if obj.actor else {
            "id": None,
            "username": "system",
            "display_name": "Support Chat",
            "avatar": None,
        }

class SupportServiceSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportServiceSettings
        fields = (
            "timezone", "business_hours_enabled", "business_hours",
            "first_response_targets", "next_response_targets",
            "resolution_targets", "due_soon_minutes",
            "default_follow_up_minutes", "alert_owner",
            "alert_assigned_agent", "updated_at",
        )
        read_only_fields = ("updated_at",)

    def validate(self, attrs):
        instance = self.instance
        current = {
            "timezone": getattr(instance, "timezone", "UTC"),
            "business_hours_enabled": getattr(instance, "business_hours_enabled", True),
            "business_hours": getattr(instance, "business_hours", None),
            "first_response_targets": getattr(instance, "first_response_targets", None),
            "next_response_targets": getattr(instance, "next_response_targets", None),
            "resolution_targets": getattr(instance, "resolution_targets", None),
            "due_soon_minutes": getattr(instance, "due_soon_minutes", 15),
            "default_follow_up_minutes": getattr(instance, "default_follow_up_minutes", 1440),
            "alert_owner": getattr(instance, "alert_owner", True),
            "alert_assigned_agent": getattr(instance, "alert_assigned_agent", True),
        }
        current.update(attrs)
        try:
            return normalize_service_settings_payload(current)
        except (SupportServiceConfigurationError, TypeError, ValueError) as exc:
            raise serializers.ValidationError(str(exc)) from exc


class SupportServiceAlertSerializer(serializers.ModelSerializer):
    conversation_id = serializers.UUIDField(source="support_conversation.id", read_only=True)
    website = serializers.SerializerMethodField()

    class Meta:
        model = SupportServiceAlert
        fields = (
            "id", "kind", "status", "due_at", "triggered_at", "read_at",
            "conversation_id", "website", "metadata",
        )
        read_only_fields = fields

    def get_website(self, obj):
        return {"id": str(obj.website_id), "name": obj.website.name, "domain": obj.website.domain}

class SupportFeedbackSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportFeedbackSettings
        fields = (
            "csat_enabled", "auto_request_on_resolve", "allow_comment",
            "survey_expiry_days", "updated_at",
        )
        read_only_fields = ("updated_at",)

    def validate_survey_expiry_days(self, value):
        if value < 1 or value > 365:
            raise serializers.ValidationError("Choose a value between 1 and 365 days.")
        return value


class SupportCSATSurveySerializer(serializers.ModelSerializer):
    available = serializers.BooleanField(source="is_available", read_only=True)
    allow_comment = serializers.SerializerMethodField()

    class Meta:
        model = SupportCSATSurvey
        fields = (
            "id", "status", "source", "rating", "comment", "available",
            "allow_comment", "requested_at", "expires_at", "submitted_at",
        )
        read_only_fields = fields

    def get_allow_comment(self, obj):
        return feedback_settings_for(obj.support_account).allow_comment


class SupportCSATSubmitSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")

    def validate_comment(self, value):
        return (value or "").strip()



class SupportKnowledgeSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportKnowledgeSettings
        fields = (
            "enabled", "show_in_widget", "suggestions_enabled",
            "allow_article_feedback", "max_suggestions", "updated_at",
        )
        read_only_fields = ("updated_at",)

    def validate_max_suggestions(self, value):
        if value < 1 or value > 10:
            raise serializers.ValidationError("Choose a value between 1 and 10.")
        return value


class SupportKnowledgeCategoryReadSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    sort_order = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    article_count = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class SupportKnowledgeCategorySerializer(serializers.ModelSerializer):
    article_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = SupportKnowledgeCategory
        fields = (
            "id", "name", "description", "sort_order", "is_active",
            "article_count", "created_at", "updated_at",
        )
        read_only_fields = ("id", "article_count", "created_at", "updated_at")

    def validate_name(self, value):
        normalized = (value or "").strip()
        if not normalized:
            raise serializers.ValidationError("Enter a category name.")
        return normalized


class SupportKnowledgeArticleSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)
    website_ids = serializers.SerializerMethodField()
    website_names = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()
    updated_by = serializers.SerializerMethodField()
    helpful_rate = serializers.SerializerMethodField()

    class Meta:
        model = SupportKnowledgeArticle
        fields = (
            "id", "category", "category_name", "title", "slug", "summary", "body",
            "status", "all_websites", "is_featured", "website_ids", "website_names",
            "published_at", "view_count", "helpful_count", "not_helpful_count",
            "helpful_rate", "created_by", "updated_by", "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "slug", "website_ids", "website_names", "published_at",
            "view_count", "helpful_count", "not_helpful_count", "helpful_rate",
            "created_by", "updated_by", "created_at", "updated_at",
        )

    def _website_assignments(self, obj):
        cache = getattr(obj, "_support_knowledge_assignment_cache", None)
        if cache is None:
            cache = list(obj.website_assignments.all())
            obj._support_knowledge_assignment_cache = cache
        return cache

    def get_website_ids(self, obj):
        return [str(assignment.website_id) for assignment in self._website_assignments(obj)]

    def get_website_names(self, obj):
        return [assignment.website.name for assignment in self._website_assignments(obj)]

    def get_created_by(self, obj):
        return user_summary(obj.created_by) if obj.created_by else None

    def get_updated_by(self, obj):
        return user_summary(obj.updated_by) if obj.updated_by else None

    def get_helpful_rate(self, obj):
        total = obj.helpful_count + obj.not_helpful_count
        return round((obj.helpful_count / total) * 100, 1) if total else None


class SupportKnowledgeArticleWriteSerializer(serializers.Serializer):
    category_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(max_length=180, trim_whitespace=True)
    summary = serializers.CharField(max_length=320, required=False, allow_blank=True, trim_whitespace=True)
    body = serializers.CharField(max_length=30000, trim_whitespace=True)
    status = serializers.ChoiceField(choices=SupportKnowledgeArticle.Status.choices, default=SupportKnowledgeArticle.Status.DRAFT)
    all_websites = serializers.BooleanField(default=True)
    website_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True, default=list)
    is_featured = serializers.BooleanField(default=False)

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("Enter an article title.")
        return value.strip()

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Enter the article content.")
        return value.strip()


class PublicKnowledgeCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportKnowledgeCategory
        fields = ("id", "name", "description")
        read_only_fields = fields


class PublicKnowledgeArticleSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    helpful_rate = serializers.SerializerMethodField()

    class Meta:
        model = SupportKnowledgeArticle
        fields = (
            "id", "title", "summary", "body", "category", "is_featured",
            "helpful_count", "not_helpful_count", "helpful_rate", "updated_at",
        )
        read_only_fields = fields

    def get_category(self, obj):
        if not obj.category_id:
            return None
        return {"id": str(obj.category_id), "name": obj.category.name}

    def get_helpful_rate(self, obj):
        total = obj.helpful_count + obj.not_helpful_count
        return round((obj.helpful_count / total) * 100, 1) if total else None


class PublicKnowledgeFeedbackSerializer(serializers.Serializer):
    helpful = serializers.BooleanField()
    client_key = serializers.CharField(min_length=16, max_length=200, trim_whitespace=True)


class SupportPrivacySettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportPrivacySettings
        fields = (
            "retention_enabled",
            "resolved_conversation_retention_days",
            "widget_session_retention_days",
            "export_retention_days",
            "allow_visitor_deletion_requests",
            "include_attachments_in_exports",
            "updated_at",
        )
        read_only_fields = ("updated_at",)


class SupportWebhookEndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportWebhookEndpoint
        fields = (
            "id", "name", "url", "event_types", "is_active", "failure_count",
            "last_delivery_at", "last_success_at", "last_failure_at", "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "failure_count", "last_delivery_at", "last_success_at", "last_failure_at",
            "created_at", "updated_at",
        )


class SupportWebhookDeliverySerializer(serializers.ModelSerializer):
    endpoint_name = serializers.CharField(source="endpoint.name", read_only=True)

    class Meta:
        model = SupportWebhookDelivery
        fields = (
            "id", "endpoint", "endpoint_name", "event_type", "event_id", "status",
            "attempt_count", "next_attempt_at", "response_status", "response_body", "error",
            "delivered_at", "created_at",
        )
        read_only_fields = fields


class SupportDataExportSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = SupportDataExport
        fields = (
            "id", "status", "file_size", "record_counts", "include_attachments",
            "started_at", "completed_at", "expires_at", "error", "download_url", "created_at",
        )
        read_only_fields = fields

    def get_download_url(self, obj):
        if obj.status != SupportDataExport.Status.READY or not obj.file:
            return None
        request = self.context.get("request")
        path = reverse("support:support-export-download", kwargs={"export_id": obj.id})
        return request.build_absolute_uri(path) if request else path


class SupportVisitorDeletionRequestSerializer(serializers.ModelSerializer):
    website_name = serializers.CharField(source="website.name", read_only=True)

    class Meta:
        model = SupportVisitorDeletionRequest
        fields = (
            "id", "website", "website_name", "visitor_external_id", "source", "status",
            "requested_at", "completed_at", "error",
        )
        read_only_fields = fields


class SupportCallSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportCallSettings
        fields = ("enabled", "allow_video", "max_duration_minutes", "updated_at")
        read_only_fields = ("updated_at",)

    def validate_max_duration_minutes(self, value):
        if not 5 <= int(value) <= 240:
            raise serializers.ValidationError("Choose a value between 5 and 240 minutes.")
        return value


class SupportCallStartSerializer(serializers.Serializer):
    call_type = serializers.ChoiceField(choices=("voice", "video"))


class SupportCallSignalWriteSerializer(serializers.Serializer):
    signal_type = serializers.ChoiceField(choices=(
        "offer", "answer", "ice_candidate", "renegotiate", "ice_restart",
        "hangup", "media_toggle", "network_state",
    ))
    payload = serializers.JSONField(default=dict)


class SupportCallMediaStateSerializer(serializers.Serializer):
    audio_enabled = serializers.BooleanField(required=False)
    video_enabled = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide audio_enabled or video_enabled.")
        return attrs
