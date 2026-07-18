from __future__ import annotations

from datetime import timedelta
import json
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.chat.services import get_turn_credentials
from apps.support.conversation_services import get_context_conversation
from apps.support.models import (
    SupportCallParticipant,
    SupportCallSession,
    SupportCallSettings,
    SupportCallSignal,
    SupportConversation,
    SupportWidgetSession,
)
from apps.support.realtime import publish_support_event
from apps.support.workflow_services import record_audit_event


ACTIVE_CALL_STATUSES = (SupportCallSession.Status.RINGING, SupportCallSession.Status.ONGOING)
TERMINAL_CALL_STATUSES = (
    SupportCallSession.Status.DECLINED,
    SupportCallSession.Status.MISSED,
    SupportCallSession.Status.ENDED,
    SupportCallSession.Status.FAILED,
)
ALLOWED_SIGNAL_TYPES = {
    "offer",
    "answer",
    "ice_candidate",
    "renegotiate",
    "ice_restart",
    "hangup",
    "media_toggle",
    "network_state",
}


class SupportCallError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def support_calls_enabled() -> bool:
    return bool(getattr(settings, "SUPPORT_CALLS_ENABLED", False))


def call_settings_for(account):
    settings_obj, _ = SupportCallSettings.objects.get_or_create(support_account=account)
    return settings_obj


def _ensure_call_feature(account, website, call_type: str):
    if not support_calls_enabled():
        raise SupportCallError("calls_disabled", "Support calls are not enabled on this deployment.", 403)
    settings_obj = call_settings_for(account)
    widget_settings = website.widget_settings
    if not settings_obj.enabled:
        raise SupportCallError("calls_disabled", "Support calls are disabled for this account.", 403)
    if call_type == SupportCallSession.CallType.VOICE and not widget_settings.allow_audio_calls:
        raise SupportCallError("voice_disabled", "Audio calls are disabled for this website.", 403)
    if call_type == SupportCallSession.CallType.VIDEO and (
        not settings_obj.allow_video or not widget_settings.allow_video_calls
    ):
        raise SupportCallError("video_disabled", "Video calls are disabled for this website.", 403)
    return settings_obj


def _expire_if_stale(call: SupportCallSession) -> bool:
    if call.status != SupportCallSession.Status.RINGING:
        return False
    timeout = max(15, int(getattr(settings, "SUPPORT_CALL_RING_TIMEOUT_SECONDS", 45) or 45))
    if call.started_at >= timezone.now() - timedelta(seconds=timeout):
        return False
    end_support_call(call=call, actor_user=None, actor_visitor=None, reason="missed", forced_status=SupportCallSession.Status.MISSED)
    return True


def _active_user_call(user):
    return SupportCallSession.objects.filter(
        initiated_by=user,
        status__in=ACTIVE_CALL_STATUSES,
    ).first()


