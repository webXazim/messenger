from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("common", "0003_realtimeoutboxevent_delivery_claim")]

    operations = [
        migrations.AlterField(
            model_name="realtimeoutboxevent",
            name="delivery_target",
            field=models.CharField(db_index=True, default="redis_stream", max_length=24),
        ),
    ]
