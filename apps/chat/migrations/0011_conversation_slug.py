from django.conf import settings
from django.db import migrations, models
from django.utils.text import slugify


def populate_group_slugs(apps, schema_editor):
    Conversation = apps.get_model("chat", "Conversation")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    reserved = {str(value).lower() for value in User.objects.values_list("username", flat=True) if value}
    used = set(reserved)
    for conversation in Conversation.objects.filter(type="group").order_by("created_at", "id"):
        base = slugify(conversation.title or "", allow_unicode=True)[:100] or f"group-{str(conversation.id)[:8]}"
        candidate = base
        suffix = 2
        while candidate.lower() in used:
            tail = f"-{suffix}"
            candidate = f"{base[:120 - len(tail)]}{tail}"
            suffix += 1
        conversation.slug = candidate
        conversation.save(update_fields=["slug"])
        used.add(candidate.lower())


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0010_messageattachment_view_once"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="slug",
            field=models.SlugField(allow_unicode=True, blank=True, max_length=120, null=True, unique=True),
        ),
        migrations.RunPython(populate_group_slugs, migrations.RunPython.noop),
    ]