@transaction.atomic
def start_support_call(*, context, actor, conversation_id, call_type: str) -> SupportCallSession:
    if call_type not in SupportCallSession.CallType.values:
        raise SupportCallError("invalid_call_type", "Choose an audio or video call.")
    conversation = get_context_conversation(context, conversation_id)
    if conversation.status == SupportConversation.Status.CLOSED:
        raise SupportCallError("conversation_closed", "This support conversation is closed.", 409)
    _ensure_call_feature(context.account, conversation.website, call_type)
    existing = SupportCallSession.objects.select_for_update().filter(
        support_conversation=conversation,
        status__in=ACTIVE_CALL_STATUSES,
    ).first()
    if existing:
        _expire_if_stale(existing)
        existing.refresh_from_db()
        if existing.status in ACTIVE_CALL_STATUSES:
            raise SupportCallError("active_call_exists", "This conversation already has an active call.", 409)
    user_call = _active_user_call(actor)
    if user_call:
        _expire_if_stale(user_call)
        user_call.refresh_from_db()
        if user_call.status in ACTIVE_CALL_STATUSES:
            raise SupportCallError("active_call_exists", "Finish your current support call first.", 409)
    try:
        call = SupportCallSession.objects.create(
            support_conversation=conversation,
            initiated_by=actor,
            call_type=call_type,
            status=SupportCallSession.Status.RINGING,
            room_key=uuid4().hex,
            metadata={"product_scope": "support", "website_id": str(conversation.website_id)},
        )
    except IntegrityError as exc:
        raise SupportCallError("active_call_exists", "This conversation already has an active call.", 409) from exc
    now = timezone.now()
    SupportCallParticipant.objects.bulk_create([
        SupportCallParticipant(
            call=call,
            kind=SupportCallParticipant.Kind.TEAM,
            user=actor,
            state=SupportCallParticipant.State.JOINED,
            joined_at=now,
            video_enabled=call_type == SupportCallSession.CallType.VIDEO,
        ),
        SupportCallParticipant(
            call=call,
            kind=SupportCallParticipant.Kind.VISITOR,
            visitor=conversation.visitor,
            state=SupportCallParticipant.State.RINGING,
            video_enabled=call_type == SupportCallSession.CallType.VIDEO,
        ),
    ])
    record_audit_event(
        account=context.account,
        actor=actor,
        action="call.started",
        target_type="support_call",
        target_id=call.id,
        summary=f"{actor.username} started a {call_type} support call.",
        metadata={"conversation_id": str(conversation.id), "website_id": str(conversation.website_id)},
    )
    publish_support_event(
        event_name="support.call.ringing",
        website_id=conversation.website_id,
        visitor_id=conversation.visitor_id,
        data=call_event_payload(call),
    )
    return call



def team_active_call_for_conversation(context, conversation_id, user) -> SupportCallSession | None:
    conversation = get_context_conversation(context, conversation_id)
    call = (
        SupportCallSession.objects.select_related(
            "support_conversation",
            "support_conversation__website",
            "support_conversation__visitor",
            "initiated_by",
            "initiated_by__profile",
        )
        .prefetch_related("participants")
        .filter(
            support_conversation=conversation,
            initiated_by=user,
            status__in=ACTIVE_CALL_STATUSES,
        )
        .order_by("-started_at")
        .first()
    )
    if not call:
        return None
    if _expire_if_stale(call):
        return None
    call.refresh_from_db()
    return call if call.status in ACTIVE_CALL_STATUSES else None

def team_call_for_context(context, call_id, user) -> SupportCallSession:
    call = (
        SupportCallSession.objects.select_related(
            "support_conversation",
            "support_conversation__website",
            "support_conversation__visitor",
            "initiated_by",
            "initiated_by__profile",
        )
        .prefetch_related("participants")
        .filter(pk=call_id)
        .first()
    )
    if not call:
        raise SupportCallError("not_found", "Support call not found.", 404)
    # Reuse the exact Support conversation permission boundary, then require the
    # actual call participant so another agent cannot consume signaling or end it.
    get_context_conversation(context, call.support_conversation_id)
    if call.initiated_by_id != user.id:
        raise SupportCallError("not_found", "Support call not found.", 404)
    _expire_if_stale(call)
    call.refresh_from_db()
    return call


def team_active_call(context, user) -> SupportCallSession | None:
    call = (
        SupportCallSession.objects.filter(
            initiated_by=user,
            support_conversation__website__support_account=context.account,
            status__in=ACTIVE_CALL_STATUSES,
        )
        .order_by("-started_at")
        .first()
    )
    if not call:
        return None
    return team_call_for_context(context, call.id, user)


def visitor_call_for_session(session: SupportWidgetSession, call_id=None) -> SupportCallSession:
    queryset = (
        SupportCallSession.objects.select_related(
            "support_conversation",
            "support_conversation__website",
            "support_conversation__visitor",
            "initiated_by",
            "initiated_by__profile",
        )
        .prefetch_related("participants")
        .filter(
            support_conversation__website=session.website,
            support_conversation__visitor=session.visitor,
        )
    )
    if call_id:
        queryset = queryset.filter(pk=call_id)
    else:
        queryset = queryset.filter(status__in=ACTIVE_CALL_STATUSES).order_by("-started_at")
    call = queryset.first()
    if not call:
        raise SupportCallError("not_found", "Support call not found.", 404)
    _expire_if_stale(call)
    call.refresh_from_db()
    return call


