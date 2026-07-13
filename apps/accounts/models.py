import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.common.models import BaseUUIDModel


class MessengerUserManager(UserManager):
    def _build_username(self, username, email):
        username = (username or "").strip()
        if username:
            return username
        email = (email or "").strip().lower()
        if email and "@" in email:
            base = email.split("@", 1)[0] or "user"
        else:
            base = "user"
        candidate = base[:150] or "user"
        if not self.model._default_manager.filter(username=candidate).exists():
            return candidate
        suffix = uuid.uuid4().hex[:8]
        trimmed = candidate[: max(1, 150 - len(suffix) - 1)]
        return f"{trimmed}_{suffix}"

    def _build_email(self, email, username):
        email = (email or "").strip().lower()
        if email:
            return email
        username = (username or "").strip().lower() or uuid.uuid4().hex[:12]
        return f"{username}@example.invalid"

    def create_user(self, username=None, email=None, password=None, **extra_fields):
        username = self._build_username(username, email)
        email = self._build_email(email, username)
        return super().create_user(username=username, email=email, password=password, **extra_fields)

    def create_superuser(self, username=None, email=None, password=None, **extra_fields):
        username = self._build_username(username, email)
        email = self._build_email(email, username)
        return super().create_superuser(username=username, email=email, password=password, **extra_fields)


class User(AbstractUser):
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    REQUIRED_FIELDS = ["email"]
    objects = MessengerUserManager()


class Profile(BaseUUIDModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=150, blank=True)
    avatar = models.ImageField(upload_to="profiles/avatars/", blank=True, null=True)
    bio = models.TextField(blank=True)
    status_message = models.CharField(max_length=255, blank=True)
    is_discoverable = models.BooleanField(default=True)
    show_online_status = models.BooleanField(default=True)
    nearby_discovery_enabled = models.BooleanField(default=False)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.display_name or self.user.username


class FriendRequest(BaseUUIDModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        CANCELED = "canceled", "Canceled"

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="friend_requests_sent")
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="friend_requests_received")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    message = models.CharField(max_length=255, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["sender", "receiver"], name="uniq_friend_request_sender_receiver"),
            models.CheckConstraint(condition=~Q(sender=models.F("receiver")), name="friend_request_sender_not_receiver"),
        ]
        indexes = [
            models.Index(fields=["sender", "status"]),
            models.Index(fields=["receiver", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def clean(self):
        if self.sender_id and self.receiver_id and self.sender_id == self.receiver_id:
            raise ValidationError("You cannot send a friend request to yourself.")

    def __str__(self):
        return f"FriendRequest<{self.sender_id}->{self.receiver_id}:{self.status}>"


class UserSession(BaseUUIDModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessions")
    refresh_jti = models.CharField(max_length=64, unique=True, db_index=True)
    device_id = models.CharField(max_length=128, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at", "-created_at"]
        indexes = [
            models.Index(fields=["user", "revoked_at"]),
            models.Index(fields=["user", "expires_at"]),
        ]

    def __str__(self):
        return f"UserSession<{self.user_id}:{self.refresh_jti}>"


class SocialAccount(BaseUUIDModel):
    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        APPLE = "apple", "Apple"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="social_accounts")
    provider = models.CharField(max_length=24, choices=Provider.choices)
    provider_user_id = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    last_login_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_user_id"], name="uniq_social_provider_user"),
        ]
        indexes = [
            models.Index(fields=["user", "provider"]),
            models.Index(fields=["provider", "email"]),
        ]

    def __str__(self):
        return f"SocialAccount<{self.provider}:{self.provider_user_id}>"


class AuthActionToken(BaseUUIDModel):
    class Purpose(models.TextChoices):
        EMAIL_VERIFY = "email_verify", "Email verify"
        PASSWORD_RESET = "password_reset", "Password reset"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="auth_action_tokens")
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    email = models.EmailField(blank=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "purpose", "expires_at"]),
            models.Index(fields=["purpose", "used_at"]),
        ]

    def __str__(self):
        return f"AuthActionToken<{self.user_id}:{self.purpose}>"
