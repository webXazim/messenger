# Generated manually for the isolated Rust media data plane.
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("chat", "0023_chat_data_plane_jobs")]

    operations = [
        migrations.CreateModel(
            name="MediaProcessingJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("processing", "Processing"), ("completed", "Completed"), ("failed", "Failed")], db_index=True, default="pending", max_length=20)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("available_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("lease_token", models.UUIDField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("worker_name", models.CharField(blank=True, max_length=120)),
                ("processing_version", models.PositiveSmallIntegerField(default=1)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("last_error", models.TextField(blank=True)),
                ("upload", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="media_processing_job", to="chat.pendingupload")),
            ],
            options={"ordering": ["available_at", "created_at", "id"]},
        ),
        migrations.AddIndex(
            model_name="mediaprocessingjob",
            index=models.Index(fields=["status", "available_at"], name="chat_media_job_status_due_idx"),
        ),
        migrations.AddIndex(
            model_name="mediaprocessingjob",
            index=models.Index(fields=["locked_at"], name="chat_media_job_locked_idx"),
        ),
    ]