@transaction.atomic
def accept_support_call(*, call: SupportCallSession, visitor) -> SupportCallSession:
    call = SupportCallSession.objects.select_for_update().get(pk=call.pk)
    if _expire_if_stale(call):
        raise SupportCallError("call_expired", "This call has expired.", 410)
    if call.status not in ACTIVE_CALL_STATUSES:
        raise SupportCallError("call_closed", "This call can no longer be accepted.", 409)
    participant = SupportCallParticipant.objects.select_for_update().get(
        call=call,
        kind=SupportCallParticipant.Kind.VISITOR,
        visitor=visitor,
    )
    now = timezone.now()
    participant.state = SupportCallParticipant.State.JOINED
    participant.joined_at = participant.joined_at or now
    participant.left_at = None
    participant.last_seen_at = now
    participant.save(update_fields=["state", "joined_at", "left_at", "last_seen_at", "updated_at"])
    call.status = SupportCallSession.Status.ONGOING
    call.answered_at = call.answered_at or now
    call.save(update_fields=["status", "answered_at", "updated_at"])
    publish_support_event(
        event_name="support.call.accepted",
        website_id=call.support_conversation.website_id,
        visitor_id=visitor.id,
        user_ids=[call.initiated_by_id],
        data=call_event_payload(call),
    )
    return call


@transaction.atomic
def decline_support_call(*, call: SupportCallSession, visitor, reason="declined") -> SupportCallSession:
    return end_support_call(
        call=call,
        actor_user=None,
        actor_visitor=visitor,
        reason=reason,
        forced_status=SupportCallSession.Status.DECLINED,
    )


@transaction.atomic
def end_support_call(
    *,
    call: SupportCallSession,
    actor_user=None,
    actor_visitor=None,
    reason="ended",
    forced_status=None,
) -> SupportCallSession:
    call = SupportCallSession.objects.select_for_update().select_related(
        "support_conversation", "support_conversation__website", "support_conversation__visitor"
    ).get(pk=call.pk)
    if call.status in TERMINAL_CALL_STATUSES:
        return call
    now = timezone.now()
    final_status = forced_status or SupportCallSession.Status.ENDED
    call.status = final_status
    call.ended_at = now
    call.ended_reason = str(reason or "ended")[:64]
    call.save(update_fields=["status", "ended_at", "ended_reason", "updated_at"])
    participant_qs = SupportCallParticipant.objects.filter(call=call)
    if final_status == SupportCallSession.Status.MISSED:
        participant_qs.filter(state=SupportCallParticipant.State.RINGING).update(
            state=SupportCallParticipant.State.MISSED, left_at=now, updated_at=now
        )
    elif final_status == SupportCallSession.Status.DECLINED:
        participant_qs.filter(kind=SupportCallParticipant.Kind.VISITOR).update(
            state=SupportCallParticipant.State.DECLINED, left_at=now, updated_at=now
        )
        participant_qs.filter(kind=SupportCallParticipant.Kind.TEAM).update(
            state=SupportCallParticipant.State.LEFT, left_at=now, updated_at=now
        )
    else:
        participant_qs.exclude(state__in=[SupportCallParticipant.State.DECLINED, SupportCallParticipant.State.MISSED]).update(
            state=SupportCallParticipant.State.LEFT, left_at=now, updated_at=now
        )
    actor = actor_user
    if actor or call.ended_reason in {"missed", "max_duration", "maintenance"}:
        summary = (
            f"{actor.username} ended a support call."
            if actor
            else f"Support Chat automatically ended a call ({call.ended_reason})."
        )
        record_audit_event(
            account=call.support_conversation.website.support_account,
            actor=actor,
            website=call.support_conversation.website,
            support_conversation=call.support_conversation,
            action="call.ended",
            target_type="support_call",
            target_id=call.id,
            summary=summary,
            metadata={"reason": call.ended_reason, "status": call.status},
        )
    publish_support_event(
        event_name="support.call.ended",
        website_id=call.support_conversation.website_id,
        visitor_id=call.support_conversation.visitor_id,
        user_ids=[call.initiated_by_id],
        data=call_event_payload(call),
    )
    return call


