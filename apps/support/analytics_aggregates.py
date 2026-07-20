from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.chat.models import Message
from apps.support.models import (
    SupportAgent,
    SupportAnalyticsDailyMetric,
    SupportAnalyticsExport,
    SupportAnalyticsHourlyMetric,
    SupportAnalyticsTagMetric,
    SupportConversation,
    SupportConversationTag,
    SupportCSATSurvey,
)


CACHE_SECONDS = 120
ACTIVE_STATUSES = (
    SupportConversation.Status.NEW,
    SupportConversation.Status.OPEN,
    SupportConversation.Status.WAITING_CUSTOMER,
    SupportConversation.Status.WAITING_TEAM,
    SupportConversation.Status.SNOOZED,
)


def _day_bounds(metric_date: date):
    zone = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(metric_date, time.min), zone)
    end = start + timedelta(days=1)
    return start, end


def _duration_seconds(start, end):
    if not start or not end or end < start:
        return 0
    return int((end - start).total_seconds())


def _scope_key(*, website_id=None, team_id=None, agent_id=None):
    return website_id, team_id, agent_id


def _metric_defaults():
    return {
        "conversations_created": 0,
        "conversations_resolved": 0,
        "conversations_reopened": 0,
        "messages_total": 0,
        "visitor_messages": 0,
        "agent_messages": 0,
        "first_response_seconds_total": 0,
        "first_response_count": 0,
        "resolution_seconds_total": 0,
        "resolution_count": 0,
        "sla_eligible_count": 0,
        "sla_compliant_count": 0,
        "csat_rating_total": 0,
        "csat_response_count": 0,
        "unassigned_seconds_total": 0,
        "handled_count": 0,
    }


def _add_metric(bucket, key, field, amount=1):
    bucket[key][field] += amount


