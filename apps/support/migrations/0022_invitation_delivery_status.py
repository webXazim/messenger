from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0021_sync_lifecycle_model_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportagentinvitation",
            name="email_delivery_status",
            field=models.CharField(
                choices=[("queued", "Queued"), ("sent", "Sent"), ("failed", "Failed")],
                db_index=True,
                default="queued",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="supportagentinvitation",
            name="email_delivery_error",
            field=models.CharField(blank=True, max_length=1000),
        ),
        migrations.AddField(
            model_name="supportagentinvitation",
            name="email_delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
