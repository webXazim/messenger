from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportAgentInvitation,
    SupportAgentInvitationWebsite,
    SupportAgentInvitationTeam,
    SupportConversation,
    SupportTeam,
    SupportTeamMembership,
    SupportWebsite,
    SupportWebsiteAgent,
)

from apps.support.realtime import publish_support_event

User = get_user_model()


def _publish_agent_access_event(agent: SupportAgent, *, reason: str) -> None:
    publish_support_event(
        event_name="support.access.updated",
        user_ids=[agent.user_id],
        data={
            "account_id": str(agent.support_account_id),
            "agent_id": str(agent.id),
            "user_id": str(agent.user_id),
            "reason": reason,
        },
    )


class SupportServiceError(Exception):
    def __init__(self, detail: str, *, code: str = "invalid"):
        super().__init__(detail)
        self.detail = detail
        self.code = code


@dataclass(frozen=True)
class SupportContext:
    account: SupportAccount | None
    role: str | None
    agent: SupportAgent | None


def support_chat_enabled() -> bool:
    return bool(getattr(settings, "SUPPORT_CHAT_ENABLED", False))


def get_support_context(user) -> SupportContext:
    try:
        account = user.owned_support_account
        return SupportContext(account=account, role="owner", agent=None)
    except SupportAccount.DoesNotExist:
        pass

    agent = (
        SupportAgent.objects.select_related("support_account", "support_account__owner")
        .filter(user=user, is_active=True)
        .order_by("created_at")
        .first()
    )
    if agent:
        return SupportContext(account=agent.support_account, role="agent", agent=agent)
    return SupportContext(account=None, role=None, agent=None)


def visible_websites(context: SupportContext):
    if not context.account:
        return SupportWebsite.objects.none()
    queryset = SupportWebsite.objects.filter(support_account=context.account, is_active=True)
    if context.role == "owner":
        return queryset
    if context.agent:
        return queryset.filter(agent_assignments__agent=context.agent).distinct()
    return queryset.none()


def normalize_agent_email(value: str) -> str:
    return User.objects.normalize_email((value or "").strip()).lower()


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def _invitation_ttl() -> timedelta:
    hours = max(1, int(getattr(settings, "SUPPORT_AGENT_INVITE_TTL_HOURS", 168) or 168))
    return timedelta(hours=hours)


def _frontend_base_url() -> str:
    return (
        getattr(settings, "FRONTEND_BASE_URL", "")
        or getattr(settings, "SITE_URL", "")
        or "http://localhost:5173"
    ).rstrip("/")


def _invitation_url(raw_token: str) -> str:
    return f"{_frontend_base_url()}/support/invitations/accept?{urlencode({'token': raw_token})}"


def _person_name(user) -> str:
    profile = getattr(user, "profile", None)
    return getattr(profile, "display_name", "") or user.get_full_name() or user.username


def send_agent_invitation_email(invitation: SupportAgentInvitation, raw_token: str) -> int:
    website_names = list(
        invitation.website_assignments.select_related("website")
        .order_by("website__name")
        .values_list("website__name", flat=True)
    )
    website_copy = ", ".join(website_names) if website_names else "No website access has been assigned yet"
    inviter = _person_name(invitation.invited_by) if invitation.invited_by else "The Support Chat owner"
    body = (
        f"{inviter} invited you to join their Support Chat team as an agent.\n\n"
        f"Website access: {website_copy}\n"
        f"Accept invitation: {_invitation_url(raw_token)}\n\n"
        "This invitation is connected to your email address and expires automatically. "
        "It does not add the inviter as a Messenger friend or expose personal Messenger data."
    )
    return send_mail(
        "You are invited to Support Chat",
        body,
        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@localhost"),
        [invitation.email],
        fail_silently=True,
    )


