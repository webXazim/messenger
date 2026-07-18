from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median

from django.db.models import Count, Q
from django.utils import timezone

from apps.chat.models import Message
from apps.support.models import SupportAgent, SupportConversation, SupportCSATSurvey
from apps.support.services import SupportContext, visible_websites


class SupportAnalyticsError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


def _seconds(value: timedelta | None) -> int | None:
    if value is None:
        return None
    return max(0, int(value.total_seconds()))


def _median(values: list[int]) -> int | None:
    return int(median(values)) if values else None


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def parse_analytics_period(*, start_value: str | None, end_value: str | None, days_value: str | None = None):
    today = timezone.localdate()
    try:
        days = min(366, max(1, int(days_value or 30)))
    except (TypeError, ValueError):
        days = 30

    try:
        start_day = date.fromisoformat(start_value) if start_value else today - timedelta(days=days - 1)
        end_day = date.fromisoformat(end_value) if end_value else today
    except ValueError as exc:
        raise SupportAnalyticsError("Use dates in YYYY-MM-DD format.", code="invalid_period") from exc

    if start_day > end_day:
        raise SupportAnalyticsError("The start date must be before the end date.", code="invalid_period")
    if (end_day - start_day).days > 365:
        raise SupportAnalyticsError("Analytics reports are limited to 366 days.", code="period_too_large")

    tz = timezone.get_current_timezone()
    start_at = timezone.make_aware(datetime.combine(start_day, time.min), tz)
    end_at = timezone.make_aware(datetime.combine(end_day + timedelta(days=1), time.min), tz)
    return start_day, end_day, start_at, end_at


def _conversation_durations(conversations: list[SupportConversation]):
    first_response = []
    resolution = []
    for conversation in conversations:
        if conversation.first_response_at:
            first_response.append(_seconds(conversation.first_response_at - conversation.created_at))
        if conversation.resolved_at:
            resolution.append(_seconds(conversation.resolved_at - conversation.created_at))
    return [value for value in first_response if value is not None], [value for value in resolution if value is not None]


