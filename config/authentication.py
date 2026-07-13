from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.accounts.models import UserSession


class CentralJWTAuthentication(JWTAuthentication):
    """Validate auth_payment JWTs and map them to local shadow users."""

    def get_validated_token(self, raw_token):
        jwt_key_configured = bool(
            settings.AUTH_PAYMENT_JWT_PUBLIC_KEY
            if str(settings.AUTH_PAYMENT_JWT_ALGORITHM).startswith(("RS", "ES"))
            else settings.AUTH_PAYMENT_JWT_SIGNING_KEY
        )
        if not jwt_key_configured:
            raise AuthenticationFailed(
                "Central auth is not configured for this service.",
                code="central_auth_not_configured",
            )
        return super().get_validated_token(raw_token)

    def _unique_username(self, User, username, email):
        base = (username or slugify(email.split("@", 1)[0]) or "central-user")[:140]
        candidate = base
        suffix = 1
        while User.objects.filter(username=candidate).exclude(email=email).exists():
            suffix += 1
            candidate = f"{base[:140 - len(str(suffix)) - 1]}-{suffix}"
        return candidate[:150]

    def get_user(self, validated_token):
        if not settings.CENTRAL_AUTH_ENABLED:
            user = super().get_user(validated_token)
            self._enforce_local_session(validated_token, user)
            return user

        User = get_user_model()
        email = str(validated_token.get("email") or "").strip().lower()
        central_user_id = str(validated_token.get("user_id") or "").strip()
        if not email:
            email = f"{central_user_id or 'unknown'}@central-auth.invalid"

        username = str(validated_token.get("username") or "").strip()
        if not username:
            base = slugify(email.split("@", 1)[0]) or "central-user"
            username = f"{base}-{central_user_id[:8]}" if central_user_id else base
        username = self._unique_username(User, username[:150], email)

        defaults = {
            "username": username,
            "email_verified": bool(validated_token.get("email_verified", False)),
            "is_staff": bool(validated_token.get("is_staff", False)),
            "is_superuser": bool(validated_token.get("is_superuser", False)),
            "is_active": True,
            "last_seen_at": timezone.now(),
        }
        user, created = User.objects.get_or_create(email=email, defaults=defaults)
        changed = False
        for field, value in defaults.items():
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        elif changed:
            user.save(update_fields=[*defaults.keys()])
        display_name = str(validated_token.get("display_name") or "").strip()
        if display_name and hasattr(user, "profile") and user.profile.display_name != display_name:
            user.profile.display_name = display_name
            user.profile.save(update_fields=["display_name", "updated_at"])
        return user

    def _enforce_local_session(self, validated_token, user):
        session_id = str(validated_token.get("session_id") or "").strip()
        if not session_id:
            raise AuthenticationFailed("Session metadata is missing.", code="session_missing")
        session = UserSession.objects.filter(id=session_id, user=user).only(
            "revoked_at", "expires_at"
        ).first()
        if (
            not session
            or session.revoked_at is not None
            or session.expires_at <= timezone.now()
            or not user.is_active
        ):
            raise AuthenticationFailed("Session expired. Please sign in again.", code="session_revoked")
