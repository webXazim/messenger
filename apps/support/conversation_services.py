from __future__ import annotations

from dataclasses import dataclass
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.chat.models import Conversation, Message
from apps.support.models import (
    SupportAgent,
    SupportConversation,
    SupportConversationReadState,
    SupportMessageAuthor,
    SupportConversationTag,
    SupportWidgetSession,
)
from apps.support.realtime import publish_support_event
from apps.support.media_services import (
    SupportMediaError,
    finalize_support_message_media,
    media_summary,
    support_uploads_for_team,
    support_uploads_for_visitor,
)
from apps.support.services import SupportContext, visible_websites
from apps.support.workflow_services import person_name, record_audit_event
from apps.support.webhook_services import queue_support_webhook_event
from apps.support.service_operations import (
    SupportServiceConfigurationError,
    on_team_message,
    on_visitor_message,
    recalculate_active_targets,
    resolve_inactive_alerts,
    set_follow_up,
)


def _person_display_name(user) -> str:
    profile = getattr(user, "profile", None)
    return (
        (getattr(profile, "display_name", "") or "").strip()
        or (getattr(user, "get_full_name", lambda: "")() or "").strip()
        or (getattr(user, "username", "") or "").strip()
        or "Support team"
    )


def _publish_message_event(*, support_conversation: SupportConversation, message: Message, sender_kind: str, display_name: str) -> None:
    publish_support_event(
        event_name="support.message.created",
        website_id=support_conversation.website_id,
        visitor_id=support_conversation.visitor_id,
        data={
            "conversation_id": str(support_conversation.id),
            "website_id": str(support_conversation.website_id),
            "website_name": support_conversation.website.name,
            "visitor_id": str(support_conversation.visitor_id),
            "message_id": str(message.id),
            "message_type": message.type,
            "text": message.text,
            "preview": media_summary(message),
            "attachment_count": message.attachments.count(),
            "created_at": message.created_at,
            "status": support_conversation.status,
            "priority": support_conversation.priority,
            "assigned_agent_id": str(support_conversation.assigned_agent_id or ""),
            "sender": {
                "kind": sender_kind,
                "display_name": display_name,
            },
        },
    )
    publish_support_event(
        event_name="support.website.updated",
        website_id=support_conversation.website_id,
        data={
            "website_id": str(support_conversation.website_id),
            "reason": "message_created",
        },
    )
    queue_support_webhook_event(
        account=support_conversation.website.support_account,
        event_type="message.created",
        payload={
            "conversation_id": str(support_conversation.id),
            "website_id": str(support_conversation.website_id),
            "visitor_id": str(support_conversation.visitor_id),
            "message_id": str(message.id),
            "sender_kind": sender_kind,
            "message_type": message.type,
            "text": message.text,
            "attachment_count": message.attachments.count(),
            "created_at": message.created_at.isoformat(),
        },
    )


def _publish_conversation_event(*, support_conversation: SupportConversation, reason: str) -> None:
    publish_support_event(
        event_name="support.conversation.updated",
        website_id=support_conversation.website_id,
        visitor_id=support_conversation.visitor_id,
        data={
            "conversation_id": str(support_conversation.id),
            "website_id": str(support_conversation.website_id),
            "website_name": support_conversation.website.name,
            "visitor_id": str(support_conversation.visitor_id),
            "reason": reason,
            "status": support_conversation.status,
            "priority": support_conversation.priority,
            "assigned_agent_id": str(support_conversation.assigned_agent_id or ""),
            "updated_at": support_conversation.updated_at,
        },
    )
    publish_support_event(
        event_name="support.website.updated",
        website_id=support_conversation.website_id,
        data={
            "website_id": str(support_conversation.website_id),
            "reason": reason,
        },
    )
    queue_support_webhook_event(
        account=support_conversation.website.support_account,
        event_type="conversation.updated",
        payload={
            "conversation_id": str(support_conversation.id),
            "website_id": str(support_conversation.website_id),
            "visitor_id": str(support_conversation.visitor_id),
            "reason": reason,
            "status": support_conversation.status,
            "priority": support_conversation.priority,
            "assigned_agent_id": str(support_conversation.assigned_agent_id or ""),
            "updated_at": support_conversation.updated_at.isoformat(),
        },
    )


