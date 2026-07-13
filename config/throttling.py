from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import UserRateThrottle


class UnsafeUserRateThrottle(UserRateThrottle):
    """Apply the global user throttle to writes, while allowing chat reads to poll."""

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)
