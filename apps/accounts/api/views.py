from decimal import Decimal
from datetime import timedelta, timezone as datetime_timezone
import hashlib
import hmac
from math import asin, cos, radians, sin, sqrt
import re
import secrets
from urllib.request import urlopen
import json as jsonlib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import generics, permissions, serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.pagination import CursorPagination
from rest_framework.throttling import ScopedRateThrottle
import jwt
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts.models import AuthActionToken, FriendRequest, SocialAccount, UserSession
from .serializers import (
    AccountDeleteSerializer,
    AvatarUploadSerializer,
    EmailVerifyConfirmSerializer,
    EmailVerifyRequestSerializer,
    FriendRequestCreateSerializer,
    FriendRequestRespondSerializer,
    FriendRequestSerializer,
    MeSerializer,
    MeUpdateSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    SocialLoginSerializer,
    UserSessionSerializer,
    UserDiscoverySerializer,
)

User = get_user_model()


class FixedRateScopedThrottle(ScopedRateThrottle):
    rate = None

    def get_rate(self):
        if self.rate:
            return self.rate
        return super().get_rate()


class RegisterThrottle(FixedRateScopedThrottle):
    scope = "auth_register"
    rate = "10/hour"


class LoginThrottle(FixedRateScopedThrottle):
    scope = "auth_login"
    rate = "30/hour"


class RefreshThrottle(FixedRateScopedThrottle):
    scope = "auth_refresh"
    rate = "120/hour"


class PasswordResetThrottle(FixedRateScopedThrottle):
    scope = "auth_password_reset"
    rate = "10/hour"


class EmailVerifyThrottle(FixedRateScopedThrottle):
    scope = "auth_email_verify"
    rate = "10/hour"


class SocialLoginThrottle(FixedRateScopedThrottle):
    scope = "auth_social_login"
    rate = "30/hour"


class AvatarWriteThrottle(FixedRateScopedThrottle):
    scope = "avatar_write"
    rate = "12/hour"


LOGIN_LOCKOUT_THRESHOLD = 8
LOGIN_LOCKOUT_TTL_SECONDS = 15 * 60
EMAIL_VERIFY_TOKEN_TTL_SECONDS = int(getattr(settings, "EMAIL_VERIFY_TOKEN_TTL_SECONDS", 24 * 60 * 60) or 24 * 60 * 60)
EMAIL_VERIFY_OTP_TTL_SECONDS = int(getattr(settings, "EMAIL_VERIFY_OTP_TTL_SECONDS", 10 * 60) or 10 * 60)
EMAIL_VERIFY_OTP_MAX_ATTEMPTS = int(getattr(settings, "EMAIL_VERIFY_OTP_MAX_ATTEMPTS", 5) or 5)
PASSWORD_RESET_TOKEN_TTL_SECONDS = int(getattr(settings, "PASSWORD_RESET_TOKEN_TTL_SECONDS", 60 * 60) or 60 * 60)
OIDC_DISCOVERY_CACHE_TTL_SECONDS = int(getattr(settings, "OIDC_DISCOVERY_CACHE_TTL_SECONDS", 60 * 60) or 60 * 60)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _sanitize_plain_text(value, *, max_length=None, multiline=False):
    text = CONTROL_CHAR_RE.sub("", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        text = "\n".join(line.strip() for line in text.split("\n")).strip()
    else:
        text = " ".join(text.split())
    if max_length is not None:
        text = text[:max_length]
    return text


def _login_lockout_key(username, request):
    username = (username or "").strip().lower()[:150] or "unknown"
    return f"auth:login-lockout:{username}:{_client_ip(request)}"


def _request_user_agent(request):
    return (request.META.get("HTTP_USER_AGENT", "") or "")[:512]


def _request_device_id(request):
    return (request.META.get("HTTP_X_DEVICE_ID", "") or request.headers.get("X-Device-Id", "") or "")[:128]


def _normalize_email(value):
    return User.objects.normalize_email(_sanitize_plain_text(value, max_length=254)).lower()


def _auth_ip_failure_key(request, scope):
    return f"auth:ip-fail:{scope}:{_client_ip(request)}"


def _auth_ip_block_key(request, scope):
    return f"auth:ip-block:{scope}:{_client_ip(request)}"


def _ensure_ip_not_blocked(request, scope):
    if not request:
        return
    if cache.get(_auth_ip_block_key(request, scope)):
        raise AuthenticationFailed("Too many failed attempts from this IP. Try again later.")


def _record_ip_failure(request, scope):
    if not request:
        return
    fail_key = _auth_ip_failure_key(request, scope)
    block_key = _auth_ip_block_key(request, scope)
    failure_window = int(getattr(settings, "AUTH_IP_FAILURE_WINDOW_SECONDS", 900) or 900)
    threshold = int(getattr(settings, "AUTH_IP_FAILURE_THRESHOLD", 20) or 20)
    block_ttl = int(getattr(settings, "AUTH_IP_BLOCK_TTL_SECONDS", 1800) or 1800)
    try:
        failures = cache.incr(fail_key)
        cache.touch(fail_key, failure_window)
    except ValueError:
        failures = 1
        cache.set(fail_key, failures, timeout=failure_window)
    if failures >= threshold:
        cache.set(block_key, True, timeout=block_ttl)


def _clear_ip_failures(request, scope):
    if not request:
        return
    cache.delete(_auth_ip_failure_key(request, scope))


def _session_expiry_from_refresh(refresh):
    exp = refresh.payload.get("exp")
    if exp:
        return timezone.datetime.fromtimestamp(exp, tz=datetime_timezone.utc)
    return timezone.now() + settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]


