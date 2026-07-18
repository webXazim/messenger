from celery import shared_task

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


@shared_task(name="apps.support.tasks.retry_pending_support_webhooks")
def retry_pending_support_webhooks():
    from django.utils import timezone
    from apps.support.models import SupportWebhookDelivery
    from apps.support.webhook_services import deliver_webhook

    deliveries = list(
        SupportWebhookDelivery.objects.filter(
            status=SupportWebhookDelivery.Status.PENDING,
            next_attempt_at__lte=timezone.now(),
        ).order_by("next_attempt_at")[:100]
    )
    for delivery in deliveries:
        deliver_webhook(delivery)
    return len(deliveries)


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
