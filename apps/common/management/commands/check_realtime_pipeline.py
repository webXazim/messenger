from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.common.operational_health import realtime_pipeline_snapshot


class Command(BaseCommand):
    help = "Check PostgreSQL outbox and Redis Stream health for the Axum realtime pipeline."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--warn-only", action="store_true")

    def handle(self, *args, **options):
        snapshot = realtime_pipeline_snapshot()
        payload = snapshot.to_dict()
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
        else:
            self.stdout.write(f"Realtime pipeline: {'healthy' if snapshot.ok else 'degraded'}")
            self.stdout.write(
                "Outbox pending={outbox_pending} processing={outbox_processing} "
                "failed={outbox_failed} oldest_unpublished_age={oldest_unpublished_age_seconds}".format(**payload)
            )
            self.stdout.write(
                "Redis stream length={stream_length} group={consumer_group} "
                "group_exists={consumer_group_exists} pending={consumer_pending} lag={consumer_lag}".format(**payload)
            )
            if not snapshot.redis_ok:
                self.stderr.write(f"Redis detail: {snapshot.redis_detail}")
        if not snapshot.ok and not options["warn_only"]:
            raise CommandError("Realtime pipeline is degraded")
