from django.conf import settings
from django.db import migrations, models
import uuid
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0017_lifecycle_collaboration"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="supportservicesettings",
            name="pause_while_waiting_customer",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="supportservicesettings",
            name="pause_resolution_while_snoozed",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="supportservicesettings",
            name="escalate_on_breach",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="supportservicesettings",
            name="escalation_team",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="service_escalation_settings", to="support.supportteam"),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="sla_paused_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="sla_pause_reason",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="sla_total_paused_seconds",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="sla_last_recalculated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="sla_escalated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="SupportSlaPolicy",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("first_response_targets", models.JSONField(blank=True, default=dict)),
                ("next_response_targets", models.JSONField(blank=True, default=dict)),
                ("resolution_targets", models.JSONField(blank=True, default=dict)),
                ("due_soon_minutes", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("pause_while_waiting_customer", models.BooleanField(blank=True, null=True)),
                ("pause_resolution_while_snoozed", models.BooleanField(blank=True, null=True)),
                ("alert_owner", models.BooleanField(blank=True, null=True)),
                ("alert_assigned_agent", models.BooleanField(blank=True, null=True)),
                ("escalate_on_breach", models.BooleanField(blank=True, null=True)),
                ("escalation_team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sla_policy_escalations", to="support.supportteam")),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sla_policies", to="support.supportaccount")),
                ("team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="sla_policies", to="support.supportteam")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_sla_policies_updated", to=settings.AUTH_USER_MODEL)),
                ("website", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="sla_policies", to="support.supportwebsite")),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="supportslapolicy",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(website__isnull=False, team__isnull=True)
                    | models.Q(website__isnull=True, team__isnull=False)
                ),
                name="support_sla_policy_one_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="supportslapolicy",
            constraint=models.UniqueConstraint(condition=models.Q(("website__isnull", False)), fields=("support_account", "website"), name="uniq_support_sla_policy_website"),
        ),
        migrations.AddConstraint(
            model_name="supportslapolicy",
            constraint=models.UniqueConstraint(condition=models.Q(("team__isnull", False)), fields=("support_account", "team"), name="uniq_support_sla_policy_team"),
        ),
        migrations.AddIndex(
            model_name="supportslapolicy",
            index=models.Index(fields=["support_account", "is_active"], name="sup_sla_policy_acct_idx"),
        ),
        migrations.AddIndex(
            model_name="supportconversation",
            index=models.Index(fields=["status", "sla_paused_at"], name="sup_conv_sla_pause_idx"),
        ),
        migrations.AlterField(
            model_name="supportservicealert",
            name="kind",
            field=models.CharField(
                choices=[
                    ("first_response_due_soon", "First response due soon"),
                    ("first_response_overdue", "First response overdue"),
                    ("next_response_due_soon", "Next response due soon"),
                    ("next_response_overdue", "Next response overdue"),
                    ("resolution_due_soon", "Resolution due soon"),
                    ("resolution_overdue", "Resolution overdue"),
                    ("follow_up_due", "Follow-up due"),
                    ("routing_unassigned", "Routing left conversation unassigned"),
                    ("sla_escalated", "SLA breach escalated"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
