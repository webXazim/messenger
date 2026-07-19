from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0002_realtimeoutboxevent_stream_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="realtimeoutboxevent",
            name="delivery_target",
            field=models.CharField(db_index=True, default="channels", max_length=24),
        ),
        migrations.AlterField(
            model_name="realtimeoutboxevent",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("published", "Published"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
    ]
