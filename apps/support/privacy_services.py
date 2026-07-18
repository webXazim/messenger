from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.utils import timezone

from apps.chat.models import Message, MessageAttachment
from apps.support.models import (
    SupportAgent,
    SupportAuditEvent,
    SupportCallParticipant,
    SupportCallSession,
    SupportConversation,
    SupportCSATSurvey,
    SupportDataExport,
    SupportKnowledgeArticle,
    SupportMessageAuthor,
    SupportPendingUpload,
    SupportPrivacySettings,
    SupportVisitor,
    SupportVisitorDeletionRequest,
    SupportWidgetSession,
)
from apps.support.webhook_services import queue_support_webhook_event
from apps.support.workflow_services import record_audit_event


class SupportPrivacyError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


def privacy_settings_for(account) -> SupportPrivacySettings:
    settings_obj, _ = SupportPrivacySettings.objects.get_or_create(support_account=account)
    return settings_obj


def _safe_csv_cell(value):
    # Prevent spreadsheet applications from treating visitor-controlled text as formulas.
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def _write_csv(archive: zipfile.ZipFile, name: str, headers: list[str], rows) -> int:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    count = 0
    for row in rows:
        writer.writerow([_safe_csv_cell(value) for value in row])
        count += 1
    archive.writestr(name, buffer.getvalue().encode("utf-8"))
    return count


def _iso(value):
    return value.isoformat() if value else ""


