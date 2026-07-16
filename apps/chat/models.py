from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseUUIDModel
from apps.chat.storage import attachment_storage_factory, pending_upload_storage_factory


def pending_upload_expiry_default():
    ttl = int(getattr(settings, 'PENDING_UPLOAD_TTL_SECONDS', 86400) or 86400)
    return timezone.now() + timedelta(seconds=ttl)


def user_status_expiry_default():
    return timezone.now() + timedelta(hours=24)


class Conversation(BaseUUIDModel):
    class ConversationType(models.TextChoices):
        DIRECT = "direct", "Direct"
        GROUP = "group", "Group"

    type = models.CharField(max_length=20, choices=ConversationType.choices)
    title = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(max_length=120, unique=True, null=True, blank=True, allow_unicode=True)
    avatar = models.ImageField(upload_to="chat/conversation_avatars/", blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_conversations")
    is_active = models.BooleanField(default=True)
    direct_key = models.CharField(max_length=255, blank=True, unique=True, null=True)
    last_message = models.ForeignKey("Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_message_at = models.DateTimeField(null=True, blank=True)
    e2ee_key_version = models.PositiveIntegerField(default=1)
    e2ee_rekey_required = models.BooleanField(default=False)
    e2ee_last_key_rotation_at = models.DateTimeField(null=True, blank=True)
    e2ee_last_security_event_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        indexes = [models.Index(fields=["type", "last_message_at"])]

    def __str__(self):
        return self.title or f"{self.type}:{self.id}"


class ConversationParticipant(BaseUUIDModel):
    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        ADMIN = "admin", "Admin"
        OWNER = "owner", "Owner"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_participations")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)
    is_blocked = models.BooleanField(default=False)
    last_read_message = models.ForeignKey("Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_read_at = models.DateTimeField(null=True, blank=True)
    last_delivered_message = models.ForeignKey("Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_delivered_at = models.DateTimeField(null=True, blank=True)
    moderation_muted_until = models.DateTimeField(null=True, blank=True)
    banned_at = models.DateTimeField(null=True, blank=True)
    banned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="chat_bans_issued")
    ban_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="uniq_conversation_user_participant")]
        indexes = [models.Index(fields=["user", "conversation"]), models.Index(fields=["conversation", "joined_at"])]


class PendingUpload(BaseUUIDModel):
    class MediaKind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        FILE = "file", "File"

    class UploadStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ATTACHED = "attached", "Attached"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    class ScanStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CLEAN = "clean", "Clean"
        INFECTED = "infected", "Infected"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pending_uploads")
    file = models.FileField(upload_to="chat/pending/%Y/%m/", storage=pending_upload_storage_factory)
    original_name = models.CharField(max_length=255)
    media_kind = models.CharField(max_length=20, choices=MediaKind.choices, default=MediaKind.FILE)
    mime_type = models.CharField(max_length=255, blank=True)
    size = models.BigIntegerField(default=0)
    extension = models.CharField(max_length=32, blank=True)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    rotation = models.IntegerField(null=True, blank=True)
    duration_seconds = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    thumbnail = models.ImageField(
        upload_to="chat/pending_thumbnails/%Y/%m/",
        storage=pending_upload_storage_factory,
        blank=True,
        null=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=UploadStatus.choices, default=UploadStatus.PENDING)
    scan_status = models.CharField(max_length=20, choices=ScanStatus.choices, default=ScanStatus.PENDING)
    scan_notes = models.CharField(max_length=255, blank=True)
    scanned_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(default=pending_upload_expiry_default)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["user", "expires_at"]),
        ]


class UserStatus(BaseUUIDModel):
    class ContentType(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_statuses")
    content_type = models.CharField(max_length=12, choices=ContentType.choices)
    text = models.TextField(blank=True)
    upload = models.OneToOneField(
        PendingUpload,
        on_delete=models.CASCADE,
        related_name="user_status",
        null=True,
        blank=True,
    )
    background_color = models.CharField(max_length=9, default="#111111")
    text_color = models.CharField(max_length=9, default="#ffffff")
    expires_at = models.DateTimeField(default=user_status_expiry_default)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["author", "expires_at"]),
            models.Index(fields=["is_deleted", "expires_at"]),
        ]


class UserStatusView(BaseUUIDModel):
    status = models.ForeignKey(UserStatus, on_delete=models.CASCADE, related_name="view_receipts")
    viewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="viewed_chat_statuses")
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["status", "viewer"], name="uniq_status_viewer")]
        indexes = [models.Index(fields=["status", "viewed_at"]), models.Index(fields=["viewer", "viewed_at"])]


