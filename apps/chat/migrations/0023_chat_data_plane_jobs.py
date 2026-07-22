import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0022_message_sender_restore_state"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatDataPlaneJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("kind", models.CharField(choices=[("message_created", "Message created"), ("conversation_cleanup", "Conversation cleanup")], max_length=40)),
                ("dedupe_key", models.CharField(max_length=180, unique=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("processing", "Processing"), ("completed", "Completed"), ("failed", "Failed")], db_index=True, default="pending", max_length=20)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("available_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("conversation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="data_plane_jobs", to="chat.conversation")),
                ("message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="data_plane_jobs", to="chat.message")),
            ],
            options={"ordering": ["available_at", "created_at", "id"]},
        ),
        migrations.AddIndex(
            model_name="chatdataplanejob",
            index=models.Index(fields=["status", "available_at"], name="chat_dp_job_status_due_idx"),
        ),
        migrations.AddIndex(
            model_name="chatdataplanejob",
            index=models.Index(fields=["conversation", "created_at"], name="chat_dp_job_conv_time_idx"),
        ),
    ]
