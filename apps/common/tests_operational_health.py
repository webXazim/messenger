from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common.models import RealtimeOutboxEvent
from apps.common.operational_health import realtime_pipeline_snapshot


@override_settings(
    REALTIME_OUTBOX_MAX_AGE_SECONDS=120,
    REALTIME_OUTBOX_MAX_FAILED=2,
    NATS_CHAT_STREAM="CHAT_EVENTS",
    NATS_DURABLE_CONSUMER="realtime-axum-v1",
)
class RealtimeOperationalHealthTests(TestCase):
    def test_pipeline_is_healthy_without_outbox_backlog(self):
        snapshot = realtime_pipeline_snapshot()
        self.assertTrue(snapshot.ok)
        self.assertEqual(snapshot.stream_name, "CHAT_EVENTS")
        self.assertEqual(snapshot.outbox_pending, 0)

    def test_old_unpublished_event_degrades_pipeline(self):
        row = RealtimeOutboxEvent.objects.create(
            event_name="message.created",
            payload={},
            audiences=[],
            status=RealtimeOutboxEvent.Status.PENDING,
            delivery_target="nats_jetstream",
        )
        RealtimeOutboxEvent.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )
        snapshot = realtime_pipeline_snapshot()
        self.assertFalse(snapshot.ok)
        self.assertGreater(snapshot.oldest_unpublished_age_seconds or 0, 120)
