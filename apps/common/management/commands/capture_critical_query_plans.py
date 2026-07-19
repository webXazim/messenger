from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta, timezone as datetime_timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from apps.chat.models import ConversationParticipant
from apps.chat.selectors import conversation_messages_qs, user_conversations_qs
from apps.common.models import RealtimeOutboxEvent
from apps.support.conversation_services import (
    support_conversations_for_context,
    with_support_inbox_metrics,
)
from apps.support.models import SupportWebhookDelivery
from apps.support.services import get_support_context


UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def _redact_plan(value):
    if isinstance(value, dict):
        return {key: _redact_plan(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_plan(item) for item in value]
    if isinstance(value, str):
        value = UUID_RE.sub("<uuid>", value)
        return EMAIL_RE.sub("<email>", value)
    return value


QUERY_LIMITS_MS = {
    "messenger_conversation_list": 150.0,
    "messenger_message_page": 120.0,
    "support_inbox": 180.0,
    "realtime_outbox_claim": 60.0,
    "support_webhook_claim": 60.0,
}


def _walk_plan(node):
    yield node
    for child in node.get("Plans", []) or []:
        yield from _walk_plan(child)


def _plan_shape(node):
    shape = {
        "node_type": node.get("Node Type"),
        "relation": node.get("Relation Name"),
        "index": node.get("Index Name"),
        "join_type": node.get("Join Type"),
    }
    children = [_plan_shape(child) for child in node.get("Plans", []) or []]
    if children:
        shape["plans"] = children
    return {key: value for key, value in shape.items() if value not in (None, "", [])}


def _write_json(path: str, payload: dict) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output


class Command(BaseCommand):
    help = "Capture JSON PostgreSQL plans for critical read/claim queries without writing SQL text to disk."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Messenger/Support username or email used for scoped plans.")
        parser.add_argument("--conversation-id", help="Optional accessible Messenger conversation UUID.")
        parser.add_argument("--limit", type=int, default=50, help="Representative page/batch size (1-200).")
        parser.add_argument(
            "--analyze",
            action="store_true",
            help="Execute the SELECT queries and include actual timing/buffer data. Use only in a controlled window.",
        )
        parser.add_argument("--output", help="Write a machine-readable JSON report.")
        parser.add_argument("--json", action="store_true", help="Print the JSON report.")
        parser.add_argument("--strict", action="store_true", help="Fail for warnings as well as hard errors.")
        parser.add_argument(
            "--large-table-rows",
            type=int,
            default=10_000,
            help="Warn when a sequential scan touches a relation estimated above this size.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Critical plan capture must run against PostgreSQL.")

        identity = options["user"].strip()
        User = get_user_model()
        user = User.objects.filter(Q(username__iexact=identity) | Q(email__iexact=identity)).first()
        if not user:
            raise CommandError("No user matched --user.")

        limit = min(200, max(1, int(options["limit"])))
        analyze = bool(options["analyze"])
        conversation_id = options.get("conversation_id")
        if conversation_id:
            participant = ConversationParticipant.objects.filter(
                user=user,
                conversation_id=conversation_id,
                left_at__isnull=True,
                conversation__is_active=True,
            ).select_related("conversation").first()
            if not participant:
                raise CommandError("The selected user cannot access --conversation-id.")
        else:
            participant = ConversationParticipant.objects.filter(
                user=user,
                left_at__isnull=True,
                conversation__is_active=True,
            ).select_related("conversation").order_by("-conversation__last_message_at", "-conversation__created_at").first()

        querysets = {
            "messenger_conversation_list": (
                user_conversations_qs(user, lightweight=True)
                .order_by("-last_message_at", "-created_at", "-id")
                .values("id", "last_message_at", "created_at", "unread_count")[:limit]
            ),
            "realtime_outbox_claim": (
                RealtimeOutboxEvent.objects.filter(
                    status=RealtimeOutboxEvent.Status.PENDING,
                    available_at__lte=timezone.now(),
                )
                .order_by("available_at", "created_at", "id")
                .values("id", "event_id", "status", "available_at")[: min(100, limit)]
            ),
            "support_webhook_claim": (
                SupportWebhookDelivery.objects.filter(
                    Q(
                        status=SupportWebhookDelivery.Status.PENDING,
                        next_attempt_at__lte=timezone.now(),
                    )
                    | Q(
                        status=SupportWebhookDelivery.Status.PROCESSING,
                        updated_at__lte=timezone.now() - timedelta(seconds=120),
                    )
                )
                .order_by("next_attempt_at", "id")
                .values("id", "status", "next_attempt_at")[: min(100, limit)]
            ),
        }
        if participant:
            querysets["messenger_message_page"] = (
                conversation_messages_qs(user, participant.conversation_id)
                .order_by("-created_at", "-id")
                .values("id", "conversation_id", "sender_id", "created_at")[:limit]
            )

        support_context = get_support_context(user)
        if support_context.account:
            querysets["support_inbox"] = (
                with_support_inbox_metrics(
                    support_conversations_for_context(support_context),
                    user,
                )
                .order_by("-conversation__last_message_at", "-created_at", "-id")
                .values(
                    "id",
                    "website_id",
                    "conversation_id",
                    "prefetched_team_unread_count",
                    "prefetched_visitor_unread_count",
                )[:limit]
            )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, GREATEST(reltuples::bigint, 0) FROM pg_class "
                "WHERE relkind IN ('r', 'p') AND relnamespace = current_schema()::regnamespace"
            )
            relation_rows = {name: int(rows or 0) for name, rows in cursor.fetchall()}

        plans = []
        all_errors: list[str] = []
        all_warnings: list[str] = []
        explain_options = {
            "format": "json",
            "analyze": analyze,
            "verbose": True,
            "costs": True,
            "settings": True,
        }
        if analyze:
            explain_options.update({"buffers": True, "timing": True, "summary": True})

        for name, queryset in querysets.items():
            raw = queryset.explain(**explain_options)
            document = json.loads(raw)
            root_document = document[0]
            root = root_document["Plan"]
            nodes = list(_walk_plan(root))
            errors: list[str] = []
            warnings: list[str] = []
            sequential_scans = []
            disk_sorts = []

            for node in nodes:
                if node.get("Node Type") == "Seq Scan":
                    relation = node.get("Relation Name") or ""
                    estimate = relation_rows.get(relation, 0)
                    scan = {
                        "relation": relation,
                        "estimated_relation_rows": estimate,
                        "plan_rows": int(node.get("Plan Rows") or 0),
                        "actual_rows": int(node.get("Actual Rows") or 0) if analyze else None,
                    }
                    sequential_scans.append(scan)
                    if estimate >= max(1, int(options["large_table_rows"])):
                        warnings.append(
                            f"Sequential scan on {relation} with approximately {estimate} rows."
                        )
                if node.get("Sort Space Type") == "Disk" or node.get("Sort Method", "").lower().startswith("external"):
                    disk_sorts.append(
                        {
                            "node_type": node.get("Node Type"),
                            "sort_method": node.get("Sort Method", ""),
                            "sort_space_kb": int(node.get("Sort Space Used") or 0),
                        }
                    )

                removed = int(node.get("Rows Removed by Filter") or 0)
                actual_rows = int(node.get("Actual Rows") or 0)
                if analyze and removed >= 10_000 and removed > max(100, actual_rows * 20):
                    warnings.append(
                        f"{node.get('Node Type')} removed {removed} rows to return {actual_rows}."
                    )

            if disk_sorts:
                errors.append("The analyzed plan spilled a sort to disk.")

            execution_ms = float(root_document.get("Execution Time") or 0.0)
            planning_ms = float(root_document.get("Planning Time") or 0.0)
            threshold_ms = QUERY_LIMITS_MS.get(name, 150.0)
            if analyze and execution_ms > threshold_ms:
                errors.append(
                    f"Execution time {execution_ms:.2f}ms exceeded the {threshold_ms:.0f}ms diagnostic threshold."
                )

            shape = _plan_shape(root)
            fingerprint = hashlib.sha256(
                json.dumps(shape, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            sanitized_document = _redact_plan(document)
            result = {
                "name": name,
                "analyzed": analyze,
                "passed": not errors,
                "strict_passed": not errors and not warnings,
                "errors": errors,
                "warnings": warnings,
                "plan_fingerprint": fingerprint,
                "root_node_type": root.get("Node Type"),
                "estimated_rows": int(root.get("Plan Rows") or 0),
                "total_cost": float(root.get("Total Cost") or 0.0),
                "planning_time_ms": planning_ms if analyze else None,
                "execution_time_ms": execution_ms if analyze else None,
                "diagnostic_threshold_ms": threshold_ms,
                "sequential_scans": sequential_scans,
                "disk_sorts": disk_sorts,
                "plan": sanitized_document,
            }
            plans.append(result)
            all_errors.extend(f"{name}: {message}" for message in errors)
            all_warnings.extend(f"{name}: {message}" for message in warnings)

        skipped = []
        if not participant:
            skipped.append("messenger_message_page: the selected user has no active conversation")
        if not support_context.account:
            skipped.append("support_inbox: the selected user has no Support Chat context")

        payload = {
            "schema_version": 1,
            "generated_at": timezone.now().astimezone(datetime_timezone.utc).isoformat(),
            "database_version": connection.pg_version,
            "user_id": str(user.id),
            "representative_conversation_id": str(participant.conversation_id) if participant else "",
            "limit": limit,
            "analyzed": analyze,
            "passed": not all_errors,
            "strict_passed": not all_errors and not all_warnings,
            "errors": all_errors,
            "warnings": all_warnings,
            "skipped": skipped,
            "plans": plans,
        }

        if options.get("output"):
            output = _write_json(options["output"], payload)
            self.stderr.write(f"Critical PostgreSQL plans written to {output}")

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        else:
            style = self.style.SUCCESS if payload["passed"] else self.style.ERROR
            self.stdout.write(style("PASS" if payload["passed"] else "FAIL"))
            for plan in plans:
                self.stdout.write(
                    f"{plan['name']}: root={plan['root_node_type']} cost={plan['total_cost']:.2f} "
                    f"execution_ms={plan['execution_time_ms'] if analyze else 'not-run'}"
                )
            for message in all_errors:
                self.stdout.write(self.style.ERROR(f"ERROR: {message}"))
            for message in all_warnings:
                self.stdout.write(self.style.WARNING(f"WARNING: {message}"))
            for message in skipped:
                self.stdout.write(self.style.WARNING(f"SKIPPED: {message}"))

        if all_errors or (options["strict"] and all_warnings):
            raise CommandError("Critical PostgreSQL plan review did not pass.")
