from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="realtimeoutboxevent",
            name="published_transport",
            field=models.CharField(blank=True, max_length=24),
        ),
        migrations.AddField(
            model_name="realtimeoutboxevent",
            name="stream_entry_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
