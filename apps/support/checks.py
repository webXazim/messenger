from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


def _is_https(value: str) -> bool:
    try:
        return urlparse(str(value or "")).scheme.lower() == "https"
    except ValueError:
        return False


@register(Tags.security, deploy=True)
def support_deploy_checks(app_configs, **kwargs):
    issues = []
    support_enabled = bool(getattr(settings, "SUPPORT_CHAT_ENABLED", False))
    widget_enabled = bool(getattr(settings, "SUPPORT_WIDGET_ENABLED", False))
    calls_enabled = bool(getattr(settings, "SUPPORT_CALLS_ENABLED", False))

    if not support_enabled:
        if widget_enabled or calls_enabled:
            issues.append(Error(
                "Support widget or calls cannot be enabled while SUPPORT_CHAT_ENABLED is disabled.",
                id="support.E001",
            ))
        return issues

    if widget_enabled:
        if not bool(getattr(settings, "SUPPORT_WIDGET_REQUIRE_ORIGIN", True)):
            issues.append(Error(
                "SUPPORT_WIDGET_REQUIRE_ORIGIN must remain enabled in production.",
                id="support.E002",
            ))
        script_url = str(getattr(settings, "SUPPORT_WIDGET_SCRIPT_URL", "") or "")
        if not _is_https(script_url):
            issues.append(Error(
                "SUPPORT_WIDGET_SCRIPT_URL must be an HTTPS URL in production.",
                id="support.E003",
            ))

    frontend_url = str(getattr(settings, "FRONTEND_BASE_URL", "") or "")
    if frontend_url and not _is_https(frontend_url):
        issues.append(Warning(
            "FRONTEND_BASE_URL should use HTTPS in production.",
            id="support.W001",
        ))

    if calls_enabled:
        if not widget_enabled:
            issues.append(Error(
                "SUPPORT_WIDGET_ENABLED must be enabled before Support guest calls can be enabled.",
                id="support.E004",
            ))
        turn_provider = str(getattr(settings, "TURN_PROVIDER", "legacy") or "legacy").strip().lower()
        if turn_provider == "cloudflare":
            if not str(getattr(settings, "CLOUDFLARE_TURN_KEY_ID", "") or "").strip():
                issues.append(Error(
                    "CLOUDFLARE_TURN_KEY_ID is required for production Support calls.",
                    id="support.E005",
                ))
            if not str(getattr(settings, "CLOUDFLARE_TURN_API_TOKEN", "") or "").strip():
                issues.append(Error(
                    "CLOUDFLARE_TURN_API_TOKEN is required for production Support calls.",
                    id="support.E006",
                ))
        else:
            if not str(getattr(settings, "TURN_URIS_JSON", "") or "").strip():
                issues.append(Error(
                    "TURN_URIS_JSON is required for legacy Support calls.",
                    id="support.E005",
                ))
            has_turn_auth = bool(str(getattr(settings, "TURN_SHARED_SECRET", "") or "").strip()) or bool(
                str(getattr(settings, "TURN_STATIC_USERNAME", "") or "").strip()
                and str(getattr(settings, "TURN_STATIC_PASSWORD", "") or "").strip()
            )
            if not has_turn_auth:
                issues.append(Error(
                    "Legacy TURN credentials are required for Support calls.",
                    id="support.E006",
                ))
        if str(getattr(settings, "REALTIME_TRANSPORT", "") or "").lower() != "axum":
            issues.append(Error(
                "Support calls require the Axum realtime transport in production.",
                id="support.E007",
            ))
        ring_timeout = int(getattr(settings, "SUPPORT_CALL_RING_TIMEOUT_SECONDS", 45) or 45)
        if not 15 <= ring_timeout <= 180:
            issues.append(Error(
                "SUPPORT_CALL_RING_TIMEOUT_SECONDS must be between 15 and 180.",
                id="support.E008",
            ))
        signal_bytes = int(getattr(settings, "SUPPORT_CALL_SIGNAL_MAX_BYTES", 131072) or 131072)
        if not 16384 <= signal_bytes <= 1048576:
            issues.append(Error(
                "SUPPORT_CALL_SIGNAL_MAX_BYTES must be between 16384 and 1048576.",
                id="support.E009",
            ))

    return issues