def _issue_session_tokens(*, user, request):
    refresh = LoginSerializer.get_token(user)
    session = _upsert_user_session(user=user, request=request, refresh=refresh)
    type(user).objects.filter(id=user.id).update(last_login=timezone.now())
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "session_id": str(session.id),
        "user": MeSerializer(type(user).objects.prefetch_related("social_accounts").select_related("profile").get(id=user.id)).data,
    }


def _upsert_user_session(*, user, request, refresh):
    session_id = refresh.payload.get("session_id")
    if not session_id:
        raise AuthenticationFailed("Session token is missing required metadata.")
    return UserSession.objects.update_or_create(
        id=session_id,
        defaults={
            "user": user,
            "refresh_jti": str(refresh.payload.get("jti", ""))[:64],
            "device_id": _request_device_id(request),
            "user_agent": _request_user_agent(request),
            "ip_address": _client_ip(request),
            "expires_at": _session_expiry_from_refresh(refresh),
            "revoked_at": None,
        },
    )[0]


def _require_active_session(refresh):
    session_id = refresh.payload.get("session_id")
    if not session_id:
        raise AuthenticationFailed("Session expired. Please sign in again.")
    session = UserSession.objects.filter(id=session_id).select_related("user").first()
    if (
        not session
        or session.revoked_at is not None
        or session.expires_at <= timezone.now()
        or not session.user.is_active
        or not secrets.compare_digest(session.refresh_jti, str(refresh.payload.get("jti", "")))
    ):
        raise AuthenticationFailed("Session expired. Please sign in again.")
    return session


def _revoke_user_sessions(user, *, exclude_session_id=None):
    qs = UserSession.objects.filter(user=user, revoked_at__isnull=True)
    if exclude_session_id:
        qs = qs.exclude(id=exclude_session_id)
    return qs.update(revoked_at=timezone.now())


def _token_hash(raw_token):
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def _frontend_base_url():
    return (
        getattr(settings, "FRONTEND_BASE_URL", "")
        or getattr(settings, "SITE_URL", "")
        or "http://localhost:5173"
    ).rstrip("/")


def _build_frontend_url(path, *, token):
    return f"{_frontend_base_url()}{path}?token={token}"


def _send_account_email(*, subject, body, recipients):
    if not recipients:
        return 0
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@localhost")
    return send_mail(subject, body, from_email, recipients, fail_silently=True)


def _create_auth_action_token(*, user, purpose, ttl_seconds, email="", metadata=None):
    raw_token = secrets.token_urlsafe(32)
    AuthActionToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=_token_hash(raw_token),
        email=email or user.email,
        expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
        metadata=metadata or {},
    )
    return raw_token


