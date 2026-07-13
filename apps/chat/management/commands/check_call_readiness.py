import json
import re
import socket
from dataclasses import dataclass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


TURN_URI_PATTERN = re.compile(
    r"^(?P<scheme>turns?):(?://)?(?P<host>\[[^\]]+\]|[^:?/]+)(?::(?P<port>\d+))?(?:\?(?P<query>.*))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TurnEndpoint:
    uri: str
    scheme: str
    host: str
    port: int
    transport: str


def parse_turn_uri(uri: str) -> TurnEndpoint:
    match = TURN_URI_PATTERN.match(uri.strip())
    if not match:
        raise ValueError(f"Invalid TURN URI: {uri}")
    scheme = match.group("scheme").lower()
    host = match.group("host").strip("[]")
    port = int(match.group("port") or (5349 if scheme == "turns" else 3478))
    query = match.group("query") or ""
    transport = "tcp" if scheme == "turns" else "udp"
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key.lower() == "transport" and value.lower() in {"udp", "tcp"}:
            transport = value.lower()
    return TurnEndpoint(uri=uri, scheme=scheme, host=host, port=port, transport=transport)


def load_turn_endpoints() -> list[TurnEndpoint]:
    raw = str(getattr(settings, "TURN_URIS_JSON", "") or "").strip()
    if not raw:
        return []
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("TURN_URIS_JSON must be a JSON list.")
    return [parse_turn_uri(str(item)) for item in payload]


class Command(BaseCommand):
    help = "Validate production WebRTC/TURN configuration and optionally probe TURN DNS/TCP reachability."

    def add_arguments(self, parser):
        parser.add_argument("--probe", action="store_true", help="Resolve TURN hosts and probe TCP/TLS endpoints.")
        parser.add_argument("--timeout", type=float, default=3.0, help="Network probe timeout in seconds.")
        parser.add_argument("--fail-on-warning", action="store_true", help="Return a non-zero exit code when warnings exist.")

    def handle(self, *args, **options):
        warnings: list[str] = []
        failures: list[str] = []

        try:
            endpoints = load_turn_endpoints()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError(str(exc)) from exc

        shared_secret = str(getattr(settings, "TURN_SHARED_SECRET", "") or "").strip()
        static_user = str(getattr(settings, "TURN_STATIC_USERNAME", "") or "").strip()
        static_password = str(getattr(settings, "TURN_STATIC_PASSWORD", "") or "").strip()
        ice_policy = str(getattr(settings, "WEBRTC_ICE_TRANSPORT_POLICY", "all") or "all").strip().lower()
        candidate_pool = int(getattr(settings, "WEBRTC_ICE_CANDIDATE_POOL_SIZE", 0) or 0)
        relay_min = int(getattr(settings, "TURN_RELAY_MIN_PORT", 49160) or 49160)
        relay_max = int(getattr(settings, "TURN_RELAY_MAX_PORT", 49200) or 49200)
        realm = str(getattr(settings, "TURN_REALM", "") or "").strip()
        external_ip = str(getattr(settings, "TURN_EXTERNAL_IP", "") or "").strip()

        self.stdout.write(self.style.NOTICE("Messenger calling readiness summary"))
        rows = [
            ("TURN endpoints", len(endpoints)),
            ("TURN realm", realm or "not configured"),
            ("TURN external IP", external_ip or "not configured"),
            ("ICE transport policy", ice_policy),
            ("ICE candidate pool", candidate_pool),
            ("TURN relay range", f"{relay_min}-{relay_max}"),
            ("TURN auth", "shared secret" if shared_secret else "static credentials" if static_user and static_password else "missing"),
        ]
        for key, value in rows:
            self.stdout.write(f"- {key}: {value}")

        if not endpoints:
            failures.append("TURN_URIS_JSON is empty; calls may fail across different networks.")
        else:
            has_udp = any(item.transport == "udp" for item in endpoints)
            has_tcp = any(item.transport == "tcp" for item in endpoints)
            if not has_udp:
                warnings.append("No UDP TURN endpoint is configured; call quality may be reduced.")
            if not has_tcp:
                warnings.append("No TCP/TLS TURN endpoint is configured for restrictive networks.")

        if not shared_secret and not (static_user and static_password):
            failures.append("TURN credentials are not configured.")
        if shared_secret and len(shared_secret) < 32:
            failures.append("TURN_SHARED_SECRET must be at least 32 characters.")
        if ice_policy not in {"all", "relay"}:
            failures.append("WEBRTC_ICE_TRANSPORT_POLICY must be 'all' or 'relay'.")
        if candidate_pool < 0 or candidate_pool > 16:
            warnings.append("WEBRTC_ICE_CANDIDATE_POOL_SIZE should normally be between 0 and 16.")
        if relay_min < 1024 or relay_max > 65535 or relay_min > relay_max:
            failures.append("The configured TURN relay port range is invalid.")
        if relay_max - relay_min + 1 < 20:
            warnings.append("The TURN relay range is narrow; concurrent calls may exhaust available relay ports.")
        if not realm:
            warnings.append("TURN_REALM is not configured.")
        if not external_ip:
            warnings.append("TURN_EXTERNAL_IP/DROPLET_PUBLIC_IP is not configured for NAT relay advertisement.")

        if options["probe"]:
            for endpoint in endpoints:
                try:
                    addresses = socket.getaddrinfo(endpoint.host, endpoint.port, type=socket.SOCK_STREAM if endpoint.transport == "tcp" else socket.SOCK_DGRAM)
                    resolved = sorted({item[4][0] for item in addresses})
                    self.stdout.write(f"- resolve {endpoint.host}: {', '.join(resolved)}")
                except OSError as exc:
                    failures.append(f"Could not resolve {endpoint.host}: {exc}")
                    continue

                if endpoint.transport == "tcp" or endpoint.scheme == "turns":
                    try:
                        with socket.create_connection((endpoint.host, endpoint.port), timeout=options["timeout"]):
                            self.stdout.write(self.style.SUCCESS(f"- TCP probe {endpoint.host}:{endpoint.port}: reachable"))
                    except OSError as exc:
                        failures.append(f"TCP probe failed for {endpoint.host}:{endpoint.port}: {exc}")
                else:
                    self.stdout.write(f"- UDP endpoint {endpoint.host}:{endpoint.port}: DNS resolved; validate relay with a real WebRTC call")

        if warnings:
            self.stdout.write(self.style.WARNING("\nWarnings:"))
            for warning in warnings:
                self.stdout.write(f"- {warning}")
        if failures:
            self.stdout.write(self.style.ERROR("\nFailures:"))
            for failure in failures:
                self.stdout.write(f"- {failure}")
            raise CommandError("Calling readiness check failed.")
        if warnings and options["fail_on_warning"]:
            raise CommandError("Calling readiness check completed with warnings.")

        self.stdout.write(self.style.SUCCESS("\nCalling configuration passed readiness checks."))
