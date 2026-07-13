from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from .centralization import centralization_snapshot
from .remote_model_admin import MODEL_ACTIONS, execute_model_action


MAX_SIGNATURE_AGE_SECONDS = 300
NONCE_CACHE_PREFIX = "admin-control-nonce"


@dataclass(frozen=True)
class AdminActionResult:
    status_code: int
    data: dict


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def sign_request(secret: str, method: str, path: str, timestamp: str, nonce: str, body_hash: str) -> str:
    canonical = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_admin_request(request) -> AdminActionResult | None:
    key = getattr(settings, "AUTH_PAYMENT_ADMIN_SERVICE_KEY", "")
    secret = getattr(settings, "AUTH_PAYMENT_ADMIN_SIGNING_SECRET", "")
    if not key or not secret:
        return AdminActionResult(503, {"detail": "admin service credentials are not configured"})
    if request.headers.get("X-Admin-Service-Key") != key:
        return AdminActionResult(401, {"detail": "invalid admin service key"})

    timestamp = request.headers.get("X-Admin-Timestamp", "")
    nonce = request.headers.get("X-Admin-Nonce", "")
    signature = request.headers.get("X-Admin-Signature", "")
    if not timestamp or not nonce or not signature:
        return AdminActionResult(401, {"detail": "missing admin signature headers"})
    try:
        age = abs(int(time.time()) - int(timestamp))
    except ValueError:
        return AdminActionResult(401, {"detail": "invalid admin timestamp"})
    if age > MAX_SIGNATURE_AGE_SECONDS:
        return AdminActionResult(401, {"detail": "admin signature expired"})
    nonce_key = f"{NONCE_CACHE_PREFIX}:{timestamp}:{nonce}"
    if not cache.add(nonce_key, "1", timeout=MAX_SIGNATURE_AGE_SECONDS):
        return AdminActionResult(401, {"detail": "admin signature nonce already used"})

    expected = sign_request(secret, request.method, request.path, timestamp, nonce, body_sha256(request.body))
    if not hmac.compare_digest(expected, signature):
        return AdminActionResult(401, {"detail": "invalid admin signature"})
    return None


def action_catalog() -> list[dict]:
    return [
        {
            "code": "project.ping",
            "label": "Ping project",
            "description": "Confirm signed admin control can reach this project.",
            "method": "POST",
            "dangerous": False,
        },
        {
            "code": "project.readiness",
            "label": "Read centralization readiness",
            "description": "Return the same centralization metadata used by readiness checks.",
            "method": "POST",
            "dangerous": False,
        },
        {
            "code": "cache.clear",
            "label": "Clear Django cache",
            "description": "Clear this project's configured Django cache backend.",
            "method": "POST",
            "dangerous": True,
        },
        *MODEL_ACTIONS,
    ]


def execute_action(action: str, payload: dict | None = None) -> AdminActionResult:
    payload = payload or {}
    if action == "project.ping":
        return AdminActionResult(200, {"status": "ok", "project": settings.CENTRAL_PROJECT_CODE})
    if action == "project.readiness":
        return AdminActionResult(200, {"status": "ok", "metadata": centralization_snapshot()})
    if action == "cache.clear":
        cache.clear()
        return AdminActionResult(200, {"status": "ok", "cleared": True})
    model_result = safe_model_action(action, payload)
    if model_result:
        return model_result
    return AdminActionResult(404, {"detail": f"unsupported admin action: {action}", "available_actions": action_catalog()})


def parse_payload(request) -> dict:
    if not request.body:
        return {}
    return json.loads(request.body.decode("utf-8"))


def safe_model_action(action: str, payload: dict) -> AdminActionResult | None:
    try:
        result = execute_model_action(action, payload)
    except ObjectDoesNotExist:
        return AdminActionResult(404, {"detail": "record not found"})
    except (ValidationError, ValueError, TypeError) as exc:
        return AdminActionResult(400, {"detail": str(exc)})
    if result is None:
        return None
    status_code, data = result
    return AdminActionResult(status_code, data)
