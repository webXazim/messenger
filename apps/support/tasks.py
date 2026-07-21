from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.support.service_operations import scan_service_operations
from apps.support.services import support_chat_enabled


@shared_task(name="apps.support.tasks.scan_support_service_operations")
def scan_support_service_operations():
    if not support_chat_enabled():
        return 0
    return scan_service_operations()


@shared_task(name="apps.support.tasks.deliver_support_webhook")
def deliver_support_webhook(delivery_id: str):
    from apps.support.models import SupportWebhookDelivery
    from apps.support.webhook_services import deliver_webhook

    delivery = SupportWebhookDelivery.objects.filter(pk=delivery_id).first()
    if not delivery:
        return None
    return str(deliver_webhook(delivery).status)


@shared_task(name="apps.support.tasks.retry_pending_support_webhooks", ignore_result=True)
def retry_pending_support_webhooks():
    """Lease due webhook deliveries and fan them out to short worker tasks.

    The lease prevents overlapping Celery Beat executions from dispatching the
    same delivery repeatedly. Stale PROCESSING rows are reclaimed after a
    bounded timeout so a terminated worker cannot strand them forever.
    """
    from apps.support.models import SupportWebhookDelivery

    now = timezone.now()
    batch_size = max(1, min(500, int(getattr(settings, "SUPPORT_WEBHOOK_DISPATCH_BATCH_SIZE", 100))))
    lease_seconds = max(30, int(getattr(settings, "SUPPORT_WEBHOOK_LEASE_SECONDS", 120)))
    stale_before = now - timedelta(seconds=lease_seconds)
    with transaction.atomic():
        deliveries = list(
            SupportWebhookDelivery.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=SupportWebhookDelivery.Status.PENDING, next_attempt_at__lte=now)
                | Q(status=SupportWebhookDelivery.Status.PROCESSING, updated_at__lte=stale_before)
            )
            .order_by("next_attempt_at", "id")[:batch_size]
        )
        ids = [delivery.id for delivery in deliveries]
        if ids:
            SupportWebhookDelivery.objects.filter(id__in=ids).update(
                status=SupportWebhookDelivery.Status.PROCESSING,
                updated_at=now,
            )

    dispatched = 0
    for delivery_id in ids:
        try:
            deliver_support_webhook.apply_async(args=[str(delivery_id)], expires=lease_seconds)
            dispatched += 1
        except Exception:
            SupportWebhookDelivery.objects.filter(
                id=delivery_id, status=SupportWebhookDelivery.Status.PROCESSING
            ).update(status=SupportWebhookDelivery.Status.PENDING, next_attempt_at=now, updated_at=timezone.now())
    return dispatched


@shared_task(name="apps.support.tasks.generate_support_data_export")
def generate_support_data_export(export_id: str):
    from apps.support.models import SupportDataExport
    from apps.support.privacy_services import generate_support_export

    export = SupportDataExport.objects.filter(pk=export_id).first()
    if not export:
        return None
    return str(generate_support_export(export).status)


@shared_task(name="apps.support.tasks.process_support_visitor_deletion")
def process_support_visitor_deletion(deletion_id: str):
    from apps.support.models import SupportVisitorDeletionRequest
    from apps.support.privacy_services import process_visitor_deletion

    deletion = SupportVisitorDeletionRequest.objects.filter(pk=deletion_id).first()
    if not deletion:
        return None
    return str(process_visitor_deletion(deletion).status)


@shared_task(name="apps.support.tasks.run_support_retention")
def run_support_retention():
    from apps.support.privacy_services import run_support_retention as run

    if not support_chat_enabled():
        return {"conversations": 0, "sessions": 0, "exports": 0}
    return run()


@shared_task(name="apps.support.tasks.maintain_support_calls")
def maintain_support_calls_task():
    from apps.support.call_services import maintain_support_calls

    return maintain_support_calls()


@shared_task(name="apps.support.tasks.reassign_offline_support_conversations")
def reassign_offline_support_conversations():
    from apps.support.routing_services import reassign_offline_conversations
    if not support_chat_enabled(): return 0
    return reassign_offline_conversations()


@shared_task(name="apps.support.tasks.wake_snoozed_support_conversations")
def wake_snoozed_support_conversations():
    from apps.support.lifecycle_services import wake_due_snoozed_conversations
    if not support_chat_enabled():
        return 0
    return wake_due_snoozed_conversations()


@shared_task(name="apps.support.tasks.aggregate_recent_support_analytics")
def aggregate_recent_support_analytics(days=3):
    from apps.support.analytics_aggregates import aggregate_recent_support_metrics
    if not support_chat_enabled():
        return 0
    return aggregate_recent_support_metrics(days=days)


@shared_task(name="apps.support.tasks.build_support_analytics_export")
def build_support_analytics_export(export_id):
    from apps.support.analytics_aggregates import build_export
    return build_export(export_id)


@shared_task(
    name="apps.support.tasks.send_support_agent_invitation_email",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def send_support_agent_invitation_email(invitation_id: str, raw_token: str):
    from apps.support.models import SupportAgentInvitation
    from apps.support.services import send_agent_invitation_email

    invitation = (
        SupportAgentInvitation.objects.select_related("invited_by", "invited_by__profile")
        .prefetch_related("website_assignments__website", "team_assignments__team")
        .filter(pk=invitation_id, status=SupportAgentInvitation.Status.PENDING)
        .first()
    )
    if not invitation:
        return 0
    try:
        sent = send_agent_invitation_email(invitation, raw_token)
        if sent < 1:
            raise RuntimeError("Support agent invitation email was not accepted by the configured email backend.")
    except Exception as exc:
        SupportAgentInvitation.objects.filter(pk=invitation.pk).update(
            email_delivery_status=SupportAgentInvitation.DeliveryStatus.FAILED,
            email_delivery_error=str(exc)[:1000],
            email_delivered_at=None,
        )
        raise
    SupportAgentInvitation.objects.filter(pk=invitation.pk).update(
        email_delivery_status=SupportAgentInvitation.DeliveryStatus.SENT,
        email_delivery_error="",
        email_delivered_at=timezone.now(),
    )
    return sent
