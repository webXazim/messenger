from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.chat.models import Message, MessageAttachment, PendingUpload
from apps.chat.services import (
    attach_pending_uploads_to_message,
    dispatch_pending_upload_scan,
    expire_pending_upload_if_needed,
    infer_message_type_from_uploads,
    is_voice_like_upload,
    scan_upload_file,
)
from apps.support.models import SupportConversation, SupportPendingUpload, SupportWidgetSession
from apps.support.services import SupportContext


class SupportMediaError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid_media", status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class SupportUploadOwner:
    source: str
    actor: object | None = None
    session: SupportWidgetSession | None = None


def support_attachment_limit() -> int:
    return max(1, int(getattr(settings, "SUPPORT_MAX_ATTACHMENTS_PER_MESSAGE", 8) or 8))


def support_upload_total_limit() -> int:
    configured = int(getattr(settings, "SUPPORT_MAX_MESSAGE_UPLOAD_BYTES", 0) or 0)
    if configured > 0:
        return configured
    return max(1, int(getattr(settings, "MAX_UPLOAD_BYTES", 25 * 1024 * 1024) or 25 * 1024 * 1024)) * support_attachment_limit()


def support_upload_metadata(*, account_id, website_id, source: str, conversation_id=None, extra=None) -> dict:
    metadata = dict(extra or {})
    metadata.update(
        {
            "product_scope": "support",
            "support_source": source,
            "support_account_id": str(account_id),
            "support_website_id": str(website_id),
        }
    )
    if conversation_id:
        metadata["support_conversation_id"] = str(conversation_id)
    return metadata


@transaction.atomic
def register_support_pending_upload(
    *,
    pending_upload: PendingUpload,
    support_conversation: SupportConversation,
    owner: SupportUploadOwner,
) -> SupportPendingUpload:
    if pending_upload.purpose != PendingUpload.Purpose.SUPPORT:
        raise SupportMediaError("The upload is not scoped to Support Chat.", code="wrong_upload_scope")
    source = owner.source
    if source == SupportPendingUpload.Source.TEAM:
        if not owner.actor or not getattr(owner.actor, "is_authenticated", False):
            raise SupportMediaError("An authenticated Support Chat user is required.", code="authentication_required", status_code=401)
        uploaded_by = owner.actor
        widget_session = None
        visitor = None
    elif source == SupportPendingUpload.Source.VISITOR:
        if not owner.session:
            raise SupportMediaError("A valid website visitor session is required.", code="visitor_session_required", status_code=401)
        uploaded_by = None
        widget_session = owner.session
        visitor = owner.session.visitor
    else:
        raise SupportMediaError("Unknown Support Chat upload source.", code="invalid_upload_source")

    record = SupportPendingUpload.objects.create(
        pending_upload=pending_upload,
        support_account=support_conversation.website.support_account,
        website=support_conversation.website,
        support_conversation=support_conversation,
        source=source,
        uploaded_by=uploaded_by,
        widget_session=widget_session,
        visitor=visitor,
    )
    return record


def dispatch_support_upload_scan(pending_upload: PendingUpload) -> None:
    if getattr(settings, "UPLOAD_SCAN_ASYNC", True):
        dispatch_pending_upload_scan(pending_upload)
    else:
        scan_upload_file(pending_upload)


def _locked_support_uploads(attachment_ids) -> list[SupportPendingUpload]:
    normalized = [str(value) for value in (attachment_ids or [])]
    if len(normalized) > support_attachment_limit():
        raise SupportMediaError(
            f"A message can contain at most {support_attachment_limit()} attachments.",
            code="attachment_limit",
        )
    if len(set(normalized)) != len(normalized):
        raise SupportMediaError("Duplicate upload identifiers are not allowed.", code="duplicate_upload")
    # Lock the Support ownership rows without joining nullable relations.
    # PostgreSQL rejects an unrestricted FOR UPDATE when select_related()
    # introduces the nullable widget_session/visitor outer joins.
    records = list(
        SupportPendingUpload.objects.select_for_update()
        .filter(pending_upload_id__in=normalized)
    )
    if len(records) != len(normalized):
        raise SupportMediaError("One or more uploads are unavailable.", code="upload_unavailable")

    # Lock the shared Messenger upload records separately. This keeps Support
    # on the same attachment finalization primitive as Messenger while making
    # ownership validation and attach status changes one atomic operation.
    pending_uploads = list(
        PendingUpload.objects.select_for_update()
        .filter(id__in=normalized)
    )
    if len(pending_uploads) != len(normalized):
        raise SupportMediaError("One or more uploads are unavailable.", code="upload_unavailable")
    pending_by_id = {str(upload.id): upload for upload in pending_uploads}
    for record in records:
        record._state.fields_cache["pending_upload"] = pending_by_id[str(record.pending_upload_id)]

    by_id = {str(record.pending_upload_id): record for record in records}
    return [by_id[value] for value in normalized]


