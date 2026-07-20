import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0019_analytics_aggregates"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportNotificationSettings",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("new_conversation", models.BooleanField(default=True)),
                ("assignment_changed", models.BooleanField(default=True)),
                ("sla_due_soon", models.BooleanField(default=True)),
                ("sla_breached", models.BooleanField(default=True)),
                ("internal_mention", models.BooleanField(default=True)),
                ("follow_up_due", models.BooleanField(default=True)),
                ("daily_summary", models.BooleanField(default=False)),
                ("daily_summary_hour", models.PositiveSmallIntegerField(default=8)),
                ("support_account", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="notification_settings", to="support.supportaccount")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_notification_settings_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name_plural": "Support notification settings"},
        ),
        migrations.CreateModel(
            name="SupportSecuritySettings",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("require_verified_identity_for_sensitive_actions", models.BooleanField(default=False)),
                ("block_unverified_attachments", models.BooleanField(default=False)),
                ("max_attachment_mb", models.PositiveSmallIntegerField(default=25)),
                ("allowed_attachment_extensions", models.JSONField(blank=True, default=list)),
                ("retain_audit_days", models.PositiveIntegerField(default=730)),
                ("webhook_failure_disable_threshold", models.PositiveSmallIntegerField(default=20)),
                ("agent_session_timeout_minutes", models.PositiveIntegerField(default=1440)),
                ("support_account", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="security_settings", to="support.supportaccount")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_security_settings_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name_plural": "Support security settings"},
        ),
        migrations.CreateModel(
            name="SupportAutomationRule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=140)),
                ("description", models.CharField(blank=True, max_length=500)),
                ("trigger", models.CharField(choices=[("conversation_created","Conversation created"),("visitor_message","Visitor message"),("agent_message","Agent message"),("status_changed","Status changed"),("assignment_changed","Assignment changed"),("tag_added","Tag added"),("sla_due_soon","SLA due soon"),("sla_breached","SLA breached"),("follow_up_due","Follow-up due")], db_index=True, max_length=40)),
                ("conditions", models.JSONField(blank=True, default=list)),
                ("actions", models.JSONField(blank=True, default=list)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("priority", models.PositiveSmallIntegerField(db_index=True, default=100)),
                ("stop_processing", models.BooleanField(default=False)),
                ("execution_limit", models.PositiveSmallIntegerField(default=10)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_automation_rules_created", to=settings.AUTH_USER_MODEL)),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="automation_rules", to="support.supportaccount")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_automation_rules_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["priority", "created_at"]},
        ),
        migrations.CreateModel(
            name="SupportAutomationExecution",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("trigger", models.CharField(db_index=True, max_length=40)),
                ("idempotency_key", models.CharField(max_length=160, unique=True)),
                ("status", models.CharField(choices=[("started","Started"),("succeeded","Succeeded"),("skipped","Skipped"),("failed","Failed")], db_index=True, default="started", max_length=16)),
                ("actions_executed", models.PositiveSmallIntegerField(default=0)),
                ("duration_ms", models.PositiveIntegerField(default=0)),
                ("context", models.JSONField(blank=True, default=dict)),
                ("error", models.CharField(blank=True, max_length=1000)),
                ("rule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="executions", to="support.supportautomationrule")),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="automation_executions", to="support.supportaccount")),
                ("support_conversation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="automation_executions", to="support.supportconversation")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(model_name="supportautomationrule", constraint=models.UniqueConstraint(fields=("support_account","name"), name="uniq_support_automation_name")),
        migrations.AddIndex(model_name="supportautomationrule", index=models.Index(fields=["support_account","trigger","is_active","priority"], name="sup_auto_acct_trigger_idx")),
        migrations.AddIndex(model_name="supportautomationexecution", index=models.Index(fields=["support_account","status","created_at"], name="sup_auto_exec_acct_idx")),
    ]