def generate_support_export(export: SupportDataExport) -> SupportDataExport:
    export = SupportDataExport.objects.select_related("support_account").get(pk=export.pk)
    if export.status == SupportDataExport.Status.READY and export.file:
        return export
    export.status = SupportDataExport.Status.PROCESSING
    export.started_at = timezone.now()
    export.error = ""
    export.save(update_fields=["status", "started_at", "error", "updated_at"])
    account = export.support_account
    counts: dict[str, int] = {}
    output = io.BytesIO()
    try:
        conversations = list(
            SupportConversation.objects.filter(website__support_account=account)
            .select_related("website", "visitor", "assigned_agent", "assigned_agent__user", "conversation")
            .order_by("created_at")
        )
        conversation_ids = [item.conversation_id for item in conversations]
        messages = list(
            Message.objects.filter(conversation_id__in=conversation_ids)
            .select_related("sender")
            .prefetch_related("support_author", "attachments")
            .order_by("created_at")
        )
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps({
                    "support_account_id": str(account.id),
                    "generated_at": timezone.now().isoformat(),
                    "scope": "support_only",
                    "includes_personal_messenger": False,
                    "include_attachments": bool(export.include_attachments),
                }, indent=2).encode("utf-8"),
            )
            counts["websites"] = _write_csv(
                archive,
                "websites.csv",
                ["id", "name", "domain", "active", "created_at"],
                ((w.id, w.name, w.domain, w.is_active, _iso(w.created_at)) for w in account.websites.order_by("created_at")),
            )
            counts["agents"] = _write_csv(
                archive,
                "agents.csv",
                ["id", "user_id", "email", "availability", "active", "joined_at"],
                ((a.id, a.user_id, a.user.email, a.availability, a.is_active, _iso(a.joined_at)) for a in SupportAgent.objects.filter(support_account=account).select_related("user").order_by("created_at")),
            )
            counts["visitors"] = _write_csv(
                archive,
                "visitors.csv",
                ["id", "website_id", "external_id", "name", "email", "locale", "first_seen_at", "last_seen_at", "blocked"],
                ((v.id, v.website_id, v.external_id, v.name, v.email, v.locale, _iso(v.first_seen_at), _iso(v.last_seen_at), v.is_blocked) for v in SupportVisitor.objects.filter(website__support_account=account).order_by("created_at")),
            )
            counts["conversations"] = _write_csv(
                archive,
                "conversations.csv",
                ["id", "website_id", "visitor_id", "assigned_agent_id", "status", "priority", "subject", "created_at", "resolved_at", "closed_at"],
                ((c.id, c.website_id, c.visitor_id, c.assigned_agent_id or "", c.status, c.priority, c.subject, _iso(c.created_at), _iso(c.resolved_at), _iso(c.closed_at)) for c in conversations),
            )
            message_rows = []
            for message in messages:
                author_kind = "team" if message.sender_id else "visitor"
                author_id = message.sender_id or ""
                if not message.sender_id:
                    support_author = getattr(message, "support_author", None)
                    author_id = getattr(support_author, "visitor_id", "") or ""
                message_rows.append((
                    message.id, message.conversation_id, author_kind, author_id, message.type,
                    message.text, json.dumps(message.metadata or {}, separators=(",", ":")), _iso(message.created_at),
                ))
            counts["messages"] = _write_csv(
                archive,
                "messages.csv",
                ["id", "conversation_id", "author_kind", "author_id", "type", "text", "metadata", "created_at"],
                message_rows,
            )
            attachment_rows = []
            attachment_total = 0
            max_attachment_bytes = max(0, int(getattr(settings, "SUPPORT_EXPORT_MAX_ATTACHMENT_BYTES", 250 * 1024 * 1024)))
            for message in messages:
                for attachment in message.attachments.all():
                    attachment_rows.append((attachment.id, message.id, attachment.original_name, attachment.mime_type, attachment.size, attachment.scan_status, attachment.file.name))
                    if not export.include_attachments or not attachment.file or attachment_total + int(attachment.size or 0) > max_attachment_bytes:
                        continue
                    try:
                        with attachment.file.open("rb") as handle:
                            content = handle.read()
                        safe_name = Path(attachment.original_name or str(attachment.id)).name
                        archive.writestr(f"attachments/{message.id}/{attachment.id}-{safe_name}", content)
                        attachment_total += len(content)
                    except Exception:
                        continue
            counts["attachments"] = _write_csv(
                archive,
                "attachments.csv",
                ["id", "message_id", "original_name", "mime_type", "size", "scan_status", "storage_key"],
                attachment_rows,
            )
            calls = list(
                SupportCallSession.objects.filter(support_conversation__website__support_account=account)
                .select_related("support_conversation", "initiated_by")
                .order_by("started_at")
            )
            counts["calls"] = _write_csv(
                archive,
                "calls.csv",
                ["id", "conversation_id", "initiated_by_id", "call_type", "status", "started_at", "answered_at", "ended_at", "ended_reason"],
                ((c.id, c.support_conversation_id, c.initiated_by_id, c.call_type, c.status, _iso(c.started_at), _iso(c.answered_at), _iso(c.ended_at), c.ended_reason) for c in calls),
            )
            call_ids = [item.id for item in calls]
            counts["call_participants"] = _write_csv(
                archive,
                "call_participants.csv",
                ["id", "call_id", "kind", "user_id", "visitor_id", "state", "audio_enabled", "video_enabled", "joined_at", "left_at"],
                ((p.id, p.call_id, p.kind, p.user_id or "", p.visitor_id or "", p.state, p.audio_enabled, p.video_enabled, _iso(p.joined_at), _iso(p.left_at)) for p in SupportCallParticipant.objects.filter(call_id__in=call_ids).order_by("created_at")),
            )
            counts["csat"] = _write_csv(
                archive,
                "csat.csv",
                ["id", "conversation_id", "website_id", "status", "rating", "comment", "requested_at", "submitted_at"],
                ((s.id, s.support_conversation_id, s.website_id, s.status, s.rating or "", s.comment, _iso(s.requested_at), _iso(s.submitted_at)) for s in SupportCSATSurvey.objects.filter(support_account=account).order_by("requested_at")),
            )
            counts["knowledge_articles"] = _write_csv(
                archive,
                "knowledge_articles.csv",
                ["id", "title", "slug", "status", "summary", "body", "published_at", "updated_at"],
                ((a.id, a.title, a.slug, a.status, a.summary, a.body, _iso(a.published_at), _iso(a.updated_at)) for a in SupportKnowledgeArticle.objects.filter(support_account=account).order_by("created_at")),
            )
            counts["audit_events"] = _write_csv(
                archive,
                "audit_events.csv",
                ["id", "action", "summary", "actor_id", "website_id", "conversation_id", "metadata", "created_at"],
                ((e.id, e.action, e.summary, e.actor_id or "", e.website_id or "", e.support_conversation_id or "", json.dumps(e.metadata or {}, separators=(",", ":")), _iso(e.created_at)) for e in SupportAuditEvent.objects.filter(support_account=account).order_by("created_at")),
            )
        output.seek(0)
        filename = f"support-export-{account.id}-{timezone.now().strftime('%Y%m%d-%H%M%S')}.zip"
        export.file.save(filename, ContentFile(output.read()), save=False)
        export.file_size = export.file.size
        export.record_counts = counts
        export.status = SupportDataExport.Status.READY
        export.completed_at = timezone.now()
        export.save(update_fields=["file", "file_size", "record_counts", "status", "completed_at", "updated_at"])
        queue_support_webhook_event(
            account=account,
            event_type="export.ready",
            payload={"export_id": str(export.id), "expires_at": export.expires_at.isoformat(), "record_counts": counts},
        )
        return export
    except Exception as exc:
        export.status = SupportDataExport.Status.FAILED
        export.error = str(exc)[:1000]
        export.save(update_fields=["status", "error", "updated_at"])
        return export


