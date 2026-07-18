from __future__ import annotations

import re

from corsheaders.signals import check_request_enabled
from django.dispatch import receiver

from apps.support.models import SupportWebsite
from apps.support.widget_services import is_origin_allowed, widget_public_enabled

_WIDGET_PATH = re.compile(r"^/api/v1/support/widget/(?P<site_key>[0-9a-fA-F-]{36})/")


@receiver(check_request_enabled)
def allow_registered_support_widget_origins(sender, request, **kwargs):
    if not widget_public_enabled():
        return False
    match = _WIDGET_PATH.match(request.path)
    if not match:
        return False
    website = (
        SupportWebsite.objects.select_related("support_account")
        .filter(site_key=match.group("site_key"), is_active=True, widget_enabled=True)
        .first()
    )
    if not website or not website.support_account.has_product_access:
        return False
    return is_origin_allowed(website, request.headers.get("Origin", ""))
