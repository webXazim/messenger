from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chat", "0014_message_sender_nullable"),
        ("support", "0003_widget_sessions"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportConversation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("new", "New"), ("open", "Open"), ("waiting_customer", "Waiting for customer"), ("waiting_team", "Waiting for team"), ("resolved", "Resolved"), ("closed", "Closed")], db_index=True, default="new", max_length=24)),
                ("priority", models.CharField(choices=[("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")], db_index=True, default="normal", max_length=16)),
                ("subject", models.CharField(blank=True, max_length=255)),
                ("first_response_at", models.DateTimeField(blank=True, null=True)),
                ("last_visitor_message_at", models.DateTimeField(blank=True, null=True)),
                ("last_agent_message_at", models.DateTimeField(blank=True, null=True)),
                ("visitor_last_read_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("assigned_agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assigned_conversations", to="support.supportagent")),
                ("conversation", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="support_conversation", to="chat.conversation")),
                ("visitor", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="support_conversation", to="support.supportvisitor")),
                ("visitor_last_read_message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="chat.message")),
                ("website", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="support_conversations", to="support.supportwebsite")),
            ],
            options={"ordering": ["-conversation__last_message_at", "-created_at"]},
        ),
        migrations.CreateModel(
            name="SupportMessageAuthor",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("display_name", models.CharField(blank=True, max_length=120)),
                ("message", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="support_author", to="chat.message")),
                ("session", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="authored_messages", to="support.supportwidgetsession")),
                ("visitor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="authored_support_messages", to="support.supportvisitor")),
            ],
        ),
        migrations.CreateModel(
            name="SupportConversationReadState",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("last_read_at", models.DateTimeField(blank=True, null=True)),
                ("last_read_message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="chat.message")),
                ("support_conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="read_states", to="support.supportconversation")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_conversation_read_states", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(model_name="supportconversation", index=models.Index(fields=["website", "status", "updated_at"], name="sup_conv_site_stat_upd_idx")),
        migrations.AddIndex(model_name="supportconversation", index=models.Index(fields=["assigned_agent", "status"], name="sup_conv_agent_status_idx")),
        migrations.AddIndex(model_name="supportconversation", index=models.Index(fields=["priority", "status"], name="sup_conv_prio_status_idx")),
        migrations.AddIndex(model_name="supportmessageauthor", index=models.Index(fields=["visitor", "created_at"], name="sup_msg_author_vis_time_idx")),
        migrations.AddIndex(model_name="supportconversationreadstate", index=models.Index(fields=["user", "updated_at"], name="sup_conv_read_user_upd_idx")),
        migrations.AddConstraint(model_name="supportconversationreadstate", constraint=models.UniqueConstraint(fields=("support_conversation", "user"), name="uniq_support_conv_user_read")),
    ]
