import logging
from datetime import timedelta
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PureWindowsPath
from typing import List

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except Exception:  # pragma: no cover
    firebase_admin = None
    credentials = None
    messaging = None


@dataclass
class PushSendResult:
    attempted: int
    sent: int
    failed: int
    engine: str
    invalid_tokens: List[str] = field(default_factory=list)
    transient_failures: List[str] = field(default_factory=list)


def resolve_firebase_service_account_path():
    raw_path = str(getattr(settings, "FIREBASE_SERVICE_ACCOUNT_PATH", "") or "").strip()
    if not raw_path:
        return None
    configured = Path(raw_path)
    candidates = [configured]
    base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    windows_name = PureWindowsPath(raw_path).name
    configured_name = windows_name if ("\\" in raw_path and windows_name) else (configured.name or windows_name)
    if configured_name:
        candidates.extend(
            [
                base_dir / configured_name,
                base_dir / "SNM" / configured_name,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    logger.warning(
        "Firebase service account file was not found. configured_path=%s candidates=%s",
        raw_path,
        [str(candidate) for candidate in candidates],
    )
    return None


def is_firebase_configured():
    return bool(
        firebase_admin is not None
        and credentials is not None
        and resolve_firebase_service_account_path() is not None
        and str(getattr(settings, "FIREBASE_PROJECT_ID", "") or "").strip()
    )


def ensure_firebase_app():
    if firebase_admin is None or credentials is None:
        return None
    if firebase_admin._apps:
        return firebase_admin.get_app()
    path = resolve_firebase_service_account_path()
    if path is None:
        return None
    try:
        cred = credentials.Certificate(str(path))
        return firebase_admin.initialize_app(cred, {"projectId": getattr(settings, "FIREBASE_PROJECT_ID", "") or None})
    except Exception:
        logger.exception("Firebase app initialization failed. path=%s", path)
        return None


def _classify_fcm_error(exc):
    if exc is None:
        return ""
    parts = [
        exc.__class__.__name__,
        getattr(exc, "code", None),
        getattr(exc, "status", None),
        str(exc),
    ]
    return " ".join(str(part).strip() for part in parts if part).strip()


def _is_invalid_token_error(code):
    normalized = str(code or "").strip().lower()
    return any(
        marker in normalized
        for marker in [
            "unregistered",
            "not_found",
            "invalid_argument",
            "senderid",
            "registration-token-not-registered",
            "requested entity was not found",
        ]
    )


def send_push(tokens, *, title, body, data=None):
    tokens = [token for token in tokens if token]
    if not tokens:
        return PushSendResult(0, 0, 0, "noop")
    app = ensure_firebase_app()
    if app is None or messaging is None:
        logger.info("Push fallback: firebase not configured. tokens=%s title=%s", len(tokens), title)
        return PushSendResult(len(tokens), 0, len(tokens), "unconfigured", transient_failures=list(tokens))
    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
    )
    dry_run = getattr(settings, "FCM_DRY_RUN", True)
    response = messaging.send_each_for_multicast(message, dry_run=dry_run, app=app)
    invalid_tokens = []
    transient_failures = []
    for token, item in zip(tokens, getattr(response, "responses", []) or []):
        if getattr(item, "success", False):
            continue
        code = _classify_fcm_error(getattr(item, "exception", None)).lower()
        if _is_invalid_token_error(code):
            invalid_tokens.append(token)
        else:
            transient_failures.append(token)
    if invalid_tokens or transient_failures:
        logger.warning(
            "FCM multicast completed with failures. attempted=%s sent=%s failed=%s invalid=%s transient=%s",
            len(tokens),
            getattr(response, "success_count", 0),
            getattr(response, "failure_count", 0),
            len(invalid_tokens),
            len(transient_failures),
        )
    return PushSendResult(
        len(tokens),
        getattr(response, "success_count", 0),
        getattr(response, "failure_count", 0),
        "firebase",
        invalid_tokens=invalid_tokens,
        transient_failures=transient_failures,
    )


def send_push_with_options(
    tokens,
    *,
    title,
    body,
    data=None,
    include_notification=True,
    android_priority=None,
    android_ttl_seconds=None,
    android_collapse_key=None,
):
    tokens = [token for token in tokens if token]
    if not tokens:
        return PushSendResult(0, 0, 0, "noop")
    app = ensure_firebase_app()
    if app is None or messaging is None:
        logger.info("Push fallback: firebase not configured. tokens=%s title=%s", len(tokens), title)
        return PushSendResult(len(tokens), 0, len(tokens), "unconfigured", transient_failures=list(tokens))
    android_config = None
    if android_priority or android_ttl_seconds is not None or android_collapse_key:
        android_ttl = None
        if android_ttl_seconds is not None:
            android_ttl = timedelta(seconds=max(int(android_ttl_seconds), 0))
        android_config = messaging.AndroidConfig(
            priority=android_priority,
            ttl=android_ttl,
            collapse_key=android_collapse_key,
        )
    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body) if include_notification else None,
        data={k: str(v) for k, v in (data or {}).items()},
        android=android_config,
    )
    dry_run = getattr(settings, "FCM_DRY_RUN", True)
    response = messaging.send_each_for_multicast(message, dry_run=dry_run, app=app)
    invalid_tokens = []
    transient_failures = []
    for token, item in zip(tokens, getattr(response, "responses", []) or []):
        if getattr(item, "success", False):
            continue
        code = _classify_fcm_error(getattr(item, "exception", None)).lower()
        if _is_invalid_token_error(code):
            invalid_tokens.append(token)
        else:
            transient_failures.append(token)
    if invalid_tokens or transient_failures:
        logger.warning(
            "FCM multicast completed with failures. attempted=%s sent=%s failed=%s invalid=%s transient=%s include_notification=%s android_priority=%s android_ttl_seconds=%s android_collapse_key=%s",
            len(tokens),
            getattr(response, "success_count", 0),
            getattr(response, "failure_count", 0),
            len(invalid_tokens),
            len(transient_failures),
            include_notification,
            android_priority or "",
            "" if android_ttl_seconds is None else android_ttl_seconds,
            android_collapse_key or "",
        )
    return PushSendResult(
        len(tokens),
        getattr(response, "success_count", 0),
        getattr(response, "failure_count", 0),
        "firebase",
        invalid_tokens=invalid_tokens,
        transient_failures=transient_failures,
    )
