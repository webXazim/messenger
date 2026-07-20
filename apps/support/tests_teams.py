from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.chat.models import Conversation
from apps.support.models import (
    SupportAccount, SupportAgent, SupportConversation, SupportTeam,
    SupportTeamMembership, SupportVisitor, SupportWebsite, SupportWebsiteTeam,
)
from apps.support.services import deactivate_agent, update_agent

User = get_user_model()


class SupportTeamSafetyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="team-owner", email="owner@teams.test")
        self.agent_user = User.objects.create_user(username="team-agent", email="agent@teams.test")
        self.other_owner = User.objects.create_user(username="other-owner", email="other@teams.test")
        self.account = SupportAccount.objects.create(owner=self.owner, status=SupportAccount.Status.ACTIVE, website_limit=3, agent_limit=3)
        self.other_account = SupportAccount.objects.create(owner=self.other_owner, status=SupportAccount.Status.ACTIVE, website_limit=3, agent_limit=3)
        self.website = SupportWebsite.objects.create(support_account=self.account, name="Main", domain="main.teams.test")
        self.other_website = SupportWebsite.objects.create(support_account=self.other_account, name="Other", domain="other.teams.test")
        self.agent = SupportAgent.objects.create(support_account=self.account, user=self.agent_user, invited_by=self.owner)
        self.team = SupportTeam.objects.create(support_account=self.account, name="Support", created_by=self.owner)
        self.other_team = SupportTeam.objects.create(support_account=self.other_account, name="Other", created_by=self.other_owner)

    def test_cross_account_team_membership_is_rejected(self):
        membership = SupportTeamMembership(team=self.other_team, agent=self.agent)
        with self.assertRaises(ValidationError):
            membership.full_clean()

    def test_cross_account_website_team_is_rejected(self):
        assignment = SupportWebsiteTeam(team=self.other_team, website=self.website)
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_update_agent_syncs_team_and_permissions(self):
        updated = update_agent(
            account=self.account, agent=self.agent, website_ids=[self.website.id], team_ids=[self.team.id],
            max_active_conversations=7, can_view_all_conversations=True, can_assign_conversations=True,
            can_view_analytics=True, can_manage_websites=True, can_manage_knowledge=True,
            can_manage_teams=True, can_manage_automations=False, can_export_data=True,
        )
        updated.refresh_from_db()
        self.assertEqual(updated.max_active_conversations, 7)
        self.assertTrue(updated.can_manage_teams)
        self.assertTrue(updated.team_memberships.filter(team=self.team).exists())
        self.assertTrue(updated.website_assignments.filter(website=self.website).exists())

    def test_deactivation_unassigns_active_conversations_but_preserves_history(self):
        visitor = SupportVisitor.objects.create(website=self.website, name="Visitor")
        shared = Conversation.objects.create(type=Conversation.ConversationType.DIRECT)
        support_conversation = SupportConversation.objects.create(conversation=shared, website=self.website, visitor=visitor, assigned_agent=self.agent, status=SupportConversation.Status.OPEN)
        deactivate_agent(account=self.account, agent=self.agent)
        self.agent.refresh_from_db(); support_conversation.refresh_from_db()
        self.assertFalse(self.agent.is_active)
        self.assertIsNone(support_conversation.assigned_agent_id)
        self.assertTrue(SupportAgent.objects.filter(pk=self.agent.pk).exists())