def _consume_auth_action_token(raw_token, *, purpose):
    token = (
        AuthActionToken.objects.select_related("user")
        .filter(
            token_hash=_token_hash(raw_token),
            purpose=purpose,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if not token:
        raise AuthenticationFailed("Token is invalid or expired.")
    token.used_at = timezone.now()
    token.save(update_fields=["used_at", "updated_at"])
    return token


def _email_otp_hash(user_id, code):
    return _token_hash(f"{user_id}:{code}")


def _send_email_verification_otp(user):
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = timezone.now()
    AuthActionToken.objects.filter(
        user=user,
        purpose=AuthActionToken.Purpose.EMAIL_VERIFY,
        used_at__isnull=True,
    ).update(used_at=now)
    AuthActionToken.objects.create(
        user=user,
        purpose=AuthActionToken.Purpose.EMAIL_VERIFY,
        token_hash=_email_otp_hash(user.id, code),
        email=user.email,
        expires_at=now + timedelta(seconds=EMAIL_VERIFY_OTP_TTL_SECONDS),
        metadata={"kind": "otp", "attempts": 0},
    )
    _send_account_email(
        subject="Your Crescentsphere verification code",
        body=(
            f"Your Crescentsphere verification code is: {code}\n\n"
            f"It expires in {max(1, EMAIL_VERIFY_OTP_TTL_SECONDS // 60)} minutes. "
            "If you did not create this account, you can ignore this email."
        ),
        recipients=[user.email],
    )


def _consume_email_verification_otp(*, email, code):
    user = User.objects.filter(email__iexact=_normalize_email(email), is_active=True).first()
    if not user:
        raise AuthenticationFailed("Code is invalid or expired.")
    with transaction.atomic():
        action_token = (
            AuthActionToken.objects.select_for_update()
            .filter(
                user=user,
                purpose=AuthActionToken.Purpose.EMAIL_VERIFY,
                used_at__isnull=True,
                expires_at__gt=timezone.now(),
                metadata__kind="otp",
            )
            .order_by("-created_at")
            .first()
        )
        if not action_token:
            raise AuthenticationFailed("Code is invalid or expired.")
        attempts = int((action_token.metadata or {}).get("attempts", 0))
        if not hmac.compare_digest(action_token.token_hash, _email_otp_hash(user.id, code)):
            attempts += 1
            action_token.metadata = {**(action_token.metadata or {}), "attempts": attempts}
            if attempts >= EMAIL_VERIFY_OTP_MAX_ATTEMPTS:
                action_token.used_at = timezone.now()
                action_token.save(update_fields=["metadata", "used_at", "updated_at"])
            else:
                action_token.save(update_fields=["metadata", "updated_at"])
            raise AuthenticationFailed("Code is invalid or expired.")
        action_token.used_at = timezone.now()
        action_token.save(update_fields=["used_at", "updated_at"])
        return action_token


def _mark_email_verified(user, *, email=None):
    user.email_verified = True
    user.email_verified_at = timezone.now()
    if email:
        user.email = _normalize_email(email)
    user.save(update_fields=["email_verified", "email_verified_at", "email"])


def _oidc_provider_settings(provider):
    defaults = {
        "google": {
            "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
            "issuers": ["https://accounts.google.com", "accounts.google.com"],
            "client_id": getattr(settings, "GOOGLE_OIDC_CLIENT_ID", ""),
        },
        "apple": {
            "discovery_url": "https://appleid.apple.com/.well-known/openid-configuration",
            "issuers": ["https://appleid.apple.com"],
            "client_id": getattr(settings, "APPLE_OIDC_CLIENT_ID", ""),
        },
    }
    return defaults.get(provider, {})


def _oidc_discovery_document(provider):
    cache_key = f"auth:oidc-discovery:{provider}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    provider_settings = _oidc_provider_settings(provider)
    discovery_url = provider_settings.get("discovery_url")
    if not discovery_url:
        raise AuthenticationFailed("Unsupported social provider.")
    with urlopen(discovery_url, timeout=5) as response:
        payload = jsonlib.loads(response.read().decode("utf-8"))
    cache.set(cache_key, payload, timeout=OIDC_DISCOVERY_CACHE_TTL_SECONDS)
    return payload


def _verify_social_id_token(provider, id_token):
    provider_settings = _oidc_provider_settings(provider)
    audience = provider_settings.get("client_id", "")
    if not audience:
        raise AuthenticationFailed(f"{provider.title()} social login is not configured.")
    discovery = _oidc_discovery_document(provider)
    jwks_uri = discovery.get("jwks_uri")
    issuer = discovery.get("issuer")
    if not jwks_uri or not issuer:
        raise AuthenticationFailed("Social provider metadata is incomplete.")
    signing_key = jwt.PyJWKClient(jwks_uri).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience=audience,
        issuer=issuer,
    )
    if claims.get("iss") not in (provider_settings.get("issuers") or [issuer]):
        raise AuthenticationFailed("Social token issuer is invalid.")
    email = _normalize_email(claims.get("email"))
    if not claims.get("sub"):
        raise AuthenticationFailed("Social token missing subject.")
    return {
        "provider_user_id": str(claims["sub"]),
        "email": email,
        "email_verified": bool(claims.get("email_verified", False) or provider == SocialAccount.Provider.APPLE),
        "first_name": (claims.get("given_name") or "").strip(),
        "last_name": (claims.get("family_name") or "").strip(),
        "display_name": (claims.get("name") or "").strip(),
        "picture": claims.get("picture") or "",
        "claims": claims,
    }


@transaction.atomic
def _get_or_create_social_user(*, provider, profile):
    account = SocialAccount.objects.select_related("user", "user__profile").filter(
        provider=provider,
        provider_user_id=profile["provider_user_id"],
    ).first()
    if account:
        account.email = profile["email"]
        account.metadata = {"claims": profile["claims"]}
        account.save(update_fields=["email", "metadata", "last_login_at", "updated_at"])
        user = account.user
        if profile["email_verified"] and not user.email_verified:
            _mark_email_verified(user, email=profile["email"] or user.email)
        return user, account, False

    user = None
    if profile["email"] and profile["email_verified"]:
        user = User.objects.filter(email__iexact=profile["email"]).select_related("profile").first()
    elif profile["email"] and User.objects.filter(email__iexact=profile["email"]).exists():
        raise AuthenticationFailed("This email already exists and cannot be linked from an unverified social identity.")
    if user is None:
        username_base = (profile["email"].split("@", 1)[0] if profile["email"] else provider).strip() or provider
        user = User.objects.create_user(
            username=username_base,
            email=profile["email"] or "",
            password=secrets.token_urlsafe(24),
            first_name=profile["first_name"],
            last_name=profile["last_name"],
        )
    if profile["email_verified"]:
        _mark_email_verified(user, email=profile["email"] or user.email)
    account = SocialAccount.objects.create(
        user=user,
        provider=provider,
        provider_user_id=profile["provider_user_id"],
        email=profile["email"],
        metadata={"claims": profile["claims"]},
    )
    if hasattr(user, "profile") and profile["display_name"]:
        user.profile.display_name = user.profile.display_name or profile["display_name"]
        user.profile.save(update_fields=["display_name", "updated_at"])
    return user, account, True


def _export_account_snapshot(user):
    profile = getattr(user, "profile", None)
    friend_requests = FriendRequest.objects.filter(Q(sender=user) | Q(receiver=user)).select_related("sender", "receiver")
    sessions = UserSession.objects.filter(user=user).order_by("-last_seen_at")
    return {
        "user": {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
            "is_active": user.is_active,
            "joined_at": user.date_joined.isoformat() if user.date_joined else None,
        },
        "profile": {
            "display_name": getattr(profile, "display_name", ""),
            "bio": getattr(profile, "bio", ""),
            "status_message": getattr(profile, "status_message", ""),
            "is_discoverable": bool(getattr(profile, "is_discoverable", True)),
            "show_online_status": bool(getattr(profile, "show_online_status", True)),
            "nearby_discovery_enabled": bool(getattr(profile, "nearby_discovery_enabled", False)),
            "latitude": str(getattr(profile, "latitude", "") or ""),
            "longitude": str(getattr(profile, "longitude", "") or ""),
            "location_updated_at": profile.location_updated_at.isoformat() if profile and profile.location_updated_at else None,
        },
        "friend_requests": [
            {
                "id": str(item.id),
                "sender_id": str(item.sender_id),
                "receiver_id": str(item.receiver_id),
                "status": item.status,
                "message": item.message,
                "created_at": item.created_at.isoformat(),
                "responded_at": item.responded_at.isoformat() if item.responded_at else None,
            }
            for item in friend_requests
        ],
        "sessions": UserSessionSerializer(sessions, many=True).data,
        "exported_at": timezone.now().isoformat(),
    }


@transaction.atomic
def _anonymize_account(user):
    from apps.chat.models import ConversationParticipant, PendingUpload, UserDevice

    deleted_slug = str(user.id).replace("-", "")[:24]
    user.username = f"deleted_{deleted_slug}"
    user.email = f"deleted+{deleted_slug}@example.invalid"
    user.first_name = ""
    user.last_name = ""
    user.last_seen_at = timezone.now()
    user.is_active = False
    user.set_unusable_password()
    user.save(update_fields=["username", "email", "first_name", "last_name", "last_seen_at", "is_active", "password"])
    if hasattr(user, "profile"):
        profile = user.profile
        if profile.avatar:
            profile.avatar.delete(save=False)
        profile.display_name = "Deleted User"
        profile.bio = ""
        profile.status_message = ""
        profile.avatar = None
        profile.is_discoverable = False
        profile.show_online_status = False
        profile.nearby_discovery_enabled = False
        profile.latitude = None
        profile.longitude = None
        profile.location_updated_at = None
        profile.save(update_fields=[
            "display_name",
            "bio",
            "status_message",
            "avatar",
            "is_discoverable",
            "show_online_status",
            "nearby_discovery_enabled",
            "latitude",
            "longitude",
            "location_updated_at",
        ])
    UserSession.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
    AuthActionToken.objects.filter(user=user).delete()
    SocialAccount.objects.filter(user=user).delete()
    FriendRequest.objects.filter(Q(sender=user) | Q(receiver=user)).update(message="")
    UserDevice.objects.filter(user=user).update(is_active=False, updated_at=timezone.now())
    PendingUpload.objects.filter(user=user, status=PendingUpload.UploadStatus.PENDING).update(
        status=PendingUpload.UploadStatus.REJECTED,
        scan_status=PendingUpload.ScanStatus.FAILED,
        scan_notes="Rejected because the account was deleted.",
        updated_at=timezone.now(),
    )
    ConversationParticipant.objects.filter(user=user, left_at__isnull=True).update(
        left_at=timezone.now(),
        is_muted=True,
        is_archived=True,
    )


class LoginSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["session_id"] = str(UserSession().id)
        return token

    def validate(self, attrs):
        request = self.context.get("request")
        _ensure_ip_not_blocked(request, "login")
        username = attrs.get(self.username_field, "")
        if username and "@" in username:
            matched_user = User.objects.filter(email__iexact=_normalize_email(username)).first()
            if matched_user:
                attrs[self.username_field] = matched_user.get_username()
                username = attrs[self.username_field]
        lockout_key = _login_lockout_key(username, request) if request is not None else None
        attempts = int(cache.get(lockout_key) or 0) if lockout_key else 0
        if attempts >= LOGIN_LOCKOUT_THRESHOLD:
            raise AuthenticationFailed("Too many failed login attempts. Try again later.")
        try:
            data = super().validate(attrs)
        except Exception:
            _record_ip_failure(request, "login")
            if lockout_key:
                try:
                    cache.incr(lockout_key)
                    cache.touch(lockout_key, LOGIN_LOCKOUT_TTL_SECONDS)
                except ValueError:
                    cache.set(lockout_key, 1, timeout=LOGIN_LOCKOUT_TTL_SECONDS)
            raise
        if lockout_key:
            cache.delete(lockout_key)
        _clear_ip_failures(request, "login")
        if getattr(settings, "AUTH_REQUIRE_EMAIL_VERIFICATION", False) and not getattr(self.user, "email_verified", False):
            raise AuthenticationFailed("Email verification is required before login.")
        refresh = RefreshToken(data["refresh"])
        session = _upsert_user_session(user=self.user, request=request, refresh=refresh)
        type(self.user).objects.filter(id=self.user.id).update(last_login=timezone.now())
        data["session_id"] = str(session.id)
        return data


class RefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        try:
            refresh = RefreshToken(attrs["refresh"])
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc
        session = _require_active_session(refresh)
        data = super().validate(attrs)
        rotated_refresh = RefreshToken(data["refresh"])
        if str(rotated_refresh.payload.get("session_id") or "") != str(session.id):
            session.revoked_at = timezone.now()
            session.save(update_fields=["revoked_at", "updated_at"])
            raise AuthenticationFailed("Session rotation failed. Please sign in again.")
        session.refresh_jti = str(rotated_refresh.payload.get("jti", ""))[:64]
        session.last_seen_at = timezone.now()
        session.expires_at = _session_expiry_from_refresh(rotated_refresh)
        session.save(update_fields=["refresh_jti", "last_seen_at", "expires_at", "updated_at"])
        data["session_id"] = str(session.id)
        return data


def _user_discovery_queryset(actor):
    # Exclude self and blocked users.
    from apps.chat.models import UserBlock

    blocked_user_ids = UserBlock.objects.filter(blocker=actor).values_list("blocked_id", flat=True)
    blocker_user_ids = UserBlock.objects.filter(blocked=actor).values_list("blocker_id", flat=True)
    base = (
        User.objects.filter(is_active=True, profile__is_discoverable=True)
        .exclude(id=actor.id)
        .exclude(id__in=blocked_user_ids)
        .exclude(id__in=blocker_user_ids)
        .select_related("profile")
    )
    return base


def _latest_relation_map(actor, user_ids):
    if not user_ids:
        return {}
    rows = (
        FriendRequest.objects.filter(
            Q(sender=actor, receiver_id__in=user_ids) | Q(sender_id__in=user_ids, receiver=actor)
        )
        .select_related("sender", "sender__profile", "receiver", "receiver__profile")
        .order_by("sender_id", "receiver_id", "-created_at")
    )
    mapping = {}
    for row in rows:
        other_id = row.receiver_id if row.sender_id == actor.id else row.sender_id
        mapping.setdefault(other_id, row)
    return mapping


def _attach_latest_relation(objects, actor):
    ids = [obj.id for obj in objects]
    relation_map = _latest_relation_map(actor, ids)
    for obj in objects:
        obj.friend_request_relation = relation_map.get(obj.id)
    return objects


def _distance_km(lat1, lon1, lat2, lon2):
    lat1 = radians(float(lat1))
    lon1 = radians(float(lon1))
    lat2 = radians(float(lat2))
    lon2 = radians(float(lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(min(1, sqrt(a)))
    return 6371.0 * c


def _validate_coordinate_range(latitude, longitude):
    if latitude < Decimal("-90") or latitude > Decimal("90"):
        raise ValueError("latitude must be between -90 and 90.")
    if longitude < Decimal("-180") or longitude > Decimal("180"):
        raise ValueError("longitude must be between -180 and 180.")


def _truthy_query_param(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterThrottle]

    def create(self, request, *args, **kwargs):
        _ensure_ip_not_blocked(request, "register")
        try:
            response = super().create(request, *args, **kwargs)
        except Exception:
            _record_ip_failure(request, "register")
            raise
        _clear_ip_failures(request, "register")
        user = User.objects.get(id=response.data["id"])
        if user.email:
            _send_email_verification_otp(user)
        response.data["email_verification_required"] = bool(getattr(settings, "AUTH_REQUIRE_EMAIL_VERIFICATION", False))
        return response


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        serializer = MeUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        request.user.refresh_from_db()
        return Response(MeSerializer(request.user, context={"request": request}).data)


class AvatarView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [AvatarWriteThrottle]
    serializer_class = AvatarUploadSerializer

    def put(self, request):
        serializer = AvatarUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save_avatar(request.user.profile)
        request.user.refresh_from_db()
        return Response(MeSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        return self.put(request)

    def delete(self, request):
        profile = request.user.profile
        old_name = profile.avatar.name if profile.avatar else ""
        old_storage = profile.avatar.storage if profile.avatar else None
        profile.avatar = None
        profile.save(update_fields=["avatar", "updated_at"])
        if old_name and old_storage:
            try:
                old_storage.delete(old_name)
            except Exception:
                pass
        request.user.refresh_from_db()
        return Response(MeSerializer(request.user, context={"request": request}).data)


class PasswordChangeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["current_password"]):
            return Response({"current_password": ["Current password is incorrect."]}, status=status.HTTP_400_BAD_REQUEST)
        validate_password(serializer.validated_data["new_password"], user=request.user)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        current_session_id = getattr(request.auth, "payload", {}).get("session_id")
        revoked = _revoke_user_sessions(request.user, exclude_session_id=current_session_id)
        return Response({"detail": "Password updated.", "revoked_sessions": revoked})


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        _ensure_ip_not_blocked(request, "password_reset")
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=_normalize_email(serializer.validated_data["email"]), is_active=True).first()
        if user:
            raw_token = _create_auth_action_token(
                user=user,
                purpose=AuthActionToken.Purpose.PASSWORD_RESET,
                ttl_seconds=PASSWORD_RESET_TOKEN_TTL_SECONDS,
                email=user.email,
            )
            reset_url = _build_frontend_url("/auth/reset-password", token=raw_token)
            _send_account_email(
                subject="Reset your password",
                body=f"Use this link to reset your password:\n\n{reset_url}\n\nIf you did not request this, you can ignore this email.",
                recipients=[user.email],
            )
        else:
            _record_ip_failure(request, "password_reset")
        return Response({"detail": "If the account exists, a reset email has been sent."})


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        _ensure_ip_not_blocked(request, "password_reset_confirm")
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            action_token = _consume_auth_action_token(serializer.validated_data["token"], purpose=AuthActionToken.Purpose.PASSWORD_RESET)
        except Exception:
            _record_ip_failure(request, "password_reset_confirm")
            raise
        user = action_token.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        revoked = _revoke_user_sessions(user)
        _clear_ip_failures(request, "password_reset_confirm")
        return Response({"detail": "Password has been reset.", "revoked_sessions": revoked})


class EmailVerifyRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [EmailVerifyThrottle]

    def post(self, request):
        serializer = EmailVerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if request.user.is_authenticated:
            user = request.user
        else:
            email = serializer.validated_data.get("email", "")
            user = User.objects.filter(email__iexact=email, is_active=True).first() if email else None
        if user and user.email and not user.email_verified:
            _send_email_verification_otp(user)
        return Response({"detail": "If the account is awaiting verification, a new code has been sent."})


class EmailVerifyConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [EmailVerifyThrottle]

    def post(self, request):
        _ensure_ip_not_blocked(request, "email_verify_confirm")
        serializer = EmailVerifyConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            if serializer.validated_data.get("token"):
                action_token = _consume_auth_action_token(serializer.validated_data["token"], purpose=AuthActionToken.Purpose.EMAIL_VERIFY)
            else:
                action_token = _consume_email_verification_otp(
                    email=serializer.validated_data["email"],
                    code=serializer.validated_data["code"],
                )
        except Exception:
            _record_ip_failure(request, "email_verify_confirm")
            raise
        _mark_email_verified(action_token.user, email=action_token.email or action_token.user.email)
        _clear_ip_failures(request, "email_verify_confirm")
        return Response({"detail": "Email verified."})


class SocialLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [SocialLoginThrottle]

    def post(self, request):
        _ensure_ip_not_blocked(request, "social_login")
        serializer = SocialLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            profile = _verify_social_id_token(
                serializer.validated_data["provider"],
                serializer.validated_data["id_token"],
            )
        except Exception:
            _record_ip_failure(request, "social_login")
            raise
        user, social_account, created = _get_or_create_social_user(
            provider=serializer.validated_data["provider"],
            profile=profile,
        )
        _clear_ip_failures(request, "social_login")
        payload = _issue_session_tokens(user=user, request=request)
        payload["social_account"] = {
            "provider": social_account.provider,
            "email": social_account.email,
            "created": created,
        }
        return Response(payload, status=status.HTTP_200_OK)


class SessionListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        current_session_id = request.auth.get("session_id") if isinstance(request.auth, dict) else getattr(request.auth, "payload", {}).get("session_id")
        sessions = UserSession.objects.filter(user=request.user).order_by("-last_seen_at", "-created_at")
        serializer = UserSessionSerializer(sessions, many=True, context={"current_session_id": current_session_id})
        return Response(serializer.data)


class SessionRevokeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(UserSession, id=session_id, user=request.user)
        if session.revoked_at is None:
            session.revoked_at = timezone.now()
            session.save(update_fields=["revoked_at", "updated_at"])
        return Response({"detail": "Session revoked."})


class SessionRevokeAllView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        current_session_id = request.auth.get("session_id") if isinstance(request.auth, dict) else getattr(request.auth, "payload", {}).get("session_id")
        revoked = UserSession.objects.filter(user=request.user, revoked_at__isnull=True).exclude(id=current_session_id).update(revoked_at=timezone.now())
        return Response({"detail": "Other sessions revoked.", "revoked_count": revoked})


class AccountExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(_export_account_snapshot(request.user))


class AccountDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = AccountDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["password"]):
            return Response({"password": ["Password is incorrect."]}, status=status.HTTP_400_BAD_REQUEST)
        _anonymize_account(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserSearchCursorPagination(CursorPagination):
    page_size = 30
    ordering = "username"


class UserSearchView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserDiscoverySerializer
    pagination_class = UserSearchCursorPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()
        query = _sanitize_plain_text(self.request.query_params.get("q", ""), max_length=120)
        if not query:
            return User.objects.none()
        return (
            _user_discovery_queryset(self.request.user)
            .filter(
                Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(profile__display_name__icontains=query)
            )
            .order_by("username")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        if _truthy_query_param(request.query_params.get("paginated")):
            page = self.paginate_queryset(queryset)
            users = list(page or [])
            _attach_latest_relation(users, request.user)
            serializer = self.get_serializer(users, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)

        # Preserve the original array response for existing native clients while
        # allowing the web messenger to opt into cursor pagination.
        users = list(queryset[:30])
        _attach_latest_relation(users, request.user)
        serializer = self.get_serializer(users, many=True, context={"request": request})
        return Response(serializer.data)


class NearbyUsersView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserDiscoverySerializer
    queryset = User.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            latitude = Decimal(str(request.query_params.get("latitude", "")))
            longitude = Decimal(str(request.query_params.get("longitude", "")))
            _validate_coordinate_range(latitude, longitude)
        except Exception:
            return Response(
                {"detail": "Valid latitude and longitude query params are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            radius_km = float(request.query_params.get("radius_km", 25) or 25)
            limit = int(request.query_params.get("limit", 20) or 20)
        except (TypeError, ValueError):
            return Response({"detail": "radius_km and limit must be valid numbers."}, status=status.HTTP_400_BAD_REQUEST)
        if radius_km <= 0 or radius_km > 100:
            return Response({"detail": "radius_km must be between 0 and 100."}, status=status.HTTP_400_BAD_REQUEST)
        if limit <= 0:
            return Response({"detail": "limit must be greater than 0."}, status=status.HTTP_400_BAD_REQUEST)
        limit = min(limit, 50)

        if _truthy_query_param(request.query_params.get("share_location")):
            profile = request.user.profile
            if not profile.is_discoverable:
                return Response(
                    {"detail": "Account discovery must be enabled before nearby visibility can be turned on."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            profile.latitude = latitude
            profile.longitude = longitude
            profile.nearby_discovery_enabled = True
            profile.location_updated_at = timezone.now()
            profile.save(
                update_fields=[
                    "latitude",
                    "longitude",
                    "nearby_discovery_enabled",
                    "location_updated_at",
                    "updated_at",
                ]
            )

        # Narrow the candidate set before doing Python-side distance checks.
        lat_delta = Decimal(str(radius_km / 111.0))
        lon_divisor = max(abs(cos(radians(float(latitude)))), 0.01)
        lon_delta = Decimal(str(radius_km / (111.0 * lon_divisor)))
        lat_min = latitude - lat_delta
        lat_max = latitude + lat_delta
        lon_min = longitude - lon_delta
        lon_max = longitude + lon_delta

        queryset = list(
            _user_discovery_queryset(request.user)
            .exclude(profile__latitude__isnull=True)
            .exclude(profile__longitude__isnull=True)
            .filter(profile__nearby_discovery_enabled=True)
            .filter(
                profile__location_updated_at__gte=timezone.now()
                - timedelta(hours=max(1, int(getattr(settings, "NEARBY_LOCATION_TTL_HOURS", 24) or 24)))
            )
            .filter(profile__latitude__gte=lat_min, profile__latitude__lte=lat_max)
            .filter(profile__longitude__gte=lon_min, profile__longitude__lte=lon_max)
        )
        matches = []
        for user in queryset:
            distance = _distance_km(latitude, longitude, user.profile.latitude, user.profile.longitude)
            if distance <= radius_km:
                user.proximity_km = round(distance, 2)
                matches.append(user)
        matches.sort(key=lambda item: (item.proximity_km, item.username.lower()))
        matches = matches[:limit]
        _attach_latest_relation(matches, request.user)
        serializer = self.get_serializer(matches, many=True, context={"request": request})
        return Response(serializer.data)


class FriendRequestListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return FriendRequestCreateSerializer if self.request.method == "POST" else FriendRequestSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return FriendRequest.objects.none()
        scope = self.request.query_params.get("scope", "all")
        from apps.chat.models import UserBlock

        blocked_user_ids = UserBlock.objects.filter(blocker=self.request.user).values_list("blocked_id", flat=True)
        blocker_user_ids = UserBlock.objects.filter(blocked=self.request.user).values_list("blocker_id", flat=True)
        hidden_user_ids = list(blocked_user_ids) + list(blocker_user_ids)
        qs = (
            FriendRequest.objects.filter(Q(sender=self.request.user) | Q(receiver=self.request.user))
            .exclude(Q(sender_id__in=hidden_user_ids) | Q(receiver_id__in=hidden_user_ids))
            .select_related("sender", "sender__profile", "receiver", "receiver__profile")
        )
        if scope == "incoming":
            qs = qs.filter(receiver=self.request.user)
        elif scope == "outgoing":
            qs = qs.filter(sender=self.request.user)
        elif scope == "friends":
            qs = qs.filter(status=FriendRequest.Status.ACCEPTED)
        elif scope == "pending":
            qs = qs.filter(status=FriendRequest.Status.PENDING)
        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_user = serializer.validated_data["target_user"]
        if target_user.id == request.user.id:
            return Response({"detail": "You cannot send a friend request to yourself."}, status=status.HTTP_400_BAD_REQUEST)

        from apps.chat.models import UserBlock
        if UserBlock.objects.filter(Q(blocker=request.user, blocked=target_user) | Q(blocker=target_user, blocked=request.user)).exists():
            return Response({"detail": "This user cannot be added right now."}, status=status.HTTP_400_BAD_REQUEST)

        existing = FriendRequest.objects.filter(
            Q(sender=request.user, receiver=target_user) | Q(sender=target_user, receiver=request.user)
        ).order_by("-created_at").first()

        if existing:
            if existing.status == FriendRequest.Status.ACCEPTED:
                return Response(FriendRequestSerializer(existing, context={"request": request}).data, status=status.HTTP_200_OK)
            if existing.status == FriendRequest.Status.PENDING:
                return Response(FriendRequestSerializer(existing, context={"request": request}).data, status=status.HTTP_200_OK)
            if existing.sender_id == request.user.id:
                existing.status = FriendRequest.Status.PENDING
                existing.message = serializer.validated_data.get("message", "")
                existing.responded_at = None
                existing.save(update_fields=["status", "message", "responded_at", "updated_at"])
                return Response(FriendRequestSerializer(existing, context={"request": request}).data, status=status.HTTP_201_CREATED)

        friend_request = FriendRequest.objects.create(
            sender=request.user,
            receiver=target_user,
            message=serializer.validated_data.get("message", ""),
        )
        return Response(FriendRequestSerializer(friend_request, context={"request": request}).data, status=status.HTTP_201_CREATED)


class FriendRequestRespondView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, request_id):
        friend_request = get_object_or_404(
            FriendRequest.objects.select_related("sender", "sender__profile", "receiver", "receiver__profile"),
            id=request_id,
        )
        serializer = FriendRequestRespondSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]

        if action in {"accept", "reject"} and friend_request.receiver_id != request.user.id:
            return Response({"detail": "You cannot respond to this friend request."}, status=status.HTTP_403_FORBIDDEN)
        if action == "cancel" and friend_request.sender_id != request.user.id:
            return Response({"detail": "You can only cancel requests you sent."}, status=status.HTTP_403_FORBIDDEN)
        if friend_request.status != FriendRequest.Status.PENDING:
            return Response({"detail": "Only pending friend requests can be updated."}, status=status.HTTP_400_BAD_REQUEST)

        friend_request.status = {
            "accept": FriendRequest.Status.ACCEPTED,
            "reject": FriendRequest.Status.REJECTED,
            "cancel": FriendRequest.Status.CANCELED,
        }[action]
        friend_request.responded_at = timezone.now()
        friend_request.save(update_fields=["status", "responded_at", "updated_at"])
        return Response(FriendRequestSerializer(friend_request, context={"request": request}).data)


class LoginView(TokenObtainPairView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginThrottle]
    serializer_class = LoginSerializer


class RefreshView(TokenRefreshView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RefreshThrottle]
    serializer_class = RefreshSerializer


DetailResponseSerializer = inline_serializer(
    name="DetailResponse",
    fields={"detail": serializers.CharField()},
)
PasswordChangeResponseSerializer = inline_serializer(
    name="PasswordChangeResponse",
    fields={"detail": serializers.CharField(), "revoked_sessions": serializers.IntegerField()},
)
SessionRevokeAllResponseSerializer = inline_serializer(
    name="SessionRevokeAllResponse",
    fields={"detail": serializers.CharField(), "revoked_count": serializers.IntegerField()},
)
LoginResponseSerializer = inline_serializer(
    name="LoginResponse",
    fields={
        "refresh": serializers.CharField(),
        "access": serializers.CharField(),
        "session_id": serializers.UUIDField(),
        "user": MeSerializer(),
    },
)
SocialLoginResponseSerializer = inline_serializer(
    name="SocialLoginResponse",
    fields={
        "refresh": serializers.CharField(),
        "access": serializers.CharField(),
        "session_id": serializers.UUIDField(),
        "user": MeSerializer(),
        "social_account": serializers.DictField(),
    },
)


MeView.get = extend_schema(responses=MeSerializer)(MeView.get)
MeView.patch = extend_schema(request=MeUpdateSerializer, responses=MeSerializer)(MeView.patch)
PasswordChangeView.post = extend_schema(request=PasswordChangeSerializer, responses=PasswordChangeResponseSerializer)(PasswordChangeView.post)
PasswordResetRequestView.post = extend_schema(request=PasswordResetRequestSerializer, responses=DetailResponseSerializer)(PasswordResetRequestView.post)
PasswordResetConfirmView.post = extend_schema(request=PasswordResetConfirmSerializer, responses=PasswordChangeResponseSerializer)(PasswordResetConfirmView.post)
EmailVerifyRequestView.post = extend_schema(request=EmailVerifyRequestSerializer, responses=DetailResponseSerializer)(EmailVerifyRequestView.post)
EmailVerifyConfirmView.post = extend_schema(request=EmailVerifyConfirmSerializer, responses=DetailResponseSerializer)(EmailVerifyConfirmView.post)
SocialLoginView.post = extend_schema(request=SocialLoginSerializer, responses=SocialLoginResponseSerializer)(SocialLoginView.post)
SessionListView.get = extend_schema(responses=UserSessionSerializer(many=True))(SessionListView.get)
SessionRevokeView.post = extend_schema(request=None, responses=DetailResponseSerializer)(SessionRevokeView.post)
SessionRevokeAllView.post = extend_schema(request=None, responses=SessionRevokeAllResponseSerializer)(SessionRevokeAllView.post)
AccountExportView.get = extend_schema(responses=OpenApiTypes.OBJECT)(AccountExportView.get)
AccountDeleteView.post = extend_schema(request=AccountDeleteSerializer, responses={204: OpenApiResponse(description="Account deleted.")})(AccountDeleteView.post)
NearbyUsersView.get = extend_schema(
    parameters=[
        OpenApiParameter("latitude", OpenApiTypes.DECIMAL, OpenApiParameter.QUERY, required=True),
        OpenApiParameter("longitude", OpenApiTypes.DECIMAL, OpenApiParameter.QUERY, required=True),
        OpenApiParameter("radius_km", OpenApiTypes.FLOAT, OpenApiParameter.QUERY),
        OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY),
    ],
    responses=UserDiscoverySerializer(many=True),
)(NearbyUsersView.get)
FriendRequestListCreateView.get = extend_schema(
    parameters=[OpenApiParameter("scope", OpenApiTypes.STR, OpenApiParameter.QUERY, enum=["all", "incoming", "outgoing", "friends", "pending"])],
    responses=FriendRequestSerializer(many=True),
)(FriendRequestListCreateView.get)
FriendRequestListCreateView.post = extend_schema(request=FriendRequestCreateSerializer, responses=FriendRequestSerializer)(FriendRequestListCreateView.post)
FriendRequestRespondView.post = extend_schema(request=FriendRequestRespondSerializer, responses=FriendRequestSerializer)(FriendRequestRespondView.post)
