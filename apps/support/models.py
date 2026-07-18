from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.common.models import BaseUUIDModel


def default_support_business_hours():
    return {
        "monday": {"enabled": True, "start": "09:00", "end": "17:00"},
        "tuesday": {"enabled": True, "start": "09:00", "end": "17:00"},
        "wednesday": {"enabled": True, "start": "09:00", "end": "17:00"},
        "thursday": {"enabled": True, "start": "09:00", "end": "17:00"},
        "friday": {"enabled": True, "start": "09:00", "end": "17:00"},
        "saturday": {"enabled": False, "start": "09:00", "end": "17:00"},
        "sunday": {"enabled": False, "start": "09:00", "end": "17:00"},
    }


def default_first_response_targets():
    return {"low": 240, "normal": 120, "high": 60, "urgent": 15}


def default_next_response_targets():
    return {"low": 480, "normal": 240, "high": 120, "urgent": 30}


def default_resolution_targets():
    return {"low": 4320, "normal": 2880, "high": 1440, "urgent": 480}


class SupportAccount(BaseUUIDModel):
    """Hidden ownership and billing boundary for the Support Chat product."""

    class Status(models.TextChoices):
        INACTIVE = "inactive", "Inactive"
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_support_account",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INACTIVE, db_index=True)
    plan_code = models.CharField(max_length=80, blank=True)
    website_limit = models.PositiveSmallIntegerField(default=1)
    agent_limit = models.PositiveSmallIntegerField(default=1)
    current_period_end = models.DateTimeField(null=True, blank=True)
    grace_ends_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "current_period_end"])]

    def __str__(self):
        return f"SupportAccount<{self.owner_id}:{self.status}>"

    @property
    def has_product_access(self) -> bool:
        if self.status == self.Status.ACTIVE:
            return True
        if self.status == self.Status.TRIALING:
            return not self.current_period_end or self.current_period_end > timezone.now()
        if self.status == self.Status.PAST_DUE and self.grace_ends_at:
            return self.grace_ends_at > timezone.now()
        return False


class SupportAgent(BaseUUIDModel):
    """An invited company person who handles support conversations."""

    class Availability(models.TextChoices):
        AVAILABLE = "available", "Available"
        BUSY = "busy", "Busy"
        AWAY = "away", "Away"
        OFFLINE = "offline", "Offline"

    support_account = models.ForeignKey(SupportAccount, on_delete=models.CASCADE, related_name="agents")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_agent_memberships")
    availability = models.CharField(max_length=20, choices=Availability.choices, default=Availability.OFFLINE)
    max_active_conversations = models.PositiveSmallIntegerField(default=5)
    can_view_all_conversations = models.BooleanField(default=False)
    can_assign_conversations = models.BooleanField(default=False)
    can_view_analytics = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_agents_invited",
    )
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["user__username"]
        constraints = [
            models.UniqueConstraint(fields=["support_account", "user"], name="uniq_support_account_agent"),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_active=True),
                name="uniq_active_support_agent_user",
            ),
        ]
        indexes = [
            models.Index(fields=["support_account", "is_active"]),
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["support_account", "availability", "is_active"]),
        ]

    def clean(self):
        if not self.support_account_id or not self.user_id:
            return
        if self.support_account.owner_id == self.user_id:
            raise ValidationError({"user": "The Support Chat owner cannot also consume an agent seat."})
        if SupportAccount.objects.filter(owner_id=self.user_id).exclude(pk=self.support_account_id).exists():
            raise ValidationError({"user": "A Support Chat owner cannot join another Support Chat account as an agent."})

    def __str__(self):
        return f"SupportAgent<{self.support_account_id}:{self.user_id}>"


class SupportWebsite(BaseUUIDModel):
    """A website whose visitor conversations belong to a Support Chat account."""

    support_account = models.ForeignKey(SupportAccount, on_delete=models.CASCADE, related_name="websites")
    name = models.CharField(max_length=120)
    domain = models.CharField(max_length=255)
    site_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    allowed_origins = models.JSONField(default=list, blank=True)
    widget_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_websites_created",
    )

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["support_account", "domain"],
                condition=Q(is_active=True),
                name="uniq_active_support_website_domain",
            ),
        ]
        indexes = [
            models.Index(fields=["support_account", "is_active"]),
            models.Index(fields=["site_key"]),
        ]

    def __str__(self):
        return self.name


class SupportWebsiteAgent(BaseUUIDModel):
    """Explicit website access for a Support Chat agent."""

    website = models.ForeignKey(SupportWebsite, on_delete=models.CASCADE, related_name="agent_assignments")
    agent = models.ForeignKey(SupportAgent, on_delete=models.CASCADE, related_name="website_assignments")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["website", "agent"], name="uniq_support_website_agent"),
        ]
        indexes = [
            models.Index(fields=["website", "agent"]),
            models.Index(fields=["agent", "website"]),
        ]

    def clean(self):
        if self.website_id and self.agent_id and self.website.support_account_id != self.agent.support_account_id:
            raise ValidationError("An agent can only be assigned to websites in the same Support Chat account.")

    def __str__(self):
        return f"SupportWebsiteAgent<{self.website_id}:{self.agent_id}>"


