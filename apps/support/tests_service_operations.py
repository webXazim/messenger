from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportConversation,
    SupportServiceAlert,
    SupportServiceSettings,
    SupportWebsite,
    SupportWebsiteAgent,
)
from apps.support.service_operations import (
    add_service_minutes,
    generate_service_alerts,
    scan_service_operations,
)

User = get_user_model()


@override_settings(
    SUPPORT_CHAT_ENABLED=True,
    SUPPORT_WIDGET_ENABLED=True,
    SUPPORT_WIDGET_REQUIRE_ORIGIN=True,
    SUPPORT_WIDGET_SESSION_TTL_HOURS=720,
)
class SupportServiceOperationsTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="service-owner",
            email="service-owner@example.com",
            password="pass",
        )
        self.agent_user = User.objects.create_user(
            username="service-agent",
            email="service-agent@example.com",
            password="pass",
        )
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            status=SupportAccount.Status.ACTIVE,
            plan_code="support-business",
            website_limit=3,
            agent_limit=3,
        )
        self.website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        self.agent = SupportAgent.objects.create(
            support_account=self.account,
            user=self.agent_user,
            invited_by=self.owner,
            can_view_all_conversations=True,
            can_assign_conversations=True,
        )
        SupportWebsiteAgent.objects.create(website=self.website, agent=self.agent)
        self.service_settings = SupportServiceSettings.objects.create(
            support_account=self.account,
            timezone="Asia/Riyadh",
            business_hours_enabled=False,
            first_response_targets={"low": 40, "normal": 30, "high": 20, "urgent": 10},
            next_response_targets={"low": 80, "normal": 60, "high": 40, "urgent": 20},
            resolution_targets={"low": 240, "normal": 180, "high": 120, "urgent": 60},
            due_soon_minutes=15,
        )

    def create_conversation(self):
        session_response = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/",
            {
                "name": "Visitor",
                "email": "visitor@example.com",
                "current_page_url": "https://main.example.com/help",
            },
            format="json",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(session_response.status_code, 201)
        session = session_response.data
        message_response = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{session['id']}/conversation/messages/",
            {"text": "I need help"},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(message_response.status_code, 201)
        conversation = SupportConversation.objects.get(
            pk=message_response.data["conversation"]["id"]
        )
        return session, conversation

    def test_business_minutes_skip_closed_days(self):
        self.service_settings.business_hours_enabled = True
        self.service_settings.business_hours = {
            "monday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "tuesday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "wednesday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "thursday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "friday": {"enabled": False, "start": "09:00", "end": "17:00"},
            "saturday": {"enabled": False, "start": "09:00", "end": "17:00"},
            "sunday": {"enabled": True, "start": "09:00", "end": "17:00"},
        }
        self.service_settings.save()
        riyadh = ZoneInfo("Asia/Riyadh")
        thursday = datetime(2026, 7, 16, 16, 30, tzinfo=riyadh)
        due = add_service_minutes(thursday, 60, self.service_settings).astimezone(riyadh)
        self.assertEqual(due.weekday(), 6)
        self.assertEqual((due.hour, due.minute), (9, 30))

    def test_visitor_message_initializes_targets_and_widget_hides_service_data(self):
        session, conversation = self.create_conversation()
        conversation.refresh_from_db()
        self.assertIsNotNone(conversation.first_response_due_at)
        self.assertIsNotNone(conversation.resolution_due_at)
        self.assertIsNone(conversation.next_response_due_at)

        widget_history = self.client.get(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{session['id']}/conversation/messages/",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(widget_history.status_code, 200)
        self.assertNotIn("service", widget_history.data["conversation"])
        self.assertNotIn("follow_up", str(widget_history.data))

    def test_team_reply_completes_first_response_and_clears_active_response_alerts(self):
        _, conversation = self.create_conversation()
        conversation.first_response_due_at = timezone.now() - timedelta(minutes=1)
        conversation.assigned_agent = self.agent
        conversation.save(update_fields=["first_response_due_at", "assigned_agent", "updated_at"])
        generate_service_alerts(conversation)
        self.assertEqual(SupportServiceAlert.objects.filter(status="unread").count(), 2)

        self.client.force_authenticate(self.agent_user)
        response = self.client.post(
            f"/api/v1/support/conversations/{conversation.id}/messages/",
            {"text": "We are checking this now."},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        conversation.refresh_from_db()
        self.assertIsNotNone(conversation.first_response_at)
        self.assertIsNone(conversation.next_response_due_at)
        self.assertFalse(
            SupportServiceAlert.objects.filter(
                support_conversation=conversation,
                status="unread",
                kind__startswith="first_response",
            ).exists()
        )

    def test_follow_up_queue_and_clear_are_private_team_actions(self):
        _, conversation = self.create_conversation()
        self.client.force_authenticate(self.owner)
        follow_up_at = timezone.now() + timedelta(hours=1)
        response = self.client.patch(
            f"/api/v1/support/conversations/{conversation.id}/",
            {
                "follow_up_at": follow_up_at.isoformat(),
                "follow_up_note": "Check whether billing replied.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["service"]["follow_up_note"], "Check whether billing replied.")

        conversation.refresh_from_db()
        conversation.follow_up_at = timezone.now() - timedelta(minutes=1)
        conversation.save(update_fields=["follow_up_at", "updated_at"])
        queue_response = self.client.get("/api/v1/support/conversations/?queue=follow_up")
        self.assertEqual(queue_response.status_code, 200)
        self.assertEqual(queue_response.data["count"], 1)

        clear_response = self.client.patch(
            f"/api/v1/support/conversations/{conversation.id}/",
            {"follow_up_at": None, "follow_up_note": ""},
            format="json",
        )
        self.assertEqual(clear_response.status_code, 200)
        conversation.refresh_from_db()
        self.assertIsNone(conversation.follow_up_at)
        self.assertIsNotNone(conversation.follow_up_completed_at)

    def test_alerts_are_deduplicated_and_recipient_scoped(self):
        _, conversation = self.create_conversation()
        conversation.assigned_agent = self.agent
        conversation.first_response_due_at = timezone.now() - timedelta(minutes=2)
        conversation.save(update_fields=["assigned_agent", "first_response_due_at", "updated_at"])
        self.assertEqual(generate_service_alerts(conversation), 2)
        self.assertEqual(generate_service_alerts(conversation), 0)

        self.client.force_authenticate(self.agent_user)
        agent_alerts = self.client.get("/api/v1/support/service-alerts/")
        self.assertEqual(agent_alerts.status_code, 200)
        self.assertEqual(agent_alerts.data["unread_count"], 1)
        alert_id = agent_alerts.data["results"][0]["id"]
        marked = self.client.post(f"/api/v1/support/service-alerts/{alert_id}/read/")
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(marked.data["status"], "read")

        other = User.objects.create_user(username="unrelated", email="unrelated@example.com", password="pass")
        self.client.force_authenticate(other)
        denied = self.client.get("/api/v1/support/service-alerts/")
        self.assertEqual(denied.status_code, 403)

    def test_owner_updates_settings_and_agent_cannot(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            "/api/v1/support/service-settings/",
            {
                "timezone": "Asia/Riyadh",
                "business_hours_enabled": False,
                "due_soon_minutes": 20,
                "default_follow_up_minutes": 120,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["due_soon_minutes"], 20)

        self.client.force_authenticate(self.agent_user)
        denied = self.client.patch(
            "/api/v1/support/service-settings/",
            {"due_soon_minutes": 5},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

    def test_scan_initializes_legacy_conversations_and_creates_due_alerts(self):
        _, conversation = self.create_conversation()
        conversation.first_response_due_at = None
        conversation.resolution_due_at = None
        conversation.save(update_fields=["first_response_due_at", "resolution_due_at", "updated_at"])
        scan_service_operations()
        conversation.refresh_from_db()
        self.assertIsNotNone(conversation.first_response_due_at)
        self.assertIsNotNone(conversation.resolution_due_at)
