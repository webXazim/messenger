from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.api.serializers import UploadCreateSerializer, _media_kind_from_mime
from apps.chat.api.views import (
    _build_media_file_response,
    _build_thumbnail_file_response,
    _can_preview_inline_mime,
    _normalize_upload_media,
    _read_upload_initial_bytes,
)
from apps.chat.models import MessageAttachment, PendingUpload
from apps.chat.services import dispatch_pending_upload_scan, scan_upload_file
from apps.support.api.permissions import IsSupportOwner
from apps.support.api.serializers import (
    SupportAccountSerializer,
    SupportAgentAvailabilitySerializer,
    SupportAgentInvitationCreateSerializer,
    SupportAgentInvitationSerializer,
    SupportAgentSerializer,
    SupportAgentUpdateSerializer,
    SupportInvitationTokenSerializer,
    SupportConversationSerializer,
    SupportConversationUpdateSerializer,
    SupportMessageSendSerializer,
    SupportMessageSerializer,
    SupportPendingUploadSerializer,
    PublicWidgetConfigSerializer,
    SupportWebsiteSerializer,
    SupportWidgetSessionCreateSerializer,
    SupportWidgetSessionSerializer,
    SupportWidgetSessionUpdateSerializer,
    SupportWidgetSettingsSerializer,
    SupportWebsiteWidgetConfigurationSerializer,
    SupportTagSerializer,
    SupportInternalNoteSerializer,
    SupportCannedReplySerializer,
    SupportCannedReplyWriteSerializer,
    SupportSavedInboxViewSerializer,
    SupportSavedInboxViewWriteSerializer,
    SupportConversationTagsUpdateSerializer,
    SupportAuditEventSerializer,
    SupportServiceAlertSerializer,
    SupportServiceSettingsSerializer,
    SupportFeedbackSettingsSerializer,
    SupportCSATSurveySerializer,
    SupportCSATSubmitSerializer,
    SupportKnowledgeSettingsSerializer,
    SupportKnowledgeCategorySerializer,
    SupportKnowledgeArticleSerializer,
    SupportKnowledgeArticleWriteSerializer,
    PublicKnowledgeCategorySerializer,
    PublicKnowledgeArticleSerializer,
    PublicKnowledgeFeedbackSerializer,
    SupportPrivacySettingsSerializer,
    SupportWebhookEndpointSerializer,
    SupportWebhookDeliverySerializer,
    SupportDataExportSerializer,
    SupportVisitorDeletionRequestSerializer,
    SupportCallSettingsSerializer,
    SupportCallStartSerializer,
    SupportCallSignalWriteSerializer,
    SupportCallMediaStateSerializer,
    invitation_preview_payload,
)
from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportAgentInvitation,
    SupportConversation,
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
    SupportKnowledgeCategory,
    SupportKnowledgeArticle,
    SupportPrivacySettings,
    SupportWebhookEndpoint,
    SupportWebhookDelivery,
    SupportDataExport,
    SupportVisitorDeletionRequest,
    SupportVisitor,
    SupportWebsite,
    SupportCallSession,
    SupportCallParticipant,
)

SUPPORT_TRIAL_PLANS = {
    "starter": {"plan_code": "support-starter", "website_limit": 1, "agent_limit": 3},
    "growth": {"plan_code": "support-growth", "website_limit": 5, "agent_limit": 15},
    "scale": {"plan_code": "support-scale", "website_limit": 20, "agent_limit": 50},
}
from apps.support.conversation_services import (
    SupportConversationError,
    can_reply,
    claim_conversation,
    get_context_conversation,
    get_or_create_visitor_conversation,
    mark_team_read,
    mark_visitor_read,
    send_team_message,
    send_visitor_message,
    support_conversations_for_context,
    support_messages_qs,
    team_unread_count,
    update_conversation_workflow,
)
from apps.support.media_services import (
    SupportMediaError,
    SupportUploadOwner,
    register_support_pending_upload,
    support_upload_metadata,
)
from apps.support.services import (
    SupportServiceError,
    accept_agent_invitation,
    active_agent_count,
    create_agent_invitation,
    deactivate_agent,
    expire_stale_invitations,
    get_support_context,
    invitation_from_token,
    pending_invitation_count,
    resend_agent_invitation,
    revoke_agent_invitation,
    support_chat_enabled,
    update_agent,
    update_agent_availability,
    used_agent_seats,
    visible_websites,
)
from apps.support.workflow_services import (
    create_internal_note,
    record_audit_event,
    replace_conversation_tags,
    request_ip,
    save_default_view,
    visible_canned_replies,
    visible_tags,
)
from apps.support.analytics import SupportAnalyticsError, analytics_overview, parse_analytics_period
from apps.support.feedback_services import (
    SupportFeedbackError,
    feedback_settings_for,
    request_csat,
    submit_csat,
    dismiss_csat,
    survey_for_conversation,
)
from apps.support.service_operations import (
    follow_up_due_q,
    overdue_conversation_q,
    recalculate_account_targets,
    service_settings_for,
)
from apps.support.knowledge_services import (
    SupportKnowledgeError,
    knowledge_settings_for,
    public_articles_for_website,
    public_search_articles,
    record_article_feedback,
    record_article_view,
    replace_article_websites,
    search_article_queryset,
    team_articles_for_context,
    unique_article_slug,
    publish_state,
)
from apps.support.privacy_services import (
    SupportPrivacyError,
    create_support_export,
    generate_support_export,
    privacy_settings_for,
    process_visitor_deletion,
    request_visitor_deletion,
)
from apps.support.webhook_services import (
    SUPPORTED_WEBHOOK_EVENTS,
    SupportWebhookError,
    deliver_webhook,
    generate_webhook_secret,
    normalize_event_types,
    validate_webhook_url,
)

from apps.support.call_services import (
    SupportCallError,
    accept_support_call,
    call_event_payload,
    call_settings_for,
    create_call_signal,
    decline_support_call,
    end_support_call,
    pending_call_signals,
    signal_payload,
    start_support_call,
    support_calls_enabled,
    support_turn_credentials,
    team_turn_credentials,
    visitor_turn_credentials,
    team_active_call,
    team_active_call_for_conversation,
    team_call_for_context,
    update_call_media,
    visitor_call_for_session,
)

from apps.support.widget_services import (
    WidgetAccessError,
    assert_widget_request_allowed,
    authenticate_widget_session,
    close_widget_session,
    create_widget_session,
    refresh_widget_session,
    regenerate_website_site_key,
    request_origin,
    token_from_request,
    update_widget_session,
    website_for_public_widget,
    widget_settings_for,
)


def attach_support_context(request):
    context = get_support_context(request.user)
    request.support_context = context
    request.support_account = context.account
    return context


def service_error_response(error: SupportServiceError):
    code_status = {
        "access_inactive": status.HTTP_403_FORBIDDEN,
        "email_mismatch": status.HTTP_403_FORBIDDEN,
        "not_agent": status.HTTP_403_FORBIDDEN,
        "invalid_invitation": status.HTTP_404_NOT_FOUND,
        "invitation_unavailable": status.HTTP_410_GONE,
        "agent_limit": status.HTTP_409_CONFLICT,
        "already_invited": status.HTTP_409_CONFLICT,
        "already_owner": status.HTTP_409_CONFLICT,
        "already_agent": status.HTTP_409_CONFLICT,
        "already_accepted": status.HTTP_409_CONFLICT,
        "not_resendable": status.HTTP_409_CONFLICT,
        "owner_email": status.HTTP_409_CONFLICT,
    }
    return Response(
        {"detail": error.detail, "code": error.code},
        status=code_status.get(error.code, status.HTTP_400_BAD_REQUEST),
    )


def require_owner(request, view, *, require_access: bool = True):
    context = attach_support_context(request)
    if not support_chat_enabled() or not context.account:
        return context, Response({"detail": "Support Chat access is not active."}, status=status.HTTP_403_FORBIDDEN)
    permission = IsSupportOwner()
    if not permission.has_permission(request, view):
        return context, Response({"detail": permission.message}, status=status.HTTP_403_FORBIDDEN)
    if require_access and not context.account.has_product_access:
        return context, Response({"detail": "Support Chat access is not active."}, status=status.HTTP_403_FORBIDDEN)
    return context, None


class SupportServiceSettingsView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        settings_obj = service_settings_for(context.account)
        return Response(SupportServiceSettingsSerializer(settings_obj).data)

    def patch(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        settings_obj = service_settings_for(context.account)
        serializer = SupportServiceSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        settings_obj = serializer.save(updated_by=request.user)
        recalculate_account_targets(context.account)
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="service.settings_changed",
            target_type="support_service_settings",
            target_id=settings_obj.id,
            summary=f"{request.user.username} updated Support service operations.",
            metadata={
                "timezone": settings_obj.timezone,
                "business_hours_enabled": settings_obj.business_hours_enabled,
            },
            ip_address=request_ip(request),
        )
        return Response(SupportServiceSettingsSerializer(settings_obj).data)


class SupportServiceAlertListView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        alert_status = (request.query_params.get("status") or SupportServiceAlert.Status.UNREAD).strip().lower()
        queryset = (
            SupportServiceAlert.objects.filter(
                support_account=context.account,
                recipient=request.user,
            )
            .select_related("website", "support_conversation")
            .order_by("-triggered_at")
        )
        if alert_status in SupportServiceAlert.Status.values:
            queryset = queryset.filter(status=alert_status)
        try:
            limit = min(100, max(1, int(request.query_params.get("limit", 50))))
        except (TypeError, ValueError):
            limit = 50
        alerts = list(queryset[:limit])
        return Response({
            "results": SupportServiceAlertSerializer(alerts, many=True).data,
            "unread_count": SupportServiceAlert.objects.filter(
                support_account=context.account,
                recipient=request.user,
                status=SupportServiceAlert.Status.UNREAD,
            ).count(),
        })


class SupportServiceAlertReadView(APIView):
    def post(self, request, alert_id):
        context, error = require_support_access(request)
        if error:
            return error
        alert = get_object_or_404(
            SupportServiceAlert.objects.select_related("website", "support_conversation"),
            pk=alert_id,
            support_account=context.account,
            recipient=request.user,
        )
        if alert.status == SupportServiceAlert.Status.UNREAD:
            alert.status = SupportServiceAlert.Status.READ
            alert.read_at = timezone.now()
            alert.save(update_fields=["status", "read_at", "updated_at"])
        return Response(SupportServiceAlertSerializer(alert).data)