class SupportAgentInvitation(BaseUUIDModel):
    """A seat-reserving invitation to join one Support Chat account as an agent."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="agent_invitations",
    )
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(default=timezone.now)
    send_count = models.PositiveSmallIntegerField(default=1)
    max_active_conversations = models.PositiveSmallIntegerField(default=5)
    can_view_all_conversations = models.BooleanField(default=False)
    can_assign_conversations = models.BooleanField(default=False)
    can_view_analytics = models.BooleanField(default=False)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_agent_invitations_sent",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_agent_invitations_accepted",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["support_account", "email"],
                condition=Q(status="pending"),
                name="uniq_pending_support_agent_invitation",
            ),
        ]
        indexes = [
            models.Index(fields=["support_account", "status", "expires_at"], name="support_inv_acct_stat_exp_idx"),
            models.Index(fields=["email", "status"], name="support_inv_email_status_idx"),
        ]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()

    def __str__(self):
        return f"SupportAgentInvitation<{self.support_account_id}:{self.email}:{self.status}>"


class SupportAgentInvitationWebsite(BaseUUIDModel):
    """Website access that will be copied to an agent when an invitation is accepted."""

    invitation = models.ForeignKey(
        SupportAgentInvitation,
        on_delete=models.CASCADE,
        related_name="website_assignments",
    )
    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="agent_invitation_assignments",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["invitation", "website"],
                name="uniq_support_invitation_website",
            ),
        ]
        indexes = [
            models.Index(fields=["invitation", "website"], name="support_invite_website_idx"),
        ]

    def clean(self):
        if (
            self.invitation_id
            and self.website_id
            and self.invitation.support_account_id != self.website.support_account_id
        ):
            raise ValidationError("An invitation can only include websites from the same Support Chat account.")

    def __str__(self):
        return f"SupportAgentInvitationWebsite<{self.invitation_id}:{self.website_id}>"


class SupportWidgetSettings(BaseUUIDModel):
    """Owner-managed presentation and identity requirements for one website widget."""

    class Position(models.TextChoices):
        RIGHT = "right", "Bottom right"
        LEFT = "left", "Bottom left"

    class Theme(models.TextChoices):
        AUTO = "auto", "Automatic"
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"

    website = models.OneToOneField(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="widget_settings",
    )
    brand_name = models.CharField(max_length=120, default="Support")
    primary_color = models.CharField(max_length=7, default="#111111")
    welcome_text = models.CharField(max_length=255, default="Hi, how can we help?")
    offline_text = models.CharField(max_length=255, default="Leave a message and our team will reply soon.")
    launcher_text = models.CharField(max_length=60, default="Chat")
    privacy_note = models.CharField(max_length=180, blank=True)
    position = models.CharField(max_length=20, choices=Position.choices, default=Position.RIGHT)
    theme = models.CharField(max_length=20, choices=Theme.choices, default=Theme.AUTO)
    require_name = models.BooleanField(default=False)
    require_email = models.BooleanField(default=False)
    allow_attachments = models.BooleanField(default=True)
    allow_audio_calls = models.BooleanField(default=True)
    allow_video_calls = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Support widget settings"

    def __str__(self):
        return f"SupportWidgetSettings<{self.website_id}>"


class SupportServiceSettings(BaseUUIDModel):
    """Account-level service targets and business hours for Support Chat."""

    support_account = models.OneToOneField(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="service_settings",
    )
    timezone = models.CharField(max_length=64, default="UTC")
    business_hours_enabled = models.BooleanField(default=True)
    business_hours = models.JSONField(default=default_support_business_hours)
    first_response_targets = models.JSONField(default=default_first_response_targets)
    next_response_targets = models.JSONField(default=default_next_response_targets)
    resolution_targets = models.JSONField(default=default_resolution_targets)
    due_soon_minutes = models.PositiveSmallIntegerField(default=15)
    default_follow_up_minutes = models.PositiveIntegerField(default=1440)
    alert_owner = models.BooleanField(default=True)
    alert_assigned_agent = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_service_settings_updated",
    )

    class Meta:
        verbose_name_plural = "Support service settings"

    def __str__(self):
        return f"SupportServiceSettings<{self.support_account_id}>"


class SupportVisitor(BaseUUIDModel):
    """A website visitor identity that never becomes a Messenger account."""

    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="visitors",
    )
    external_id = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    locale = models.CharField(max_length=32, blank=True)
    current_page_url = models.URLField(max_length=1000, blank=True)
    referrer = models.URLField(max_length=1000, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_blocked = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-last_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["website", "external_id"],
                name="uniq_support_visitor_external_id",
            ),
        ]
        indexes = [
            models.Index(fields=["website", "last_seen_at"], name="support_visitor_site_seen_idx"),
            models.Index(fields=["website", "is_blocked"], name="support_visitor_site_block_idx"),
        ]

    def __str__(self):
        return f"SupportVisitor<{self.website_id}:{self.external_id}>"


class SupportWidgetSession(BaseUUIDModel):
    """A revocable, origin-bound browser session for the public support widget."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="widget_sessions",
    )
    visitor = models.ForeignKey(
        SupportVisitor,
        on_delete=models.CASCADE,
        related_name="widget_sessions",
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    token_version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    origin = models.URLField(max_length=500)
    expires_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    user_agent = models.CharField(max_length=500, blank=True)
    current_page_url = models.URLField(max_length=1000, blank=True)
    referrer = models.URLField(max_length=1000, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [
            models.Index(fields=["website", "status", "last_seen_at"], name="sup_wsess_site_stat_seen_idx"),
            models.Index(fields=["visitor", "status", "expires_at"], name="sup_wsess_vis_stat_exp_idx"),
        ]

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE and self.expires_at > timezone.now()

    def __str__(self):
        return f"SupportWidgetSession<{self.website_id}:{self.visitor_id}:{self.status}>"


class SupportPendingUpload(BaseUUIDModel):
    """Support-scoped ownership for a shared chat.PendingUpload record."""

    class Source(models.TextChoices):
        TEAM = "team", "Support team"
        VISITOR = "visitor", "Website visitor"

    pending_upload = models.OneToOneField(
        "chat.PendingUpload",
        on_delete=models.CASCADE,
        related_name="support_upload",
    )
    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="pending_uploads",
    )
    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="pending_uploads",
    )
    support_conversation = models.ForeignKey(
        "SupportConversation",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="pending_uploads",
    )
    source = models.CharField(max_length=16, choices=Source.choices)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="support_pending_uploads",
    )
    widget_session = models.ForeignKey(
        SupportWidgetSession,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="pending_uploads",
    )
    visitor = models.ForeignKey(
        SupportVisitor,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="pending_uploads",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["support_account", "website", "created_at"], name="sup_upload_acct_site_time_idx"),
            models.Index(fields=["source", "created_at"], name="sup_upload_source_time_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(source="team", uploaded_by__isnull=False, widget_session__isnull=True, visitor__isnull=True)
                    | models.Q(source="visitor", uploaded_by__isnull=True, widget_session__isnull=False, visitor__isnull=False)
                ),
                name="support_upload_valid_owner",
            ),
        ]

    def clean(self):
        errors = {}
        if self.website_id and self.support_account_id and self.website.support_account_id != self.support_account_id:
            errors["website"] = "The upload website must belong to the same Support Chat account."
        if self.support_conversation_id and self.website_id and self.support_conversation.website_id != self.website_id:
            errors["support_conversation"] = "The upload conversation must belong to the selected website."
        if self.source == self.Source.TEAM:
            if not self.uploaded_by_id:
                errors["uploaded_by"] = "Team uploads require an authenticated Support Chat user."
            if self.widget_session_id or self.visitor_id:
                errors["source"] = "Team uploads cannot belong to a visitor session."
        elif self.source == self.Source.VISITOR:
            if not self.widget_session_id or not self.visitor_id:
                errors["source"] = "Visitor uploads require a widget session and visitor."
            if self.uploaded_by_id:
                errors["uploaded_by"] = "Visitor uploads cannot belong to a platform user."
            if self.widget_session_id and self.website_id and self.widget_session.website_id != self.website_id:
                errors["widget_session"] = "The widget session must belong to the selected website."
            if self.visitor_id and self.website_id and self.visitor.website_id != self.website_id:
                errors["visitor"] = "The visitor must belong to the selected website."
        if self.pending_upload_id and self.pending_upload.purpose != "support":
            errors["pending_upload"] = "Support uploads must use the Support Chat purpose."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"SupportPendingUpload<{self.website_id}:{self.source}:{self.pending_upload_id}>"


