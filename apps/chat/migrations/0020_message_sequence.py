from django.db import migrations, models
from django.db.models import Q


def backfill_sequences(apps, schema_editor):
    Conversation = apps.get_model("chat", "Conversation")
    Message = apps.get_model("chat", "Message")
    for conversation in Conversation.objects.all().only("id").iterator(chunk_size=200):
        pending = []
        sequence = 0
        queryset = Message.objects.filter(conversation_id=conversation.id).order_by("created_at", "id").only("id", "sequence")
        for message in queryset.iterator(chunk_size=1000):
            sequence += 1
            message.sequence = sequence
            pending.append(message)
            if len(pending) >= 1000:
                Message.objects.bulk_update(pending, ["sequence"], batch_size=1000)
                pending.clear()
        if pending:
            Message.objects.bulk_update(pending, ["sequence"], batch_size=1000)
        Conversation.objects.filter(pk=conversation.id).update(next_message_sequence=sequence)


def reverse_sequences(apps, schema_editor):
    Conversation = apps.get_model("chat", "Conversation")
    Message = apps.get_model("chat", "Message")
    Message.objects.update(sequence=None)
    Conversation.objects.update(next_message_sequence=0)


class Migration(migrations.Migration):
    dependencies = [("chat", "0019_userblock_reverse_index")]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="next_message_sequence",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="message",
            name="sequence",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_sequences, reverse_sequences),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(fields=["conversation", "-sequence"], name="chat_msg_conv_seq_idx"),
        ),
        migrations.AddConstraint(
            model_name="message",
            constraint=models.UniqueConstraint(
                fields=("conversation", "sequence"),
                condition=Q(sequence__isnull=False),
                name="uniq_msg_conversation_sequence",
            ),
        ),
    ]
