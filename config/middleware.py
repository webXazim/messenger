import logging
import time
import uuid

from django.conf import settings
from django.db import connection

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

class QueryMetricsMiddleware:
    """Record aggregate SQL cost without retaining SQL text or parameters.

    Disabled by default in production. When enabled, only slow/high-query
    requests are logged unless DJANGO_QUERY_METRICS_LOG_ALL is true. This
    keeps the measurement layer safe for real traffic and avoids DEBUG query
    collection overhead.
    """

    logger = logging.getLogger("performance.django")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "DJANGO_QUERY_METRICS_ENABLED", False):
            return self.get_response(request)

        excluded = getattr(settings, "DJANGO_QUERY_METRICS_EXCLUDE_PREFIXES", ())
        if any(request.path.startswith(prefix) for prefix in excluded):
            return self.get_response(request)

        stats = {"count": 0, "duration": 0.0}

        def wrapper(execute, sql, params, many, context):
            started = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                stats["count"] += 1
                stats["duration"] += time.perf_counter() - started

        started = time.perf_counter()
        with connection.execute_wrapper(wrapper):
            response = self.get_response(request)
        request_duration_ms = (time.perf_counter() - started) * 1000
        db_duration_ms = stats["duration"] * 1000
        query_count = int(stats["count"])

        should_log = (
            getattr(settings, "DJANGO_QUERY_METRICS_LOG_ALL", False)
            or request_duration_ms >= getattr(settings, "DJANGO_QUERY_METRICS_REQUEST_MS", 250)
            or db_duration_ms >= getattr(settings, "DJANGO_QUERY_METRICS_DB_MS", 100)
            or query_count >= getattr(settings, "DJANGO_QUERY_METRICS_MAX_QUERIES", 20)
            or getattr(response, "status_code", 200) >= 500
        )
        if should_log:
            self.logger.info(
                "request_performance method=%s path=%s status=%s duration_ms=%.2f db_ms=%.2f queries=%s",
                request.method,
                request.path,
                getattr(response, "status_code", 200),
                request_duration_ms,
                db_duration_ms,
                query_count,
            )

        if getattr(settings, "DEBUG", False):
            response["Server-Timing"] = f"db;dur={db_duration_ms:.2f}, app;dur={request_duration_ms:.2f}"
            response["X-DB-Query-Count"] = str(query_count)
        return response

