from django.conf import settings
from django.core.management.base import BaseCommand

from apps.chat.tasks import integration_health_snapshot
from config.health import cache_ready, database_ready, migrations_ready


class Command(BaseCommand):
    help = "Print a deployment readiness summary for the messenger backend."

    def handle(self, *args, **options):
        db_ok, db_detail = database_ready()
        cache_ok, cache_detail = cache_ready()
        migrations_ok, migrations_detail = migrations_ready()
        integrations = integration_health_snapshot()
        channel_backend = settings.CHANNEL_LAYERS.get("default", {}).get("BACKEND", "")
        channel_hosts = settings.CHANNEL_LAYERS.get("default", {}).get("CONFIG", {}).get("hosts", [])
        cache_backend = settings.CACHES.get("default", {}).get("BACKEND", "")

        rows = [
            ("service", getattr(settings, "SERVICE_NAME", "messenger_api")),
            ("version", getattr(settings, "APP_VERSION", "v24")),
            ("debug", settings.DEBUG),
            ("database", f"{'ok' if db_ok else 'fail'} ({db_detail})"),
            ("cache", f"{'ok' if cache_ok else 'fail'} ({cache_detail})"),
            ("migrations", f"{'ok' if migrations_ok else 'fail'} ({migrations_detail})"),
            ("db engine", settings.DB_ENGINE),
            ("channel layer backend", channel_backend),
            ("channel layer hosts", channel_hosts or "in-memory"),
            ("cache backend", cache_backend),
            ("ssl redirect", settings.SECURE_SSL_REDIRECT),
            ("hsts seconds", settings.SECURE_HSTS_SECONDS),
            ("session cookie secure", settings.SESSION_COOKIE_SECURE),
            ("csrf cookie secure", settings.CSRF_COOKIE_SECURE),
            ("db conn max age", settings.DATABASE_CONN_MAX_AGE),
            ("celery eager", settings.CELERY_TASK_ALWAYS_EAGER),
            ("upload scan async", settings.UPLOAD_SCAN_ASYNC),
            ("email backend", settings.EMAIL_BACKEND),
            ("clamav enabled", integrations.get("antivirus", {}).get("enabled")),
            ("clamav available", integrations.get("antivirus", {}).get("available")),
            ("push configured", integrations.get("push", {}).get("configured")),
            ("push dry run", integrations.get("push", {}).get("dry_run")),
        ]

        self.stdout.write(self.style.NOTICE("Messenger backend readiness summary"))
        for key, value in rows:
            self.stdout.write(f"- {key}: {value}")

        issues = []
        if settings.DEBUG:
            issues.append("DEBUG is enabled")
        if not settings.SECURE_SSL_REDIRECT:
            issues.append("SECURE_SSL_REDIRECT is disabled")
        if not settings.SESSION_COOKIE_SECURE:
            issues.append("SESSION_COOKIE_SECURE is disabled")
        if not settings.CSRF_COOKIE_SECURE:
            issues.append("CSRF_COOKIE_SECURE is disabled")
        if settings.DB_ENGINE != "postgres":
            issues.append("DB_ENGINE is not postgres")
        if not migrations_ok:
            issues.append("There are unapplied migrations")
        if not cache_ok:
            issues.append("Cache is not healthy")
        if not db_ok:
            issues.append("Database is not healthy")
        if str(channel_backend).endswith("InMemoryChannelLayer"):
            issues.append("Channel layer is in-memory")
        if str(cache_backend).endswith("LocMemCache"):
            issues.append("Cache backend is local memory")
        if settings.CELERY_TASK_ALWAYS_EAGER:
            issues.append("Celery tasks are running eagerly")
        if not settings.UPLOAD_SCAN_ASYNC:
            issues.append("Upload scanning is running on the request path")
        if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
            issues.append("Email backend is console")

        if issues:
            self.stdout.write(self.style.WARNING("\nReadiness warnings:"))
            for issue in issues:
                self.stdout.write(f"- {issue}")
        else:
            self.stdout.write(self.style.SUCCESS("\nNo readiness warnings detected."))
