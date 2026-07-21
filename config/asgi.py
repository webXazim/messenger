"""ASGI entrypoint for Django HTTP traffic.

All application WebSocket connections are terminated by the Rust/Axum
realtime service. Keeping Django's ASGI app HTTP-only avoids importing or
running a second WebSocket stack inside Granian.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
