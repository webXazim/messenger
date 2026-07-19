from django.contrib import admin

from apps.common.models import RealtimeOutboxEvent


@admin.register(RealtimeOutboxEvent)
class RealtimeOutboxEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_name",
        "status",
        "delivery_target",
        "published_transport",
        "attempts",
        "created_at",
        "published_at",
    )
    list_filter = ("status", "delivery_target", "published_transport", "event_name")
    search_fields = ("event_id", "stream_entry_id", "event_name")
    readonly_fields = (
        "id",
        "event_id",
        "event_name",
        "payload",
        "audiences",
        "status",
        "attempts",
        "available_at",
        "published_at",
        "delivery_target",
        "published_transport",
        "stream_entry_id",
        "last_error",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)