def _ensure_clean(records: list[SupportPendingUpload]) -> list[PendingUpload]:
    uploads: list[PendingUpload] = []
    total_bytes = 0
    for record in records:
        upload = expire_pending_upload_if_needed(record.pending_upload)
        if upload.status == PendingUpload.UploadStatus.EXPIRED:
            raise SupportMediaError(f"Upload {upload.id} has expired.", code="upload_expired", status_code=410)
        if upload.status != PendingUpload.UploadStatus.PENDING:
            raise SupportMediaError("One or more uploads are already attached or unavailable.", code="upload_unavailable")
        if upload.purpose != PendingUpload.Purpose.SUPPORT:
            raise SupportMediaError("A Messenger upload cannot be attached to Support Chat.", code="wrong_upload_scope", status_code=403)
        if upload.scan_status == PendingUpload.ScanStatus.PENDING:
            upload = scan_upload_file(upload)
        if upload.scan_status != PendingUpload.ScanStatus.CLEAN:
            raise SupportMediaError("Only clean scanned files can be sent.", code="upload_not_clean")
        total_bytes += int(upload.size or 0)
        uploads.append(upload)
    if total_bytes > support_upload_total_limit():
        raise SupportMediaError("The combined attachment size is too large.", code="message_upload_too_large")
    return uploads


def _validate_voice_note(uploads: list[PendingUpload], voice_note: bool) -> None:
    if not voice_note:
        return
    if len(uploads) != 1 or not is_voice_like_upload(uploads[0]):
        raise SupportMediaError("A voice message must contain exactly one audio recording.", code="invalid_voice_message")


@transaction.atomic
def support_uploads_for_team(
    *,
    context: SupportContext,
    actor,
    support_conversation: SupportConversation,
    attachment_ids,
    voice_note: bool = False,
) -> list[PendingUpload]:
    records = _locked_support_uploads(attachment_ids)
    for record in records:
        if record.source != SupportPendingUpload.Source.TEAM or record.uploaded_by_id != actor.id:
            raise SupportMediaError("This upload does not belong to the signed-in Support Chat user.", code="upload_forbidden", status_code=403)
        if record.support_account_id != context.account.id or record.website_id != support_conversation.website_id:
            raise SupportMediaError("This upload belongs to another Support Chat website.", code="upload_forbidden", status_code=403)
        if record.support_conversation_id != support_conversation.id:
            raise SupportMediaError("This upload belongs to another Support Chat conversation.", code="upload_forbidden", status_code=403)
    uploads = _ensure_clean(records)
    _validate_voice_note(uploads, voice_note)
    return uploads


@transaction.atomic
def support_uploads_for_visitor(
    *,
    session: SupportWidgetSession,
    support_conversation: SupportConversation,
    attachment_ids,
    voice_note: bool = False,
) -> list[PendingUpload]:
    records = _locked_support_uploads(attachment_ids)
    for record in records:
        if record.source != SupportPendingUpload.Source.VISITOR:
            raise SupportMediaError("This upload does not belong to a website visitor.", code="upload_forbidden", status_code=403)
        if record.widget_session_id != session.id or record.visitor_id != session.visitor_id:
            raise SupportMediaError("This upload belongs to another visitor session.", code="upload_forbidden", status_code=403)
        if record.website_id != support_conversation.website_id or record.support_conversation_id != support_conversation.id:
            raise SupportMediaError("This upload belongs to another Support Chat conversation.", code="upload_forbidden", status_code=403)
    uploads = _ensure_clean(records)
    _validate_voice_note(uploads, voice_note)
    return uploads


def finalize_support_message_media(
    *,
    message: Message,
    uploads: list[PendingUpload],
    text: str,
    voice_note: bool,
) -> list[MessageAttachment]:
    attachments = attach_pending_uploads_to_message(message=message, uploads=uploads)
    if voice_note:
        message.type = Message.MessageType.AUDIO
        message.metadata = {**dict(message.metadata or {}), "voice_note": True}
        message.save(update_fields=["type", "metadata", "updated_at"])
    elif attachments and not text:
        message.type = infer_message_type_from_uploads(uploads)
        message.save(update_fields=["type", "updated_at"])
    return attachments


def media_summary(message: Message) -> str:
    attachments = list(message.attachments.all())
    if not attachments:
        return message.text or "New support message"
    if (message.metadata or {}).get("voice_note"):
        return "Voice message"
    if len(attachments) > 1:
        return f"{len(attachments)} attachments"
    attachment = attachments[0]
    return {
        MessageAttachment.MediaKind.IMAGE: "Photo",
        MessageAttachment.MediaKind.VIDEO: "Video",
        MessageAttachment.MediaKind.AUDIO: "Audio",
    }.get(attachment.media_kind, Path(attachment.original_name or "File").name or "File")
