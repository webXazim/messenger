from django.db import migrations, models


def clear_external_duplicates(apps, schema_editor):
    Message = apps.get_model("chat", "Message")
    duplicates = (
        Message.objects.filter(sender__isnull=True)
        .exclude(client_temp_id="")
        .values("conversation_id", "client_temp_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    for duplicate in duplicates.iterator():
        ids = list(
            Message.objects.filter(
                sender__isnull=True,
                conversation_id=duplicate["conversation_id"],
                client_temp_id=duplicate["client_temp_id"],
            )
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        if len(ids) > 1:
            Message.objects.filter(id__in=ids[1:]).update(client_temp_id="")


class Migration(migrations.Migration):
    dependencies = [("chat", "0016_message_client_temp_id_uniqueness")]

    operations = [
        migrations.RunPython(clear_external_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="message",
            constraint=models.UniqueConstraint(
                fields=("conversation", "client_temp_id"),
                condition=models.Q(client_temp_id__gt="", sender__isnull=True),
                name="uniq_msg_conv_external_client_tmp",
            ),
        ),
    ]