class SupportConversation(BaseUUIDModel):
    """Support-only workflow wrapped around the proven chat conversation record."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        OPEN = "open", "Open"
        WAITING_CUSTOMER = "waiting_customer", "Waiting for customer"
        WAITING_TEAM = "waiting_team", "Waiting for team"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    conversation = models.OneToOneField(
        "chat.Conversation",
        on_delete=models.CASCADE,
        related_name="support_conversation",
    )
    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.PROTECT,
        related_name="support_conversations",
    )
    visitor = models.OneToOneField(
        SupportVisitor,
        on_delete=models.PROTECT,
        related_name="support_conversation",
    )
    assigned_agent = models.ForeignKey(
        SupportAgent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_conversations",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NEW, db_index=True)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.NORMAL, db_index=True)
    subject = models.CharField(max_length=255, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    last_visitor_message_at = models.DateTimeField(null=True, blank=True)
    last_agent_message_at = models.DateTimeField(null=True, blank=True)
    visitor_last_read_message = models.ForeignKey(
        "chat.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    visitor_last_read_at = models.DateTimeField(null=True, blank=True)
    visitor_last_delivered_message = models.ForeignKey(
        "chat.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    visitor_last_delivered_at = models.DateTimeField(null=True, blank=True)
    first_response_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    next_response_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolution_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    first_response_breached_at = models.DateTimeField(null=True, blank=True)
    next_response_breached_at = models.DateTimeField(null=True, blank=True)
    resolution_breached_at = models.DateTimeField(null=True, blank=True)
    follow_up_at = models.DateTimeField(null=True, blank=True, db_index=True)
    follow_up_note = models.CharField(max_length=255, blank=True)
    follow_up_created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_follow_ups_created",
    )
    follow_up_completed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-conversation__last_message_at", "-created_at"]
        indexes = [
            models.Index(fields=["website", "status", "updated_at"], name="sup_conv_site_stat_upd_idx"),
            models.Index(fields=["assigned_agent", "status"], name="sup_conv_agent_status_idx"),
            models.Index(fields=["priority", "status"], name="sup_conv_prio_status_idx"),
            models.Index(fields=["status", "first_response_due_at"], name="sup_conv_first_due_idx"),
            models.Index(fields=["status", "next_response_due_at"], name="sup_conv_next_due_idx"),
            models.Index(fields=["status", "resolution_due_at"], name="sup_conv_res_due_idx"),
            models.Index(fields=["status", "follow_up_at"], name="sup_conv_follow_due_idx"),
        ]

    def clean(self):
        if self.website_id and self.visitor_id and self.visitor.website_id != self.website_id:
            raise ValidationError("The visitor and conversation website must match.")
        if (
            self.assigned_agent_id
            and self.website_id
            and self.assigned_agent.support_account_id != self.website.support_account_id
        ):
            raise ValidationError("The assigned agent must belong to the same Support Chat account.")

    def __str__(self):
        return f"SupportConversation<{self.website_id}:{self.visitor_id}:{self.status}>"


class SupportMessageAuthor(BaseUUIDModel):
    """External visitor authorship for a shared chat.Message without fake Messenger users."""

    message = models.OneToOneField(
        "chat.Message",
        on_delete=models.CASCADE,
        related_name="support_author",
    )
    visitor = models.ForeignKey(
        SupportVisitor,
        on_delete=models.PROTECT,
        related_name="authored_support_messages",
    )
    session = models.ForeignKey(
        SupportWidgetSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authored_messages",
    )
    display_name = models.CharField(max_length=120, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["visitor", "created_at"], name="sup_msg_author_vis_time_idx"),
        ]

    def clean(self):
        if not self.message_id or not self.visitor_id:
            return
        try:
            support_conversation = self.message.conversation.support_conversation
        except SupportConversation.DoesNotExist as exc:
            raise ValidationError("External authors are only valid for Support Chat messages.") from exc
        if support_conversation.visitor_id != self.visitor_id:
            raise ValidationError("The message visitor must match the Support Chat conversation visitor.")
        if self.session_id and self.session.visitor_id != self.visitor_id:
            raise ValidationError("The widget session must belong to the same visitor.")

    def __str__(self):
        return f"SupportMessageAuthor<{self.message_id}:{self.visitor_id}>"


class SupportConversationReadState(BaseUUIDModel):
    """Per-owner or per-agent read position, independent from Messenger participants."""

    support_conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="read_states",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_conversation_read_states",
    )
    last_read_message = models.ForeignKey(
        "chat.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    last_delivered_message = models.ForeignKey(
        "chat.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    last_delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["support_conversation", "user"],
                name="uniq_support_conv_user_read",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "updated_at"], name="sup_conv_read_user_upd_idx"),
        ]

    def __str__(self):
        return f"SupportConversationReadState<{self.support_conversation_id}:{self.user_id}>"


class SupportTag(BaseUUIDModel):
    """Account-level label that can be applied to Support conversations."""

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="tags",
    )
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=7, default="#4f46e5")
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_tags_created",
    )

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "support_account",
                condition=Q(is_active=True),
                name="uniq_sup_active_tag_name_ci",
            ),
        ]
        indexes = [
            models.Index(fields=["support_account", "is_active"], name="sup_tag_acct_active_idx"),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        color = (self.color or "").strip().lower()
        if len(color) != 7 or not color.startswith("#") or any(ch not in "0123456789abcdef" for ch in color[1:]):
            raise ValidationError({"color": "Enter a valid six-digit hex color."})
        self.color = color

    def __str__(self):
        return f"SupportTag<{self.support_account_id}:{self.name}>"


class SupportConversationTag(BaseUUIDModel):
    support_conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="tag_assignments",
    )
    tag = models.ForeignKey(
        SupportTag,
        on_delete=models.CASCADE,
        related_name="conversation_assignments",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_conversation_tags_added",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["support_conversation", "tag"],
                name="uniq_support_conversation_tag",
            ),
        ]
        indexes = [
            models.Index(fields=["support_conversation", "tag"], name="sup_conv_tag_lookup_idx"),
        ]

    def clean(self):
        if (
            self.support_conversation_id
            and self.tag_id
            and self.support_conversation.website.support_account_id != self.tag.support_account_id
        ):
            raise ValidationError("A tag can only be applied inside its Support Chat account.")

    def __str__(self):
        return f"SupportConversationTag<{self.support_conversation_id}:{self.tag_id}>"


class SupportInternalNote(BaseUUIDModel):
    """Private team-only note. It is never exposed through public widget serializers."""

    support_conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="internal_notes",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="support_internal_notes",
    )
    body = models.TextField(max_length=10000)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["support_conversation", "created_at"], name="sup_note_conv_time_idx"),
        ]

    def clean(self):
        self.body = (self.body or "").strip()
        if not self.body:
            raise ValidationError({"body": "Write a note before saving."})

    def __str__(self):
        return f"SupportInternalNote<{self.support_conversation_id}:{self.author_id}>"


class SupportCannedReply(BaseUUIDModel):
    """Reusable reply available account-wide or restricted to one website."""

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="canned_replies",
    )
    website = models.ForeignKey(
        SupportWebsite,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="canned_replies",
    )
    shortcut = models.CharField(max_length=40)
    title = models.CharField(max_length=120)
    body = models.TextField(max_length=10000)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_canned_replies_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_canned_replies_updated",
    )

    class Meta:
        ordering = ["shortcut", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["support_account", "shortcut"],
                condition=Q(is_active=True),
                name="uniq_sup_active_reply_shortcut",
            ),
        ]
        indexes = [
            models.Index(fields=["support_account", "is_active"], name="sup_reply_acct_active_idx"),
            models.Index(fields=["website", "is_active"], name="sup_reply_site_active_idx"),
        ]

    def clean(self):
        self.shortcut = (self.shortcut or "").strip().lower()
        if self.shortcut and not self.shortcut.startswith("/"):
            self.shortcut = f"/{self.shortcut}"
        self.title = (self.title or "").strip()
        self.body = (self.body or "").strip()
        errors = {}
        if not self.shortcut or " " in self.shortcut:
            errors["shortcut"] = "Use a shortcut such as /hello without spaces."
        if not self.title:
            errors["title"] = "A title is required."
        if not self.body:
            errors["body"] = "Reply text is required."
        if self.website_id and self.website.support_account_id != self.support_account_id:
            errors["website"] = "The website must belong to the same Support Chat account."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"SupportCannedReply<{self.support_account_id}:{self.shortcut}>"


class SupportSavedInboxView(BaseUUIDModel):
    """A private saved filter for one owner or agent."""

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="saved_inbox_views",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_saved_inbox_views",
    )
    name = models.CharField(max_length=80)
    website = models.ForeignKey(
        SupportWebsite,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="saved_inbox_views",
    )
    queue = models.CharField(max_length=24, blank=True)
    status = models.CharField(max_length=24, blank=True)
    priority = models.CharField(max_length=16, blank=True)
    tag = models.ForeignKey(
        SupportTag,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="saved_inbox_views",
    )
    search = models.CharField(max_length=120, blank=True)
    is_default = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-is_default", "name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "support_account",
                "user",
                name="uniq_sup_saved_view_name_ci",
            ),
        ]
        indexes = [
            models.Index(fields=["support_account", "user"], name="sup_saved_view_user_idx"),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.search = (self.search or "").strip()
        errors = {}
        if not self.name:
            errors["name"] = "A saved-view name is required."
        if self.website_id and self.website.support_account_id != self.support_account_id:
            errors["website"] = "The website must belong to the same Support Chat account."
        if self.tag_id and self.tag.support_account_id != self.support_account_id:
            errors["tag"] = "The tag must belong to the same Support Chat account."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"SupportSavedInboxView<{self.user_id}:{self.name}>"


class SupportAuditEvent(BaseUUIDModel):
    """Immutable audit event for sensitive Support Chat workflow changes."""

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    website = models.ForeignKey(
        SupportWebsite,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    support_conversation = models.ForeignKey(
        SupportConversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_audit_events",
    )
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    summary = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["support_account", "created_at"], name="sup_audit_acct_time_idx"),
            models.Index(fields=["support_conversation", "created_at"], name="sup_audit_conv_time_idx"),
            models.Index(fields=["actor", "created_at"], name="sup_audit_actor_time_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Support audit events are immutable.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"SupportAuditEvent<{self.action}:{self.created_at}>"

class SupportServiceAlert(BaseUUIDModel):
    """Persistent owner/agent alert generated by Support service deadlines."""

    class Kind(models.TextChoices):
        FIRST_RESPONSE_DUE_SOON = "first_response_due_soon", "First response due soon"
        FIRST_RESPONSE_OVERDUE = "first_response_overdue", "First response overdue"
        NEXT_RESPONSE_DUE_SOON = "next_response_due_soon", "Next response due soon"
        NEXT_RESPONSE_OVERDUE = "next_response_overdue", "Next response overdue"
        RESOLUTION_DUE_SOON = "resolution_due_soon", "Resolution due soon"
        RESOLUTION_OVERDUE = "resolution_overdue", "Resolution overdue"
        FOLLOW_UP_DUE = "follow_up_due", "Follow-up due"

    class Status(models.TextChoices):
        UNREAD = "unread", "Unread"
        READ = "read", "Read"
        RESOLVED = "resolved", "Resolved"

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="service_alerts",
    )
    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="service_alerts",
    )
    support_conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="service_alerts",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_service_alerts",
    )
    kind = models.CharField(max_length=40, choices=Kind.choices, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNREAD, db_index=True)
    due_at = models.DateTimeField()
    triggered_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=64, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-triggered_at"]
        indexes = [
            models.Index(fields=["recipient", "status", "triggered_at"], name="sup_alert_user_stat_time_idx"),
            models.Index(fields=["support_account", "status", "due_at"], name="sup_alert_acct_stat_due_idx"),
            models.Index(fields=["support_conversation", "kind"], name="sup_alert_conv_kind_idx"),
        ]

    def __str__(self):
        return f"SupportServiceAlert<{self.recipient_id}:{self.kind}:{self.status}>"



class SupportFeedbackSettings(BaseUUIDModel):
    """Account-level customer feedback controls for Support Chat."""

    support_account = models.OneToOneField(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="feedback_settings",
    )
    csat_enabled = models.BooleanField(default=True)
    auto_request_on_resolve = models.BooleanField(default=True)
    allow_comment = models.BooleanField(default=True)
    survey_expiry_days = models.PositiveSmallIntegerField(default=30)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_feedback_settings_updated",
    )

    class Meta:
        verbose_name_plural = "Support feedback settings"

    def clean(self):
        if self.survey_expiry_days < 1 or self.survey_expiry_days > 365:
            raise ValidationError({"survey_expiry_days": "Choose a value between 1 and 365 days."})

    def __str__(self):
        return f"SupportFeedbackSettings<{self.support_account_id}>"


class SupportCSATSurvey(BaseUUIDModel):
    """One customer-satisfaction request for a resolved Support conversation."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUBMITTED = "submitted", "Submitted"
        DISMISSED = "dismissed", "Dismissed"
        EXPIRED = "expired", "Expired"

    class Source(models.TextChoices):
        AUTO = "auto", "Automatic"
        MANUAL = "manual", "Manual"

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="csat_surveys",
    )
    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="csat_surveys",
    )
    support_conversation = models.OneToOneField(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="csat_survey",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.AUTO)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    comment = models.TextField(max_length=2000, blank=True)
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_csat_requests_created",
    )

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__isnull=True) | (Q(rating__gte=1) & Q(rating__lte=5)),
                name="support_csat_rating_1_to_5",
            ),
        ]
        indexes = [
            models.Index(fields=["support_account", "status", "requested_at"], name="sup_csat_acct_stat_req_idx"),
            models.Index(fields=["website", "status", "submitted_at"], name="sup_csat_site_stat_sub_idx"),
        ]

    def clean(self):
        errors = {}
        if self.support_conversation_id:
            if self.support_conversation.website.support_account_id != self.support_account_id:
                errors["support_account"] = "The survey account must match the conversation account."
            if self.support_conversation.website_id != self.website_id:
                errors["website"] = "The survey website must match the conversation website."
        if self.rating is not None and not 1 <= int(self.rating) <= 5:
            errors["rating"] = "Choose a rating from 1 to 5."
        if self.status == self.Status.SUBMITTED and self.rating is None:
            errors["rating"] = "A submitted survey requires a rating."
        if errors:
            raise ValidationError(errors)

    @property
    def is_available(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()

    def __str__(self):
        return f"SupportCSATSurvey<{self.support_conversation_id}:{self.status}>"


class SupportKnowledgeSettings(BaseUUIDModel):
    """Account-level controls for visitor self-service and team knowledge tools."""

    support_account = models.OneToOneField(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="knowledge_settings",
    )
    enabled = models.BooleanField(default=True)
    show_in_widget = models.BooleanField(default=True)
    suggestions_enabled = models.BooleanField(default=True)
    allow_article_feedback = models.BooleanField(default=True)
    max_suggestions = models.PositiveSmallIntegerField(default=5)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_knowledge_settings_updated",
    )

    class Meta:
        verbose_name_plural = "Support knowledge settings"

    def clean(self):
        if self.max_suggestions < 1 or self.max_suggestions > 10:
            raise ValidationError({"max_suggestions": "Choose a value between 1 and 10."})

    def __str__(self):
        return f"SupportKnowledgeSettings<{self.support_account_id}>"


