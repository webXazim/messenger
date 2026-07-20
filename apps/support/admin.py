from django.contrib import admin

from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportAgentInvitation,
    SupportAgentInvitationWebsite,
    SupportAgentInvitationTeam,
    SupportTeam,
    SupportTeamMembership,
    SupportRoutingPolicy,
    SupportRoutingCursor,
    SupportWebsiteTeam,
    SupportConversation,
    SupportConversationReadState,
    SupportMessageAuthor,
    SupportPendingUpload,
    SupportTag,
    SupportConversationTag,
    SupportInternalNote,
    SupportConversationFollower,
    SupportInternalNoteMention,
    SupportConversationTransfer,
    SupportCannedReply,
    SupportSavedInboxView,
    SupportAuditEvent,
    SupportServiceAlert,
    SupportServiceSettings,
    SupportSlaPolicy,
    SupportAnalyticsDailyMetric,
    SupportAnalyticsHourlyMetric,
    SupportAnalyticsTagMetric,
    SupportAnalyticsExport,
    SupportNotificationSettings,
    SupportSecuritySettings,
    SupportAutomationRule,
    SupportAutomationExecution,
    SupportFeedbackSettings,
    SupportCSATSurvey,
    SupportKnowledgeSettings,
    SupportKnowledgeCategory,
    SupportKnowledgeArticle,
    SupportKnowledgeArticleWebsite,
    SupportKnowledgeArticleRevision,
    SupportKnowledgeRelatedArticle,
    SupportKnowledgeFeedback,
    SupportPrivacySettings,
    SupportWebhookEndpoint,
    SupportWebhookDelivery,
    SupportDataExport,
    SupportVisitorDeletionRequest,
    SupportWebsite,
    SupportWebsiteAgent,
    SupportVisitor,
    SupportWidgetSession,
    SupportWidgetSettings,
    SupportCallSettings,
    SupportCallSession,
    SupportCallParticipant,
    SupportCallSignal,
)


@admin.register(SupportAccount)
class SupportAccountAdmin(admin.ModelAdmin):
    list_display = ("owner", "status", "plan_code", "website_limit", "agent_limit", "current_period_end")
    list_filter = ("status", "plan_code")
    search_fields = ("owner__username", "owner__email", "plan_code")
    autocomplete_fields = ("owner",)




class SupportTeamMembershipInline(admin.TabularInline):
    model = SupportTeamMembership
    extra = 0
    autocomplete_fields = ("agent",)


class SupportWebsiteTeamInline(admin.TabularInline):
    model = SupportWebsiteTeam
    extra = 0
    autocomplete_fields = ("website",)


@admin.register(SupportTeam)
class SupportTeamAdmin(admin.ModelAdmin):
    list_display = ("name", "support_account", "is_active", "default_max_active_conversations")
    list_filter = ("is_active",)
    search_fields = ("name", "support_account__owner__email")
    autocomplete_fields = ("support_account", "created_by")
    inlines = (SupportTeamMembershipInline, SupportWebsiteTeamInline)

@admin.register(SupportAgent)
class SupportAgentAdmin(admin.ModelAdmin):
    list_display = ("user", "support_account", "availability", "is_active", "can_assign_conversations")
    list_filter = ("availability", "is_active", "can_view_all_conversations", "can_assign_conversations")
    search_fields = ("user__username", "user__email", "support_account__owner__email")
    autocomplete_fields = ("support_account", "user", "invited_by")


@admin.register(SupportWebsite)
class SupportWebsiteAdmin(admin.ModelAdmin):
    list_display = ("name", "domain", "support_account", "widget_enabled", "is_active")
    list_filter = ("widget_enabled", "is_active")
    search_fields = ("name", "domain", "support_account__owner__email")
    autocomplete_fields = ("support_account", "created_by")
    readonly_fields = ("site_key",)


@admin.register(SupportWebsiteAgent)
class SupportWebsiteAgentAdmin(admin.ModelAdmin):
    list_display = ("website", "agent", "created_at")
    autocomplete_fields = ("website", "agent")


