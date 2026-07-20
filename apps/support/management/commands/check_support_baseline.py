import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F

from apps.support.models import SupportConversation, SupportWebsiteAgent


class Command(BaseCommand):
    help = "Check the non-destructive Support Chat isolation baseline before and after upgrades."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        checks = {
            "support_conversations": SupportConversation.objects.count(),
            "support_conversations_with_messenger_participants": SupportConversation.objects.annotate(
                participant_count=Count("conversation__participants")
            ).filter(participant_count__gt=0).count(),
            "cross_account_website_assignments": SupportWebsiteAgent.objects.exclude(
                website__support_account_id=F("agent__support_account_id")
            ).count(),
            "cross_account_assigned_agents": SupportConversation.objects.filter(
                assigned_agent__isnull=False
            ).exclude(
                website__support_account_id=F("assigned_agent__support_account_id")
            ).count(),
            "visitor_website_mismatches": SupportConversation.objects.exclude(
                website_id=F("visitor__website_id")
            ).count(),
        }
        failures = {
            key: value
            for key, value in checks.items()
            if key != "support_conversations" and value
        }
        payload = {"ok": not failures, "checks": checks, "failures": failures}
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(self.style.NOTICE("Support Chat baseline"))
            for key, value in checks.items():
                self.stdout.write(f"- {key}: {value}")
        if failures:
            raise CommandError("Support Chat baseline check failed.")
        self.stdout.write(self.style.SUCCESS("Support Chat baseline passed."))
