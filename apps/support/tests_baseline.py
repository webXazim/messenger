from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.chat.models import Conversation, ConversationParticipant
from apps.chat.selectors import user_conversations_qs
from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportConversation,
    SupportVisitor,
    SupportWebsite,
    SupportWebsiteAgent,
)

User = get_user_model()


class SupportIsolationBaselineTests(TestCase):
    """Non-destructive regression contract for the current Support/Messenger boundary."""

    def setUp(self):
        self.owner = User.objects.create_user(username="baseline-owner", email="owner@example.com")
        self.agent_user = User.objects.create_user(username="baseline-agent", email="agent@example.com")
        self.other_owner = User.objects.create_user(username="baseline-other-owner", email="other@example.com")
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            status=SupportAccount.Status.ACTIVE,
            website_limit=3,
            agent_limit=3,
        )
        self.other_account = SupportAccount.objects.create(
            owner=self.other_owner,
            status=SupportAccount.Status.ACTIVE,
            website_limit=3,
            agent_limit=3,
        )
        self.website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Main",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        self.other_website = SupportWebsite.objects.create(
            support_account=self.other_account,
            name="Other",
            domain="other.example.com",
            allowed_origins=["https://other.example.com"],
        )
        self.agent = SupportAgent.objects.create(
            support_account=self.account,
            user=self.agent_user,
            invited_by=self.owner,
        )

    def test_support_visitor_is_not_a_messenger_user(self):
        visitor = SupportVisitor.objects.create(website=self.website, name="Visitor")
        self.assertIsNone(getattr(visitor, "user_id", None))

    def test_support_conversation_uses_no_messenger_participant(self):
        visitor = SupportVisitor.objects.create(website=self.website, name="Visitor")
        shared = Conversation.objects.create(type=Conversation.ConversationType.DIRECT)
        SupportConversation.objects.create(conversation=shared, website=self.website, visitor=visitor)
        self.assertFalse(ConversationParticipant.objects.filter(conversation=shared).exists())
        self.assertFalse(user_conversations_qs(self.owner).filter(pk=shared.pk).exists())
        self.assertFalse(user_conversations_qs(self.agent_user).filter(pk=shared.pk).exists())

    def test_cross_account_website_assignment_is_rejected(self):
        assignment = SupportWebsiteAgent(website=self.other_website, agent=self.agent)
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_cross_account_conversation_agent_is_rejected(self):
        other_agent_user = User.objects.create_user(username="baseline-other-agent")
        other_agent = SupportAgent.objects.create(
            support_account=self.other_account,
            user=other_agent_user,
            invited_by=self.other_owner,
        )
        visitor = SupportVisitor.objects.create(website=self.website, name="Visitor")
        shared = Conversation.objects.create(type=Conversation.ConversationType.DIRECT)
        support_conversation = SupportConversation(
            conversation=shared,
            website=self.website,
            visitor=visitor,
            assigned_agent=other_agent,
        )
        with self.assertRaises(ValidationError):
            support_conversation.full_clean()

    def test_visitor_and_conversation_website_must_match(self):
        visitor = SupportVisitor.objects.create(website=self.other_website, name="Visitor")
        shared = Conversation.objects.create(type=Conversation.ConversationType.DIRECT)
        support_conversation = SupportConversation(
            conversation=shared,
            website=self.website,
            visitor=visitor,
        )
        with self.assertRaises(ValidationError):
            support_conversation.full_clean()