class UserE2EEDeviceKey(BaseUUIDModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="e2ee_device_keys")
    device_id = models.CharField(max_length=128)
    key_id = models.CharField(max_length=256, unique=True)
    label = models.CharField(max_length=120, blank=True)
    algorithm = models.CharField(max_length=80)
    fingerprint = models.CharField(max_length=128, blank=True)
    public_key_jwk = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "device_id", "key_id"], name="uniq_user_device_e2ee_key"),
        ]
        indexes = [
            models.Index(fields=["user", "is_active", "updated_at"], name="chat_usere2_user_id_79646a_idx"),
            models.Index(fields=["device_id", "is_active"], name="chat_usere2_device__e3cf40_idx"),
            models.Index(fields=["user", "fingerprint"], name="chat_usere2_user_id_df26fc_idx"),
        ]


class Message(BaseUUIDModel):
    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        FILE = "file", "File"
        SYSTEM = "system", "System"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_messages")
    type = models.CharField(max_length=20, choices=MessageType.choices, default="text")
    text = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies")
    forwarded_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="forwards")
    is_edited = models.BooleanField(default=False)
    edited_at = models.DateTimeField(null=True, blank=True)
    edit_locked_at = models.DateTimeField(null=True, blank=True)
    edit_locked_reason = models.CharField(max_length=32, blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    client_temp_id = models.CharField(max_length=100, blank=True, db_index=True)
    delivery_status = models.CharField(max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.SENT)
    failed_reason = models.CharField(max_length=255, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["conversation", "-created_at"]), models.Index(fields=["sender", "-created_at"])]


class MessageEditHistory(BaseUUIDModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="edit_history")
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="message_edit_history")
    previous_text = models.TextField(blank=True)
    new_text = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["message", "-created_at"])]




class MessageTranscript(BaseUUIDModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTO = "auto", "Auto"

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="transcript")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    language_code = models.CharField(max_length=16, blank=True)
    text = models.TextField(blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)

    class Meta:
        indexes = [models.Index(fields=["status", "updated_at"])]


class MessageAttachment(BaseUUIDModel):
    class MediaKind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        FILE = "file", "File"

    class ScanStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CLEAN = "clean", "Clean"
        INFECTED = "infected", "Infected"
        FAILED = "failed", "Failed"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="chat/attachments/%Y/%m/", storage=attachment_storage_factory)
    original_name = models.CharField(max_length=255)
    media_kind = models.CharField(max_length=20, choices=MediaKind.choices, default=MediaKind.FILE)
    mime_type = models.CharField(max_length=255, blank=True)
    size = models.BigIntegerField(default=0)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    rotation = models.IntegerField(null=True, blank=True)
    duration_seconds = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    thumbnail = models.ImageField(
        upload_to="chat/attachments/thumbnails/%Y/%m/",
        storage=attachment_storage_factory,
        blank=True,
        null=True,
    )
    scan_status = models.CharField(max_length=20, choices=ScanStatus.choices, default=ScanStatus.CLEAN)
    scan_notes = models.CharField(max_length=255, blank=True)
    scanned_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    view_once = models.BooleanField(default=False, db_index=True)


class MessageAttachmentViewReceipt(BaseUUIDModel):
    attachment = models.ForeignKey(MessageAttachment, on_delete=models.CASCADE, related_name="view_receipts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="view_once_attachment_receipts")
    opened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attachment", "user"], name="uniq_view_once_attachment_user")]
        indexes = [models.Index(fields=["user", "opened_at"])]


class MessageReaction(BaseUUIDModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="message_reactions")
    emoji = models.CharField(max_length=32)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "user"], name="uniq_message_user_reaction")]
        indexes = [models.Index(fields=["message", "created_at"])]


class MessageDelivery(BaseUUIDModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="deliveries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="message_deliveries")
    delivered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "user"], name="uniq_message_delivery_user")]
        indexes = [models.Index(fields=["user", "delivered_at"])]


class CallSession(BaseUUIDModel):
    class CallType(models.TextChoices):
        VOICE = "voice", "Voice"
        VIDEO = "video", "Video"

    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        RINGING = "ringing", "Ringing"
        ONGOING = "ongoing", "Ongoing"
        DECLINED = "declined", "Declined"
        MISSED = "missed", "Missed"
        ENDED = "ended", "Ended"
        FAILED = "failed", "Failed"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="calls")
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="initiated_calls")
    call_type = models.CharField(max_length=16, choices=CallType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    room_key = models.CharField(max_length=64, unique=True, db_index=True)
    answered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="answered_calls")
    started_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_signal_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["conversation", "-started_at"]),
            models.Index(fields=["status", "-started_at"]),
        ]