class SupportServiceAlertReadAllView(APIView):
    def post(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        now = timezone.now()
        updated = SupportServiceAlert.objects.filter(
            support_account=context.account,
            recipient=request.user,
            status=SupportServiceAlert.Status.UNREAD,
        ).update(status=SupportServiceAlert.Status.READ, read_at=now, updated_at=now)
        return Response({"updated": updated})


class SupportBootstrapView(APIView):
    def get(self, request):
        enabled = support_chat_enabled()
        context = attach_support_context(request)
        account = context.account

        if not enabled:
            return Response({
                "feature_enabled": False,
                "access": "disabled",
                "role": None,
                "account": None,
                "limits": None,
                "websites": [],
                "agents": [],
                "invitations": [],
            })

        if account is None:
            return Response({
                "feature_enabled": True,
                "access": "upgrade_required",
                "role": None,
                "account": None,
                "limits": None,
                "websites": [],
                "agents": [],
                "invitations": [],
            })

        expire_stale_invitations(account)
        websites = visible_websites(context).order_by("name")
        agents = (
            SupportAgent.objects.filter(support_account=account, is_active=True)
            .select_related("user", "user__profile")
            .prefetch_related("website_assignments")
            .order_by("user__username")
        )
        invitations = SupportAgentInvitation.objects.none()
        if context.role == "owner":
            invitations = (
                SupportAgentInvitation.objects.filter(
                    support_account=account,
                    status__in=[
                        SupportAgentInvitation.Status.PENDING,
                        SupportAgentInvitation.Status.EXPIRED,
                    ],
                )
                .select_related("invited_by", "invited_by__profile")
                .prefetch_related("website_assignments__website")
                .order_by("-created_at")[:50]
            )
        else:
            agents = agents.filter(pk=context.agent.id) if getattr(context.agent, "id", None) else agents.none()

        website_count = SupportWebsite.objects.filter(support_account=account, is_active=True).count()
        active_agents = active_agent_count(account)
        pending_agents = pending_invitation_count(account)
        access = "active" if account.has_product_access else "restricted"
        return Response({
            "feature_enabled": True,
            "access": access,
            "role": context.role,
            "account": SupportAccountSerializer(account).data,
            "limits": {
                "websites": {"used": website_count, "limit": account.website_limit},
                "agents": {
                    "used": active_agents + pending_agents,
                    "active": active_agents,
                    "pending": pending_agents,
                    "limit": account.agent_limit,
                },
            },
            "websites": SupportWebsiteSerializer(websites, many=True).data,
            "agents": SupportAgentSerializer(agents, many=True).data,
            "invitations": SupportAgentInvitationSerializer(invitations, many=True).data,
        })


class SupportPlanActivateView(APIView):
    """Temporary self-service trial activation until paid checkout is connected."""

    @transaction.atomic
    def post(self, request):
        if not support_chat_enabled():
            return Response({"detail": "Support Chat is not enabled."}, status=status.HTTP_403_FORBIDDEN)

        plan_key = str(request.data.get("plan_code") or "").strip().lower()
        plan = SUPPORT_TRIAL_PLANS.get(plan_key)
        if not plan:
            return Response(
                {"detail": "Choose a valid Support Chat plan.", "code": "invalid_plan"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if SupportAgent.objects.filter(user=request.user, is_active=True).exists():
            return Response(
                {"detail": "Support agents cannot create a separate Support Chat account.", "code": "agent_account"},
                status=status.HTTP_409_CONFLICT,
            )

        account = SupportAccount.objects.select_for_update().filter(owner=request.user).first()
        if account and account.has_product_access:
            return Response(
                {"detail": "Your Support Chat plan is already active.", "code": "already_active"},
                status=status.HTTP_409_CONFLICT,
            )

        trial_ends_at = timezone.now() + timedelta(days=14)
        created = account is None
        if created:
            account = SupportAccount(owner=request.user)

        metadata = dict(account.metadata or {})
        metadata.update({
            "activation_source": "self_service_trial",
            "trial_started_at": timezone.now().isoformat(),
        })
        account.status = SupportAccount.Status.TRIALING
        account.plan_code = plan["plan_code"]
        account.website_limit = plan["website_limit"]
        account.agent_limit = plan["agent_limit"]
        account.current_period_end = trial_ends_at
        account.grace_ends_at = None
        account.metadata = metadata
        account.save()

        return Response(
            {
                "account": SupportAccountSerializer(account).data,
                "trial_ends_at": trial_ends_at,
                "message": "Your 14-day Support Chat trial is active.",
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SupportWebsiteListCreateView(APIView):
    def get(self, request):
        context = attach_support_context(request)
        if not support_chat_enabled() or not context.account or not context.account.has_product_access:
            return Response({"detail": "Support Chat access is not active."}, status=status.HTTP_403_FORBIDDEN)
        websites = visible_websites(context).order_by("name")
        return Response(SupportWebsiteSerializer(websites, many=True).data)

    def post(self, request):
        context, error = require_owner(request, self)
        if error:
            return error

        serializer = SupportWebsiteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            account = type(context.account).objects.select_for_update().get(pk=context.account.pk)
            current_count = SupportWebsite.objects.filter(support_account=account, is_active=True).count()
            if current_count >= account.website_limit:
                return Response(
                    {"detail": "Your current Support Chat plan has reached its website limit."},
                    status=status.HTTP_409_CONFLICT,
                )
            website = serializer.save(support_account=account, created_by=request.user)
        return Response(SupportWebsiteSerializer(website).data, status=status.HTTP_201_CREATED)


class SupportWebsiteDetailView(APIView):
    def patch(self, request, website_id):
        context, error = require_owner(request, self)
        if error:
            return error
        website = SupportWebsite.objects.filter(pk=website_id, support_account=context.account, is_active=True).first()
        if not website:
            return Response({"detail": "Website not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = SupportWebsiteSerializer(website, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, website_id):
        context, error = require_owner(request, self, require_access=False)
        if error:
            return error
        website = SupportWebsite.objects.filter(pk=website_id, support_account=context.account, is_active=True).first()
        if not website:
            return Response({"detail": "Website not found."}, status=status.HTTP_404_NOT_FOUND)
        website.is_active = False
        website.widget_enabled = False
        website.save(update_fields=["is_active", "widget_enabled", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportAgentInvitationListCreateView(APIView):
    def get(self, request):
        context, error = require_owner(request, self, require_access=False)
        if error:
            return error
        expire_stale_invitations(context.account)
        invitations = (
            SupportAgentInvitation.objects.filter(support_account=context.account)
            .select_related("invited_by", "invited_by__profile")
            .prefetch_related("website_assignments__website")
            .order_by("-created_at")[:100]
        )
        return Response(SupportAgentInvitationSerializer(invitations, many=True).data)

    def post(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        serializer = SupportAgentInvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            invitation = create_agent_invitation(
                actor=request.user,
                account=context.account,
                **serializer.validated_data,
            )
        except SupportServiceError as service_error:
            return service_error_response(service_error)
        return Response(SupportAgentInvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


class SupportAgentInvitationDetailView(APIView):
    def delete(self, request, invitation_id):
        context, error = require_owner(request, self, require_access=False)
        if error:
            return error
        invitation = get_object_or_404(
            SupportAgentInvitation,
            pk=invitation_id,
            support_account=context.account,
        )
        try:
            revoke_agent_invitation(account=context.account, invitation=invitation)
        except SupportServiceError as service_error:
            return service_error_response(service_error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportAgentInvitationResendView(APIView):
    def post(self, request, invitation_id):
        context, error = require_owner(request, self)
        if error:
            return error
        invitation = get_object_or_404(
            SupportAgentInvitation,
            pk=invitation_id,
            support_account=context.account,
        )
        try:
            invitation = resend_agent_invitation(
                actor=request.user,
                account=context.account,
                invitation=invitation,
            )
        except SupportServiceError as service_error:
            return service_error_response(service_error)
        return Response(SupportAgentInvitationSerializer(invitation).data)


class SupportAgentInvitationPreviewView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        raw_token = (request.query_params.get("token") or "").strip()
        invitation = invitation_from_token(raw_token)
        if not invitation:
            return Response({"detail": "This invitation is invalid or no longer available."}, status=status.HTTP_404_NOT_FOUND)
        return Response(invitation_preview_payload(invitation))


class SupportAgentInvitationAcceptView(APIView):
    def post(self, request):
        if not support_chat_enabled():
            return Response({"detail": "Support Chat is not enabled."}, status=status.HTTP_403_FORBIDDEN)
        serializer = SupportInvitationTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            agent = accept_agent_invitation(user=request.user, raw_token=serializer.validated_data["token"])
        except SupportServiceError as service_error:
            return service_error_response(service_error)
        agent = (
            SupportAgent.objects.select_related("user", "user__profile")
            .prefetch_related("website_assignments")
            .get(pk=agent.pk)
        )
        return Response(SupportAgentSerializer(agent).data)


class SupportAgentDetailView(APIView):
    def patch(self, request, agent_id):
        context, error = require_owner(request, self)
        if error:
            return error
        agent = get_object_or_404(
            SupportAgent,
            pk=agent_id,
            support_account=context.account,
            is_active=True,
        )
        serializer = SupportAgentUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            agent = update_agent(account=context.account, agent=agent, **serializer.validated_data)
        except SupportServiceError as service_error:
            return service_error_response(service_error)
        agent = (
            SupportAgent.objects.select_related("user", "user__profile")
            .prefetch_related("website_assignments")
            .get(pk=agent.pk)
        )
        return Response(SupportAgentSerializer(agent).data)

    def delete(self, request, agent_id):
        context, error = require_owner(request, self, require_access=False)
        if error:
            return error
        agent = get_object_or_404(SupportAgent, pk=agent_id, support_account=context.account)
        deactivate_agent(account=context.account, agent=agent)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportAgentAvailabilityView(APIView):
    def patch(self, request):
        if not support_chat_enabled():
            return Response({"detail": "Support Chat is not enabled."}, status=status.HTTP_403_FORBIDDEN)
        serializer = SupportAgentAvailabilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            agent = update_agent_availability(
                user=request.user,
                availability=serializer.validated_data["availability"],
            )
        except SupportServiceError as service_error:
            return service_error_response(service_error)
        agent = (
            SupportAgent.objects.select_related("user", "user__profile")
            .prefetch_related("website_assignments")
            .get(pk=agent.pk)
        )
        return Response(SupportAgentSerializer(agent).data)


def widget_error_response(error: WidgetAccessError):
    return Response({"detail": error.detail, "code": error.code}, status=error.status_code)


def public_widget_response(payload, *, status_code=status.HTTP_200_OK):
    response = Response(payload, status=status_code)
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    response["Vary"] = "Origin"
    return response


class SupportWebsiteWidgetSettingsView(APIView):
    def get(self, request, website_id):
        context, error = require_owner(request, self, require_access=False)
        if error:
            return error
        website = get_object_or_404(
            SupportWebsite,
            pk=website_id,
            support_account=context.account,
            is_active=True,
        )
        return Response(SupportWidgetSettingsSerializer(widget_settings_for(website)).data)

    def patch(self, request, website_id):
        context, error = require_owner(request, self)
        if error:
            return error
        website = get_object_or_404(
            SupportWebsite,
            pk=website_id,
            support_account=context.account,
            is_active=True,
        )
        widget_settings = widget_settings_for(website)
        serializer = SupportWidgetSettingsSerializer(widget_settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class SupportWebsiteSiteKeyRegenerateView(APIView):
    def post(self, request, website_id):
        context, error = require_owner(request, self)
        if error:
            return error
        website = get_object_or_404(
            SupportWebsite,
            pk=website_id,
            support_account=context.account,
            is_active=True,
        )
        regenerate_website_site_key(website)
        return Response(SupportWebsiteSerializer(website).data)


class SupportWidgetConfigView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_config"

    def get(self, request, site_key):
        try:
            website = website_for_public_widget(site_key)
            assert_widget_request_allowed(request, website)
            widget_settings = widget_settings_for(website)
        except WidgetAccessError as error:
            return widget_error_response(error)

        knowledge_settings = knowledge_settings_for(website.support_account)
        privacy_settings = privacy_settings_for(website.support_account)
        call_settings = call_settings_for(website.support_account)
        website_calls_enabled = bool(
            widget_settings.allow_audio_calls
            or (call_settings.allow_video and widget_settings.allow_video_calls)
        )
        payload = PublicWidgetConfigSerializer({
            "site_key": website.site_key,
            "website_name": website.name,
            "brand_name": widget_settings.brand_name,
            "primary_color": widget_settings.primary_color,
            "welcome_text": widget_settings.welcome_text,
            "offline_text": widget_settings.offline_text,
            "launcher_text": widget_settings.launcher_text,
            "privacy_note": widget_settings.privacy_note,
            "position": widget_settings.position,
            "theme": widget_settings.theme,
            "require_name": widget_settings.require_name,
            "require_email": widget_settings.require_email,
            "allow_attachments": widget_settings.allow_attachments,
            "allow_audio_calls": widget_settings.allow_audio_calls,
            "allow_video_calls": widget_settings.allow_video_calls,
            "calls_enabled": bool(support_calls_enabled() and call_settings.enabled and website_calls_enabled),
            "session_enabled": True,
            "messaging_enabled": True,
            "knowledge_enabled": bool(knowledge_settings.enabled and knowledge_settings.show_in_widget),
            "knowledge_suggestions_enabled": bool(knowledge_settings.suggestions_enabled),
            "visitor_deletion_enabled": bool(privacy_settings.allow_visitor_deletion_requests),
        }).data
        return public_widget_response(payload)


class SupportWidgetSessionCreateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_session"

    def post(self, request, site_key):
        serializer = SupportWidgetSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            website = website_for_public_widget(site_key)
            origin = assert_widget_request_allowed(request, website)
            issued = create_widget_session(
                website=website,
                origin=origin,
                user_agent=request.headers.get("User-Agent", ""),
                **serializer.validated_data,
            )
        except WidgetAccessError as error:
            return widget_error_response(error)
        payload = SupportWidgetSessionSerializer(
            issued.session,
            context={"raw_token": issued.raw_token},
        ).data
        return public_widget_response(payload, status_code=status.HTTP_201_CREATED)


class SupportWidgetSessionDetailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_resume"

    def _session(self, request, site_key, session_id):
        website = website_for_public_widget(site_key)
        origin = assert_widget_request_allowed(request, website)
        return authenticate_widget_session(
            website=website,
            session_id=session_id,
            raw_token=token_from_request(request),
            origin=origin,
        )

    def get(self, request, site_key, session_id):
        try:
            session = self._session(request, site_key, session_id)
            session = update_widget_session(session=session)
        except WidgetAccessError as error:
            return widget_error_response(error)
        return public_widget_response(SupportWidgetSessionSerializer(session).data)

    def patch(self, request, site_key, session_id):
        serializer = SupportWidgetSessionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session = self._session(request, site_key, session_id)
            session = update_widget_session(session=session, **serializer.validated_data)
        except WidgetAccessError as error:
            return widget_error_response(error)
        return public_widget_response(SupportWidgetSessionSerializer(session).data)

    def delete(self, request, site_key, session_id):
        try:
            session = self._session(request, site_key, session_id)
            close_widget_session(session)
        except WidgetAccessError as error:
            return widget_error_response(error)
        return public_widget_response({}, status_code=status.HTTP_204_NO_CONTENT)


class SupportWidgetSessionRefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_session"

    def post(self, request, site_key, session_id):
        try:
            website = website_for_public_widget(site_key)
            origin = assert_widget_request_allowed(request, website)
            session = authenticate_widget_session(
                website=website,
                session_id=session_id,
                raw_token=token_from_request(request),
                origin=origin,
            )
            issued = refresh_widget_session(session)
        except WidgetAccessError as error:
            return widget_error_response(error)
        payload = SupportWidgetSessionSerializer(
            issued.session,
            context={"raw_token": issued.raw_token},
        ).data
        return public_widget_response(payload)


class SupportWebsiteWidgetConfigurationView(APIView):
    def patch(self, request, website_id):
        context, error = require_owner(request, self)
        if error:
            return error
        serializer = SupportWebsiteWidgetConfigurationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            website = get_object_or_404(
                SupportWebsite.objects.select_for_update(),
                pk=website_id,
                support_account=context.account,
                is_active=True,
            )
            website.allowed_origins = serializer.validated_data["allowed_origins"]
            website.widget_enabled = serializer.validated_data["widget_enabled"]
            website.save(update_fields=["allowed_origins", "widget_enabled", "updated_at"])
            widget_settings = widget_settings_for(website)
            settings_serializer = SupportWidgetSettingsSerializer(
                widget_settings,
                data=serializer.validated_data["settings"],
                partial=True,
            )
            settings_serializer.is_valid(raise_exception=True)
            settings_serializer.save()
        return Response(SupportWebsiteSerializer(website).data)


def support_conversation_error_response(error: SupportConversationError):
    return Response({"detail": error.detail, "code": error.code}, status=error.status_code)


def require_support_access(request):
    context = attach_support_context(request)
    if not support_chat_enabled() or not context.account or not context.account.has_product_access:
        return context, Response(
            {"detail": "Support Chat access is not active."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return context, None


class SupportConversationListView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error

        queryset = support_conversations_for_context(context)
        website_id = request.query_params.get("website")
        status_filter = request.query_params.get("status")
        priority_filter = request.query_params.get("priority")
        tag_id = request.query_params.get("tag")
        queue = (request.query_params.get("queue") or "open").strip().lower()
        search = (request.query_params.get("search") or "").strip()

        if website_id:
            queryset = queryset.filter(website_id=website_id)
        if priority_filter:
            queryset = queryset.filter(priority=priority_filter)
        if tag_id:
            queryset = queryset.filter(tag_assignments__tag_id=tag_id, tag_assignments__tag__is_active=True).distinct()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        elif queue == "open":
            queryset = queryset.exclude(status__in=[SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED])
        elif queue == "mine" and context.agent:
            queryset = queryset.filter(assigned_agent=context.agent).exclude(status=SupportConversation.Status.CLOSED)
        elif queue == "unassigned":
            queryset = queryset.filter(assigned_agent__isnull=True).exclude(status=SupportConversation.Status.CLOSED)
        elif queue == "overdue":
            queryset = queryset.filter(overdue_conversation_q()).exclude(
                status__in=[SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED]
            )
        elif queue == "follow_up":
            queryset = queryset.filter(follow_up_due_q()).exclude(
                status__in=[SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED]
            )
        elif queue == "resolved":
            queryset = queryset.filter(status=SupportConversation.Status.RESOLVED)
        elif queue == "closed":
            queryset = queryset.filter(status=SupportConversation.Status.CLOSED)

        if search:
            queryset = queryset.filter(
                Q(visitor__name__icontains=search)
                | Q(visitor__email__icontains=search)
                | Q(subject__icontains=search)
                | Q(conversation__messages__text__icontains=search)
            ).distinct()

        try:
            limit = min(100, max(1, int(request.query_params.get("limit", 50))))
            offset = max(0, int(request.query_params.get("offset", 0)))
        except (TypeError, ValueError):
            limit, offset = 50, 0

        queryset = queryset.order_by("-conversation__last_message_at", "-created_at")
        total = queryset.count()
        conversations = list(queryset[offset:offset + limit])
        serializer = SupportConversationSerializer(
            conversations,
            many=True,
            context={"user": request.user, "request": request},
        )
        unread_total = sum(team_unread_count(conversation, request.user) for conversation in conversations)
        website_unread = {}
        for conversation in conversations:
            unread = team_unread_count(conversation, request.user)
            if unread:
                key = str(conversation.website_id)
                website_unread[key] = website_unread.get(key, 0) + unread
        return Response({
            "results": serializer.data,
            "count": total,
            "next_offset": offset + limit if offset + limit < total else None,
            "unread_total": unread_total,
            "website_unread": website_unread,
        })


class SupportUnreadSummaryView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        conversations = list(support_conversations_for_context(context))
        website_unread: dict[str, int] = {}
        total = 0
        for conversation in conversations:
            count = team_unread_count(conversation, request.user)
            if not count:
                continue
            total += count
            website_id = str(conversation.website_id)
            website_unread[website_id] = website_unread.get(website_id, 0) + count
        alert_unread = SupportServiceAlert.objects.filter(
            support_account=context.account,
            recipient=request.user,
            status=SupportServiceAlert.Status.UNREAD,
        ).count()
        return Response({
            "unread_total": total,
            "website_unread": website_unread,
            "alert_unread": alert_unread,
        })


class SupportConversationDetailView(APIView):
    def get(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = get_context_conversation(context, conversation_id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        return Response(SupportConversationSerializer(conversation, context={"user": request.user, "request": request}).data)

    def patch(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        serializer = SupportConversationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            conversation = get_context_conversation(context, conversation_id)
            conversation = update_conversation_workflow(
                context=context,
                support_conversation=conversation,
                **serializer.validated_data,
            )
            conversation = get_context_conversation(context, conversation.id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        return Response(SupportConversationSerializer(conversation, context={"user": request.user, "request": request}).data)


def support_media_error_response(error: SupportMediaError):
    return Response({"detail": error.detail, "code": error.code}, status=error.status_code)


def _create_support_pending_upload(*, serializer, support_conversation, owner: SupportUploadOwner):
    file_obj = serializer.validated_data["file"]
    initial_bytes = _read_upload_initial_bytes(file_obj)
    original_name, normalized_mime, extension = _normalize_upload_media(
        file_obj,
        supplied_original_name=serializer.validated_data.get("original_name", ""),
        supplied_mime_type=serializer.validated_data.get("mime_type", ""),
    )
    metadata = support_upload_metadata(
        account_id=support_conversation.website.support_account_id,
        website_id=support_conversation.website_id,
        conversation_id=support_conversation.id,
        source=owner.source,
        extra=serializer.validated_data.get("metadata") or {},
    )
    pending = PendingUpload.objects.create(
        user=owner.actor if owner.source == SupportPendingUpload.Source.TEAM else None,
        purpose=PendingUpload.Purpose.SUPPORT,
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
        metadata=metadata,
    )
    register_support_pending_upload(
        pending_upload=pending,
        support_conversation=support_conversation,
        owner=owner,
    )
    if getattr(settings, "UPLOAD_SCAN_ASYNC", True):
        dispatch_pending_upload_scan(pending)
    else:
        scan_upload_file(pending, initial_bytes=initial_bytes)
    pending.refresh_from_db()
    return pending


class SupportConversationUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_upload_create"

    def post(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = get_context_conversation(context, conversation_id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        serializer = UploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            pending = _create_support_pending_upload(
                serializer=serializer,
                support_conversation=conversation,
                owner=SupportUploadOwner(source=SupportPendingUpload.Source.TEAM, actor=request.user),
            )
        except SupportMediaError as media_error:
            return support_media_error_response(media_error)
        return Response(
            SupportPendingUploadSerializer(pending).data,
            status=status.HTTP_201_CREATED,
        )


class SupportWidgetConversationUploadView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_widget_upload"

    def post(self, request, site_key, session_id):
        try:
            website = website_for_public_widget(site_key)
            origin = assert_widget_request_allowed(request, website)
            session = authenticate_widget_session(
                website=website,
                session_id=session_id,
                raw_token=token_from_request(request),
                origin=origin,
            )
            widget_settings = widget_settings_for(website)
            if not widget_settings.allow_attachments:
                raise WidgetAccessError("Attachments are disabled for this website.", code="attachments_disabled", status_code=403)
            conversation, _ = get_or_create_visitor_conversation(session)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        serializer = UploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            pending = _create_support_pending_upload(
                serializer=serializer,
                support_conversation=conversation,
                owner=SupportUploadOwner(source=SupportPendingUpload.Source.VISITOR, session=session),
            )
        except SupportMediaError as media_error:
            return public_widget_response(
                {"detail": media_error.detail, "code": media_error.code},
                status_code=media_error.status_code,
            )
        return public_widget_response(
            SupportPendingUploadSerializer(pending).data,
            status_code=status.HTTP_201_CREATED,
        )


def _support_attachment_queryset():
    return MessageAttachment.objects.filter(
        scan_status=MessageAttachment.ScanStatus.CLEAN,
        message__is_deleted=False,
        message__conversation__support_conversation__isnull=False,
    ).select_related(
        "message",
        "message__conversation",
        "message__conversation__support_conversation",
        "message__conversation__support_conversation__website",
        "message__conversation__support_conversation__visitor",
    )


class SupportAttachmentAccessView(APIView):
    disposition = "attachment"

    def get(self, request, attachment_id):
        context, error = require_support_access(request)
        if error:
            return error
        attachment = get_object_or_404(_support_attachment_queryset(), pk=attachment_id)
        support_conversation = attachment.message.conversation.support_conversation
        try:
            get_context_conversation(context, support_conversation.id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        return self.build_response(request, attachment)

    def build_response(self, request, attachment):
        if self.disposition == "thumbnail":
            if not attachment.thumbnail:
                raise Http404("Thumbnail is not available for this attachment.")
            return _build_thumbnail_file_response(
                attachment.thumbnail,
                filename=Path(attachment.thumbnail.name).name,
                request=request,
            )
        if self.disposition == "inline" and not _can_preview_inline_mime(attachment.mime_type):
            return Response({"detail": "This attachment cannot be previewed inline."}, status=status.HTTP_403_FORBIDDEN)
        return _build_media_file_response(
            attachment.file,
            filename=attachment.original_name,
            mime_type=attachment.mime_type,
            as_attachment=self.disposition == "attachment",
            request=request,
        )


class SupportAttachmentPreviewView(SupportAttachmentAccessView):
    disposition = "inline"


class SupportAttachmentThumbnailView(SupportAttachmentAccessView):
    disposition = "thumbnail"


class SupportWidgetAttachmentAccessView(SupportAttachmentAccessView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, site_key, session_id, attachment_id):
        try:
            website = website_for_public_widget(site_key)
            origin = assert_widget_request_allowed(request, website)
            session = authenticate_widget_session(
                website=website,
                session_id=session_id,
                raw_token=token_from_request(request),
                origin=origin,
            )
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        attachment = get_object_or_404(
            _support_attachment_queryset(),
            pk=attachment_id,
            message__conversation__support_conversation__website=website,
            message__conversation__support_conversation__visitor=session.visitor,
        )
        response = self.build_response(request, attachment)
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
        response["Vary"] = "Origin"
        return response


class SupportWidgetAttachmentPreviewView(SupportWidgetAttachmentAccessView):
    disposition = "inline"


class SupportWidgetAttachmentThumbnailView(SupportWidgetAttachmentAccessView):
    disposition = "thumbnail"


class SupportConversationMessagesView(APIView):
    def get(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = get_context_conversation(context, conversation_id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        messages = list(support_messages_qs(conversation))
        mark_team_read(support_conversation=conversation, user=request.user)
        return Response({
            "conversation": SupportConversationSerializer(conversation, context={"user": request.user, "request": request}).data,
            "messages": SupportMessageSerializer(
                messages,
                many=True,
                context={"user": request.user, "support_conversation": conversation, "request": request},
            ).data,
        })

    def post(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        serializer = SupportMessageSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            conversation = get_context_conversation(context, conversation_id)
            message = send_team_message(
                context=context,
                actor=request.user,
                support_conversation=conversation,
                text=serializer.validated_data.get("text", ""),
                attachment_ids=serializer.validated_data.get("attachment_ids", []),
                voice_note=serializer.validated_data.get("voice_note", False),
            )
            conversation = get_context_conversation(context, conversation.id)
            mark_team_read(support_conversation=conversation, user=request.user)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        message = support_messages_qs(conversation).filter(pk=message.pk).first()
        return Response(
            SupportMessageSerializer(
                message,
                context={"user": request.user, "support_conversation": conversation, "request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class SupportConversationReadView(APIView):
    def post(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = get_context_conversation(context, conversation_id)
            mark_team_read(support_conversation=conversation, user=request.user)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportConversationClaimView(APIView):
    def post(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = get_context_conversation(context, conversation_id)
            conversation = claim_conversation(context=context, support_conversation=conversation)
            conversation = get_context_conversation(context, conversation.id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        return Response(SupportConversationSerializer(conversation, context={"user": request.user, "request": request}).data)


class SupportWidgetConversationMessagesView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_message"

    def _session(self, request, site_key, session_id):
        website = website_for_public_widget(site_key)
        origin = assert_widget_request_allowed(request, website)
        return authenticate_widget_session(
            website=website,
            session_id=session_id,
            raw_token=token_from_request(request),
            origin=origin,
        )

    def get(self, request, site_key, session_id):
        try:
            session = self._session(request, site_key, session_id)
            conversation = SupportConversation.objects.select_related(
                "conversation",
                "conversation__last_message",
                "website",
                "website__support_account",
                "visitor",
                "assigned_agent",
                "assigned_agent__user",
                "assigned_agent__user__profile",
                "visitor_last_read_message",
            ).filter(visitor=session.visitor, website=session.website).first()
            if not conversation:
                return public_widget_response({"conversation": None, "messages": []})
            messages = list(support_messages_qs(conversation))
            mark_visitor_read(support_conversation=conversation)
        except (WidgetAccessError, SupportConversationError) as conversation_error:
            if isinstance(conversation_error, WidgetAccessError):
                return widget_error_response(conversation_error)
            return public_widget_response(
                {"detail": conversation_error.detail, "code": conversation_error.code},
                status_code=conversation_error.status_code,
            )
        return public_widget_response({
            "conversation": SupportConversationSerializer(conversation, context={"visitor": session.visitor, "request": request, "widget_site_key": site_key, "widget_session_id": session.id}).data,
            "messages": SupportMessageSerializer(
                messages,
                many=True,
                context={"visitor": session.visitor, "support_conversation": conversation, "request": request, "widget_site_key": site_key, "widget_session_id": session.id},
            ).data,
        })

    def post(self, request, site_key, session_id):
        serializer = SupportMessageSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session = self._session(request, site_key, session_id)
            conversation, message = send_visitor_message(
                session=session,
                text=serializer.validated_data.get("text", ""),
                attachment_ids=serializer.validated_data.get("attachment_ids", []),
                voice_note=serializer.validated_data.get("voice_note", False),
            )
            conversation = SupportConversation.objects.select_related(
                "conversation",
                "conversation__last_message",
                "website",
                "website__support_account",
                "visitor",
                "assigned_agent",
                "assigned_agent__user",
                "assigned_agent__user__profile",
                "visitor_last_read_message",
            ).get(pk=conversation.pk)
            message = support_messages_qs(conversation).filter(pk=message.pk).first()
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportConversationError as conversation_error:
            return public_widget_response(
                {"detail": conversation_error.detail, "code": conversation_error.code},
                status_code=conversation_error.status_code,
            )
        return public_widget_response(
            {
                "conversation": SupportConversationSerializer(conversation, context={"visitor": session.visitor, "request": request, "widget_site_key": site_key, "widget_session_id": session.id}).data,
                "message": SupportMessageSerializer(
                    message,
                    context={"visitor": session.visitor, "support_conversation": conversation, "request": request, "widget_site_key": site_key, "widget_session_id": session.id},
                ).data,
            },
            status_code=status.HTTP_201_CREATED,
        )


class SupportWidgetConversationReadView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_resume"

    def post(self, request, site_key, session_id):
        try:
            website = website_for_public_widget(site_key)
            origin = assert_widget_request_allowed(request, website)
            session = authenticate_widget_session(
                website=website,
                session_id=session_id,
                raw_token=token_from_request(request),
                origin=origin,
            )
            conversation = SupportConversation.objects.filter(visitor=session.visitor, website=website).first()
            if conversation:
                mark_visitor_read(support_conversation=conversation)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        return public_widget_response({}, status_code=status.HTTP_204_NO_CONTENT)


class SupportTagListCreateView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        return Response(SupportTagSerializer(visible_tags(context), many=True).data)

    def post(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        serializer = SupportTagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                tag = SupportTag(
                    support_account=context.account,
                    created_by=request.user,
                    **serializer.validated_data,
                )
                tag.full_clean()
                tag.save()
                record_audit_event(
                    account=context.account,
                    actor=request.user,
                    action="tag.created",
                    target_type="support_tag",
                    target_id=tag.id,
                    summary=f"Created tag {tag.name}.",
                    metadata={"name": tag.name, "color": tag.color},
                    ip_address=request_ip(request),
                )
        except (IntegrityError, ValidationError):
            return Response({"detail": "A tag with this name already exists."}, status=status.HTTP_409_CONFLICT)
        return Response(SupportTagSerializer(tag).data, status=status.HTTP_201_CREATED)


class SupportTagDetailView(APIView):
    def patch(self, request, tag_id):
        context, error = require_owner(request, self)
        if error:
            return error
        tag = get_object_or_404(SupportTag, pk=tag_id, support_account=context.account, is_active=True)
        serializer = SupportTagSerializer(tag, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                for key, value in serializer.validated_data.items():
                    setattr(tag, key, value)
                tag.full_clean()
                tag.save()
                record_audit_event(
                    account=context.account,
                    actor=request.user,
                    action="tag.updated",
                    target_type="support_tag",
                    target_id=tag.id,
                    summary=f"Updated tag {tag.name}.",
                    metadata={"name": tag.name, "color": tag.color},
                    ip_address=request_ip(request),
                )
        except (IntegrityError, ValidationError):
            return Response({"detail": "A tag with this name already exists."}, status=status.HTTP_409_CONFLICT)
        return Response(SupportTagSerializer(tag).data)

    def delete(self, request, tag_id):
        context, error = require_owner(request, self)
        if error:
            return error
        tag = get_object_or_404(SupportTag, pk=tag_id, support_account=context.account, is_active=True)
        with transaction.atomic():
            tag.is_active = False
            tag.save(update_fields=["is_active", "updated_at"])
            SupportConversationTag.objects.filter(tag=tag).delete()
            record_audit_event(
                account=context.account,
                actor=request.user,
                action="tag.deactivated",
                target_type="support_tag",
                target_id=tag.id,
                summary=f"Removed tag {tag.name}.",
                metadata={"name": tag.name},
                ip_address=request_ip(request),
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportCannedReplyListCreateView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        website_id = request.query_params.get("website") or None
        replies = visible_canned_replies(context, website_id=website_id)
        return Response(SupportCannedReplySerializer(replies, many=True).data)

    def post(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        serializer = SupportCannedReplyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        website_id = values.pop("website_id", None)
        website = None
        if website_id:
            website = SupportWebsite.objects.filter(
                pk=website_id,
                support_account=context.account,
                is_active=True,
            ).first()
            if not website:
                return Response({"detail": "Website not found."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                reply = SupportCannedReply(
                    support_account=context.account,
                    website=website,
                    created_by=request.user,
                    updated_by=request.user,
                    **values,
                )
                reply.full_clean()
                reply.save()
                record_audit_event(
                    account=context.account,
                    website=website,
                    actor=request.user,
                    action="canned_reply.created",
                    target_type="support_canned_reply",
                    target_id=reply.id,
                    summary=f"Created canned reply {reply.shortcut}.",
                    metadata={"shortcut": reply.shortcut, "title": reply.title},
                    ip_address=request_ip(request),
                )
        except (IntegrityError, ValidationError):
            return Response({"detail": "This canned-reply shortcut is already in use."}, status=status.HTTP_409_CONFLICT)
        return Response(SupportCannedReplySerializer(reply).data, status=status.HTTP_201_CREATED)


class SupportCannedReplyDetailView(APIView):
    def patch(self, request, reply_id):
        context, error = require_owner(request, self)
        if error:
            return error
        reply = get_object_or_404(SupportCannedReply, pk=reply_id, support_account=context.account, is_active=True)
        serializer = SupportCannedReplyWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        if "website_id" in values:
            website_id = values.pop("website_id")
            if website_id is None:
                reply.website = None
            else:
                website = SupportWebsite.objects.filter(
                    pk=website_id,
                    support_account=context.account,
                    is_active=True,
                ).first()
                if not website:
                    return Response({"detail": "Website not found."}, status=status.HTTP_400_BAD_REQUEST)
                reply.website = website
        for key, value in values.items():
            setattr(reply, key, value)
        reply.updated_by = request.user
        try:
            with transaction.atomic():
                reply.full_clean()
                reply.save()
                record_audit_event(
                    account=context.account,
                    website=reply.website,
                    actor=request.user,
                    action="canned_reply.updated",
                    target_type="support_canned_reply",
                    target_id=reply.id,
                    summary=f"Updated canned reply {reply.shortcut}.",
                    metadata={"shortcut": reply.shortcut, "title": reply.title},
                    ip_address=request_ip(request),
                )
        except (IntegrityError, ValidationError):
            return Response({"detail": "This canned-reply shortcut is already in use."}, status=status.HTTP_409_CONFLICT)
        return Response(SupportCannedReplySerializer(reply).data)

    def delete(self, request, reply_id):
        context, error = require_owner(request, self)
        if error:
            return error
        reply = get_object_or_404(SupportCannedReply, pk=reply_id, support_account=context.account, is_active=True)
        with transaction.atomic():
            reply.is_active = False
            reply.updated_by = request.user
            reply.save(update_fields=["is_active", "updated_by", "updated_at"])
            record_audit_event(
                account=context.account,
                website=reply.website,
                actor=request.user,
                action="canned_reply.deactivated",
                target_type="support_canned_reply",
                target_id=reply.id,
                summary=f"Removed canned reply {reply.shortcut}.",
                metadata={"shortcut": reply.shortcut, "title": reply.title},
                ip_address=request_ip(request),
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportSavedInboxViewListCreateView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        views = SupportSavedInboxView.objects.filter(
            support_account=context.account,
            user=request.user,
        ).select_related("website", "tag")
        return Response(SupportSavedInboxViewSerializer(views, many=True).data)

    def post(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        serializer = SupportSavedInboxViewWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        website_id = values.pop("website_id", None)
        tag_id = values.pop("tag_id", None)
        website = visible_websites(context).filter(pk=website_id).first() if website_id else None
        if website_id and not website:
            return Response({"detail": "Website not found."}, status=status.HTTP_400_BAD_REQUEST)
        tag = visible_tags(context).filter(pk=tag_id).first() if tag_id else None
        if tag_id and not tag:
            return Response({"detail": "Tag not found."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                view = SupportSavedInboxView(
                    support_account=context.account,
                    user=request.user,
                    website=website,
                    tag=tag,
                    **values,
                )
                view.full_clean()
                view.save()
                save_default_view(view)
        except (IntegrityError, ValidationError):
            return Response({"detail": "You already have a saved view with this name."}, status=status.HTTP_409_CONFLICT)
        return Response(SupportSavedInboxViewSerializer(view).data, status=status.HTTP_201_CREATED)


class SupportSavedInboxViewDetailView(APIView):
    def patch(self, request, view_id):
        context, error = require_support_access(request)
        if error:
            return error
        view = get_object_or_404(
            SupportSavedInboxView,
            pk=view_id,
            support_account=context.account,
            user=request.user,
        )
        serializer = SupportSavedInboxViewWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        if "website_id" in values:
            website_id = values.pop("website_id")
            website = visible_websites(context).filter(pk=website_id).first() if website_id else None
            if website_id and not website:
                return Response({"detail": "Website not found."}, status=status.HTTP_400_BAD_REQUEST)
            view.website = website
        if "tag_id" in values:
            tag_id = values.pop("tag_id")
            tag = visible_tags(context).filter(pk=tag_id).first() if tag_id else None
            if tag_id and not tag:
                return Response({"detail": "Tag not found."}, status=status.HTTP_400_BAD_REQUEST)
            view.tag = tag
        for key, value in values.items():
            setattr(view, key, value)
        try:
            with transaction.atomic():
                view.full_clean()
                view.save()
                save_default_view(view)
        except (IntegrityError, ValidationError):
            return Response({"detail": "You already have a saved view with this name."}, status=status.HTTP_409_CONFLICT)
        return Response(SupportSavedInboxViewSerializer(view).data)

    def delete(self, request, view_id):
        context, error = require_support_access(request)
        if error:
            return error
        view = get_object_or_404(
            SupportSavedInboxView,
            pk=view_id,
            support_account=context.account,
            user=request.user,
        )
        view.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportConversationInternalNotesView(APIView):
    def get(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = get_context_conversation(context, conversation_id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        notes = conversation.internal_notes.select_related("author", "author__profile").order_by("created_at")
        return Response(SupportInternalNoteSerializer(notes, many=True).data)

    def post(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        serializer = SupportInternalNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            conversation = get_context_conversation(context, conversation_id)
            if context.role != "owner" and not can_reply(context, conversation):
                return Response({"detail": "This conversation is assigned to another agent."}, status=status.HTTP_403_FORBIDDEN)
            note = create_internal_note(
                context=context,
                conversation=conversation,
                actor=request.user,
                body=serializer.validated_data["body"],
                ip_address=request_ip(request),
            )
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        return Response(SupportInternalNoteSerializer(note).data, status=status.HTTP_201_CREATED)


class SupportConversationTagsView(APIView):
    def put(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        serializer = SupportConversationTagsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            conversation = get_context_conversation(context, conversation_id)
            if context.role != "owner" and not can_reply(context, conversation):
                return Response({"detail": "This conversation is assigned to another agent."}, status=status.HTTP_403_FORBIDDEN)
            tags = replace_conversation_tags(
                context=context,
                conversation=conversation,
                actor=request.user,
                tag_ids=serializer.validated_data["tag_ids"],
                ip_address=request_ip(request),
            )
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        except ValueError as value_error:
            return Response({"detail": str(value_error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SupportTagSerializer(tags, many=True).data)


class SupportConversationActivityView(APIView):
    def get(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = get_context_conversation(context, conversation_id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        events = conversation.audit_events.select_related("actor", "actor__profile").order_by("-created_at")[:100]
        notes = conversation.internal_notes.select_related("author", "author__profile").order_by("-created_at")[:100]
        return Response({
            "events": SupportAuditEventSerializer(events, many=True).data,
            "notes": SupportInternalNoteSerializer(notes, many=True).data,
        })


class SupportFeedbackSettingsView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        settings_obj = feedback_settings_for(context.account)
        return Response(SupportFeedbackSettingsSerializer(settings_obj).data)

    def patch(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        settings_obj = feedback_settings_for(context.account)
        serializer = SupportFeedbackSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        settings_obj = serializer.save(updated_by=request.user)
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="feedback.settings_changed",
            target_type="support_feedback_settings",
            target_id=settings_obj.id,
            summary=f"{request.user.username} updated customer feedback settings.",
            metadata={
                "csat_enabled": settings_obj.csat_enabled,
                "auto_request_on_resolve": settings_obj.auto_request_on_resolve,
                "allow_comment": settings_obj.allow_comment,
                "survey_expiry_days": settings_obj.survey_expiry_days,
            },
            ip_address=request_ip(request),
        )
        return Response(SupportFeedbackSettingsSerializer(settings_obj).data)


class SupportAnalyticsOverviewView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        if context.role != "owner" and not bool(context.agent and context.agent.can_view_analytics):
            return Response(
                {"detail": "Analytics access has not been granted for this Support agent.", "code": "analytics_denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            start_day, end_day, start_at, end_at = parse_analytics_period(
                start_value=request.query_params.get("start"),
                end_value=request.query_params.get("end"),
                days_value=request.query_params.get("days"),
            )
            payload = analytics_overview(
                context=context,
                start_at=start_at,
                end_at=end_at,
                start_day=start_day,
                end_day=end_day,
                website_id=request.query_params.get("website") or None,
            )
        except SupportAnalyticsError as analytics_error:
            return Response(
                {"detail": analytics_error.detail, "code": analytics_error.code},
                status=analytics_error.status_code,
            )
        return Response(payload)


class SupportConversationCSATView(APIView):
    def _conversation(self, context, conversation_id):
        return get_context_conversation(context, conversation_id)

    def get(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = self._conversation(context, conversation_id)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        survey = survey_for_conversation(conversation)
        return Response({
            "settings": SupportFeedbackSettingsSerializer(feedback_settings_for(context.account)).data,
            "survey": SupportCSATSurveySerializer(survey).data if survey else None,
        })

    def post(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = self._conversation(context, conversation_id)
            if context.role != "owner" and not can_reply(context, conversation):
                raise SupportConversationError(
                    "You cannot request feedback for this conversation.",
                    code="csat_denied",
                    status_code=403,
                )
            if conversation.status not in {SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED}:
                raise SupportConversationError(
                    "Resolve the conversation before requesting customer feedback.",
                    code="conversation_not_resolved",
                    status_code=409,
                )
            actor = context.account.owner if context.role == "owner" else context.agent.user
            survey = request_csat(conversation, actor=actor, source=SupportCSATSurvey.Source.MANUAL)
            if survey is None:
                return Response(
                    {"detail": "Customer feedback is disabled.", "code": "feedback_disabled"},
                    status=status.HTTP_409_CONFLICT,
                )
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        return Response(SupportCSATSurveySerializer(survey).data, status=status.HTTP_201_CREATED)

    def delete(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            conversation = self._conversation(context, conversation_id)
            if context.role != "owner" and not can_reply(context, conversation):
                raise SupportConversationError(
                    "You cannot dismiss feedback for this conversation.",
                    code="csat_denied",
                    status_code=403,
                )
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        survey = survey_for_conversation(conversation)
        if survey:
            survey = dismiss_csat(survey)
        return Response(SupportCSATSurveySerializer(survey).data if survey else None)


class SupportWidgetConversationCSATView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_message"

    def _context(self, request, site_key, session_id):
        website = website_for_public_widget(site_key)
        origin = assert_widget_request_allowed(request, website)
        session = authenticate_widget_session(
            website=website,
            session_id=session_id,
            raw_token=token_from_request(request),
            origin=origin,
        )
        conversation = SupportConversation.objects.select_related(
            "website", "website__support_account", "visitor"
        ).filter(visitor=session.visitor, website=website).first()
        return session, conversation

    def get(self, request, site_key, session_id):
        try:
            session, conversation = self._context(request, site_key, session_id)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        if not conversation:
            return public_widget_response({"enabled": False, "survey": None})
        settings_obj = feedback_settings_for(conversation.website.support_account)
        survey = survey_for_conversation(conversation)
        return public_widget_response({
            "enabled": settings_obj.csat_enabled,
            "allow_comment": settings_obj.allow_comment,
            "survey": SupportCSATSurveySerializer(survey).data if survey else None,
        })

    def post(self, request, site_key, session_id):
        serializer = SupportCSATSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session, conversation = self._context(request, site_key, session_id)
            if not conversation:
                raise SupportFeedbackError("No feedback request is available.", code="survey_unavailable", status_code=404)
            survey = survey_for_conversation(conversation)
            if not survey:
                raise SupportFeedbackError("No feedback request is available.", code="survey_unavailable", status_code=404)
            survey = submit_csat(survey, **serializer.validated_data)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportFeedbackError as feedback_error:
            return public_widget_response(
                {"detail": feedback_error.detail, "code": feedback_error.code},
                status_code=feedback_error.status_code,
            )
        return public_widget_response({"survey": SupportCSATSurveySerializer(survey).data})

    def delete(self, request, site_key, session_id):
        try:
            session, conversation = self._context(request, site_key, session_id)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        survey = survey_for_conversation(conversation) if conversation else None
        if survey:
            survey = dismiss_csat(survey)
        return public_widget_response({"survey": SupportCSATSurveySerializer(survey).data if survey else None})


def knowledge_error_response(error: SupportKnowledgeError):
    return Response(
        {"detail": error.detail, "code": error.code},
        status=error.status_code,
    )


class SupportKnowledgeSettingsView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        return Response(SupportKnowledgeSettingsSerializer(knowledge_settings_for(context.account)).data)

    def patch(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        settings_obj = knowledge_settings_for(context.account)
        serializer = SupportKnowledgeSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        settings_obj = serializer.save(updated_by=request.user)
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="knowledge.settings_changed",
            target_type="support_knowledge_settings",
            target_id=settings_obj.id,
            summary=f"{request.user.username} updated knowledge base settings.",
            metadata={
                "enabled": settings_obj.enabled,
                "show_in_widget": settings_obj.show_in_widget,
                "suggestions_enabled": settings_obj.suggestions_enabled,
            },
            ip_address=request_ip(request),
        )
        return Response(SupportKnowledgeSettingsSerializer(settings_obj).data)


class SupportKnowledgeCategoryListCreateView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        queryset = SupportKnowledgeCategory.objects.filter(support_account=context.account)
        if context.role != "owner" or request.query_params.get("include_inactive") != "1":
            queryset = queryset.filter(is_active=True)
        queryset = queryset.annotate(
            article_count=Count("articles", filter=Q(articles__status=SupportKnowledgeArticle.Status.PUBLISHED))
        )
        return Response(SupportKnowledgeCategorySerializer(queryset, many=True).data)

    def post(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        serializer = SupportKnowledgeCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            category = serializer.save(
                support_account=context.account,
                created_by=request.user,
                updated_by=request.user,
            )
        except IntegrityError:
            return Response(
                {"detail": "An active category with this name already exists.", "code": "category_exists"},
                status=status.HTTP_409_CONFLICT,
            )
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="knowledge.category_created",
            target_type="support_knowledge_category",
            target_id=category.id,
            summary=f"{request.user.username} created the knowledge category {category.name}.",
            ip_address=request_ip(request),
        )
        return Response(SupportKnowledgeCategorySerializer(category).data, status=status.HTTP_201_CREATED)


class SupportKnowledgeCategoryDetailView(APIView):
    def _category(self, context, category_id):
        return get_object_or_404(SupportKnowledgeCategory, pk=category_id, support_account=context.account)

    def patch(self, request, category_id):
        context, error = require_owner(request, self)
        if error:
            return error
        category = self._category(context, category_id)
        serializer = SupportKnowledgeCategorySerializer(category, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            category = serializer.save(updated_by=request.user)
        except IntegrityError:
            return Response(
                {"detail": "An active category with this name already exists.", "code": "category_exists"},
                status=status.HTTP_409_CONFLICT,
            )
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="knowledge.category_updated",
            target_type="support_knowledge_category",
            target_id=category.id,
            summary=f"{request.user.username} updated the knowledge category {category.name}.",
            ip_address=request_ip(request),
        )
        return Response(SupportKnowledgeCategorySerializer(category).data)

    def delete(self, request, category_id):
        context, error = require_owner(request, self)
        if error:
            return error
        category = self._category(context, category_id)
        category.is_active = False
        category.updated_by = request.user
        category.save(update_fields=["is_active", "updated_by", "updated_at"])
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="knowledge.category_archived",
            target_type="support_knowledge_category",
            target_id=category.id,
            summary=f"{request.user.username} archived the knowledge category {category.name}.",
            ip_address=request_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportKnowledgeArticleListCreateView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        website = None
        website_id = request.query_params.get("website")
        if website_id:
            website = visible_websites(context).filter(pk=website_id).first()
            if not website:
                return Response({"detail": "Website access is unavailable.", "code": "website_denied"}, status=403)
        queryset = team_articles_for_context(
            context,
            website=website,
            status_value=request.query_params.get("status") or None,
        )
        category_id = request.query_params.get("category")
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        queryset = search_article_queryset(queryset, request.query_params.get("q") or "")
        return Response(SupportKnowledgeArticleSerializer(queryset.distinct(), many=True).data)

    def post(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        serializer = SupportKnowledgeArticleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        category = None
        if values.get("category_id"):
            category = get_object_or_404(
                SupportKnowledgeCategory,
                pk=values["category_id"],
                support_account=context.account,
                is_active=True,
            )
        try:
            with transaction.atomic():
                article = SupportKnowledgeArticle.objects.create(
                    support_account=context.account,
                    category=category,
                    title=values["title"],
                    slug=unique_article_slug(context.account, values["title"]),
                    summary=values.get("summary", ""),
                    body=values["body"],
                    status=values["status"],
                    all_websites=values["all_websites"],
                    is_featured=values.get("is_featured", False),
                    created_by=request.user,
                    updated_by=request.user,
                )
                replace_article_websites(article, values.get("website_ids", []))
                publish_state(article)
        except SupportKnowledgeError as knowledge_error:
            return knowledge_error_response(knowledge_error)
        article = SupportKnowledgeArticle.objects.select_related("category", "created_by", "updated_by").prefetch_related("website_assignments__website").get(pk=article.pk)
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="knowledge.article_created",
            target_type="support_knowledge_article",
            target_id=article.id,
            summary=f"{request.user.username} created the knowledge article {article.title}.",
            metadata={"status": article.status, "all_websites": article.all_websites},
            ip_address=request_ip(request),
        )
        return Response(SupportKnowledgeArticleSerializer(article).data, status=status.HTTP_201_CREATED)


class SupportKnowledgeArticleDetailView(APIView):
    def _article(self, context, article_id):
        article = team_articles_for_context(context).filter(pk=article_id).first()
        if not article:
            raise Http404
        return article

    def get(self, request, article_id):
        context, error = require_support_access(request)
        if error:
            return error
        return Response(SupportKnowledgeArticleSerializer(self._article(context, article_id)).data)

    def patch(self, request, article_id):
        context, error = require_owner(request, self)
        if error:
            return error
        article = get_object_or_404(SupportKnowledgeArticle, pk=article_id, support_account=context.account)
        serializer = SupportKnowledgeArticleWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        category = article.category
        if "category_id" in values:
            category = None
            if values["category_id"]:
                category = get_object_or_404(
                    SupportKnowledgeCategory,
                    pk=values["category_id"],
                    support_account=context.account,
                    is_active=True,
                )
        previous_status = article.status
        for field in ("title", "summary", "body", "status", "all_websites", "is_featured"):
            if field in values:
                setattr(article, field, values[field])
        article.category = category
        article.updated_by = request.user
        try:
            with transaction.atomic():
                article.full_clean(exclude=["websites"])
                article.save()
                if "all_websites" in values or "website_ids" in values:
                    replace_article_websites(article, values.get("website_ids", [a.website_id for a in article.website_assignments.all()]))
                publish_state(article, previous_status)
        except SupportKnowledgeError as knowledge_error:
            return knowledge_error_response(knowledge_error)
        article = SupportKnowledgeArticle.objects.select_related("category", "created_by", "updated_by").prefetch_related("website_assignments__website").get(pk=article.pk)
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="knowledge.article_updated",
            target_type="support_knowledge_article",
            target_id=article.id,
            summary=f"{request.user.username} updated the knowledge article {article.title}.",
            metadata={"status": article.status, "all_websites": article.all_websites},
            ip_address=request_ip(request),
        )
        return Response(SupportKnowledgeArticleSerializer(article).data)

    def delete(self, request, article_id):
        context, error = require_owner(request, self)
        if error:
            return error
        article = get_object_or_404(SupportKnowledgeArticle, pk=article_id, support_account=context.account)
        article.status = SupportKnowledgeArticle.Status.ARCHIVED
        article.published_at = None
        article.updated_by = request.user
        article.save(update_fields=["status", "published_at", "updated_by", "updated_at"])
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="knowledge.article_archived",
            target_type="support_knowledge_article",
            target_id=article.id,
            summary=f"{request.user.username} archived the knowledge article {article.title}.",
            ip_address=request_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportWidgetKnowledgeListView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_config"

    def get(self, request, site_key):
        try:
            website = website_for_public_widget(site_key)
            assert_widget_request_allowed(request, website)
        except WidgetAccessError as error:
            return widget_error_response(error)
        settings_obj = knowledge_settings_for(website.support_account)
        if not settings_obj.enabled or not settings_obj.show_in_widget:
            return public_widget_response({"enabled": False, "categories": [], "articles": []})
        category_id = request.query_params.get("category") or None
        query_value = request.query_params.get("q") or ""
        if not settings_obj.suggestions_enabled and not query_value.strip() and not category_id:
            articles = SupportKnowledgeArticle.objects.none()
        else:
            articles = public_search_articles(
                website,
                query=query_value,
                category_id=category_id,
                limit=request.query_params.get("limit") or settings_obj.max_suggestions,
            )
        visible_category_ids = public_articles_for_website(website).exclude(category_id=None).values_list("category_id", flat=True)
        categories = SupportKnowledgeCategory.objects.filter(
            support_account=website.support_account,
            is_active=True,
            id__in=visible_category_ids,
        ).distinct()
        return public_widget_response({
            "enabled": True,
            "suggestions_enabled": settings_obj.suggestions_enabled,
            "allow_feedback": settings_obj.allow_article_feedback,
            "categories": PublicKnowledgeCategorySerializer(categories, many=True).data,
            "articles": PublicKnowledgeArticleSerializer(articles, many=True).data,
        })


class SupportWidgetKnowledgeArticleView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_config"

    def get(self, request, site_key, article_id):
        try:
            website = website_for_public_widget(site_key)
            assert_widget_request_allowed(request, website)
        except WidgetAccessError as error:
            return widget_error_response(error)
        article = public_articles_for_website(website).filter(pk=article_id).first()
        if not article:
            return public_widget_response({"detail": "Article not found.", "code": "article_not_found"}, status_code=404)
        record_article_view(article)
        article.refresh_from_db(fields=["view_count", "helpful_count", "not_helpful_count"])
        return public_widget_response(PublicKnowledgeArticleSerializer(article).data)


class SupportWidgetKnowledgeFeedbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "support_widget_message"

    def post(self, request, site_key, article_id):
        serializer = PublicKnowledgeFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            website = website_for_public_widget(site_key)
            assert_widget_request_allowed(request, website)
        except WidgetAccessError as error:
            return widget_error_response(error)
        article = public_articles_for_website(website).filter(pk=article_id).first()
        if not article:
            return public_widget_response({"detail": "Article not found.", "code": "article_not_found"}, status_code=404)
        try:
            record_article_feedback(
                article=article,
                website=website,
                client_key=serializer.validated_data["client_key"],
                helpful=serializer.validated_data["helpful"],
            )
        except SupportKnowledgeError as knowledge_error:
            return public_widget_response(
                {"detail": knowledge_error.detail, "code": knowledge_error.code},
                status_code=knowledge_error.status_code,
            )
        article.refresh_from_db(fields=["helpful_count", "not_helpful_count"])
        return public_widget_response({
            "helpful": serializer.validated_data["helpful"],
            "helpful_count": article.helpful_count,
            "not_helpful_count": article.not_helpful_count,
        })


class SupportPrivacySettingsView(APIView):
    def get(self, request):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        settings_obj = privacy_settings_for(context.account)
        return Response(SupportPrivacySettingsSerializer(settings_obj).data)

    def patch(self, request):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        settings_obj = privacy_settings_for(context.account)
        serializer = SupportPrivacySettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(settings_obj, field, value)
        settings_obj.updated_by = request.user
        try:
            settings_obj.full_clean()
        except ValidationError as exc:
            return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=400)
        settings_obj.save()
        obj = settings_obj
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="privacy.settings_updated",
            target_type="support_privacy_settings",
            target_id=obj.id,
            summary="Support privacy and retention settings were updated.",
            metadata={
                "retention_enabled": obj.retention_enabled,
                "conversation_retention_days": obj.resolved_conversation_retention_days,
                "session_retention_days": obj.widget_session_retention_days,
            },
            ip_address=request_ip(request),
        )
        return Response(SupportPrivacySettingsSerializer(obj).data)


class SupportWebhookEndpointListCreateView(APIView):
    def get(self, request):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        endpoints = SupportWebhookEndpoint.objects.filter(support_account=context.account).order_by("name")
        return Response({
            "supported_events": list(SUPPORTED_WEBHOOK_EVENTS),
            "endpoints": SupportWebhookEndpointSerializer(endpoints, many=True).data,
        })

    def post(self, request):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        try:
            url = validate_webhook_url(request.data.get("url"))
            event_types = normalize_event_types(request.data.get("event_types"))
            secret = generate_webhook_secret()
            endpoint = SupportWebhookEndpoint(
                support_account=context.account,
                name=(request.data.get("name") or "").strip(),
                url=url,
                signing_secret=secret,
                event_types=event_types,
                is_active=bool(request.data.get("is_active", True)),
                created_by=request.user,
            )
            endpoint.full_clean()
            endpoint.save()
        except SupportWebhookError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)
        except ValidationError as exc:
            return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=400)
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="webhook.created",
            target_type="support_webhook_endpoint",
            target_id=endpoint.id,
            summary=f"Webhook {endpoint.name} was created.",
            metadata={"event_types": endpoint.event_types, "url": endpoint.url},
            ip_address=request_ip(request),
        )
        data = SupportWebhookEndpointSerializer(endpoint).data
        data["signing_secret"] = secret
        data["secret_notice"] = "Copy this signing secret now. It will not be shown again."
        return Response(data, status=201)


class SupportWebhookEndpointDetailView(APIView):
    def _endpoint(self, account, endpoint_id):
        return get_object_or_404(SupportWebhookEndpoint, pk=endpoint_id, support_account=account)

    def patch(self, request, endpoint_id):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        endpoint = self._endpoint(context.account, endpoint_id)
        try:
            if "url" in request.data:
                endpoint.url = validate_webhook_url(request.data.get("url"))
            if "event_types" in request.data:
                endpoint.event_types = normalize_event_types(request.data.get("event_types"))
            if "name" in request.data:
                endpoint.name = (request.data.get("name") or "").strip()
            if "is_active" in request.data:
                endpoint.is_active = bool(request.data.get("is_active"))
            endpoint.full_clean()
            endpoint.save()
        except SupportWebhookError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)
        except ValidationError as exc:
            return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=400)
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="webhook.updated",
            target_type="support_webhook_endpoint",
            target_id=endpoint.id,
            summary=f"Webhook {endpoint.name} was updated.",
            metadata={"event_types": endpoint.event_types, "active": endpoint.is_active},
            ip_address=request_ip(request),
        )
        return Response(SupportWebhookEndpointSerializer(endpoint).data)

    def delete(self, request, endpoint_id):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        endpoint = self._endpoint(context.account, endpoint_id)
        endpoint_id_value = endpoint.id
        endpoint_name = endpoint.name
        endpoint.delete()
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="webhook.deleted",
            target_type="support_webhook_endpoint",
            target_id=endpoint_id_value,
            summary=f"Webhook {endpoint_name} was removed.",
            ip_address=request_ip(request),
        )
        return Response(status=204)


class SupportWebhookEndpointSecretRotateView(APIView):
    def post(self, request, endpoint_id):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        endpoint = get_object_or_404(SupportWebhookEndpoint, pk=endpoint_id, support_account=context.account)
        secret = generate_webhook_secret()
        endpoint.signing_secret = secret
        endpoint.failure_count = 0
        endpoint.save(update_fields=["signing_secret", "failure_count", "updated_at"])
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="webhook.secret_rotated",
            target_type="support_webhook_endpoint",
            target_id=endpoint.id,
            summary=f"Webhook signing secret for {endpoint.name} was rotated.",
            ip_address=request_ip(request),
        )
        return Response({
            "signing_secret": secret,
            "secret_notice": "Copy this signing secret now. It will not be shown again.",
        })


class SupportWebhookEndpointTestView(APIView):
    def post(self, request, endpoint_id):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        endpoint = get_object_or_404(SupportWebhookEndpoint, pk=endpoint_id, support_account=context.account)
        delivery = SupportWebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type="webhook.test",
            payload={
                "id": None,
                "type": "webhook.test",
                "created_at": timezone.now().isoformat(),
                "data": {"support_account_id": str(context.account.id), "message": "Support Chat webhook test"},
            },
        )
        delivery.payload["id"] = str(delivery.event_id)
        delivery.save(update_fields=["payload", "updated_at"])
        try:
            from apps.support.tasks import deliver_support_webhook
            deliver_support_webhook.delay(str(delivery.id))
        except Exception:
            pass
        return Response(SupportWebhookDeliverySerializer(delivery).data, status=202)


class SupportWebhookDeliveryListView(APIView):
    def get(self, request):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        deliveries = SupportWebhookDelivery.objects.filter(endpoint__support_account=context.account).select_related("endpoint")
        endpoint_id = request.query_params.get("endpoint")
        if endpoint_id:
            deliveries = deliveries.filter(endpoint_id=endpoint_id)
        return Response(SupportWebhookDeliverySerializer(deliveries[:100], many=True).data)


class SupportDataExportListCreateView(APIView):
    def get(self, request):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        exports = SupportDataExport.objects.filter(support_account=context.account).order_by("-created_at")[:50]
        return Response(SupportDataExportSerializer(exports, many=True, context={"request": request}).data)

    def post(self, request):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        recent_active = SupportDataExport.objects.filter(
            support_account=context.account,
            status__in=[SupportDataExport.Status.PENDING, SupportDataExport.Status.PROCESSING],
        ).exists()
        if recent_active:
            return Response({"detail": "A Support data export is already being prepared.", "code": "export_in_progress"}, status=409)
        export = create_support_export(
            account=context.account,
            actor=request.user,
            include_attachments=request.data.get("include_attachments") if "include_attachments" in request.data else None,
        )
        try:
            from apps.support.tasks import generate_support_data_export
            generate_support_data_export.delay(str(export.id))
        except Exception:
            pass
        return Response(SupportDataExportSerializer(export, context={"request": request}).data, status=202)


class SupportDataExportDownloadView(APIView):
    def get(self, request, export_id):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        export = get_object_or_404(SupportDataExport, pk=export_id, support_account=context.account)
        if export.status != SupportDataExport.Status.READY or not export.file:
            return Response({"detail": "This export is not ready for download."}, status=409)
        if export.expires_at <= timezone.now():
            return Response({"detail": "This export has expired."}, status=410)
        response = FileResponse(export.file.open("rb"), as_attachment=True, filename=Path(export.file.name).name)
        response["Cache-Control"] = "no-store, private"
        return response


class SupportVisitorDeletionRequestListView(APIView):
    def get(self, request):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        items = SupportVisitorDeletionRequest.objects.filter(support_account=context.account).select_related("website")[:100]
        return Response(SupportVisitorDeletionRequestSerializer(items, many=True).data)


class SupportVisitorDeletionRequestCreateView(APIView):
    def post(self, request, visitor_id):
        context, denied = require_owner(request, self)
        if denied:
            return denied
        visitor = get_object_or_404(
            SupportVisitor.objects.select_related("website", "website__support_account"),
            pk=visitor_id,
            website__support_account=context.account,
        )
        try:
            deletion = request_visitor_deletion(
                visitor=visitor,
                source=SupportVisitorDeletionRequest.Source.OWNER,
                requested_by=request.user,
            )
        except SupportPrivacyError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)
        record_audit_event(
            account=context.account,
            website=visitor.website,
            actor=request.user,
            action="visitor.deletion_requested",
            target_type="support_visitor",
            target_id=visitor.id,
            summary="Visitor data deletion was requested.",
            metadata={"source": "owner"},
            ip_address=request_ip(request),
        )
        return Response(SupportVisitorDeletionRequestSerializer(deletion).data, status=202)


class SupportWidgetVisitorDeletionRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_widget_session"

    def post(self, request, site_key, session_id):
        try:
            website = website_for_public_widget(site_key)
            origin = assert_widget_request_allowed(request, website)
            session = authenticate_widget_session(
                website=website,
                session_id=session_id,
                raw_token=token_from_request(request),
                origin=origin,
            )
            settings_obj = privacy_settings_for(session.website.support_account)
            if not settings_obj.allow_visitor_deletion_requests:
                return Response({"detail": "Visitor deletion requests are not enabled."}, status=403)
            deletion = request_visitor_deletion(
                visitor=session.visitor,
                source=SupportVisitorDeletionRequest.Source.VISITOR,
                requested_by=None,
            )
            response = Response({"status": deletion.status, "requested_at": deletion.requested_at}, status=202)
            response["Cache-Control"] = "no-store"
            return response
        except WidgetAccessError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)


def support_call_error_response(error: SupportCallError, *, public=False):
    payload = {"detail": error.detail, "code": error.code}
    if public:
        return public_widget_response(payload, status_code=error.status_code)
    return Response(payload, status=error.status_code)


class SupportCallSettingsView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        return Response(SupportCallSettingsSerializer(call_settings_for(context.account)).data)

    def patch(self, request):
        context, error = require_owner(request, self)
        if error:
            return error
        settings_obj = call_settings_for(context.account)
        serializer = SupportCallSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        settings_obj = serializer.save(updated_by=request.user)
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="call.settings_changed",
            target_type="support_call_settings",
            target_id=settings_obj.id,
            summary=f"{request.user.username} updated Support call settings.",
            metadata={"enabled": settings_obj.enabled, "allow_video": settings_obj.allow_video},
            ip_address=request_ip(request),
        )
        return Response(SupportCallSettingsSerializer(settings_obj).data)


class SupportActiveCallView(APIView):
    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            call = team_active_call(context, request.user)
        except (SupportCallError, SupportConversationError) as call_error:
            if isinstance(call_error, SupportConversationError):
                return support_conversation_error_response(call_error)
            return support_call_error_response(call_error)
        return Response({"call": call_event_payload(call) if call else None})


class SupportConversationCallStartView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def get(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            call = team_active_call_for_conversation(context, conversation_id, request.user)
        except SupportConversationError as conversation_error:
            return support_conversation_error_response(conversation_error)
        return Response({"call": call_event_payload(call) if call else None})

    def post(self, request, conversation_id):
        context, error = require_support_access(request)
        if error:
            return error
        serializer = SupportCallStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            call = start_support_call(
                context=context,
                actor=request.user,
                conversation_id=conversation_id,
                call_type=serializer.validated_data["call_type"],
            )
        except (SupportCallError, SupportConversationError) as call_error:
            if isinstance(call_error, SupportConversationError):
                return support_conversation_error_response(call_error)
            return support_call_error_response(call_error)
        return Response(call_event_payload(call), status=status.HTTP_201_CREATED)


class SupportCallDetailView(APIView):
    def get(self, request, call_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            call = team_call_for_context(context, call_id, request.user)
            signals = pending_call_signals(
                call=call,
                recipient_kind=SupportCallParticipant.Kind.TEAM,
                consume=request.query_params.get("consume", "1") != "0",
            )
        except (SupportCallError, SupportConversationError) as call_error:
            if isinstance(call_error, SupportConversationError):
                return support_conversation_error_response(call_error)
            return support_call_error_response(call_error)
        payload = call_event_payload(call)
        payload["pending_signals"] = [signal_payload(item) for item in signals]
        return Response(payload)


class SupportCallEndView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def post(self, request, call_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            call = team_call_for_context(context, call_id, request.user)
            call = end_support_call(
                call=call,
                actor_user=request.user,
                reason=str(request.data.get("reason") or "ended"),
            )
        except (SupportCallError, SupportConversationError) as call_error:
            if isinstance(call_error, SupportConversationError):
                return support_conversation_error_response(call_error)
            return support_call_error_response(call_error)
        return Response(call_event_payload(call))


class SupportCallSignalView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_signal"

    def get(self, request, call_id):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            call = team_call_for_context(context, call_id, request.user)
            signals = pending_call_signals(call=call, recipient_kind=SupportCallParticipant.Kind.TEAM)
        except (SupportCallError, SupportConversationError) as call_error:
            if isinstance(call_error, SupportConversationError):
                return support_conversation_error_response(call_error)
            return support_call_error_response(call_error)
        return Response({"signals": [signal_payload(item) for item in signals]})

    def post(self, request, call_id):
        context, error = require_support_access(request)
        if error:
            return error
        serializer = SupportCallSignalWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            call = team_call_for_context(context, call_id, request.user)
            if call.initiated_by_id != request.user.id:
                raise SupportCallError("not_participant", "Only the agent who started this call can signal it.", 403)
            signal = create_call_signal(
                call=call,
                sender_kind=SupportCallParticipant.Kind.TEAM,
                sender_user=request.user,
                signal_type=serializer.validated_data["signal_type"],
                payload=serializer.validated_data.get("payload", {}),
            )
        except (SupportCallError, SupportConversationError) as call_error:
            if isinstance(call_error, SupportConversationError):
                return support_conversation_error_response(call_error)
            return support_call_error_response(call_error)
        return Response(signal_payload(signal), status=status.HTTP_201_CREATED)


class SupportCallMediaStateView(APIView):
    def patch(self, request, call_id):
        context, error = require_support_access(request)
        if error:
            return error
        serializer = SupportCallMediaStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            call = team_call_for_context(context, call_id, request.user)
            if call.initiated_by_id != request.user.id:
                raise SupportCallError("not_participant", "Only the agent who started this call can update it.", 403)
            update_call_media(
                call=call,
                kind=SupportCallParticipant.Kind.TEAM,
                user=request.user,
                **serializer.validated_data,
            )
        except (SupportCallError, SupportConversationError) as call_error:
            if isinstance(call_error, SupportConversationError):
                return support_conversation_error_response(call_error)
            return support_call_error_response(call_error)
        return Response(call_event_payload(call))


class SupportCallTurnCredentialsView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def get(self, request):
        context, error = require_support_access(request)
        if error:
            return error
        try:
            payload = team_turn_credentials(context, request.user)
        except SupportCallError as call_error:
            return support_call_error_response(call_error)
        return Response(payload)


def _widget_call_session(request, site_key, session_id):
    website = website_for_public_widget(site_key)
    origin = assert_widget_request_allowed(request, website)
    return authenticate_widget_session(
        website=website,
        session_id=session_id,
        raw_token=token_from_request(request),
        origin=origin,
    )


class SupportWidgetActiveCallView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def get(self, request, site_key, session_id):
        try:
            session = _widget_call_session(request, site_key, session_id)
            try:
                call = visitor_call_for_session(session)
            except SupportCallError as call_error:
                if call_error.code == "not_found":
                    return public_widget_response({"call": None})
                raise
            signals = pending_call_signals(call=call, recipient_kind=SupportCallParticipant.Kind.VISITOR)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        payload = call_event_payload(call)
        payload["pending_signals"] = [signal_payload(item) for item in signals]
        return public_widget_response({"call": payload})


class SupportWidgetCallDetailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def get(self, request, site_key, session_id, call_id):
        try:
            session = _widget_call_session(request, site_key, session_id)
            call = visitor_call_for_session(session, call_id)
            signals = pending_call_signals(call=call, recipient_kind=SupportCallParticipant.Kind.VISITOR)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        payload = call_event_payload(call)
        payload["pending_signals"] = [signal_payload(item) for item in signals]
        return public_widget_response(payload)


class SupportWidgetCallAcceptView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def post(self, request, site_key, session_id, call_id):
        try:
            session = _widget_call_session(request, site_key, session_id)
            call = visitor_call_for_session(session, call_id)
            call = accept_support_call(call=call, visitor=session.visitor)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        return public_widget_response(call_event_payload(call))


class SupportWidgetCallDeclineView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def post(self, request, site_key, session_id, call_id):
        try:
            session = _widget_call_session(request, site_key, session_id)
            call = visitor_call_for_session(session, call_id)
            call = decline_support_call(call=call, visitor=session.visitor, reason=str(request.data.get("reason") or "declined"))
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        return public_widget_response(call_event_payload(call))


class SupportWidgetCallEndView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def post(self, request, site_key, session_id, call_id):
        try:
            session = _widget_call_session(request, site_key, session_id)
            call = visitor_call_for_session(session, call_id)
            call = end_support_call(call=call, actor_visitor=session.visitor, reason=str(request.data.get("reason") or "ended"))
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        return public_widget_response(call_event_payload(call))


class SupportWidgetCallSignalView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_signal"

    def get(self, request, site_key, session_id, call_id):
        try:
            session = _widget_call_session(request, site_key, session_id)
            call = visitor_call_for_session(session, call_id)
            signals = pending_call_signals(call=call, recipient_kind=SupportCallParticipant.Kind.VISITOR)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        return public_widget_response({"signals": [signal_payload(item) for item in signals]})

    def post(self, request, site_key, session_id, call_id):
        serializer = SupportCallSignalWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session = _widget_call_session(request, site_key, session_id)
            call = visitor_call_for_session(session, call_id)
            signal = create_call_signal(
                call=call,
                sender_kind=SupportCallParticipant.Kind.VISITOR,
                sender_visitor=session.visitor,
                signal_type=serializer.validated_data["signal_type"],
                payload=serializer.validated_data.get("payload", {}),
            )
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        return public_widget_response(signal_payload(signal), status_code=status.HTTP_201_CREATED)


class SupportWidgetCallMediaStateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def patch(self, request, site_key, session_id, call_id):
        serializer = SupportCallMediaStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session = _widget_call_session(request, site_key, session_id)
            call = visitor_call_for_session(session, call_id)
            update_call_media(
                call=call,
                kind=SupportCallParticipant.Kind.VISITOR,
                visitor=session.visitor,
                **serializer.validated_data,
            )
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        return public_widget_response(call_event_payload(call))


class SupportWidgetCallTurnCredentialsView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_call_action"

    def get(self, request, site_key, session_id):
        try:
            session = _widget_call_session(request, site_key, session_id)
            payload = visitor_turn_credentials(session)
        except WidgetAccessError as widget_error:
            return widget_error_response(widget_error)
        except SupportCallError as call_error:
            return support_call_error_response(call_error, public=True)
        return public_widget_response(payload)