class SupportAgentInvitationWebsiteInline(admin.TabularInline):
    model = SupportAgentInvitationWebsite
    extra = 0
    autocomplete_fields = ("website",)


@admin.register(SupportAgentInvitation)
class SupportAgentInvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "support_account", "status", "expires_at", "send_count", "last_sent_at")
    list_filter = ("status", "can_assign_conversations", "can_view_analytics")
    search_fields = ("email", "support_account__owner__email", "invited_by__email")
    autocomplete_fields = ("support_account", "invited_by", "accepted_by")
    readonly_fields = ("token_hash", "accepted_at", "revoked_at", "last_sent_at", "send_count")
    inlines = (SupportAgentInvitationWebsiteInline,)


@admin.register(SupportWidgetSettings)
class SupportWidgetSettingsAdmin(admin.ModelAdmin):
    list_display = ("website", "brand_name", "position", "theme", "require_name", "require_email")
    list_filter = ("position", "theme", "require_name", "require_email", "allow_attachments")
    search_fields = ("website__name", "website__domain", "brand_name")
    autocomplete_fields = ("website",)


@admin.register(SupportVisitor)
class SupportVisitorAdmin(admin.ModelAdmin):
    list_display = ("external_id", "website", "name", "email", "last_seen_at", "is_blocked")
    list_filter = ("is_blocked", "website")
    search_fields = ("name", "email", "external_id", "website__domain")
    autocomplete_fields = ("website",)
    readonly_fields = ("external_id", "first_seen_at", "last_seen_at")


@admin.register(SupportWidgetSession)
class SupportWidgetSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "website", "visitor", "status", "origin", "expires_at", "last_seen_at")
    list_filter = ("status", "website")
    search_fields = ("visitor__email", "visitor__name", "origin", "website__domain")
    autocomplete_fields = ("website", "visitor")
    readonly_fields = ("token_hash", "token_version", "origin", "expires_at", "last_seen_at", "closed_at")


@admin.register(SupportConversation)
class SupportConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "website", "visitor", "assigned_agent", "status", "priority", "updated_at")
    list_filter = ("status", "priority", "website")
    search_fields = ("visitor__name", "visitor__email", "subject", "website__domain")
    autocomplete_fields = (
        "conversation",
        "website",
        "visitor",
        "assigned_agent",
        "visitor_last_read_message",
    )
    readonly_fields = (
        "first_response_at", "last_visitor_message_at", "last_agent_message_at",
        "first_response_due_at", "next_response_due_at", "resolution_due_at",
        "first_response_breached_at", "next_response_breached_at",
        "resolution_breached_at", "follow_up_completed_at", "resolved_at", "closed_at",
    )


@admin.register(SupportMessageAuthor)
class SupportMessageAuthorAdmin(admin.ModelAdmin):
    list_display = ("message", "visitor", "session", "display_name", "created_at")
    search_fields = ("display_name", "visitor__name", "visitor__email", "message__text")
    autocomplete_fields = ("message", "visitor", "session")


@admin.register(SupportConversationReadState)
class SupportConversationReadStateAdmin(admin.ModelAdmin):
    list_display = ("support_conversation", "user", "last_read_at", "updated_at")
    search_fields = ("user__email", "support_conversation__visitor__name", "support_conversation__website__domain")
    autocomplete_fields = ("support_conversation", "user", "last_read_message")


@admin.register(SupportPendingUpload)
class SupportPendingUploadAdmin(admin.ModelAdmin):
    list_display = ("pending_upload", "website", "source", "uploaded_by", "visitor", "created_at")
    list_filter = ("source", "website")
    search_fields = ("pending_upload__original_name", "uploaded_by__email", "visitor__email", "website__domain")
    autocomplete_fields = (
        "pending_upload",
        "support_account",
        "website",
        "support_conversation",
        "uploaded_by",
        "widget_session",
        "visitor",
    )


