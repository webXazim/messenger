from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


DEV_SECRET_KEYS = {"change-me", "changeme", "secret", "dev-secret"}
DEV_TURN_SECRETS = {"dev-turn-shared-secret-change-me", "replace-with-a-long-random-turn-secret", "change-me", "changeme"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}


def _is_enabled(value):
    return bool(value)


def _turn_uri_host(uri):
    parsed = urlparse(uri)
    if parsed.hostname:
        return parsed.hostname
    if ":" not in uri:
        return ""
    remainder = uri.split(":", 1)[1].split("?", 1)[0]
    return remainder.rsplit(":", 1)[0].strip("[]")


def _turn_hosts():
    hosts = set()
    try:
        import json

        uris = json.loads(getattr(settings, "TURN_URIS_JSON", "") or "[]")
    except (TypeError, ValueError):
        return hosts
    for uri in uris if isinstance(uris, list) else []:
        if not isinstance(uri, str):
            continue
        host = _turn_uri_host(uri)
        if host:
            hosts.add(host)
    return hosts


@register(Tags.security, deploy=True)
def enterprise_deploy_checks(app_configs, **kwargs):
    from .push import resolve_firebase_service_account_path

    issues = []

    if getattr(settings, "DEBUG", False):
        issues.append(Error("DEBUG must be disabled for production.", id="chat.E001"))

    secret_key = getattr(settings, "SECRET_KEY", "")
    if secret_key in DEV_SECRET_KEYS or len(secret_key) < 32:
        issues.append(Error("SECRET_KEY must be a long, unique production secret.", id="chat.E002"))

    allowed_hosts = set(getattr(settings, "ALLOWED_HOSTS", []))
    if not allowed_hosts or "*" in allowed_hosts or allowed_hosts <= LOCAL_HOSTS:
        issues.append(Error("ALLOWED_HOSTS must contain the production domain and must not be wildcard-only or local-only.", id="chat.E003"))

    if not _is_enabled(getattr(settings, "SECURE_SSL_REDIRECT", False)):
        issues.append(Warning("SECURE_SSL_REDIRECT should be enabled behind a correctly configured TLS proxy.", id="chat.W001"))
    if int(getattr(settings, "SECURE_HSTS_SECONDS", 0) or 0) < 31536000:
        issues.append(Warning("SECURE_HSTS_SECONDS should be at least 31536000 for production HTTPS deployments.", id="chat.W002"))
    if not _is_enabled(getattr(settings, "SESSION_COOKIE_SECURE", False)):
        issues.append(Error("SESSION_COOKIE_SECURE must be enabled for production.", id="chat.E004"))
    if not _is_enabled(getattr(settings, "CSRF_COOKIE_SECURE", False)):
        issues.append(Error("CSRF_COOKIE_SECURE must be enabled for production.", id="chat.E005"))
    if getattr(settings, "DB_ENGINE", "").lower() != "postgres":
        issues.append(Error("DB_ENGINE must be postgres for production deployments.", id="chat.E012"))

    realtime_transport = str(getattr(settings, "REALTIME_TRANSPORT", "") or "").lower()
    if realtime_transport != "axum":
        issues.append(Error("REALTIME_TRANSPORT must be axum in production.", id="chat.E013"))
    durable_backend = str(getattr(settings, "REALTIME_DURABLE_BACKEND", "") or "").strip().lower()
    if durable_backend not in {"nats", "jetstream"}:
        issues.append(Error("REALTIME_DURABLE_BACKEND must be nats.", id="chat.E016"))
    if not bool(getattr(settings, "REALTIME_OUTBOX_ENABLED", False)):
        issues.append(Error("REALTIME_OUTBOX_ENABLED must be enabled for durable realtime delivery.", id="chat.E017"))
    if not bool(getattr(settings, "REALTIME_AUTH_ENABLED", False)):
        issues.append(Error("REALTIME_AUTH_ENABLED must be enabled in production.", id="chat.E018"))

    if bool(getattr(settings, "AXUM_DATA_PLANE_REQUIRED", False)):
        expected_backends = {
            "REALTIME_EPHEMERAL_BACKEND": "nats",
            "REALTIME_PRESENCE_BACKEND": "local",
            "REALTIME_OUTBOX_PUBLISHER": "axum",
            "CHAT_READ_BACKEND": "sqlx",
            "CHAT_COMMAND_BACKEND": "axum",
            "CHAT_INTERACTION_BACKEND": "axum",
            "CHAT_MESSAGE_MUTATION_BACKEND": "axum",
            "CHAT_CALL_RUNTIME_BACKEND": "axum",
            "CHAT_ATTACHMENT_BACKEND": "axum",
            "CHAT_CONVERSATION_COMMAND_BACKEND": "axum",
            "SUPPORT_DATA_BACKEND": "axum",
            "MEDIA_PROCESSING_BACKEND": "rust",
            "DATABASE_RUNTIME_ENDPOINT": "pgbouncer",
        }
        mismatches = [
            f"{name}={getattr(settings, name, None)!r}"
            for name, expected in expected_backends.items()
            if str(getattr(settings, name, "") or "").strip().lower() != expected
        ]
        if mismatches:
            issues.append(Error(
                "The required Axum data plane is incomplete: " + ", ".join(mismatches),
                id="chat.E030",
            ))
        if bool(getattr(settings, "MEDIA_WORKER_DJANGO_FALLBACK_ENABLED", True)):
            issues.append(Error(
                "MEDIA_WORKER_DJANGO_FALLBACK_ENABLED must be false when AXUM_DATA_PLANE_REQUIRED is enabled.",
                id="chat.E031",
            ))

    cache_backend = str(settings.CACHES.get("default", {}).get("BACKEND", "") or "")
    if cache_backend.endswith("LocMemCache"):
        issues.append(Error("CACHES['default'] must use a shared cache backend in production.", id="chat.E014"))

    if bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)):
        issues.append(Error("CELERY_TASK_ALWAYS_EAGER must be disabled for production workers.", id="chat.E015"))
    if not bool(getattr(settings, "UPLOAD_SCAN_ASYNC", True)):
        issues.append(Warning("UPLOAD_SCAN_ASYNC is disabled; upload scanning and media enrichment will block request latency.", id="chat.W008"))

    email_backend = str(getattr(settings, "EMAIL_BACKEND", "") or "")
    if email_backend == "django.core.mail.backends.console.EmailBackend":
        issues.append(Warning("EMAIL_BACKEND is set to console; transactional emails will not be delivered.", id="chat.W007"))

    turn_provider = str(getattr(settings, "TURN_PROVIDER", "legacy") or "legacy").strip().lower()
    if turn_provider == "cloudflare":
        if not str(getattr(settings, "CLOUDFLARE_TURN_KEY_ID", "") or "").strip():
            issues.append(Error("CLOUDFLARE_TURN_KEY_ID is required for production calling.", id="chat.E006"))
        if not str(getattr(settings, "CLOUDFLARE_TURN_API_TOKEN", "") or "").strip():
            issues.append(Error("CLOUDFLARE_TURN_API_TOKEN is required for production calling.", id="chat.E007"))
        turn_api_url = str(getattr(settings, "CLOUDFLARE_TURN_API_BASE_URL", "") or "").strip()
        if not turn_api_url.startswith("https://"):
            issues.append(Error("CLOUDFLARE_TURN_API_BASE_URL must use HTTPS.", id="chat.E008"))
    else:
        if not getattr(settings, "TURN_URIS_JSON", ""):
            issues.append(Error("TURN_URIS_JSON must be configured for legacy TURN.", id="chat.E006"))
        if not getattr(settings, "TURN_SHARED_SECRET", "") and not (
            getattr(settings, "TURN_STATIC_USERNAME", "") and getattr(settings, "TURN_STATIC_PASSWORD", "")
        ):
            issues.append(Error("Legacy TURN credentials must be configured.", id="chat.E007"))
        turn_shared_secret = str(getattr(settings, "TURN_SHARED_SECRET", "") or "").strip()
        if turn_shared_secret and turn_shared_secret in DEV_TURN_SECRETS:
            issues.append(Error("TURN_SHARED_SECRET must be replaced with a production secret.", id="chat.E011"))
        if _turn_hosts() & LOCAL_HOSTS:
            issues.append(Error("TURN_URIS_JSON contains local addresses; replace them with public relay hostnames before production.", id="chat.E008"))

    firebase_project_id = str(getattr(settings, "FIREBASE_PROJECT_ID", "") or "").strip()
    firebase_service_account_setting = str(
        getattr(settings, "FIREBASE_SERVICE_ACCOUNT_PATH", "") or ""
    ).strip()
    firebase_service_account_path = resolve_firebase_service_account_path()
    firebase_required = bool(getattr(settings, "FIREBASE_PUSH_REQUIRED", False))
    firebase_configured = bool(firebase_project_id and firebase_service_account_path is not None)
    firebase_partially_configured = bool(firebase_project_id or firebase_service_account_setting) and not firebase_configured
    if firebase_required and not firebase_configured:
        issues.append(Error("Firebase Admin credentials must be configured for production push calling.", id="chat.E009"))
    elif firebase_partially_configured:
        issues.append(Error("Firebase Admin credentials are incomplete or the service-account file is unavailable.", id="chat.E009"))
    elif not firebase_configured:
        issues.append(Warning(
            "Firebase push is disabled; offline devices will not receive message or incoming-call notifications.",
            id="chat.W009",
        ))
    if firebase_configured and bool(getattr(settings, "FCM_DRY_RUN", True)):
        issues.append(Error("FCM_DRY_RUN must be disabled for production push delivery.", id="chat.E010"))

    if not getattr(settings, "CHAT_USE_S3_STORAGE", False):
        issues.append(Warning("CHAT_USE_S3_STORAGE is disabled; local media storage is not suitable for multi-instance production.", id="chat.W006"))
    if getattr(settings, "CHAT_USE_R2_STORAGE", False):
        missing_r2 = [
            name
            for name in (
                "CLOUDFLARE_R2_BUCKET_NAME",
                "CLOUDFLARE_R2_ACCESS_KEY_ID",
                "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
            )
            if not str(getattr(settings, name, "") or "").strip()
        ]
        if not getattr(settings, "AWS_S3_ENDPOINT_URL", ""):
            missing_r2.append("CLOUDFLARE_R2_ACCOUNT_ID or CLOUDFLARE_R2_ENDPOINT_URL")
        if missing_r2:
            issues.append(Error(f"Cloudflare R2 storage is enabled but missing: {', '.join(missing_r2)}.", id="chat.E016"))
        try:
            import boto3  # noqa: F401
            import storages  # noqa: F401
        except ImportError:
            issues.append(Error(
                "Cloudflare R2 is enabled but boto3/django-storages is not installed.",
                id="chat.E017",
            ))
        endpoint = str(getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "")
        if endpoint and not endpoint.startswith("https://"):
            issues.append(Error("Cloudflare R2 endpoint must use HTTPS.", id="chat.E018"))
        if getattr(settings, "AWS_S3_CUSTOM_DOMAIN", None):
            issues.append(Warning(
                "R2 custom domains are not used for private chat attachments; keep AWS_S3_CUSTOM_DOMAIN empty.",
                id="chat.W009",
            ))

    return issues