def create_support_export(*, account, actor, include_attachments: bool | None = None) -> SupportDataExport:
    settings_obj = privacy_settings_for(account)
    include = settings_obj.include_attachments_in_exports if include_attachments is None else bool(include_attachments)
    export = SupportDataExport.objects.create(
        support_account=account,
        requested_by=actor,
        include_attachments=include,
        expires_at=timezone.now() + timedelta(days=settings_obj.export_retention_days),
    )
    record_audit_event(
        account=account,
        actor=actor,
        action="data_export.requested",
        target_type="support_data_export",
        target_id=export.id,
        summary="Support data export requested.",
        metadata={"include_attachments": include},
    )
    return export


def _delete_attachment_files(attachment: MessageAttachment) -> None:
    for field_name in ("file", "thumbnail"):
        field = getattr(attachment, field_name, None)
        if field:
            try:
                field.delete(save=False)
            except Exception:
                pass


def erase_support_conversation(support_conversation: SupportConversation) -> None:
    chat_conversation = support_conversation.conversation
    for attachment in MessageAttachment.objects.filter(message__conversation=chat_conversation):
        _delete_attachment_files(attachment)
    chat_conversation.delete()


def _delete_visitor_pending_uploads(visitor: SupportVisitor) -> None:
    for support_upload in SupportPendingUpload.objects.filter(visitor=visitor).select_related("pending_upload"):
        pending = support_upload.pending_upload
        for field_name in ("file", "thumbnail"):
            field = getattr(pending, field_name, None)
            if field:
                try:
                    field.delete(save=False)
                except Exception:
                    pass
        pending.delete()


def process_visitor_deletion(deletion: SupportVisitorDeletionRequest) -> SupportVisitorDeletionRequest:
    deletion = SupportVisitorDeletionRequest.objects.select_related("support_account", "website", "visitor").get(pk=deletion.pk)
    if deletion.status == SupportVisitorDeletionRequest.Status.COMPLETED:
        return deletion
    deletion.status = SupportVisitorDeletionRequest.Status.PROCESSING
    deletion.error = ""
    deletion.save(update_fields=["status", "error", "updated_at"])
    try:
        visitor = deletion.visitor
        if visitor:
            _delete_visitor_pending_uploads(visitor)
            conversations = list(SupportConversation.objects.filter(visitor=visitor).select_related("conversation"))
            for conversation in conversations:
                erase_support_conversation(conversation)
            SupportWidgetSession.objects.filter(visitor=visitor).delete()
            visitor.delete()
        deletion.visitor = None
        deletion.status = SupportVisitorDeletionRequest.Status.COMPLETED
        deletion.completed_at = timezone.now()
        deletion.save(update_fields=["visitor", "status", "completed_at", "updated_at"])
        record_audit_event(
            account=deletion.support_account,
            website=deletion.website,
            actor=deletion.requested_by,
            action="visitor.data_deleted",
            target_type="support_visitor",
            target_id=deletion.visitor_external_id,
            summary="Website visitor data was deleted.",
            metadata={"source": deletion.source},
        )
        queue_support_webhook_event(
            account=deletion.support_account,
            event_type="visitor.deletion_completed",
            payload={"website_id": str(deletion.website_id), "visitor_external_id": str(deletion.visitor_external_id)},
        )
        return deletion
    except Exception as exc:
        deletion.status = SupportVisitorDeletionRequest.Status.FAILED
        deletion.error = str(exc)[:1000]
        deletion.save(update_fields=["status", "error", "updated_at"])
        return deletion