@transaction.atomic
def update_call_media(*, call, kind, user=None, visitor=None, audio_enabled=None, video_enabled=None):
    participant = SupportCallParticipant.objects.select_for_update().get(
        call=call,
        kind=kind,
        **({"user": user} if user is not None else {"visitor": visitor}),
    )
    fields = ["last_seen_at", "updated_at"]
    if audio_enabled is not None:
        participant.audio_enabled = bool(audio_enabled)
        fields.append("audio_enabled")
    if video_enabled is not None:
        participant.video_enabled = bool(video_enabled) and call.call_type == SupportCallSession.CallType.VIDEO
        fields.append("video_enabled")
    participant.last_seen_at = timezone.now()
    participant.save(update_fields=fields)
    publish_support_event(
        event_name="support.call.media_updated",
        website_id=call.support_conversation.website_id,
        visitor_id=call.support_conversation.visitor_id,
        user_ids=[call.initiated_by_id],
        data=call_event_payload(call),
    )
    return participant


@transaction.atomic
def create_call_signal(*, call, sender_kind, signal_type, payload, sender_user=None, sender_visitor=None):
    if call.status not in ACTIVE_CALL_STATUSES:
        raise SupportCallError("call_closed", "This call is no longer active.", 409)
    if signal_type not in ALLOWED_SIGNAL_TYPES:
        raise SupportCallError("invalid_signal", "Unsupported call signaling event.")
    payload = dict(payload or {})
    try:
        payload_size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise SupportCallError("invalid_signal", "The call signal payload is invalid.") from exc
    if payload_size > int(getattr(settings, "SUPPORT_CALL_SIGNAL_MAX_BYTES", 131072) or 131072):
        raise SupportCallError("signal_too_large", "The call signal is too large.", 413)
    signal_id = str(payload.pop("signal_id", "") or uuid4().hex)[:64]
    recipient_kind = (
        SupportCallParticipant.Kind.VISITOR
        if sender_kind == SupportCallParticipant.Kind.TEAM
        else SupportCallParticipant.Kind.TEAM
    )
    signal, created = SupportCallSignal.objects.get_or_create(
        signal_id=signal_id,
        defaults={
            "call": call,
            "sender_kind": sender_kind,
            "sender_user": sender_user,
            "sender_visitor": sender_visitor,
            "recipient_kind": recipient_kind,
            "signal_type": signal_type,
            "payload": payload,
        },
    )
    if not created and not (
        signal.call_id == call.id
        and signal.sender_kind == sender_kind
        and signal.sender_user_id == getattr(sender_user, "id", None)
        and signal.sender_visitor_id == getattr(sender_visitor, "id", None)
        and signal.signal_type == signal_type
        and signal.payload == payload
    ):
        raise SupportCallError("duplicate_signal", "This signal identifier is already in use.", 409)
    call.last_signal_at = timezone.now()
    call.save(update_fields=["last_signal_at", "updated_at"])
    event_data = {
        "call_id": str(call.id),
        "conversation_id": str(call.support_conversation_id),
        "signal": signal_payload(signal),
    }
    publish_support_event(
        event_name="support.call.signal",
        visitor_id=call.support_conversation.visitor_id if recipient_kind == SupportCallParticipant.Kind.VISITOR else None,
        user_ids=[call.initiated_by_id] if recipient_kind == SupportCallParticipant.Kind.TEAM else None,
        data=event_data,
    )
    return signal


def pending_call_signals(*, call, recipient_kind, consume=True):
    signals = list(
        SupportCallSignal.objects.filter(
            call=call,
            recipient_kind=recipient_kind,
            consumed_at__isnull=True,
        ).order_by("created_at")[:200]
    )
    if consume and signals:
        SupportCallSignal.objects.filter(pk__in=[signal.pk for signal in signals]).update(consumed_at=timezone.now())
    return signals