class CallParticipant(BaseUUIDModel):
    class State(models.TextChoices):
        INVITED = "invited", "Invited"
        RINGING = "ringing", "Ringing"
        JOINED = "joined", "Joined"
        DECLINED = "declined", "Declined"
        MISSED = "missed", "Missed"
        LEFT = "left", "Left"

    class NetworkQuality(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        EXCELLENT = "excellent", "Excellent"
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"
        POOR = "poor", "Poor"
        OFFLINE = "offline", "Offline"

    class VideoPreference(models.TextChoices):
        AUTO = "auto", "Auto"
        OFF = "off", "Off"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class ConnectionState(models.TextChoices):
        NEW = "new", "New"
        CHECKING = "checking", "Checking"
        CONNECTED = "connected", "Connected"
        DEGRADED = "degraded", "Degraded"
        DISCONNECTED = "disconnected", "Disconnected"
        FAILED = "failed", "Failed"
        CLOSED = "closed", "Closed"

    class AudioRoute(models.TextChoices):
        AUTO = "auto", "Auto"
        SPEAKER = "speaker", "Speaker"
        EARPIECE = "earpiece", "Earpiece"
        BLUETOOTH = "bluetooth", "Bluetooth"
        WIRED = "wired", "Wired"

    call = models.ForeignKey(CallSession, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="call_participations")
    state = models.CharField(max_length=20, choices=State.choices, default=State.INVITED)
    network_quality = models.CharField(max_length=20, choices=NetworkQuality.choices, default=NetworkQuality.UNKNOWN)
    preferred_video_quality = models.CharField(max_length=16, choices=VideoPreference.choices, default=VideoPreference.AUTO)
    audio_enabled = models.BooleanField(default=True)
    video_enabled = models.BooleanField(default=True)
    is_on_hold = models.BooleanField(default=False)
    reconnecting = models.BooleanField(default=False)
    connection_state = models.CharField(max_length=20, choices=ConnectionState.choices, default=ConnectionState.NEW)
    audio_route = models.CharField(max_length=16, choices=AudioRoute.choices, default=AudioRoute.AUTO)
    screen_share_enabled = models.BooleanField(default=False)
    screen_share_started_at = models.DateTimeField(null=True, blank=True)
    raised_hand_at = models.DateTimeField(null=True, blank=True)
    is_speaking = models.BooleanField(default=False)
    speaking_level = models.PositiveSmallIntegerField(default=0)
    last_spoke_at = models.DateTimeField(null=True, blank=True)
    reconnect_deadline_at = models.DateTimeField(null=True, blank=True)
    last_quality_report_at = models.DateTimeField(null=True, blank=True)
    last_seen_signal_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    packet_loss_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    jitter_ms = models.PositiveIntegerField(null=True, blank=True)
    round_trip_time_ms = models.PositiveIntegerField(null=True, blank=True)
    bitrate_kbps = models.PositiveIntegerField(null=True, blank=True)
    frame_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    quality_score = models.PositiveSmallIntegerField(default=100)
    quality_alert = models.CharField(max_length=32, blank=True)
    invited_at = models.DateTimeField(auto_now_add=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    diagnostics = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["call", "user"], name="uniq_call_user_participant")]
        indexes = [
            models.Index(fields=["user", "state"]),
            models.Index(fields=["call", "network_quality"]),
            models.Index(fields=["call", "last_heartbeat_at"]),
            models.Index(fields=["call", "connection_state"]),
            models.Index(fields=["call", "raised_hand_at"]),
        ]


class UserBlock(BaseUUIDModel):
    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_initiated")
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_received")
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["blocker", "blocked"], name="uniq_blocker_blocked")]
        indexes = [models.Index(fields=["blocker", "blocked"])]


class MessageReport(BaseUUIDModel):
    class ReportReason(models.TextChoices):
        SPAM = "spam", "Spam"
        HARASSMENT = "harassment", "Harassment"
        HATE = "hate", "Hate"
        VIOLENCE = "violence", "Violence"
        IMPERSONATION = "impersonation", "Impersonation"
        OTHER = "other", "Other"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="message_reports")
    reason = models.CharField(max_length=32, choices=ReportReason.choices)
    details = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "reporter"], name="uniq_message_report_reporter")]
        indexes = [models.Index(fields=["reason", "created_at"])]


class UserDevice(BaseUUIDModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="devices")
    platform = models.CharField(max_length=20)
    push_token = models.CharField(max_length=512, db_index=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "push_token"], name="uniq_user_push_token")]


