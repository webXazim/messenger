from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from apps.support.models import (
    SupportAuditEvent,
    SupportCannedReply,
    SupportConversation,
    SupportConversationTag,
    SupportInternalNote,
    SupportSavedInboxView,
    SupportTag,
    SupportWebsite,
)
from apps.support.realtime import publish_support_event
from apps.support.services import SupportContext, visible_websites


def person_name(user) -> str:
    if not user:
        return "Support Chat"
    profile = getattr(user, "profile", None)
    return (
        (getattr(profile, "display_name", "") or "").strip()
        or (getattr(user, "get_full_name", lambda: "")() or "").strip()
        or (getattr(user, "username", "") or "").strip()
        or "Support team"
    )


def request_ip(request) -> str | None:
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR") or None


def record_audit_event(
    *,
    account,
    action: str,
    summary: str,
    actor=None,
    website=None,
    support_conversation=None,
    target_type: str = "",
    target_id=None,
    metadata: dict | None = None,
    ip_address: str | None = None,
) -> SupportAuditEvent:
    return SupportAuditEvent.objects.create(
        support_account=account,
        website=website or (support_conversation.website if support_conversation else None),
        support_conversation=support_conversation,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=(summary or action)[:255],
        metadata=metadata or {},
        ip_address=ip_address,
    )


def publish_private_conversation_refresh(support_conversation: SupportConversation, reason: str) -> None:
    publish_support_event(
        event_name="support.conversation.private_updated",
        website_id=support_conversation.website_id,
        data={
            "conversation_id": str(support_conversation.id),
            "website_id": str(support_conversation.website_id),
            "reason": reason,
        },
    )


def visible_tags(context: SupportContext):
    if not context.account:
        return SupportTag.objects.none()
    return SupportTag.objects.filter(support_account=context.account, is_active=True).order_by("name")


def visible_canned_replies(context: SupportContext, website_id=None):
    if not context.account:
        return SupportCannedReply.objects.none()
    queryset = SupportCannedReply.objects.filter(support_account=context.account, is_active=True).select_related("website")
    visible_ids = visible_websites(context).values_list("id", flat=True)
    queryset = queryset.filter(Q(website__isnull=True) | Q(website_id__in=visible_ids))
    if website_id:
        queryset = queryset.filter(Q(website__isnull=True) | Q(website_id=website_id))
    return queryset.distinct().order_by("shortcut", "title")


def resolve_visible_website(context: SupportContext, website_id):
    if not website_id:
        return None
    return visible_websites(context).filter(pk=website_id).first()


@transaction.atomic
def create_internal_note(*, context: SupportContext, conversation: SupportConversation, actor, body: str, ip_address=None):
    body = (body or "").strip()
    if not body:
        raise ValueError("Write a note before saving.")
    note = SupportInternalNote(
        support_conversation=conversation,
        author=actor,
        body=body,
    )
    note.full_clean()
    note.save()
    record_audit_event(
        account=context.account,
        website=conversation.website,
        support_conversation=conversation,
        actor=actor,
        action="conversation.note_added",
        target_type="internal_note",
        target_id=note.id,
        summary=f"{person_name(actor)} added an internal note.",
        metadata={"note_id": str(note.id)},
        ip_address=ip_address,
    )
    publish_private_conversation_refresh(conversation, "internal_note_added")
    return note


@transaction.atomic
def replace_conversation_tags(
    *,
    context: SupportContext,
    conversation: SupportConversation,
    actor,
    tag_ids,
    ip_address=None,
):
    normalized = list(dict.fromkeys(tag_ids or []))
    tags = list(SupportTag.objects.filter(
        support_account=context.account,
        is_active=True,
        id__in=normalized,
    ).order_by("name"))
    if len(tags) != len(normalized):
        raise ValueError("One or more selected tags are unavailable.")

    current = {
        assignment.tag_id: assignment
        for assignment in SupportConversationTag.objects.select_for_update().filter(
            support_conversation=conversation
        ).select_related("tag")
    }
    wanted = {tag.id for tag in tags}
    removed = [assignment for tag_id, assignment in current.items() if tag_id not in wanted]
    added = [tag for tag in tags if tag.id not in current]

    if removed:
        SupportConversationTag.objects.filter(id__in=[item.id for item in removed]).delete()
    if added:
        SupportConversationTag.objects.bulk_create([
            SupportConversationTag(
                support_conversation=conversation,
                tag=tag,
                added_by=actor,
            )
            for tag in added
        ])

    if added or removed:
        record_audit_event(
            account=context.account,
            website=conversation.website,
            support_conversation=conversation,
            actor=actor,
            action="conversation.tags_changed",
            target_type="support_conversation",
            target_id=conversation.id,
            summary=f"{person_name(actor)} updated conversation tags.",
            metadata={
                "added": [{"id": str(tag.id), "name": tag.name} for tag in added],
                "removed": [{"id": str(item.tag_id), "name": item.tag.name} for item in removed],
            },
            ip_address=ip_address,
        )
        publish_private_conversation_refresh(conversation, "tags_changed")
    return tags


@transaction.atomic
def save_default_view(view: SupportSavedInboxView) -> None:
    if not view.is_default:
        return
    SupportSavedInboxView.objects.filter(
        support_account=view.support_account,
        user=view.user,
        is_default=True,
    ).exclude(pk=view.pk).update(is_default=False)
