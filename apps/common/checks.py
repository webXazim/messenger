from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from apps.common.realtime_auth import configured_realtime_origins, realtime_private_key, realtime_public_key


@register(Tags.security, deploy=True)
def check_realtime_auth_configuration(app_configs, **kwargs):
    if not getattr(settings, "REALTIME_AUTH_ENABLED", False):
        return []

    issues = []
    try:
        private_key_text = realtime_private_key()
        private_key = serialization.load_pem_private_key(
            private_key_text.encode("utf-8"), password=None
        )
    except Exception as exc:
        issues.append(
            Error(
                "The realtime signing private key cannot be loaded.",
                hint=str(exc),
                id="realtime.E001",
            )
        )
        private_key = None

    try:
        public_key_text = realtime_public_key()
        public_key = serialization.load_pem_public_key(public_key_text.encode("utf-8"))
    except Exception as exc:
        issues.append(
            Error(
                "The realtime signing public key cannot be loaded.",
                hint=str(exc),
                id="realtime.E002",
            )
        )
        public_key = None

    if private_key is not None and public_key is not None:
        derived_public = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        configured_public = public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if derived_public != configured_public:
            issues.append(
                Error(
                    "The realtime signing private and public keys are not a matching pair.",
                    id="realtime.E003",
                )
            )

    origins = configured_realtime_origins()
    if getattr(settings, "REALTIME_REQUIRE_ORIGIN", True) and not origins:
        issues.append(
            Error(
                "Realtime Origin enforcement is enabled but no valid allowed origins are configured.",
                id="realtime.E004",
            )
        )
    if not settings.DEBUG:
        insecure = sorted(origin for origin in origins if origin.startswith("http://"))
        if insecure:
            issues.append(
                Warning(
                    "Realtime production origins should use HTTPS.",
                    hint=", ".join(insecure),
                    id="realtime.W001",
                )
            )

    for setting_name in (
        "REALTIME_SIGNING_PRIVATE_KEY_PATH",
        "REALTIME_SIGNING_PUBLIC_KEY_PATH",
    ):
        inline_name = setting_name.replace("_PATH", "")
        if str(getattr(settings, inline_name, "") or "").strip():
            continue
        raw_path = str(getattr(settings, setting_name, "") or "").strip()
        if raw_path and not Path(raw_path).is_file():
            issues.append(
                Error(
                    f"{setting_name} does not point to a readable file.",
                    hint=raw_path,
                    id="realtime.E005",
                )
            )
    return issues
