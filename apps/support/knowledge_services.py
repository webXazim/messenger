from __future__ import annotations

import hashlib
import re
from typing import Iterable

from django.db import transaction
from django.db.models import F, Q, QuerySet
from django.utils import timezone
from django.utils.text import slugify

from apps.support.models import (
    SupportKnowledgeArticle,
    SupportKnowledgeArticleWebsite,
    SupportKnowledgeFeedback,
    SupportKnowledgeSettings,
    SupportWebsite,
)
from apps.support.services import SupportContext, visible_websites


class SupportKnowledgeError(Exception):
    def __init__(self, detail: str, *, code: str = "knowledge_invalid", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


def knowledge_settings_for(account):
    settings_obj, _ = SupportKnowledgeSettings.objects.get_or_create(support_account=account)
    return settings_obj


def _website_visibility_q(website: SupportWebsite) -> Q:
    return Q(all_websites=True) | Q(website_assignments__website=website)


def public_articles_for_website(website: SupportWebsite) -> QuerySet[SupportKnowledgeArticle]:
    settings_obj = knowledge_settings_for(website.support_account)
    if not settings_obj.enabled or not settings_obj.show_in_widget:
        return SupportKnowledgeArticle.objects.none()
    return (
        SupportKnowledgeArticle.objects.filter(
            support_account=website.support_account,
            status=SupportKnowledgeArticle.Status.PUBLISHED,
        )
        .filter(_website_visibility_q(website))
        .select_related("category")
        .prefetch_related("website_assignments__website")
        .distinct()
    )


def team_articles_for_context(
    context: SupportContext,
    *,
    website: SupportWebsite | None = None,
    status_value: str | None = None,
) -> QuerySet[SupportKnowledgeArticle]:
    if not context.account:
        return SupportKnowledgeArticle.objects.none()
    queryset = (
        SupportKnowledgeArticle.objects.filter(support_account=context.account)
        .select_related("category", "created_by", "updated_by")
        .prefetch_related("website_assignments__website")
    )
    if context.role != "owner":
        queryset = queryset.filter(status=SupportKnowledgeArticle.Status.PUBLISHED)
        permitted_ids = visible_websites(context).values_list("id", flat=True)
        queryset = queryset.filter(Q(all_websites=True) | Q(website_assignments__website_id__in=permitted_ids)).distinct()
    elif status_value in dict(SupportKnowledgeArticle.Status.choices):
        queryset = queryset.filter(status=status_value)
    if website is not None:
        if not visible_websites(context).filter(pk=website.pk).exists():
            return SupportKnowledgeArticle.objects.none()
        queryset = queryset.filter(_website_visibility_q(website)).distinct()
    return queryset


def search_article_queryset(queryset, query: str):
    normalized = re.sub(r"\s+", " ", (query or "").strip())
    if not normalized:
        return queryset
    terms = [term for term in normalized.split(" ") if len(term) > 1][:8]
    if not terms:
        return queryset
    condition = Q()
    for term in terms:
        condition |= Q(title__icontains=term) | Q(summary__icontains=term) | Q(body__icontains=term)
    return queryset.filter(condition)


def public_search_articles(
    website: SupportWebsite,
    *,
    query: str = "",
    category_id=None,
    limit: int | None = None,
):
    settings_obj = knowledge_settings_for(website.support_account)
    queryset = public_articles_for_website(website)
    if category_id:
        queryset = queryset.filter(category_id=category_id, category__is_active=True)
    queryset = search_article_queryset(queryset, query)
    max_results = max(1, min(10, int(limit or settings_obj.max_suggestions or 5)))
    return queryset.order_by("-is_featured", "title")[:max_results]


def unique_article_slug(account, title: str, *, exclude_id=None) -> str:
    base = slugify(title or "article")[:180] or "article"
    slug = base
    counter = 2
    queryset = SupportKnowledgeArticle.objects.filter(support_account=account)
    if exclude_id:
        queryset = queryset.exclude(pk=exclude_id)
    while queryset.filter(slug=slug).exists():
        suffix = f"-{counter}"
        slug = f"{base[:200-len(suffix)]}{suffix}"
        counter += 1
    return slug


def replace_article_websites(article: SupportKnowledgeArticle, website_ids: Iterable) -> None:
    normalized = list(dict.fromkeys(str(value) for value in (website_ids or []) if value))
    if article.all_websites:
        article.website_assignments.all().delete()
        return
    websites = list(
        SupportWebsite.objects.filter(
            support_account=article.support_account,
            is_active=True,
            id__in=normalized,
        )
    )
    if len(websites) != len(normalized):
        raise SupportKnowledgeError("One or more selected websites are unavailable.", code="invalid_websites")
    if not websites:
        raise SupportKnowledgeError(
            "Select at least one website or make the article available to all websites.",
            code="website_required",
        )
    article.website_assignments.all().delete()
    SupportKnowledgeArticleWebsite.objects.bulk_create(
        [SupportKnowledgeArticleWebsite(article=article, website=website) for website in websites]
    )


def publish_state(article: SupportKnowledgeArticle, previous_status: str | None = None) -> None:
    if article.status == SupportKnowledgeArticle.Status.PUBLISHED and not article.published_at:
        article.published_at = timezone.now()
        article.save(update_fields=["published_at", "updated_at"])
    elif previous_status == SupportKnowledgeArticle.Status.PUBLISHED and article.status != previous_status:
        article.published_at = None
        article.save(update_fields=["published_at", "updated_at"])


def record_article_view(article: SupportKnowledgeArticle) -> None:
    SupportKnowledgeArticle.objects.filter(pk=article.pk).update(view_count=F("view_count") + 1)


def record_article_feedback(*, article: SupportKnowledgeArticle, website: SupportWebsite, client_key: str, helpful: bool):
    raw_key = (client_key or "").strip()
    if len(raw_key) < 16 or len(raw_key) > 200:
        raise SupportKnowledgeError("A valid browser feedback key is required.", code="invalid_client_key")
    if article.support_account_id != website.support_account_id:
        raise SupportKnowledgeError("This article is unavailable for the selected website.", code="article_unavailable", status_code=404)
    if not public_articles_for_website(website).filter(pk=article.pk).exists():
        raise SupportKnowledgeError("This article is unavailable.", code="article_unavailable", status_code=404)
    settings_obj = knowledge_settings_for(website.support_account)
    if not settings_obj.allow_article_feedback:
        raise SupportKnowledgeError("Article feedback is disabled.", code="feedback_disabled", status_code=409)
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    with transaction.atomic():
        existing = SupportKnowledgeFeedback.objects.select_for_update().filter(
            article=article,
            website=website,
            client_key_hash=key_hash,
        ).first()
        previous = existing.helpful if existing else None
        if existing:
            existing.helpful = helpful
            existing.save(update_fields=["helpful", "updated_at"])
        else:
            existing = SupportKnowledgeFeedback.objects.create(
                article=article,
                website=website,
                client_key_hash=key_hash,
                helpful=helpful,
            )
        updates = {}
        if previous is None:
            updates["helpful_count" if helpful else "not_helpful_count"] = F("helpful_count" if helpful else "not_helpful_count") + 1
        elif previous != helpful:
            updates["helpful_count" if helpful else "not_helpful_count"] = F("helpful_count" if helpful else "not_helpful_count") + 1
            updates["helpful_count" if previous else "not_helpful_count"] = F("helpful_count" if previous else "not_helpful_count") - 1
        if updates:
            SupportKnowledgeArticle.objects.filter(pk=article.pk).update(**updates)
    return existing
