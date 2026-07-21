from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportKnowledgeArticle,
    SupportKnowledgeArticleWebsite,
    SupportKnowledgeFeedback,
    SupportKnowledgeSettings,
    SupportWebsite,
    SupportWebsiteAgent,
)

User = get_user_model()


@override_settings(
    SUPPORT_CHAT_ENABLED=True,
    SUPPORT_WIDGET_ENABLED=True,
    SUPPORT_WIDGET_REQUIRE_ORIGIN=True,
)
class SupportKnowledgeTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="knowledge-owner",
            email="knowledge-owner@example.com",
            password="pass",
        )
        self.agent_user = User.objects.create_user(
            username="knowledge-agent",
            email="knowledge-agent@example.com",
            password="pass",
        )
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            status=SupportAccount.Status.ACTIVE,
            plan_code="support-business",
            website_limit=3,
            agent_limit=3,
        )
        self.main = SupportWebsite.objects.create(
            support_account=self.account,
            name="Main website",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        self.products = SupportWebsite.objects.create(
            support_account=self.account,
            name="Products website",
            domain="products.example.com",
            allowed_origins=["https://products.example.com"],
        )
        self.agent = SupportAgent.objects.create(
            support_account=self.account,
            user=self.agent_user,
            invited_by=self.owner,
        )
        SupportWebsiteAgent.objects.create(website=self.main, agent=self.agent)

    def create_category_and_article(self, *, all_websites=True, website_ids=None, status="published", title="Reset your password"):
        self.client.force_authenticate(self.owner)
        category_response = self.client.post(
            "/api/v1/support/knowledge/categories/",
            {"name": f"Account help {title}", "description": "Account questions"},
            format="json",
        )
        self.assertEqual(category_response.status_code, 201)
        article_response = self.client.post(
            "/api/v1/support/knowledge/articles/",
            {
                "category_id": category_response.data["id"],
                "title": title,
                "summary": "Use the reset link from the sign-in page.",
                "body": "Open the sign-in page, choose Forgot password, and follow the secure email link.",
                "status": status,
                "all_websites": all_websites,
                "website_ids": website_ids or [],
                "is_featured": True,
            },
            format="json",
        )
        self.assertEqual(article_response.status_code, 201)
        return category_response.data, article_response.data

    def test_owner_can_create_scoped_article_and_agent_only_sees_assigned_published_answers(self):
        _, published = self.create_category_and_article(all_websites=False, website_ids=[str(self.main.id)])
        _, draft = self.create_category_and_article(status="draft", title="Internal draft")

        self.client.force_authenticate(self.agent_user)
        visible = self.client.get(f"/api/v1/support/knowledge/articles/?website={self.main.id}")
        self.assertEqual(visible.status_code, 200)
        self.assertEqual([item["id"] for item in visible.data], [published["id"]])

        denied_website = self.client.get(f"/api/v1/support/knowledge/articles/?website={self.products.id}")
        self.assertEqual(denied_website.status_code, 403)

        create_denied = self.client.post(
            "/api/v1/support/knowledge/articles/",
            {"title": "Agent draft", "body": "No", "status": "draft", "all_websites": True, "website_ids": []},
            format="json",
        )
        self.assertEqual(create_denied.status_code, 403)
        self.assertTrue(SupportKnowledgeArticle.objects.filter(pk=draft["id"], status="draft").exists())

    def test_public_widget_only_returns_published_articles_for_the_current_website(self):
        _, main_article = self.create_category_and_article(all_websites=False, website_ids=[str(self.main.id)])
        _, products_article = self.create_category_and_article(
            all_websites=False,
            website_ids=[str(self.products.id)],
            title="Products-only answer",
        )
        self.create_category_and_article(status="draft", title="Hidden draft")

        self.client.force_authenticate(None)
        response = self.client.get(
            f"/api/v1/support/widget/{self.main.site_key}/knowledge/?q=password",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["enabled"])
        self.assertEqual([item["id"] for item in response.data["articles"]], [main_article["id"]])
        self.assertNotIn(products_article["id"], [item["id"] for item in response.data["articles"]])

        wrong_origin = self.client.get(
            f"/api/v1/support/widget/{self.main.site_key}/knowledge/",
            HTTP_ORIGIN="https://products.example.com",
        )
        self.assertEqual(wrong_origin.status_code, 403)

    def test_article_detail_counts_views_and_feedback_is_pseudonymous_and_idempotent(self):
        _, article_payload = self.create_category_and_article()
        self.client.force_authenticate(None)
        detail_url = f"/api/v1/support/widget/{self.main.site_key}/knowledge/articles/{article_payload['id']}/"
        detail = self.client.get(detail_url, HTTP_ORIGIN="https://main.example.com")
        self.assertEqual(detail.status_code, 200)
        article = SupportKnowledgeArticle.objects.get(pk=article_payload["id"])
        self.assertEqual(article.view_count, 1)

        feedback_url = detail_url + "feedback/"
        client_key = "knowledge-browser-key-123456789"
        first = self.client.post(
            feedback_url,
            {"helpful": True, "client_key": client_key},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            feedback_url,
            {"helpful": True, "client_key": client_key},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(SupportKnowledgeFeedback.objects.count(), 1)
        article.refresh_from_db()
        self.assertEqual(article.helpful_count, 1)
        self.assertEqual(article.not_helpful_count, 0)

        changed = self.client.post(
            feedback_url,
            {"helpful": False, "client_key": client_key},
            format="json",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(changed.status_code, 200)
        article.refresh_from_db()
        self.assertEqual(article.helpful_count, 0)
        self.assertEqual(article.not_helpful_count, 1)

    def test_owner_can_disable_widget_self_service_without_hiding_team_articles(self):
        _, article = self.create_category_and_article()
        self.client.force_authenticate(self.owner)
        settings_response = self.client.patch(
            "/api/v1/support/knowledge/settings/",
            {"show_in_widget": False, "enabled": True},
            format="json",
        )
        self.assertEqual(settings_response.status_code, 200)
        self.assertFalse(settings_response.data["show_in_widget"])

        team_response = self.client.get("/api/v1/support/knowledge/articles/?status=published")
        self.assertEqual(team_response.status_code, 200)
        self.assertEqual([item["id"] for item in team_response.data], [article["id"]])

        self.client.force_authenticate(None)
        public_response = self.client.get(
            f"/api/v1/support/widget/{self.main.site_key}/knowledge/",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(public_response.status_code, 200)
        self.assertFalse(public_response.data["enabled"])
        config_response = self.client.get(
            f"/api/v1/support/widget/{self.main.site_key}/config/",
            HTTP_ORIGIN="https://main.example.com",
        )
        self.assertEqual(config_response.status_code, 200)
        self.assertFalse(config_response.data["knowledge_enabled"])

    def test_article_cannot_be_assigned_to_another_support_accounts_website(self):
        other_owner = User.objects.create_user(username="other-owner", email="other@example.com", password="pass")
        other_account = SupportAccount.objects.create(owner=other_owner, status=SupportAccount.Status.ACTIVE)
        other_website = SupportWebsite.objects.create(
            support_account=other_account,
            name="Other",
            domain="other.example.com",
            allowed_origins=["https://other.example.com"],
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/support/knowledge/articles/",
            {
                "title": "Invalid scope",
                "body": "This must not cross accounts.",
                "status": "published",
                "all_websites": False,
                "website_ids": [str(other_website.id)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SupportKnowledgeArticleWebsite.objects.filter(website=other_website).exists())

    def test_owner_can_delete_one_article_permanently(self):
        _, article = self.create_category_and_article(title="Delete one article")
        self.client.force_authenticate(self.owner)
        response = self.client.delete(f"/api/v1/support/knowledge/articles/{article['id']}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(SupportKnowledgeArticle.objects.filter(pk=article["id"]).exists())

    def test_owner_can_bulk_delete_only_selected_articles(self):
        _, first = self.create_category_and_article(title="Bulk delete first")
        _, second = self.create_category_and_article(title="Bulk delete second")
        _, untouched = self.create_category_and_article(title="Keep this article")
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/support/knowledge/articles/bulk-delete/",
            {"article_ids": [first["id"], second["id"]]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["deleted"], 2)
        self.assertFalse(SupportKnowledgeArticle.objects.filter(pk__in=[first["id"], second["id"]]).exists())
        self.assertTrue(SupportKnowledgeArticle.objects.filter(pk=untouched["id"]).exists())

    def test_article_response_identifies_writer(self):
        _, article = self.create_category_and_article(title="Author attribution")
        self.assertEqual(article["created_by"]["id"], str(self.owner.id))
        self.assertEqual(article["created_by"]["username"], self.owner.username)
