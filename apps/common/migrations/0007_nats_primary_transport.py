from django.db import migrations, models


def retarget(apps, schema_editor):
    Event = apps.get_model("common", "RealtimeOutboxEvent")
    Event.objects.filter(delivery_target="redis_stream", status__in=["pending", "failed"]).update(delivery_target="nats_jetstream")


class Migration(migrations.Migration):
    dependencies = [("common", "0006_realtimeoutboxevent_nats_shadow")]
    operations = [
        migrations.AlterField(model_name="realtimeoutboxevent", name="delivery_target", field=models.CharField(db_index=True, default="nats_jetstream", max_length=24)),
        migrations.RunPython(retarget, migrations.RunPython.noop),
    ]
