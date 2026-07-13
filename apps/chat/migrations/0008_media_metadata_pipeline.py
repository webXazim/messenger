from django.db import migrations, models

import apps.chat.storage


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0007_conversationdraft"),
    ]

    operations = [
        migrations.AddField(
            model_name="messageattachment",
            name="media_kind",
            field=models.CharField(
                choices=[("image", "Image"), ("video", "Video"), ("audio", "Audio"), ("file", "File")],
                default="file",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="messageattachment",
            name="rotation",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="messageattachment",
            name="thumbnail",
            field=models.ImageField(
                blank=True,
                null=True,
                storage=apps.chat.storage.attachment_storage_factory,
                upload_to="chat/attachments/thumbnails/%Y/%m/",
            ),
        ),
        migrations.AddField(
            model_name="pendingupload",
            name="duration_seconds",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="pendingupload",
            name="height",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pendingupload",
            name="media_kind",
            field=models.CharField(
                choices=[("image", "Image"), ("video", "Video"), ("audio", "Audio"), ("file", "File")],
                default="file",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="pendingupload",
            name="rotation",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pendingupload",
            name="thumbnail",
            field=models.ImageField(
                blank=True,
                null=True,
                storage=apps.chat.storage.pending_upload_storage_factory,
                upload_to="chat/pending_thumbnails/%Y/%m/",
            ),
        ),
        migrations.AddField(
            model_name="pendingupload",
            name="width",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
