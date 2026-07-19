from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.tasks import integration_health_snapshot
from apps.common.operational_health import realtime_pipeline_snapshot


def database_ready(alias="default"):
    try:
        connections[alias].ensure_connection()
        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def cache_ready():
    key = "healthcheck:ping"
    value = timezone.now().isoformat()
    try:
        cache.set(key, value, timeout=30)
        cached = cache.get(key)
        return cached == value, "ok" if cached == value else "cache roundtrip mismatch"
    except Exception as exc:
        return False, str(exc)


def migrations_ready(alias="default"):
    try:
        connection = connections[alias]
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        if not plan:
            return True, "ok"
        pending = [f"{migration.app_label}.{migration.name}" for migration, _ in plan]
        detail = f"{len(pending)} unapplied migration(s): {', '.join(pending[:5])}"
        if len(pending) > 5:
            detail += ", ..."
        return False, detail
    except Exception as exc:
        return False, str(exc)


class LiveHealthView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response({"status": "ok", "service": settings.SERVICE_NAME, "version": settings.APP_VERSION, "time": timezone.now().isoformat()})


class ReadyHealthView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        db_ok, db_detail = database_ready()
        cache_ok, cache_detail = cache_ready()
        migrations_ok, migrations_detail = migrations_ready()
        ok = db_ok and cache_ok and migrations_ok
        status_code = 200 if ok else 503
        return Response({
            "status": "ready" if ok else "degraded",
            "checks": {
                "database": {"ok": db_ok, "detail": db_detail},
                "cache": {"ok": cache_ok, "detail": cache_detail},
                "migrations": {"ok": migrations_ok, "detail": migrations_detail},
            },
            "service": settings.SERVICE_NAME,
            "version": settings.APP_VERSION,
            "time": timezone.now().isoformat(),
        }, status=status_code)


class DeepHealthView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        db_ok, db_detail = database_ready()
        cache_ok, cache_detail = cache_ready()
        migrations_ok, migrations_detail = migrations_ready()
        integrations = integration_health_snapshot()
        realtime = realtime_pipeline_snapshot()
        av_ok = integrations.get("antivirus", {}).get("available", False) or not integrations.get("antivirus", {}).get("enabled", False)
        push_ok = True if integrations.get("push", {}).get("dry_run", True) else integrations.get("push", {}).get("configured", False)
        ok = db_ok and cache_ok and migrations_ok and av_ok and push_ok and realtime.ok
        status_code = 200 if ok else 503
        return Response({
            "status": "ready" if ok else "degraded",
            "checks": {
                "database": {"ok": db_ok, "detail": db_detail},
                "cache": {"ok": cache_ok, "detail": cache_detail},
                "migrations": {"ok": migrations_ok, "detail": migrations_detail},
                "integrations": integrations,
                "realtime_pipeline": realtime.to_dict(),
            },
            "time": timezone.now().isoformat(),
        }, status=status_code)


LiveHealthView.get = extend_schema(responses=OpenApiTypes.OBJECT)(LiveHealthView.get)
ReadyHealthView.get = extend_schema(responses=OpenApiTypes.OBJECT)(ReadyHealthView.get)
DeepHealthView.get = extend_schema(responses=OpenApiTypes.OBJECT)(DeepHealthView.get)