@admin.register(SupportTag)
class SupportTagAdmin(admin.ModelAdmin):
    list_display = ("name", "support_account", "color", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "support_account__owner__email")
    autocomplete_fields = ("support_account", "created_by")


@admin.register(SupportConversationTag)
class SupportConversationTagAdmin(admin.ModelAdmin):
    list_display = ("support_conversation", "tag", "added_by", "created_at")
    search_fields = ("support_conversation__visitor__name", "tag__name")
    autocomplete_fields = ("support_conversation", "tag", "added_by")


@admin.register(SupportInternalNote)
class SupportInternalNoteAdmin(admin.ModelAdmin):
    list_display = ("support_conversation", "author", "created_at")
    search_fields = ("body", "support_conversation__visitor__name", "author__email")
    autocomplete_fields = ("support_conversation", "author")
    readonly_fields = ("support_conversation", "author", "body", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SupportCannedReply)
class SupportCannedReplyAdmin(admin.ModelAdmin):
    list_display = ("shortcut", "title", "support_account", "website", "is_active")
    list_filter = ("is_active", "website")
    search_fields = ("shortcut", "title", "body", "support_account__owner__email")
    autocomplete_fields = ("support_account", "website", "created_by", "updated_by")


@admin.register(SupportSavedInboxView)
class SupportSavedInboxViewAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "support_account", "website", "queue", "is_default")
    list_filter = ("is_default", "queue", "priority")
    search_fields = ("name", "user__email", "support_account__owner__email")
    autocomplete_fields = ("support_account", "user", "website", "tag")


@admin.register(SupportAuditEvent)
class SupportAuditEventAdmin(admin.ModelAdmin):
    list_display = ("action", "summary", "actor", "website", "support_conversation", "created_at")
    list_filter = ("action", "website")
    search_fields = ("summary", "actor__email", "support_conversation__visitor__name")
    autocomplete_fields = ("support_account", "website", "support_conversation", "actor")
    readonly_fields = (
        "support_account", "website", "support_conversation", "actor", "action",
        "target_type", "target_id", "summary", "metadata", "ip_address",
        "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(SupportServiceSettings)
class SupportServiceSettingsAdmin(admin.ModelAdmin):
    list_display = ("support_account", "timezone", "business_hours_enabled", "due_soon_minutes", "updated_at")
    list_filter = ("business_hours_enabled", "alert_owner", "alert_assigned_agent")
    search_fields = ("support_account__owner__email", "support_account__owner__username", "timezone")
    autocomplete_fields = ("support_account", "updated_by")


@admin.register(SupportServiceAlert)
class SupportServiceAlertAdmin(admin.ModelAdmin):
    list_display = ("kind", "recipient", "website", "status", "due_at", "triggered_at")
    list_filter = ("kind", "status", "website")
    search_fields = ("recipient__email", "support_conversation__visitor__name", "website__domain")
    autocomplete_fields = ("support_account", "website", "support_conversation", "recipient")
    readonly_fields = (
        "support_account", "website", "support_conversation", "recipient", "kind",
        "status", "due_at", "triggered_at", "read_at", "resolved_at",
        "dedupe_key", "metadata", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False



@admin.register(SupportFeedbackSettings)
class SupportFeedbackSettingsAdmin(admin.ModelAdmin):
    list_display = ("support_account", "csat_enabled", "auto_request_on_resolve", "allow_comment", "survey_expiry_days", "updated_at")
    list_filter = ("csat_enabled", "auto_request_on_resolve", "allow_comment")
    search_fields = ("support_account__owner__email", "support_account__owner__username")
    autocomplete_fields = ("support_account", "updated_by")


@admin.register(SupportCSATSurvey)
class SupportCSATSurveyAdmin(admin.ModelAdmin):
    list_display = ("support_conversation", "website", "status", "rating", "source", "requested_at", "submitted_at")
    list_filter = ("status", "rating", "source", "website")
    search_fields = ("support_conversation__visitor__name", "support_conversation__visitor__email", "comment", "website__domain")
    autocomplete_fields = ("support_account", "website", "support_conversation", "requested_by")
    readonly_fields = ("support_account", "website", "support_conversation", "source", "requested_at", "expires_at", "submitted_at", "dismissed_at", "created_at", "updated_at")


@admin.register(SupportKnowledgeSettings)
class SupportKnowledgeSettingsAdmin(admin.ModelAdmin):
    list_display = ("support_account", "enabled", "show_in_widget", "suggestions_enabled", "allow_article_feedback", "updated_at")
    list_filter = ("enabled", "show_in_widget", "suggestions_enabled", "allow_article_feedback")
    search_fields = ("support_account__owner__email", "support_account__owner__username")
    autocomplete_fields = ("support_account", "updated_by")


@admin.register(SupportKnowledgeCategory)
class SupportKnowledgeCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "support_account", "sort_order", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "description", "support_account__owner__email")
    autocomplete_fields = ("support_account", "created_by", "updated_by")




class SupportKnowledgeRelatedArticleInline(admin.TabularInline):
    model = SupportKnowledgeRelatedArticle
    fk_name = "article"
    extra = 0
    autocomplete_fields = ("related_article",)


class SupportKnowledgeArticleRevisionInline(admin.TabularInline):
    model = SupportKnowledgeArticleRevision
    extra = 0
    can_delete = False
    readonly_fields = ("version", "title", "status", "change_note", "created_by", "created_at")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


class SupportKnowledgeArticleWebsiteInline(admin.TabularInline):
    model = SupportKnowledgeArticleWebsite
    extra = 0
    autocomplete_fields = ("website",)


@admin.register(SupportKnowledgeArticle)
class SupportKnowledgeArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "support_account", "category", "status", "all_websites", "is_featured", "published_at")
    list_filter = ("status", "all_websites", "is_featured", "category")
    search_fields = ("title", "summary", "body", "support_account__owner__email")
    autocomplete_fields = ("support_account", "category", "created_by", "updated_by")
    readonly_fields = ("slug", "published_at", "view_count", "helpful_count", "not_helpful_count")
    inlines = (SupportKnowledgeArticleWebsiteInline, SupportKnowledgeRelatedArticleInline, SupportKnowledgeArticleRevisionInline)


