from django.db import migrations, models


def keep_one_reaction_per_user(apps, schema_editor):
    message_reaction = apps.get_model("chat", "MessageReaction")
    seen = set()
    duplicate_ids = []
    reactions = message_reaction.objects.order_by("message_id", "user_id", "-created_at", "-id")
    for reaction in reactions.iterator():
        key = (reaction.message_id, reaction.user_id)
        if key in seen:
            duplicate_ids.append(reaction.id)
        else:
            seen.add(key)
    if duplicate_ids:
        message_reaction.objects.filter(id__in=duplicate_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="messagereaction",
            name="uniq_message_user_emoji_reaction",
        ),
        migrations.RunPython(keep_one_reaction_per_user, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="messagereaction",
            constraint=models.UniqueConstraint(fields=("message", "user"), name="uniq_message_user_reaction"),
        ),
    ]
