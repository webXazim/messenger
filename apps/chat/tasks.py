import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .antivirus import antivirus_healthcheck
from .antivirus import scan_file_field
from .models import CallSession, ConversationNotificationSetting, Message, MessageAttachment, PendingUpload, UserDevice
from .push import is_firebase_configured, resolve_firebase_service_account_path, send_push, send_push_with_options
from .services import (
    refresh_call_orchestration,
    build_call_recovery_plan,
    get_call_quality_summary,
    expire_pending_upload_if_needed,
    expire_ringing_call,
    expire_stale_call_participants,
    get_calling_config,
    get_notification_preference,
    request_user_devices,
    scan_upload_file,
)

logger = logging.getLogger(__name__)


def _message_notification_preview(message, preference):
    body_fallback = "New message"
    metadata = dict(message.metadata or {})
    has_encrypted_attachments = message.attachments.filter(metadata__encrypted_attachment=True).exists()
    if metadata.get("encrypted") or has_encrypted_attachments:
        return body_fallback
    if not getattr(preference, "message_preview_enabled", True):
        return body_fallback
    return (message.text or body_fallback)[:120]


@shared_task
def send_push_notification_stub(message_id):
    logger.info("Push notification task queued for message %s", message_id)


@shared_task
def fanout_push_notifications(message_id):
    try:
        message = Message.objects.select_related("sender", "sender__profile", "conversation").get(id=message_id)
    except Message.DoesNotExist:
        logger.warning("Message %s does not exist for push fanout", message_id)
        return {"attempted": 0, "sent": 0, "failed": 0, "invalidated_devices": 0}

    attempted = sent = failed = invalidated_devices = 0
    title = getattr(getattr(message.sender, "profile", None), "display_name", "") or message.sender.username
    recipients = message.conversation.participants.filter(left_at__isnull=True).select_related("user", "user__profile")
    for participant in recipients:
        if participant.user_id == message.sender_id:
            continue
        preference = get_notification_preference(participant.user)
        if preference.mute_all or not preference.push_enabled:
            continue
        convo_setting, _ = ConversationNotificationSetting.objects.get_or_create(
            conversation=message.conversation,
            user=participant.user,
        )
        if not convo_setting.message_notifications_enabled:
            continue
        if convo_setting.muted_until and convo_setting.muted_until > timezone.now():
            continue
        if convo_setting.mentions_only:
            mentioned_user_ids = {str(user_id) for user_id in (message.metadata or {}).get("mentioned_user_ids", [])}
            if str(participant.user_id) not in mentioned_user_ids:
                continue
        devices = request_user_devices(participant.user).filter(is_active=True)
        preview = _message_notification_preview(message, preference)
        common_data = {
            "event": "message",
            "conversation_id": message.conversation_id,
            "message_id": message.id,
            "sender_name": title,
            "title": title,
            "body": preview,
        }
        for platform in ("android", "ios", "web"):
            platform_devices = devices.filter(platform=platform)
            tokens = list(platform_devices.values_list("push_token", flat=True))
            if not tokens:
                continue
            if platform == "web":
                # Data-only web pushes let our service worker render actions such as Reply.
                result = send_push_with_options(
                    tokens,
                    title=title,
                    body=preview,
                    data=common_data,
                    include_notification=False,
                )
            else:
                result = send_push(
                    tokens,
                    title=title,
                    body=preview,
                    data=common_data,
                )
            attempted += result.attempted
            sent += result.sent
            failed += result.failed
            invalid_tokens = getattr(result, "invalid_tokens", []) or []
            if invalid_tokens:
                invalidated_devices += platform_devices.filter(push_token__in=invalid_tokens).update(is_active=False, updated_at=timezone.now())
    return {"attempted": attempted, "sent": sent, "failed": failed, "invalidated_devices": invalidated_devices}