@transaction.atomic
def aggregate_support_day(account, metric_date: date):
    """Rebuild one account/day from source-of-truth records.

    The task is idempotent: existing rows for the day are replaced inside one
    transaction. It is safe to reconcile recent dates repeatedly.
    """

    start_at, end_at = _day_bounds(metric_date)
    buckets = defaultdict(_metric_defaults)

    conversations = list(
        SupportConversation.objects.filter(
            website__support_account=account,
            created_at__gte=start_at,
            created_at__lt=end_at,
        ).select_related("website", "assigned_team", "assigned_agent", "conversation")
    )
    resolved = list(
        SupportConversation.objects.filter(
            website__support_account=account,
            resolved_at__gte=start_at,
            resolved_at__lt=end_at,
        ).select_related("website", "assigned_team", "assigned_agent", "conversation")
    )

    def keys_for(conversation):
        keys = [_scope_key()]
        keys.append(_scope_key(website_id=conversation.website_id))
        if conversation.assigned_team_id:
            keys.append(_scope_key(team_id=conversation.assigned_team_id))
        if conversation.assigned_agent_id:
            keys.append(_scope_key(agent_id=conversation.assigned_agent_id))
        return keys

    for conversation in conversations:
        compliant = not any(
            (
                conversation.first_response_breached_at,
                conversation.next_response_breached_at,
                conversation.resolution_breached_at,
            )
        )
        for key in keys_for(conversation):
            _add_metric(buckets, key, "conversations_created")
            _add_metric(buckets, key, "sla_eligible_count")
            if compliant:
                _add_metric(buckets, key, "sla_compliant_count")
            if conversation.reopen_count:
                _add_metric(buckets, key, "conversations_reopened", conversation.reopen_count)
            if conversation.first_response_at:
                _add_metric(
                    buckets,
                    key,
                    "first_response_seconds_total",
                    _duration_seconds(conversation.created_at, conversation.first_response_at),
                )
                _add_metric(buckets, key, "first_response_count")
            if conversation.assigned_agent_id:
                _add_metric(buckets, key, "handled_count")

    for conversation in resolved:
        for key in keys_for(conversation):
            _add_metric(buckets, key, "conversations_resolved")
            if conversation.resolved_at:
                _add_metric(
                    buckets,
                    key,
                    "resolution_seconds_total",
                    _duration_seconds(conversation.created_at, conversation.resolved_at),
                )
                _add_metric(buckets, key, "resolution_count")

    messages = Message.objects.filter(
        conversation__support_conversation__website__support_account=account,
        created_at__gte=start_at,
        created_at__lt=end_at,
        is_deleted=False,
    ).select_related(
        "conversation__support_conversation",
        "conversation__support_conversation__website",
        "conversation__support_conversation__assigned_team",
        "conversation__support_conversation__assigned_agent",
    )
    for message in messages.iterator(chunk_size=1000):
        conversation = message.conversation.support_conversation
        for key in keys_for(conversation):
            _add_metric(buckets, key, "messages_total")
            _add_metric(
                buckets,
                key,
                "agent_messages" if message.sender_id else "visitor_messages",
            )

    surveys = SupportCSATSurvey.objects.filter(
        support_account=account,
        status=SupportCSATSurvey.Status.SUBMITTED,
        submitted_at__gte=start_at,
        submitted_at__lt=end_at,
        rating__isnull=False,
    ).select_related(
        "support_conversation",
        "support_conversation__assigned_team",
        "support_conversation__assigned_agent",
    )
    for survey in surveys:
        for key in keys_for(survey.support_conversation):
            _add_metric(buckets, key, "csat_rating_total", int(survey.rating))
            _add_metric(buckets, key, "csat_response_count")

    SupportAnalyticsDailyMetric.objects.filter(
        support_account=account,
        metric_date=metric_date,
    ).delete()
    SupportAnalyticsHourlyMetric.objects.filter(
        support_account=account,
        metric_date=metric_date,
    ).delete()
    SupportAnalyticsTagMetric.objects.filter(
        support_account=account,
        metric_date=metric_date,
    ).delete()

    rows = []
    for (website_id, team_id, agent_id), values in buckets.items():
        rows.append(
            SupportAnalyticsDailyMetric(
                support_account=account,
                metric_date=metric_date,
                website_id=website_id,
                team_id=team_id,
                agent_id=agent_id,
                **values,
            )
        )
    SupportAnalyticsDailyMetric.objects.bulk_create(rows, batch_size=500)

    hourly = defaultdict(int)
    for conversation in conversations:
        local = timezone.localtime(conversation.created_at)
        hourly[(None, local.hour)] += 1
        hourly[(conversation.website_id, local.hour)] += 1
    SupportAnalyticsHourlyMetric.objects.bulk_create(
        [
            SupportAnalyticsHourlyMetric(
                support_account=account,
                metric_date=metric_date,
                website_id=website_id,
                hour=hour,
                conversations_created=count,
            )
            for (website_id, hour), count in hourly.items()
        ],
        batch_size=200,
    )

    tag_counts = defaultdict(int)
    assignments = SupportConversationTag.objects.filter(
        tag__support_account=account,
        support_conversation__created_at__gte=start_at,
        support_conversation__created_at__lt=end_at,
    ).select_related("tag", "support_conversation")
    for assignment in assignments:
        website_id = assignment.support_conversation.website_id
        tag_counts[(assignment.tag_id, None)] += 1
        tag_counts[(assignment.tag_id, website_id)] += 1
    SupportAnalyticsTagMetric.objects.bulk_create(
        [
            SupportAnalyticsTagMetric(
                support_account=account,
                metric_date=metric_date,
                tag_id=tag_id,
                website_id=website_id,
                conversation_count=count,
            )
            for (tag_id, website_id), count in tag_counts.items()
        ],
        batch_size=200,
    )

    cache.delete_pattern(f"support:analytics:{account.id}:*") if hasattr(cache, "delete_pattern") else None
    return len(rows)


def aggregate_recent_support_metrics(days=3):
    from apps.support.models import SupportAccount

    today = timezone.localdate()
    count = 0
    for account in SupportAccount.objects.filter(is_active=True).iterator():
        for offset in range(max(1, days)):
            aggregate_support_day(account, today - timedelta(days=offset))
            count += 1
    return count


def _avg(total, count):
    return round(total / count) if count else None


def _pct(numerator, denominator):
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


def _sum_rows(queryset):
    return queryset.aggregate(
        conversations=Sum("conversations_created"),
        resolved=Sum("conversations_resolved"),
        reopened=Sum("conversations_reopened"),
        messages=Sum("messages_total"),
        first_total=Sum("first_response_seconds_total"),
        first_count=Sum("first_response_count"),
        resolution_total=Sum("resolution_seconds_total"),
        resolution_count=Sum("resolution_count"),
        eligible=Sum("sla_eligible_count"),
        compliant=Sum("sla_compliant_count"),
        csat_total=Sum("csat_rating_total"),
        csat_count=Sum("csat_response_count"),
        handled=Sum("handled_count"),
    )


def _base_metrics(account, start_day, end_day, *, website_id=None, team_id=None, agent_id=None):
    qs = SupportAnalyticsDailyMetric.objects.filter(
        support_account=account,
        metric_date__range=(start_day, end_day),
    )
    if website_id:
        return qs.filter(website_id=website_id, team__isnull=True, agent__isnull=True)
    if team_id:
        return qs.filter(team_id=team_id, website__isnull=True, agent__isnull=True)
    if agent_id:
        return qs.filter(agent_id=agent_id, website__isnull=True, team__isnull=True)
    return qs.filter(website__isnull=True, team__isnull=True, agent__isnull=True)


