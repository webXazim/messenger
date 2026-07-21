from __future__ import annotations

from datetime import timedelta
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.support.models import (
    SupportAgent,
    SupportConversation,
    SupportConversationFollower,
    SupportConversationTransfer,
    SupportInternalNoteMention,
)
from apps.support.realtime import publish_support_event
from apps.support.workflow_services import person_name, record_audit_event
from apps.support.service_operations import apply_status_sla_policy


class SupportLifecycleError(Exception):
    def __init__(self, message, *, code="invalid_lifecycle", status_code=400):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


ACTIVE = {
    SupportConversation.Status.NEW,
    SupportConversation.Status.OPEN,
    SupportConversation.Status.WAITING_CUSTOMER,
    SupportConversation.Status.WAITING_TEAM,
}
TRANSITIONS = {
    SupportConversation.Status.NEW: {SupportConversation.Status.OPEN, SupportConversation.Status.WAITING_TEAM, SupportConversation.Status.RESOLVED, SupportConversation.Status.SNOOZED},
    SupportConversation.Status.OPEN: {SupportConversation.Status.WAITING_CUSTOMER, SupportConversation.Status.WAITING_TEAM, SupportConversation.Status.RESOLVED, SupportConversation.Status.SNOOZED},
    SupportConversation.Status.WAITING_CUSTOMER: {SupportConversation.Status.OPEN, SupportConversation.Status.RESOLVED, SupportConversation.Status.SNOOZED},
    SupportConversation.Status.WAITING_TEAM: {SupportConversation.Status.OPEN, SupportConversation.Status.RESOLVED, SupportConversation.Status.SNOOZED},
    SupportConversation.Status.SNOOZED: {SupportConversation.Status.OPEN, SupportConversation.Status.WAITING_CUSTOMER, SupportConversation.Status.WAITING_TEAM, SupportConversation.Status.RESOLVED},
    SupportConversation.Status.RESOLVED: {SupportConversation.Status.OPEN, SupportConversation.Status.CLOSED},
    SupportConversation.Status.CLOSED: set(),
}


def _actor(context):
    return context.account.owner if context.role == "owner" else context.agent.user


def _can_manage(context, conversation):
    if context.role == "owner":
        return True
    return bool(context.agent and context.agent.is_active and (
        context.agent.can_assign_conversations or conversation.assigned_agent_id == context.agent.id
    ))


def _publish(conversation, event_name, data):
    publish_support_event(
        event_name=event_name,
        website_id=conversation.website_id,
        user_ids=[u for u in conversation.followers.values_list("user_id", flat=True)],
        data={"version": 1, "conversation_id": str(conversation.id), **data},
    )


@transaction.atomic
def transition_conversation(*, context, conversation, target_status, resolution_reason="", closure_reason="", force=False):
    conversation = SupportConversation.objects.select_for_update().select_related("website").get(pk=conversation.pk)
    if not _can_manage(context, conversation):
        raise SupportLifecycleError("You cannot change this conversation.", code="lifecycle_denied", status_code=403)
    current = conversation.status
    if current == target_status:
        return conversation
    allowed = TRANSITIONS.get(current, set())
    if target_status not in allowed and not (force and context.role == "owner"):
        raise SupportLifecycleError(f"Cannot move conversation from {current} to {target_status}.", code="invalid_transition", status_code=409)
    if current == SupportConversation.Status.CLOSED and context.role != "owner":
        raise SupportLifecycleError("Only the owner may reopen a closed conversation.", code="closed_protected", status_code=403)

    now = timezone.now()
    actor = _actor(context)
    conversation.status = target_status
    conversation.revision_number = F("revision_number") + 1
    if target_status == SupportConversation.Status.RESOLVED:
        if not (resolution_reason or "").strip():
            raise SupportLifecycleError("Select a resolution reason.", code="resolution_reason_required", status_code=400)
        conversation.resolution_reason = resolution_reason.strip()[:120]
        conversation.resolved_at = now
        conversation.closed_at = None
        conversation.snoozed_until = None
        conversation.snoozed_by = None
    elif target_status == SupportConversation.Status.CLOSED:
        conversation.closure_reason = (closure_reason or "").strip()[:120]
        conversation.closed_at = now
        conversation.snoozed_until = None
        conversation.snoozed_by = None
    else:
        if current in {SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED}:
            conversation.reopened_at = now
            conversation.reopen_count = F("reopen_count") + 1
        conversation.resolved_at = None
        conversation.closed_at = None
        if target_status != SupportConversation.Status.SNOOZED:
            conversation.snoozed_until = None
            conversation.snoozed_by = None
    conversation.save()
    conversation.refresh_from_db()
    conversation = apply_status_sla_policy(
        conversation, previous_status=current, now=now
    )
    conversation.refresh_from_db()

    record_audit_event(
        account=context.account, website=conversation.website, support_conversation=conversation,
        actor=actor, action="conversation.lifecycle_changed", target_type="support_conversation",
        target_id=conversation.id, summary=f"{person_name(actor)} changed conversation status.",
        metadata={"from": current, "to": target_status, "revision": conversation.revision_number,
                  "resolution_reason": conversation.resolution_reason, "closure_reason": conversation.closure_reason},
    )
    _publish(conversation, "support.status.changed", {"from": current, "to": target_status, "revision": conversation.revision_number})
    return conversation


