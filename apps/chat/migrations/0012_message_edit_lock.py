from django.db import migrations, models
from django.utils import timezone


def backfill_message_edit_locks(apps, schema_editor):
    Message = apps.get_model("chat", "Message")
    MessageReaction = apps.get_model("chat", "MessageReaction")
    locked_at = timezone.now()

    reaction_ids = MessageReaction.objects.values_list("message_id", flat=True).distinct()
    Message.objects.filter(id__in=reaction_ids, edit_locked_at__isnull=True).update(
        edit_locked_at=locked_at,
        edit_locked_reason="message_has_reactions",
    )
    reply_ids = Message.objects.exclude(reply_to_id=None).values_list("reply_to_id", flat=True).distinct()
    Message.objects.filter(id__in=reply_ids, edit_locked_at__isnull=True).update(
        edit_locked_at=locked_at,
        edit_locked_reason="message_has_replies",
    )
    forward_ids = Message.objects.exclude(forwarded_from_id=None).values_list("forwarded_from_id", flat=True).distinct()
    Message.objects.filter(id__in=forward_ids, edit_locked_at__isnull=True).update(
        edit_locked_at=locked_at,
        edit_locked_reason="message_was_forwarded",
    )


class Migration(migrations.Migration):
    dependencies = [("chat", "0011_conversation_slug")]

    operations = [
        migrations.AddField(
            model_name="message",
            name="edit_locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="message",
            name="edit_locked_reason",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.RunPython(backfill_message_edit_locks, migrations.RunPython.noop),
    ]
