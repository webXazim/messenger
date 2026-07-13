from django.db import connection
from django.db.models import Count, F, Prefetch, Q

from .models import Conversation, ConversationDraft, ConversationParticipant, Message, MessageAttachment, UserBlock, UserDevice

try:
    from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
except Exception:  # pragma: no cover
    SearchVector = SearchQuery = SearchRank = None


def user_conversations_qs(user, *, lightweight=False):
    participant_queryset = ConversationParticipant.objects.select_related("user", "user__profile").order_by("joined_at")
    if not lightweight:
        participant_queryset = participant_queryset.select_related("last_read_message", "last_delivered_message")

    conversation_queryset = (
        Conversation.objects.filter(participants__user=user, participants__left_at__isnull=True, is_active=True)
        .select_related("last_message", "last_message__sender", "created_by")
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=participant_queryset,
            ),
            Prefetch(
                "drafts",
                queryset=ConversationDraft.objects.filter(user=user).select_related("reply_to", "reply_to__sender", "reply_to__sender__profile").order_by("-updated_at"),
                to_attr="viewer_drafts",
            ),
            Prefetch("last_message__attachments", queryset=MessageAttachment.objects.filter(scan_status=MessageAttachment.ScanStatus.CLEAN)),
        )
        .annotate(active_participant_count=Count("participants", filter=Q(participants__left_at__isnull=True), distinct=True))
        .annotate(
            unread_count=Count(
                "messages",
                filter=(
                    Q(messages__is_deleted=False)
                    & ~Q(messages__sender=user)
                    & (
                        Q(participants__last_read_message__isnull=True)
                        | Q(messages__created_at__gt=F("participants__last_read_message__created_at"))
                    )
                ),
                distinct=True,
            )
        )
        .distinct()
    )
    if lightweight:
        conversation_queryset = conversation_queryset.prefetch_related(
            Prefetch("last_message__reactions"),
        )
    return conversation_queryset


def conversation_messages_qs(user, conversation_id):
    return (
        Message.objects.filter(conversation_id=conversation_id, conversation__participants__user=user, conversation__participants__left_at__isnull=True)
        .select_related("sender", "sender__profile", "reply_to", "forwarded_from", "conversation")
        .prefetch_related(
            Prefetch("attachments", queryset=MessageAttachment.objects.filter(scan_status=MessageAttachment.ScanStatus.CLEAN)),
            "reactions__user__profile",
            "deliveries__user__profile",
            "edit_history__edited_by__profile",
        )
        .distinct()
    )


def searchable_messages_qs(user, query):
    qs = (
        Message.objects.filter(conversation__participants__user=user, conversation__participants__left_at__isnull=True)
        .select_related("sender", "sender__profile", "conversation")
        .prefetch_related(Prefetch("attachments", queryset=MessageAttachment.objects.filter(scan_status=MessageAttachment.ScanStatus.CLEAN)))
        .distinct()
    )
    if connection.vendor == "postgresql" and SearchVector:
        search_vector = SearchVector("text", weight="A")
        search_query = SearchQuery(query)
        return qs.annotate(rank=SearchRank(search_vector, search_query)).filter(rank__gte=0.05).order_by("-rank", "-created_at")
    return qs.filter(text__icontains=query).order_by("-created_at")


def searchable_conversations_qs(user, query):
    qs = user_conversations_qs(user, lightweight=True)
    if connection.vendor == "postgresql" and SearchVector:
        search_vector = SearchVector("title", weight="A")
        search_query = SearchQuery(query)
        return qs.annotate(rank=SearchRank(search_vector, search_query)).filter(
            Q(rank__gte=0.01)
            | Q(participants__user__username__icontains=query)
            | Q(participants__user__profile__display_name__icontains=query)
        ).order_by("-rank", "-last_message_at", "-created_at").distinct()
    return qs.filter(
        Q(title__icontains=query)
        | Q(participants__user__username__icontains=query)
        | Q(participants__user__profile__display_name__icontains=query)
    ).distinct().order_by("-last_message_at", "-created_at")


def user_blocks_qs(user):
    return UserBlock.objects.filter(blocker=user).select_related("blocked", "blocked__profile")


def request_user_devices(user):
    return UserDevice.objects.filter(user=user).only("id", "user_id", "platform", "push_token", "is_active", "last_seen_at", "created_at").order_by("-last_seen_at", "-created_at")



def conversation_media_qs(user, conversation_id, media_kind="all"):
    qs = (
        MessageAttachment.objects.filter(
            message__conversation_id=conversation_id,
            message__conversation__participants__user=user,
            message__conversation__participants__left_at__isnull=True,
            scan_status=MessageAttachment.ScanStatus.CLEAN,
            message__is_deleted=False,
        )
        .select_related("message", "message__sender", "message__sender__profile")
        .order_by("-created_at")
        .distinct()
    )
    if media_kind == "image":
        qs = qs.filter(mime_type__startswith="image/")
    elif media_kind == "video":
        qs = qs.filter(mime_type__startswith="video/")
    elif media_kind == "audio":
        qs = qs.filter(mime_type__startswith="audio/")
    elif media_kind == "file":
        qs = qs.exclude(mime_type__startswith="image/").exclude(mime_type__startswith="video/").exclude(mime_type__startswith="audio/")
    return qs
