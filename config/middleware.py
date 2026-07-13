import uuid

from config.logging_utils import clear_request_id, set_request_id


class RequestIDMiddleware:
    header_name = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.META.get(self.header_name) or str(uuid.uuid4())
        set_request_id(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            clear_request_id()
        response["X-Request-ID"] = request.request_id
        return response


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(self)")
        if request.is_secure():
            response.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
