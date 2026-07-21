from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.support.models import (
    SupportConversation,
    SupportServiceAlert,
    SupportServiceSettings,
    SupportSlaPolicy,
    SupportTeamMembership,
    default_first_response_targets,
    default_next_response_targets,
    default_resolution_targets,
    default_support_business_hours,
)
from apps.support.realtime import publish_support_event
from apps.support.workflow_services import record_audit_event

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
TARGET_TYPES = ("first_response", "next_response", "resolution")
PRIORITIES = tuple(value for value, _ in SupportConversation.Priority.choices)
OPEN_STATUSES = (
    SupportConversation.Status.NEW,
    SupportConversation.Status.OPEN,
    SupportConversation.Status.WAITING_CUSTOMER,
    SupportConversation.Status.WAITING_TEAM,
    SupportConversation.Status.SNOOZED,
)


class SupportServiceConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class EffectiveSlaPolicy:
    timezone: str
    business_hours_enabled: bool
    business_hours: dict
    first_response_targets: dict
    next_response_targets: dict
    resolution_targets: dict
    due_soon_minutes: int
    default_follow_up_minutes: int
    alert_owner: bool
    alert_assigned_agent: bool
    pause_while_waiting_customer: bool
    pause_resolution_while_snoozed: bool
    escalate_on_breach: bool
    escalation_team_id: object | None
    source: str = "account"


def _merged_targets(base: dict, override: dict | None) -> dict:
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if key in PRIORITIES and value not in (None, ""):
            merged[key] = int(value)
    return merged


def effective_sla_policy(conversation: SupportConversation) -> EffectiveSlaPolicy:
    """Resolve account defaults with team override, then website override."""

    base = service_settings_for(conversation.website.support_account)
    values = {
        "timezone": base.timezone,
        "business_hours_enabled": base.business_hours_enabled,
        "business_hours": base.business_hours,
        "first_response_targets": base.first_response_targets,
        "next_response_targets": base.next_response_targets,
        "resolution_targets": base.resolution_targets,
        "due_soon_minutes": base.due_soon_minutes,
        "default_follow_up_minutes": base.default_follow_up_minutes,
        "alert_owner": base.alert_owner,
        "alert_assigned_agent": base.alert_assigned_agent,
        "pause_while_waiting_customer": base.pause_while_waiting_customer,
        "pause_resolution_while_snoozed": base.pause_resolution_while_snoozed,
        "escalate_on_breach": base.escalate_on_breach,
        "escalation_team_id": base.escalation_team_id,
        "source": "account",
    }
    scope_q = Q(website=conversation.website)
    if conversation.assigned_team_id:
        scope_q |= Q(team_id=conversation.assigned_team_id)
    policies = list(
        SupportSlaPolicy.objects.filter(
            support_account=conversation.website.support_account,
            is_active=True,
        ).filter(scope_q).select_related("website", "team", "escalation_team")
    )
    policies.sort(key=lambda item: 1 if item.team_id else 2)
    for policy in policies:
        values["first_response_targets"] = _merged_targets(
            values["first_response_targets"], policy.first_response_targets
        )
        values["next_response_targets"] = _merged_targets(
            values["next_response_targets"], policy.next_response_targets
        )
        values["resolution_targets"] = _merged_targets(
            values["resolution_targets"], policy.resolution_targets
        )
        for field in (
            "due_soon_minutes",
            "pause_while_waiting_customer",
            "pause_resolution_while_snoozed",
            "alert_owner",
            "alert_assigned_agent",
            "escalate_on_breach",
        ):
            value = getattr(policy, field)
            if value is not None:
                values[field] = value
        if policy.escalation_team_id:
            values["escalation_team_id"] = policy.escalation_team_id
        values["source"] = "website" if policy.website_id else "team"
    return EffectiveSlaPolicy(**values)


def service_settings_for(account) -> SupportServiceSettings:
    try:
        return account.service_settings
    except SupportServiceSettings.DoesNotExist:
        settings_obj, _ = SupportServiceSettings.objects.get_or_create(support_account=account)
        return settings_obj