def expire_stale_invitations(account: SupportAccount | None = None) -> int:
    queryset = SupportAgentInvitation.objects.filter(
        status=SupportAgentInvitation.Status.PENDING,
        expires_at__lte=timezone.now(),
    )
    if account is not None:
        queryset = queryset.filter(support_account=account)
    return queryset.update(status=SupportAgentInvitation.Status.EXPIRED, updated_at=timezone.now())


def active_agent_count(account: SupportAccount) -> int:
    return SupportAgent.objects.filter(support_account=account, is_active=True).count()


def pending_invitation_count(account: SupportAccount) -> int:
    expire_stale_invitations(account)
    return SupportAgentInvitation.objects.filter(
        support_account=account,
        status=SupportAgentInvitation.Status.PENDING,
        expires_at__gt=timezone.now(),
    ).count()


def used_agent_seats(account: SupportAccount) -> int:
    return active_agent_count(account) + pending_invitation_count(account)


def _websites_for_account(account: SupportAccount, website_ids) -> list[SupportWebsite]:
    normalized_ids = list(dict.fromkeys(str(value) for value in (website_ids or []) if value))
    if not normalized_ids:
        return []
    websites = list(
        SupportWebsite.objects.filter(
            support_account=account,
            is_active=True,
            id__in=normalized_ids,
        ).order_by("name")
    )
    if len(websites) != len(normalized_ids):
        raise SupportServiceError("One or more selected websites are unavailable.", code="invalid_websites")
    return websites


def _teams_for_account(account: SupportAccount, team_ids) -> list[SupportTeam]:
    normalized_ids=list(dict.fromkeys(str(v) for v in (team_ids or []) if v))
    if not normalized_ids: return []
    teams=list(SupportTeam.objects.filter(support_account=account, is_active=True, id__in=normalized_ids).order_by("name"))
    if len(teams)!=len(normalized_ids): raise SupportServiceError("One or more selected teams are unavailable.", code="invalid_teams")
    return teams


def _ensure_invitable_email(account: SupportAccount, email: str) -> None:
    if email == normalize_agent_email(account.owner.email):
        raise SupportServiceError("The Support Chat owner is already included and does not use an agent seat.", code="owner_email")

    user = User.objects.filter(email__iexact=email).first()
    if not user:
        return
    if SupportAccount.objects.filter(owner=user).exists():
        raise SupportServiceError("This person already owns a Support Chat account.", code="already_owner")
    if SupportAgent.objects.filter(user=user, is_active=True).exists():
        raise SupportServiceError("This person is already an active Support Chat agent.", code="already_agent")


def create_agent_invitation(
    *, actor, account: SupportAccount, email: str, website_ids, team_ids=None,
    max_active_conversations: int = 5, can_view_all_conversations: bool = False,
    can_assign_conversations: bool = False, can_view_analytics: bool = False,
    can_manage_websites: bool = False, can_manage_knowledge: bool = False,
    can_manage_teams: bool = False, can_manage_automations: bool = False,
    can_export_data: bool = False,
) -> SupportAgentInvitation:
    normalized_email = normalize_agent_email(email)
    if not normalized_email or "@" not in normalized_email:
        raise SupportServiceError("Enter a valid email address.", code="invalid_email")

    raw_token = secrets.token_urlsafe(32)
    with transaction.atomic():
        locked_account = SupportAccount.objects.select_for_update().get(pk=account.pk)
        if not locked_account.has_product_access:
            raise SupportServiceError("Support Chat access is not active.", code="access_inactive")
        expire_stale_invitations(locked_account)
        _ensure_invitable_email(locked_account, normalized_email)
        if SupportAgentInvitation.objects.filter(
            support_account=locked_account,
            email=normalized_email,
            status=SupportAgentInvitation.Status.PENDING,
            expires_at__gt=timezone.now(),
        ).exists():
            raise SupportServiceError("A pending invitation already exists for this email.", code="already_invited")
        if used_agent_seats(locked_account) >= locked_account.agent_limit:
            raise SupportServiceError("Your current Support Chat plan has reached its agent limit.", code="agent_limit")

        websites = _websites_for_account(locked_account, website_ids)
        teams = _teams_for_account(locked_account, team_ids)
        invitation = SupportAgentInvitation.objects.create(
            support_account=locked_account,
            email=normalized_email,
            token_hash=_token_hash(raw_token),
            expires_at=timezone.now() + _invitation_ttl(),
            max_active_conversations=max_active_conversations,
            can_view_all_conversations=can_view_all_conversations,
            can_assign_conversations=can_assign_conversations,
            can_view_analytics=can_view_analytics,
            can_manage_websites=can_manage_websites, can_manage_knowledge=can_manage_knowledge, can_manage_teams=can_manage_teams, can_manage_automations=can_manage_automations, can_export_data=can_export_data,
            invited_by=actor,
        )
        SupportAgentInvitationTeam.objects.bulk_create([SupportAgentInvitationTeam(invitation=invitation, team=team) for team in teams])
        SupportAgentInvitationWebsite.objects.bulk_create([
            SupportAgentInvitationWebsite(invitation=invitation, website=website)
            for website in websites
        ])

    invitation = SupportAgentInvitation.objects.prefetch_related("website_assignments__website", "team_assignments__team").get(pk=invitation.pk)
    send_agent_invitation_email(invitation, raw_token)
    return invitation


