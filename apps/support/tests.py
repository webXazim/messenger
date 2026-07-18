import re
from datetime import timedelta
from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.chat.models import Conversation, ConversationParticipant, Message, MessageAttachment, PendingUpload
from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportAgentInvitation,
    SupportConversation,
    SupportMessageAuthor,
    SupportPendingUpload,
    SupportVisitor,
    SupportWebsite,
    SupportWebsiteAgent,
    SupportWidgetSession,
    SupportWidgetSettings,
)

User = get_user_model()


def invitation_token_from_email(index=-1):
    match = re.search(r"[?&]token=([^\s]+)", mail.outbox[index].body)
    if not match:
        raise AssertionError("Invitation email did not contain a token.")
    return unquote(match.group(1))


@override_settings(
    SUPPORT_CHAT_ENABLED=True,
    SUPPORT_WIDGET_ENABLED=True,
    SUPPORT_WIDGET_REQUIRE_ORIGIN=True,
    SUPPORT_WIDGET_SESSION_TTL_HOURS=720,
    SUPPORT_WIDGET_SCRIPT_URL="https://messenger.example.com/support-widget/v1/widget.js",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_BASE_URL="https://messenger.example.com",
)
class SupportFoundationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", email="owner@example.com", password="pass")
        self.agent_user = User.objects.create_user(username="agent", email="agent@example.com", password="pass")
        self.other_user = User.objects.create_user(username="other", email="other@example.com", password="pass")

    def active_account(self, **overrides):
        values = {
            "owner": self.owner,
            "status": SupportAccount.Status.ACTIVE,
            "plan_code": "support-starter",
            "website_limit": 3,
            "agent_limit": 2,
        }
        values.update(overrides)
        return SupportAccount.objects.create(**values)

    def test_user_without_support_account_receives_upgrade_state(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/support/bootstrap/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["access"], "upgrade_required")

    def test_owner_can_create_website_with_active_support_access(self):
        account = self.active_account(website_limit=1)
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/support/websites/",
            {"name": "Main site", "domain": "https://Example.com/path", "allowed_origins": ["https://example.com/"]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        website = SupportWebsite.objects.get(support_account=account)
        self.assertEqual(website.domain, "example.com")
        self.assertEqual(website.allowed_origins, ["https://example.com"])

    def test_website_plan_limit_is_enforced(self):
        account = self.active_account(website_limit=1)
        SupportWebsite.objects.create(support_account=account, name="Existing", domain="existing.example.com")
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/support/websites/",
            {"name": "Second", "domain": "second.example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_agent_only_sees_assigned_websites_even_with_view_all_permission(self):
        account = self.active_account()
        first = SupportWebsite.objects.create(support_account=account, name="First", domain="first.example.com")
        SupportWebsite.objects.create(support_account=account, name="Second", domain="second.example.com")
        agent = SupportAgent.objects.create(
            support_account=account,
            user=self.agent_user,
            invited_by=self.owner,
            can_view_all_conversations=True,
        )
        SupportWebsiteAgent.objects.create(website=first, agent=agent)
        self.client.force_authenticate(self.agent_user)
        response = self.client.get("/api/v1/support/bootstrap/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "agent")
        self.assertEqual([item["domain"] for item in response.data["websites"]], ["first.example.com"])

    def test_pending_invitation_reserves_agent_seat_and_sends_email(self):
        account = self.active_account(agent_limit=1)
        website = SupportWebsite.objects.create(support_account=account, name="Main", domain="main.example.com")
        self.client.force_authenticate(self.owner)
        first = self.client.post(
            "/api/v1/support/agents/invitations/",
            {
                "email": "Agent@Example.com",
                "website_ids": [str(website.id)],
                "max_active_conversations": 7,
                "can_view_all_conversations": True,
                "can_assign_conversations": False,
                "can_view_analytics": False,
            },
            format="json",
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.data["email"], "agent@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/support/invitations/accept?token=", mail.outbox[0].body)

        second = self.client.post(
            "/api/v1/support/agents/invitations/",
            {
                "email": "other@example.com",
                "website_ids": [str(website.id)],
                "max_active_conversations": 5,
                "can_view_all_conversations": False,
                "can_assign_conversations": False,
                "can_view_analytics": False,
            },
            format="json",
        )
        self.assertEqual(second.status_code, 409)
        bootstrap = self.client.get("/api/v1/support/bootstrap/")
        self.assertEqual(bootstrap.data["limits"]["agents"], {"used": 1, "active": 0, "pending": 1, "limit": 1})

    def test_invited_user_accepts_and_receives_exact_website_access(self):
        account = self.active_account(agent_limit=1)
        first = SupportWebsite.objects.create(support_account=account, name="First", domain="first.example.com")
        SupportWebsite.objects.create(support_account=account, name="Second", domain="second.example.com")
        self.client.force_authenticate(self.owner)
        invite = self.client.post(
            "/api/v1/support/agents/invitations/",
            {
                "email": self.agent_user.email,
                "website_ids": [str(first.id)],
                "max_active_conversations": 8,
                "can_view_all_conversations": True,
                "can_assign_conversations": True,
                "can_view_analytics": False,
            },
            format="json",
        )
        self.assertEqual(invite.status_code, 201)
        token = invitation_token_from_email()

        self.client.force_authenticate(self.agent_user)
        accepted = self.client.post("/api/v1/support/invitations/accept/", {"token": token}, format="json")
        self.assertEqual(accepted.status_code, 200)
        agent = SupportAgent.objects.get(user=self.agent_user, support_account=account)
        self.assertTrue(agent.is_active)
        self.assertEqual(agent.max_active_conversations, 8)
        self.assertTrue(agent.can_assign_conversations)
        self.assertEqual(list(agent.website_assignments.values_list("website_id", flat=True)), [first.id])
        invitation = SupportAgentInvitation.objects.get(pk=invite.data["id"])
        self.assertEqual(invitation.status, SupportAgentInvitation.Status.ACCEPTED)
        self.assertEqual(invitation.accepted_by, self.agent_user)

    def test_invitation_requires_matching_signed_in_email(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(support_account=account, name="Main", domain="main.example.com")
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/support/agents/invitations/",
            {
                "email": self.agent_user.email,
                "website_ids": [str(website.id)],
                "max_active_conversations": 5,
                "can_view_all_conversations": False,
                "can_assign_conversations": False,
                "can_view_analytics": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        token = invitation_token_from_email()
        self.client.force_authenticate(self.other_user)
        accepted = self.client.post("/api/v1/support/invitations/accept/", {"token": token}, format="json")
        self.assertEqual(accepted.status_code, 403)
        self.assertEqual(accepted.data["code"], "email_mismatch")

    def test_owner_can_update_agent_access_and_remove_agent(self):
        account = self.active_account(agent_limit=1)
        first = SupportWebsite.objects.create(support_account=account, name="First", domain="first.example.com")
        second = SupportWebsite.objects.create(support_account=account, name="Second", domain="second.example.com")
        agent = SupportAgent.objects.create(support_account=account, user=self.agent_user, invited_by=self.owner)
        SupportWebsiteAgent.objects.create(website=first, agent=agent)
        self.client.force_authenticate(self.owner)
        updated = self.client.patch(
            f"/api/v1/support/agents/{agent.id}/",
            {
                "website_ids": [str(second.id)],
                "max_active_conversations": 10,
                "can_view_all_conversations": True,
                "can_assign_conversations": True,
                "can_view_analytics": True,
            },
            format="json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["assigned_website_ids"], [str(second.id)])
        self.assertTrue(updated.data["can_view_analytics"])

        removed = self.client.delete(f"/api/v1/support/agents/{agent.id}/")
        self.assertEqual(removed.status_code, 204)
        agent.refresh_from_db()
        self.assertFalse(agent.is_active)
        self.assertEqual(agent.availability, SupportAgent.Availability.OFFLINE)
        self.assertFalse(agent.website_assignments.exists())

    def test_agent_can_update_only_their_support_availability(self):
        account = self.active_account()
        agent = SupportAgent.objects.create(support_account=account, user=self.agent_user, invited_by=self.owner)
        self.client.force_authenticate(self.agent_user)
        response = self.client.patch(
            "/api/v1/support/agents/me/availability/",
            {"availability": "available"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        agent.refresh_from_db()
        self.assertEqual(agent.availability, SupportAgent.Availability.AVAILABLE)

    def test_expired_invitation_does_not_reserve_seat_and_can_be_resent(self):
        account = self.active_account(agent_limit=1)
        website = SupportWebsite.objects.create(support_account=account, name="Main", domain="main.example.com")
        self.client.force_authenticate(self.owner)
        created = self.client.post(
            "/api/v1/support/agents/invitations/",
            {
                "email": self.agent_user.email,
                "website_ids": [str(website.id)],
                "max_active_conversations": 5,
                "can_view_all_conversations": False,
                "can_assign_conversations": False,
                "can_view_analytics": False,
            },
            format="json",
        )
        old_token = invitation_token_from_email()
        invitation = SupportAgentInvitation.objects.get(pk=created.data["id"])
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=["expires_at", "updated_at"])

        preview = self.client.get("/api/v1/support/invitations/preview/", {"token": old_token})
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.data["valid"])
        self.assertEqual(preview.data["status"], "expired")

        resent = self.client.post(f"/api/v1/support/agents/invitations/{invitation.id}/resend/")
        self.assertEqual(resent.status_code, 200)
        self.assertEqual(resent.data["status"], "pending")
        self.assertEqual(resent.data["send_count"], 2)
        new_token = invitation_token_from_email()
        self.assertNotEqual(old_token, new_token)
        invalid_old = self.client.get("/api/v1/support/invitations/preview/", {"token": old_token})
        self.assertEqual(invalid_old.status_code, 404)

    def test_public_widget_config_enforces_registered_origin_and_dynamic_cors(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        settings_object = SupportWidgetSettings.objects.get(website=website)
        settings_object.brand_name = "Main Support"
        settings_object.require_email = True
        settings_object.save(update_fields=["brand_name", "require_email", "updated_at"])

        allowed = self.client.get(
            f"/api/v1/support/widget/{website.site_key}/config/",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed["Access-Control-Allow-Origin"], "https://main.example.com")
        self.assertEqual(allowed.data["brand_name"], "Main Support")
        self.assertTrue(allowed.data["require_email"])
        self.assertNotIn("support_account", allowed.data)

        denied = self.client.get(
            f"/api/v1/support/widget/{website.site_key}/config/",
            HTTP_ORIGIN="https://attacker.example.com",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "origin_denied")
        self.assertNotIn("Access-Control-Allow-Origin", denied)

    def test_widget_session_is_origin_bound_resumable_and_not_a_messenger_user(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        settings_object = SupportWidgetSettings.objects.get(website=website)
        settings_object.require_name = True
        settings_object.require_email = True
        settings_object.save(update_fields=["require_name", "require_email", "updated_at"])
        user_count = User.objects.count()

        created = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/",
            {
                "name": "Visitor",
                "email": "visitor@example.com",
                "locale": "en-US",
                "current_page_url": "https://main.example.com/pricing",
                "referrer": "https://search.example.com/",
            },
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_USER_AGENT="Widget Browser",
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.data["token"])
        self.assertEqual(created.data["origin"], "https://main.example.com")
        self.assertEqual(created.data["visitor"]["email"], "visitor@example.com")
        self.assertEqual(User.objects.count(), user_count)
        self.assertEqual(SupportVisitor.objects.count(), 1)

        session_id = created.data["id"]
        token = created.data["token"]
        resumed = self.client.get(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session_id}/",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertNotIn("token", resumed.data)

        wrong_origin = self.client.get(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session_id}/",
            HTTP_ORIGIN="https://other.example.com",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertEqual(wrong_origin.data["code"], "origin_denied")

    def test_widget_session_refresh_rotates_token_and_site_key_revokes_session(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        created = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/",
            {"current_page_url": "https://main.example.com/"},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(created.status_code, 201)
        session_id = created.data["id"]
        old_token = created.data["token"]

        refreshed = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session_id}/refresh/",
            {},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {old_token}",
        )
        self.assertEqual(refreshed.status_code, 200)
        new_token = refreshed.data["token"]
        self.assertNotEqual(new_token, old_token)
        self.assertEqual(refreshed.data["token_version"], 2)

        rejected_old = self.client.get(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session_id}/",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {old_token}",
        )
        self.assertEqual(rejected_old.status_code, 403)
        valid_new = self.client.get(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session_id}/",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {new_token}",
        )
        self.assertEqual(valid_new.status_code, 200)

        previous_site_key = website.site_key
        self.client.force_authenticate(self.owner)
        regenerated = self.client.post(f"/api/v1/support/websites/{website.id}/site-key/regenerate/")
        self.assertEqual(regenerated.status_code, 200)
        self.assertNotEqual(regenerated.data["site_key"], str(previous_site_key))
        session = SupportWidgetSession.objects.get(pk=session_id)
        self.assertEqual(session.status, SupportWidgetSession.Status.REVOKED)

    def test_widget_owner_can_save_settings_and_install_code(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
        )
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/v1/support/websites/{website.id}/widget/",
            {
                "brand_name": "Customer Care",
                "primary_color": "#222222",
                "welcome_text": "Welcome",
                "require_name": True,
                "position": "left",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["brand_name"], "Customer Care")
        bootstrap = self.client.get("/api/v1/support/bootstrap/")
        website_payload = bootstrap.data["websites"][0]
        self.assertIn(str(website.site_key), website_payload["install_code"])
        self.assertEqual(website_payload["widget_settings"]["position"], "left")


    def _create_widget_session(self, website, *, name="Visitor", email="visitor@example.com"):
        response = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/",
            {
                "name": name,
                "email": email,
                "current_page_url": f"https://{website.domain}/support",
            },
            format="json",
            HTTP_ORIGIN=f"https://{website.domain}",
        )
        self.assertEqual(response.status_code, 201)
        return response.data

    def _visitor_message(self, website, session, text):
        return self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session['id']}/conversation/messages/",
            {"text": text},
            format="json",
            HTTP_ORIGIN=f"https://{website.domain}",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )

    def test_widget_messages_create_one_isolated_support_conversation(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        session = self._create_widget_session(website)

        first = self._visitor_message(website, session, "Hello support")
        second = self._visitor_message(website, session, "I need help")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(SupportConversation.objects.count(), 1)
        support_conversation = SupportConversation.objects.select_related("conversation").get()
        self.assertEqual(support_conversation.website, website)
        self.assertEqual(support_conversation.conversation.messages.count(), 2)
        self.assertEqual(ConversationParticipant.objects.filter(conversation=support_conversation.conversation).count(), 0)
        self.assertEqual(Message.objects.filter(conversation=support_conversation.conversation, sender__isnull=True).count(), 2)
        self.assertEqual(SupportMessageAuthor.objects.filter(message__conversation=support_conversation.conversation).count(), 2)
        self.assertEqual(User.objects.count(), 3)

    def test_owner_inbox_reply_returns_to_widget_but_not_messenger_list(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        session = self._create_widget_session(website)
        created = self._visitor_message(website, session, "Can you help?")
        conversation_id = created.data["conversation"]["id"]

        self.client.force_authenticate(self.owner)
        inbox = self.client.get("/api/v1/support/conversations/")
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.data["count"], 1)
        self.assertEqual(inbox.data["results"][0]["id"], conversation_id)

        reply = self.client.post(
            f"/api/v1/support/conversations/{conversation_id}/messages/",
            {"text": "Yes, we can help."},
            format="json",
        )
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.data["sender"]["kind"], "owner")

        messenger_list = self.client.get("/api/v1/chat/conversations/")
        self.assertEqual(messenger_list.status_code, 200)
        self.assertNotIn(str(SupportConversation.objects.get(pk=conversation_id).conversation_id), str(messenger_list.data))

        self.client.force_authenticate(user=None)
        widget_history = self.client.get(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session['id']}/conversation/messages/",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(widget_history.status_code, 200)
        self.assertEqual([item["text"] for item in widget_history.data["messages"]], ["Can you help?", "Yes, we can help."])
        self.assertEqual(widget_history.data["messages"][-1]["sender"]["kind"], "owner")

    def test_agent_cannot_access_another_website_conversation(self):
        account = self.active_account()
        first = SupportWebsite.objects.create(
            support_account=account,
            name="First",
            domain="first.example.com",
            allowed_origins=["https://first.example.com"],
        )
        second = SupportWebsite.objects.create(
            support_account=account,
            name="Second",
            domain="second.example.com",
            allowed_origins=["https://second.example.com"],
        )
        agent = SupportAgent.objects.create(
            support_account=account,
            user=self.agent_user,
            invited_by=self.owner,
            can_view_all_conversations=True,
        )
        SupportWebsiteAgent.objects.create(website=second, agent=agent)
        session = self._create_widget_session(first)
        created = self._visitor_message(first, session, "Private to first site")
        conversation_id = created.data["conversation"]["id"]

        self.client.force_authenticate(self.agent_user)
        inbox = self.client.get("/api/v1/support/conversations/")
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.data["count"], 0)
        detail = self.client.get(f"/api/v1/support/conversations/{conversation_id}/")
        self.assertEqual(detail.status_code, 404)

    def test_owner_can_assign_and_explicitly_unassign_agent(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        agent = SupportAgent.objects.create(
            support_account=account,
            user=self.agent_user,
            invited_by=self.owner,
        )
        SupportWebsiteAgent.objects.create(website=website, agent=agent)
        session = self._create_widget_session(website)
        created = self._visitor_message(website, session, "Assign me")
        conversation_id = created.data["conversation"]["id"]

        self.client.force_authenticate(self.owner)
        assigned = self.client.patch(
            f"/api/v1/support/conversations/{conversation_id}/",
            {"assigned_agent_id": str(agent.id)},
            format="json",
        )
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.data["assigned_agent"]["id"], str(agent.id))

        unassigned = self.client.patch(
            f"/api/v1/support/conversations/{conversation_id}/",
            {"assigned_agent_id": None},
            format="json",
        )
        self.assertEqual(unassigned.status_code, 200)
        self.assertIsNone(unassigned.data["assigned_agent"])

    def test_agent_claim_respects_active_conversation_capacity(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        agent = SupportAgent.objects.create(
            support_account=account,
            user=self.agent_user,
            invited_by=self.owner,
            max_active_conversations=1,
        )
        SupportWebsiteAgent.objects.create(website=website, agent=agent)
        first_session = self._create_widget_session(website, name="First", email="first@example.com")
        first_message = self._visitor_message(website, first_session, "First conversation")
        second_session = self._create_widget_session(website, name="Second", email="second@example.com")
        second_message = self._visitor_message(website, second_session, "Second conversation")

        self.client.force_authenticate(self.agent_user)
        first_claim = self.client.post(
            f"/api/v1/support/conversations/{first_message.data['conversation']['id']}/claim/",
            {},
            format="json",
        )
        self.assertEqual(first_claim.status_code, 200)
        second_claim = self.client.post(
            f"/api/v1/support/conversations/{second_message.data['conversation']['id']}/claim/",
            {},
            format="json",
        )
        self.assertEqual(second_claim.status_code, 409)
        self.assertEqual(second_claim.data["code"], "agent_capacity_reached")

    def _visitor_upload(self, website, session, *, name="document.txt", content=b"support file", mime="text/plain", **metadata):
        payload = {
            "file": SimpleUploadedFile(name, content, content_type=mime),
            "original_name": name,
            "mime_type": mime,
            **metadata,
        }
        return self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session['id']}/conversation/uploads/",
            payload,
            format="multipart",
            HTTP_ORIGIN=f"https://{website.domain}",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )

    def test_visitor_attachment_uses_private_support_scope_and_download_authorization(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        session = self._create_widget_session(website)
        upload_response = self._visitor_upload(website, session)
        self.assertEqual(upload_response.status_code, 201)
        upload_id = upload_response.data["id"]
        pending = PendingUpload.objects.get(pk=upload_id)
        self.assertEqual(pending.purpose, PendingUpload.Purpose.SUPPORT)
        self.assertIsNone(pending.user_id)
        self.assertIn(f"support/{account.id}/{website.id}/pending/", pending.file.name)
        support_upload = SupportPendingUpload.objects.get(pending_upload=pending)
        self.assertEqual(str(support_upload.widget_session_id), session["id"])

        sent = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session['id']}/conversation/messages/",
            {"text": "", "attachment_ids": [upload_id]},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(sent.data["message"]["type"], "file")
        self.assertEqual(len(sent.data["message"]["attachments"]), 1)
        attachment = MessageAttachment.objects.get(message_id=sent.data["message"]["id"])
        self.assertIn(f"support/{account.id}/{website.id}/", attachment.file.name)

        download = self.client.get(
            sent.data["message"]["attachments"][0]["download_url"],
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(download.status_code, 200)

        wrong_origin = self.client.get(
            sent.data["message"]["attachments"][0]["download_url"],
            HTTP_ORIGIN="https://evil.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(wrong_origin.status_code, 403)

    def test_owner_can_send_support_attachment_and_other_website_agent_cannot_open_it(self):
        account = self.active_account()
        first = SupportWebsite.objects.create(
            support_account=account,
            name="First",
            domain="first.example.com",
            allowed_origins=["https://first.example.com"],
        )
        second = SupportWebsite.objects.create(
            support_account=account,
            name="Second",
            domain="second.example.com",
            allowed_origins=["https://second.example.com"],
        )
        agent = SupportAgent.objects.create(support_account=account, user=self.agent_user, invited_by=self.owner)
        SupportWebsiteAgent.objects.create(website=second, agent=agent)
        session = self._create_widget_session(first)
        created = self._visitor_message(first, session, "Need a document")
        conversation_id = created.data["conversation"]["id"]

        self.client.force_authenticate(self.owner)
        upload = self.client.post(
            f"/api/v1/support/conversations/{conversation_id}/uploads/",
            {
                "file": SimpleUploadedFile("guide.pdf", b"%PDF-1.4\nminimal", content_type="application/pdf"),
                "original_name": "guide.pdf",
                "mime_type": "application/pdf",
            },
            format="multipart",
        )
        self.assertEqual(upload.status_code, 201)
        sent = self.client.post(
            f"/api/v1/support/conversations/{conversation_id}/messages/",
            {"text": "Here is the guide.", "attachment_ids": [upload.data["id"]]},
            format="json",
        )
        self.assertEqual(sent.status_code, 201)
        attachment_payload = sent.data["attachments"][0]
        self.assertTrue(attachment_payload["download_url"].startswith("http://testserver/"))
        allowed = self.client.get(attachment_payload["download_url"])
        self.assertEqual(allowed.status_code, 200)

        self.client.force_authenticate(self.agent_user)
        denied = self.client.get(attachment_payload["download_url"])
        self.assertEqual(denied.status_code, 404)

    def test_widget_voice_message_requires_one_audio_upload(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        session = self._create_widget_session(website)
        upload = self._visitor_upload(
            website,
            session,
            name="voice.webm",
            content=b"webm voice payload",
            mime="audio/webm",
            duration_seconds="2.40",
        )
        self.assertEqual(upload.status_code, 201)
        sent = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session['id']}/conversation/messages/",
            {"attachment_ids": [upload.data["id"]], "voice_note": True},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(sent.data["message"]["type"], "audio")
        self.assertTrue(sent.data["message"]["voice_note"])
        self.assertEqual(sent.data["message"]["preview_text"], "Voice message")

    def test_support_upload_cannot_be_attached_to_personal_messenger(self):
        account = self.active_account()
        website = SupportWebsite.objects.create(
            support_account=account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        session = self._create_widget_session(website)
        created = self._visitor_message(website, session, "Start support")
        conversation_id = created.data["conversation"]["id"]
        self.client.force_authenticate(self.owner)
        upload = self.client.post(
            f"/api/v1/support/conversations/{conversation_id}/uploads/",
            {"file": SimpleUploadedFile("private.txt", b"support only", content_type="text/plain")},
            format="multipart",
        )
        self.assertEqual(upload.status_code, 201)

        personal = Conversation.objects.create(type=Conversation.ConversationType.DIRECT, created_by=self.owner)
        ConversationParticipant.objects.create(conversation=personal, user=self.owner)
        messenger_send = self.client.post(
            f"/api/v1/chat/conversations/{personal.id}/messages/",
            {"text": "", "attachment_ids": [upload.data["id"]]},
            format="json",
        )
        self.assertEqual(messenger_send.status_code, 400)
        self.assertFalse(Message.objects.filter(conversation=personal).exists())

