from __future__ import annotations

import hashlib
import json
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.support.models import (
    SupportAutomationExecution,
    SupportAutomationRule,
    SupportTag,
    SupportTeam,
    SupportWebhookEndpoint,
)
from apps.support.realtime import publish_support_event
from apps.support.workflow_services import record_audit_event


class SupportAutomationError(Exception):
    pass


MAX_RULES_PER_EVENT = 25
MAX_EXECUTION_SECONDS = 3


def _fingerprint(rule, trigger, conversation, event_key):
    raw = f"{rule.id}:{trigger}:{conversation.id if conversation else '-'}:{event_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _matches(conditions, conversation, context):
    for condition in conditions:
        kind = condition.get("type")
        value = condition.get("value")
        if kind == "website" and str(conversation.website_id) != str(value):
            return False
        if kind == "team" and str(conversation.assigned_team_id or "") != str(value):
            return False
        if kind == "priority" and conversation.priority != value:
            return False
        if kind == "status" and conversation.status != value:
            return False
        if kind == "verified_visitor" and bool(getattr(conversation.visitor, "is_verified", False)) != bool(value):
            return False
        if kind == "assignment_state":
            assigned = conversation.assigned_agent_id is not None
            if (value == "assigned") != assigned:
                return False
        if kind == "tag":
            if not conversation.tag_assignments.filter(tag_id=value).exists():
                return False
        if kind == "business_hours":
            if bool(context.get("inside_business_hours")) != bool(value):
                return False
    return True


@transaction.atomic
def execute_rule(rule, *, trigger, conversation, event_key, context=None, dry_run=False):
    context = context or {}
    idempotency_key = _fingerprint(rule, trigger, conversation, event_key)
    execution, created = SupportAutomationExecution.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "support_account": rule.support_account,
            "rule": rule,
            "support_conversation": conversation,
            "trigger": trigger,
            "context": context,
        },
    )
    if not created:
        return execution

    started = time.monotonic()
    try:
        if not _matches(rule.conditions, conversation, context):
            execution.status = SupportAutomationExecution.Status.SKIPPED
            execution.save(update_fields=["status", "updated_at"])
            return execution

        action_count = 0
        seen_actions = set()
        for action in rule.actions[: rule.execution_limit]:
            if time.monotonic() - started > MAX_EXECUTION_SECONDS:
                raise SupportAutomationError("Automation execution timed out.")
            normalized = json.dumps(action, sort_keys=True)
            if normalized in seen_actions:
                continue
            seen_actions.add(normalized)
            if dry_run:
                action_count += 1
                continue

            kind = action.get("type")
            value = action.get("value")
            if kind == "set_priority":
                conversation.priority = value
                conversation.save(update_fields=["priority", "updated_at"])
            elif kind == "assign_team":
                team = SupportTeam.objects.filter(
                    pk=value,
                    support_account=rule.support_account,
                    is_active=True,
                ).first()
                if team:
                    conversation.assigned_team = team
                    conversation.save(update_fields=["assigned_team", "updated_at"])
            elif kind == "add_tag":
                tag = SupportTag.objects.filter(
                    pk=value,
                    support_account=rule.support_account,
                ).first()
                if tag:
                    conversation.tag_assignments.get_or_create(tag=tag)
            elif kind == "set_follow_up":
                minutes = max(1, min(43200, int(value or 60)))
                conversation.follow_up_at = timezone.now() + timedelta(minutes=minutes)
                conversation.save(update_fields=["follow_up_at", "updated_at"])
            elif kind in {"notify_owner", "notify_agent"}:
                user_ids = []
                if kind == "notify_owner":
                    user_ids = [rule.support_account.owner_id]
                elif conversation.assigned_agent_id:
                    user_ids = [conversation.assigned_agent.user_id]
                publish_support_event(
                    event_name="support.automation.notification",
                    user_ids=user_ids,
                    data={
                        "version": 1,
                        "conversation_id": str(conversation.id),
                        "rule_id": str(rule.id),
                        "message": str(value or rule.name)[:500],
                    },
                )
            elif kind == "trigger_webhook":
                endpoint = SupportWebhookEndpoint.objects.filter(
                    pk=value,
                    support_account=rule.support_account,
                    is_active=True,
                ).first()
                if endpoint:
                    from apps.support.webhooks import queue_webhook_event
                    queue_webhook_event(
                        endpoint=endpoint,
                        event_type="support.automation.triggered",
                        payload={
                            "rule_id": str(rule.id),
                            "conversation_id": str(conversation.id),
                            "trigger": trigger,
                        },
                    )
            elif kind in {"route", "send_response"}:
                # Kept explicit and safe: routing and approved responses are
                # dispatched by their dedicated services, never arbitrary code.
                publish_support_event(
                    event_name="support.automation.action_requested",
                    website_id=conversation.website_id,
                    data={
                        "version": 1,
                        "conversation_id": str(conversation.id),
                        "rule_id": str(rule.id),
                        "action": action,
                    },
                )
            action_count += 1

        execution.actions_executed = action_count
        execution.status = SupportAutomationExecution.Status.SUCCEEDED
        execution.duration_ms = int((time.monotonic() - started) * 1000)
        execution.save(update_fields=["actions_executed", "status", "duration_ms", "updated_at"])
        record_audit_event(
            account=rule.support_account,
            website=conversation.website,
            support_conversation=conversation,
            actor=None,
            action="automation.rule_executed",
            target_type="support_automation_rule",
            target_id=rule.id,
            summary=f'Automation "{rule.name}" executed.',
            metadata={"trigger": trigger, "actions": action_count, "dry_run": dry_run},
        )
    except Exception as exc:
        execution.status = SupportAutomationExecution.Status.FAILED
        execution.error = str(exc)[:1000]
        execution.duration_ms = int((time.monotonic() - started) * 1000)
        execution.save(update_fields=["status", "error", "duration_ms", "updated_at"])
        if not dry_run:
            raise
    return execution


def run_automations(*, account, trigger, conversation, event_key, context=None, dry_run=False):
    results = []
    rules = SupportAutomationRule.objects.filter(
        support_account=account,
        trigger=trigger,
        is_active=True,
    ).order_by("priority", "created_at")[:MAX_RULES_PER_EVENT]
    for rule in rules:
        result = execute_rule(
            rule,
            trigger=trigger,
            conversation=conversation,
            event_key=event_key,
            context=context,
            dry_run=dry_run,
        )
        results.append(result)
        if rule.stop_processing and result.status == SupportAutomationExecution.Status.SUCCEEDED:
            break
    return results