def call_event_payload(call: SupportCallSession):
    call = SupportCallSession.objects.select_related(
        "support_conversation",
        "support_conversation__website",
        "support_conversation__visitor",
        "initiated_by",
        "initiated_by__profile",
    ).prefetch_related("participants").get(pk=call.pk)
    profile = getattr(call.initiated_by, "profile", None)
    participants = []
    for participant in call.participants.all():
        participants.append({
            "kind": participant.kind,
            "state": participant.state,
            "audio_enabled": participant.audio_enabled,
            "video_enabled": participant.video_enabled,
            "joined_at": participant.joined_at.isoformat() if participant.joined_at else None,
            "left_at": participant.left_at.isoformat() if participant.left_at else None,
        })
    return {
        "id": str(call.id),
        "conversation_id": str(call.support_conversation_id),
        "website_id": str(call.support_conversation.website_id),
        "website_name": call.support_conversation.website.name,
        "visitor_id": str(call.support_conversation.visitor_id),
        "visitor_name": call.support_conversation.visitor.name or "Website visitor",
        "initiated_by": {
            "id": str(call.initiated_by_id),
            "display_name": getattr(profile, "display_name", "") or call.initiated_by.get_full_name() or call.initiated_by.username,
            "avatar": profile.avatar.url if profile and profile.avatar else None,
        },
        "call_type": call.call_type,
        "status": call.status,
        "started_at": call.started_at.isoformat(),
        "answered_at": call.answered_at.isoformat() if call.answered_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "ended_reason": call.ended_reason,
        "participants": participants,
    }


def signal_payload(signal: SupportCallSignal):
    return {
        "id": str(signal.id),
        "signal_id": signal.signal_id,
        "signal_type": signal.signal_type,
        "payload": signal.payload,
        "sender_kind": signal.sender_kind,
        "created_at": signal.created_at.isoformat(),
    }



def maintain_support_calls(*, now=None) -> dict[str, int]:
    now = now or timezone.now()
    ring_timeout = max(15, int(getattr(settings, "SUPPORT_CALL_RING_TIMEOUT_SECONDS", 45) or 45))
    missed = 0
    duration_ended = 0
    for call in list(
        SupportCallSession.objects.filter(
            status=SupportCallSession.Status.RINGING,
            started_at__lt=now - timedelta(seconds=ring_timeout),
        ).select_related("support_conversation__website__support_account")[:500]
    ):
        end_support_call(
            call=call,
            reason="missed",
            forced_status=SupportCallSession.Status.MISSED,
        )
        missed += 1

    settings_cache = {}
    ongoing_calls = (
        SupportCallSession.objects.filter(status=SupportCallSession.Status.ONGOING)
        .select_related("support_conversation__website__support_account")
        .order_by("answered_at")[:500]
    )
    for call in ongoing_calls:
        account = call.support_conversation.website.support_account
        limit = settings_cache.get(account.id)
        if limit is None:
            limit = call_settings_for(account).max_duration_minutes
            settings_cache[account.id] = limit
        started = call.answered_at or call.started_at
        if started <= now - timedelta(minutes=max(5, int(limit or 60))):
            end_support_call(call=call, reason="max_duration")
            duration_ended += 1

    signal_cutoff = now - timedelta(days=1)
    deleted_signals = SupportCallSignal.objects.filter(
        models.Q(consumed_at__lt=signal_cutoff)
        | models.Q(call__status__in=TERMINAL_CALL_STATUSES, created_at__lt=signal_cutoff)
    ).delete()[0]
    return {
        "missed": missed,
        "duration_ended": duration_ended,
        "signals_deleted": deleted_signals,
    }

def support_turn_credentials(identity):
    if not support_calls_enabled():
        raise SupportCallError("calls_disabled", "Support calls are not enabled.", 403)
    return get_turn_credentials(identity)


def team_turn_credentials(context, user):
    call = (
        SupportCallSession.objects.filter(
            initiated_by=user,
            support_conversation__website__support_account=context.account,
            status__in=ACTIVE_CALL_STATUSES,
        )
        .order_by("-started_at")
        .first()
    )
    if not call or _expire_if_stale(call):
        raise SupportCallError("no_active_call", "Start a Support call before requesting relay credentials.", 409)
    return support_turn_credentials(user)


def visitor_turn_credentials(session: SupportWidgetSession):
    try:
        visitor_call_for_session(session)
    except SupportCallError as exc:
        if exc.code == "not_found":
            raise SupportCallError("no_active_call", "There is no active Support call for this visitor.", 409) from exc
        raise
    identity = type("SupportVisitorIdentity", (), {"id": f"support-visitor-{session.visitor_id}"})()
    return support_turn_credentials(identity)