class SupportConversationError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ConversationPage:
    items: list[SupportConversation]
    has_more: bool


def support_messages_qs(support_conversation: SupportConversation):
    return (
        Message.objects.filter(
            conversation=support_conversation.conversation,
            is_deleted=False,
        )
        .select_related(
            "sender",
            "sender__profile",
            "support_author",
            "support_author__visitor",
            "support_author__session",
        )
        .prefetch_related("attachments")
        .order_by("created_at", "id")
    )


def support_conversations_for_context(context: SupportContext):
    website_ids = visible_websites(context).values_list("id", flat=True)
    queryset = (
        SupportConversation.objects.filter(website_id__in=website_ids)
        .select_related(
            "conversation",
            "conversation__last_message",
            "conversation__last_message__sender",
            "conversation__last_message__sender__profile",
            "website",
            "website__support_account",
            "website__support_account__service_settings",
            "visitor",
            "assigned_agent",
            "assigned_agent__user",
            "assigned_agent__user__profile",
            "follow_up_created_by",
            "follow_up_created_by__profile",
        )
        .prefetch_related(
            Prefetch(
                "tag_assignments",
                queryset=SupportConversationTag.objects.select_related("tag").filter(tag__is_active=True).order_by("tag__name"),
                to_attr="prefetched_tag_assignments",
            )
        )
    )
    if context.role == "owner":
        return queryset
    if not context.agent:
        return queryset.none()
    if context.agent.can_view_all_conversations:
        return queryset
    return queryset.filter(Q(assigned_agent=context.agent) | Q(assigned_agent__isnull=True))


def get_context_conversation(context: SupportContext, conversation_id) -> SupportConversation:
    conversation = support_conversations_for_context(context).filter(pk=conversation_id).first()
    if not conversation:
        raise SupportConversationError(
            "The Support Chat conversation was not found or is not available to you.",
            code="conversation_not_found",
            status_code=404,
        )
    return conversation


@transaction.atomic
def get_or_create_visitor_conversation(session: SupportWidgetSession) -> tuple[SupportConversation, bool]:
    visitor = type(session.visitor).objects.select_for_update().get(pk=session.visitor_id)
    existing = (
        SupportConversation.objects.select_related("conversation", "website", "visitor")
        .filter(visitor=visitor)
        .first()
    )
    if existing:
        return existing, False

    visitor_label = visitor.name.strip() or "Website visitor"
    chat_conversation = Conversation.objects.create(
        type=Conversation.ConversationType.DIRECT,
        title=f"{session.website.name} · {visitor_label}"[:255],
        created_by=None,
        is_active=True,
    )
    support_conversation = SupportConversation.objects.create(
        conversation=chat_conversation,
        website=session.website,
        visitor=visitor,
        subject=f"Conversation with {visitor_label}"[:255],
    )
    queue_support_webhook_event(
        account=session.website.support_account,
        event_type="conversation.created",
        payload={
            "conversation_id": str(support_conversation.id),
            "website_id": str(session.website_id),
            "visitor_id": str(visitor.id),
            "status": support_conversation.status,
            "priority": support_conversation.priority,
            "created_at": support_conversation.created_at.isoformat(),
        },
    )
    return support_conversation, True


def _clean_message_text(text: str, *, required: bool = True) -> str:
    normalized = (text or "").strip()
    if required and not normalized:
        raise SupportConversationError("Write a message before sending.", code="empty_message")
    if len(normalized) > 10000:
        raise SupportConversationError("Messages can contain at most 10,000 characters.", code="message_too_long")
    return normalized


