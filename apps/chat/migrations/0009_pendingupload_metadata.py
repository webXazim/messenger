from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0008_media_metadata_pipeline"),
    ]

    operations = [
        migrations.AddField(
            model_name="pendingupload",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