def overview_payload(account, start_day, end_day, **filters):
    totals = _sum_rows(_base_metrics(account, start_day, end_day, **filters))
    open_qs = SupportConversation.objects.filter(
        website__support_account=account,
        status__in=ACTIVE_STATUSES,
    )
    if filters.get("website_id"):
        open_qs = open_qs.filter(website_id=filters["website_id"])
    if filters.get("team_id"):
        open_qs = open_qs.filter(assigned_team_id=filters["team_id"])
    if filters.get("agent_id"):
        open_qs = open_qs.filter(assigned_agent_id=filters["agent_id"])

    conversations = totals["conversations"] or 0
    resolved = totals["resolved"] or 0
    current_open = open_qs.count()
    current_unassigned = open_qs.filter(assigned_agent__isnull=True).count()
    current_overdue = open_qs.filter(
        Q(first_response_breached_at__isnull=False)
        | Q(next_response_breached_at__isnull=False)
        | Q(resolution_breached_at__isnull=False)
    ).count()
    current_at_risk = open_qs.filter(
        Q(first_response_due_at__isnull=False)
        | Q(next_response_due_at__isnull=False)
        | Q(resolution_due_at__isnull=False),
        first_response_breached_at__isnull=True,
        next_response_breached_at__isnull=True,
        resolution_breached_at__isnull=True,
    ).count()
    return {
        "conversations": conversations,
        "resolved": resolved,
        "resolution_rate": _pct(resolved, conversations),
        "first_response_seconds": _avg(totals["first_total"] or 0, totals["first_count"] or 0),
        "resolution_seconds": _avg(totals["resolution_total"] or 0, totals["resolution_count"] or 0),
        "sla_compliance": _pct(totals["compliant"] or 0, totals["eligible"] or 0),
        "csat_average": round((totals["csat_total"] or 0) / (totals["csat_count"] or 1), 2) if totals["csat_count"] else None,
        "unassigned_rate": _pct(current_unassigned, current_open),
        "queue": {
            "open": current_open,
            "unassigned": current_unassigned,
            "overdue": current_overdue,
            "at_risk": current_at_risk,
        },
    }


def volume_payload(account, start_day, end_day, **filters):
    qs = _base_metrics(account, start_day, end_day, **filters).order_by("metric_date")
    return [
        {
            "date": row.metric_date.isoformat(),
            "created": row.conversations_created,
            "resolved": row.conversations_resolved,
            "messages": row.messages_total,
        }
        for row in qs
    ]


def website_payload(account, start_day, end_day):
    rows = (
        SupportAnalyticsDailyMetric.objects.filter(
            support_account=account,
            metric_date__range=(start_day, end_day),
            website__isnull=False,
            team__isnull=True,
            agent__isnull=True,
        )
        .values("website_id", "website__name", "website__domain")
        .annotate(
            conversations=Sum("conversations_created"),
            resolved=Sum("conversations_resolved"),
            first_total=Sum("first_response_seconds_total"),
            first_count=Sum("first_response_count"),
            resolution_total=Sum("resolution_seconds_total"),
            resolution_count=Sum("resolution_count"),
            eligible=Sum("sla_eligible_count"),
            compliant=Sum("sla_compliant_count"),
            csat_total=Sum("csat_rating_total"),
            csat_count=Sum("csat_response_count"),
        )
        .order_by("-conversations")
    )
    return [
        {
            "website": {
                "id": str(row["website_id"]),
                "name": row["website__name"],
                "domain": row["website__domain"],
            },
            "conversations": row["conversations"] or 0,
            "resolved": row["resolved"] or 0,
            "first_response_seconds": _avg(row["first_total"] or 0, row["first_count"] or 0),
            "resolution_seconds": _avg(row["resolution_total"] or 0, row["resolution_count"] or 0),
            "sla_compliance": _pct(row["compliant"] or 0, row["eligible"] or 0),
            "csat_average": round((row["csat_total"] or 0) / row["csat_count"], 2) if row["csat_count"] else None,
        }
        for row in rows
    ]


