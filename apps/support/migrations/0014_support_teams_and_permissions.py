from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("support", "0013_support_call_initiator_kind"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        *[migrations.AddField(model_name=model, name=name, field=models.BooleanField(default=False))
          for model in ("supportagent", "supportagentinvitation")
          for name in ("can_manage_websites", "can_manage_knowledge", "can_manage_teams", "can_manage_automations", "can_export_data")],
        migrations.CreateModel(
            name="SupportTeam",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("default_max_active_conversations", models.PositiveSmallIntegerField(default=5)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_teams_created", to=settings.AUTH_USER_MODEL)),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="teams", to="support.supportaccount")),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="SupportTeamMembership",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("agent", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="team_memberships", to="support.supportagent")),
                ("team", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="support.supportteam")),
            ],
        ),
        migrations.CreateModel(
            name="SupportWebsiteTeam",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_default", models.BooleanField(default=False)),
                ("team", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="website_assignments", to="support.supportteam")),
                ("website", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="team_assignments", to="support.supportwebsite")),
            ],
        ),
        migrations.CreateModel(
            name="SupportAgentInvitationTeam",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("invitation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="team_assignments", to="support.supportagentinvitation")),
                ("team", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_invitation_assignments", to="support.supportteam")),
            ],
        ),
        migrations.AddConstraint(model_name="supportteam", constraint=models.UniqueConstraint(models.functions.Lower("name"), models.F("support_account"), name="uniq_support_team_name_ci")),
        migrations.AddIndex(model_name="supportteam", index=models.Index(fields=["support_account", "is_active"], name="sup_team_acct_active_idx")),
        migrations.AddConstraint(model_name="supportteammembership", constraint=models.UniqueConstraint(fields=("team", "agent"), name="uniq_support_team_agent")),
        migrations.AddIndex(model_name="supportteammembership", index=models.Index(fields=["team", "agent"], name="sup_team_agent_idx")),
        migrations.AddConstraint(model_name="supportwebsiteteam", constraint=models.UniqueConstraint(fields=("website", "team"), name="uniq_support_website_team")),
        migrations.AddConstraint(model_name="supportwebsiteteam", constraint=models.UniqueConstraint(condition=models.Q(("is_default", True)), fields=("website",), name="uniq_default_support_website_team")),
        migrations.AddConstraint(model_name="supportagentinvitationteam", constraint=models.UniqueConstraint(fields=("invitation", "team"), name="uniq_support_invitation_team")),
    ]
