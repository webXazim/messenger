from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.support.analytics import SupportAnalyticsError, parse_analytics_period
from apps.support.analytics_aggregates import (
    agent_payload,
    cached_report,
    comparison_period,
    hours_payload,
    overview_payload,
    tags_payload,
    volume_payload,
    website_payload,
)
from apps.support.models import (
    SupportAnalyticsExport,
    SupportTeam,
)
from apps.support.services import get_support_context, visible_websites
from apps.support.tasks import build_support_analytics_export


class AnalyticsAccessMixin:
    permission_classes = [IsAuthenticated]

    def analytics_context(self, request):
        context = get_support_context(request.user)
        if not context.account:
            return None, Response(
                {"detail": "Support Chat access is required.", "code": "support_access_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if context.role != "owner" and not (
            context.agent and context.agent.can_view_analytics
        ):
            return None, Response(
                {"detail": "Analytics access has not been granted.", "code": "analytics_denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return context, None

    def period_and_filters(self, request, context):
        start_day, end_day, _, _ = parse_analytics_period(
            start_value=request.query_params.get("start"),
            end_value=request.query_params.get("end"),
            days_value=request.query_params.get("days"),
        )
        website_id = request.query_params.get("website") or None
        team_id = request.query_params.get("team") or None
        agent_id = request.query_params.get("agent") or None

        if website_id and not visible_websites(context).filter(pk=website_id).exists():
            raise SupportAnalyticsError(
                "The selected website is unavailable.",
                code="website_denied",
                status_code=403,
            )
        if team_id and not SupportTeam.objects.filter(
            pk=team_id,
            support_account=context.account,
            is_active=True,
        ).exists():
            raise SupportAnalyticsError(
                "The selected team is unavailable.",
                code="team_denied",
                status_code=403,
            )
        if context.role == "agent":
            agent_id = str(context.agent.id)
        return start_day, end_day, {
            "website_id": website_id,
            "team_id": team_id,
            "agent_id": agent_id,
        }

    def handle(self, request, builder, report_name, *, allow_dimensions=True):
        context, error = self.analytics_context(request)
        if error:
            return error
        try:
            start_day, end_day, filters = self.period_and_filters(request, context)
            if not allow_dimensions:
                filters = {}
            payload = cached_report(
                context.account,
                report_name,
                start_day,
                end_day,
                filters,
                builder,
            )
            return Response(
                {
                    "period": {
                        "start": start_day.isoformat(),
                        "end": end_day.isoformat(),
                        "days": (end_day - start_day).days + 1,
                    },
                    "filters": filters,
                    "results": payload,
                }
            )
        except SupportAnalyticsError as exc:
            return Response(
                {"detail": exc.detail, "code": exc.code},
                status=exc.status_code,
            )


class SupportAnalyticsV2OverviewView(AnalyticsAccessMixin, APIView):
    def get(self, request):
        context, error = self.analytics_context(request)
        if error:
            return error
        try:
            start_day, end_day, filters = self.period_and_filters(request, context)
            current = overview_payload(context.account, start_day, end_day, **filters)
            previous_start, previous_end = comparison_period(start_day, end_day)
            previous = overview_payload(
                context.account,
                previous_start,
                previous_end,
                **filters,
            )
            return Response(
                {
                    "period": {
                        "start": start_day.isoformat(),
                        "end": end_day.isoformat(),
                        "days": (end_day - start_day).days + 1,
                    },
                    "filters": filters,
                    "current": current,
                    "previous": previous,
                }
            )
        except SupportAnalyticsError as exc:
            return Response(
                {"detail": exc.detail, "code": exc.code},
                status=exc.status_code,
            )


class SupportAnalyticsV2VolumeView(AnalyticsAccessMixin, APIView):
    def get(self, request):
        context, error = self.analytics_context(request)
        if error:
            return error
        try:
            start_day, end_day, filters = self.period_and_filters(request, context)
            previous_start, previous_end = comparison_period(start_day, end_day)
            return Response(
                {
                    "period": {
                        "start": start_day.isoformat(),
                        "end": end_day.isoformat(),
                        "days": (end_day - start_day).days + 1,
                    },
                    "filters": filters,
                    "current": volume_payload(
                        context.account, start_day, end_day, **filters
                    ),
                    "previous": volume_payload(
                        context.account, previous_start, previous_end, **filters
                    ),
                }
            )
        except SupportAnalyticsError as exc:
            return Response(
                {"detail": exc.detail, "code": exc.code},
                status=exc.status_code,
            )


class SupportAnalyticsV2WebsitesView(AnalyticsAccessMixin, APIView):
    def get(self, request):
        return self.handle(request, website_payload, "websites", allow_dimensions=False)


class SupportAnalyticsV2QueueView(AnalyticsAccessMixin, APIView):
    def get(self, request):
        context, error = self.analytics_context(request)
        if error:
            return error
        try:
            start_day, end_day, filters = self.period_and_filters(request, context)
            return Response(
                {
                    "period": {"start": start_day.isoformat(), "end": end_day.isoformat()},
                    "results": overview_payload(
                        context.account,
                        start_day,
                        end_day,
                        **filters,
                    )["queue"],
                }
            )
        except SupportAnalyticsError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)


class SupportAnalyticsV2TagsView(AnalyticsAccessMixin, APIView):
    def get(self, request):
        context, error = self.analytics_context(request)
        if error:
            return error
        try:
            start_day, end_day, filters = self.period_and_filters(request, context)
            payload = tags_payload(
                context.account,
                start_day,
                end_day,
                website_id=filters["website_id"],
            )
            return Response({"period": {"start": start_day.isoformat(), "end": end_day.isoformat()}, "results": payload})
        except SupportAnalyticsError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)


class SupportAnalyticsV2HoursView(AnalyticsAccessMixin, APIView):
    def get(self, request):
        context, error = self.analytics_context(request)
        if error:
            return error
        try:
            start_day, end_day, filters = self.period_and_filters(request, context)
            payload = hours_payload(
                context.account,
                start_day,
                end_day,
                website_id=filters["website_id"],
            )
            return Response({"period": {"start": start_day.isoformat(), "end": end_day.isoformat()}, "results": payload})
        except SupportAnalyticsError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)


