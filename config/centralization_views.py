import hashlib
import hmac
import os
from json import JSONDecodeError

from django.conf import settings
from django.apps import apps
from django.db import connection
from django.contrib.admin.models import LogEntry
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .admin_control import action_catalog, execute_action, parse_payload, verify_admin_request
from .centralization import centralization_snapshot


@require_GET
def centralization_readiness(request):
    snapshot = centralization_snapshot()
    configured = snapshot["auth_payment_admin_credentials_configured"]
    jwt_configured = snapshot["auth_payment_jwt_signing_key_configured"]
    auth_ok = snapshot["central_auth_enabled"] and jwt_configured
    admin_ok = snapshot["central_admin_enabled"]
    admin_coverage = snapshot["django_admin_model_coverage"]
    return JsonResponse(
        {
            "status": "ready" if configured and auth_ok and admin_ok else "needs_configuration",
            "checks": [
                {"code": "central_auth", "ok": auth_ok},
                {"code": "auth_payment_jwt_signing_key", "ok": jwt_configured},
                {"code": "central_payments", "ok": snapshot["central_payments_enabled"]},
                {"code": "central_admin", "ok": admin_ok},
                {"code": "auth_payment_service_credentials", "ok": configured},
                {"code": "django_admin_model_coverage", "ok": admin_coverage["complete"]},
            ],
            "metadata": snapshot,
        }
    )


def _database_status() -> dict:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - readiness must report safely.
        return {"status": "error", "detail": exc.__class__.__name__}


def _pending_migrations_count() -> int | None:
    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        return len(executor.migration_plan(targets))
    except Exception:
        return None


def centralization_control_snapshot_payload() -> dict:
    snapshot = centralization_snapshot()
    admin_coverage = snapshot["django_admin_model_coverage"]
    auth_ok = snapshot["central_auth_enabled"] and snapshot["auth_payment_jwt_signing_key_configured"]
    configured = snapshot["auth_payment_admin_credentials_configured"]
    database = _database_status()
    pending_migrations = _pending_migrations_count()
    db_ok = database.get("status") == "ok"
    migrations_ok = pending_migrations in (0, None)
    ready = configured and auth_ok and snapshot["central_admin_enabled"]
    status = "healthy" if ready and db_ok and migrations_ok else "needs_attention"

    return {
        "status": status,
        "manifest": {
            "code": snapshot["service"],
            "name": snapshot["business_product"].replace("_", " ").title(),
            "environment": getattr(settings, "ENVIRONMENT", "development"),
            "version": getattr(settings, "APP_VERSION", ""),
            "agent_version": "1.0",
            "contract_version": "centralization.v1",
            "features": ["django", "central_auth", "central_admin"],
            "capabilities": ["readiness", "snapshot", "actions", "models"],
        },
        "capabilities": {"readiness": True, "control_snapshot": True, "admin_actions": True, "remote_model_admin": True},
        "services": {
            "database": database,
            "auth_payment": {"status": "configured" if snapshot["auth_payment_base_url"] else "missing"},
            "admin_control": {"status": "configured" if snapshot["admin_control_base_url"] else "missing"},
        },
        "metrics": {
            "pending_migrations": pending_migrations,
            "installed_apps": len(settings.INSTALLED_APPS),
            "admin_model_coverage_percent": admin_coverage.get("coverage_percent"),
        },
        "checks": [
            {"code": "central_auth", "ok": auth_ok},
            {"code": "central_payments", "ok": snapshot["central_payments_enabled"]},
            {"code": "central_admin", "ok": snapshot["central_admin_enabled"]},
            {"code": "auth_payment_service_credentials", "ok": configured},
            {"code": "django_admin_model_coverage", "ok": admin_coverage["complete"]},
            {"code": "database", "ok": db_ok},
            {"code": "pending_migrations", "ok": migrations_ok, "count": pending_migrations},
        ],
        "deployment": {"debug": bool(settings.DEBUG), "allowed_hosts": bool(settings.ALLOWED_HOSTS)},
        "backups": {"database": {"status": "not_configured"}, "media": {"status": "not_configured"}},
        "security": {
            "debug": bool(settings.DEBUG),
            "allowed_hosts_configured": bool(settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS),
            "admin_credentials_configured": configured,
        },
        "errors": [],
    }


@require_GET
@csrf_exempt
def centralization_control_snapshot(request):
    error = verify_admin_request(request)
    if error:
        return JsonResponse(error.data, status=error.status_code)
    return JsonResponse(centralization_control_snapshot_payload())


def _verify_internal_monitoring_token(request):
    expected_hash = os.getenv("INTERNAL_ADMIN_MONITORING_TOKEN_HASH", "").strip()
    if not expected_hash:
        return False
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    prefix = "Bearer "
    if not auth.startswith(prefix):
        return False
    token_hash = hashlib.sha256(auth[len(prefix):].strip().encode("utf-8")).hexdigest()
    return hmac.compare_digest(token_hash, expected_hash)


def _model_count(label):
    try:
        return apps.get_model(label).objects.count()
    except Exception:
        return None


def _recent_admin_operations(limit=50):
    rows = LogEntry.objects.select_related("user", "content_type").order_by("-action_time")[:limit]
    return [
        {
            "time": item.action_time.isoformat(),
            "user": item.user.get_username() if item.user_id else "",
            "object": item.object_repr,
            "model": item.content_type.model if item.content_type_id else "",
            "app": item.content_type.app_label if item.content_type_id else "",
            "action": "add" if item.is_addition() else "change" if item.is_change() else "delete" if item.is_deletion() else "unknown",
            "message": item.change_message,
        }
        for item in rows
    ]


@require_GET
def internal_admin_monitoring(request):
    if not _verify_internal_monitoring_token(request):
        return JsonResponse({"message": "Not found"}, status=404)
    snapshot = centralization_control_snapshot_payload()
    return JsonResponse(
        {
            "project": "messenger",
            "status": snapshot.get("status", "unknown"),
            "users_count": _model_count(settings.AUTH_USER_MODEL),
            "orders_today": None,
            "activity_count": _model_count("chat.Message"),
            "errors_last_24h": len(snapshot.get("errors", [])),
            "maintenance_mode": bool(getattr(settings, "MAINTENANCE_MODE", False)),
            "last_deploy_at": snapshot.get("deployment", {}).get("deployed_at") or "",
            "recent_operations": _recent_admin_operations(),
        }
    )


@require_GET
@csrf_exempt
def admin_action_catalog(request):
    error = verify_admin_request(request)
    if error:
        return JsonResponse(error.data, status=error.status_code)
    return JsonResponse({"project": centralization_snapshot()["service"], "actions": action_catalog()})


@require_POST
@csrf_exempt
def admin_action_execute(request, action):
    error = verify_admin_request(request)
    if error:
        return JsonResponse(error.data, status=error.status_code)
    try:
        payload = parse_payload(request)
    except JSONDecodeError:
        return JsonResponse({"detail": "invalid JSON payload"}, status=400)
    result = execute_action(action, payload)
    return JsonResponse(result.data, status=result.status_code)
