import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.support.models import SupportAccount, SupportAgent, SupportWebsite


class Command(BaseCommand):
    help = "Validate Support Chat, widget, guest-call, plan-limit, and scheduled-task readiness."

    def add_arguments(self, parser):
        parser.add_argument("--fail-on-warning", action="store_true")

    def handle(self, *args, **options):
        warnings: list[str] = []
        failures: list[str] = []
        support_enabled = bool(getattr(settings, "SUPPORT_CHAT_ENABLED", False))
        widget_enabled = bool(getattr(settings, "SUPPORT_WIDGET_ENABLED", False))
        calls_enabled = bool(getattr(settings, "SUPPORT_CALLS_ENABLED", False))

        self.stdout.write(self.style.NOTICE("Support Chat readiness summary"))
        self.stdout.write(f"- Support Chat: {'enabled' if support_enabled else 'disabled'}")
        self.stdout.write(f"- Public widget: {'enabled' if widget_enabled else 'disabled'}")
        self.stdout.write(f"- Guest calls: {'enabled' if calls_enabled else 'disabled'}")
        self.stdout.write(f"- Origin enforcement: {bool(getattr(settings, 'SUPPORT_WIDGET_REQUIRE_ORIGIN', True))}")
        self.stdout.write(f"- Database engine: {connection.vendor}")
        self.stdout.write(f"- Upload scanning: {'async' if bool(getattr(settings, 'UPLOAD_SCAN_ASYNC', True)) else 'inline'}")
        self.stdout.write(f"- Antivirus: {'enabled' if bool(getattr(settings, 'CLAMAV_ENABLED', False)) else 'signature checks only'}")
        self.stdout.write(
            f"- Attachment storage: {'S3/R2' if bool(getattr(settings, 'CHAT_USE_S3_STORAGE', False)) else 'shared private media volume'}"
        )

        if (widget_enabled or calls_enabled) and not support_enabled:
            failures.append("Support Chat must be enabled before its widget or calls.")
        if widget_enabled and not bool(getattr(settings, "SUPPORT_WIDGET_REQUIRE_ORIGIN", True)):
            failures.append("Widget origin enforcement is disabled.")
        if not bool(getattr(settings, "CHAT_USE_S3_STORAGE", False)):
            private_media_root = str(getattr(settings, "PRIVATE_MEDIA_ROOT", "") or "")
            local_test_services = bool(getattr(settings, "USE_LOCAL_TEST_SERVICES", False))
            if (not private_media_root or not os.path.isdir(private_media_root)) and not local_test_services:
                failures.append("The private attachment media directory does not exist.")
            elif os.path.isdir(private_media_root) and not os.access(private_media_root, os.R_OK | os.W_OK):
                failures.append("The private attachment media directory is not readable and writable.")
        if calls_enabled:
            if not widget_enabled:
                failures.append("The public widget must be enabled for visitor calls.")
            turn_provider = str(getattr(settings, "TURN_PROVIDER", "legacy") or "legacy").strip().lower()
            self.stdout.write(f"- TURN provider: {turn_provider}")
            if turn_provider == "cloudflare":
                if not str(getattr(settings, "CLOUDFLARE_TURN_KEY_ID", "") or "").strip():
                    failures.append("CLOUDFLARE_TURN_KEY_ID is not configured.")
                if not str(getattr(settings, "CLOUDFLARE_TURN_API_TOKEN", "") or "").strip():
                    failures.append("CLOUDFLARE_TURN_API_TOKEN is not configured.")
            else:
                if not str(getattr(settings, "TURN_URIS_JSON", "") or "").strip():
                    failures.append("TURN_URIS_JSON is not configured for legacy TURN.")
                has_turn_auth = bool(str(getattr(settings, "TURN_SHARED_SECRET", "") or "").strip()) or bool(
                    str(getattr(settings, "TURN_STATIC_USERNAME", "") or "").strip()
                    and str(getattr(settings, "TURN_STATIC_PASSWORD", "") or "").strip()
                )
                if not has_turn_auth:
                    failures.append("Legacy TURN authentication is not configured.")
            if str(getattr(settings, "REALTIME_TRANSPORT", "") or "").lower() != "axum":
                failures.append("Guest calls require the Axum realtime transport.")
            ephemeral_backend = str(getattr(settings, "REALTIME_EPHEMERAL_BACKEND", "") or "").strip().lower()
            if ephemeral_backend not in {"nats", "local", "memory"}:
                failures.append("Guest calls require REALTIME_EPHEMERAL_BACKEND=nats or local.")


        beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None)
        if beat_schedule is None:
            try:
                from config.celery import app

                beat_schedule = app.conf.beat_schedule or {}
            except Exception:
                beat_schedule = {}
        required_tasks = {
            "apps.support.tasks.scan_support_service_operations",
            "apps.support.tasks.retry_pending_support_webhooks",
            "apps.support.tasks.run_support_retention",
            "apps.support.tasks.maintain_support_calls",
        }
        scheduled_tasks = {str(item.get("task")) for item in (beat_schedule or {}).values()}
        missing_tasks = sorted(required_tasks - scheduled_tasks)
        if missing_tasks:
            failures.append("Missing Celery Beat tasks: " + ", ".join(missing_tasks))

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            accounts = list(SupportAccount.objects.all())
            active_accounts = [item for item in accounts if item.status in {SupportAccount.Status.ACTIVE, SupportAccount.Status.TRIALING}]
            active_websites = SupportWebsite.objects.filter(support_account__in=active_accounts, is_active=True)
            active_agents = SupportAgent.objects.filter(support_account__in=active_accounts, is_active=True)
            self.stdout.write(f"- Active Support accounts: {len(active_accounts)}")
            self.stdout.write(f"- Active websites: {active_websites.count()}")
            self.stdout.write(f"- Active agents: {active_agents.count()}")
            if widget_enabled:
                missing_origins = active_websites.filter(widget_enabled=True, allowed_origins=[]).count()
                if missing_origins:
                    failures.append(f"{missing_origins} active widget website(s) have no allowed origins.")
            for account in active_accounts:
                website_count = account.websites.filter(is_active=True).count()
                agent_count = account.agents.filter(is_active=True).count()
                if website_count > account.website_limit:
                    warnings.append(f"Support account {account.id} exceeds its website plan limit.")
                if agent_count > account.agent_limit:
                    warnings.append(f"Support account {account.id} exceeds its agent plan limit.")
        except Exception as exc:
            failures.append(f"Support database readiness could not be checked: {exc}")

        if warnings:
            self.stdout.write(self.style.WARNING("\nWarnings:"))
            for warning in warnings:
                self.stdout.write(f"- {warning}")
        if failures:
            self.stdout.write(self.style.ERROR("\nFailures:"))
            for failure in failures:
                self.stdout.write(f"- {failure}")
            raise CommandError("Support Chat readiness check failed.")
        if warnings and options["fail_on_warning"]:
            raise CommandError("Support Chat readiness completed with warnings.")
        self.stdout.write(self.style.SUCCESS("\nSupport Chat configuration passed readiness checks."))
