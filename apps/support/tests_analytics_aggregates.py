from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import Conversation, Message
from apps.support.analytics_aggregates import (
    aggregate_support_day,
    hours_payload,
    overview_payload,
    volume_payload,
)
from apps.support.models import (
    SupportAccount,
    SupportAnalyticsDailyMetric,
    SupportConversation,
    SupportVisitor,
    SupportWebsite,
)


class SupportAnalyticsAggregateTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="analytics-owner",
            email="analytics@example.com",
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
            name="Analytics website",
            domain="analytics.example.com",
        )
        self.visitor = SupportVisitor.objects.create(website=self.website)

    def create_conversation(self, created_at):
        chat = Conversation.objects.create(type="direct")
        conversation = SupportConversation.objects.create(
            conversation=chat,
            website=self.website,
            visitor=self.visitor,
        )
        SupportConversation.objects.filter(pk=conversation.pk).update(
            created_at=created_at,
            first_response_at=created_at + timedelta(minutes=3),
            resolved_at=created_at + timedelta(minutes=20),
            status=SupportConversation.Status.RESOLVED,
        )
        conversation.refresh_from_db()
        Message.objects.create(
            conversation=chat,
            sender=self.owner,
            text="Reply",
            created_at=created_at + timedelta(minutes=3),
        )
        return conversation

    def test_daily_aggregation_is_idempotent(self):
        day = timezone.localdate()
        self.create_conversation(timezone.now() - timedelta(minutes=30))
        aggregate_support_day(self.account, day)
        aggregate_support_day(self.account, day)
        rows = SupportAnalyticsDailyMetric.objects.filter(
            support_account=self.account,
            metric_date=day,
            website__isnull=True,
            team__isnull=True,
            agent__isnull=True,
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().conversations_created, 1)

    def test_reporting_reads_aggregates(self):
        day = timezone.localdate()
        self.create_conversation(timezone.now() - timedelta(minutes=30))
        aggregate_support_day(self.account, day)
        overview = overview_payload(self.account, day, day)
        volume = volume_payload(self.account, day, day)
        hours = hours_payload(self.account, day, day)
        self.assertEqual(overview["conversations"], 1)
        self.assertEqual(overview["resolved"], 1)
        self.assertEqual(len(volume), 1)
        self.assertEqual(sum(item["conversations"] for item in hours), 1)

    def test_account_isolation(self):
        other_owner = User.objects.create_user(
            username="other-analytics",
            email="other-analytics@example.com",
            password="x",
        )
        other_account = SupportAccount.objects.create(
            owner=other_owner,
            plan_code="support-growth",
            website_limit=5,
            agent_limit=5,
        )
        day = timezone.localdate()
        SupportAnalyticsDailyMetric.objects.create(
            support_account=other_account,
            metric_date=day,
            conversations_created=99,
        )
        overview = overview_payload(self.account, day, day)
        self.assertEqual(overview["conversations"], 0)
