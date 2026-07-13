import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_usersession"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="AuthActionToken",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("purpose", models.CharField(choices=[("email_verify", "Email verify"), ("password_reset", "Password reset")], max_length=32)),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="auth_action_tokens", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="SocialAccount",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.CharField(choices=[("google", "Google"), ("apple", "Apple")], max_length=24)),
                ("provider_user_id", models.CharField(max_length=255)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("last_login_at", models.DateTimeField(auto_now=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="social_accounts", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="authactiontoken",
            index=models.Index(fields=["user", "purpose", "expires_at"], name="accounts_au_user_id_2ceb3f_idx"),
        ),
        migrations.AddIndex(
            model_name="authactiontoken",
            index=models.Index(fields=["purpose", "used_at"], name="accounts_au_purpose_166f6f_idx"),
        ),
        migrations.AddConstraint(
            model_name="socialaccount",
            constraint=models.UniqueConstraint(fields=("provider", "provider_user_id"), name="uniq_social_provider_user"),
        ),
        migrations.AddIndex(
            model_name="socialaccount",
            index=models.Index(fields=["user", "provider"], name="accounts_so_user_id_a75664_idx"),
        ),
        migrations.AddIndex(
            model_name="socialaccount",
            index=models.Index(fields=["provider", "email"], name="accounts_so_provider_9ee6ef_idx"),
        ),
    ]