@admin.register(SupportKnowledgeFeedback)
class SupportKnowledgeFeedbackAdmin(admin.ModelAdmin):
    list_display = ("article", "website", "helpful", "created_at")
    list_filter = ("helpful", "website")
    search_fields = ("article__title", "website__domain")
    autocomplete_fields = ("article", "website")
    readonly_fields = ("article", "website", "client_key_hash", "helpful", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SupportPrivacySettings)
class SupportPrivacySettingsAdmin(admin.ModelAdmin):
    list_display = (
        "support_account", "retention_enabled", "resolved_conversation_retention_days",
        "widget_session_retention_days", "export_retention_days", "updated_at",
    )
    list_filter = ("retention_enabled", "allow_visitor_deletion_requests", "include_attachments_in_exports")
    search_fields = ("support_account__owner__email", "support_account__owner__username")
    autocomplete_fields = ("support_account", "updated_by")


@admin.register(SupportWebhookEndpoint)
class SupportWebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ("name", "support_account", "url", "is_active", "failure_count", "last_success_at")
    list_filter = ("is_active",)
    search_fields = ("name", "url", "support_account__owner__email")
    autocomplete_fields = ("support_account", "created_by")
    readonly_fields = ("signing_secret", "failure_count", "last_delivery_at", "last_success_at", "last_failure_at")