def analytics_overview(
    *,
    context: SupportContext,
    start_at,
    end_at,
    start_day: date,
    end_day: date,
    website_id=None,
):
    permitted_websites = visible_websites(context).order_by("name")
    if website_id:
        permitted_websites = permitted_websites.filter(pk=website_id)
        if not permitted_websites.exists():
            raise SupportAnalyticsError("The selected website is unavailable.", code="website_denied", status_code=403)
    websites = list(permitted_websites)
    website_ids = [website.id for website in websites]

    period_qs = (
        SupportConversation.objects.filter(
            website_id__in=website_ids,
            created_at__gte=start_at,
            created_at__lt=end_at,
        )
        .select_related("website", "assigned_agent", "assigned_agent__user", "conversation")
        .order_by("created_at")
    )
    conversations = list(period_qs)
    conversation_ids = [conversation.id for conversation in conversations]

    resolved_qs = SupportConversation.objects.filter(
        website_id__in=website_ids,
        resolved_at__gte=start_at,
        resolved_at__lt=end_at,
    ).select_related("website", "assigned_agent", "assigned_agent__user", "conversation")
    resolved_conversations = list(resolved_qs)

    current_open_qs = SupportConversation.objects.filter(
        website_id__in=website_ids,
        status__in=[
            SupportConversation.Status.NEW,
            SupportConversation.Status.OPEN,
            SupportConversation.Status.WAITING_CUSTOMER,
            SupportConversation.Status.WAITING_TEAM,
        ],
    )
    current_open = current_open_qs.count()
    current_unassigned = current_open_qs.filter(assigned_agent__isnull=True).count()
    current_overdue = current_open_qs.filter(
        Q(first_response_breached_at__isnull=False)
        | Q(next_response_breached_at__isnull=False)
        | Q(resolution_breached_at__isnull=False)
    ).count()

    first_response_values, _ = _conversation_durations(conversations)
    _, resolution_values = _conversation_durations(resolved_conversations)
    cohort_resolved = [
        conversation for conversation in conversations
        if conversation.resolved_at and conversation.resolved_at < end_at
    ]
    breached = sum(
        1
        for conversation in conversations
        if conversation.first_response_breached_at
        or conversation.next_response_breached_at
        or conversation.resolution_breached_at
    )

    survey_qs = SupportCSATSurvey.objects.filter(
        website_id__in=website_ids,
        requested_at__gte=start_at,
        requested_at__lt=end_at,
    )
    survey_requested = survey_qs.count()
    submitted_surveys = list(survey_qs.filter(status=SupportCSATSurvey.Status.SUBMITTED).only("rating", "submitted_at", "website_id"))
    csat_ratings = [int(survey.rating) for survey in submitted_surveys if survey.rating]

    message_qs = Message.objects.filter(
        conversation__support_conversation__website_id__in=website_ids,
        created_at__gte=start_at,
        created_at__lt=end_at,
        is_deleted=False,
    )
    total_messages = message_qs.count()
    visitor_messages = message_qs.filter(support_author__isnull=False).count()
    team_messages = message_qs.filter(sender__isnull=False).count()

    status_counts = defaultdict(int)
    for conversation in conversations:
        status_counts[conversation.status] += 1

    daily = {}
    cursor = start_day
    while cursor <= end_day:
        daily[cursor.isoformat()] = {
            "date": cursor.isoformat(),
            "created": 0,
            "resolved": 0,
            "messages": 0,
            "csat_responses": 0,
            "csat_average": None,
            "_ratings": [],
        }
        cursor += timedelta(days=1)

    for conversation in conversations:
        key = timezone.localtime(conversation.created_at).date().isoformat()
        if key in daily:
            daily[key]["created"] += 1
    for conversation in resolved_conversations:
        key = timezone.localtime(conversation.resolved_at).date().isoformat() if conversation.resolved_at else ""
        if key in daily:
            daily[key]["resolved"] += 1
    for row in message_qs.values("created_at").iterator():
        key = timezone.localtime(row["created_at"]).date().isoformat()
        if key in daily:
            daily[key]["messages"] += 1
    for survey in submitted_surveys:
        if not survey.submitted_at:
            continue
        key = timezone.localtime(survey.submitted_at).date().isoformat()
        if key in daily and survey.rating:
            daily[key]["csat_responses"] += 1
            daily[key]["_ratings"].append(int(survey.rating))
    for item in daily.values():
        ratings = item.pop("_ratings")
        item["csat_average"] = round(sum(ratings) / len(ratings), 2) if ratings else None

    website_rows = []
    for website in websites:
        site_conversations = [item for item in conversations if item.website_id == website.id]
        site_resolved = [item for item in resolved_conversations if item.website_id == website.id]
        site_cohort_resolved = [
            item for item in site_conversations
            if item.resolved_at and item.resolved_at < end_at
        ]
        site_first, _ = _conversation_durations(site_conversations)
        _, site_resolution = _conversation_durations(site_resolved)
        site_surveys = [item for item in submitted_surveys if item.website_id == website.id and item.rating]
        site_requested = survey_qs.filter(website=website).count()
        site_breached = sum(
            1 for item in site_conversations
            if item.first_response_breached_at or item.next_response_breached_at or item.resolution_breached_at
        )
        website_rows.append({
            "website": {"id": str(website.id), "name": website.name, "domain": website.domain},
            "conversations": len(site_conversations),
            "resolved": len(site_resolved),
            "resolution_rate": _percent(len(site_cohort_resolved), len(site_conversations)),
            "median_first_response_seconds": _median(site_first),
            "median_resolution_seconds": _median(site_resolution),
            "sla_breach_rate": _percent(site_breached, len(site_conversations)),
            "csat_average": round(sum(int(item.rating) for item in site_surveys) / len(site_surveys), 2) if site_surveys else None,
            "csat_response_rate": _percent(len(site_surveys), site_requested),
        })

    agents_qs = SupportAgent.objects.filter(
        support_account=context.account,
        is_active=True,
    ).select_related("user", "user__profile").order_by("user__username")
    if context.role == "agent":
        agents_qs = agents_qs.filter(pk=context.agent.id)
    agent_message_counts = {
        row["sender_id"]: row["count"]
        for row in message_qs.filter(sender__isnull=False)
        .values("sender_id")
        .annotate(count=Count("id"))
    }
    agent_rows = []
    for agent in agents_qs:
        accessible_site_ids = set(
            agent.website_assignments.filter(website_id__in=website_ids).values_list("website_id", flat=True)
        )
        if not accessible_site_ids and context.role != "owner":
            continue
        agent_period = [
            item for item in conversations
            if item.assigned_agent_id == agent.id and item.website_id in accessible_site_ids
        ]
        agent_resolved = [
            item for item in resolved_conversations
            if item.assigned_agent_id == agent.id and item.website_id in accessible_site_ids
        ]
        agent_first, _ = _conversation_durations(agent_period)
        _, agent_resolution = _conversation_durations(agent_resolved)
        profile = getattr(agent.user, "profile", None)
        agent_rows.append({
            "agent": {
                "id": str(agent.id),
                "user_id": str(agent.user_id),
                "display_name": getattr(profile, "display_name", "") or agent.user.get_full_name() or agent.user.username,
                "username": agent.user.username,
                "avatar": profile.avatar.url if profile and profile.avatar else None,
            },
            "availability": agent.availability,
            "active_assigned": SupportConversation.objects.filter(
                assigned_agent=agent,
                website_id__in=accessible_site_ids,
                status__in=[
                    SupportConversation.Status.NEW,
                    SupportConversation.Status.OPEN,
                    SupportConversation.Status.WAITING_CUSTOMER,
                    SupportConversation.Status.WAITING_TEAM,
                ],
            ).count(),
            "assigned_in_period": len(agent_period),
            "resolved_in_period": len(agent_resolved),
            "team_messages": agent_message_counts.get(agent.user_id, 0),
            "median_first_response_seconds": _median(agent_first),
            "median_resolution_seconds": _median(agent_resolution),
        })

    return {
        "period": {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "days": (end_day - start_day).days + 1,
            "website_id": str(website_id) if website_id else None,
        },
        "summary": {
            "conversations_created": len(conversations),
            "resolved": len(resolved_conversations),
            "resolution_rate": _percent(len(cohort_resolved), len(conversations)),
            "current_open": current_open,
            "current_unassigned": current_unassigned,
            "current_overdue": current_overdue,
            "median_first_response_seconds": _median(first_response_values),
            "median_resolution_seconds": _median(resolution_values),
            "sla_breach_rate": _percent(breached, len(conversations)),
            "messages": total_messages,
            "visitor_messages": visitor_messages,
            "team_messages": team_messages,
            "csat_average": round(sum(csat_ratings) / len(csat_ratings), 2) if csat_ratings else None,
            "csat_responses": len(csat_ratings),
            "csat_response_rate": _percent(len(csat_ratings), survey_requested),
        },
        "status_counts": {
            value: status_counts[value]
            for value, _ in SupportConversation.Status.choices
        },
        "daily": list(daily.values()),
        "websites": website_rows,
        "agents": agent_rows,
    }
