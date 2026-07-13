from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from config.admin_autoregistry import admin_model_coverage


@dataclass(frozen=True)
class CentralServiceResponse:
    status_code: int
    data: dict | list


class CentralServiceConfigurationError(RuntimeError):
    pass


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def sign_request(secret: str, method: str, path: str, timestamp: str, nonce: str, body_hash: str) -> str:
    canonical = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _signed_admin_headers(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    key = settings.AUTH_PAYMENT_ADMIN_SERVICE_KEY
    secret = settings.AUTH_PAYMENT_ADMIN_SIGNING_SECRET
    if not key or not secret:
        raise CentralServiceConfigurationError("auth_payment admin service credentials are not configured")
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    digest = body_sha256(body)
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Admin-Service-Key": key,
        "X-Admin-Timestamp": timestamp,
        "X-Admin-Nonce": nonce,
        "X-Admin-Signature": sign_request(secret, method, path, timestamp, nonce, digest),
    }


def auth_payment_request(path: str, *, method: str = "GET", payload: dict | None = None, bearer_token: str = "") -> CentralServiceResponse:
    body = b""
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    url = f"{settings.AUTH_PAYMENT_BASE_URL}{path}"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    else:
        headers.update(_signed_admin_headers(method, urlsplit(url).path, body))
    request = Request(url, data=body if method.upper() != "GET" else None, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=settings.AUTH_PAYMENT_REQUEST_TIMEOUT_SECONDS) as result:
            content = result.read().decode("utf-8")
            return CentralServiceResponse(result.status, json.loads(content or "{}"))
    except HTTPError as exc:
        content = exc.read().decode("utf-8")
        try:
            data = json.loads(content or "{}")
        except json.JSONDecodeError:
            data = {"detail": content or exc.reason}
        return CentralServiceResponse(exc.code, data)
    except URLError as exc:
        return CentralServiceResponse(503, {"detail": f"auth_payment unavailable: {exc.reason}"})


def centralization_snapshot() -> dict:
    return {
        "service": settings.CENTRAL_PROJECT_CODE,
        "business_product": settings.CENTRAL_BUSINESS_PRODUCT_CODE,
        "central_auth_enabled": settings.CENTRAL_AUTH_ENABLED,
        "central_payments_enabled": settings.CENTRAL_PAYMENTS_ENABLED,
        "central_admin_enabled": settings.CENTRAL_ADMIN_ENABLED,
        "auth_payment_base_url": settings.AUTH_PAYMENT_BASE_URL,
        "admin_control_base_url": settings.ADMIN_CONTROL_BASE_URL,
        "central_auth_public_base_url": getattr(settings, "CENTRAL_AUTH_PUBLIC_BASE_URL", settings.AUTH_PAYMENT_BASE_URL),
        "central_admin_public_base_url": getattr(settings, "CENTRAL_ADMIN_PUBLIC_BASE_URL", settings.ADMIN_CONTROL_BASE_URL),
        "central_login_url": getattr(settings, "CENTRAL_LOGIN_URL", ""),
        "central_signup_url": getattr(settings, "CENTRAL_SIGNUP_URL", ""),
        "central_account_url": getattr(settings, "CENTRAL_ACCOUNT_URL", ""),
        "central_password_reset_url": getattr(settings, "CENTRAL_PASSWORD_RESET_URL", ""),
        "central_email_verification_url": getattr(settings, "CENTRAL_EMAIL_VERIFICATION_URL", ""),
        "central_logout_url": getattr(settings, "CENTRAL_LOGOUT_URL", ""),
        "auth_payment_jwt_signing_key_configured": bool(
            getattr(settings, "AUTH_PAYMENT_JWT_PUBLIC_KEY", "")
            if str(getattr(settings, "AUTH_PAYMENT_JWT_ALGORITHM", "HS256")).startswith(("RS", "ES"))
            else getattr(settings, "AUTH_PAYMENT_JWT_SIGNING_KEY", "")
        ),
        "auth_payment_admin_credentials_configured": bool(
            settings.AUTH_PAYMENT_ADMIN_SERVICE_KEY and settings.AUTH_PAYMENT_ADMIN_SIGNING_SECRET
        ),
        "django_admin_model_coverage": admin_model_coverage(),
    }


def extract_bearer_token(request) -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return ""


def check_product_access(
    request,
    action: str,
    *,
    organization_slug: str = "",
    metadata: dict | None = None,
    quantity: int = 1,
    record_usage: bool = False,
    idempotency_key: str = "",
    source: str = "messenger",
) -> CentralServiceResponse:
    token = extract_bearer_token(request)
    if not token:
        return CentralServiceResponse(401, {"allowed": False, "reason": "missing_central_bearer_token"})
    payload = {
        "product": settings.CENTRAL_BUSINESS_PRODUCT_CODE,
        "action": action,
        "organization_slug": organization_slug,
        "quantity": quantity,
        "record_usage": record_usage,
        "idempotency_key": idempotency_key,
        "source": source,
        "metadata": metadata or {},
    }
    return auth_payment_request("/api/v1/business/access-check/", method="POST", payload=payload, bearer_token=token)
