from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.support.models import (
    SupportCSATSurvey,
    SupportConversation,
    SupportFeedbackSettings,
)
from apps.support.realtime import publish_support_event
from apps.support.webhook_services import queue_support_webhook_event
from apps.support.workflow_services import record_audit_event


class SupportFeedbackError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


def feedback_settings_for(account):
    settings_obj, _ = SupportFeedbackSettings.objects.get_or_create(
        support_account=account,
    )
    return settings_obj


def expire_survey_if_needed(survey: SupportCSATSurvey | None) -> SupportCSATSurvey | None:
    if not survey:
        return None
    if survey.status == SupportCSATSurvey.Status.PENDING and survey.expires_at <= timezone.now():
        survey.status = SupportCSATSurvey.Status.EXPIRED
        survey.save(update_fields=["status", "updated_at"])
    return survey


def survey_for_conversation(conversation: SupportConversation) -> SupportCSATSurvey | None:
    try:
        survey = conversation.csat_survey
    except SupportCSATSurvey.DoesNotExist:
        return None
    return expire_survey_if_needed(survey)


def request_csat(
    conversation: SupportConversation,
    *,
    actor=None,
    source: str = SupportCSATSurvey.Source.MANUAL,
    force: bool = False,
) -> SupportCSATSurvey | None:
    settings_obj = feedback_settings_for(conversation.website.support_account)
    if not settings_obj.csat_enabled and not force:
        return None

    now = timezone.now()
    expires_at = now + timedelta(days=settings_obj.survey_expiry_days)
    with transaction.atomic():
        locked = SupportConversation.objects.select_for_update().select_related(
            "website", "website__support_account"
        ).get(pk=conversation.pk)
        survey = SupportCSATSurvey.objects.filter(support_conversation=locked).first()
        if survey and survey.status == SupportCSATSurvey.Status.SUBMITTED:
            return survey
        if survey:
            survey.status = SupportCSATSurvey.Status.PENDING
            survey.source = source
            survey.rating = None
            survey.comment = ""
            survey.requested_at = now
            survey.expires_at = expires_at
            survey.submitted_at = None
            survey.dismissed_at = None
            survey.requested_by = actor
            survey.save(update_fields=[
                "status", "source", "rating", "comment", "requested_at", "expires_at",
                "submitted_at", "dismissed_at", "requested_by", "updated_at",
            ])
        else:
            survey = SupportCSATSurvey.objects.create(
                support_account=locked.website.support_account,
                website=locked.website,
                support_conversation=locked,
                source=source,
                requested_by=actor,
                requested_at=now,
                expires_at=expires_at,
            )

    record_audit_event(
        account=conversation.website.support_account,
        website=conversation.website,
        support_conversation=conversation,
        actor=actor,
        action="conversation.csat_requested",
        target_type="support_csat_survey",
        target_id=survey.id,
        summary=(
            f"{getattr(actor, 'username', 'Support Chat')} requested customer feedback."
            if actor
            else "Support Chat requested customer feedback automatically."
        ),
        metadata={"source": source, "expires_at": survey.expires_at.isoformat()},
    )
    publish_support_event(
        event_name="support.csat.updated",
        website_id=conversation.website_id,
        visitor_id=conversation.visitor_id,
        data={
            "account_id": str(conversation.website.support_account_id),
            "website_id": str(conversation.website_id),
            "conversation_id": str(conversation.id),
            "survey_id": str(survey.id),
            "status": survey.status,
        },
    )
    return survey


def maybe_request_csat_on_resolve(conversation: SupportConversation, *, actor=None):
    settings_obj = feedback_settings_for(conversation.website.support_account)
    if not settings_obj.csat_enabled or not settings_obj.auto_request_on_resolve:
        return None
    return request_csat(
        conversation,
        actor=actor,
        source=SupportCSATSurvey.Source.AUTO,
    )


def submit_csat(
    survey: SupportCSATSurvey,
    *,
    rating: int,
    comment: str = "",
) -> SupportCSATSurvey:
    settings_obj = feedback_settings_for(survey.support_account)
    if not settings_obj.csat_enabled:
        raise SupportFeedbackError("Customer feedback is not enabled.", code="feedback_disabled", status_code=403)
    survey = expire_survey_if_needed(survey)
    if not survey or survey.status == SupportCSATSurvey.Status.EXPIRED:
        raise SupportFeedbackError("This feedback request has expired.", code="survey_expired", status_code=410)
    if survey.status == SupportCSATSurvey.Status.SUBMITTED:
        raise SupportFeedbackError("Feedback has already been submitted.", code="already_submitted", status_code=409)
    if survey.status == SupportCSATSurvey.Status.DISMISSED:
        raise SupportFeedbackError("This feedback request is no longer available.", code="survey_dismissed", status_code=410)
    try:
        rating = int(rating)
    except (TypeError, ValueError) as exc:
        raise SupportFeedbackError("Choose a rating from 1 to 5.", code="invalid_rating") from exc
    if rating < 1 or rating > 5:
        raise SupportFeedbackError("Choose a rating from 1 to 5.", code="invalid_rating")
    comment = (comment or "").strip()
    if not settings_obj.allow_comment:
        comment = ""
    if len(comment) > 2000:
        raise SupportFeedbackError("Feedback comments must be 2,000 characters or fewer.", code="comment_too_long")

    now = timezone.now()
    with transaction.atomic():
        locked = SupportCSATSurvey.objects.select_for_update().get(pk=survey.pk)
        locked = expire_survey_if_needed(locked)
        if locked.status != SupportCSATSurvey.Status.PENDING:
            raise SupportFeedbackError("This feedback request is no longer available.", code="survey_unavailable", status_code=409)
        locked.rating = rating
        locked.comment = comment
        locked.status = SupportCSATSurvey.Status.SUBMITTED
        locked.submitted_at = now
        locked.save(update_fields=["rating", "comment", "status", "submitted_at", "updated_at"])

    publish_support_event(
        event_name="support.csat.updated",
        website_id=locked.website_id,
        visitor_id=locked.support_conversation.visitor_id,
        data={
            "account_id": str(locked.support_account_id),
            "website_id": str(locked.website_id),
            "conversation_id": str(locked.support_conversation_id),
            "survey_id": str(locked.id),
            "status": locked.status,
            "rating": locked.rating,
        },
    )
    queue_support_webhook_event(
        account=locked.support_account,
        event_type="csat.submitted",
        payload={
            "survey_id": str(locked.id),
            "conversation_id": str(locked.support_conversation_id),
            "website_id": str(locked.website_id),
            "rating": locked.rating,
            "comment": locked.comment,
            "submitted_at": locked.submitted_at.isoformat() if locked.submitted_at else None,
        },
    )
    return locked


def dismiss_csat(survey: SupportCSATSurvey) -> SupportCSATSurvey:
    survey = expire_survey_if_needed(survey)
    if not survey or survey.status != SupportCSATSurvey.Status.PENDING:
        return survey
    survey.status = SupportCSATSurvey.Status.DISMISSED
    survey.dismissed_at = timezone.now()
    survey.save(update_fields=["status", "dismissed_at", "updated_at"])
    return survey
