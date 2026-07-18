import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()

from apps.chat.websocket_routing import websocket_urlpatterns as chat_websocket_urlpatterns  # noqa: E402
from apps.support.websocket_routing import websocket_urlpatterns as support_websocket_urlpatterns  # noqa: E402

websocket_urlpatterns = [*chat_websocket_urlpatterns, *support_websocket_urlpatterns]

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
