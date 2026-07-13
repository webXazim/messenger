from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_usere2eedevicekey"),
    ]

    operations = [
        migrations.AddField(
            model_name="messageattachment",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
