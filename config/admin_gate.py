import base64
import hashlib
import hmac
import json
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseNotFound
from django.shortcuts import redirect


SESSION_KEY = "project_admin_gate"


def _secret():
    return getattr(settings, "PROJECT_ADMIN_GATE_SECRET", "") or settings.SECRET_KEY


def _client_ip(request):
    forwarded = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",", 1)[0].strip() if forwarded else request.META.get("REMOTE_ADDR", ""))


def _unb64(value):
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def verify_project_admin_handoff(token, request):
    if not token or "." not in token:
        return False
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(_secret().encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        payload = json.loads(_unb64(encoded).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False
    if payload.get("source") != "super_admin_gateway":
        return False
    if payload.get("project") != getattr(settings, "PROJECT_ADMIN_GATE_SLUG", "messenger"):
        return False
    if int(payload.get("exp", 0)) < int(time.time()):
        return False
    if payload.get("ip") and payload.get("ip") != _client_ip(request):
        return False
    nonce = str(payload.get("nonce") or "").strip()
    if not nonce:
        return False
    ttl = max(1, int(payload["exp"]) - int(time.time()))
    nonce_key = f"project-admin-handoff:{payload.get('project')}:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"
    if not cache.add(nonce_key, "1", timeout=ttl):
        return False
    request.session[SESSION_KEY] = {"exp": int(payload["exp"]), "email": payload.get("email", ""), "ip": payload.get("ip", "")}
    return True


def project_admin_entry(request):
    if not verify_project_admin_handoff(request.GET.get("handoff", ""), request):
        return HttpResponseNotFound("Not found")
    return redirect("/admin/login/?next=/admin/")


class ProjectAdminGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            gate = request.session.get(SESSION_KEY) or {}
            if int(gate.get("exp", 0)) < int(time.time()) or (gate.get("ip") and gate.get("ip") != _client_ip(request)):
                request.session.pop(SESSION_KEY, None)
                return HttpResponseNotFound("Not found")
        return self.get_response(request)
