from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import Conversation
from apps.support.models import (
    SupportAccount,
    SupportConversation,
    SupportServiceSettings,
    SupportSlaPolicy,
    SupportTeam,
    SupportVisitor,
    SupportWebsite,
)
from apps.support.service_operations import (
    effective_sla_policy,
    initialize_service_targets,
    pause_sla,
    resume_sla,
    service_minutes_between,
)


class SupportProductionSlaTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="sla-owner", email="sla-owner@example.com", password="x"
        )
        self.account = SupportAccount.objects.create(
            owner=self.owner, plan_code="support-growth", website_limit=5, agent_limit=5
        )
        self.website = SupportWebsite.objects.create(
            support_account=self.account, name="Primary", domain="example.com"
        )
        self.team = SupportTeam.objects.create(
            support_account=self.account, name="Priority team"
        )
        self.visitor = SupportVisitor.objects.create(website=self.website)
        self.chat = Conversation.objects.create(type="direct")
        self.conversation = SupportConversation.objects.create(
            conversation=self.chat,
            website=self.website,
            visitor=self.visitor,
            assigned_team=self.team,
            created_at=timezone.now(),
        )
        self.settings = SupportServiceSettings.objects.create(
            support_account=self.account,
            timezone="UTC",
            business_hours_enabled=False,
            first_response_targets={"low": 60, "normal": 30, "high": 15, "urgent": 5},
            next_response_targets={"low": 120, "normal": 60, "high": 30, "urgent": 10},
            resolution_targets={"low": 480, "normal": 240, "high": 120, "urgent": 60},
        )

    def test_website_policy_takes_priority_over_team_policy(self):
        SupportSlaPolicy.objects.create(
            support_account=self.account,
            name="Team",
            team=self.team,
            first_response_targets={"normal": 20},
        )
        SupportSlaPolicy.objects.create(
            support_account=self.account,
            name="Website",
            website=self.website,
            first_response_targets={"normal": 10},
        )
        policy = effective_sla_policy(self.conversation)
        self.assertEqual(policy.first_response_targets["normal"], 10)
        self.assertEqual(policy.source, "website")

    def test_pause_and_resume_shift_deadlines(self):
        anchor = timezone.now()
        self.conversation.created_at = anchor
        self.conversation.save(update_fields=["created_at"])
        initialize_service_targets(self.conversation, anchor=anchor)
        self.conversation.refresh_from_db()
        original_due = self.conversation.first_response_due_at
        paused_at = anchor + timedelta(minutes=5)
        pause_sla(self.conversation, reason="waiting_customer", paused_at=paused_at)
        resume_sla(self.conversation, resumed_at=paused_at + timedelta(minutes=20))
        self.conversation.refresh_from_db()
        self.assertEqual(
            int((self.conversation.first_response_due_at - original_due).total_seconds()),
            20 * 60,
        )
        self.assertEqual(self.conversation.sla_total_paused_seconds, 20 * 60)
        self.assertIsNone(self.conversation.sla_paused_at)

    def test_business_minutes_exclude_closed_time(self):
        self.settings.business_hours_enabled = True
        self.settings.business_hours = {
            "monday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "tuesday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "wednesday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "thursday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "friday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "saturday": {"enabled": False, "start": "09:00", "end": "17:00"},
            "sunday": {"enabled": False, "start": "09:00", "end": "17:00"},
        }
        self.settings.save()
        start = datetime(2026, 7, 20, 16, 30, tzinfo=dt_timezone.utc)  # Monday
        end = datetime(2026, 7, 21, 9, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(service_minutes_between(start, end, self.settings), 60)