def _touch_chat_conversation(conversation: Conversation, message: Message) -> None:
    conversation.last_message = message
    conversation.last_message_at = message.created_at
    conversation.save(update_fields=["last_message", "last_message_at", "updated_at"])


@transaction.atomic
def send_visitor_message(
    *,
    session: SupportWidgetSession,
    text: str = "",
    attachment_ids=None,
    voice_note: bool = False,
) -> tuple[SupportConversation, Message]:
    support_conversation, _ = get_or_create_visitor_conversation(session)
    support_conversation = (
        SupportConversation.objects.select_for_update()
        .select_related("conversation", "website", "website__support_account", "visitor")
        .get(pk=support_conversation.pk)
    )
    if support_conversation.status == SupportConversation.Status.CLOSED:
        raise SupportConversationError(
            "This support conversation is closed. Start a new visitor session to contact the team again.",
            code="conversation_closed",
            status_code=410,
        )

    clean_text = _clean_message_text(text, required=False)
    attachment_ids = attachment_ids or []
    if not clean_text and not attachment_ids:
        raise SupportConversationError("Write a message or add an attachment before sending.", code="empty_message")
    try:
        uploads = support_uploads_for_visitor(
            session=session,
            support_conversation=support_conversation,
            attachment_ids=attachment_ids,
            voice_note=voice_note,
        ) if attachment_ids else []
    except SupportMediaError as exc:
        raise SupportConversationError(exc.detail, code=exc.code, status_code=exc.status_code) from exc

    message = Message.objects.create(
        conversation=support_conversation.conversation,
        sender=None,
        type=Message.MessageType.AUDIO if voice_note else Message.MessageType.TEXT,
        text=clean_text,
        metadata={"voice_note": True} if voice_note else {},
        delivery_status=Message.DeliveryStatus.SENT,
    )
    SupportMessageAuthor.objects.create(
        message=message,
        visitor=session.visitor,
        session=session,
        display_name=session.visitor.name.strip() or "Website visitor",
    )
    finalize_support_message_media(message=message, uploads=uploads, text=clean_text, voice_note=voice_note)
    _touch_chat_conversation(support_conversation.conversation, message)

    support_conversation.last_visitor_message_at = message.created_at
    if support_conversation.status in {
        SupportConversation.Status.NEW,
        SupportConversation.Status.OPEN,
        SupportConversation.Status.WAITING_CUSTOMER,
        SupportConversation.Status.WAITING_TEAM,
        SupportConversation.Status.RESOLVED,
    }:
        support_conversation.status = SupportConversation.Status.OPEN
        support_conversation.resolved_at = None
    support_conversation.save(
        update_fields=["last_visitor_message_at", "status", "resolved_at", "updated_at"]
    )
    on_visitor_message(support_conversation, message_at=message.created_at)
    _publish_message_event(
        support_conversation=support_conversation,
        message=message,
        sender_kind="visitor",
        display_name=session.visitor.name.strip() or "Website visitor",
    )
    return support_conversation, message


def can_reply(context: SupportContext, support_conversation: SupportConversation) -> bool:
    if context.role == "owner":
        return True
    agent = context.agent
    if not agent or not agent.is_active:
        return False
    if support_conversation.assigned_agent_id in {None, agent.id}:
        return True
    return bool(agent.can_assign_conversations)


