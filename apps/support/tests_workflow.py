from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.chat.models import Message
from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportAuditEvent,
    SupportConversation,
    SupportConversationTag,
    SupportInternalNote,
    SupportSavedInboxView,
    SupportTag,
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
class SupportWorkflowTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner-workflow", email="owner-workflow@example.com", password="pass")
        self.agent_user = User.objects.create_user(username="agent-workflow", email="agent-workflow@example.com", password="pass")
        self.other_owner = User.objects.create_user(username="other-owner", email="other-owner@example.com", password="pass")
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
        )
        SupportWebsiteAgent.objects.create(website=self.website, agent=self.agent)

    def create_conversation(self):
        session_response = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/",
            {"name": "Visitor", "email": "visitor@example.com", "current_page_url": "https://main.example.com/help"},
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
        return session, SupportConversation.objects.get(pk=message_response.data["conversation"]["id"])

    def test_owner_manages_catalog_and_agent_can_use_it(self):
        self.client.force_authenticate(self.owner)
        tag_response = self.client.post("/api/v1/support/tags/", {"name": "Billing", "color": "#123456"}, format="json")
        self.assertEqual(tag_response.status_code, 201)
        reply_response = self.client.post(
            "/api/v1/support/canned-replies/",
            {"website_id": str(self.website.id), "shortcut": "/hello", "title": "Welcome", "body": "Hello, how can we help?"},
            format="json",
        )
        self.assertEqual(reply_response.status_code, 201)

        self.client.force_authenticate(self.agent_user)
        self.assertEqual(self.client.get("/api/v1/support/tags/").status_code, 200)
        replies = self.client.get(f"/api/v1/support/canned-replies/?website={self.website.id}")
        self.assertEqual(replies.status_code, 200)
        self.assertEqual(replies.data[0]["shortcut"], "/hello")
        denied = self.client.post("/api/v1/support/tags/", {"name": "Agent-created", "color": "#111111"}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_notes_tags_and_audit_are_private_from_widget(self):
        session, conversation = self.create_conversation()
        self.client.force_authenticate(self.owner)
        tag = SupportTag.objects.create(support_account=self.account, name="VIP", color="#654321", created_by=self.owner)
        tags_response = self.client.put(
            f"/api/v1/support/conversations/{conversation.id}/tags/",
            {"tag_ids": [str(tag.id)]},
            format="json",
        )
        self.assertEqual(tags_response.status_code, 200)
        note_response = self.client.post(
            f"/api/v1/support/conversations/{conversation.id}/notes/",
            {"body": "Call the customer after checking billing."},
            format="json",
        )
        self.assertEqual(note_response.status_code, 201)
        update_response = self.client.patch(
            f"/api/v1/support/conversations/{conversation.id}/",
            {"priority": "urgent", "status": "waiting_team"},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(SupportInternalNote.objects.count(), 1)
        self.assertEqual(SupportConversationTag.objects.count(), 1)
        self.assertEqual(Message.objects.filter(text__icontains="Call the customer").count(), 0)
        self.assertTrue(SupportAuditEvent.objects.filter(support_conversation=conversation, action="conversation.note_added").exists())
        self.assertTrue(SupportAuditEvent.objects.filter(support_conversation=conversation, action="conversation.tags_changed").exists())
        self.assertTrue(SupportAuditEvent.objects.filter(support_conversation=conversation, action="conversation.workflow_changed").exists())

        activity = self.client.get(f"/api/v1/support/conversations/{conversation.id}/activity/")
        self.assertEqual(activity.status_code, 200)
        self.assertEqual(activity.data["notes"][0]["body"], "Call the customer after checking billing.")

        self.client.force_authenticate(user=None)
        widget_history = self.client.get(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{session['id']}/conversation/messages/",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(widget_history.status_code, 200)
        self.assertNotIn("notes", widget_history.data)
        self.assertNotIn("events", widget_history.data)
        self.assertNotIn("Call the customer", str(widget_history.data))
        self.assertNotIn("VIP", str(widget_history.data))
        self.assertEqual(widget_history.data["conversation"]["tags"], [])

    def test_removed_catalog_items_can_be_recreated_and_active_names_are_case_insensitive(self):
        self.client.force_authenticate(self.owner)
        first_tag = self.client.post("/api/v1/support/tags/", {"name": "Billing", "color": "#123456"}, format="json")
        self.assertEqual(first_tag.status_code, 201)
        duplicate_tag = self.client.post("/api/v1/support/tags/", {"name": "billing", "color": "#654321"}, format="json")
        self.assertEqual(duplicate_tag.status_code, 409)
        self.assertEqual(self.client.delete(f"/api/v1/support/tags/{first_tag.data['id']}/").status_code, 204)
        recreated_tag = self.client.post("/api/v1/support/tags/", {"name": "billing", "color": "#654321"}, format="json")
        self.assertEqual(recreated_tag.status_code, 201)

        first_reply = self.client.post(
            "/api/v1/support/canned-replies/",
            {"shortcut": "/hello", "title": "Hello", "body": "Hello there"},
            format="json",
        )
        self.assertEqual(first_reply.status_code, 201)
        duplicate_reply = self.client.post(
            "/api/v1/support/canned-replies/",
            {"shortcut": "HELLO", "title": "Duplicate", "body": "Duplicate"},
            format="json",
        )
        self.assertEqual(duplicate_reply.status_code, 409)
        self.assertEqual(self.client.delete(f"/api/v1/support/canned-replies/{first_reply.data['id']}/").status_code, 204)
        recreated_reply = self.client.post(
            "/api/v1/support/canned-replies/",
            {"shortcut": "hello", "title": "New hello", "body": "Welcome back"},
            format="json",
        )
        self.assertEqual(recreated_reply.status_code, 201)

    def test_cross_account_tag_cannot_be_applied(self):
        _, conversation = self.create_conversation()
        other_account = SupportAccount.objects.create(owner=self.other_owner, status=SupportAccount.Status.ACTIVE)
        other_tag = SupportTag.objects.create(support_account=other_account, name="Other", color="#111111", created_by=self.other_owner)
        self.client.force_authenticate(self.owner)
        response = self.client.put(
            f"/api/v1/support/conversations/{conversation.id}/tags/",
            {"tag_ids": [str(other_tag.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SupportConversationTag.objects.count(), 0)

    def test_saved_views_are_private_and_validate_visible_websites(self):
        self.client.force_authenticate(self.agent_user)
        created = self.client.post(
            "/api/v1/support/saved-views/",
            {"name": "Urgent billing", "website_id": str(self.website.id), "queue": "mine", "priority": "urgent", "search": "invoice"},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(SupportSavedInboxView.objects.get().user, self.agent_user)

        self.client.force_authenticate(self.owner)
        owner_views = self.client.get("/api/v1/support/saved-views/")
        self.assertEqual(owner_views.status_code, 200)
        self.assertEqual(owner_views.data, [])

    def test_conversation_list_filters_by_tag_and_priority(self):
        _, conversation = self.create_conversation()
        tag = SupportTag.objects.create(support_account=self.account, name="Sales", color="#222222", created_by=self.owner)
        SupportConversationTag.objects.create(support_conversation=conversation, tag=tag, added_by=self.owner)
        conversation.priority = SupportConversation.Priority.HIGH
        conversation.save(update_fields=["priority", "updated_at"])
        self.client.force_authenticate(self.owner)
        matching = self.client.get(f"/api/v1/support/conversations/?tag={tag.id}&priority=high")
        self.assertEqual(matching.status_code, 200)
        self.assertEqual(matching.data["count"], 1)
        self.assertEqual(matching.data["results"][0]["tags"][0]["name"], "Sales")
        missing = self.client.get(f"/api/v1/support/conversations/?tag={tag.id}&priority=low")
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.data["count"], 0)
