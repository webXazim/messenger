from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.base import ContentFile
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
import re
from io import BytesIO
from uuid import uuid4
from typing import Any
from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.accounts.models import AuthActionToken, FriendRequest, Profile, SocialAccount, UserSession
from apps.chat.services import get_presence_snapshot, get_presence_snapshots

User = get_user_model()


CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
USERNAME_VALIDATOR = UnicodeUsernameValidator()


def _clean_text(value, *, max_length=None, multiline=False):
    text = CONTROL_CHAR_RE.sub("", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not multiline:
        text = " ".join(text.split())
    else:
        text = "\n".join(line.strip() for line in text.split("\n"))
        text = text.strip()
    if max_length is not None:
        text = text[:max_length]
    return text


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("id", "username", "email", "password", "first_name", "last_name")

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_username(self, value):
        username = _clean_text(value, max_length=150)
        if not username:
            raise serializers.ValidationError("Enter a username.")
        try:
            USERNAME_VALIDATOR(username)
        except DjangoValidationError:
            raise serializers.ValidationError("Use only letters, numbers, and @/./+/-/_ characters.")
        if User.objects.filter(username__iexact=username).exists():
            raise serializers.ValidationError("This username is already taken.")
        return username

    def validate_email(self, value):
        return User.objects.normalize_email(_clean_text(value, max_length=254)).lower()

    def validate_first_name(self, value):
        return _clean_text(value, max_length=150)

    def validate_last_name(self, value):
        return _clean_text(value, max_length=150)

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        try:
            user.save()
        except IntegrityError as exc:
            raise serializers.ValidationError({"username": "This username is already taken."}) from exc
        return user


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = (
            "display_name",
            "avatar",
            "bio",
            "status_message",
            "is_discoverable",
            "show_online_status",
            "nearby_discovery_enabled",
            "latitude",
            "longitude",
            "location_updated_at",
        )
        read_only_fields = ("location_updated_at",)


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = (
            "display_name",
            "avatar",
            "bio",
            "status_message",
            "is_discoverable",
            "show_online_status",
            "nearby_discovery_enabled",
            "latitude",
            "longitude",
        )

    def validate_display_name(self, value):
        return _clean_text(value, max_length=150)

    def validate_bio(self, value):
        return _clean_text(value, max_length=1000, multiline=True)

    def validate_status_message(self, value):
        return _clean_text(value, max_length=255)




class AvatarUploadSerializer(serializers.Serializer):
    avatar = serializers.ImageField(write_only=True)

    allowed_formats = {"JPEG", "PNG", "WEBP"}

    def validate_avatar(self, value):
        max_bytes = int(getattr(settings, "PROFILE_AVATAR_MAX_BYTES", 5 * 1024 * 1024) or 5 * 1024 * 1024)
        if getattr(value, "size", 0) > max_bytes:
            raise serializers.ValidationError(f"Profile photos must be {max_bytes // (1024 * 1024)} MB or smaller.")

        try:
            value.seek(0)
            with Image.open(value) as image:
                width, height = image.size
                max_pixels = int(getattr(settings, "PROFILE_AVATAR_MAX_PIXELS", 25_000_000) or 25_000_000)
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise serializers.ValidationError(
                        f"Profile photos must contain no more than {max_pixels:,} pixels."
                    )
                image.verify()
                image_format = str(image.format or "").upper()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise serializers.ValidationError("Upload a valid JPEG, PNG, or WebP image.") from exc
        finally:
            try:
                value.seek(0)
            except Exception:
                pass

        if image_format not in self.allowed_formats:
            raise serializers.ValidationError("Only JPEG, PNG, and WebP profile photos are supported.")
        return value

    def save_avatar(self, profile):
        uploaded = self.validated_data["avatar"]
        max_dimension = int(getattr(settings, "PROFILE_AVATAR_MAX_DIMENSION", 1024) or 1024)

        uploaded.seek(0)
        try:
            with Image.open(uploaded) as source:
                image = ImageOps.exif_transpose(source)
                image.load()
                has_alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
                image = image.convert("RGBA" if has_alpha else "RGB")
                image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

                output = BytesIO()
                image.save(output, format="WEBP", quality=86, method=6)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise serializers.ValidationError({"avatar": "This profile photo could not be processed."}) from exc

        old_name = profile.avatar.name if profile.avatar else ""
        old_storage = profile.avatar.storage if profile.avatar else None
        filename = f"{uuid4().hex}.webp"
        profile.avatar.save(filename, ContentFile(output.getvalue()), save=False)
        profile.save(update_fields=["avatar", "updated_at"])

        if old_name and old_storage and old_name != profile.avatar.name:
            try:
                old_storage.delete(old_name)
            except Exception:
                pass
        return profile


class MeSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    social_accounts = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "email", "email_verified", "email_verified_at", "first_name", "last_name", "last_seen_at", "profile", "social_accounts")

    def get_social_accounts(self, obj) -> list[dict[str, Any]]:
        return [
            {
                "provider": item.provider,
                "email": item.email,
                "last_login_at": item.last_login_at,
            }
            for item in obj.social_accounts.all().order_by("provider")
        ]


