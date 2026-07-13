from django.contrib import admin
from django.utils import timezone

from .models import (
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
    UserBlock,
    UserDevice,
    UserE2EEDeviceKey,
)


class UUIDAdminMixin:
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


class ConversationParticipantInline(admin.TabularInline):
    model = ConversationParticipant
    extra = 0
    raw_id_fields = ("user", "last_read_message", "last_delivered_message", "banned_by")
    readonly_fields = ("id", "joined_at")
    show_change_link = True


class MessageAttachmentInline(admin.TabularInline):
    model = MessageAttachment
    extra = 0
    readonly_fields = ("id", "created_at", "updated_at")
    show_change_link = True


class MessageReactionInline(admin.TabularInline):
    model = MessageReaction
    extra = 0
    raw_id_fields = ("user",)
    readonly_fields = ("id", "created_at")
    show_change_link = True


@admin.action(description="Activate selected conversations")
def activate_conversations(modeladmin, request, queryset):
    queryset.update(is_active=True)


@admin.action(description="Deactivate selected conversations")
def deactivate_conversations(modeladmin, request, queryset):
    queryset.update(is_active=False)


@admin.register(Conversation)
class ConversationAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("id", "type", "title", "created_by", "is_active", "last_message_at", "created_at")
    list_filter = ("type", "is_active", "e2ee_rekey_required", "created_at", "last_message_at")
    search_fields = ("id", "title", "direct_key", "created_by__username", "created_by__email")
    raw_id_fields = ("created_by", "last_message")
    inlines = (ConversationParticipantInline,)
    actions = (activate_conversations, deactivate_conversations)
    date_hierarchy = "created_at"


@admin.action(description="Mute selected participants")
def mute_participants(modeladmin, request, queryset):
    queryset.update(is_muted=True)


@admin.action(description="Unmute selected participants")
def unmute_participants(modeladmin, request, queryset):
    queryset.update(is_muted=False, moderation_muted_until=None)


@admin.action(description="Ban selected participants")
def ban_participants(modeladmin, request, queryset):
    queryset.filter(banned_at__isnull=True).update(banned_at=timezone.now(), is_blocked=True)


@admin.action(description="Unban selected participants")
def unban_participants(modeladmin, request, queryset):
    queryset.update(banned_at=None, is_blocked=False, ban_reason="")


@admin.register(ConversationParticipant)
class ConversationParticipantAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("conversation", "user", "role", "is_muted", "is_blocked", "left_at", "banned_at", "joined_at")
    list_filter = ("role", "is_muted", "is_archived", "is_pinned", "is_blocked", "banned_at", "joined_at")
    search_fields = ("conversation__id", "conversation__title", "user__username", "user__email", "ban_reason")
    raw_id_fields = ("conversation", "user", "last_read_message", "last_delivered_message", "banned_by")
    actions = (mute_participants, unmute_participants, ban_participants, unban_participants)


@admin.action(description="Mark selected messages as hidden/deleted")
def hide_messages(modeladmin, request, queryset):
    queryset.filter(is_deleted=False).update(is_deleted=True, deleted_at=timezone.now())


@admin.action(description="Restore selected messages")
def restore_messages(modeladmin, request, queryset):
    queryset.update(is_deleted=False, deleted_at=None)


@admin.register(Message)
class MessageAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "type", "delivery_status", "is_edited", "is_deleted", "created_at")
    list_filter = ("type", "delivery_status", "is_edited", "is_deleted", "created_at", "edited_at", "deleted_at")
    search_fields = ("id", "text", "conversation__id", "conversation__title", "sender__username", "sender__email")
    raw_id_fields = ("conversation", "sender", "reply_to", "forwarded_from")
    readonly_fields = UUIDAdminMixin.readonly_fields + ("edited_at", "deleted_at")
    inlines = (MessageAttachmentInline, MessageReactionInline)
    actions = (hide_messages, restore_messages)
    date_hierarchy = "created_at"


@admin.register(MessageAttachment)
class MessageAttachmentAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("original_name", "message", "media_kind", "mime_type", "size", "scan_status", "created_at")
    list_filter = ("media_kind", "scan_status", "mime_type", "created_at", "scanned_at")
    search_fields = ("original_name", "mime_type", "message__id", "message__text", "scan_notes")
    raw_id_fields = ("message",)


@admin.register(PendingUpload)
class PendingUploadAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("original_name", "user", "media_kind", "status", "scan_status", "size", "expires_at", "created_at")
    list_filter = ("media_kind", "status", "scan_status", "expires_at", "created_at")
    search_fields = ("original_name", "mime_type", "extension", "user__username", "user__email", "scan_notes")
    raw_id_fields = ("user",)


@admin.register(MessageReport)
class MessageReportAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("message", "reporter", "reason", "created_at")
    list_filter = ("reason", "created_at")
    search_fields = ("message__id", "message__text", "reporter__username", "reporter__email", "details")
    raw_id_fields = ("message", "reporter")


@admin.register(ModerationAction)
class ModerationActionAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("action_type", "actor", "message", "report", "created_at")
    list_filter = ("action_type", "created_at")
    search_fields = ("actor__username", "actor__email", "message__id", "report__id", "notes")
    raw_id_fields = ("report", "message", "actor")


