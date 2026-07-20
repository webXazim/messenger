from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.support.models import SupportAccount, SupportAgentInvitation, SupportWebsite


@override_settings(SUPPORT_CHAT_ENABLED=True)
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

    @patch("apps.support.tasks.send_support_agent_invitation_email.delay", side_effect=RuntimeError("broker unavailable"))
    def test_invitation_creation_survives_broker_failure(self, _delay):
        response = self.client.post(
            "/api/v1/support/agents/invitations/",
            {
                "email": "new-agent@example.com",
                "website_ids": [str(self.website.id)],
                "team_ids": [],
                "max_active_conversations": 5,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            SupportAgentInvitation.objects.filter(
                support_account=self.account, email="new-agent@example.com"
            ).exists()
        )
