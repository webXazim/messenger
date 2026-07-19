from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("chat", "0018_message_live_conversation_index")]

    operations = [
        migrations.AddIndex(
            model_name="userblock",
            index=models.Index(
                fields=["blocked", "blocker"],
                name="chat_block_reverse_idx",
            ),
        ),
    ]