@transaction.atomic
def send_team_message(
    *,
    context: SupportContext,
    actor,
    support_conversation: SupportConversation,
    text: str = "",
    attachment_ids=None,
    voice_note: bool = False,
) -> Message:
    support_conversation = (
        SupportConversation.objects.select_for_update()
        .select_related("conversation", "assigned_agent", "website", "website__support_account", "visitor")
        .get(pk=support_conversation.pk)
    )
    if not can_reply(context, support_conversation):
        raise SupportConversationError(
            "This conversation is assigned to another agent.",
            code="conversation_assigned_elsewhere",
            status_code=403,
        )
    if support_conversation.status == SupportConversation.Status.CLOSED:
        raise SupportConversationError(
            "Closed conversations cannot receive new replies.",
            code="conversation_closed",
            status_code=409,
        )
    if context.agent and support_conversation.assigned_agent_id is None:
        support_conversation.assigned_agent = context.agent

    clean_text = _clean_message_text(text, required=False)
    attachment_ids = attachment_ids or []
    if not clean_text and not attachment_ids:
        raise SupportConversationError("Write a reply or add an attachment before sending.", code="empty_message")
    try:
        uploads = support_uploads_for_team(
            context=context,
            actor=actor,
            support_conversation=support_conversation,
            attachment_ids=attachment_ids,
            voice_note=voice_note,
        ) if attachment_ids else []
    except SupportMediaError as exc:
        raise SupportConversationError(exc.detail, code=exc.code, status_code=exc.status_code) from exc

    message = Message.objects.create(
        conversation=support_conversation.conversation,
        sender=actor,
        type=Message.MessageType.AUDIO if voice_note else Message.MessageType.TEXT,
        text=clean_text,
        metadata={"voice_note": True} if voice_note else {},
        delivery_status=Message.DeliveryStatus.SENT,
    )
    finalize_support_message_media(message=message, uploads=uploads, text=clean_text, voice_note=voice_note)
    _touch_chat_conversation(support_conversation.conversation, message)

    support_conversation.last_agent_message_at = message.created_at
    if support_conversation.first_response_at is None:
        support_conversation.first_response_at = message.created_at
    if support_conversation.status in {
        SupportConversation.Status.NEW,
        SupportConversation.Status.OPEN,
        SupportConversation.Status.WAITING_TEAM,
        SupportConversation.Status.RESOLVED,
    }:
        support_conversation.status = SupportConversation.Status.WAITING_CUSTOMER
        support_conversation.resolved_at = None
    support_conversation.save(
        update_fields=[
            "assigned_agent",
            "last_agent_message_at",
            "first_response_at",
            "status",
            "resolved_at",
            "updated_at",
        ]
    )
    on_team_message(support_conversation, message_at=message.created_at)
    _publish_message_event(
        support_conversation=support_conversation,
        message=message,
        sender_kind=context.role or "agent",
        display_name=_person_display_name(actor),
    )
    return message


@transaction.atomic
def mark_team_read(*, support_conversation: SupportConversation, user) -> SupportConversationReadState:
    last_message = (
        Message.objects.filter(conversation=support_conversation.conversation, is_deleted=False)
        .order_by("-created_at", "-id")
        .first()
    )
    state, _ = SupportConversationReadState.objects.select_for_update().get_or_create(
        support_conversation=support_conversation,
        user=user,
    )
    state.last_read_message = last_message
    state.last_read_at = timezone.now()
    state.save(update_fields=["last_read_message", "last_read_at", "updated_at"])
    return state


