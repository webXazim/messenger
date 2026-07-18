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
