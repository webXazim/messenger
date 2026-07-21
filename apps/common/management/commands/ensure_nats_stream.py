from django.core.management.base import BaseCommand, CommandError

from apps.common.nats_durable import ensure_stream_sync


class Command(BaseCommand):
    help = "Create or verify the configured NATS JetStream durable event stream."

    def handle(self, *args, **options):
        try:
            ensure_stream_sync()
        except Exception as exc:
            raise CommandError(f"Unable to initialize NATS JetStream: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("NATS JetStream is ready."))
