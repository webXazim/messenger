from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0020_message_sequence"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="conversationparticipant",
            index=models.Index(
                condition=models.Q(left_at__isnull=True, banned_at__isnull=True),
                fields=["user", "conversation"],
                name="chat_part_active_user_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["conversation", "-created_at", "-id"],
                name="chat_msg_conv_time_id_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="messageattachment",
            index=models.Index(
                fields=["message", "-created_at"],
                name="chat_attach_msg_time_idx",
            ),
        ),
    ]
