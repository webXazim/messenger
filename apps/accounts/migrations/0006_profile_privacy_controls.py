from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_authactiontoken_socialaccount_user_email_verified"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="is_discoverable",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="profile",
            name="nearby_discovery_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="profile",
            name="show_online_status",
            field=models.BooleanField(default=True),
        ),
    ]
