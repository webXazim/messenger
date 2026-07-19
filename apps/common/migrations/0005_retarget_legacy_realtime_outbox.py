from django.db import migrations


def retarget_legacy_rows(apps, schema_editor):
    RealtimeOutboxEvent = apps.get_model("common", "RealtimeOutboxEvent")
    RealtimeOutboxEvent.objects.filter(delivery_target="channels").update(
        delivery_target="redis_stream"
    )


class Migration(migrations.Migration):
    dependencies = [("common", "0004_realtimeoutboxevent_axum_default")]

    operations = [migrations.RunPython(retarget_legacy_rows, migrations.RunPython.noop)]
