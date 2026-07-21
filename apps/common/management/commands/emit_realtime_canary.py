from __future__ import annotations

import time
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.models import RealtimeOutboxEvent
from apps.common.realtime import publish_realtime_event, user_audience


class Command(BaseCommand):
    help = "Publish one durable no-recipient realtime canary and wait for JetStream publication."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=20)

    def handle(self, *args, **options):
        timeout = max(3, min(int(options["timeout"]), 120))
        canary_id = str(uuid.uuid4())
        with transaction.atomic():
            event = publish_realtime_event(
                event_name="operations.canary",
                data={"canary_id": canary_id},
                audiences=[user_audience(f"operations-canary-{canary_id}")],
                durable=True,
            )
        if not event:
            raise CommandError("The canary event could not be created.")
        event_id = event["event_id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = RealtimeOutboxEvent.objects.filter(event_id=event_id).only(
                "status", "stream_entry_id", "last_error"
            ).first()
            if row and row.status == RealtimeOutboxEvent.Status.PUBLISHED and row.stream_entry_id:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Realtime canary published event_id={event_id} stream_id={row.stream_entry_id}"
                    )
                )
                return
            if row and row.status == RealtimeOutboxEvent.Status.FAILED:
                raise CommandError(f"Realtime canary failed: {row.last_error}")
            time.sleep(0.5)
        raise CommandError(f"Realtime canary did not publish within {timeout} seconds: {event_id}")
