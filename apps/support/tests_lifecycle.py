from datetime import timedelta
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import Conversation
from apps.support.lifecycle_services import (
    SupportLifecycleError,
    snooze_conversation,
    transition_conversation,
    wake_due_snoozed_conversations,
)
from apps.support.models import (
    SupportAccount, SupportAgent, SupportConversation, SupportVisitor, SupportWebsite
)
from apps.support.services import SupportContext


class SupportLifecycleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner-lifecycle", email="owner@example.com", password="x")
        self.account = SupportAccount.objects.create(owner=self.owner, plan_code="support-growth", website_limit=5, agent_limit=5)
        self.website = SupportWebsite.objects.create(support_account=self.account, name="Site", domain="example.com")
        self.visitor = SupportVisitor.objects.create(website=self.website)
        self.chat = Conversation.objects.create(type="direct")
        self.conversation = SupportConversation.objects.create(conversation=self.chat, website=self.website, visitor=self.visitor)
        self.context = SupportContext(account=self.account, role="owner", agent=None)

    def test_resolution_requires_reason(self):
        with self.assertRaises(SupportLifecycleError):
            transition_conversation(context=self.context, conversation=self.conversation, target_status=SupportConversation.Status.RESOLVED)

    def test_snooze_and_wake(self):
        until = timezone.now() + timedelta(minutes=10)
        row = snooze_conversation(context=self.context, conversation=self.conversation, until=until)
        self.assertEqual(row.status, SupportConversation.Status.SNOOZED)
        SupportConversation.objects.filter(pk=row.pk).update(snoozed_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(wake_due_snoozed_conversations(), 1)
        row.refresh_from_db()
        self.assertEqual(row.status, SupportConversation.Status.NEW)

    def test_closed_is_owner_protected_but_force_reopen_supported(self):
        resolved = transition_conversation(
            context=self.context, conversation=self.conversation,
            target_status=SupportConversation.Status.RESOLVED, resolution_reason="answered"
        )
        closed = transition_conversation(
            context=self.context, conversation=resolved,
            target_status=SupportConversation.Status.CLOSED, closure_reason="retention"
        )
        reopened = transition_conversation(
            context=self.context, conversation=closed,
            target_status=SupportConversation.Status.OPEN, force=True
        )
        self.assertEqual(reopened.status, SupportConversation.Status.OPEN)
        self.assertEqual(reopened.reopen_count, 1)
