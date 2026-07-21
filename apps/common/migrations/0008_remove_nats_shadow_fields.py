from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("common", "0007_nats_primary_transport")]

    operations = [
        migrations.RemoveField(model_name="realtimeoutboxevent", name="nats_shadow_status"),
        migrations.RemoveField(model_name="realtimeoutboxevent", name="nats_shadow_attempts"),
        migrations.RemoveField(model_name="realtimeoutboxevent", name="nats_shadow_sequence"),
        migrations.RemoveField(model_name="realtimeoutboxevent", name="nats_shadow_published_at"),
        migrations.RemoveField(model_name="realtimeoutboxevent", name="nats_shadow_last_error"),
    ]
