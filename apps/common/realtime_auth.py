from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import jwt
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.chat.models import ConversationParticipant
from apps.common.realtime import (
    RealtimeAudience,
    conversation_audience,
    support_user_audience,
    support_visitor_audience,
    support_website_audience,
    user_audience,
)
from apps.support.services import get_support_context, visible_websites
from apps.common.realtime_presence import cache_user_presence_metadata


class RealtimeCredentialError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid_realtime_credential", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class IssuedRealtimeCredential:
    token: str
    expires_in: int
    expires_at: int
    jti: str


def realtime_auth_enabled() -> bool:
    return bool(getattr(settings, "REALTIME_AUTH_ENABLED", False))


def normalize_realtime_origin(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return ""
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    authority = host if port is None or default_port else f"{host}:{port}"
    return f"{scheme}://{authority}"


def configured_realtime_origins() -> set[str]:
    configured = {
        normalize_realtime_origin(origin)
        for origin in getattr(settings, "REALTIME_ALLOWED_ORIGINS", [])
    }
    configured.discard("")
    return configured


def validated_request_origin(request, *, required: bool | None = None) -> str:
    require_origin = (
        bool(getattr(settings, "REALTIME_REQUIRE_ORIGIN", True))
        if required is None
        else bool(required)
    )
    raw_origin = request.headers.get("Origin", "").strip()
    origin = normalize_realtime_origin(raw_origin)
    if raw_origin and not origin:
        raise RealtimeCredentialError(
            "The browser origin is invalid.",
            code="origin_invalid",
            status_code=403,
        )
    if not origin:
        if require_origin:
            raise RealtimeCredentialError(
                "A valid browser origin is required.",
                code="origin_required",
                status_code=403,
            )
        return ""
    allowed = configured_realtime_origins()
    if allowed and origin not in allowed:
        raise RealtimeCredentialError(
            "This origin is not allowed to request realtime access.",
            code="origin_denied",
            status_code=403,
        )
    return origin


def _read_key(*, inline_setting: str, path_setting: str, label: str) -> str:
    inline = str(getattr(settings, inline_setting, "") or "").strip()
    if inline:
        return inline.replace("\\n", "\n")
    raw_path = str(getattr(settings, path_setting, "") or "").strip()
    if not raw_path:
        raise ImproperlyConfigured(f"{path_setting} or {inline_setting} must be configured for {label}.")
    path = Path(raw_path)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ImproperlyConfigured(f"Unable to read {label} from {path}.") from exc
    if not value:
        raise ImproperlyConfigured(f"The configured {label} is empty.")
    return value


@lru_cache(maxsize=1)
def realtime_private_key() -> str:
    return _read_key(
        inline_setting="REALTIME_SIGNING_PRIVATE_KEY",
        path_setting="REALTIME_SIGNING_PRIVATE_KEY_PATH",
        label="realtime signing private key",
    )


@lru_cache(maxsize=1)
def realtime_public_key() -> str:
    return _read_key(
        inline_setting="REALTIME_SIGNING_PUBLIC_KEY",
        path_setting="REALTIME_SIGNING_PUBLIC_KEY_PATH",
        label="realtime signing public key",
    )


def _base_claims(*, audience: str, token_use: str, ttl_seconds: int) -> tuple[dict[str, Any], int, str]:
    now = int(time())
    ttl = max(15, int(ttl_seconds))
    expires_at = now + ttl
    jti = str(uuid4())
    claims = {
        "iss": settings.REALTIME_TOKEN_ISSUER,
        "aud": audience,
        "iat": now,
        "nbf": now - 1,
        "exp": expires_at,
        "jti": jti,
        "token_use": token_use,
        "protocol_version": 1,
    }
    return claims, expires_at, jti


def _encode(claims: Mapping[str, Any]) -> str:
    if not realtime_auth_enabled():
        raise RealtimeCredentialError(
            "Realtime authentication is not enabled.",
            code="realtime_auth_disabled",
            status_code=503,
        )
    algorithm = str(getattr(settings, "REALTIME_TOKEN_ALGORITHM", "RS256") or "RS256")
    if algorithm != "RS256":
        raise ImproperlyConfigured("REALTIME_TOKEN_ALGORITHM must be RS256.")
    return jwt.encode(dict(claims), realtime_private_key(), algorithm=algorithm)


def _audience_dicts(audiences: Iterable[RealtimeAudience]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for audience in audiences:
        key = (audience.kind, str(audience.identifier))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append({"kind": key[0], "id": key[1]})
    return result


def _user_realtime_identity(user) -> dict[str, str]:
    profile = getattr(user, "profile", None)
    display_name = str(
        getattr(profile, "display_name", "")
        or getattr(user, "get_full_name", lambda: "")()
        or getattr(user, "username", "")
        or "User"
    ).strip()
    return {
        "username": str(getattr(user, "username", "") or "")[:150],
        "display_name": display_name[:160],
    }


def issue_user_realtime_ticket(*, user, request, device_id: str = "", device_type: str = "unknown") -> IssuedRealtimeCredential:
    origin = validated_request_origin(request)
    context = get_support_context(user)
    identity = _user_realtime_identity(user)
    scopes = ["messenger"]
    initial_audiences = [user_audience(user.id)]
    support_context: dict[str, Any] | None = None
    if context.account and context.account.has_product_access:
        scopes.append("support_team")
        initial_audiences.append(support_user_audience(user.id))
        support_context = {
            "account_id": str(context.account.id),
            "role": context.role,
            "agent_id": str(context.agent.id) if context.agent else "",
        }

    claims, expires_at, jti = _base_claims(
        audience=settings.REALTIME_TICKET_AUDIENCE,
        token_use="realtime_ticket",
        ttl_seconds=settings.REALTIME_TICKET_TTL_SECONDS,
    )
    auth_claims = request.auth if hasattr(request.auth, "get") else {}
    from apps.chat.services import get_public_presence_snapshot, presence_recipient_ids

    recipient_ids = presence_recipient_ids(user)
    presence_profile = {**identity, **get_public_presence_snapshot(user)}
    cache_user_presence_metadata(
        user_id=user.id,
        recipient_ids=recipient_ids,
        profile=presence_profile,
    )

    claims.update(
        {
            "sub": str(user.id),
            "actor_type": "user",
            "username": identity["username"],
            "display_name": identity["display_name"],
            "scopes": scopes,
            "session_id": str(auth_claims.get("session_id") or ""),
            "device_id": str(device_id or auth_claims.get("device_id") or "")[:128],
            "device_type": str(device_type or "unknown")[:32],
            "origin": origin,
            "initial_audiences": _audience_dicts(initial_audiences),
            "presence_recipient_ids": [str(value) for value in recipient_ids],
            "support": support_context,
        }
    )
    token = _encode(claims)
    return IssuedRealtimeCredential(
        token=token,
        expires_in=expires_at - int(time()),
        expires_at=expires_at,
        jti=jti,
    )


def issue_widget_realtime_ticket(*, session, origin: str) -> IssuedRealtimeCredential:
    from apps.support.models import SupportConversation

    normalized_origin = normalize_realtime_origin(origin)
    if not normalized_origin or normalized_origin != normalize_realtime_origin(session.origin):
        raise RealtimeCredentialError(
            "The visitor session origin is invalid.",
            code="session_origin_mismatch",
            status_code=403,
        )
    claims, expires_at, jti = _base_claims(
        audience=settings.REALTIME_TICKET_AUDIENCE,
        token_use="realtime_ticket",
        ttl_seconds=settings.REALTIME_TICKET_TTL_SECONDS,
    )
    conversation_id = (
        SupportConversation.objects.filter(
            visitor_id=session.visitor_id,
            website_id=session.website_id,
        ).values_list("id", flat=True).first()
    )
    visitor_name = str(getattr(session.visitor, "name", "") or "Website visitor")[:160]
    claims.update(
        {
            "sub": str(session.visitor_id),
            "actor_type": "support_widget",
            "scopes": ["support_widget"],
            "session_id": str(session.id),
            "website_id": str(session.website_id),
            "support_conversation_id": str(conversation_id or ""),
            "display_name": visitor_name,
            "username": "",
            "token_version": int(session.token_version),
            "device_id": f"widget:{session.id}",
            "device_type": "widget",
            "origin": normalized_origin,
            "initial_audiences": _audience_dicts(
                [support_visitor_audience(session.visitor_id)]
            ),
        }
    )
    token = _encode(claims)
    return IssuedRealtimeCredential(
        token=token,
        expires_in=expires_at - int(time()),
        expires_at=expires_at,
        jti=jti,
    )


def normalize_requested_audience(value: Mapping[str, Any]) -> RealtimeAudience:
    kind = str(value.get("kind") or "").strip()
    identifier = str(value.get("id") or "").strip()
    if kind not in {"conversation", "user", "support_website", "support_user"}:
        raise RealtimeCredentialError(
            "This realtime audience type cannot be requested.",
            code="audience_kind_denied",
            status_code=403,
        )
    if not identifier or len(identifier) > 160:
        raise RealtimeCredentialError("The realtime audience is invalid.", code="invalid_audience")
    return RealtimeAudience(kind=kind, identifier=identifier)


def authorize_user_audiences(*, user, requested: Iterable[Mapping[str, Any]]) -> list[RealtimeAudience]:
    audiences = [normalize_requested_audience(value) for value in requested]
    if len(audiences) > settings.REALTIME_MAX_GRANTS_PER_REQUEST:
        raise RealtimeCredentialError(
            "Too many realtime audiences were requested.",
            code="too_many_audiences",
            status_code=400,
        )

    conversation_ids = [a.identifier for a in audiences if a.kind == "conversation"]
    allowed_conversations = {
        str(value)
        for value in ConversationParticipant.objects.filter(
            user=user,
            left_at__isnull=True,
            conversation_id__in=conversation_ids,
            conversation__is_active=True,
        ).values_list("conversation_id", flat=True)
    }

    context = get_support_context(user)
    support_active = bool(context.account and context.account.has_product_access)
    website_ids = [a.identifier for a in audiences if a.kind == "support_website"]
    allowed_websites = (
        {
            str(value)
            for value in visible_websites(context)
            .filter(id__in=website_ids)
            .values_list("id", flat=True)
        }
        if support_active and website_ids
        else set()
    )

    authorized: list[RealtimeAudience] = []
    for audience in audiences:
        permitted = False
        if audience.kind == "conversation":
            permitted = audience.identifier in allowed_conversations
        elif audience.kind == "user":
            permitted = audience.identifier == str(user.id)
        elif audience.kind == "support_user":
            permitted = support_active and audience.identifier == str(user.id)
        elif audience.kind == "support_website":
            permitted = audience.identifier in allowed_websites
        if not permitted:
            raise RealtimeCredentialError(
                "You do not have access to one or more requested realtime audiences.",
                code="audience_access_denied",
                status_code=403,
            )
        authorized.append(audience)
    return authorized


def issue_audience_grant(*, user, audience: RealtimeAudience, request) -> IssuedRealtimeCredential:
    origin = validated_request_origin(request)
    claims, expires_at, jti = _base_claims(
        audience=settings.REALTIME_GRANT_AUDIENCE,
        token_use="realtime_grant",
        ttl_seconds=settings.REALTIME_GRANT_TTL_SECONDS,
    )
    claims.update(
        {
            "sub": str(user.id),
            "actor_type": "user",
            "origin": origin,
            "audience_key": audience.as_dict(),
        }
    )
    token = _encode(claims)
    return IssuedRealtimeCredential(
        token=token,
        expires_in=expires_at - int(time()),
        expires_at=expires_at,
        jti=jti,
    )


def issue_call_grant(*, user, call_id, request) -> tuple[IssuedRealtimeCredential, list[str]]:
    from apps.chat.models import CallSession

    origin = validated_request_origin(request)
    call = (
        CallSession.objects.filter(
            id=call_id,
            participants__user=user,
            status__in=[
                CallSession.Status.INITIATED,
                CallSession.Status.RINGING,
                CallSession.Status.ONGOING,
            ],
            conversation__is_active=True,
        )
        .prefetch_related("participants")
        .distinct()
        .first()
    )
    if call is None:
        raise RealtimeCredentialError(
            "You do not have access to this active call.",
            code="call_access_denied",
            status_code=403,
        )
    participant_ids = sorted({str(participant.user_id) for participant in call.participants.all()})
    if str(user.id) not in participant_ids:
        raise RealtimeCredentialError(
            "You are not a participant in this call.",
            code="call_access_denied",
            status_code=403,
        )
    claims, expires_at, jti = _base_claims(
        audience=settings.REALTIME_CALL_GRANT_AUDIENCE,
        token_use="realtime_call_grant",
        ttl_seconds=settings.REALTIME_CALL_GRANT_TTL_SECONDS,
    )
    claims.update(
        {
            "sub": str(user.id),
            "actor_type": "user",
            "origin": origin,
            "call_id": str(call.id),
            "conversation_id": str(call.conversation_id),
            "participant_ids": participant_ids,
        }
    )
    token = _encode(claims)
    return (
        IssuedRealtimeCredential(
            token=token,
            expires_in=expires_at - int(time()),
            expires_at=expires_at,
            jti=jti,
        ),
        participant_ids,
    )
