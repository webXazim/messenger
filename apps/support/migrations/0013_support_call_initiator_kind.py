from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0012_support_message_receipts"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportcallsession",
            name="initiator_kind",
            field=models.CharField(
                choices=[
                    ("team", "Support team"),
                    ("visitor", "Website visitor"),
                ],
                default="team",
                max_length=16,
            ),
        ),
    ]
