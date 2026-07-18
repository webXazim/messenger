from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0015_pendingupload_purpose_alter_messageattachment_file_and_more"),
        ("support", "0011_supportwidgetsettings_allow_audio_calls_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportconversation",
            name="visitor_last_delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="visitor_last_delivered_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="chat.message",
            ),
        ),
        migrations.AddField(
            model_name="supportconversationreadstate",
            name="last_delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportconversationreadstate",
            name="last_delivered_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="chat.message",
            ),
        ),
    ]
