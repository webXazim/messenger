from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone

from .models import AuthActionToken, FriendRequest, Profile, SocialAccount, User, UserSession


class UUIDAdminMixin:
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    extra = 0
    readonly_fields = ("id", "created_at", "updated_at", "location_updated_at")
    fieldsets = (
        (None, {"fields": ("display_name", "avatar", "bio", "status_message")}),
        (
            "Privacy and discovery",
            {"fields": ("is_discoverable", "show_online_status", "nearby_discovery_enabled")},
        ),
        ("Location", {"fields": ("latitude", "longitude", "location_updated_at")}),
        ("System", {"fields": ("id", "created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(User)
class MessengerUserAdmin(UserAdmin):
    inlines = (ProfileInline,)
    list_display = (
        "username",
        "email",
        "email_verified",
        "is_staff",
        "is_active",
        "last_seen_at",
        "date_joined",
    )
    list_filter = ("email_verified", "is_staff", "is_superuser", "is_active", "groups", "date_joined")
    search_fields = ("username", "email", "first_name", "last_name", "profile__display_name")
    ordering = ("-date_joined",)
    readonly_fields = ("last_login", "date_joined", "email_verified_at", "last_seen_at")
    fieldsets = UserAdmin.fieldsets + (
        ("Messenger status", {"fields": ("email_verified", "email_verified_at", "last_seen_at")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Contact", {"fields": ("email",)}),
    )


@admin.register(Profile)
class ProfileAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("display_name", "user", "is_discoverable", "show_online_status", "updated_at")
    list_filter = ("is_discoverable", "show_online_status", "nearby_discovery_enabled", "created_at")
    search_fields = ("display_name", "user__username", "user__email", "bio", "status_message")
    raw_id_fields = ("user",)


@admin.action(description="Mark selected friend requests as accepted")
def accept_friend_requests(modeladmin, request, queryset):
    queryset.update(status=FriendRequest.Status.ACCEPTED, responded_at=timezone.now())


@admin.action(description="Mark selected friend requests as rejected")
def reject_friend_requests(modeladmin, request, queryset):
    queryset.update(status=FriendRequest.Status.REJECTED, responded_at=timezone.now())


@admin.register(FriendRequest)
class FriendRequestAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("sender", "receiver", "status", "created_at", "responded_at")
    list_filter = ("status", "created_at", "responded_at")
    search_fields = ("sender__username", "sender__email", "receiver__username", "receiver__email", "message")
    raw_id_fields = ("sender", "receiver")
    actions = (accept_friend_requests, reject_friend_requests)


@admin.action(description="Revoke selected sessions")
def revoke_sessions(modeladmin, request, queryset):
    queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())


@admin.register(UserSession)
class UserSessionAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("user", "device_id", "ip_address", "last_seen_at", "expires_at", "revoked_at")
    list_filter = ("revoked_at", "expires_at", "last_seen_at", "created_at")
    search_fields = ("user__username", "user__email", "device_id", "refresh_jti", "ip_address", "user_agent")
    raw_id_fields = ("user",)
    readonly_fields = UUIDAdminMixin.readonly_fields + ("refresh_jti", "last_seen_at")
    actions = (revoke_sessions,)


@admin.register(SocialAccount)
class SocialAccountAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("user", "provider", "email", "provider_user_id", "last_login_at")
    list_filter = ("provider", "last_login_at", "created_at")
    search_fields = ("user__username", "user__email", "email", "provider_user_id")
    raw_id_fields = ("user",)


@admin.register(AuthActionToken)
class AuthActionTokenAdmin(UUIDAdminMixin, admin.ModelAdmin):
    list_display = ("user", "purpose", "email", "expires_at", "used_at", "created_at")
    list_filter = ("purpose", "used_at", "expires_at", "created_at")
    search_fields = ("user__username", "user__email", "email", "token_hash")
    raw_id_fields = ("user",)
    readonly_fields = UUIDAdminMixin.readonly_fields + ("token_hash",)
