from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("support", "0014_support_teams_and_permissions"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="SupportRoutingPolicy",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mode", models.CharField(choices=[("manual", "Manual"), ("round_robin", "Round robin"), ("least_busy", "Least busy")], default="manual", max_length=24)),
                ("least_busy_tiebreaker", models.BooleanField(default=True)),
                ("overflow_behavior", models.CharField(choices=[("leave_unassigned", "Leave unassigned"), ("least_busy", "Assign to least-busy agent"), ("notify_owner", "Notify owner")], default="leave_unassigned", max_length=24)),
                ("offline_reassignment_minutes", models.PositiveSmallIntegerField(default=15)),
                ("prefer_previous_agent", models.BooleanField(default=True)),
                ("enabled", models.BooleanField(default=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_routing_policies_updated", to=settings.AUTH_USER_MODEL)),
                ("website", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="routing_policy", to="support.supportwebsite")),
            ],
        ),
        migrations.CreateModel(
            name="SupportRoutingCursor",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("assignment_count", models.PositiveBigIntegerField(default=0)),
                ("last_assigned_agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="support.supportagent")),
                ("policy", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cursor", to="support.supportroutingpolicy")),
            ],
        ),
        migrations.AddField(model_name="supportconversation", name="assigned_team", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assigned_conversations", to="support.supportteam")),
        migrations.AddField(model_name="supportconversation", name="assigned_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="supportconversation", name="assignment_trigger", field=models.CharField(blank=True, max_length=40)),
        migrations.AddIndex(model_name="supportroutingpolicy", index=models.Index(fields=["website", "enabled"], name="sup_route_site_enabled_idx")),
        migrations.AddIndex(model_name="supportconversation", index=models.Index(fields=["assigned_team", "status"], name="sup_conv_team_status_idx")),
        migrations.AlterField(
            model_name="supportservicealert",
            name="kind",
            field=models.CharField(choices=[("first_response_due_soon", "First response due soon"), ("first_response_overdue", "First response overdue"), ("next_response_due_soon", "Next response due soon"), ("next_response_overdue", "Next response overdue"), ("resolution_due_soon", "Resolution due soon"), ("resolution_overdue", "Resolution overdue"), ("follow_up_due", "Follow-up due"), ("routing_unassigned", "Routing left conversation unassigned")], db_index=True, max_length=40),
        ),
    ]