class SupportKnowledgeCategory(BaseUUIDModel):
    """Owner-managed category shared by articles in one Support account."""

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="knowledge_categories",
    )
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_knowledge_categories_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_knowledge_categories_updated",
    )

    class Meta:
        ordering = ["sort_order", "name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "support_account",
                condition=Q(is_active=True),
                name="uniq_sup_active_kb_category_ci",
            ),
        ]
        indexes = [
            models.Index(fields=["support_account", "is_active", "sort_order"], name="sup_kb_cat_acct_active_idx"),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.description = (self.description or "").strip()
        if not self.name:
            raise ValidationError({"name": "Enter a category name."})

    def __str__(self):
        return self.name


class SupportKnowledgeArticle(BaseUUIDModel):
    """A public Support answer that may be shared across or limited to websites."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="knowledge_articles",
    )
    category = models.ForeignKey(
        SupportKnowledgeCategory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="articles",
    )
    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200)
    summary = models.CharField(max_length=320, blank=True)
    body = models.TextField(max_length=30000)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    all_websites = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_knowledge_articles_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_knowledge_articles_updated",
    )
    view_count = models.PositiveIntegerField(default=0)
    helpful_count = models.PositiveIntegerField(default=0)
    not_helpful_count = models.PositiveIntegerField(default=0)
    websites = models.ManyToManyField(
        SupportWebsite,
        through="SupportKnowledgeArticleWebsite",
        related_name="knowledge_articles",
        blank=True,
    )

    class Meta:
        ordering = ["-is_featured", "title", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["support_account", "slug"], name="uniq_support_kb_article_slug"),
        ]
        indexes = [
            models.Index(fields=["support_account", "status", "is_featured"], name="sup_kb_art_acct_status_idx"),
            models.Index(fields=["category", "status"], name="sup_kb_art_cat_status_idx"),
            models.Index(fields=["published_at"], name="sup_kb_art_published_idx"),
        ]

    def clean(self):
        self.title = (self.title or "").strip()
        self.summary = (self.summary or "").strip()
        self.body = (self.body or "").strip()
        if not self.title:
            raise ValidationError({"title": "Enter an article title."})
        if not self.body:
            raise ValidationError({"body": "Enter the article content."})
        if self.category_id and self.category.support_account_id != self.support_account_id:
            raise ValidationError({"category": "The category must belong to the same Support account."})

    def __str__(self):
        return self.title


class SupportKnowledgeArticleWebsite(BaseUUIDModel):
    """Explicit website availability for an article that is not account-wide."""

    article = models.ForeignKey(
        SupportKnowledgeArticle,
        on_delete=models.CASCADE,
        related_name="website_assignments",
    )
    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="knowledge_article_assignments",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["article", "website"], name="uniq_support_kb_article_website"),
        ]
        indexes = [
            models.Index(fields=["website", "article"], name="sup_kb_site_article_idx"),
        ]

    def clean(self):
        if self.article_id and self.website_id and self.article.support_account_id != self.website.support_account_id:
            raise ValidationError("The article and website must belong to the same Support account.")

    def __str__(self):
        return f"SupportKnowledgeArticleWebsite<{self.article_id}:{self.website_id}>"


class SupportKnowledgeFeedback(BaseUUIDModel):
    """Origin-scoped, pseudonymous helpfulness response for one public article."""

    article = models.ForeignKey(
        SupportKnowledgeArticle,
        on_delete=models.CASCADE,
        related_name="feedback_entries",
    )
    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="knowledge_feedback_entries",
    )
    client_key_hash = models.CharField(max_length=64)
    helpful = models.BooleanField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["article", "website", "client_key_hash"],
                name="uniq_support_kb_client_feedback",
            ),
        ]
        indexes = [
            models.Index(fields=["website", "created_at"], name="sup_kb_feedback_site_idx"),
        ]

    def clean(self):
        if self.article_id and self.website_id:
            if self.article.support_account_id != self.website.support_account_id:
                raise ValidationError("The feedback article and website must belong to the same Support account.")

    def __str__(self):
        return f"SupportKnowledgeFeedback<{self.article_id}:{self.helpful}>"


class SupportPrivacySettings(BaseUUIDModel):
    """Owner-managed retention and privacy controls for Support Chat only."""

    support_account = models.OneToOneField(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="privacy_settings",
    )
    retention_enabled = models.BooleanField(default=False)
    resolved_conversation_retention_days = models.PositiveIntegerField(default=730)
    widget_session_retention_days = models.PositiveIntegerField(default=90)
    export_retention_days = models.PositiveSmallIntegerField(default=7)
    allow_visitor_deletion_requests = models.BooleanField(default=True)
    include_attachments_in_exports = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_privacy_settings_updated",
    )

    class Meta:
        verbose_name_plural = "Support privacy settings"

    def clean(self):
        errors = {}
        if not 30 <= int(self.resolved_conversation_retention_days or 0) <= 3650:
            errors["resolved_conversation_retention_days"] = "Choose a value between 30 and 3,650 days."
        if not 7 <= int(self.widget_session_retention_days or 0) <= 730:
            errors["widget_session_retention_days"] = "Choose a value between 7 and 730 days."
        if not 1 <= int(self.export_retention_days or 0) <= 30:
            errors["export_retention_days"] = "Choose a value between 1 and 30 days."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"SupportPrivacySettings<{self.support_account_id}>"


class SupportWebhookEndpoint(BaseUUIDModel):
    """Owner-configured HTTPS destination for Support-only event delivery."""

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
    )
    name = models.CharField(max_length=120)
    url = models.URLField(max_length=1000)
    signing_secret = models.CharField(max_length=128)
    event_types = models.JSONField(default=list)
    is_active = models.BooleanField(default=True, db_index=True)
    failure_count = models.PositiveIntegerField(default=0)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_webhook_endpoints_created",
    )

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["support_account", "name"], name="uniq_support_webhook_name"),
        ]
        indexes = [
            models.Index(fields=["support_account", "is_active"], name="sup_hook_acct_active_idx"),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.url = (self.url or "").strip()
        if not self.name:
            raise ValidationError({"name": "Enter a webhook name."})
        if not isinstance(self.event_types, list):
            raise ValidationError({"event_types": "Choose one or more supported events."})

    def __str__(self):
        return f"SupportWebhookEndpoint<{self.support_account_id}:{self.name}>"


class SupportWebhookDelivery(BaseUUIDModel):
    """Durable, retryable delivery record for one outbound Support webhook."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    endpoint = models.ForeignKey(
        SupportWebhookEndpoint,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event_type = models.CharField(max_length=80, db_index=True)
    event_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.CharField(max_length=1000, blank=True)
    error = models.CharField(max_length=1000, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="sup_hook_del_stat_next_idx"),
            models.Index(fields=["endpoint", "created_at"], name="sup_hook_del_ep_time_idx"),
        ]

    def __str__(self):
        return f"SupportWebhookDelivery<{self.event_type}:{self.status}>"