@transaction.atomic
def mark_visitor_read(*, support_conversation: SupportConversation) -> SupportConversation:
    last_team_message = (
        Message.objects.filter(
            conversation=support_conversation.conversation,
            sender__isnull=False,
            is_deleted=False,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    support_conversation = SupportConversation.objects.select_for_update().get(pk=support_conversation.pk)
    support_conversation.visitor_last_read_message = last_team_message
    support_conversation.visitor_last_read_at = timezone.now()
    support_conversation.save(
        update_fields=["visitor_last_read_message", "visitor_last_read_at", "updated_at"]
    )
    return support_conversation


def team_unread_count(support_conversation: SupportConversation, user) -> int:
    state = SupportConversationReadState.objects.filter(
        support_conversation=support_conversation,
        user=user,
    ).select_related("last_read_message").first()
    queryset = Message.objects.filter(
        conversation=support_conversation.conversation,
        is_deleted=False,
    ).exclude(sender=user)
    if state and state.last_read_message_id:
        queryset = queryset.filter(created_at__gt=state.last_read_message.created_at)
    return queryset.count()


def visitor_unread_count(support_conversation: SupportConversation) -> int:
    queryset = Message.objects.filter(
        conversation=support_conversation.conversation,
        sender__isnull=False,
        is_deleted=False,
    )
    if support_conversation.visitor_last_read_message_id:
        queryset = queryset.filter(created_at__gt=support_conversation.visitor_last_read_message.created_at)
    return queryset.count()


@transaction.atomic
def claim_conversation(*, context: SupportContext, support_conversation: SupportConversation) -> SupportConversation:
    if not context.agent or not context.agent.is_active:
        raise SupportConversationError("Only an active support agent can take a conversation.", code="not_agent", status_code=403)
    support_conversation = SupportConversation.objects.select_for_update().get(pk=support_conversation.pk)
    if support_conversation.assigned_agent_id not in {None, context.agent.id}:
        raise SupportConversationError(
            "Another agent already took this conversation.",
            code="already_assigned",
            status_code=409,
        )
    active_count = SupportConversation.objects.filter(
        assigned_agent=context.agent,
        status__in=[
            SupportConversation.Status.NEW,
            SupportConversation.Status.OPEN,
            SupportConversation.Status.WAITING_CUSTOMER,
            SupportConversation.Status.WAITING_TEAM,
        ],
    ).exclude(pk=support_conversation.pk).count()
    if active_count >= context.agent.max_active_conversations:
        raise SupportConversationError(
            "You have reached your active-conversation capacity.",
            code="agent_capacity_reached",
            status_code=409,
        )
    support_conversation.assigned_agent = context.agent
    if support_conversation.status == SupportConversation.Status.NEW:
        support_conversation.status = SupportConversation.Status.OPEN
    support_conversation.save(update_fields=["assigned_agent", "status", "updated_at"])
    support_conversation = SupportConversation.objects.select_related("website", "visitor").get(pk=support_conversation.pk)
    record_audit_event(
        account=context.account,
        website=support_conversation.website,
        support_conversation=support_conversation,
        actor=context.agent.user,
        action="conversation.claimed",
        target_type="support_conversation",
        target_id=support_conversation.id,
        summary=f"{person_name(context.agent.user)} took the conversation.",
        metadata={"assigned_agent_id": str(context.agent.id)},
    )
    _publish_conversation_event(support_conversation=support_conversation, reason="claimed")
    return support_conversation


_ASSIGNMENT_UNSET = object()


@transaction.atomic
def update_conversation_workflow(
    *,
    context: SupportContext,
    support_conversation: SupportConversation,
    status: str | None = None,
    priority: str | None = None,
    assigned_agent_id=_ASSIGNMENT_UNSET,
    follow_up_at=_ASSIGNMENT_UNSET,
    follow_up_note: str = "",
) -> SupportConversation:
    support_conversation = SupportConversation.objects.select_for_update().select_related(
        "website", "assigned_agent", "assigned_agent__user"
    ).get(pk=support_conversation.pk)
    before = {
        "status": support_conversation.status,
        "priority": support_conversation.priority,
        "assigned_agent_id": str(support_conversation.assigned_agent_id or ""),
        "assigned_agent_name": person_name(support_conversation.assigned_agent.user) if support_conversation.assigned_agent else "",
    }
    actor_can_manage_assignment = context.role == "owner" or bool(context.agent and context.agent.can_assign_conversations)

    if assigned_agent_id is not _ASSIGNMENT_UNSET:
        if not actor_can_manage_assignment:
            raise SupportConversationError(
                "You cannot assign support conversations.", code="assignment_denied", status_code=403
            )
        if assigned_agent_id is None or str(assigned_agent_id) == "":
            support_conversation.assigned_agent = None
        else:
            assigned_agent = SupportAgent.objects.filter(
                pk=assigned_agent_id,
                support_account=support_conversation.website.support_account,
                is_active=True,
                website_assignments__website=support_conversation.website,
            ).distinct().first()
            if not assigned_agent:
                raise SupportConversationError(
                    "The selected agent cannot access this website.",
                    code="invalid_agent",
                    status_code=400,
                )
            support_conversation.assigned_agent = assigned_agent

    if status is not None:
        if context.role != "owner" and not can_reply(context, support_conversation):
            raise SupportConversationError(
                "You cannot change this conversation status.", code="status_denied", status_code=403
            )
        support_conversation.status = status
        now = timezone.now()
        if status == SupportConversation.Status.RESOLVED:
            support_conversation.resolved_at = now
            support_conversation.closed_at = None
        elif status == SupportConversation.Status.CLOSED:
            support_conversation.closed_at = now
        else:
            support_conversation.resolved_at = None
            support_conversation.closed_at = None

    if priority is not None:
        if context.role != "owner" and not can_reply(context, support_conversation):
            raise SupportConversationError(
                "You cannot change this conversation priority.", code="priority_denied", status_code=403
            )
        support_conversation.priority = priority

    support_conversation.save(
        update_fields=["assigned_agent", "status", "priority", "resolved_at", "closed_at", "updated_at"]
    )
    actor = context.account.owner if context.role == "owner" else context.agent.user
    if follow_up_at is not _ASSIGNMENT_UNSET:
        if context.role != "owner" and not can_reply(context, support_conversation):
            raise SupportConversationError(
                "You cannot manage follow-ups for this conversation.",
                code="follow_up_denied",
                status_code=403,
            )
        try:
            set_follow_up(
                support_conversation,
                actor=actor,
                follow_up_at=follow_up_at,
                note=follow_up_note,
            )
        except SupportServiceConfigurationError as exc:
            raise SupportConversationError(str(exc), code="invalid_follow_up", status_code=400) from exc
    if priority is not None and before["priority"] != support_conversation.priority:
        recalculate_active_targets(support_conversation)
    if status in {SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED}:
        resolve_inactive_alerts(support_conversation)
    if status == SupportConversation.Status.RESOLVED and before["status"] != SupportConversation.Status.RESOLVED:
        from apps.support.feedback_services import maybe_request_csat_on_resolve

        maybe_request_csat_on_resolve(support_conversation, actor=actor)
    support_conversation = SupportConversation.objects.select_related(
        "website", "visitor", "assigned_agent", "assigned_agent__user"
    ).get(pk=support_conversation.pk)
    after = {
        "status": support_conversation.status,
        "priority": support_conversation.priority,
        "assigned_agent_id": str(support_conversation.assigned_agent_id or ""),
        "assigned_agent_name": person_name(support_conversation.assigned_agent.user) if support_conversation.assigned_agent else "",
    }
    changes = {key: {"from": before[key], "to": after[key]} for key in before if before[key] != after[key]}
    if follow_up_at is not _ASSIGNMENT_UNSET:
        record_audit_event(
            account=context.account,
            website=support_conversation.website,
            support_conversation=support_conversation,
            actor=actor,
            action="conversation.follow_up_changed",
            target_type="support_conversation",
            target_id=support_conversation.id,
            summary=(
                f"{person_name(actor)} scheduled a follow-up."
                if follow_up_at is not None
                else f"{person_name(actor)} cleared the follow-up."
            ),
            metadata={
                "follow_up_at": support_conversation.follow_up_at.isoformat() if support_conversation.follow_up_at else None,
                "note": support_conversation.follow_up_note,
            },
        )
    if changes:
        labels = []
        if "assigned_agent_id" in changes:
            labels.append("assignment")
        if "status" in changes:
            labels.append("status")
        if "priority" in changes:
            labels.append("priority")
        record_audit_event(
            account=context.account,
            website=support_conversation.website,
            support_conversation=support_conversation,
            actor=actor,
            action="conversation.workflow_changed",
            target_type="support_conversation",
            target_id=support_conversation.id,
            summary=f"{person_name(actor)} changed {', '.join(labels)}.",
            metadata={"changes": changes},
        )
    _publish_conversation_event(support_conversation=support_conversation, reason="workflow_changed")
    return support_conversation