def request_visitor_deletion(*, visitor: SupportVisitor, source: str, requested_by=None) -> SupportVisitorDeletionRequest:
    account = visitor.website.support_account
    settings_obj = privacy_settings_for(account)
    if source == SupportVisitorDeletionRequest.Source.VISITOR and not settings_obj.allow_visitor_deletion_requests:
        raise SupportPrivacyError("Visitor deletion requests are not enabled.", code="deletion_disabled", status_code=403)
    with transaction.atomic():
        request_obj = (
            SupportVisitorDeletionRequest.objects.select_for_update()
            .filter(
                website=visitor.website,
                visitor_external_id=visitor.external_id,
                status__in=[
                    SupportVisitorDeletionRequest.Status.PENDING,
                    SupportVisitorDeletionRequest.Status.PROCESSING,
                ],
            )
            .first()
        )
        if request_obj:
            return request_obj
        request_obj = SupportVisitorDeletionRequest.objects.create(
            support_account=account,
            website=visitor.website,
            visitor=visitor,
            visitor_external_id=visitor.external_id,
            source=source,
            requested_by=requested_by,
        )
    try:
        from apps.support.tasks import process_support_visitor_deletion

        process_support_visitor_deletion.delay(str(request_obj.id))
    except Exception:
        pass
    return request_obj


def run_support_retention() -> dict[str, int]:
    now = timezone.now()
    deleted_conversations = 0
    deleted_sessions = 0
    expired_exports = 0
    for settings_obj in SupportPrivacySettings.objects.select_related("support_account").all():
        session_cutoff = now - timedelta(days=settings_obj.widget_session_retention_days)
        deleted_sessions += SupportWidgetSession.objects.filter(
            website__support_account=settings_obj.support_account,
            status__in=[SupportWidgetSession.Status.CLOSED, SupportWidgetSession.Status.REVOKED, SupportWidgetSession.Status.EXPIRED],
            last_seen_at__lt=session_cutoff,
        ).delete()[0]
        if settings_obj.retention_enabled:
            cutoff = now - timedelta(days=settings_obj.resolved_conversation_retention_days)
            conversations = list(
                SupportConversation.objects.filter(
                    website__support_account=settings_obj.support_account,
                    status__in=[SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED],
                ).filter(
                    models.Q(closed_at__lt=cutoff) | models.Q(closed_at__isnull=True, resolved_at__lt=cutoff)
                ).select_related("conversation", "visitor", "website")
            )
            for conversation in conversations:
                visitor = conversation.visitor
                _delete_visitor_pending_uploads(visitor)
                erase_support_conversation(conversation)
                SupportWidgetSession.objects.filter(visitor=visitor).delete()
                visitor.delete()
                deleted_conversations += 1
    for export in SupportDataExport.objects.filter(expires_at__lte=now).exclude(status=SupportDataExport.Status.EXPIRED):
        if export.file:
            try:
                export.file.delete(save=False)
            except Exception:
                pass
        export.status = SupportDataExport.Status.EXPIRED
        export.save(update_fields=["status", "updated_at"])
        expired_exports += 1
    return {"conversations": deleted_conversations, "sessions": deleted_sessions, "exports": expired_exports}
