from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Q

from apps.support.conversation_services import (
    support_conversations_for_context,
    with_support_inbox_metrics,
)
from apps.support.services import get_support_context


class Command(BaseCommand):
    help = "Print the database execution plan for the optimized Support Inbox query."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Support owner/agent username or email.")
        parser.add_argument("--limit", type=int, default=50, help="Rows represented in the plan (1-500).")
        parser.add_argument(
            "--analyze",
            action="store_true",
            help="Execute the SELECT and include actual timings. Use only during controlled diagnostics.",
        )

    def handle(self, *args, **options):
        identity = options["user"].strip()
        user = (
            get_user_model().objects.filter(
                Q(username__iexact=identity) | Q(email__iexact=identity)
            ).first()
        )
        if not user:
            raise CommandError("No user matched --user.")

        context = get_support_context(user)
        if not context.account:
            raise CommandError("The selected user has no active Support Chat context.")

        limit = min(500, max(1, int(options["limit"])))
        queryset = (
            with_support_inbox_metrics(
                support_conversations_for_context(context),
                user,
            )
            .order_by("-conversation__last_message_at", "-created_at")
            .values(
                "id",
                "website_id",
                "conversation_id",
                "prefetched_team_unread_count",
                "prefetched_visitor_unread_count",
            )[:limit]
        )

        if connection.vendor != "postgresql":
            self.stderr.write(
                self.style.WARNING(
                    "This command is intended for PostgreSQL; the current database may show a less useful plan."
                )
            )

        explain_options = {"analyze": bool(options["analyze"])}
        if connection.vendor == "postgresql" and options["analyze"]:
            explain_options.update({"buffers": True, "verbose": True})
        self.stdout.write(queryset.explain(**explain_options))