@admin.register(UserBlock)
class UserBlockAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("blocker", "blocked", "reason", "created_at")
    list_filter = ("created_at",)
    search_fields = ("blocker__username", "blocker__email", "blocked__username", "blocked__email", "reason")
    raw_id_fields = ("blocker", "blocked")


@admin.register(CallSession)
class CallSessionAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("room_key", "conversation", "initiated_by", "call_type", "status", "started_at", "ended_at")
    list_filter = ("call_type", "status", "started_at", "ended_at")
    search_fields = ("room_key", "conversation__id", "conversation__title", "initiated_by__username", "initiated_by__email")
    raw_id_fields = ("conversation", "initiated_by", "answered_by")
    date_hierarchy = "started_at"


@admin.register(CallParticipant)
class CallParticipantAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = (
        "call",
        "user",
        "state",
        "network_quality",
        "connection_state",
        "audio_enabled",
        "video_enabled",
        "joined_at",
        "left_at",
    )
    list_filter = ("state", "network_quality", "connection_state", "audio_enabled", "video_enabled", "joined_at")
    search_fields = ("call__room_key", "user__username", "user__email", "quality_alert")
    raw_id_fields = ("call", "user")


@admin.register(UserDevice)
class UserDeviceAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("user", "platform", "is_active", "last_seen_at", "created_at")
    list_filter = ("platform", "is_active", "last_seen_at", "created_at")
    search_fields = ("user__username", "user__email", "platform", "push_token")
    raw_id_fields = ("user",)


@admin.register(UserE2EEDeviceKey)
class UserE2EEDeviceKeyAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("user", "device_id", "label", "algorithm", "fingerprint", "is_active", "last_seen_at")
    list_filter = ("algorithm", "is_active", "revoked_at", "last_seen_at")
    search_fields = ("user__username", "user__email", "device_id", "key_id", "label", "fingerprint")
    raw_id_fields = ("user",)
    readonly_fields = UUIDAdminMixin.readonly_fields + ("key_id", "last_seen_at")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("user", "push_enabled", "message_preview_enabled", "mute_all", "call_quality_preference")
    list_filter = ("push_enabled", "message_preview_enabled", "mute_all", "call_quality_preference")
    search_fields = ("user__username", "user__email")
    raw_id_fields = ("user",)


@admin.register(ConversationNotificationSetting)
class ConversationNotificationSettingAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("conversation", "user", "message_notifications_enabled", "call_notifications_enabled", "mentions_only", "muted_until")
    list_filter = ("message_notifications_enabled", "call_notifications_enabled", "mentions_only", "muted_until")
    search_fields = ("conversation__id", "conversation__title", "user__username", "user__email")
    raw_id_fields = ("conversation", "user")


@admin.register(ConversationDraft)
class ConversationDraftAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("conversation", "user", "updated_at")
    list_filter = ("updated_at", "created_at")
    search_fields = ("conversation__id", "conversation__title", "user__username", "user__email", "text")
    raw_id_fields = ("conversation", "user", "reply_to")


@admin.register(ConversationInviteLink)
class ConversationInviteLinkAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("conversation", "created_by", "is_active", "use_count", "max_uses", "expires_at", "revoked_at", "created_at")
    list_filter = ("expires_at", "revoked_at", "created_at")
    search_fields = ("conversation__id", "conversation__title", "created_by__username", "created_by__email", "token")
    raw_id_fields = ("conversation", "created_by")
    readonly_fields = UUIDAdminMixin.readonly_fields + ("token", "is_active")


@admin.register(MessageEditHistory)
class MessageEditHistoryAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("message", "edited_by", "created_at")
    list_filter = ("created_at",)
    search_fields = ("message__id", "edited_by__username", "edited_by__email", "previous_text", "new_text")
    raw_id_fields = ("message", "edited_by")


@admin.register(MessageTranscript)
class MessageTranscriptAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("message", "status", "language_code", "confidence", "source", "updated_at")
    list_filter = ("status", "source", "language_code", "updated_at")
    search_fields = ("message__id", "message__text", "text", "language_code")
    raw_id_fields = ("message",)


@admin.register(MessageReaction)
class MessageReactionAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("message", "user", "emoji", "created_at")
    list_filter = ("emoji", "created_at")
    search_fields = ("message__id", "message__text", "user__username", "user__email", "emoji")
    raw_id_fields = ("message", "user")


@admin.register(MessageDelivery)
class MessageDeliveryAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("message", "user", "delivered_at")
    list_filter = ("delivered_at",)
    search_fields = ("message__id", "message__text", "user__username", "user__email")
    raw_id_fields = ("message", "user")


@admin.register(ChatAuditLog)
class ChatAuditLogAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("event_type", "actor", "conversation", "message", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("actor__username", "actor__email", "conversation__id", "conversation__title", "message__id")
    raw_id_fields = ("actor", "conversation", "message")
    readonly_fields = UUIDAdminMixin.readonly_fields + ("event_type", "actor", "conversation", "message", "metadata")
