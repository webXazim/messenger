from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("common", "0005_retarget_legacy_realtime_outbox")]
    operations = [
        migrations.AddField(model_name="realtimeoutboxevent", name="nats_shadow_status", field=models.CharField(blank=True, db_index=True, max_length=16)),
        migrations.AddField(model_name="realtimeoutboxevent", name="nats_shadow_attempts", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="realtimeoutboxevent", name="nats_shadow_sequence", field=models.BigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="realtimeoutboxevent", name="nats_shadow_published_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="realtimeoutboxevent", name="nats_shadow_last_error", field=models.TextField(blank=True)),
    ]
