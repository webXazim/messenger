from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import MediaProcessingJob, PendingUpload


PROCESSABLE_MEDIA_KINDS = {
    PendingUpload.MediaKind.IMAGE,
    PendingUpload.MediaKind.VIDEO,
    PendingUpload.MediaKind.AUDIO,
}


def media_processing_backend() -> str:
    value = str(getattr(settings, "MEDIA_PROCESSING_BACKEND", "django") or "django").strip().lower()
    return value if value in {"django", "rust_shadow", "rust"} else "django"


def enqueue_media_processing(upload: PendingUpload, *, force: bool = False) -> MediaProcessingJob | None:
    """Create one durable Rust media job after antivirus approval.

    The pending upload remains the source of truth. The worker rechecks scan and
    upload state while holding a lease, so enqueueing is safe to repeat.
    """
    if upload.scan_status != PendingUpload.ScanStatus.CLEAN:
        return None
    if upload.status not in {PendingUpload.UploadStatus.PENDING, PendingUpload.UploadStatus.ATTACHED}:
        return None
    if upload.media_kind not in PROCESSABLE_MEDIA_KINDS:
        return None

    with transaction.atomic():
        job, created = MediaProcessingJob.objects.select_for_update().get_or_create(
            upload=upload,
            defaults={
                "status": MediaProcessingJob.Status.PENDING,
                "available_at": timezone.now(),
                "processing_version": 1,
            },
        )
        if created:
            return job
        if job.status == MediaProcessingJob.Status.PROCESSING and not force:
            return job
        if job.status == MediaProcessingJob.Status.COMPLETED and not force:
            return job
        job.status = MediaProcessingJob.Status.PENDING
        job.available_at = timezone.now()
        job.locked_at = None
        job.lease_token = None
        job.completed_at = None
        job.worker_name = ""
        job.last_error = ""
        job.result = {}
        job.save(
            update_fields=[
                "status",
                "available_at",
                "locked_at",
                "lease_token",
                "completed_at",
                "worker_name",
                "last_error",
                "result",
                "updated_at",
            ]
        )
        return job


def enqueue_missing_media_processing_jobs(*, batch_size: int = 250) -> int:
    """Recover clean uploads whose enqueue transaction was interrupted."""
    if media_processing_backend() not in {"rust", "rust_shadow"}:
        return 0
    limit = max(1, min(2000, int(batch_size)))
    uploads = (
        PendingUpload.objects.filter(
            scan_status=PendingUpload.ScanStatus.CLEAN,
            status__in=[PendingUpload.UploadStatus.PENDING, PendingUpload.UploadStatus.ATTACHED],
            media_kind__in=PROCESSABLE_MEDIA_KINDS,
        )
        .filter(Q(media_processing_job__isnull=True) | Q(media_processing_job__status=MediaProcessingJob.Status.FAILED))
        .order_by("created_at", "id")[:limit]
    )
    count = 0
    for upload in uploads:
        if enqueue_media_processing(upload, force=hasattr(upload, "media_processing_job")):
            count += 1
    return count


def recover_media_processing_with_django(*, batch_size: int = 10) -> int:
    """Optional emergency fallback; disabled by default in production.

    It intentionally processes only exhausted Rust jobs. Normal media CPU work
    never returns to Celery unless an operator explicitly enables this switch.
    """
    if media_processing_backend() != "rust":
        return 0
    if not bool(getattr(settings, "MEDIA_WORKER_DJANGO_FALLBACK_ENABLED", False)):
        return 0
    minimum_attempts = max(1, int(getattr(settings, "MEDIA_WORKER_DJANGO_FALLBACK_AFTER_ATTEMPTS", 4)))
    limit = max(1, min(50, int(batch_size)))
    stale_before = timezone.now() - timedelta(minutes=2)

    jobs = list(
        MediaProcessingJob.objects.select_related("upload")
        .filter(status=MediaProcessingJob.Status.FAILED, attempts__gte=minimum_attempts, available_at__lte=timezone.now())
        .filter(Q(locked_at__isnull=True) | Q(locked_at__lte=stale_before))
        .order_by("available_at", "created_at", "id")[:limit]
    )
    completed = 0
    for job in jobs:
        try:
            upload = job.upload
            if upload.scan_status != PendingUpload.ScanStatus.CLEAN:
                MediaProcessingJob.objects.filter(pk=job.pk).update(
                    status=MediaProcessingJob.Status.FAILED,
                    last_error="Upload is no longer antivirus-clean.",
                    updated_at=timezone.now(),
                )
                continue
            from .services import enrich_pending_upload_media

            enrich_pending_upload_media(upload)
            MediaProcessingJob.objects.filter(pk=job.pk).update(
                status=MediaProcessingJob.Status.COMPLETED,
                completed_at=timezone.now(),
                locked_at=None,
                lease_token=None,
                worker_name="django-emergency-fallback",
                last_error="",
                result={"processor": "django-emergency-fallback"},
                updated_at=timezone.now(),
            )
            completed += 1
        except Exception as exc:
            MediaProcessingJob.objects.filter(pk=job.pk).update(
                available_at=timezone.now() + timedelta(minutes=5),
                last_error=str(exc)[:2000],
                updated_at=timezone.now(),
            )
    return completed
