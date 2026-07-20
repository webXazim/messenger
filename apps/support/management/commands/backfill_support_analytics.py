from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.support.analytics_aggregates import aggregate_support_day
from apps.support.models import SupportAccount


class Command(BaseCommand):
    help = "Backfill bounded Support analytics aggregates safely."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--account-id", default="")
        parser.add_argument("--start", default="")
        parser.add_argument("--end", default="")

    def handle(self, *args, **options):
        days = max(1, min(366, int(options["days"] or 90)))
        if options["start"] or options["end"]:
            if not (options["start"] and options["end"]):
                raise CommandError("Use both --start and --end.")
            try:
                start_day = date.fromisoformat(options["start"])
                end_day = date.fromisoformat(options["end"])
            except ValueError as exc:
                raise CommandError("Dates must use YYYY-MM-DD.") from exc
            if start_day > end_day or (end_day - start_day).days > 365:
                raise CommandError("Choose a valid range of at most 366 days.")
        else:
            end_day = timezone.localdate()
            start_day = end_day - timedelta(days=days - 1)

        accounts = SupportAccount.objects.filter(is_active=True)
        if options["account_id"]:
            accounts = accounts.filter(pk=options["account_id"])
            if not accounts.exists():
                raise CommandError("Support account was not found.")

        processed = 0
        for account in accounts.iterator():
            cursor = start_day
            while cursor <= end_day:
                aggregate_support_day(account, cursor)
                processed += 1
                self.stdout.write(
                    f"Aggregated account={account.id} date={cursor.isoformat()}"
                )
                cursor += timedelta(days=1)
        self.stdout.write(self.style.SUCCESS(f"Completed {processed} account-day aggregates."))
