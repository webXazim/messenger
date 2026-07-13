from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404


def firebase_messaging_service_worker(request):
    """
    Serve the Firebase messaging service worker from the site root so the
    browser does not hit the SPA catch-all and receive index.html instead.
    """

    candidates = [
        Path(settings.BASE_DIR) / "frontend" / "dist" / "firebase-messaging-sw.js",
        Path(settings.BASE_DIR) / "frontend" / "public" / "firebase-messaging-sw.js",
    ]
    for candidate in candidates:
        if candidate.exists():
            response = FileResponse(candidate.open("rb"), content_type="application/javascript")
            response["Service-Worker-Allowed"] = "/"
            return response
    raise Http404("Firebase messaging service worker not found.")