def resend_agent_invitation(*, actor, account: SupportAccount, invitation: SupportAgentInvitation) -> SupportAgentInvitation:
    raw_token = secrets.token_urlsafe(32)
    with transaction.atomic():
        locked_account = SupportAccount.objects.select_for_update().get(pk=account.pk)
        locked_invitation = SupportAgentInvitation.objects.select_for_update().get(
            pk=invitation.pk,
            support_account=locked_account,
        )
        if locked_invitation.status in {
            SupportAgentInvitation.Status.ACCEPTED,
            SupportAgentInvitation.Status.REVOKED,
        }:
            raise SupportServiceError("This invitation can no longer be resent.", code="not_resendable")
        _ensure_invitable_email(locked_account, locked_invitation.email)
        if SupportAgentInvitation.objects.filter(
            support_account=locked_account,
            email=locked_invitation.email,
            status=SupportAgentInvitation.Status.PENDING,
            expires_at__gt=timezone.now(),
        ).exclude(pk=locked_invitation.pk).exists():
            raise SupportServiceError("A newer pending invitation already exists for this email.", code="already_invited")
        was_reserving_seat = locked_invitation.is_active
        if not was_reserving_seat and used_agent_seats(locked_account) >= locked_account.agent_limit:
            raise SupportServiceError("Your current Support Chat plan has reached its agent limit.", code="agent_limit")

        now = timezone.now()
        locked_invitation.token_hash = _token_hash(raw_token)
        locked_invitation.status = SupportAgentInvitation.Status.PENDING
        locked_invitation.expires_at = now + _invitation_ttl()
        locked_invitation.last_sent_at = now
        locked_invitation.send_count += 1
        locked_invitation.invited_by = actor
        locked_invitation.revoked_at = None
        locked_invitation.save(update_fields=[
            "token_hash",
            "status",
            "expires_at",
            "last_sent_at",
            "send_count",
            "invited_by",
            "revoked_at",
            "updated_at",
        ])

    locked_invitation = SupportAgentInvitation.objects.prefetch_related("website_assignments__website", "team_assignments__team").get(pk=locked_invitation.pk)
    send_agent_invitation_email(locked_invitation, raw_token)
    return locked_invitation


def revoke_agent_invitation(*, account: SupportAccount, invitation: SupportAgentInvitation) -> SupportAgentInvitation:
    with transaction.atomic():
        locked = SupportAgentInvitation.objects.select_for_update().get(pk=invitation.pk, support_account=account)
        if locked.status == SupportAgentInvitation.Status.ACCEPTED:
            raise SupportServiceError("An accepted invitation cannot be revoked.", code="already_accepted")
        if locked.status != SupportAgentInvitation.Status.REVOKED:
            locked.status = SupportAgentInvitation.Status.REVOKED
            locked.revoked_at = timezone.now()
            locked.save(update_fields=["status", "revoked_at", "updated_at"])
        return locked


