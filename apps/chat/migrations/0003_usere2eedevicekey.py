from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0002_single_reaction_per_user"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserE2EEDeviceKey",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("device_id", models.CharField(max_length=128)),
                ("key_id", models.CharField(max_length=256, unique=True)),
                ("algorithm", models.CharField(max_length=80)),
                ("public_key_jwk", models.JSONField(default=dict)),
                ("is_active", models.BooleanField(default=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="e2ee_device_keys", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="usere2eedevicekey",
            constraint=models.UniqueConstraint(fields=("user", "device_id", "key_id"), name="uniq_user_device_e2ee_key"),
        ),
        migrations.AddIndex(
            model_name="usere2eedevicekey",
            index=models.Index(fields=["user", "is_active", "updated_at"], name="chat_useree_user_id_74e1cc_idx"),
        ),
        migrations.AddIndex(
            model_name="usere2eedevicekey",
            index=models.Index(fields=["device_id", "is_active"], name="chat_useree_device__fe9c6f_idx"),
        ),
    ]
