from unittest.mock import patch

from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.support.models import SupportAccount, SupportAgentInvitation, SupportWebsite


@override_settings(
    SUPPORT_CHAT_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="support@example.com",
    FRONTEND_BASE_URL="https://crescentsphere.com",
    SUPPORT_INVITATION_EMAIL_ASYNC=False,
)
class SupportInvitationDeliveryTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="invite-owner", email="invite-owner@example.com", password="x"
        )
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            status=SupportAccount.Status.ACTIVE,
            plan_code="support-growth",
            website_limit=5,
            agent_limit=15,
        )
        self.website = SupportWebsite.objects.create(
            support_account=self.account, name="Main", domain="example.com"
        )
        self.client.force_authenticate(self.owner)

    def invitation_payload(self, email="new-agent@example.com"):
        return {
            "email": email,
            "website_ids": [str(self.website.id)],
            "team_ids": [],
            "max_active_conversations": 5,
        }

    def test_direct_delivery_sends_email_and_records_status(self):
        response = self.client.post(
            "/api/v1/support/agents/invitations/",
            self.invitation_payload(),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        invitation = SupportAgentInvitation.objects.get(
            support_account=self.account, email="new-agent@example.com"
        )
        self.assertEqual(invitation.email_delivery_status, SupportAgentInvitation.DeliveryStatus.SENT)
        self.assertIsNotNone(invitation.email_delivered_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/support/invitations/accept?token=", mail.outbox[0].body)

    @override_settings(SUPPORT_INVITATION_EMAIL_ASYNC=True)
    @patch("apps.support.tasks.send_support_agent_invitation_email.delay", side_effect=RuntimeError("broker unavailable"))
    def test_broker_failure_falls_back_to_direct_delivery(self, _delay):
        response = self.client.post(
            "/api/v1/support/agents/invitations/",
            self.invitation_payload("fallback-agent@example.com"),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        invitation = SupportAgentInvitation.objects.get(email="fallback-agent@example.com")
        self.assertEqual(invitation.email_delivery_status, SupportAgentInvitation.DeliveryStatus.SENT)
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_targets_only_the_selected_invitation(self):
        first = self.client.post(
            "/api/v1/support/agents/invitations/",
            self.invitation_payload("first-agent@example.com"),
            format="json",
        )
        second = self.client.post(
            "/api/v1/support/agents/invitations/",
            self.invitation_payload("second-agent@example.com"),
            format="json",
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        first_id = first.data["id"]
        second_id = second.data["id"]
        mail.outbox.clear()

        response = self.client.post(
            f"/api/v1/support/agents/invitations/{first_id}/resend/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        first_invitation = SupportAgentInvitation.objects.get(pk=first_id)
        second_invitation = SupportAgentInvitation.objects.get(pk=second_id)
        self.assertEqual(first_invitation.send_count, 2)
        self.assertEqual(second_invitation.send_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["first-agent@example.com"])

    def test_revoke_targets_only_the_selected_invitation(self):
        first = self.client.post(
            "/api/v1/support/agents/invitations/",
            self.invitation_payload("revoke-first@example.com"),
            format="json",
        )
        second = self.client.post(
            "/api/v1/support/agents/invitations/",
            self.invitation_payload("keep-second@example.com"),
            format="json",
        )
        response = self.client.delete(
            f"/api/v1/support/agents/invitations/{first.data['id']}/"
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            SupportAgentInvitation.objects.get(pk=first.data["id"]).status,
            SupportAgentInvitation.Status.REVOKED,
        )
        self.assertEqual(
            SupportAgentInvitation.objects.get(pk=second.data["id"]).status,
            SupportAgentInvitation.Status.PENDING,
        )