@shared_task
def fanout_incoming_call_notifications(call_id):
    try:
        call = CallSession.objects.select_related("initiated_by", "initiated_by__profile", "conversation").get(id=call_id)
    except CallSession.DoesNotExist:
        logger.warning("Call %s does not exist for push fanout", call_id)
        return {"attempted": 0, "sent": 0, "failed": 0, "invalidated_devices": 0}

    if call.status not in {CallSession.Status.INITIATED, CallSession.Status.RINGING}:
        return {"attempted": 0, "sent": 0, "failed": 0, "invalidated_devices": 0}
    ring_timeout_seconds = int(get_calling_config()["offer_timeout_seconds"])
    if call.started_at and call.started_at <= timezone.now() - timedelta(seconds=ring_timeout_seconds):
        expire_ringing_call(call)
        return {"attempted": 0, "sent": 0, "failed": 0, "invalidated_devices": 0, "expired": 1}

    attempted = sent = failed = invalidated_devices = 0
    caller_name = getattr(getattr(call.initiated_by, "profile", None), "display_name", "") or call.initiated_by.username
    title = f"Incoming {call.call_type} call"
    body = f"{caller_name} is calling you"
    participants = call.conversation.participants.filter(left_at__isnull=True).select_related("user", "user__profile")
    for participant in participants:
        if participant.user_id == call.initiated_by_id:
            continue
        preference = get_notification_preference(participant.user)
        if preference.mute_all or not preference.push_enabled:
            continue
        convo_setting, _ = ConversationNotificationSetting.objects.get_or_create(
            conversation=call.conversation,
            user=participant.user,
        )
        if not convo_setting.call_notifications_enabled:
            continue
        if convo_setting.muted_until and convo_setting.muted_until > timezone.now():
            continue
        devices = request_user_devices(participant.user).filter(is_active=True)
        common_data = {
            "event": "incoming_call",
            "mode": "incoming",
            "call_id": call.id,
            "conversation_id": call.conversation_id,
            "call_type": call.call_type,
            "status": call.status,
            "caller_name": caller_name,
            "title": title,
            "body": body,
        }
        for platform in ("android", "ios", "web"):
            platform_devices = devices.filter(platform=platform)
            tokens = list(platform_devices.values_list("push_token", flat=True))
            if not tokens:
                continue
            if platform == "android":
                result = send_push_with_options(
                    tokens,
                    title=title,
                    body=body,
                    data=common_data,
                    include_notification=False,
                    android_priority="high",
                    android_ttl_seconds=ring_timeout_seconds,
                    android_collapse_key=f"incoming-call-{call.id}",
                )
            else:
                result = send_push(
                    tokens,
                    title=title,
                    body=body,
                    data=common_data,
                )
            attempted += result.attempted
            sent += result.sent
            failed += result.failed
            invalid_tokens = getattr(result, "invalid_tokens", []) or []
            if invalid_tokens:
                invalidated_devices += platform_devices.filter(push_token__in=invalid_tokens).update(is_active=False, updated_at=timezone.now())
    return {"attempted": attempted, "sent": sent, "failed": failed, "invalidated_devices": invalidated_devices}


@shared_task
def scan_pending_upload(upload_id):
    try:
        upload = PendingUpload.objects.get(id=upload_id)
    except PendingUpload.DoesNotExist:
        logger.warning("Pending upload %s does not exist", upload_id)
        return None
    return scan_upload_file(upload).scan_status


@shared_task
def rescan_attachment(attachment_id):
    try:
        attachment = MessageAttachment.objects.get(id=attachment_id)
    except MessageAttachment.DoesNotExist:
        logger.warning("Attachment %s does not exist", attachment_id)
        return None
    verdict = scan_file_field(attachment.file)
    if verdict.is_clean:
        attachment.scan_status = MessageAttachment.ScanStatus.CLEAN
    elif verdict.status == "failed":
        attachment.scan_status = MessageAttachment.ScanStatus.FAILED
    else:
        attachment.scan_status = MessageAttachment.ScanStatus.INFECTED
    attachment.scan_notes = f"{verdict.engine}: {verdict.notes}"
    attachment.scanned_at = timezone.now()
    attachment.save(update_fields=["scan_status", "scan_notes", "scanned_at", "updated_at"])
    return attachment.scan_status


@shared_task
def expire_stale_pending_uploads():
    expired = 0
    for upload in PendingUpload.objects.filter(status=PendingUpload.UploadStatus.PENDING, expires_at__lte=timezone.now()):
        expire_pending_upload_if_needed(upload)
        expired += 1
    return expired


@shared_task

def deactivate_stale_devices():
    threshold = timezone.now() - timedelta(days=int(getattr(settings, "DEVICE_INACTIVE_DAYS", 30) or 30))
    qs = UserDevice.objects.filter(is_active=True, last_seen_at__lt=threshold)
    count = qs.update(is_active=False, updated_at=timezone.now())
    return count


@shared_task
def integration_health_snapshot():
    av = antivirus_healthcheck()
    firebase_path = resolve_firebase_service_account_path()
    return {
        "antivirus": {
            "enabled": av.enabled,
            "available": av.available,
            "engine": av.engine,
            "details": av.details,
            "ping_ok": av.ping_ok,
            "version": av.version,
            "fail_open": av.fail_open,
        },
        "push": {
            "configured": is_firebase_configured(),
            "project_id": getattr(settings, "FIREBASE_PROJECT_ID", ""),
            "dry_run": bool(getattr(settings, "FCM_DRY_RUN", True)),
            "service_account_path": str(firebase_path) if firebase_path else "",
        },
    }


@shared_task
def expire_stale_calls():
    config = get_calling_config()
    threshold = timezone.now() - timedelta(seconds=int(config["offer_timeout_seconds"]))
    count = 0
    for call in CallSession.objects.filter(status__in=[CallSession.Status.INITIATED, CallSession.Status.RINGING], started_at__lt=threshold):
        expire_ringing_call(call)
        count += 1
    return count


@shared_task
def expire_stale_call_participants_task():
    return expire_stale_call_participants()


@shared_task
def refresh_active_call_orchestration_task():
    count = 0
    for call in CallSession.objects.filter(status=CallSession.Status.ONGOING).prefetch_related("participants"):
        metadata = dict(call.metadata or {})
        metadata["recovery_plan"] = build_call_recovery_plan(call)
        metadata["aggregate_quality"] = get_call_quality_summary(call)
        call.metadata = metadata
        call.save(update_fields=["metadata", "updated_at"])
        refresh_call_orchestration(call)
        count += 1
    return count
