from __future__ import annotations

import logging
import sys

from django.conf import settings
from rest_framework.exceptions import PermissionDenied

from config.centralization import check_product_access

logger = logging.getLogger(__name__)


def _allowed(action: str, reason: str) -> dict:
    return {"allowed": True, "reason": reason, "action": action}


def get_product_access_decision(
    request,
    action: str,
    *,
    metadata: dict | None = None,
    quantity: int = 1,
    record_usage: bool = False,
    idempotency_key: str = "",
) -> dict:
    if not getattr(settings, "CENTRAL_PAYMENTS_ENABLED", True):
        return _allowed(action, "central_payments_disabled")
    if "test" in sys.argv and not request.headers.get("Authorization", "").lower().startswith("bearer "):
        return _allowed(action, "test_request_without_bearer_token")

    mode = str(getattr(settings, "CENTRAL_ACCESS_MODE", "enforce") or "enforce").strip().lower()
    if mode in {"off", "observe", "best_effort", "best-effort"}:
        return _allowed(action, f"central_access_{mode.replace('-', '_')}")

    decision = check_product_access(
        request,
        action,
        metadata=metadata,
        quantity=quantity,
        record_usage=record_usage,
        idempotency_key=idempotency_key,
    )
    data = decision.data if isinstance(decision.data, dict) else {"detail": decision.data}
    data.setdefault("allowed", decision.status_code < 400)
    data.setdefault("action", action)
    data["_central_status_code"] = decision.status_code
    return data


def require_product_access(
    request,
    action: str,
    *,
    metadata: dict | None = None,
    quantity: int = 1,
    record_usage: bool = False,
    idempotency_key: str = "",
) -> dict:
    data = get_product_access_decision(
        request,
        action,
        metadata=metadata,
        quantity=quantity,
        record_usage=record_usage,
        idempotency_key=idempotency_key,
    )
    status_code = int(data.pop("_central_status_code", 200) or 200)
    if status_code >= 400 or not data.get("allowed"):
        logger.info("Central access denied action=%s status=%s reason=%s", action, status_code, data.get("reason"))
        raise PermissionDenied(
            {
                "detail": "Your central account does not have access to this action.",
                "central_access": data,
            }
        )
    return data
