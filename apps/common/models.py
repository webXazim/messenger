import uuid

from django.db import models
from django.utils import timezone


class BaseUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class RealtimeOutboxEvent(BaseUUIDModel):
    """Durable handoff record between Django transactions and Axum delivery."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event_name = models.CharField(max_length=120, db_index=True)
    payload = models.JSONField(default=dict)
    audiences = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    delivery_target = models.CharField(max_length=24, default="redis_stream", db_index=True)
    published_transport = models.CharField(max_length=24, blank=True)
    stream_entry_id = models.CharField(max_length=64, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["status", "available_at", "created_at"], name="rt_outbox_pending_idx"),
            models.Index(fields=["event_name", "created_at"], name="rt_outbox_event_time_idx"),
        ]
