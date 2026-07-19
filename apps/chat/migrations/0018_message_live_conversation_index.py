from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("chat", "0017_message_external_client_temp_id_uniqueness")]

    operations = [
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["conversation", "created_at", "id"],
                condition=models.Q(is_deleted=False),
                name="chat_msg_conv_live_time_idx",
            ),
        ),
    ]
