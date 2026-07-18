from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportCSATSurvey,
    SupportConversation,
    SupportFeedbackSettings,
    SupportWebsite,
    SupportWebsiteAgent,
)

User = get_user_model()


@override_settings(
    SUPPORT_CHAT_ENABLED=True,
    SUPPORT_WIDGET_ENABLED=True,
    SUPPORT_WIDGET_REQUIRE_ORIGIN=True,
    SUPPORT_WIDGET_SESSION_TTL_HOURS=720,
)
class SupportAnalyticsFeedbackTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="analytics-owner",
            email="analytics-owner@example.com",
            password="pass",
        )
        self.agent_user = User.objects.create_user(
            username="analytics-agent",
            email="analytics-agent@example.com",
            password="pass",
        )
        self.restricted_agent_user = User.objects.create_user(
            username="restricted-agent",
            email="restricted-agent@example.com",
            password="pass",
        )
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            status=SupportAccount.Status.ACTIVE,
            plan_code="support-business",
            website_limit=3,
            agent_limit=3,
        )
        self.first = SupportWebsite.objects.create(
            support_account=self.account,
            name="Main website",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        self.second = SupportWebsite.objects.create(
            support_account=self.account,
            name="Products website",
            domain="products.example.com",
            allowed_origins=["https://products.example.com"],
        )
        self.agent = SupportAgent.objects.create(
            support_account=self.account,
            user=self.agent_user,
            invited_by=self.owner,
            can_view_all_conversations=True,
            can_view_analytics=True,
        )
        self.restricted_agent = SupportAgent.objects.create(
            support_account=self.account,
            user=self.restricted_agent_user,
            invited_by=self.owner,
            can_view_all_conversations=True,
            can_view_analytics=False,
        )
        SupportWebsiteAgent.objects.create(website=self.first, agent=self.agent)
        SupportWebsiteAgent.objects.create(website=self.first, agent=self.restricted_agent)

    def create_widget_conversation(self, website, origin, *, name="Visitor"):
        session_response = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/",
            {
                "name": name,
                "email": f"{name.lower().replace(' ', '-')}@example.com",
                "current_page_url": f"{origin}/help",
            },
            format="json",
            HTTP_ORIGIN=origin,
        )
        self.assertEqual(session_response.status_code, 201)
        session = session_response.data
        message_response = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session['id']}/conversation/messages/",
            {"text": "I need help"},
            format="json",
            HTTP_ORIGIN=origin,
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(message_response.status_code, 201)
        conversation = SupportConversation.objects.get(pk=message_response.data["conversation"]["id"])
        return session, conversation

    def resolve_with_owner_reply(self, conversation):
        self.client.force_authenticate(self.owner)
        reply = self.client.post(
            f"/api/v1/support/conversations/{conversation.id}/messages/",
            {"text": "This has been resolved."},
            format="json",
        )
        self.assertEqual(reply.status_code, 201)
        resolved = self.client.patch(
            f"/api/v1/support/conversations/{conversation.id}/",
            {"status": "resolved"},
            format="json",
        )
        self.assertEqual(resolved.status_code, 200)
        return resolved

    def test_owner_controls_feedback_settings_and_agent_cannot_update(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            "/api/v1/support/feedback-settings/",
            {
                "csat_enabled": True,
                "auto_request_on_resolve": False,
                "allow_comment": False,
                "survey_expiry_days": 14,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["auto_request_on_resolve"])
        self.assertFalse(response.data["allow_comment"])
        self.assertEqual(response.data["survey_expiry_days"], 14)

        self.client.force_authenticate(self.agent_user)
        denied = self.client.patch(
            "/api/v1/support/feedback-settings/",
            {"survey_expiry_days": 60},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

    def test_resolving_conversation_requests_feedback_and_widget_submits_once(self):
        session, conversation = self.create_widget_conversation(
            self.first,
            "https://main.example.com",
        )
        resolved = self.resolve_with_owner_reply(conversation)
        self.assertEqual(resolved.data["csat"]["status"], "pending")
        survey = SupportCSATSurvey.objects.get(support_conversation=conversation)
        self.assertEqual(survey.source, SupportCSATSurvey.Source.AUTO)

        self.client.force_authenticate(user=None)
        prompt = self.client.get(
            f"/api/v1/support/widget/{self.first.site_key}/sessions/{session['id']}/conversation/csat/",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(prompt.status_code, 200)
        self.assertTrue(prompt.data["enabled"])
        self.assertEqual(prompt.data["survey"]["status"], "pending")

        submitted = self.client.post(
            f"/api/v1/support/widget/{self.first.site_key}/sessions/{session['id']}/conversation/csat/",
            {"rating": 5, "comment": "Fast and helpful."},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.data["survey"]["rating"], 5)
        self.assertEqual(submitted.data["survey"]["comment"], "Fast and helpful.")

        duplicate = self.client.post(
            f"/api/v1/support/widget/{self.first.site_key}/sessions/{session['id']}/conversation/csat/",
            {"rating": 3},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_feedback_disabled_prevents_automatic_and_manual_requests(self):
        SupportFeedbackSettings.objects.create(
            support_account=self.account,
            csat_enabled=False,
            auto_request_on_resolve=True,
        )
        _, conversation = self.create_widget_conversation(self.first, "https://main.example.com")
        resolved = self.resolve_with_owner_reply(conversation)
        self.assertIsNone(resolved.data["csat"])
        manual = self.client.post(f"/api/v1/support/conversations/{conversation.id}/csat/")
        self.assertEqual(manual.status_code, 409)
        self.assertFalse(SupportCSATSurvey.objects.filter(support_conversation=conversation).exists())

    def test_analytics_are_permission_and_website_scoped(self):
        first_session, first_conversation = self.create_widget_conversation(
            self.first,
            "https://main.example.com",
            name="First Visitor",
        )
        self.resolve_with_owner_reply(first_conversation)
        self.client.force_authenticate(user=None)
        rating = self.client.post(
            f"/api/v1/support/widget/{self.first.site_key}/sessions/{first_session['id']}/conversation/csat/",
            {"rating": 4},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {first_session['token']}",
        )
        self.assertEqual(rating.status_code, 200)

        self.create_widget_conversation(
            self.second,
            "https://products.example.com",
            name="Second Visitor",
        )

        self.client.force_authenticate(self.owner)
        owner_report = self.client.get("/api/v1/support/analytics/overview/?days=30")
        self.assertEqual(owner_report.status_code, 200)
        self.assertEqual(owner_report.data["summary"]["conversations_created"], 2)
        self.assertEqual(owner_report.data["summary"]["csat_average"], 4.0)
        self.assertEqual(len(owner_report.data["websites"]), 2)

        self.client.force_authenticate(self.agent_user)
        agent_report = self.client.get("/api/v1/support/analytics/overview/?days=30")
        self.assertEqual(agent_report.status_code, 200)
        self.assertEqual(agent_report.data["summary"]["conversations_created"], 1)
        self.assertEqual([item["website"]["id"] for item in agent_report.data["websites"]], [str(self.first.id)])
        self.assertEqual(len(agent_report.data["agents"]), 1)

        denied_website = self.client.get(f"/api/v1/support/analytics/overview/?website={self.second.id}")
        self.assertEqual(denied_website.status_code, 403)

        self.client.force_authenticate(self.restricted_agent_user)
        denied = self.client.get("/api/v1/support/analytics/overview/")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "analytics_denied")

    def test_widget_session_cannot_read_another_websites_feedback(self):
        session, conversation = self.create_widget_conversation(self.first, "https://main.example.com")
        self.resolve_with_owner_reply(conversation)
        self.client.force_authenticate(user=None)
        denied = self.client.get(
            f"/api/v1/support/widget/{self.second.site_key}/sessions/{session['id']}/conversation/csat/",
            HTTP_ORIGIN="https://products.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertIn(denied.status_code, {401, 403, 404})
