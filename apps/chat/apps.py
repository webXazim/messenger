from django.apps import AppConfig

class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.chat"

    def ready(self):
        from . import checks  # noqa: F401
        from . import schema  # noqa: F401
