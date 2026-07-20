import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0018_production_sla"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportAnalyticsDailyMetric",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("metric_date", models.DateField(db_index=True)),
                ("conversations_created", models.PositiveIntegerField(default=0)),
                ("conversations_resolved", models.PositiveIntegerField(default=0)),
                ("conversations_reopened", models.PositiveIntegerField(default=0)),
                ("messages_total", models.PositiveIntegerField(default=0)),
                ("visitor_messages", models.PositiveIntegerField(default=0)),
                ("agent_messages", models.PositiveIntegerField(default=0)),
                ("first_response_seconds_total", models.PositiveBigIntegerField(default=0)),
                ("first_response_count", models.PositiveIntegerField(default=0)),
                ("resolution_seconds_total", models.PositiveBigIntegerField(default=0)),
                ("resolution_count", models.PositiveIntegerField(default=0)),
                ("sla_eligible_count", models.PositiveIntegerField(default=0)),
                ("sla_compliant_count", models.PositiveIntegerField(default=0)),
                ("csat_rating_total", models.PositiveIntegerField(default=0)),
                ("csat_response_count", models.PositiveIntegerField(default=0)),
                ("unassigned_seconds_total", models.PositiveBigIntegerField(default=0)),
                ("handled_count", models.PositiveIntegerField(default=0)),
                ("agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="analytics_daily_metrics", to="support.supportagent")),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="analytics_daily_metrics", to="support.supportaccount")),
                ("team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="analytics_daily_metrics", to="support.supportteam")),
                ("website", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="analytics_daily_metrics", to="support.supportwebsite")),
            ],
        ),
        migrations.CreateModel(
            name="SupportAnalyticsHourlyMetric",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("metric_date", models.DateField(db_index=True)),
                ("hour", models.PositiveSmallIntegerField()),
                ("conversations_created", models.PositiveIntegerField(default=0)),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="analytics_hourly_metrics", to="support.supportaccount")),
                ("website", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="analytics_hourly_metrics", to="support.supportwebsite")),
            ],
        ),
        migrations.CreateModel(
            name="SupportAnalyticsTagMetric",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("metric_date", models.DateField(db_index=True)),
                ("conversation_count", models.PositiveIntegerField(default=0)),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="analytics_tag_metrics", to="support.supportaccount")),
                ("tag", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="analytics_metrics", to="support.supporttag")),
                ("website", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="analytics_tag_metrics", to="support.supportwebsite")),
            ],
        ),
        migrations.CreateModel(
            name="SupportAnalyticsExport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("queued","Queued"),("processing","Processing"),("ready","Ready"),("failed","Failed")], db_index=True, default="queued", max_length=16)),
                ("format", models.CharField(default="csv", max_length=12)),
                ("filters", models.JSONField(blank=True, default=dict)),
                ("file_key", models.CharField(blank=True, max_length=500)),
                ("error_message", models.TextField(blank=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_analytics_exports", to=settings.AUTH_USER_MODEL)),
                ("support_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="analytics_exports", to="support.supportaccount")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="supportanalyticsdailymetric",
            constraint=models.UniqueConstraint(fields=("support_account","metric_date","website","team","agent"), name="uniq_support_daily_metric_scope", nulls_distinct=False),
        ),
        migrations.AddConstraint(
            model_name="supportanalyticsdailymetric",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(website__isnull=True, team__isnull=True, agent__isnull=True)
                    | models.Q(website__isnull=False, team__isnull=True, agent__isnull=True)
                    | models.Q(website__isnull=True, team__isnull=False, agent__isnull=True)
                    | models.Q(website__isnull=True, team__isnull=True, agent__isnull=False)
                ),
                name="support_daily_metric_one_dimension",
            ),
        ),
        migrations.AddConstraint(
            model_name="supportanalyticshourlymetric",
            constraint=models.UniqueConstraint(fields=("support_account","metric_date","hour","website"), name="uniq_support_hourly_metric_scope", nulls_distinct=False),
        ),
        migrations.AddConstraint(
            model_name="supportanalyticshourlymetric",
            constraint=models.CheckConstraint(condition=models.Q(hour__gte=0, hour__lte=23), name="support_hour_between_0_23"),
        ),
        migrations.AddConstraint(
            model_name="supportanalyticstagmetric",
            constraint=models.UniqueConstraint(fields=("support_account","metric_date","tag","website"), name="uniq_support_tag_metric_scope", nulls_distinct=False),
        ),
        migrations.AddIndex(model_name="supportanalyticsdailymetric", index=models.Index(fields=["support_account","metric_date"], name="sup_metric_acct_date_idx")),
        migrations.AddIndex(model_name="supportanalyticsdailymetric", index=models.Index(fields=["website","metric_date"], name="sup_metric_site_date_idx")),
        migrations.AddIndex(model_name="supportanalyticsdailymetric", index=models.Index(fields=["team","metric_date"], name="sup_metric_team_date_idx")),
        migrations.AddIndex(model_name="supportanalyticsdailymetric", index=models.Index(fields=["agent","metric_date"], name="sup_metric_agent_date_idx")),
        migrations.AddIndex(model_name="supportanalyticshourlymetric", index=models.Index(fields=["support_account","metric_date","hour"], name="sup_hour_acct_date_idx")),
        migrations.AddIndex(model_name="supportanalyticstagmetric", index=models.Index(fields=["support_account","metric_date"], name="sup_tag_metric_date_idx")),
        migrations.AddIndex(model_name="supportanalyticsexport", index=models.Index(fields=["support_account","status","created_at"], name="sup_export_acct_status_idx")),
    ]
