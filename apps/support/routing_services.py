from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from django.db import transaction
from django.db.models import Count, Q, F
from django.utils import timezone

from apps.support.models import (
    SupportAgent, SupportConversation, SupportRoutingCursor, SupportRoutingPolicy,
    SupportServiceAlert, SupportWebsiteTeam,
)
from apps.support.workflow_services import person_name, record_audit_event, publish_private_conversation_refresh

ACTIVE_STATUSES = [
    SupportConversation.Status.NEW, SupportConversation.Status.OPEN,
    SupportConversation.Status.WAITING_CUSTOMER, SupportConversation.Status.WAITING_TEAM,
]

@dataclass(frozen=True)
class RoutingResult:
    assigned: bool
    agent_id: str | None = None
    team_id: str | None = None
    reason: str = ""


def active_conversation_count(agent: SupportAgent, *, exclude_id=None) -> int:
    qs = SupportConversation.objects.filter(assigned_agent=agent, status__in=ACTIVE_STATUSES)
    if exclude_id: qs = qs.exclude(pk=exclude_id)
    return qs.count()


def _default_team(website):
    assignment = SupportWebsiteTeam.objects.filter(website=website, team__is_active=True).select_related("team").order_by("-is_default", "created_at").first()
    return assignment.team if assignment else None


def _eligible_agents(*, conversation, team=None, enforce_capacity=True):
    queryset = SupportAgent.objects.filter(
        support_account=conversation.website.support_account,
        is_active=True,
        user__is_active=True,
        availability=SupportAgent.Availability.AVAILABLE,
        website_assignments__website=conversation.website,
    )
    if team is not None:
        queryset = queryset.filter(team_memberships__team=team)
    # Lock plain agent rows first. PostgreSQL does not allow FOR UPDATE on the
    # grouped query produced by Count annotations.
    candidates = list(queryset.select_for_update().distinct().order_by("joined_at", "id"))
    result = []
    for agent in candidates:
        agent.active_count = active_conversation_count(agent, exclude_id=conversation.id)
        if not enforce_capacity or agent.active_count < agent.max_active_conversations:
            result.append(agent)
    return result


def _choose_agent(policy, candidates):
    if not candidates: return None
    if policy.mode == SupportRoutingPolicy.Mode.LEAST_BUSY:
        return sorted(candidates, key=lambda a: (a.active_count, a.joined_at, str(a.id)))[0]
    cursor, _ = SupportRoutingCursor.objects.select_for_update().get_or_create(policy=policy)
    ids = [a.id for a in candidates]
    index = 0
    if cursor.last_assigned_agent_id in ids:
        index = (ids.index(cursor.last_assigned_agent_id) + 1) % len(ids)
    chosen = candidates[index]
    cursor.last_assigned_agent = chosen; cursor.assignment_count += 1
    cursor.save(update_fields=["last_assigned_agent", "assignment_count", "updated_at"])
    return chosen


@transaction.atomic
def assign_support_conversation(*, conversation, trigger="automatic", actor=None, force=False) -> RoutingResult:
    conversation = SupportConversation.objects.select_for_update().select_related("website", "website__support_account", "assigned_agent", "assigned_team").get(pk=conversation.pk)
    if conversation.assigned_agent_id and not force:
        return RoutingResult(True, str(conversation.assigned_agent_id), str(conversation.assigned_team_id) if conversation.assigned_team_id else None, "already_assigned")
    policy, _ = SupportRoutingPolicy.objects.select_for_update().get_or_create(website=conversation.website)
    if not policy.enabled or policy.mode == SupportRoutingPolicy.Mode.MANUAL:
        return RoutingResult(False, reason="manual_routing")
    team = _default_team(conversation.website)
    candidates = _eligible_agents(conversation=conversation, team=team, enforce_capacity=True)
    if not candidates and policy.overflow_behavior == SupportRoutingPolicy.Overflow.LEAST_BUSY:
        candidates = _eligible_agents(conversation=conversation, team=team, enforce_capacity=False)
    chosen = _choose_agent(policy, candidates)
    if not chosen:
        record_audit_event(account=conversation.website.support_account, website=conversation.website, support_conversation=conversation, actor=actor, action="conversation.routing_unassigned", target_type="support_conversation", target_id=conversation.id, summary="Automatic routing left the conversation unassigned.", metadata={"trigger": trigger, "mode": policy.mode, "overflow": policy.overflow_behavior, "team_id": str(team.id) if team else None})
        if policy.overflow_behavior == SupportRoutingPolicy.Overflow.NOTIFY_OWNER:
            SupportServiceAlert.objects.get_or_create(
                dedupe_key=f"routing:{conversation.id}",
                defaults={
                    "support_account": conversation.website.support_account,
                    "website": conversation.website,
                    "support_conversation": conversation,
                    "recipient": conversation.website.support_account.owner,
                    "kind": SupportServiceAlert.Kind.ROUTING_UNASSIGNED,
                    "due_at": timezone.now(),
                    "metadata": {"trigger": trigger, "team_id": str(team.id) if team else None},
                },
            )
        return RoutingResult(False, team_id=str(team.id) if team else None, reason="no_eligible_agent")
    conversation.assigned_agent = chosen; conversation.assigned_team = team; conversation.assigned_at = timezone.now(); conversation.assignment_trigger = trigger
    if conversation.status == SupportConversation.Status.NEW: conversation.status = SupportConversation.Status.OPEN
    conversation.save(update_fields=["assigned_agent", "assigned_team", "assigned_at", "assignment_trigger", "status", "updated_at"])
    record_audit_event(account=conversation.website.support_account, website=conversation.website, support_conversation=conversation, actor=actor, action="conversation.auto_assigned", target_type="support_conversation", target_id=conversation.id, summary=f"Conversation assigned to {person_name(chosen.user)}.", metadata={"agent_id": str(chosen.id), "team_id": str(team.id) if team else None, "trigger": trigger, "mode": policy.mode})
    publish_private_conversation_refresh(conversation, "automatic_assignment")
    return RoutingResult(True, str(chosen.id), str(team.id) if team else None, "assigned")


@transaction.atomic
def reassign_offline_conversations(*, limit=200) -> int:
    now = timezone.now(); count = 0
    qs = SupportConversation.objects.filter(status__in=ACTIVE_STATUSES, assigned_agent__isnull=False).select_related("assigned_agent", "website", "website__routing_policy").order_by("updated_at")[:limit]
    for item in qs:
        try: policy = item.website.routing_policy
        except SupportRoutingPolicy.DoesNotExist: continue
        if not policy.enabled or policy.mode == SupportRoutingPolicy.Mode.MANUAL or policy.offline_reassignment_minutes <= 0: continue
        agent = item.assigned_agent
        if agent.is_active and agent.user.is_active and agent.availability != SupportAgent.Availability.OFFLINE: continue
        if item.assigned_at and item.assigned_at > now - timedelta(minutes=policy.offline_reassignment_minutes): continue
        locked = SupportConversation.objects.select_for_update().get(pk=item.pk)
        locked.assigned_agent = None; locked.assigned_at = None; locked.assignment_trigger = "offline_reassignment"
        locked.save(update_fields=["assigned_agent", "assigned_at", "assignment_trigger", "updated_at"])
        result = assign_support_conversation(conversation=locked, trigger="offline_reassignment", force=True)
        if result.assigned: count += 1
    return count