@admin.register(SupportWebhookDelivery)
class SupportWebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ("event_type", "endpoint", "status", "attempt_count", "response_status", "created_at")
    list_filter = ("status", "event_type")
    search_fields = ("endpoint__name", "endpoint__support_account__owner__email", "error")
    autocomplete_fields = ("endpoint",)
    readonly_fields = (
        "endpoint", "event_type", "event_id", "payload", "status", "attempt_count",
        "next_attempt_at", "response_status", "response_body", "error", "delivered_at",
        "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SupportDataExport)
class SupportDataExportAdmin(admin.ModelAdmin):
    list_display = ("support_account", "status", "requested_by", "file_size", "expires_at", "created_at")
    list_filter = ("status", "include_attachments")
    search_fields = ("support_account__owner__email", "requested_by__email", "error")
    autocomplete_fields = ("support_account", "requested_by")
    readonly_fields = (
        "support_account", "requested_by", "status", "file", "file_size", "record_counts",
        "include_attachments", "started_at", "completed_at", "expires_at", "error",
        "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SupportVisitorDeletionRequest)
class SupportVisitorDeletionRequestAdmin(admin.ModelAdmin):
    list_display = ("website", "visitor_external_id", "source", "status", "requested_by", "requested_at")
    list_filter = ("source", "status", "website")
    search_fields = ("visitor_external_id", "website__domain", "support_account__owner__email", "error")
    autocomplete_fields = ("support_account", "website", "visitor", "requested_by")
    readonly_fields = (
        "support_account", "website", "visitor", "visitor_external_id", "source", "status",
        "requested_by", "requested_at", "completed_at", "error", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SupportCallSettings)
class SupportCallSettingsAdmin(admin.ModelAdmin):
    list_display = ("support_account", "enabled", "allow_video", "max_duration_minutes", "updated_at")
    list_filter = ("enabled", "allow_video")
    search_fields = ("support_account__owner__email", "support_account__owner__username")
    autocomplete_fields = ("support_account", "updated_by")


class SupportCallParticipantInline(admin.TabularInline):
    model = SupportCallParticipant
    extra = 0
    readonly_fields = ("kind", "user", "visitor", "state", "audio_enabled", "video_enabled", "joined_at", "left_at", "last_seen_at")
    can_delete = False


@admin.register(SupportCallSession)
class SupportCallSessionAdmin(admin.ModelAdmin):
    list_display = ("support_conversation", "call_type", "status", "initiated_by", "started_at", "answered_at", "ended_at")
    list_filter = ("call_type", "status")
    search_fields = ("support_conversation__visitor__name", "support_conversation__website__domain", "initiated_by__email")
    autocomplete_fields = ("support_conversation", "initiated_by")
    readonly_fields = ("room_key", "started_at", "answered_at", "ended_at", "last_signal_at", "metadata", "created_at", "updated_at")
    inlines = (SupportCallParticipantInline,)


@admin.register(SupportCallSignal)
class SupportCallSignalAdmin(admin.ModelAdmin):
    list_display = ("call", "signal_type", "sender_kind", "recipient_kind", "created_at", "consumed_at")
    list_filter = ("signal_type", "sender_kind", "recipient_kind")
    search_fields = ("signal_id", "call__support_conversation__website__domain")
    readonly_fields = ("call", "sender_kind", "sender_user", "sender_visitor", "recipient_kind", "signal_id", "signal_type", "payload", "consumed_at", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SupportRoutingPolicy)
class SupportRoutingPolicyAdmin(admin.ModelAdmin):
    list_display = ("website", "mode", "overflow_behavior", "enabled", "offline_reassignment_minutes")
    list_filter = ("mode", "overflow_behavior", "enabled")
    search_fields = ("website__name", "website__domain")

@admin.register(SupportRoutingCursor)
class SupportRoutingCursorAdmin(admin.ModelAdmin):
    list_display = ("policy", "last_assigned_agent", "assignment_count", "updated_at")


@admin.register(SupportKnowledgeArticleRevision)
class SupportKnowledgeArticleRevisionAdmin(admin.ModelAdmin):
    list_display = ("article", "version", "status", "created_by", "created_at")
    list_filter = ("status", "language")
    search_fields = ("article__title", "title", "change_note")
    readonly_fields = ("article", "version", "title", "summary", "seo_description", "language", "body", "status", "category_name", "all_websites", "website_ids", "is_featured", "change_note", "created_by", "created_at", "updated_at")


@admin.register(SupportKnowledgeRelatedArticle)
class SupportKnowledgeRelatedArticleAdmin(admin.ModelAdmin):
    list_display = ("article", "related_article", "sort_order")
    search_fields = ("article__title", "related_article__title")
    autocomplete_fields = ("article", "related_article")


@admin.register(SupportConversationFollower)
class SupportConversationFollowerAdmin(admin.ModelAdmin):
    list_display = ("support_conversation", "user", "created_at")
    search_fields = ("user__username", "support_conversation__subject")


@admin.register(SupportConversationTransfer)
class SupportConversationTransferAdmin(admin.ModelAdmin):
    list_display = ("support_conversation", "from_agent", "to_agent", "from_team", "to_team", "created_at")
    list_filter = ("from_team", "to_team")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SupportInternalNoteMention)
class SupportInternalNoteMentionAdmin(admin.ModelAdmin):
    list_display = ("note", "user", "created_at")


@admin.register(SupportSlaPolicy)
class SupportSlaPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "support_account", "website", "team", "is_active", "due_soon_minutes")
    list_filter = ("is_active", "pause_while_waiting_customer", "escalate_on_breach")
    search_fields = ("name", "support_account__owner__email", "website__name", "team__name")
    autocomplete_fields = ("support_account", "website", "team", "escalation_team", "updated_by")


