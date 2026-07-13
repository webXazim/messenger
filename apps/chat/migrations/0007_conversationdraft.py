from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0006_e2ee_index_names"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationDraft",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("text", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="drafts", to="chat.conversation")),
                ("reply_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="chat.message")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conversation_drafts", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="conversationdraft",
            constraint=models.UniqueConstraint(fields=("conversation", "user"), name="uniq_conversation_draft"),
        ),
        migrations.AddIndex(
            model_name="conversationdraft",
            index=models.Index(fields=["user", "updated_at"], name="chat_conver_user_id_d2b10f_idx"),
        ),
        migrations.AddIndex(
            model_name="conversationdraft",
            index=models.Index(fields=["conversation", "updated_at"], name="chat_conver_convers_35cd6d_idx"),
        ),
    ]
