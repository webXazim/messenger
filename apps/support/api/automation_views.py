from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.support.automation_services import run_automations
from apps.support.models import (
    SupportAutomationExecution,
    SupportAutomationRule,
    SupportConversation,
    SupportNotificationSettings,
    SupportSecuritySettings,
)
from apps.support.services import get_support_context
from apps.support.workflow_services import record_audit_event


def _owner_context(request):
    context = get_support_context(request.user)
    if not context.account or context.role != "owner":
        return None, Response(
            {"detail": "Only the Support Chat owner may manage these settings.", "code": "owner_required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return context, None


def _validation_response(exc):
    if hasattr(exc, "message_dict"):
        return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class SupportNotificationSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        context, error = _owner_context(request)
        if error:
            return error
        row, _ = SupportNotificationSettings.objects.get_or_create(
            support_account=context.account
        )
        return Response(model_to_dict(row, exclude=["id", "support_account", "updated_by"]))

    def patch(self, request):
        context, error = _owner_context(request)
        if error:
            return error
        row, _ = SupportNotificationSettings.objects.get_or_create(
            support_account=context.account
        )
        allowed = {
            "new_conversation", "assignment_changed", "sla_due_soon",
            "sla_breached", "internal_mention", "follow_up_due",
            "daily_summary", "daily_summary_hour",
        }
        for key, value in request.data.items():
            if key in allowed:
                setattr(row, key, value)
        row.updated_by = request.user
        try:
            row.full_clean()
        except ValidationError as exc:
            return _validation_response(exc)
        row.save()
        return self.get(request)


class SupportSecuritySettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        context, error = _owner_context(request)
        if error:
            return error
        row, _ = SupportSecuritySettings.objects.get_or_create(
            support_account=context.account
        )
        return Response(model_to_dict(row, exclude=["id", "support_account", "updated_by"]))

    def patch(self, request):
        context, error = _owner_context(request)
        if error:
            return error
        row, _ = SupportSecuritySettings.objects.get_or_create(
            support_account=context.account
        )
        allowed = {
            "require_verified_identity_for_sensitive_actions",
            "block_unverified_attachments",
            "max_attachment_mb",
            "allowed_attachment_extensions",
            "retain_audit_days",
            "webhook_failure_disable_threshold",
            "agent_session_timeout_minutes",
        }
        for key, value in request.data.items():
            if key in allowed:
                setattr(row, key, value)
        row.updated_by = request.user
        try:
            row.full_clean()
        except ValidationError as exc:
            return _validation_response(exc)
        row.save()
        return self.get(request)


def _rule_payload(rule):
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description,
        "trigger": rule.trigger,
        "conditions": rule.conditions,
        "actions": rule.actions,
        "is_active": rule.is_active,
        "priority": rule.priority,
        "stop_processing": rule.stop_processing,
        "execution_limit": rule.execution_limit,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


class SupportAutomationRuleListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        context, error = _owner_context(request)
        if error:
            return error
        rows = SupportAutomationRule.objects.filter(
            support_account=context.account
        ).order_by("priority", "created_at")
        return Response([_rule_payload(row) for row in rows])

    @transaction.atomic
    def post(self, request):
        context, error = _owner_context(request)
        if error:
            return error
        rule = SupportAutomationRule(
            support_account=context.account,
            name=(request.data.get("name") or "").strip(),
            description=(request.data.get("description") or "").strip(),
            trigger=request.data.get("trigger"),
            conditions=request.data.get("conditions") or [],
            actions=request.data.get("actions") or [],
            is_active=bool(request.data.get("is_active", True)),
            priority=int(request.data.get("priority") or 100),
            stop_processing=bool(request.data.get("stop_processing", False)),
            execution_limit=int(request.data.get("execution_limit") or 10),
            created_by=request.user,
            updated_by=request.user,
        )
        try:
            rule.full_clean()
        except ValidationError as exc:
            return _validation_response(exc)
        rule.save()
        record_audit_event(
            account=context.account,
            actor=request.user,
            action="automation.rule_created",
            target_type="support_automation_rule",
            target_id=rule.id,
            summary=f'{request.user.username} created automation "{rule.name}".',
        )
        return Response(_rule_payload(rule), status=status.HTTP_201_CREATED)


class SupportAutomationRuleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, context, rule_id):
        return get_object_or_404(
            SupportAutomationRule,
            pk=rule_id,
            support_account=context.account,
        )

    @transaction.atomic
    def patch(self, request, rule_id):
        context, error = _owner_context(request)
        if error:
            return error
        rule = self._get(context, rule_id)
        allowed = {
            "name", "description", "trigger", "conditions", "actions",
            "is_active", "priority", "stop_processing", "execution_limit",
        }
        for key, value in request.data.items():
            if key in allowed:
                setattr(rule, key, value)
        rule.updated_by = request.user
        try:
            rule.full_clean()
        except ValidationError as exc:
            return _validation_response(exc)
        rule.save()
        return Response(_rule_payload(rule))

    @transaction.atomic
    def delete(self, request, rule_id):
        context, error = _owner_context(request)
        if error:
            return error
        rule = self._get(context, rule_id)
        rule.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportAutomationRuleTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, rule_id):
        context, error = _owner_context(request)
        if error:
            return error
        rule = get_object_or_404(
            SupportAutomationRule,
            pk=rule_id,
            support_account=context.account,
        )
        conversation = get_object_or_404(
            SupportConversation.objects.select_related(
                "website", "visitor", "assigned_agent", "assigned_team"
            ),
            pk=request.data.get("conversation_id"),
            website__support_account=context.account,
        )
        results = run_automations(
            account=context.account,
            trigger=rule.trigger,
            conversation=conversation,
            event_key=f"dry-run:{rule.id}:{conversation.id}",
            context={"inside_business_hours": True},
            dry_run=True,
        )
        selected = next((item for item in results if item.rule_id == rule.id), None)
        return Response({
            "matched": bool(selected and selected.status == selected.Status.SUCCEEDED),
            "actions": selected.actions_executed if selected else 0,
            "status": selected.status if selected else "skipped",
        })


class SupportAutomationExecutionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        context, error = _owner_context(request)
        if error:
            return error
        rows = SupportAutomationExecution.objects.filter(
            support_account=context.account
        ).select_related("rule", "support_conversation")[:100]
        return Response([
            {
                "id": str(row.id),
                "rule": {"id": str(row.rule_id), "name": row.rule.name},
                "conversation_id": str(row.support_conversation_id) if row.support_conversation_id else None,
                "trigger": row.trigger,
                "status": row.status,
                "actions_executed": row.actions_executed,
                "duration_ms": row.duration_ms,
                "error": row.error,
                "created_at": row.created_at,
            }
            for row in rows
        ])
