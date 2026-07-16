from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import apps.chat.models
import uuid


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chat", "0012_message_edit_lock"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserStatus",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("content_type", models.CharField(choices=[("text", "Text"), ("image", "Image"), ("video", "Video")], max_length=12)),
                ("text", models.TextField(blank=True)),
                ("background_color", models.CharField(default="#111111", max_length=9)),
                ("text_color", models.CharField(default="#ffffff", max_length=9)),
                ("expires_at", models.DateTimeField(default=apps.chat.models.user_status_expiry_default)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("author", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_statuses", to=settings.AUTH_USER_MODEL)),
                ("upload", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="user_status", to="chat.pendingupload")),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.CreateModel(
            name="UserStatusView",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("viewed_at", models.DateTimeField(auto_now_add=True)),
                ("status", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="view_receipts", to="chat.userstatus")),
                ("viewer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="viewed_chat_statuses", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(model_name="userstatus", index=models.Index(fields=["author", "expires_at"], name="chat_userst_author__cc8a23_idx")),
        migrations.AddIndex(model_name="userstatus", index=models.Index(fields=["is_deleted", "expires_at"], name="chat_userst_is_dele_df35bd_idx")),
        migrations.AddIndex(model_name="userstatusview", index=models.Index(fields=["status", "viewed_at"], name="chat_userst_status__2d1420_idx")),
        migrations.AddIndex(model_name="userstatusview", index=models.Index(fields=["viewer", "viewed_at"], name="chat_userst_viewer__56a169_idx")),
        migrations.AddConstraint(model_name="userstatusview", constraint=models.UniqueConstraint(fields=("status", "viewer"), name="uniq_status_viewer")),
    ]
