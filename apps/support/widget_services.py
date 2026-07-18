from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.support.models import (
    SupportVisitor,
    SupportWebsite,
    SupportWidgetSession,
    SupportWidgetSettings,
)


class WidgetAccessError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class IssuedWidgetSession:
    session: SupportWidgetSession
    raw_token: str


def widget_public_enabled() -> bool:
    return bool(getattr(settings, "SUPPORT_CHAT_ENABLED", False) and getattr(settings, "SUPPORT_WIDGET_ENABLED", False))


def normalize_origin(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    port = parsed.port
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (parsed.scheme.lower() == "http" and port == 80)
    netloc = host if not port or default_port else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{netloc}"


def request_origin(request) -> str:
    return normalize_origin(request.headers.get("Origin", ""))


def allowed_origins_for_website(website: SupportWebsite) -> set[str]:
    configured = {normalize_origin(value) for value in (website.allowed_origins or [])}
    configured.discard("")
    if configured:
        return configured

    domain = (website.domain or "").strip().lower().rstrip(".")
    if not domain:
        return set()
    origins = {f"https://{domain}"}
    if not domain.startswith("www."):
        origins.add(f"https://www.{domain}")
    if settings.DEBUG:
        origins.add(f"http://{domain}")
        if not domain.startswith("www."):
            origins.add(f"http://www.{domain}")
    return origins


def is_origin_allowed(website: SupportWebsite, origin: str) -> bool:
    normalized = normalize_origin(origin)
    if not normalized:
        return not bool(getattr(settings, "SUPPORT_WIDGET_REQUIRE_ORIGIN", True))
    return normalized in allowed_origins_for_website(website)


def website_for_public_widget(site_key) -> SupportWebsite:
    website = (
        SupportWebsite.objects.select_related("support_account")
        .filter(site_key=site_key, is_active=True, widget_enabled=True)
        .first()
    )
    if not website or not website.support_account.has_product_access:
        raise WidgetAccessError("This support widget is not available.", code="widget_unavailable", status_code=404)
    return website


def assert_widget_request_allowed(request, website: SupportWebsite) -> str:
    if not widget_public_enabled():
        raise WidgetAccessError("This support widget is not available.", code="widget_disabled", status_code=404)
    origin = request_origin(request)
    if not is_origin_allowed(website, origin):
        raise WidgetAccessError("This website origin is not allowed.", code="origin_denied", status_code=403)
    return origin


def widget_settings_for(website: SupportWebsite) -> SupportWidgetSettings:
    settings_object, _ = SupportWidgetSettings.objects.get_or_create(
        website=website,
        defaults={"brand_name": f"{website.name} Support"},
    )
    return settings_object


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _session_expiry():
    hours = max(1, int(getattr(settings, "SUPPORT_WIDGET_SESSION_TTL_HOURS", 24 * 30) or (24 * 30)))
    return timezone.now() + timedelta(hours=hours)


def token_from_request(request) -> str:
    authorization = (request.headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return (request.headers.get("X-Support-Session-Token") or "").strip()


def _expire_session(session: SupportWidgetSession) -> None:
    if session.status == SupportWidgetSession.Status.ACTIVE and session.expires_at <= timezone.now():
        session.status = SupportWidgetSession.Status.EXPIRED
        session.closed_at = timezone.now()
        session.save(update_fields=["status", "closed_at", "updated_at"])


def authenticate_widget_session(
    *,
    website: SupportWebsite,
    session_id,
    raw_token: str,
    origin: str,
) -> SupportWidgetSession:
    session = (
        SupportWidgetSession.objects.select_related("visitor", "website")
        .filter(pk=session_id, website=website)
        .first()
    )
    if not session:
        raise WidgetAccessError("The visitor session was not found.", code="session_not_found", status_code=404)
    _expire_session(session)
    if not session.is_active:
        raise WidgetAccessError("The visitor session is no longer active.", code="session_unavailable", status_code=410)
    if not raw_token or not hmac.compare_digest(session.token_hash, _token_hash(raw_token)):
        raise WidgetAccessError("The visitor session token is invalid.", code="invalid_session_token", status_code=403)
    if normalize_origin(session.origin) != normalize_origin(origin):
        raise WidgetAccessError("The visitor session belongs to another website origin.", code="session_origin_mismatch", status_code=403)
    if session.visitor.is_blocked:
        raise WidgetAccessError("This visitor session is blocked.", code="visitor_blocked", status_code=403)
    return session


@transaction.atomic
def create_widget_session(
    *,
    website: SupportWebsite,
    origin: str,
    name: str = "",
    email: str = "",
    locale: str = "",
    current_page_url: str = "",
    referrer: str = "",
    user_agent: str = "",
) -> IssuedWidgetSession:
    settings_object = widget_settings_for(website)
    if settings_object.require_name and not (name or "").strip():
        raise WidgetAccessError("Your name is required to start support chat.", code="name_required", status_code=400)
    if settings_object.require_email and not (email or "").strip():
        raise WidgetAccessError("Your email is required to start support chat.", code="email_required", status_code=400)

    now = timezone.now()
    visitor = SupportVisitor.objects.create(
        website=website,
        name=(name or "").strip(),
        email=(email or "").strip().lower(),
        locale=(locale or "").strip()[:32],
        current_page_url=(current_page_url or "").strip(),
        referrer=(referrer or "").strip(),
        first_seen_at=now,
        last_seen_at=now,
    )
    raw_token = _new_token()
    session = SupportWidgetSession.objects.create(
        website=website,
        visitor=visitor,
        token_hash=_token_hash(raw_token),
        origin=normalize_origin(origin),
        expires_at=_session_expiry(),
        last_seen_at=now,
        user_agent=(user_agent or "").strip()[:500],
        current_page_url=(current_page_url or "").strip(),
        referrer=(referrer or "").strip(),
    )
    return IssuedWidgetSession(session=session, raw_token=raw_token)


@transaction.atomic
def update_widget_session(
    *,
    session: SupportWidgetSession,
    name: str | None = None,
    email: str | None = None,
    locale: str | None = None,
    current_page_url: str | None = None,
    referrer: str | None = None,
) -> SupportWidgetSession:
    now = timezone.now()
    visitor = session.visitor
    visitor_fields = ["last_seen_at", "updated_at"]
    visitor.last_seen_at = now
    if name is not None:
        visitor.name = name.strip()
        visitor_fields.append("name")
    if email is not None:
        visitor.email = email.strip().lower()
        visitor_fields.append("email")
    if locale is not None:
        visitor.locale = locale.strip()[:32]
        visitor_fields.append("locale")
    if current_page_url is not None:
        visitor.current_page_url = current_page_url.strip()
        visitor_fields.append("current_page_url")
        session.current_page_url = visitor.current_page_url
    if referrer is not None:
        visitor.referrer = referrer.strip()
        visitor_fields.append("referrer")
        session.referrer = visitor.referrer
    visitor.save(update_fields=list(dict.fromkeys(visitor_fields)))

    session.last_seen_at = now
    session.save(update_fields=["last_seen_at", "current_page_url", "referrer", "updated_at"])
    return session


@transaction.atomic
def refresh_widget_session(session: SupportWidgetSession) -> IssuedWidgetSession:
    raw_token = _new_token()
    session.token_hash = _token_hash(raw_token)
    session.token_version += 1
    session.expires_at = _session_expiry()
    session.last_seen_at = timezone.now()
    session.save(update_fields=["token_hash", "token_version", "expires_at", "last_seen_at", "updated_at"])
    return IssuedWidgetSession(session=session, raw_token=raw_token)


@transaction.atomic
def close_widget_session(session: SupportWidgetSession, *, revoked: bool = False) -> SupportWidgetSession:
    session.status = SupportWidgetSession.Status.REVOKED if revoked else SupportWidgetSession.Status.CLOSED
    session.closed_at = timezone.now()
    session.save(update_fields=["status", "closed_at", "updated_at"])
    return session


@transaction.atomic
def regenerate_website_site_key(website: SupportWebsite) -> SupportWebsite:
    import uuid

    website.site_key = uuid.uuid4()
    website.save(update_fields=["site_key", "updated_at"])
    SupportWidgetSession.objects.filter(
        website=website,
        status=SupportWidgetSession.Status.ACTIVE,
    ).update(
        status=SupportWidgetSession.Status.REVOKED,
        closed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return website
