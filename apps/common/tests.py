from unittest.mock import patch
from uuid import UUID

from django.test import TestCase, override_settings

from apps.common.models import RealtimeOutboxEvent
from apps.common.realtime import (
    conversation_audience,
    make_realtime_event,
    publish_realtime_event,
    support_website_audience,
    user_audience,
)
from apps.common.tasks import publish_realtime_outbox_events


class RealtimeEnvelopeTests(TestCase):
    def test_event_envelope_is_versioned_and_json_safe(self):
        event = make_realtime_event(
            "message.created",
            {"message_id": UUID("12345678-1234-5678-1234-567812345678")},
        )

        self.assertEqual(event["type"], "chat.event")
        self.assertEqual(event["version"], 1)
        self.assertEqual(event["event"], "message.created")
        self.assertEqual(event["data"]["message_id"], "12345678-1234-5678-1234-567812345678")
        self.assertTrue(event["event_id"])
        self.assertTrue(event["occurred_at"])


class RealtimePublisherTests(TestCase):
    @override_settings(
        REALTIME_OUTBOX_ENABLED=True,
        REALTIME_STREAM_ENABLED=True,
        REALTIME_STREAM_URL="redis://example.invalid/3",
    )
    @patch("apps.common.realtime.publish_outbox_event_to_stream", return_value="1-0")
    def test_durable_event_is_recorded_and_published_to_axum_stream(self, stream_delivery):
        event = publish_realtime_event(
            event_name="message.created",
            data={"message_id": "message-1"},
            audiences=[conversation_audience("conversation-1")],
            defer_until_commit=False,
        )

        stream_delivery.assert_called_once()
        row = RealtimeOutboxEvent.objects.get(event_id=event["event_id"])
        self.assertEqual(row.status, RealtimeOutboxEvent.Status.PUBLISHED)
        self.assertEqual(row.delivery_target, "redis_stream")
        self.assertEqual(row.published_transport, "redis_stream")
        self.assertEqual(row.stream_entry_id, "1-0")
        self.assertEqual(row.audiences, [{"kind": "conversation", "id": "conversation-1"}])

    @override_settings(REALTIME_STREAM_ENABLED=True, REALTIME_STREAM_URL="redis://example.invalid/3")
    @patch("apps.common.realtime.publish_event_to_stream", return_value="2-0")
    def test_disposable_event_skips_database_outbox(self, stream_delivery):
        event = publish_realtime_event(
            event_name="presence.updated",
            data={"is_online": True},
            audiences=[user_audience("user-1")],
            durable=False,
            defer_until_commit=False,
        )

        stream_delivery.assert_called_once()
        self.assertFalse(RealtimeOutboxEvent.objects.filter(event_id=event["event_id"]).exists())


class RealtimeOutboxTaskTests(TestCase):
    @override_settings(
        REALTIME_STREAM_ENABLED=True,
        REALTIME_STREAM_URL="redis://example.invalid/3",
        REALTIME_OUTBOX_BATCH_SIZE=10,
    )
    @patch("apps.common.tasks.publish_outbox_event_to_stream", return_value="2-0")
    def test_retry_task_publishes_pending_rows(self, publish):
        row = RealtimeOutboxEvent.objects.create(
            event_name="message.created",
            payload={"event": "message.created"},
            audiences=[{"kind": "conversation", "id": "conversation-1"}],
            delivery_target="redis_stream",
        )

        result = publish_realtime_outbox_events.run()

        self.assertEqual(result["published"], 1)
        publish.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.status, RealtimeOutboxEvent.Status.PUBLISHED)
        self.assertEqual(row.stream_entry_id, "2-0")


class QueryMetricsMiddlewareTests(TestCase):
    @override_settings(
        DJANGO_QUERY_METRICS_ENABLED=True,
        DJANGO_QUERY_METRICS_LOG_ALL=True,
        DEBUG=True,
        DJANGO_QUERY_METRICS_EXCLUDE_PREFIXES=(),
    )
    def test_metrics_add_debug_headers_without_logging_sql(self):
        from django.http import JsonResponse
        from django.test import RequestFactory
        from config.middleware import QueryMetricsMiddleware

        request = RequestFactory().get("/api/v1/test-performance/")

        def view(_request):
            RealtimeOutboxEvent.objects.exists()
            return JsonResponse({"ok": True})

        with self.assertLogs("performance.django", level="INFO") as captured:
            response = QueryMetricsMiddleware(view)(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Server-Timing", response)
        self.assertGreaterEqual(int(response["X-DB-Query-Count"]), 1)
        combined = " ".join(captured.output)
        self.assertIn("request_performance", combined)
        self.assertNotIn("SELECT", combined.upper())