def validate_timezone_name(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise SupportServiceConfigurationError("Choose a valid timezone.")
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise SupportServiceConfigurationError("Choose a valid IANA timezone.") from exc
    return normalized


def _parse_clock(value: str) -> time:
    try:
        parsed = datetime.strptime(str(value), "%H:%M").time()
    except (TypeError, ValueError) as exc:
        raise SupportServiceConfigurationError("Business-hour times must use HH:MM.") from exc
    return parsed


def normalize_business_hours(value) -> dict:
    if not isinstance(value, dict):
        raise SupportServiceConfigurationError("Business hours must contain all seven weekdays.")
    normalized: dict[str, dict] = {}
    for weekday in WEEKDAYS:
        day = value.get(weekday)
        if not isinstance(day, dict):
            raise SupportServiceConfigurationError(f"Configure business hours for {weekday.title()}.")
        enabled = bool(day.get("enabled"))
        start = str(day.get("start") or "09:00")
        end = str(day.get("end") or "17:00")
        start_clock = _parse_clock(start)
        end_clock = _parse_clock(end)
        if enabled and end_clock <= start_clock:
            raise SupportServiceConfigurationError(
                f"{weekday.title()} closing time must be later than opening time."
            )
        normalized[weekday] = {
            "enabled": enabled,
            "start": start_clock.strftime("%H:%M"),
            "end": end_clock.strftime("%H:%M"),
        }
    if not any(day["enabled"] for day in normalized.values()):
        raise SupportServiceConfigurationError("Enable at least one business day.")
    return normalized


def normalize_targets(value, *, fallback: dict) -> dict[str, int]:
    if not isinstance(value, dict):
        raise SupportServiceConfigurationError("Service targets must be provided for every priority.")
    normalized = {}
    for priority in PRIORITIES:
        raw = value.get(priority, fallback[priority])
        try:
            minutes = int(raw)
        except (TypeError, ValueError) as exc:
            raise SupportServiceConfigurationError("Service targets must be whole minutes.") from exc
        if minutes < 1 or minutes > 43200:
            raise SupportServiceConfigurationError("Service targets must be between 1 minute and 30 days.")
        normalized[priority] = minutes
    return normalized


def normalize_service_settings_payload(payload: dict) -> dict:
    return {
        "timezone": validate_timezone_name(payload.get("timezone", "UTC")),
        "business_hours_enabled": bool(payload.get("business_hours_enabled", True)),
        "business_hours": normalize_business_hours(payload.get("business_hours") or default_support_business_hours()),
        "first_response_targets": normalize_targets(
            payload.get("first_response_targets") or default_first_response_targets(),
            fallback=default_first_response_targets(),
        ),
        "next_response_targets": normalize_targets(
            payload.get("next_response_targets") or default_next_response_targets(),
            fallback=default_next_response_targets(),
        ),
        "resolution_targets": normalize_targets(
            payload.get("resolution_targets") or default_resolution_targets(),
            fallback=default_resolution_targets(),
        ),
        "due_soon_minutes": max(1, min(1440, int(payload.get("due_soon_minutes", 15)))),
        "default_follow_up_minutes": max(1, min(43200, int(payload.get("default_follow_up_minutes", 1440)))),
        "alert_owner": bool(payload.get("alert_owner", True)),
        "alert_assigned_agent": bool(payload.get("alert_assigned_agent", True)),
        "pause_while_waiting_customer": bool(payload.get("pause_while_waiting_customer", True)),
        "pause_resolution_while_snoozed": bool(payload.get("pause_resolution_while_snoozed", True)),
        "escalate_on_breach": bool(payload.get("escalate_on_breach", True)),
        "escalation_team": payload.get("escalation_team"),
    }


def _aware_local(day, clock: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, clock, tzinfo=zone)


def add_service_minutes(start_at: datetime, minutes: int, settings_obj: SupportServiceSettings) -> datetime:
    minutes = max(0, int(minutes))
    if minutes == 0:
        return start_at
    if not settings_obj.business_hours_enabled:
        return start_at + timedelta(minutes=minutes)

    zone = ZoneInfo(validate_timezone_name(settings_obj.timezone))
    schedule = normalize_business_hours(settings_obj.business_hours)
    current = start_at.astimezone(zone)
    remaining = minutes

    # The configured maximum target is 30 days. This guard still allows long
    # weekends and sparse schedules without an unbounded loop.
    for _ in range(370):
        day_config = schedule[WEEKDAYS[current.weekday()]]
        if day_config["enabled"]:
            opens = _aware_local(current.date(), _parse_clock(day_config["start"]), zone)
            closes = _aware_local(current.date(), _parse_clock(day_config["end"]), zone)
            if current < opens:
                current = opens
            if opens <= current < closes:
                available = max(0, int((closes - current).total_seconds() // 60))
                if remaining <= available:
                    return (current + timedelta(minutes=remaining)).astimezone(datetime_timezone.utc)
                remaining -= available
        current = _aware_local(current.date() + timedelta(days=1), time(0, 0), zone)

    raise SupportServiceConfigurationError("Business-hour schedule could not produce a service deadline.")


def service_minutes_between(start_at: datetime, end_at: datetime, settings_obj) -> int:
    """Count only minutes that consume SLA time between two instants."""

    if end_at <= start_at:
        return 0
    if not settings_obj.business_hours_enabled:
        return max(0, int((end_at - start_at).total_seconds() // 60))

    zone = ZoneInfo(validate_timezone_name(settings_obj.timezone))
    schedule = normalize_business_hours(settings_obj.business_hours)
    start_local = start_at.astimezone(zone)
    end_local = end_at.astimezone(zone)
    total = 0
    cursor_day = start_local.date()
    while cursor_day <= end_local.date():
        config = schedule[WEEKDAYS[cursor_day.weekday()]]
        if config["enabled"]:
            opens = _aware_local(cursor_day, _parse_clock(config["start"]), zone)
            closes = _aware_local(cursor_day, _parse_clock(config["end"]), zone)
            overlap_start = max(start_local, opens)
            overlap_end = min(end_local, closes)
            if overlap_end > overlap_start:
                total += int((overlap_end - overlap_start).total_seconds() // 60)
        cursor_day += timedelta(days=1)
    return max(0, total)


def target_minutes(settings_obj: SupportServiceSettings, target_type: str, priority: str) -> int:
    source = {
        "first_response": settings_obj.first_response_targets,
        "next_response": settings_obj.next_response_targets,
        "resolution": settings_obj.resolution_targets,
    }[target_type]
    fallback = {
        "first_response": default_first_response_targets(),
        "next_response": default_next_response_targets(),
        "resolution": default_resolution_targets(),
    }[target_type]
    return normalize_targets(source, fallback=fallback).get(priority, fallback["normal"])


def _deadline(settings_obj, target_type: str, priority: str, anchor: datetime | None):
    if not anchor:
        return None
    return add_service_minutes(anchor, target_minutes(settings_obj, target_type, priority), settings_obj)


def initialize_service_targets(conversation: SupportConversation, *, anchor: datetime | None = None, save: bool = True):
    settings_obj = effective_sla_policy(conversation)
    anchor = anchor or conversation.last_visitor_message_at or conversation.created_at
    changed = []
    if conversation.first_response_at is None and conversation.first_response_due_at is None:
        conversation.first_response_due_at = _deadline(settings_obj, "first_response", conversation.priority, anchor)
        changed.append("first_response_due_at")
    if conversation.resolution_due_at is None and conversation.status not in {
        SupportConversation.Status.RESOLVED,
        SupportConversation.Status.CLOSED,
    }:
        conversation.resolution_due_at = _deadline(settings_obj, "resolution", conversation.priority, anchor)
        changed.append("resolution_due_at")
    if save and changed:
        conversation.save(update_fields=[*changed, "updated_at"])
    return conversation


def on_visitor_message(conversation: SupportConversation, *, message_at: datetime):
    settings_obj = effective_sla_policy(conversation)
    if conversation.sla_paused_at:
        resume_sla(conversation, resumed_at=message_at)
        conversation.refresh_from_db()
    changed = []
    if conversation.first_response_at is None:
        if conversation.first_response_due_at is None:
            conversation.first_response_due_at = _deadline(
                settings_obj, "first_response", conversation.priority, message_at
            )
            changed.append("first_response_due_at")
    else:
        conversation.next_response_due_at = _deadline(
            settings_obj, "next_response", conversation.priority, message_at
        )
        conversation.next_response_breached_at = None
        changed.extend(["next_response_due_at", "next_response_breached_at"])
    if conversation.resolution_due_at is None:
        conversation.resolution_due_at = _deadline(
            settings_obj, "resolution", conversation.priority, conversation.created_at
        )
        changed.append("resolution_due_at")
    if conversation.follow_up_at and conversation.follow_up_completed_at is None:
        conversation.follow_up_completed_at = message_at
        changed.append("follow_up_completed_at")
    if changed:
        conversation.save(update_fields=[*dict.fromkeys(changed), "updated_at"])
    resolve_inactive_alerts(conversation)
    return conversation


def on_team_message(conversation: SupportConversation, *, message_at: datetime):
    changed = []
    if conversation.next_response_due_at is not None:
        conversation.next_response_due_at = None
        changed.append("next_response_due_at")
    if conversation.follow_up_at and conversation.follow_up_completed_at is None:
        conversation.follow_up_completed_at = message_at
        changed.append("follow_up_completed_at")
    if changed:
        conversation.save(update_fields=[*changed, "updated_at"])
    resolve_inactive_alerts(conversation)
    return conversation


def recalculate_active_targets(conversation: SupportConversation):
    settings_obj = effective_sla_policy(conversation)
    changed = []
    if conversation.first_response_at is None:
        anchor = conversation.last_visitor_message_at or conversation.created_at
        conversation.first_response_due_at = _deadline(settings_obj, "first_response", conversation.priority, anchor)
        changed.append("first_response_due_at")
    if (
        conversation.first_response_at is not None
        and conversation.last_visitor_message_at
        and (
            conversation.last_agent_message_at is None
            or conversation.last_visitor_message_at > conversation.last_agent_message_at
        )
    ):
        conversation.next_response_due_at = _deadline(
            settings_obj, "next_response", conversation.priority, conversation.last_visitor_message_at
        )
        changed.append("next_response_due_at")
    if conversation.status not in {SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED}:
        conversation.resolution_due_at = _deadline(
            settings_obj, "resolution", conversation.priority, conversation.created_at
        )
        changed.append("resolution_due_at")
    conversation.sla_last_recalculated_at = timezone.now()
    changed.append("sla_last_recalculated_at")
    if changed:
        conversation.save(update_fields=[*dict.fromkeys(changed), "updated_at"])
    resolve_inactive_alerts(conversation)
    return conversation


@transaction.atomic
def pause_sla(conversation: SupportConversation, *, reason: str, paused_at=None):
    paused_at = paused_at or timezone.now()
    conversation = SupportConversation.objects.select_for_update(of=("self",)).select_related(
        "website", "website__support_account", "assigned_team"
    ).get(pk=conversation.pk)
    if conversation.sla_paused_at or conversation.status in {
        SupportConversation.Status.RESOLVED,
        SupportConversation.Status.CLOSED,
    }:
        return conversation
    conversation.sla_paused_at = paused_at
    conversation.sla_pause_reason = (reason or "manual")[:40]
    conversation.save(update_fields=["sla_paused_at", "sla_pause_reason", "updated_at"])
    resolve_inactive_alerts(conversation)
    publish_support_event(
        event_name="support.sla.paused",
        website_id=conversation.website_id,
        data={
            "version": 1,
            "conversation_id": str(conversation.id),
            "reason": conversation.sla_pause_reason,
            "paused_at": paused_at,
        },
    )
    return conversation


@transaction.atomic
def resume_sla(conversation: SupportConversation, *, resumed_at=None):
    resumed_at = resumed_at or timezone.now()
    conversation = SupportConversation.objects.select_for_update(of=("self",)).select_related(
        "website", "website__support_account", "assigned_team"
    ).get(pk=conversation.pk)
    if not conversation.sla_paused_at:
        return conversation
    policy = effective_sla_policy(conversation)
    paused_at = conversation.sla_paused_at
    consumed_minutes = service_minutes_between(paused_at, resumed_at, policy)
    wall_seconds = max(0, int((resumed_at - paused_at).total_seconds()))
    changed = []
    if consumed_minutes:
        for field in ("first_response_due_at", "next_response_due_at", "resolution_due_at"):
            due_at = getattr(conversation, field)
            if due_at:
                setattr(conversation, field, add_service_minutes(due_at, consumed_minutes, policy))
                changed.append(field)
    conversation.sla_total_paused_seconds += wall_seconds
    conversation.sla_paused_at = None
    conversation.sla_pause_reason = ""
    conversation.sla_last_recalculated_at = resumed_at
    changed.extend([
        "sla_total_paused_seconds", "sla_paused_at",
        "sla_pause_reason", "sla_last_recalculated_at",
    ])
    conversation.save(update_fields=[*dict.fromkeys(changed), "updated_at"])
    resolve_inactive_alerts(conversation)
    publish_support_event(
        event_name="support.sla.resumed",
        website_id=conversation.website_id,
        data={
            "version": 1,
            "conversation_id": str(conversation.id),
            "resumed_at": resumed_at,
            "paused_seconds": wall_seconds,
        },
    )
    return conversation


def apply_status_sla_policy(conversation: SupportConversation, *, previous_status: str, now=None):
    """Pause/resume SLA after a lifecycle status transition."""

    now = now or timezone.now()
    policy = effective_sla_policy(conversation)
    should_pause = (
        conversation.status == SupportConversation.Status.WAITING_CUSTOMER
        and policy.pause_while_waiting_customer
    ) or (
        conversation.status == SupportConversation.Status.SNOOZED
        and policy.pause_resolution_while_snoozed
    )
    if should_pause:
        reason = "waiting_customer" if conversation.status == SupportConversation.Status.WAITING_CUSTOMER else "snoozed"
        return pause_sla(conversation, reason=reason, paused_at=now)
    if conversation.sla_paused_at and conversation.status not in {
        SupportConversation.Status.WAITING_CUSTOMER,
        SupportConversation.Status.SNOOZED,
    }:
        return resume_sla(conversation, resumed_at=now)
    return conversation


def set_follow_up(conversation: SupportConversation, *, actor, follow_up_at, note: str = ""):
    note = (note or "").strip()
    if follow_up_at is None:
        conversation.follow_up_at = None
        conversation.follow_up_note = ""
        conversation.follow_up_created_by = None
        conversation.follow_up_completed_at = timezone.now()
    else:
        if follow_up_at <= timezone.now() - timedelta(minutes=1):
            raise SupportServiceConfigurationError("Choose a follow-up time in the future.")
        conversation.follow_up_at = follow_up_at
        conversation.follow_up_note = note[:255]
        conversation.follow_up_created_by = actor
        conversation.follow_up_completed_at = None
    conversation.save(update_fields=[
        "follow_up_at", "follow_up_note", "follow_up_created_by",
        "follow_up_completed_at", "updated_at",
    ])
    resolve_inactive_alerts(conversation)
    return conversation


def _active_deadlines(conversation: SupportConversation):
    if conversation.sla_paused_at:
        return []
    if conversation.status in {SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED}:
        return []
    deadlines = []
    if conversation.first_response_at is None and conversation.first_response_due_at:
        deadlines.append(("first_response", conversation.first_response_due_at))
    if (
        conversation.first_response_at is not None
        and conversation.next_response_due_at
        and conversation.last_visitor_message_at
        and (
            conversation.last_agent_message_at is None
            or conversation.last_visitor_message_at > conversation.last_agent_message_at
        )
    ):
        deadlines.append(("next_response", conversation.next_response_due_at))
    if conversation.resolution_due_at:
        deadlines.append(("resolution", conversation.resolution_due_at))
    return deadlines


def service_snapshot(conversation: SupportConversation, *, now=None, settings_obj=None) -> dict:
    now = now or timezone.now()
    settings_obj = effective_sla_policy(conversation)
    deadlines = _active_deadlines(conversation)
    active_target = min(deadlines, key=lambda item: item[1]) if deadlines else None
    overdue = [item for item in deadlines if item[1] <= now]
    due_soon_cutoff = now + timedelta(minutes=settings_obj.due_soon_minutes)
    due_soon = [item for item in deadlines if now < item[1] <= due_soon_cutoff]
    if conversation.sla_paused_at:
        state = "paused"
    elif overdue:
        state = "overdue"
    elif due_soon:
        state = "due_soon"
    elif deadlines:
        state = "on_track"
    elif conversation.status in {SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED}:
        state = "complete"
    else:
        state = "none"

    follow_up_due = bool(
        conversation.follow_up_at
        and conversation.follow_up_completed_at is None
        and conversation.follow_up_at <= now
    )
    return {
        "state": state,
        "active_target": active_target[0] if active_target else None,
        "active_due_at": active_target[1] if active_target else None,
        "is_overdue": bool(overdue),
        "is_due_soon": bool(due_soon),
        "overdue_targets": [name for name, _ in overdue],
        "first_response_due_at": conversation.first_response_due_at,
        "next_response_due_at": conversation.next_response_due_at,
        "resolution_due_at": conversation.resolution_due_at,
        "first_response_breached_at": conversation.first_response_breached_at,
        "next_response_breached_at": conversation.next_response_breached_at,
        "resolution_breached_at": conversation.resolution_breached_at,
        "paused_at": conversation.sla_paused_at,
        "pause_reason": conversation.sla_pause_reason,
        "total_paused_seconds": conversation.sla_total_paused_seconds,
        "last_recalculated_at": conversation.sla_last_recalculated_at,
        "escalated_at": conversation.sla_escalated_at,
        "policy_source": getattr(settings_obj, "source", "account"),
        "follow_up_at": conversation.follow_up_at,
        "follow_up_note": conversation.follow_up_note,
        "follow_up_due": follow_up_due,
        "follow_up_completed_at": conversation.follow_up_completed_at,
    }


def overdue_conversation_q(now=None) -> Q:
    now = now or timezone.now()
    return (
        Q(first_response_at__isnull=True, first_response_due_at__lte=now)
        | Q(next_response_due_at__lte=now)
        | Q(resolution_due_at__lte=now)
    )


def follow_up_due_q(now=None) -> Q:
    now = now or timezone.now()
    return Q(follow_up_at__lte=now, follow_up_completed_at__isnull=True)


def _alert_recipients(conversation: SupportConversation, settings_obj: SupportServiceSettings):
    user_ids = []
    if settings_obj.alert_owner:
        user_ids.append(conversation.website.support_account.owner_id)
    if settings_obj.alert_assigned_agent and conversation.assigned_agent_id:
        user_ids.append(conversation.assigned_agent.user_id)
    return list(dict.fromkeys(user_id for user_id in user_ids if user_id))


def _dedupe_key(conversation, recipient_id, kind, due_at):
    value = f"{conversation.id}:{recipient_id}:{kind}:{due_at.isoformat()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _alert_summary(kind: str, conversation: SupportConversation) -> str:
    visitor = conversation.visitor.name.strip() or "Website visitor"
    labels = {
        SupportServiceAlert.Kind.FIRST_RESPONSE_DUE_SOON: f"First response for {visitor} is due soon.",
        SupportServiceAlert.Kind.FIRST_RESPONSE_OVERDUE: f"First response for {visitor} is overdue.",
        SupportServiceAlert.Kind.NEXT_RESPONSE_DUE_SOON: f"Next response for {visitor} is due soon.",
        SupportServiceAlert.Kind.NEXT_RESPONSE_OVERDUE: f"Next response for {visitor} is overdue.",
        SupportServiceAlert.Kind.RESOLUTION_DUE_SOON: f"Resolution target for {visitor} is due soon.",
        SupportServiceAlert.Kind.RESOLUTION_OVERDUE: f"Resolution target for {visitor} is overdue.",
        SupportServiceAlert.Kind.FOLLOW_UP_DUE: f"Follow up with {visitor} now.",
    }
    return labels[kind]


def _kind_for(target: str, overdue: bool):
    return {
        ("first_response", False): SupportServiceAlert.Kind.FIRST_RESPONSE_DUE_SOON,
        ("first_response", True): SupportServiceAlert.Kind.FIRST_RESPONSE_OVERDUE,
        ("next_response", False): SupportServiceAlert.Kind.NEXT_RESPONSE_DUE_SOON,
        ("next_response", True): SupportServiceAlert.Kind.NEXT_RESPONSE_OVERDUE,
        ("resolution", False): SupportServiceAlert.Kind.RESOLUTION_DUE_SOON,
        ("resolution", True): SupportServiceAlert.Kind.RESOLUTION_OVERDUE,
    }[(target, overdue)]


def resolve_inactive_alerts(conversation: SupportConversation):
    active = set()
    now = timezone.now()
    settings_obj = effective_sla_policy(conversation)
    cutoff = now + timedelta(minutes=settings_obj.due_soon_minutes)
    for target, due_at in _active_deadlines(conversation):
        if due_at <= now:
            active.add((_kind_for(target, True), due_at))
        elif due_at <= cutoff:
            active.add((_kind_for(target, False), due_at))
    if conversation.follow_up_at and conversation.follow_up_completed_at is None and conversation.follow_up_at <= now:
        active.add((SupportServiceAlert.Kind.FOLLOW_UP_DUE, conversation.follow_up_at))

    valid_recipients = set(_alert_recipients(conversation, settings_obj))
    alerts = SupportServiceAlert.objects.filter(
        support_conversation=conversation,
        status__in=[SupportServiceAlert.Status.UNREAD, SupportServiceAlert.Status.READ],
    )
    resolved_at = timezone.now()
    for alert in alerts:
        if alert.kind == SupportServiceAlert.Kind.SLA_ESCALATED:
            continue
        if alert.recipient_id not in valid_recipients or (alert.kind, alert.due_at) not in active:
            alert.status = SupportServiceAlert.Status.RESOLVED
            alert.resolved_at = resolved_at
            alert.save(update_fields=["status", "resolved_at", "updated_at"])


def refresh_breach_markers(conversation: SupportConversation, *, now=None):
    now = now or timezone.now()
    changed = []
    new_breaches = []
    active = dict(_active_deadlines(conversation))
    for target, field in (
        ("first_response", "first_response_breached_at"),
        ("next_response", "next_response_breached_at"),
        ("resolution", "resolution_breached_at"),
    ):
        due_at = active.get(target)
        if due_at and due_at <= now and getattr(conversation, field) is None:
            setattr(conversation, field, now)
            changed.append(field)
            new_breaches.append(target)
    if changed:
        conversation.save(update_fields=[*changed, "updated_at"])
        for target in new_breaches:
            record_audit_event(
                account=conversation.website.support_account,
                website=conversation.website,
                support_conversation=conversation,
                actor=None,
                action="conversation.service_target_breached",
                target_type="support_conversation",
                target_id=conversation.id,
                summary=f"{target.replace('_', ' ').title()} target became overdue.",
                metadata={"target": target},
            )
    return new_breaches


def _escalation_recipients(conversation: SupportConversation, policy) -> list:
    user_ids = []
    if policy.escalation_team_id:
        user_ids.extend(
            SupportTeamMembership.objects.filter(
                team_id=policy.escalation_team_id,
                is_active=True,
                agent__is_active=True,
            ).values_list("agent__user_id", flat=True)
        )
    user_ids.append(conversation.website.support_account.owner_id)
    return list(dict.fromkeys(user_id for user_id in user_ids if user_id))


def escalate_sla_breach(conversation: SupportConversation, *, targets: list[str], now=None, policy=None) -> int:
    now = now or timezone.now()
    policy = policy or effective_sla_policy(conversation)
    if not targets or not policy.escalate_on_breach or conversation.sla_escalated_at:
        return 0
    conversation.sla_escalated_at = now
    conversation.save(update_fields=["sla_escalated_at", "updated_at"])
    created = 0
    due_at = min(
        due for target, due in _active_deadlines(conversation) if target in set(targets)
    )
    for recipient_id in _escalation_recipients(conversation, policy):
        alert, was_created = SupportServiceAlert.objects.get_or_create(
            dedupe_key=_dedupe_key(conversation, recipient_id, SupportServiceAlert.Kind.SLA_ESCALATED, due_at),
            defaults={
                "support_account": conversation.website.support_account,
                "website": conversation.website,
                "support_conversation": conversation,
                "recipient_id": recipient_id,
                "kind": SupportServiceAlert.Kind.SLA_ESCALATED,
                "due_at": due_at,
                "metadata": {"targets": targets, "policy_source": policy.source},
            },
        )
        if was_created:
            created += 1
            publish_support_event(
                event_name="support.sla.escalated",
                user_ids=[recipient_id],
                data={
                    "version": 1,
                    "alert_id": str(alert.id),
                    "conversation_id": str(conversation.id),
                    "targets": targets,
                    "escalated_at": now,
                },
            )
    record_audit_event(
        account=conversation.website.support_account,
        website=conversation.website,
        support_conversation=conversation,
        actor=None,
        action="conversation.sla_escalated",
        target_type="support_conversation",
        target_id=conversation.id,
        summary="SLA breach was escalated.",
        metadata={"targets": targets, "policy_source": policy.source},
    )
    return created


@transaction.atomic
def generate_service_alerts(conversation: SupportConversation, *, now=None) -> int:
    now = now or timezone.now()
    conversation = (
        SupportConversation.objects.select_for_update(of=("self",))
        .select_related(
            "website", "website__support_account", "visitor",
            "assigned_agent", "assigned_agent__user",
        )
        .get(pk=conversation.pk)
    )
    settings_obj = effective_sla_policy(conversation)
    new_breaches = refresh_breach_markers(conversation, now=now)
    escalation_created = escalate_sla_breach(
        conversation, targets=new_breaches, now=now, policy=settings_obj
    )
    candidates = []
    cutoff = now + timedelta(minutes=settings_obj.due_soon_minutes)
    for target, due_at in _active_deadlines(conversation):
        if due_at <= now:
            candidates.append((_kind_for(target, True), due_at, target))
        elif due_at <= cutoff:
            candidates.append((_kind_for(target, False), due_at, target))
    if conversation.follow_up_at and conversation.follow_up_completed_at is None and conversation.follow_up_at <= now:
        candidates.append((SupportServiceAlert.Kind.FOLLOW_UP_DUE, conversation.follow_up_at, "follow_up"))

    created_count = 0
    for recipient_id in _alert_recipients(conversation, settings_obj):
        for kind, due_at, target in candidates:
            alert, created = SupportServiceAlert.objects.get_or_create(
                dedupe_key=_dedupe_key(conversation, recipient_id, kind, due_at),
                defaults={
                    "support_account": conversation.website.support_account,
                    "website": conversation.website,
                    "support_conversation": conversation,
                    "recipient_id": recipient_id,
                    "kind": kind,
                    "due_at": due_at,
                    "metadata": {
                        "target": target,
                        "website_name": conversation.website.name,
                        "visitor_name": conversation.visitor.name.strip() or "Website visitor",
                    },
                },
            )
            if not created:
                continue
            created_count += 1
            publish_support_event(
                event_name="support.service.alert",
                user_ids=[recipient_id],
                data={
                    "alert_id": str(alert.id),
                    "conversation_id": str(conversation.id),
                    "website_id": str(conversation.website_id),
                    "website_name": conversation.website.name,
                    "kind": kind,
                    "due_at": due_at,
                    "summary": _alert_summary(kind, conversation),
                },
            )
    resolve_inactive_alerts(conversation)
    return created_count + escalation_created


def scan_service_operations(*, now=None) -> int:
    now = now or timezone.now()
    missing = (
        SupportConversation.objects.filter(status__in=OPEN_STATUSES, sla_paused_at__isnull=True)
        .filter(Q(first_response_due_at__isnull=True) | Q(resolution_due_at__isnull=True))
        .select_related("website", "website__support_account", "website__support_account__service_settings")
        .order_by("created_at")
    )
    for conversation in missing:
        initialize_service_targets(conversation)

    soon_ceiling = now + timedelta(days=1)
    candidates = (
        SupportConversation.objects.filter(status__in=OPEN_STATUSES, sla_paused_at__isnull=True)
        .filter(
            Q(first_response_due_at__lte=soon_ceiling)
            | Q(next_response_due_at__lte=soon_ceiling)
            | Q(resolution_due_at__lte=soon_ceiling)
            | Q(follow_up_at__lte=now, follow_up_completed_at__isnull=True)
        )
        .select_related(
            "website", "website__support_account", "visitor",
            "assigned_agent", "assigned_agent__user",
        )
        .order_by("updated_at")
    )
    return sum(
        generate_service_alerts(conversation, now=now)
        for conversation in candidates.iterator(chunk_size=200)
    )

def recalculate_account_targets(account) -> int:
    conversations = (
        SupportConversation.objects.filter(
            website__support_account=account,
            status__in=OPEN_STATUSES,
        )
        .select_related("website", "website__support_account")
        .order_by("created_at")
    )
    count = 0
    for conversation in conversations.iterator(chunk_size=200):
        recalculate_active_targets(conversation)
        count += 1
    return count