def invitation_from_token(raw_token: str) -> SupportAgentInvitation | None:
    if not raw_token:
        return None
    invitation = (
        SupportAgentInvitation.objects.select_related(
            "support_account",
            "support_account__owner",
            "invited_by",
        )
        .prefetch_related("website_assignments__website", "team_assignments__team")
        .filter(token_hash=_token_hash(raw_token))
        .first()
    )
    if invitation and invitation.status == SupportAgentInvitation.Status.PENDING and invitation.expires_at <= timezone.now():
        SupportAgentInvitation.objects.filter(pk=invitation.pk).update(
            status=SupportAgentInvitation.Status.EXPIRED,
            updated_at=timezone.now(),
        )
        invitation.status = SupportAgentInvitation.Status.EXPIRED
    return invitation


def accept_agent_invitation(*, user, raw_token: str) -> SupportAgent:
    invitation = invitation_from_token(raw_token)
    if not invitation:
        raise SupportServiceError("This invitation is invalid or no longer available.", code="invalid_invitation")

    with transaction.atomic():
        locked_invitation = (
            SupportAgentInvitation.objects.select_for_update()
            .select_related("support_account", "support_account__owner")
            .get(pk=invitation.pk)
        )
        account = SupportAccount.objects.select_for_update().get(pk=locked_invitation.support_account_id)
        if locked_invitation.status != SupportAgentInvitation.Status.PENDING or locked_invitation.expires_at <= timezone.now():
            if locked_invitation.status == SupportAgentInvitation.Status.PENDING:
                locked_invitation.status = SupportAgentInvitation.Status.EXPIRED
                locked_invitation.save(update_fields=["status", "updated_at"])
            raise SupportServiceError("This invitation has expired or is no longer available.", code="invitation_unavailable")
        if not account.has_product_access:
            raise SupportServiceError("The owner must renew Support Chat before this invitation can be accepted.", code="access_inactive")
        if normalize_agent_email(user.email) != locked_invitation.email:
            raise SupportServiceError("Sign in with the email address that received this invitation.", code="email_mismatch")
        if account.owner_id == user.id or SupportAccount.objects.filter(owner=user).exists():
            raise SupportServiceError("A Support Chat owner cannot join another Support Chat account as an agent.", code="already_owner")
        other_agent = SupportAgent.objects.filter(user=user, is_active=True).exclude(support_account=account).first()
        if other_agent:
            raise SupportServiceError("This account is already an active agent for another Support Chat team.", code="already_agent")
        if active_agent_count(account) >= account.agent_limit:
            raise SupportServiceError("The Support Chat plan no longer has an available agent seat.", code="agent_limit")

        agent, _ = SupportAgent.objects.get_or_create(
            support_account=account,
            user=user,
            defaults={"invited_by": locked_invitation.invited_by},
        )
        agent.availability = SupportAgent.Availability.OFFLINE
        agent.max_active_conversations = locked_invitation.max_active_conversations
        agent.can_view_all_conversations = locked_invitation.can_view_all_conversations
        agent.can_assign_conversations = locked_invitation.can_assign_conversations
        agent.can_view_analytics = locked_invitation.can_view_analytics
        agent.can_manage_websites = locked_invitation.can_manage_websites
        agent.can_manage_knowledge = locked_invitation.can_manage_knowledge
        agent.can_manage_teams = locked_invitation.can_manage_teams
        agent.can_manage_automations = locked_invitation.can_manage_automations
        agent.can_export_data = locked_invitation.can_export_data
        agent.is_active = True
        agent.invited_by = locked_invitation.invited_by
        agent.joined_at = timezone.now()
        agent.full_clean()
        agent.save()

        SupportWebsiteAgent.objects.filter(agent=agent).delete()
        SupportTeamMembership.objects.filter(agent=agent).delete()
        website_ids = list(
            SupportAgentInvitationWebsite.objects.filter(
                invitation=locked_invitation,
                website__is_active=True,
                website__support_account=account,
            ).values_list("website_id", flat=True)
        )
        SupportWebsiteAgent.objects.bulk_create([
            SupportWebsiteAgent(agent=agent, website_id=website_id)
            for website_id in website_ids
        ])

        team_ids=list(SupportAgentInvitationTeam.objects.filter(invitation=locked_invitation, team__is_active=True, team__support_account=account).values_list("team_id", flat=True))
        SupportTeamMembership.objects.bulk_create([SupportTeamMembership(agent=agent, team_id=team_id) for team_id in team_ids])

        locked_invitation.status = SupportAgentInvitation.Status.ACCEPTED
        locked_invitation.accepted_at = timezone.now()
        locked_invitation.accepted_by = user
        locked_invitation.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])
        _publish_agent_access_event(agent, reason="invitation_accepted")
        return agent


