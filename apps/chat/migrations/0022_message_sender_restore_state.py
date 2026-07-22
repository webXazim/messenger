from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("chat", "0021_hot_read_indexes")]

    operations = [
        migrations.AddField(
            model_name="message",
            name="deleted_text_backup",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="message",
            name="deletion_source",
            field=models.CharField(
                blank=True,
                choices=[("sender", "Sender"), ("moderation", "Moderation")],
                max_length=16,
            ),
        ),
    ]