@admin.register(SupportAnalyticsDailyMetric)
class SupportAnalyticsDailyMetricAdmin(admin.ModelAdmin):
    list_display = (
        "metric_date", "support_account", "website", "team", "agent",
        "conversations_created", "conversations_resolved", "sla_compliant_count",
    )
    list_filter = ("metric_date",)
    search_fields = (
        "support_account__owner__email", "website__name",
        "team__name", "agent__user__email",
    )
    readonly_fields = tuple(field.name for field in SupportAnalyticsDailyMetric._meta.fields)


@admin.register(SupportAnalyticsHourlyMetric)
class SupportAnalyticsHourlyMetricAdmin(admin.ModelAdmin):
    list_display = ("metric_date", "hour", "support_account", "website", "conversations_created")
    list_filter = ("metric_date", "hour")
    readonly_fields = tuple(field.name for field in SupportAnalyticsHourlyMetric._meta.fields)


@admin.register(SupportAnalyticsTagMetric)
class SupportAnalyticsTagMetricAdmin(admin.ModelAdmin):
    list_display = ("metric_date", "support_account", "website", "tag", "conversation_count")
    list_filter = ("metric_date",)
    readonly_fields = tuple(field.name for field in SupportAnalyticsTagMetric._meta.fields)


@admin.register(SupportAnalyticsExport)
class SupportAnalyticsExportAdmin(admin.ModelAdmin):
    list_display = ("created_at", "support_account", "requested_by", "status", "format", "completed_at")
    list_filter = ("status", "format")
    search_fields = ("support_account__owner__email", "requested_by__email")
    readonly_fields = ("created_at", "updated_at", "completed_at")


@admin.register(SupportNotificationSettings)
class SupportNotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ("support_account", "daily_summary", "daily_summary_hour", "updated_at")
    search_fields = ("support_account__owner__email",)


@admin.register(SupportSecuritySettings)
class SupportSecuritySettingsAdmin(admin.ModelAdmin):
    list_display = (
        "support_account", "max_attachment_mb",
        "retain_audit_days", "agent_session_timeout_minutes", "updated_at",
    )
    search_fields = ("support_account__owner__email",)


@admin.register(SupportAutomationRule)
class SupportAutomationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "support_account", "trigger", "priority", "is_active", "updated_at")
    list_filter = ("trigger", "is_active")
    search_fields = ("name", "support_account__owner__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SupportAutomationExecution)
class SupportAutomationExecutionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "rule", "trigger", "status", "actions_executed", "duration_ms")
    list_filter = ("status", "trigger")
    search_fields = ("rule__name", "support_account__owner__email", "idempotency_key")
    readonly_fields = tuple(field.name for field in SupportAutomationExecution._meta.fields)
