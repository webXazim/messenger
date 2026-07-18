from django.apps import AppConfig


class SupportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.support"
    verbose_name = "Support Chat"

    def ready(self):
        from apps.support import checks, cors, signals  # noqa: F401
