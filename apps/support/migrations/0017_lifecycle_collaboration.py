from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0016_knowledge_production"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="supportconversation",
            name="follow_up_assignee",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="follow_up_conversations", to="support.supportagent"),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="previous_status",
            field=models.CharField(blank=True, choices=[("new","New"),("open","Open"),("waiting_customer","Waiting for customer"),("waiting_team","Waiting for team"),("snoozed","Snoozed"),("resolved","Resolved"),("closed","Closed")], max_length=24),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="snoozed_until",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="snoozed_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_conversations_snoozed", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(model_name="supportconversation", name="resolution_reason", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="supportconversation", name="closure_reason", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="supportconversation", name="reopened_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="supportconversation", name="reopen_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="supportconversation", name="revision_number", field=models.PositiveBigIntegerField(default=1)),
        migrations.CreateModel(
            name="SupportConversationFollower",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("added_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_followers_added", to=settings.AUTH_USER_MODEL)),
                ("support_conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="followers", to="support.supportconversation")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="followed_support_conversations", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="SupportInternalNoteMention",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("note", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mentions", to="support.supportinternalnote")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_note_mentions", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="SupportConversationTransfer",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("note", models.TextField(blank=True, max_length=2000)),
                ("from_agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="support.supportagent")),
                ("from_team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="support.supportteam")),
                ("support_conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transfers", to="support.supportconversation")),
                ("to_agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="support.supportagent")),
                ("to_team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="support.supportteam")),
                ("transferred_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_transfers_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="supportconversationfollower",
            constraint=models.UniqueConstraint(fields=("support_conversation","user"), name="uniq_support_conversation_follower"),
        ),
        migrations.AddConstraint(
            model_name="supportinternalnotemention",
            constraint=models.UniqueConstraint(fields=("note","user"), name="uniq_support_note_mention"),
        ),
        migrations.AddIndex(
            model_name="supportconversationfollower",
            index=models.Index(fields=["user","created_at"], name="sup_follow_user_time_idx"),
        ),
        migrations.AddIndex(
            model_name="supportconversationtransfer",
            index=models.Index(fields=["support_conversation","created_at"], name="sup_transfer_conv_time_idx"),
        ),
        migrations.AddIndex(
            model_name="supportconversation",
            index=models.Index(fields=["status","snoozed_until"], name="sup_conv_snooze_due_idx"),
        ),
        migrations.AddIndex(
            model_name="supportconversation",
            index=models.Index(fields=["follow_up_assignee","follow_up_at"], name="sup_conv_follow_agent_idx"),
        ),
    ]
