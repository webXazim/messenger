import json

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.support.feature_flags import support_feature_snapshot
from apps.support.models import (
    SupportAccount,
    SupportAnalyticsDailyMetric,
    SupportAutomationRule,
    SupportConversation,
    SupportRoutingPolicy,
    SupportSlaPolicy,
)


class Command(BaseCommand):
    help = "Run non-destructive production readiness checks for Support Chat."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--strict", action="store_true")

    def handle(self, *args, **options):
        checks = []

        def add(name, ok, detail, critical=True):
            checks.append({
                "name": name,
                "ok": bool(ok),
                "detail": str(detail),
                "critical": bool(critical),
            })

        try:
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                add("database", cursor.fetchone()[0] == 1, "Database connection is healthy.")
        except Exception as exc:
            add("database", False, exc)

        try:
            cache_key = f"support-readiness:{timezone.now().timestamp()}"
            cache.set(cache_key, "ok", timeout=30)
            add("cache", cache.get(cache_key) == "ok", "Cache read/write succeeded.")
            cache.delete(cache_key)
        except Exception as exc:
            add("cache", False, exc)

        add(
            "support_enabled",
            bool(getattr(settings, "SUPPORT_CHAT_ENABLED", False)),
            f'SUPPORT_CHAT_ENABLED={getattr(settings, "SUPPORT_CHAT_ENABLED", False)}',
        )
        add(
            "https_configuration",
            bool(getattr(settings, "SECURE_SSL_REDIRECT", False)) or bool(getattr(settings, "DEBUG", False)),
            "HTTPS redirect is enabled, or DEBUG is active for development.",
        )
        add(
            "secret_key",
            len(str(getattr(settings, "SECRET_KEY", ""))) >= 32,
            "Django secret key has a production-safe minimum length.",
        )

        flags = support_feature_snapshot()
        add("feature_flags", True, json.dumps(flags, sort_keys=True), critical=False)

        try:
            call_command("check", verbosity=0)
            add("django_checks", True, "Django system checks passed.")
        except Exception as exc:
            add("django_checks", False, exc)

        add(
            "support_data",
            True,
            (
                f"accounts={SupportAccount.objects.count()}, "
                f"conversations={SupportConversation.objects.count()}, "
                f"routing_policies={SupportRoutingPolicy.objects.count()}, "
                f"sla_policies={SupportSlaPolicy.objects.count()}, "
                f"analytics_rows={SupportAnalyticsDailyMetric.objects.count()}, "
                f"automation_rules={SupportAutomationRule.objects.count()}"
            ),
            critical=False,
        )

        failed = [item for item in checks if item["critical"] and not item["ok"]]
        payload = {
            "ready": not failed,
            "checked_at": timezone.now().isoformat(),
            "checks": checks,
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            for item in checks:
                marker = "PASS" if item["ok"] else "FAIL"
                self.stdout.write(f"[{marker}] {item['name']}: {item['detail']}")
            self.stdout.write(
                self.style.SUCCESS("Support Chat is ready.")
                if payload["ready"]
                else self.style.ERROR("Support Chat is not ready.")
            )

        if options["strict"] and failed:
            raise SystemExit(1)
