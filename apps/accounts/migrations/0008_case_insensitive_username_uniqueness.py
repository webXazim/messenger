from django.db import migrations, models
from django.db.models.functions import Lower


def resolve_case_insensitive_collisions(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    used = set()
    users = User.objects.all().order_by("date_joined", "id")
    for user in users.iterator():
        original = (user.username or "user").strip()[:150] or "user"
        candidate = original
        normalized = candidate.lower()
        if normalized in used:
            suffix = f"_{str(user.id).replace('-', '')[:8]}"
            candidate = f"{original[:150 - len(suffix)]}{suffix}"
            normalized = candidate.lower()
            counter = 2
            while normalized in used:
                extra = f"_{counter}"
                candidate = f"{original[:150 - len(suffix) - len(extra)]}{suffix}{extra}"
                normalized = candidate.lower()
                counter += 1
        used.add(normalized)
        if candidate != user.username:
            User.objects.filter(pk=user.pk).update(username=candidate)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_alter_user_managers_and_more"),
    ]

    operations = [
        migrations.RunPython(resolve_case_insensitive_collisions, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(Lower("username"), name="uniq_user_username_ci"),
        ),
    ]
