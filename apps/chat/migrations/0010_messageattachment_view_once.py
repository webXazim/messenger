from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0009_pendingupload_metadata"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="messageattachment",
            name="view_once",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.CreateModel(
            name="MessageAttachmentViewReceipt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("opened_at", models.DateTimeField(auto_now_add=True)),
                ("attachment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="view_receipts", to="chat.messageattachment")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="view_once_attachment_receipts", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["user", "opened_at"], name="chat_messag_user_id_f1c9d5_idx")],
                "constraints": [models.UniqueConstraint(fields=("attachment", "user"), name="uniq_view_once_attachment_user")],
            },
        ),
    ]
