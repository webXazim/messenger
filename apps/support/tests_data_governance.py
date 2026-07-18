from __future__ import annotations

import io
import json
import tempfile
import zipfile
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.chat.models import Conversation, ConversationParticipant, Message
from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportConversation,
    SupportDataExport,
    SupportPrivacySettings,
    SupportVisitor,
    SupportVisitorDeletionRequest,
    SupportWebhookDelivery,
    SupportWebhookEndpoint,
    SupportWebsite,
    SupportWebsiteAgent,
    SupportWidgetSession,
)
from apps.support.privacy_services import generate_support_export, process_visitor_deletion, run_support_retention
from apps.support.webhook_services import deliver_webhook

User = get_user_model()


@override_settings(
    SUPPORT_CHAT_ENABLED=True,
    SUPPORT_WIDGET_ENABLED=True,
    SUPPORT_WIDGET_REQUIRE_ORIGIN=True,
    SUPPORT_WIDGET_SESSION_TTL_HOURS=720,
    DEBUG=True,
    SUPPORT_WEBHOOK_MAX_ATTEMPTS=3,
)
class SupportDataGovernanceTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="privacy-owner", email="owner@example.com", password="pass")
        self.agent_user = User.objects.create_user(username="privacy-agent", email="agent@example.com", password="pass")
        self.friend = User.objects.create_user(username="personal-friend", email="friend@example.com", password="pass")
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            status=SupportAccount.Status.ACTIVE,
            plan_code="support-business",
            website_limit=2,
            agent_limit=2,
        )
        self.website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Main website",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        self.agent = SupportAgent.objects.create(support_account=self.account, user=self.agent_user, invited_by=self.owner)
        SupportWebsiteAgent.objects.create(website=self.website, agent=self.agent)
        self.tempdir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.tempdir.name)
        self.override.enable()

    def tearDown(self):
        cache.clear()
        self.override.disable()
        self.tempdir.cleanup()
        super().tearDown()

    def create_widget_conversation(self, *, name="Visitor"):
        self.client.force_authenticate(None)
        session_response = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/",
            {"name": name, "email": "visitor@example.com", "current_page_url": "https://main.example.com/help"},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(session_response.status_code, 201)
        session = session_response.data
        message_response = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{session['id']}/conversation/messages/",
            {"text": "Support-only visitor message"},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(message_response.status_code, 201)
        conversation = SupportConversation.objects.get(pk=message_response.data["conversation"]["id"])
        return session, conversation

    def create_personal_message(self):
        conversation = Conversation.objects.create(type=Conversation.ConversationType.DIRECT, created_by=self.owner, title="Personal")
        ConversationParticipant.objects.create(conversation=conversation, user=self.owner)
        ConversationParticipant.objects.create(conversation=conversation, user=self.friend)
        message = Message.objects.create(conversation=conversation, sender=self.owner, text="PRIVATE MESSENGER TEXT")
        conversation.last_message = message
        conversation.last_message_at = message.created_at
        conversation.save(update_fields=["last_message", "last_message_at", "updated_at"])
        return conversation, message

    def test_owner_controls_privacy_settings_and_agent_is_denied(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            "/api/v1/support/privacy/settings/",
            {
                "retention_enabled": True,
                "resolved_conversation_retention_days": 365,
                "widget_session_retention_days": 60,
                "export_retention_days": 5,
                "allow_visitor_deletion_requests": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["retention_enabled"])
        self.assertEqual(response.data["resolved_conversation_retention_days"], 365)

        self.client.force_authenticate(self.agent_user)
        denied = self.client.get("/api/v1/support/privacy/settings/")
        self.assertEqual(denied.status_code, 403)

    @patch("apps.support.webhook_services.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    def test_webhook_secret_is_shown_once_and_delivery_is_signed(self, _resolve):
        self.client.force_authenticate(self.owner)
        created = self.client.post(
            "/api/v1/support/webhooks/",
            {
                "name": "CRM",
                "url": "https://hooks.example.com/support",
                "event_types": ["message.created", "conversation.updated"],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.data["signing_secret"])
        endpoint = SupportWebhookEndpoint.objects.get(pk=created.data["id"])

        listed = self.client.get("/api/v1/support/webhooks/")
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("signing_secret", listed.data["endpoints"][0])

        delivery = SupportWebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type="message.created",
            payload={"id": "event", "type": "message.created", "data": {"message_id": "1"}},
        )
        response = Mock(status_code=204, text="")
        with patch("apps.support.webhook_services.requests.post", return_value=response) as post, patch(
            "apps.support.webhook_services.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ):
            result = deliver_webhook(delivery)
        self.assertEqual(result.status, SupportWebhookDelivery.Status.SUCCEEDED)
        headers = post.call_args.kwargs["headers"]
        self.assertTrue(headers["X-Support-Signature"].startswith("sha256="))
        self.assertEqual(headers["X-Support-Event"], "message.created")

    def test_export_contains_support_data_and_excludes_personal_messenger(self):
        _, support_conversation = self.create_widget_conversation()
        Message.objects.filter(conversation=support_conversation.conversation).update(
            text='=HYPERLINK("https://malicious.example", "visitor text")'
        )
        personal_conversation, _ = self.create_personal_message()
        self.client.force_authenticate(self.owner)
        export = SupportDataExport.objects.create(
            support_account=self.account,
            requested_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )
        export = generate_support_export(export)
        self.assertEqual(export.status, SupportDataExport.Status.READY)
        with export.file.open("rb") as handle:
            data = handle.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            messages_csv = archive.read("messages.csv").decode("utf-8")
            conversations_csv = archive.read("conversations.csv").decode("utf-8")
        self.assertFalse(manifest["includes_personal_messenger"])
        self.assertIn("'=HYPERLINK", messages_csv)
        self.assertNotIn("PRIVATE MESSENGER TEXT", messages_csv)
        self.assertIn(str(support_conversation.id), conversations_csv)
        self.assertNotIn(str(personal_conversation.id), conversations_csv)

    def test_owner_deletion_removes_support_visitor_but_not_personal_messenger(self):
        _, support_conversation = self.create_widget_conversation()
        visitor_id = support_conversation.visitor_id
        personal_conversation, personal_message = self.create_personal_message()
        deletion = SupportVisitorDeletionRequest.objects.create(
            support_account=self.account,
            website=self.website,
            visitor=support_conversation.visitor,
            visitor_external_id=support_conversation.visitor.external_id,
            source=SupportVisitorDeletionRequest.Source.OWNER,
            requested_by=self.owner,
        )
        deletion = process_visitor_deletion(deletion)
        self.assertEqual(deletion.status, SupportVisitorDeletionRequest.Status.COMPLETED)
        self.assertFalse(SupportVisitor.objects.filter(pk=visitor_id).exists())
        self.assertFalse(SupportConversation.objects.filter(pk=support_conversation.id).exists())
        self.assertTrue(Conversation.objects.filter(pk=personal_conversation.id).exists())
        self.assertTrue(Message.objects.filter(pk=personal_message.id).exists())

    def test_widget_visitor_can_request_only_own_deletion_when_enabled(self):
        session, conversation = self.create_widget_conversation()
        visitor_external_id = conversation.visitor.external_id
        wrong_origin = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{session['id']}/privacy/delete/",
            format="json",
            HTTP_ORIGIN="https://evil.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(wrong_origin.status_code, 403)

        response = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{session['id']}/privacy/delete/",
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(response.status_code, 202)
        deletion = SupportVisitorDeletionRequest.objects.get(visitor_external_id=visitor_external_id)
        self.assertEqual(deletion.source, SupportVisitorDeletionRequest.Source.VISITOR)

    def test_retention_deletes_only_old_support_conversations(self):
        _, support_conversation = self.create_widget_conversation()
        personal_conversation, personal_message = self.create_personal_message()
        old = timezone.now() - timedelta(days=60)
        support_conversation.status = SupportConversation.Status.CLOSED
        support_conversation.closed_at = old
        support_conversation.save(update_fields=["status", "closed_at", "updated_at"])
        settings_obj = SupportPrivacySettings.objects.get(support_account=self.account)
        settings_obj.retention_enabled = True
        settings_obj.resolved_conversation_retention_days = 30
        settings_obj.save(update_fields=["retention_enabled", "resolved_conversation_retention_days", "updated_at"])

        result = run_support_retention()
        self.assertEqual(result["conversations"], 1)
        self.assertFalse(SupportConversation.objects.filter(pk=support_conversation.id).exists())
        self.assertTrue(Conversation.objects.filter(pk=personal_conversation.id).exists())
        self.assertTrue(Message.objects.filter(pk=personal_message.id).exists())

    def test_agent_cannot_manage_webhooks_exports_or_deletion(self):
        _, conversation = self.create_widget_conversation()
        self.client.force_authenticate(self.agent_user)
        self.assertEqual(self.client.get("/api/v1/support/webhooks/").status_code, 403)
        self.assertEqual(self.client.post("/api/v1/support/exports/", {}, format="json").status_code, 403)
        self.assertEqual(
            self.client.post(f"/api/v1/support/privacy/visitors/{conversation.visitor_id}/delete/", {}, format="json").status_code,
            403,
        )