class SupportAnalyticsV2AgentsView(AnalyticsAccessMixin, APIView):
    def get(self, request):
        context, error = self.analytics_context(request)
        if error:
            return error
        try:
            start_day, end_day, filters = self.period_and_filters(request, context)
            payload = agent_payload(
                context.account,
                start_day,
                end_day,
                team_id=filters["team_id"],
            )
            if context.role == "agent":
                payload = [row for row in payload if row["agent"]["id"] == str(context.agent.id)]
            return Response({"period": {"start": start_day.isoformat(), "end": end_day.isoformat()}, "results": payload})
        except SupportAnalyticsError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)


class SupportAnalyticsExportListCreateView(AnalyticsAccessMixin, APIView):
    def get(self, request):
        context, error = self.analytics_context(request)
        if error:
            return error
        rows = SupportAnalyticsExport.objects.filter(
            support_account=context.account,
            requested_by=request.user,
        )[:20]
        return Response(
            [
                {
                    "id": str(row.id),
                    "status": row.status,
                    "format": row.format,
                    "filters": row.filters,
                    "download_ready": bool(row.file_key and row.status == row.Status.READY),
                    "created_at": row.created_at,
                    "completed_at": row.completed_at,
                    "error_message": row.error_message,
                }
                for row in rows
            ]
        )

    def post(self, request):
        context, error = self.analytics_context(request)
        if error:
            return error
        if context.role != "owner" and not context.agent.can_export_data:
            return Response(
                {"detail": "Export permission is required.", "code": "export_denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            start_day, end_day, filters = self.period_and_filters(request, context)
        except SupportAnalyticsError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)
        export = SupportAnalyticsExport.objects.create(
            support_account=context.account,
            requested_by=request.user,
            format=(request.data.get("format") or "csv")[:12],
            filters={
                "start": start_day.isoformat(),
                "end": end_day.isoformat(),
                "website": filters["website_id"],
                "team": filters["team_id"],
                "agent": filters["agent_id"],
            },
        )
        build_support_analytics_export.delay(str(export.id))
        return Response(
            {"id": str(export.id), "status": export.status},
            status=status.HTTP_202_ACCEPTED,
        )


class SupportAnalyticsExportDownloadView(AnalyticsAccessMixin, APIView):
    def get(self, request, export_id):
        context, error = self.analytics_context(request)
        if error:
            return error
        export = get_object_or_404(
            SupportAnalyticsExport,
            pk=export_id,
            support_account=context.account,
            requested_by=request.user,
        )
        if export.status != export.Status.READY or not export.file_key:
            return Response(
                {"detail": "The export is not ready.", "code": "export_not_ready"},
                status=status.HTTP_409_CONFLICT,
            )
        path = Path(settings.MEDIA_ROOT) / export.file_key
        if not path.exists():
            return Response(
                {"detail": "The export file is unavailable.", "code": "export_missing"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return FileResponse(
            path.open("rb"),
            as_attachment=True,
            filename=f"support-analytics-{export.created_at.date()}.csv",
        )
