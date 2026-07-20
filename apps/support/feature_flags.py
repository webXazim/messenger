from django.conf import settings


SUPPORT_FEATURE_FLAGS = {
    "websites_v2": "SUPPORT_WEBSITES_V2_ENABLED",
    "teams": "SUPPORT_TEAMS_ENABLED",
    "routing": "SUPPORT_ROUTING_ENABLED",
    "knowledge_v2": "SUPPORT_KNOWLEDGE_V2_ENABLED",
    "lifecycle_v2": "SUPPORT_LIFECYCLE_V2_ENABLED",
    "sla_v2": "SUPPORT_SLA_V2_ENABLED",
    "analytics_v2": "SUPPORT_ANALYTICS_V2_ENABLED",
    "automations": "SUPPORT_AUTOMATIONS_ENABLED",
    "security_v2": "SUPPORT_SECURITY_V2_ENABLED",
}


def support_feature_enabled(name: str) -> bool:
    setting_name = SUPPORT_FEATURE_FLAGS.get(name)
    if not setting_name:
        return False
    return bool(getattr(settings, setting_name, False))


def support_feature_snapshot() -> dict[str, bool]:
    return {name: support_feature_enabled(name) for name in SUPPORT_FEATURE_FLAGS}
