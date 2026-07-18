import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models
from django.db.models import Count


def deactivate_duplicate_active_agents(apps, schema_editor):
    SupportAgent = apps.get_model("support", "SupportAgent")
    duplicates = (
        SupportAgent.objects.filter(is_active=True)
        .values("user_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for row in duplicates.iterator():
        agents = list(
            SupportAgent.objects.filter(user_id=row["user_id"], is_active=True)
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        if len(agents) > 1:
            SupportAgent.objects.filter(id__in=agents[1:]).update(is_active=False, availability="offline")


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(deactivate_duplicate_active_agents, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="supportagent",
            constraint=models.UniqueConstraint(
                fields=("user",),
                condition=models.Q(is_active=True),
                name="uniq_active_support_agent_user",
            ),
        ),
        migrations.CreateModel(
            name="SupportAgentInvitation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254)),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("accepted", "Accepted"), ("revoked", "Revoked"), ("expired", "Expired")], db_index=True, default="pending", max_length=20)),
                ("expires_at", models.DateTimeField()),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("last_sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("send_count", models.PositiveSmallIntegerField(default=1)),
                ("max_active_conversations", models.PositiveSmallIntegerField(default=5)),
                ("can_view_all_conversations", models.BooleanField(default=False)),
                ("can_assign_conversations", models.BooleanField(default=False)),
                ("can_view_analytics", models.BooleanField(default=False)),
                ("accepted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_agent_invitations_accepted", to=settings.AUTH_USER_MODEL)),
                ("invited_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_agent_invitations_sent", to=settings.AUTH_USER_MODEL)),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_invitations", to="support.supportaccount")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SupportAgentInvitationWebsite",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("invitation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="website_assignments", to="support.supportagentinvitation")),
                ("website", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_invitation_assignments", to="support.supportwebsite")),
            ],
        ),
        migrations.AddIndex(
            model_name="supportagentinvitation",
            index=models.Index(fields=["support_account", "status", "expires_at"], name="support_inv_acct_stat_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="supportagentinvitation",
            index=models.Index(fields=["email", "status"], name="support_inv_email_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="supportagentinvitation",
            constraint=models.UniqueConstraint(
                fields=("support_account", "email"),
                condition=models.Q(status="pending"),
                name="uniq_pending_support_agent_invitation",
            ),
        ),
        migrations.AddIndex(
            model_name="supportagentinvitationwebsite",
            index=models.Index(fields=["invitation", "website"], name="support_invite_website_idx"),
        ),
        migrations.AddConstraint(
            model_name="supportagentinvitationwebsite",
            constraint=models.UniqueConstraint(fields=("invitation", "website"), name="uniq_support_invitation_website"),
        ),
    ]
