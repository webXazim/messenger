from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
from datetime import timedelta
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.support.models import SupportWebhookDelivery, SupportWebhookEndpoint


SUPPORTED_WEBHOOK_EVENTS = (
    "webhook.test",
    "conversation.created",
    "conversation.updated",
    "message.created",
    "csat.submitted",
    "visitor.deletion_completed",
    "export.ready",
)


class SupportWebhookError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(36)


def validate_webhook_url(value: str) -> str:
    raw = (value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise SupportWebhookError("Enter a valid webhook URL.", code="invalid_url")
    if parsed.username or parsed.password:
        raise SupportWebhookError("Webhook URLs cannot contain credentials.", code="invalid_url")
    if parsed.scheme != "https" and not settings.DEBUG:
        raise SupportWebhookError("Production webhooks must use HTTPS.", code="https_required")
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise SupportWebhookError("Local network webhook destinations are not allowed.", code="private_destination")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except socket.gaierror as exc:
        raise SupportWebhookError("The webhook host could not be resolved.", code="unresolvable_host") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise SupportWebhookError("Private or local webhook destinations are not allowed.", code="private_destination")
    return raw


def normalize_event_types(values) -> list[str]:
    event_types = list(dict.fromkeys(str(value).strip() for value in (values or []) if str(value).strip()))
    invalid = [value for value in event_types if value not in SUPPORTED_WEBHOOK_EVENTS]
    if invalid:
        raise SupportWebhookError(f"Unsupported webhook event: {invalid[0]}", code="invalid_event")
    if not event_types:
        raise SupportWebhookError("Choose at least one webhook event.", code="event_required")
    return event_types


def queue_support_webhook_event(*, account, event_type: str, payload: dict) -> int:
    if event_type not in SUPPORTED_WEBHOOK_EVENTS:
        return 0

    def _create_deliveries():
        endpoints = list(
            SupportWebhookEndpoint.objects.filter(
                support_account=account,
                is_active=True,
            )
        )
        created = 0
        for endpoint in endpoints:
            if event_type not in (endpoint.event_types or []):
                continue
            delivery = SupportWebhookDelivery.objects.create(
                endpoint=endpoint,
                event_type=event_type,
                payload={
                    "id": None,
                    "type": event_type,
                    "created_at": timezone.now().isoformat(),
                    "data": payload,
                },
            )
            delivery.payload["id"] = str(delivery.event_id)
            delivery.save(update_fields=["payload", "updated_at"])
            created += 1
            try:
                from apps.support.tasks import deliver_support_webhook

                deliver_support_webhook.delay(str(delivery.id))
            except Exception:
                # The durable pending delivery remains available for Celery Beat retry.
                pass
        return created

    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(_create_deliveries)
        return 0
    return _create_deliveries()


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def deliver_webhook(delivery: SupportWebhookDelivery) -> SupportWebhookDelivery:
    delivery = SupportWebhookDelivery.objects.select_related("endpoint").get(pk=delivery.pk)
    endpoint = delivery.endpoint
    if not endpoint.is_active:
        delivery.status = SupportWebhookDelivery.Status.FAILED
        delivery.error = "Webhook endpoint is disabled."
        delivery.save(update_fields=["status", "error", "updated_at"])
        return delivery

    try:
        validate_webhook_url(endpoint.url)
    except SupportWebhookError as exc:
        delivery.status = SupportWebhookDelivery.Status.FAILED
        delivery.error = exc.detail
        delivery.save(update_fields=["status", "error", "updated_at"])
        return delivery

    now = timezone.now()
    delivery.status = SupportWebhookDelivery.Status.PROCESSING
    delivery.attempt_count += 1
    delivery.save(update_fields=["status", "attempt_count", "updated_at"])
    body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(now.timestamp()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CrescentSphere-Support-Webhook/1.0",
        "X-Support-Event": delivery.event_type,
        "X-Support-Delivery": str(delivery.event_id),
        "X-Support-Timestamp": timestamp,
        "X-Support-Signature": _signature(endpoint.signing_secret, timestamp, body),
    }
    try:
        response = requests.post(
            endpoint.url,
            data=body,
            headers=headers,
            timeout=max(2, int(getattr(settings, "SUPPORT_WEBHOOK_TIMEOUT_SECONDS", 10))),
            allow_redirects=False,
        )
        delivery.response_status = response.status_code
        delivery.response_body = (response.text or "")[:1000]
        if 200 <= response.status_code < 300:
            delivery.status = SupportWebhookDelivery.Status.SUCCEEDED
            delivery.error = ""
            delivery.delivered_at = timezone.now()
            endpoint.failure_count = 0
            endpoint.last_delivery_at = delivery.delivered_at
            endpoint.last_success_at = delivery.delivered_at
            endpoint.save(update_fields=["failure_count", "last_delivery_at", "last_success_at", "updated_at"])
            delivery.save(update_fields=[
                "status", "response_status", "response_body", "error", "delivered_at", "updated_at"
            ])
            return delivery
        raise RuntimeError(f"Endpoint returned HTTP {response.status_code}.")
    except Exception as exc:
        delivery.error = str(exc)[:1000]
        endpoint.failure_count += 1
        endpoint.last_delivery_at = timezone.now()
        endpoint.last_failure_at = endpoint.last_delivery_at
        endpoint.save(update_fields=["failure_count", "last_delivery_at", "last_failure_at", "updated_at"])
        max_attempts = max(1, int(getattr(settings, "SUPPORT_WEBHOOK_MAX_ATTEMPTS", 6)))
        if delivery.attempt_count >= max_attempts:
            delivery.status = SupportWebhookDelivery.Status.FAILED
        else:
            delays = [1, 5, 15, 60, 360, 720]
            delay_minutes = delays[min(delivery.attempt_count - 1, len(delays) - 1)]
            delivery.status = SupportWebhookDelivery.Status.PENDING
            delivery.next_attempt_at = timezone.now() + timedelta(minutes=delay_minutes)
        delivery.save(update_fields=[
            "status", "next_attempt_at", "response_status", "response_body", "error", "updated_at"
        ])
        return delivery