class NotificationPreference(BaseUUIDModel):
    class CallQualityPreference(models.TextChoices):
        AUTO = "auto", "Auto"
        LOW = "low", "Low"
        MID = "mid", "Mid"
        CLEAR = "clear", "Clear"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preference")
    push_enabled = models.BooleanField(default=True)
    message_preview_enabled = models.BooleanField(default=True)
    mute_all = models.BooleanField(default=False)
    call_quality_preference = models.CharField(
        max_length=16,
        choices=CallQualityPreference.choices,
        default=CallQualityPreference.AUTO,
    )

    def __str__(self):
        return f"NotificationPreference<{self.user_id}>"


class ModerationAction(BaseUUIDModel):
    class ActionType(models.TextChoices):
        RESOLVE_REPORT = "resolve_report", "Resolve report"
        DISMISS_REPORT = "dismiss_report", "Dismiss report"
        HIDE_MESSAGE = "hide_message", "Hide message"
        RESTORE_MESSAGE = "restore_message", "Restore message"

    report = models.ForeignKey("MessageReport", on_delete=models.SET_NULL, null=True, blank=True, related_name="actions")
    message = models.ForeignKey("Message", on_delete=models.SET_NULL, null=True, blank=True, related_name="moderation_actions")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="moderation_actions")
    action_type = models.CharField(max_length=32, choices=ActionType.choices)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["action_type", "created_at"])]




class ConversationNotificationSetting(BaseUUIDModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="notification_settings")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversation_notification_settings")
    message_notifications_enabled = models.BooleanField(default=True)
    call_notifications_enabled = models.BooleanField(default=True)
    mentions_only = models.BooleanField(default=False)
    muted_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="uniq_conversation_notification_setting")]
        indexes = [models.Index(fields=["user", "conversation"])]


class ConversationDraft(BaseUUIDModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="drafts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversation_drafts")
    text = models.TextField(blank=True)
    reply_to = models.ForeignKey(Message, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="uniq_conversation_draft")]
        indexes = [
            models.Index(fields=["user", "updated_at"], name="chat_conver_user_id_d2b10f_idx"),
            models.Index(fields=["conversation", "updated_at"], name="chat_conver_convers_35cd6d_idx"),
        ]


class ConversationInviteLink(BaseUUIDModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="invite_links")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_conversation_invite_links")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(default=0)
    use_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["conversation", "revoked_at", "expires_at"])]

    @property
    def is_active(self):
        if self.revoked_at:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        if self.max_uses and self.use_count >= self.max_uses:
            return False
        return True


class ChatAuditLog(BaseUUIDModel):
    class EventType(models.TextChoices):
        MESSAGE_SENT = "message_sent", "Message sent"
        MESSAGE_EDITED = "message_edited", "Message edited"
        MESSAGE_DELETED = "message_deleted", "Message deleted"
        MESSAGE_RESTORED = "message_restored", "Message restored"
        REACTION_ADDED = "reaction_added", "Reaction added"
        REACTION_REMOVED = "reaction_removed", "Reaction removed"
        DELIVERY_MARKED = "delivery_marked", "Delivery marked"
        READ_MARKED = "read_marked", "Read marked"
        PARTICIPANTS_ADDED = "participants_added", "Participants added"
        PARTICIPANT_REMOVED = "participant_removed", "Participant removed"
        ROLE_CHANGED = "role_changed", "Role changed"
        OWNERSHIP_TRANSFERRED = "ownership_transferred", "Ownership transferred"
        USER_BLOCKED = "user_blocked", "User blocked"
        USER_UNBLOCKED = "user_unblocked", "User unblocked"
        REPORT_CREATED = "report_created", "Report created"
        MODERATION_ACTION = "moderation_action", "Moderation action"
        UPLOAD_SCANNED = "upload_scanned", "Upload scanned"
        MEDIA_TOKEN_ISSUED = "media_token_issued", "Media token issued"
        MEDIA_ACCESSED = "media_accessed", "Media accessed"
        CALL_STARTED = "call_started", "Call started"
        CALL_JOINED = "call_joined", "Call joined"
        CALL_DECLINED = "call_declined", "Call declined"
        CALL_ENDED = "call_ended", "Call ended"
        CALL_SIGNAL_SENT = "call_signal_sent", "Call signal sent"
        PARTICIPANT_MUTED = "participant_muted", "Participant muted"
        PARTICIPANT_BANNED = "participant_banned", "Participant banned"
        PARTICIPANT_UNBANNED = "participant_unbanned", "Participant unbanned"
        MESSAGE_FAILED = "message_failed", "Message failed"
        MESSAGE_RETRIED = "message_retried", "Message retried"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="chat_audit_logs")
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    message = models.ForeignKey(Message, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    event_type = models.CharField(max_length=40, choices=EventType.choices)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["event_type", "created_at"]), models.Index(fields=["conversation", "created_at"])]
