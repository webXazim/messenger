from django.urls import path

from apps.support.consumers import SupportTeamConsumer, SupportWidgetConsumer

websocket_urlpatterns = [
    path("ws/support/", SupportTeamConsumer.as_asgi()),
    path("ws/support/widget/<uuid:site_key>/", SupportWidgetConsumer.as_asgi()),
]
