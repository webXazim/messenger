from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("support", "0022_invitation_delivery_status"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS sup_data_site_stat_upd_idx "
                        "ON support_supportconversation (website_id, status, updated_at DESC, id)"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS sup_data_site_stat_upd_idx",
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="supportconversation",
                    index=models.Index(
                        fields=["website", "status", "-updated_at", "id"],
                        name="sup_data_site_stat_upd_idx",
                    ),
                ),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS sup_data_agent_stat_upd_idx "
                        "ON support_supportconversation (assigned_agent_id, status, updated_at DESC, id) "
                        "WHERE assigned_agent_id IS NOT NULL"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS sup_data_agent_stat_upd_idx",
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="supportconversation",
                    index=models.Index(
                        fields=["assigned_agent", "status", "-updated_at", "id"],
                        condition=Q(assigned_agent__isnull=False),
                        name="sup_data_agent_stat_upd_idx",
                    ),
                ),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS sup_data_upload_conv_idx "
                        "ON support_supportpendingupload (support_conversation_id, source, created_at DESC) "
                        "WHERE support_conversation_id IS NOT NULL"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS sup_data_upload_conv_idx",
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="supportpendingupload",
                    index=models.Index(
                        fields=["support_conversation", "source", "-created_at"],
                        condition=Q(support_conversation__isnull=False),
                        name="sup_data_upload_conv_idx",
                    ),
                ),
            ],
        ),
    ]
