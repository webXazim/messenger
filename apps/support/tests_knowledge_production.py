from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.support.models import SupportAccount, SupportKnowledgeArticle, SupportKnowledgeArticleRevision, SupportKnowledgeRelatedArticle


class SupportKnowledgeProductionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="kb-owner", password="pass")
        self.account = SupportAccount.objects.create(owner=self.user, name="Knowledge account")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_article_create_writes_revision(self):
        response = self.client.post("/api/support/knowledge/articles/", {"title": "Reset password", "body": "Steps", "status": "draft", "all_websites": True, "website_ids": [], "is_featured": False, "language": "en", "seo_description": "Reset access"}, format="json")
        self.assertEqual(response.status_code, 201)
        article = SupportKnowledgeArticle.objects.get(pk=response.data["id"])
        self.assertEqual(article.revisions.count(), 1)
        self.assertEqual(article.seo_description, "Reset access")

    def test_related_articles_cannot_cross_accounts(self):
        other_user = get_user_model().objects.create_user(username="other-owner", password="pass")
        other_account = SupportAccount.objects.create(owner=other_user, name="Other")
        first = SupportKnowledgeArticle.objects.create(support_account=self.account, title="A", slug="a", body="A")
        second = SupportKnowledgeArticle.objects.create(support_account=other_account, title="B", slug="b", body="B")
        link = SupportKnowledgeRelatedArticle(article=first, related_article=second)
        with self.assertRaises(Exception):
            link.full_clean()

    def test_revision_restore_returns_article_to_draft(self):
        article = SupportKnowledgeArticle.objects.create(support_account=self.account, title="Current", slug="current", body="Current", status="published")
        revision = SupportKnowledgeArticleRevision.objects.create(article=article, version=1, title="Previous", body="Previous", status="published", all_websites=True, website_ids=[])
        response = self.client.post(f"/api/support/knowledge/articles/{article.id}/revisions/{revision.id}/restore/")
        self.assertEqual(response.status_code, 200)
        article.refresh_from_db()
        self.assertEqual(article.title, "Previous")
        self.assertEqual(article.status, "draft")