def agent_payload(account, start_day, end_day, team_id=None):
    qs = SupportAnalyticsDailyMetric.objects.filter(
        support_account=account,
        metric_date__range=(start_day, end_day),
        agent__isnull=False,
        website__isnull=True,
        team__isnull=True,
    )
    if team_id:
        allowed_agents = SupportAgent.objects.filter(
            team_memberships__team_id=team_id,
            team_memberships__is_active=True,
        ).values_list("id", flat=True)
        qs = qs.filter(agent_id__in=allowed_agents)
    rows = (
        qs.values(
            "agent_id",
            "agent__user__username",
            "agent__user__first_name",
            "agent__user__last_name",
            "agent__availability",
        )
        .annotate(
            conversations=Sum("handled_count"),
            resolved=Sum("conversations_resolved"),
            replies=Sum("agent_messages"),
            first_total=Sum("first_response_seconds_total"),
            first_count=Sum("first_response_count"),
            resolution_total=Sum("resolution_seconds_total"),
            resolution_count=Sum("resolution_count"),
            csat_total=Sum("csat_rating_total"),
            csat_count=Sum("csat_response_count"),
        )
        .order_by("-conversations")
    )
    return [
        {
            "agent": {
                "id": str(row["agent_id"]),
                "display_name": (
                    f'{row["agent__user__first_name"]} {row["agent__user__last_name"]}'.strip()
                    or row["agent__user__username"]
                ),
            },
            "availability": row["agent__availability"],
            "conversations": row["conversations"] or 0,
            "resolved": row["resolved"] or 0,
            "replies": row["replies"] or 0,
            "first_response_seconds": _avg(row["first_total"] or 0, row["first_count"] or 0),
            "resolution_seconds": _avg(row["resolution_total"] or 0, row["resolution_count"] or 0),
            "csat_average": round((row["csat_total"] or 0) / row["csat_count"], 2) if row["csat_count"] else None,
        }
        for row in rows
    ]


def hours_payload(account, start_day, end_day, website_id=None):
    qs = SupportAnalyticsHourlyMetric.objects.filter(
        support_account=account,
        metric_date__range=(start_day, end_day),
    )
    qs = qs.filter(website_id=website_id) if website_id else qs.filter(website__isnull=True)
    values = {row["hour"]: row["total"] or 0 for row in qs.values("hour").annotate(total=Sum("conversations_created"))}
    return [{"hour": hour, "conversations": values.get(hour, 0)} for hour in range(24)]


def tags_payload(account, start_day, end_day, website_id=None, limit=8):
    qs = SupportAnalyticsTagMetric.objects.filter(
        support_account=account,
        metric_date__range=(start_day, end_day),
    )
    qs = qs.filter(website_id=website_id) if website_id else qs.filter(website__isnull=True)
    rows = (
        qs.values("tag_id", "tag__name", "tag__color")
        .annotate(conversations=Sum("conversation_count"))
        .order_by("-conversations")[:limit]
    )
    total = sum(row["conversations"] or 0 for row in rows)
    return [
        {
            "tag": {"id": str(row["tag_id"]), "name": row["tag__name"], "color": row["tag__color"]},
            "conversations": row["conversations"] or 0,
            "share": _pct(row["conversations"] or 0, total),
        }
        for row in rows
    ]


def comparison_period(start_day, end_day):
    length = (end_day - start_day).days + 1
    previous_end = start_day - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length - 1)
    return previous_start, previous_end


def cached_report(account, report_name, start_day, end_day, filters, builder):
    key = "support:analytics:{}:{}:{}:{}:{}".format(
        account.id,
        report_name,
        start_day.isoformat(),
        end_day.isoformat(),
        hash(tuple(sorted((key, str(value)) for key, value in filters.items()))),
    )
    cached = cache.get(key)
    if cached is not None:
        return cached
    payload = builder(account, start_day, end_day, **filters)
    cache.set(key, payload, timeout=CACHE_SECONDS)
    return payload


def build_export(export_id):
    export = SupportAnalyticsExport.objects.select_related("support_account").get(pk=export_id)
    export.status = SupportAnalyticsExport.Status.PROCESSING
    export.save(update_fields=["status", "updated_at"])
    try:
        start_day = date.fromisoformat(export.filters["start"])
        end_day = date.fromisoformat(export.filters["end"])
        rows = agent_payload(
            export.support_account,
            start_day,
            end_day,
            team_id=export.filters.get("team"),
        )
        folder = Path(settings.MEDIA_ROOT) / "support-analytics"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{export.id}.csv"
        path = folder / filename
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "Agent", "Conversations", "Resolved", "Replies",
                "First response seconds", "Resolution seconds", "CSAT",
            ])
            for row in rows:
                writer.writerow([
                    row["agent"]["display_name"],
                    row["conversations"],
                    row["resolved"],
                    row["replies"],
                    row["first_response_seconds"] or "",
                    row["resolution_seconds"] or "",
                    row["csat_average"] or "",
                ])
        export.file_key = f"support-analytics/{filename}"
        export.status = SupportAnalyticsExport.Status.READY
        export.completed_at = timezone.now()
        export.save(update_fields=["file_key", "status", "completed_at", "updated_at"])
    except Exception as exc:
        export.status = SupportAnalyticsExport.Status.FAILED
        export.error_message = str(exc)[:2000]
        export.save(update_fields=["status", "error_message", "updated_at"])
        raise
    return str(export.id)