class MeUpdateSerializer(serializers.ModelSerializer):
    profile = ProfileUpdateSerializer(required=False)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "profile")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        profile_data = attrs.get("profile") or {}
        current_profile = getattr(self.instance, "profile", None)
        discoverable = profile_data.get(
            "is_discoverable",
            getattr(current_profile, "is_discoverable", True),
        )
        nearby_enabled = profile_data.get(
            "nearby_discovery_enabled",
            getattr(current_profile, "nearby_discovery_enabled", False),
        )
        if profile_data.get("is_discoverable") is False:
            nearby_enabled = False
        if nearby_enabled and not discoverable:
            raise serializers.ValidationError({
                "profile": {
                    "nearby_discovery_enabled": "Account discovery must be enabled before nearby visibility can be turned on."
                }
            })
        return attrs

    def validate_first_name(self, value):
        return _clean_text(value, max_length=150)

    def validate_last_name(self, value):
        return _clean_text(value, max_length=150)

    def validate_email(self, value):
        return User.objects.normalize_email(_clean_text(value, max_length=254)).lower()

    def update(self, instance, validated_data):
        profile_data = validated_data.pop("profile", None)
        update_fields = []
        new_email = validated_data.get("email")
        if new_email is not None and new_email.lower() != (instance.email or "").lower():
            instance.email_verified = False
            instance.email_verified_at = None
            update_fields.extend(["email_verified", "email_verified_at"])
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
            update_fields.append(attr)
        if update_fields:
            instance.save(update_fields=list(dict.fromkeys(update_fields)))
        else:
            instance.save()
        if profile_data is not None:
            profile = instance.profile
            profile_data = dict(profile_data)
            if profile_data.get("is_discoverable") is False or profile_data.get("nearby_discovery_enabled") is False:
                profile_data["nearby_discovery_enabled"] = False
                profile_data["latitude"] = None
                profile_data["longitude"] = None
            changed = []
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
                changed.append(attr)
            if "latitude" in profile_data or "longitude" in profile_data:
                if profile_data.get("latitude") is None and profile_data.get("longitude") is None:
                    profile.location_updated_at = None
                else:
                    profile.location_updated_at = timezone.now()
                changed.append("location_updated_at")
            if changed:
                profile.save(update_fields=list(dict.fromkeys(changed)))
        return instance


class UserDiscoveryListSerializer(serializers.ListSerializer):
    """Batch user presence for discovery/search responses."""

    def to_representation(self, data):
        iterable = list(data.all() if hasattr(data, "all") else data)
        presence_map = self.context.setdefault("presence_map", {})
        missing_ids = [
            str(user.id)
            for user in iterable
            if str(user.id) not in presence_map
        ]
        if missing_ids:
            presence_map.update(get_presence_snapshots(missing_ids))
        return super().to_representation(iterable)


class UserDiscoverySerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    bio = serializers.SerializerMethodField()
    status_message = serializers.SerializerMethodField()
    is_current_user = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()
    active_devices = serializers.SerializerMethodField()
    last_seen_at = serializers.SerializerMethodField()
    presence_label = serializers.SerializerMethodField()
    presence_status = serializers.SerializerMethodField()
    device_type = serializers.SerializerMethodField()
    device_types = serializers.SerializerMethodField()
    presence_visibility = serializers.SerializerMethodField()
    friendship_status = serializers.SerializerMethodField()
    proximity_km = serializers.FloatField(read_only=True, required=False)

    class Meta:
        model = User
        list_serializer_class = UserDiscoveryListSerializer
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "display_name",
            "avatar",
            "bio",
            "status_message",
            "is_current_user",
            "is_online",
            "active_devices",
            "last_seen_at",
            "presence_label",
            "presence_status",
            "device_type",
            "device_types",
            "presence_visibility",
            "friendship_status",
            "proximity_km",
        )

    def get_display_name(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "display_name", "") or obj.get_full_name() or obj.username

    def get_avatar(self, obj) -> str | None:
        profile = getattr(obj, "profile", None)
        avatar = getattr(profile, "avatar", None)
        request = self.context.get("request")
        if not avatar:
            return None
        try:
            url = avatar.url
        except Exception:
            return None
        return request.build_absolute_uri(url) if request else url

    def get_bio(self, obj) -> str:
        return getattr(getattr(obj, "profile", None), "bio", "")

    def get_status_message(self, obj) -> str:
        return getattr(getattr(obj, "profile", None), "status_message", "")

    def get_is_current_user(self, obj) -> bool:
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        return bool(actor and getattr(actor, "is_authenticated", False) and obj.id == actor.id)

    def _presence_is_visible(self, obj) -> bool:
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        if actor is not None and getattr(actor, "is_authenticated", False) and str(actor.id) == str(obj.id):
            return True
        profile = getattr(obj, "profile", None)
        return profile is None or bool(getattr(profile, "show_online_status", True))

    def _presence_snapshot(self, obj):
        presence_map = self.context.get("presence_map")
        if presence_map is not None and str(obj.id) in presence_map:
            return presence_map[str(obj.id)]
        cache_key = f"_user_presence_{obj.id}"
        if not hasattr(self, cache_key):
            setattr(self, cache_key, get_presence_snapshot(obj.id))
        return getattr(self, cache_key)

    def get_is_online(self, obj) -> bool:
        return bool(self._presence_snapshot(obj)["is_online"]) if self._presence_is_visible(obj) else False

    def get_active_devices(self, obj) -> int:
        return int(self._presence_snapshot(obj)["active_devices"]) if self._presence_is_visible(obj) else 0

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_last_seen_at(self, obj):
        return getattr(obj, "last_seen_at", None) if self._presence_is_visible(obj) else None

    def get_presence_label(self, obj) -> str:
        if not self._presence_is_visible(obj):
            return "offline"
        return str(self._presence_snapshot(obj).get("presence_label") or "offline")

    def get_presence_status(self, obj) -> str:
        if not self._presence_is_visible(obj):
            return "offline"
        return str(self._presence_snapshot(obj).get("presence_status") or "offline")

    def get_device_type(self, obj):
        return self._presence_snapshot(obj).get("device_type") if self._presence_is_visible(obj) else None

    def get_device_types(self, obj) -> list[str]:
        return list(self._presence_snapshot(obj).get("device_types") or []) if self._presence_is_visible(obj) else []

    def get_presence_visibility(self, obj) -> str:
        return "public" if self._presence_is_visible(obj) else "hidden"

    def get_friendship_status(self, obj) -> str:
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        if not actor or not getattr(actor, "is_authenticated", False):
            return "none"
        req = getattr(obj, "friend_request_relation", None)
        if req is not None:
            if req.status == FriendRequest.Status.ACCEPTED:
                return "friends"
            if req.status == FriendRequest.Status.PENDING:
                return "incoming_request" if req.receiver_id == actor.id else "outgoing_request"
            if req.status == FriendRequest.Status.REJECTED:
                return "rejected"
            if req.status == FriendRequest.Status.CANCELED:
                return "canceled"
        return "none"


class FriendRequestSerializer(serializers.ModelSerializer):
    sender = UserDiscoverySerializer(read_only=True)
    receiver = UserDiscoverySerializer(read_only=True)

    class Meta:
        model = FriendRequest
        fields = (
            "id",
            "sender",
            "receiver",
            "status",
            "message",
            "responded_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "status", "responded_at", "created_at", "updated_at")


class FriendRequestCreateSerializer(serializers.Serializer):
    user_id = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True), source="target_user", write_only=True)
    message = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def __init__(self, *args, **kwargs):
        data = kwargs.get("data")
        if isinstance(data, dict) and "user_id" not in data:
            data = data.copy()
            for key in ("receiver_id", "to_user_id", "target_user_id"):
                if key in data:
                    data["user_id"] = data[key]
                    kwargs["data"] = data
                    break
        super().__init__(*args, **kwargs)

    def validate_message(self, value):
        return _clean_text(value, max_length=255)


class FriendRequestRespondSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=(("accept", "Accept"), ("reject", "Reject"), ("cancel", "Cancel")))


class UserSessionSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = (
            "id",
            "device_id",
            "user_agent",
            "ip_address",
            "last_seen_at",
            "expires_at",
            "revoked_at",
            "is_current",
        )

    def get_is_current(self, obj) -> bool:
        current_session_id = self.context.get("current_session_id")
        return str(obj.id) == str(current_session_id)


class AccountDeleteSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        user = self.context.get("user")
        validate_password(value, user=user)
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return User.objects.normalize_email(_clean_text(value, max_length=254)).lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=255)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class EmailVerifyConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=255, required=False)
    email = serializers.EmailField(required=False)
    code = serializers.RegexField(r"^\d{6}$", required=False)

    def validate_token(self, value):
        return _clean_text(value, max_length=255)

    def validate_email(self, value):
        return User.objects.normalize_email(_clean_text(value, max_length=254)).lower()

    def validate(self, attrs):
        if attrs.get("token") or (attrs.get("email") and attrs.get("code")):
            return attrs
        raise serializers.ValidationError("Provide a token or an email and six-digit code.")


class EmailVerifyRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)

    def validate_email(self, value):
        return User.objects.normalize_email(_clean_text(value, max_length=254)).lower()


class SocialLoginSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=SocialAccount.Provider.choices)
    id_token = serializers.CharField()

    def validate_id_token(self, value):
        return _clean_text(value, max_length=8192)