@transaction.atomic
def snooze_conversation(*, context, conversation, until):
    if until <= timezone.now():
        raise SupportLifecycleError("Snooze time must be in the future.")
    conversation = SupportConversation.objects.select_for_update().select_related("website").get(pk=conversation.pk)
    if not _can_manage(context, conversation):
        raise SupportLifecycleError("You cannot snooze this conversation.", code="snooze_denied", status_code=403)
    if conversation.status in {SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED}:
        raise SupportLifecycleError("Resolved or closed conversations cannot be snoozed.", code="invalid_snooze", status_code=409)
    actor = _actor(context)
    previous = conversation.status if conversation.status != SupportConversation.Status.SNOOZED else (conversation.previous_status or SupportConversation.Status.OPEN)
    conversation.previous_status = previous
    conversation.status = SupportConversation.Status.SNOOZED
    conversation.snoozed_until = until
    conversation.snoozed_by = actor
    conversation.revision_number = F("revision_number") + 1
    conversation.save()
    conversation.refresh_from_db()
    conversation = apply_status_sla_policy(
        conversation, previous_status=previous, now=timezone.now()
    )
    conversation.refresh_from_db()
    record_audit_event(
        account=context.account, website=conversation.website, support_conversation=conversation,
        actor=actor, action="conversation.snoozed", summary=f"{person_name(actor)} snoozed the conversation.",
        metadata={"until": until.isoformat(), "previous_status": previous},
    )
    _publish(conversation, "support.snoozed", {"until": until.isoformat(), "previous_status": previous, "revision": conversation.revision_number})
    return conversation


@transaction.atomic
def wake_due_snoozed_conversations(now=None):
    now = now or timezone.now()
    rows = list(SupportConversation.objects.select_for_update(skip_locked=True).filter(
        status=SupportConversation.Status.SNOOZED, snoozed_until__lte=now
    )[:500])
    for conversation in rows:
        target = conversation.previous_status if conversation.previous_status in ACTIVE else SupportConversation.Status.OPEN
        conversation.status = target
        conversation.snoozed_until = None
        conversation.snoozed_by = None
        conversation.revision_number = F("revision_number") + 1
        conversation.save()
        conversation.refresh_from_db()
        apply_status_sla_policy(
            conversation,
            previous_status=SupportConversation.Status.SNOOZED,
            now=now,
        )
        publish_support_event(
            event_name="support.snooze.woke",
            website_id=conversation.website_id,
            data={"version": 1, "conversation_id": str(conversation.id), "status": target},
        )
    return len(rows)


@transaction.atomic
def set_following(*, context, conversation, user, following):
    if user != _actor(context) and context.role != "owner":
        raise SupportLifecycleError("You cannot change another user's follow state.", code="follow_denied", status_code=403)
    if following:
        SupportConversationFollower.objects.get_or_create(
            support_conversation=conversation, user=user, defaults={"added_by": _actor(context)}
        )
    else:
        SupportConversationFollower.objects.filter(support_conversation=conversation, user=user).delete()
    _publish(conversation, "support.followers.changed", {"user_id": str(user.id), "following": bool(following)})
    return following


@transaction.atomic
def transfer_conversation(*, context, conversation, to_agent=None, to_team=None, note=""):
    if context.role != "owner" and not (context.agent and context.agent.can_assign_conversations):
        raise SupportLifecycleError("You cannot transfer conversations.", code="transfer_denied", status_code=403)
    conversation = SupportConversation.objects.select_for_update(of=("self",)).select_related("website", "assigned_agent", "assigned_team").get(pk=conversation.pk)
    if to_agent and (to_agent.support_account_id != context.account.id or not to_agent.is_active):
        raise SupportLifecycleError("The target agent is unavailable.", code="invalid_transfer_agent")
    if to_team and (to_team.support_account_id != context.account.id or not to_team.is_active):
        raise SupportLifecycleError("The target team is unavailable.", code="invalid_transfer_team")
    old_agent, old_team = conversation.assigned_agent, conversation.assigned_team
    conversation.assigned_agent = to_agent
    conversation.assigned_team = to_team
    conversation.assigned_at = timezone.now()
    conversation.assignment_trigger = "transfer"
    conversation.revision_number = F("revision_number") + 1
    conversation.save()
    conversation.refresh_from_db()
    SupportConversationTransfer.objects.create(
        support_conversation=conversation, from_agent=old_agent, to_agent=to_agent,
        from_team=old_team, to_team=to_team, transferred_by=_actor(context), note=(note or "").strip()[:2000],
    )
    record_audit_event(
        account=context.account, website=conversation.website, support_conversation=conversation,
        actor=_actor(context), action="conversation.transferred", summary=f"{person_name(_actor(context))} transferred the conversation.",
        metadata={"from_agent_id": str(old_agent.id) if old_agent else None, "to_agent_id": str(to_agent.id) if to_agent else None,
                  "from_team_id": str(old_team.id) if old_team else None, "to_team_id": str(to_team.id) if to_team else None, "note": (note or "")[:2000]},
    )
    _publish(conversation, "support.assignment.changed", {"agent_id": str(to_agent.id) if to_agent else None, "team_id": str(to_team.id) if to_team else None, "revision": conversation.revision_number})
    return conversation


def viewer_heartbeat(*, conversation, user_id, display_name, ttl=90):
    key = f"support:conversation-viewers:{conversation.id}"
    viewers = cache.get(key, {})
    now = timezone.now().timestamp()
    viewers = {str(k): v for k, v in viewers.items() if float(v.get("expires", 0)) > now}
    viewers[str(user_id)] = {"user_id": str(user_id), "display_name": display_name, "expires": now + ttl}
    cache.set(key, viewers, timeout=ttl + 10)
    publish_support_event(
        event_name="support.viewer.presence",
        website_id=conversation.website_id,
        data={"version": 1, "conversation_id": str(conversation.id), "viewers": list(viewers.values())},
    )
    return list(viewers.values())


def current_viewers(conversation):
    key = f"support:conversation-viewers:{conversation.id}"
    viewers = cache.get(key, {})
    now = timezone.now().timestamp()
    return [v for v in viewers.values() if float(v.get("expires", 0)) > now]
