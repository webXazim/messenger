from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.chat.models import Conversation
from apps.support.automation_services import run_automations
from apps.support.models import (
    SupportAccount,
    SupportAutomationExecution,
    SupportAutomationRule,
    SupportConversation,
    SupportSecuritySettings,
    SupportVisitor,
    SupportWebsite,
)


class SupportAutomationSafetyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="automation-owner",
            email="automation@example.com",
            password="x",
        )
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            plan_code="support-growth",
            website_limit=5,
            agent_limit=5,
        )
        self.website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Automation website",
            domain="automation.example.com",
        )
        self.visitor = SupportVisitor.objects.create(website=self.website)
        self.chat = Conversation.objects.create(type="direct")
        self.conversation = SupportConversation.objects.create(
            conversation=self.chat,
            website=self.website,
            visitor=self.visitor,
        )

    def test_unsupported_action_is_rejected(self):
        rule = SupportAutomationRule(
            support_account=self.account,
            name="Unsafe",
            trigger=SupportAutomationRule.Trigger.CONVERSATION_CREATED,
            actions=[{"type": "run_python", "value": "dangerous"}],
        )
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def test_execution_is_idempotent(self):
        rule = SupportAutomationRule.objects.create(
            support_account=self.account,
            name="Priority",
            trigger=SupportAutomationRule.Trigger.CONVERSATION_CREATED,
            actions=[{"type": "set_priority", "value": "high"}],
        )
        run_automations(
            account=self.account,
            trigger=rule.trigger,
            conversation=self.conversation,
            event_key="event-1",
        )
        run_automations(
            account=self.account,
            trigger=rule.trigger,
            conversation=self.conversation,
            event_key="event-1",
        )
        self.assertEqual(SupportAutomationExecution.objects.count(), 1)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.priority, "high")

    def test_action_limit_is_bounded(self):
        rule = SupportAutomationRule(
            support_account=self.account,
            name="Too many",
            trigger=SupportAutomationRule.Trigger.CONVERSATION_CREATED,
            execution_limit=26,
            actions=[{"type": "notify_owner"}],
        )
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def test_security_settings_are_bounded(self):
        row = SupportSecuritySettings(
            support_account=self.account,
            max_attachment_mb=500,
        )
        with self.assertRaises(ValidationError):
            row.full_clean()
