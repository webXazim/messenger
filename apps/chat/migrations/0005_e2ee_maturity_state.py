from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0004_messageattachment_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="e2ee_key_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="conversation",
            name="e2ee_last_key_rotation_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conversation",
            name="e2ee_last_security_event_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conversation",
            name="e2ee_rekey_required",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="usere2eedevicekey",
            name="fingerprint",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="usere2eedevicekey",
            name="label",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="usere2eedevicekey",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="usere2eedevicekey",
            index=models.Index(fields=["user", "fingerprint"], name="chat_usere2_user_id_df26fc_idx"),
        ),
    ]
