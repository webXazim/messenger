from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.support.models import (
    SupportAuditEvent,
    SupportDataPlaneJob,
    SupportServiceAlert,
)
from apps.support.service_operations import on_team_message, on_visitor_message
from apps.support.webhook_services import queue_support_webhook_event
from apps.support.workflow_services import person_name, record_audit_event


def _message_payload(job: SupportDataPlaneJob) -> dict:
    conversation = job.support_conversation
    message = job.message
    if message is None:
        return {}
    attachment_count = message.attachments.count()
    sender_kind = str((job.payload or {}).get("sender_kind") or ("visitor" if message.sender_id is None else "agent"))
    return {
        "conversation_id": str(conversation.id),
        "website_id": str(conversation.website_id),
        "visitor_id": str(conversation.visitor_id),
        "message_id": str(message.id),
        "sender_kind": sender_kind,
        "message_type": message.type,
        "text": message.text,
        "attachment_count": attachment_count,
        "created_at": message.created_at.isoformat(),
    }


def _process_message_created(job: SupportDataPlaneJob) -> None:
    conversation = job.support_conversation
    message = job.message
    if message is None:
        return
    sender_kind = str((job.payload or {}).get("sender_kind") or "")
    if sender_kind == "visitor" or message.sender_id is None:
        on_visitor_message(conversation, message_at=message.created_at)
    else:
        on_team_message(conversation, message_at=message.created_at)
    queue_support_webhook_event(
        account=conversation.website.support_account,
        event_type="message.created",
        payload=_message_payload(job),
    )


def _process_conversation_created(job: SupportDataPlaneJob) -> None:
    conversation = job.support_conversation
    account = conversation.website.support_account
    queue_support_webhook_event(
        account=account,
        event_type="conversation.created",
        payload={
            "conversation_id": str(conversation.id),
            "website_id": str(conversation.website_id),
            "visitor_id": str(conversation.visitor_id),
            "status": conversation.status,
            "priority": conversation.priority,
            "created_at": conversation.created_at.isoformat(),
        },
    )

    # Axum owns routing decisions; Django records the durable business audit and
    # owner alert asynchronously without adding work to the visitor request.
    if conversation.assigned_agent_id:
        exists = SupportAuditEvent.objects.filter(
            support_conversation=conversation,
            action="conversation.auto_assigned",
        ).exists()
        if not exists:
            record_audit_event(
                account=account,
                website=conversation.website,
                support_conversation=conversation,
                action="conversation.auto_assigned",
                target_type="support_conversation",
                target_id=conversation.id,
                summary=f"Conversation assigned to {person_name(conversation.assigned_agent.user)}.",
                metadata={
                    "agent_id": str(conversation.assigned_agent_id),
                    "team_id": str(conversation.assigned_team_id or ""),
                    "trigger": conversation.assignment_trigger or "conversation_created",
                    "source": "axum_data_plane",
                },
            )
        return

    policy = getattr(conversation.website, "routing_policy", None)
    if not policy or not policy.enabled or policy.mode == "manual":
        return
    exists = SupportAuditEvent.objects.filter(
        support_conversation=conversation,
        action="conversation.routing_unassigned",
    ).exists()
    if not exists:
        record_audit_event(
            account=account,
            website=conversation.website,
            support_conversation=conversation,
            action="conversation.routing_unassigned",
            target_type="support_conversation",
            target_id=conversation.id,
            summary="Automatic routing left the conversation unassigned.",
            metadata={
                "trigger": conversation.assignment_trigger or "conversation_created",
                "mode": policy.mode,
                "overflow": policy.overflow_behavior,
                "source": "axum_data_plane",
            },
        )
    if policy.overflow_behavior == "notify_owner":
        SupportServiceAlert.objects.get_or_create(
            dedupe_key=f"routing:{conversation.id}",
            defaults={
                "support_account": account,
                "website": conversation.website,
                "support_conversation": conversation,
                "recipient": account.owner,
                "kind": SupportServiceAlert.Kind.ROUTING_UNASSIGNED,
                "due_at": timezone.now(),
                "metadata": {"trigger": conversation.assignment_trigger or "conversation_created"},
            },
        )


def process_due_support_data_plane_jobs(*, batch_size: int = 100, lease_seconds: int = 120) -> int:
    # Import locally to avoid making model module import order more fragile.
    from django.db import models

    now = timezone.now()
    stale_before = now - timedelta(seconds=max(30, lease_seconds))
    with transaction.atomic():
        jobs = list(
            SupportDataPlaneJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status__in=[SupportDataPlaneJob.Status.PENDING, SupportDataPlaneJob.Status.FAILED], available_at__lte=now)
                | Q(status=SupportDataPlaneJob.Status.PROCESSING, locked_at__lte=stale_before)
            )
            .order_by("available_at", "created_at", "id")[: max(1, min(500, batch_size))]
        )
        ids = [job.id for job in jobs]
        if ids:
            SupportDataPlaneJob.objects.filter(id__in=ids).update(
                status=SupportDataPlaneJob.Status.PROCESSING,
                locked_at=now,
                attempts=models.F("attempts") + 1,
                updated_at=now,
            )

    completed = 0
    for job_id in ids:
        try:
            with transaction.atomic():
                job = (
                    SupportDataPlaneJob.objects.select_for_update()
                    .select_related(
                        "message",
                        "support_conversation__assigned_agent__user",
                        "support_conversation__assigned_team",
                        "support_conversation__website__support_account__owner",
                        "support_conversation__website__routing_policy",
                    )
                    .get(pk=job_id)
                )
                if job.status == SupportDataPlaneJob.Status.COMPLETED:
                    continue
                if job.kind == SupportDataPlaneJob.Kind.MESSAGE_CREATED:
                    _process_message_created(job)
                elif job.kind == SupportDataPlaneJob.Kind.CONVERSATION_CREATED:
                    _process_conversation_created(job)
                job.status = SupportDataPlaneJob.Status.COMPLETED
                job.locked_at = None
                job.last_error = ""
                job.save(update_fields=["status", "locked_at", "last_error", "updated_at"])
                completed += 1
        except Exception as exc:
            failed = SupportDataPlaneJob.objects.filter(pk=job_id).first()
            if failed:
                delay = min(300, 2 ** min(int(failed.attempts or 1), 8))
                SupportDataPlaneJob.objects.filter(pk=job_id).update(
                    status=SupportDataPlaneJob.Status.FAILED,
                    locked_at=None,
                    available_at=timezone.now() + timedelta(seconds=delay),
                    last_error=str(exc)[:2000],
                    updated_at=timezone.now(),
                )
    return completed

def cleanup_completed_support_data_plane_jobs(*, retention_days: int = 7, batch_size: int = 5000) -> int:
    cutoff = timezone.now() - timedelta(days=max(1, retention_days))
    ids = list(
        SupportDataPlaneJob.objects.filter(
            status=SupportDataPlaneJob.Status.COMPLETED,
            updated_at__lt=cutoff,
        )
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[: max(1, min(20000, batch_size))]
    )
    if not ids:
        return 0
    deleted, _ = SupportDataPlaneJob.objects.filter(id__in=ids).delete()
    return int(deleted)

