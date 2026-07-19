from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.support.models import SupportAccount, SupportKnowledgeSettings, SupportPrivacySettings, SupportWebsite, SupportWidgetSettings


@receiver(post_save, sender=SupportWebsite)
def ensure_support_widget_settings(sender, instance, created, **kwargs):
    SupportWidgetSettings.objects.get_or_create(
        website=instance,
        defaults={"brand_name": f"{instance.name} Support"},
    )


@receiver(post_save, sender=SupportAccount)
def ensure_support_knowledge_settings(sender, instance, created, **kwargs):
    SupportKnowledgeSettings.objects.get_or_create(support_account=instance)
    SupportPrivacySettings.objects.get_or_create(support_account=instance)

from django.db import transaction
from django.db.models.signals import post_delete, pre_delete

from apps.support.cache import invalidate_public_kb
from apps.support.models import (
    SupportKnowledgeArticle,
    SupportKnowledgeArticleWebsite,
    SupportKnowledgeCategory,
)


def _invalidate_kb_after_commit(account_id):
    if account_id:
        transaction.on_commit(lambda: invalidate_public_kb(account_id))


@receiver(post_save, sender=SupportKnowledgeSettings)
def invalidate_kb_for_settings(sender, instance, **kwargs):
    _invalidate_kb_after_commit(instance.support_account_id)


@receiver([post_save, post_delete], sender=SupportKnowledgeCategory)
def invalidate_kb_for_category(sender, instance, **kwargs):
    _invalidate_kb_after_commit(instance.support_account_id)


@receiver([post_save, post_delete], sender=SupportKnowledgeArticle)
def invalidate_kb_for_article(sender, instance, **kwargs):
    _invalidate_kb_after_commit(instance.support_account_id)


@receiver(pre_delete, sender=SupportKnowledgeArticleWebsite)
def remember_kb_assignment_account(sender, instance, **kwargs):
    instance._kb_account_id = (
        SupportKnowledgeArticle.objects.filter(pk=instance.article_id)
        .values_list("support_account_id", flat=True)
        .first()
    )


@receiver([post_save, post_delete], sender=SupportKnowledgeArticleWebsite)
def invalidate_kb_for_assignment(sender, instance, **kwargs):
    account_id = getattr(instance, "_kb_account_id", None)
    if account_id is None and instance.article_id:
        account_id = (
            SupportKnowledgeArticle.objects.filter(pk=instance.article_id)
            .values_list("support_account_id", flat=True)
            .first()
        )
    _invalidate_kb_after_commit(account_id)