class SupportDataExport(BaseUUIDModel):
    """Owner-requested Support-only export stored privately for a short period."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="data_exports",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_data_exports_requested",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    file = models.FileField(upload_to="support/exports/%Y/%m/%d/", max_length=500, blank=True)
    file_size = models.BigIntegerField(default=0)
    record_counts = models.JSONField(default=dict, blank=True)
    include_attachments = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    error = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["support_account", "status", "created_at"], name="sup_export_acct_stat_idx"),
            models.Index(fields=["status", "expires_at"], name="sup_export_stat_exp_idx"),
        ]

    def __str__(self):
        return f"SupportDataExport<{self.support_account_id}:{self.status}>"


class SupportVisitorDeletionRequest(BaseUUIDModel):
    """Auditable request to erase one website visitor and their Support conversation data."""

    class Source(models.TextChoices):
        OWNER = "owner", "Owner"
        VISITOR = "visitor", "Visitor"
        RETENTION = "retention", "Retention"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    support_account = models.ForeignKey(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="visitor_deletion_requests",
    )
    website = models.ForeignKey(
        SupportWebsite,
        on_delete=models.CASCADE,
        related_name="visitor_deletion_requests",
    )
    visitor = models.ForeignKey(
        SupportVisitor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deletion_requests",
    )
    visitor_external_id = models.UUIDField()
    source = models.CharField(max_length=16, choices=Source.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_visitor_deletions_requested",
    )
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["support_account", "status", "requested_at"], name="sup_delete_acct_stat_idx"),
            models.Index(fields=["website", "visitor_external_id"], name="sup_delete_site_ext_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["website", "visitor_external_id"],
                condition=Q(status__in=["pending", "processing"]),
                name="uniq_active_support_visitor_delete",
            ),
        ]

    def clean(self):
        if self.website_id and self.support_account_id and self.website.support_account_id != self.support_account_id:
            raise ValidationError({"website": "The website must belong to the same Support Chat account."})
        if self.visitor_id and self.visitor.website_id != self.website_id:
            raise ValidationError({"visitor": "The visitor must belong to the selected website."})

    def __str__(self):
        return f"SupportVisitorDeletionRequest<{self.website_id}:{self.visitor_external_id}:{self.status}>"


class SupportCallSettings(BaseUUIDModel):
    """Account-level controls for visitor audio/video calls."""

    support_account = models.OneToOneField(
        SupportAccount,
        on_delete=models.CASCADE,
        related_name="call_settings",
    )
    enabled = models.BooleanField(default=True)
    allow_video = models.BooleanField(default=True)
    max_duration_minutes = models.PositiveSmallIntegerField(default=60)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_call_settings_updated",
    )

    class Meta:
        verbose_name_plural = "Support call settings"

    def clean(self):
        if not 5 <= int(self.max_duration_minutes or 0) <= 240:
            raise ValidationError({"max_duration_minutes": "Choose a value between 5 and 240 minutes."})


class SupportCallSession(BaseUUIDModel):
    """One-to-one guest call between a Support team user and a website visitor."""

    class CallType(models.TextChoices):
        VOICE = "voice", "Voice"
        VIDEO = "video", "Video"

    class Status(models.TextChoices):
        RINGING = "ringing", "Ringing"
        ONGOING = "ongoing", "Ongoing"
        DECLINED = "declined", "Declined"
        MISSED = "missed", "Missed"
        ENDED = "ended", "Ended"
        FAILED = "failed", "Failed"

    class InitiatorKind(models.TextChoices):
        TEAM = "team", "Support team"
        VISITOR = "visitor", "Website visitor"

    support_conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="calls",
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="initiated_support_calls",
    )
    initiator_kind = models.CharField(
        max_length=16,
        choices=InitiatorKind.choices,
        default=InitiatorKind.TEAM,
    )
    call_type = models.CharField(max_length=16, choices=CallType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RINGING, db_index=True)
    room_key = models.CharField(max_length=64, unique=True, db_index=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(max_length=64, blank=True)
    last_signal_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["support_conversation", "-started_at"], name="sup_call_conv_time_idx"),
            models.Index(fields=["status", "-started_at"], name="sup_call_status_time_idx"),
            models.Index(fields=["initiated_by", "status"], name="sup_call_user_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["support_conversation"],
                condition=Q(status__in=["ringing", "ongoing"]),
                name="uniq_active_support_call_conversation",
            ),
            models.UniqueConstraint(
                fields=["initiated_by"],
                condition=Q(status__in=["ringing", "ongoing"]),
                name="uniq_active_support_call_initiator",
            ),
        ]

    @property
    def is_active(self):
        return self.status in {self.Status.RINGING, self.Status.ONGOING}


class SupportCallParticipant(BaseUUIDModel):
    class Kind(models.TextChoices):
        TEAM = "team", "Support team"
        VISITOR = "visitor", "Website visitor"

    class State(models.TextChoices):
        RINGING = "ringing", "Ringing"
        JOINED = "joined", "Joined"
        DECLINED = "declined", "Declined"
        MISSED = "missed", "Missed"
        LEFT = "left", "Left"

    call = models.ForeignKey(SupportCallSession, on_delete=models.CASCADE, related_name="participants")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="support_call_participations",
    )
    visitor = models.ForeignKey(
        SupportVisitor,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="call_participations",
    )
    state = models.CharField(max_length=16, choices=State.choices)
    audio_enabled = models.BooleanField(default=True)
    video_enabled = models.BooleanField(default=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["call", "kind"], name="sup_call_part_kind_idx"),
            models.Index(fields=["user", "state"], name="sup_call_part_user_idx"),
            models.Index(fields=["visitor", "state"], name="sup_call_part_visit_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(kind="team", user__isnull=False, visitor__isnull=True)
                    | Q(kind="visitor", user__isnull=True, visitor__isnull=False)
                ),
                name="support_call_participant_identity",
            ),
            models.UniqueConstraint(
                fields=["call", "kind"],
                name="uniq_support_call_participant_kind",
            ),
        ]


class SupportCallSignal(BaseUUIDModel):
    """Short-lived persisted WebRTC signaling for reconnect and polling fallback."""

    call = models.ForeignKey(SupportCallSession, on_delete=models.CASCADE, related_name="signals")
    sender_kind = models.CharField(max_length=16, choices=SupportCallParticipant.Kind.choices)
    sender_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="support_call_signals_sent",
    )
    sender_visitor = models.ForeignKey(
        SupportVisitor,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="call_signals_sent",
    )
    recipient_kind = models.CharField(max_length=16, choices=SupportCallParticipant.Kind.choices)
    signal_id = models.CharField(max_length=64, unique=True, db_index=True)
    signal_type = models.CharField(max_length=32)
    payload = models.JSONField(default=dict, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["call", "recipient_kind", "created_at"], name="sup_call_signal_recv_idx"),
            models.Index(fields=["call", "consumed_at"], name="sup_call_signal_cons_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(sender_kind="team", sender_user__isnull=False, sender_visitor__isnull=True)
                    | Q(sender_kind="visitor", sender_user__isnull=True, sender_visitor__isnull=False)
                ),
                name="support_call_signal_sender_identity",
            ),
        ]
