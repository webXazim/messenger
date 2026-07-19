from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common.models import RealtimeOutboxEvent
from apps.common.operational_health import realtime_pipeline_snapshot


@override_settings(
    REALTIME_STREAM_ENABLED=True,
    REALTIME_STREAM_URL="redis://test/3",
    REALTIME_STREAM_NAME="realtime:test",
    REALTIME_STREAM_GROUP="axum-test",
    REALTIME_OUTBOX_MAX_AGE_SECONDS=120,
    REALTIME_OUTBOX_MAX_FAILED=2,
    REALTIME_STREAM_MAX_PENDING=10,
)
class RealtimeOperationalHealthTests(TestCase):
    def redis_client(self, *, pending=0, lag=0):
        client = Mock()
        client.ping.return_value = True
        client.xlen.return_value = 5
        client.xinfo_groups.return_value = [
            {"name": "axum-test", "pending": pending, "lag": lag}
        ]
        return client

    @patch("apps.common.operational_health._redis_client")
    def test_pipeline_is_healthy_with_current_stream_and_no_backlog(self, redis_client):
        redis_client.return_value = self.redis_client()
        snapshot = realtime_pipeline_snapshot()
        self.assertTrue(snapshot.ok)
        self.assertTrue(snapshot.consumer_group_exists)
        self.assertEqual(snapshot.consumer_pending, 0)

    @patch("apps.common.operational_health._redis_client")
    def test_old_unpublished_event_degrades_pipeline(self, redis_client):
        redis_client.return_value = self.redis_client()
        row = RealtimeOutboxEvent.objects.create(
            event_name="message.created",
            payload={},
            audiences=[],
            status=RealtimeOutboxEvent.Status.PENDING,
        )
        RealtimeOutboxEvent.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )
        snapshot = realtime_pipeline_snapshot()
        self.assertFalse(snapshot.ok)
        self.assertGreater(snapshot.oldest_unpublished_age_seconds or 0, 120)
