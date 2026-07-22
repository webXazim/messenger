from __future__ import annotations

from datetime import timedelta

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from .models import ChatDataPlaneJob
from .services import cleanup_conversation_if_unretained


def _process_message_created(job: ChatDataPlaneJob) -> None:
    if job.message_id:
        from .tasks import fanout_push_notifications

        # Execute inside the existing Celery worker instead of enqueueing a
        # second broker message. The data-plane job itself is the durable retry.
        fanout_push_notifications.run(str(job.message_id))


def _process_conversation_cleanup(job: ChatDataPlaneJob) -> None:
    if job.conversation_id:
        cleanup_conversation_if_unretained(job.conversation)


def process_due_chat_data_plane_jobs(*, batch_size: int = 150, lease_seconds: int = 120) -> int:
    now = timezone.now()
    stale_before = now - timedelta(seconds=max(30, lease_seconds))
    limit = max(1, min(500, int(batch_size)))
    with transaction.atomic():
        jobs = list(
            ChatDataPlaneJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status__in=[ChatDataPlaneJob.Status.PENDING, ChatDataPlaneJob.Status.FAILED], available_at__lte=now)
                | Q(status=ChatDataPlaneJob.Status.PROCESSING, locked_at__lte=stale_before)
            )
            .order_by("available_at", "created_at", "id")[:limit]
        )
        ids = [job.id for job in jobs]
        if ids:
            ChatDataPlaneJob.objects.filter(id__in=ids).update(
                status=ChatDataPlaneJob.Status.PROCESSING,
                locked_at=now,
                attempts=models.F("attempts") + 1,
                updated_at=now,
            )

    completed = 0
    for job_id in ids:
        try:
            job = (
                ChatDataPlaneJob.objects.select_related("conversation", "message")
                .get(pk=job_id)
            )
            if job.status == ChatDataPlaneJob.Status.COMPLETED:
                continue
            if job.kind == ChatDataPlaneJob.Kind.MESSAGE_CREATED:
                _process_message_created(job)
            elif job.kind == ChatDataPlaneJob.Kind.CONVERSATION_CLEANUP:
                _process_conversation_cleanup(job)
            ChatDataPlaneJob.objects.filter(pk=job_id).update(
                status=ChatDataPlaneJob.Status.COMPLETED,
                locked_at=None,
                last_error="",
                updated_at=timezone.now(),
            )
            completed += 1
        except Exception as exc:
            current = ChatDataPlaneJob.objects.filter(pk=job_id).only("attempts").first()
            if current:
                delay = min(300, 2 ** min(int(current.attempts or 1), 8))
                ChatDataPlaneJob.objects.filter(pk=job_id).update(
                    status=ChatDataPlaneJob.Status.FAILED,
                    locked_at=None,
                    available_at=timezone.now() + timedelta(seconds=delay),
                    last_error=str(exc)[:2000],
                    updated_at=timezone.now(),
                )
    return completed


def cleanup_completed_chat_data_plane_jobs(*, retention_days: int = 7, batch_size: int = 5000) -> int:
    cutoff = timezone.now() - timedelta(days=max(1, int(retention_days)))
    ids = list(
        ChatDataPlaneJob.objects.filter(
            status=ChatDataPlaneJob.Status.COMPLETED,
            updated_at__lt=cutoff,
        )
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[: max(1, min(20_000, int(batch_size)))]
    )
    if not ids:
        return 0
    deleted, _ = ChatDataPlaneJob.objects.filter(id__in=ids).delete()
    return int(deleted)
