from django.db import migrations, models
from django.db.models import Count, Q


def clear_duplicate_client_temp_ids(apps, schema_editor):
    Message = apps.get_model("chat", "Message")
    duplicates = (
        Message.objects.exclude(client_temp_id="")
        .exclude(sender_id=None)
        .values("conversation_id", "sender_id", "client_temp_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for duplicate in duplicates.iterator():
        duplicate_ids = list(
            Message.objects.filter(
                conversation_id=duplicate["conversation_id"],
                sender_id=duplicate["sender_id"],
                client_temp_id=duplicate["client_temp_id"],
            )
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        if len(duplicate_ids) > 1:
            Message.objects.filter(id__in=duplicate_ids[1:]).update(client_temp_id="")


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0015_pendingupload_purpose_alter_messageattachment_file_and_more"),
    ]

    operations = [
        migrations.RunPython(clear_duplicate_client_temp_ids, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="message",
            constraint=models.UniqueConstraint(
                condition=Q(client_temp_id__gt="", sender__isnull=False),
                fields=("conversation", "sender", "client_temp_id"),
                name="uniq_msg_conv_sender_client_tmp",
            ),
        ),
    ]
