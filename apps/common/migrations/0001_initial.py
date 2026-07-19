# Generated manually for the Axum realtime migration foundation.

import uuid

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="RealtimeOutboxEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("event_name", models.CharField(db_index=True, max_length=120)),
                ("payload", models.JSONField(default=dict)),
                ("audiences", models.JSONField(default=list)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("published", "Published"), ("failed", "Failed")], db_index=True, default="pending", max_length=16)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("available_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="realtimeoutboxevent",
            index=models.Index(fields=["status", "available_at", "created_at"], name="rt_outbox_pending_idx"),
        ),
        migrations.AddIndex(
            model_name="realtimeoutboxevent",
            index=models.Index(fields=["event_name", "created_at"], name="rt_outbox_event_time_idx"),
        ),
    ]