def update_agent(*, account: SupportAccount, agent: SupportAgent, website_ids, team_ids=None, max_active_conversations: int, **permissions) -> SupportAgent:
    allowed = {"can_view_all_conversations", "can_assign_conversations", "can_view_analytics", "can_manage_websites", "can_manage_knowledge", "can_manage_teams", "can_manage_automations", "can_export_data"}
    with transaction.atomic():
        locked=SupportAgent.objects.select_for_update().get(pk=agent.pk, support_account=account, is_active=True)
        websites=_websites_for_account(account, website_ids); teams=_teams_for_account(account, team_ids)
        locked.max_active_conversations=max_active_conversations
        for key in allowed:
            if key in permissions: setattr(locked,key,bool(permissions[key]))
        locked.full_clean(); locked.save(update_fields=["max_active_conversations", *sorted(allowed), "updated_at"])
        SupportWebsiteAgent.objects.filter(agent=locked).delete(); SupportWebsiteAgent.objects.bulk_create([SupportWebsiteAgent(agent=locked, website=w) for w in websites])
        SupportTeamMembership.objects.filter(agent=locked).delete(); SupportTeamMembership.objects.bulk_create([SupportTeamMembership(agent=locked, team=t) for t in teams])
        _publish_agent_access_event(locked, reason="agent_access_changed")
        return locked


def deactivate_agent(*, account: SupportAccount, agent: SupportAgent) -> SupportAgent:
    with transaction.atomic():
        locked = SupportAgent.objects.select_for_update().get(pk=agent.pk, support_account=account)
        if locked.is_active:
            locked.is_active = False
            locked.availability = SupportAgent.Availability.OFFLINE
            locked.save(update_fields=["is_active", "availability", "updated_at"])
            SupportConversation.objects.filter(assigned_agent=locked).exclude(status__in=[SupportConversation.Status.RESOLVED, SupportConversation.Status.CLOSED]).update(assigned_agent=None, updated_at=timezone.now())
            SupportWebsiteAgent.objects.filter(agent=locked).delete()
            SupportTeamMembership.objects.filter(agent=locked).delete()
            _publish_agent_access_event(locked, reason="agent_deactivated")
        return locked


def update_agent_availability(*, user, availability: str) -> SupportAgent:
    with transaction.atomic():
        agent = (
            SupportAgent.objects.select_for_update()
            .select_related("support_account")
            .filter(user=user, is_active=True)
            .first()
        )
        if not agent:
            raise SupportServiceError("You are not an active Support Chat agent.", code="not_agent")
        if not agent.support_account.has_product_access:
            raise SupportServiceError("Support Chat access is not active.", code="access_inactive")
        valid = {choice for choice, _label in SupportAgent.Availability.choices}
        if availability not in valid:
            raise SupportServiceError("Choose a valid availability status.", code="invalid_availability")
        agent.availability = availability
        agent.save(update_fields=["availability", "updated_at"])
        return agent
