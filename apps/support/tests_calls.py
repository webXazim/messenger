from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.db import IntegrityError, transaction
from django.core.management import call_command
from io import StringIO
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.chat.models import CallParticipant, CallSession, ConversationParticipant
from apps.support.call_services import maintain_support_calls
from apps.support.models import (
    SupportAccount,
    SupportAgent,
    SupportCallParticipant,
    SupportCallSession,
    SupportCallSettings,
    SupportCallSignal,
    SupportConversation,
    SupportWebsite,
    SupportWidgetSettings,
    SupportWebsiteAgent,
)

User = get_user_model()


@override_settings(
    SUPPORT_CHAT_ENABLED=True,
    SUPPORT_WIDGET_ENABLED=True,
    SUPPORT_WIDGET_REQUIRE_ORIGIN=True,
    SUPPORT_CALLS_ENABLED=True,
    SUPPORT_CALL_RING_TIMEOUT_SECONDS=45,
    SUPPORT_CALL_SIGNAL_MAX_BYTES=131072,
    TURN_PROVIDER="legacy",
    TURN_URIS_JSON='["turn:turn.example.com:3478?transport=udp","turns:turn.example.com:5349?transport=tcp"]',
    TURN_SHARED_SECRET="support-test-turn-shared-secret-that-is-long-enough",
)
class SupportGuestCallTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="support-owner", email="owner@example.com", password="pass")
        self.agent_user = User.objects.create_user(username="support-agent", email="agent@example.com", password="pass")
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            status=SupportAccount.Status.ACTIVE,
            plan_code="support-business",
            website_limit=5,
            agent_limit=5,
        )
        self.website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Main website",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        self.session = self._create_session(self.website, "Visitor One")
        self.conversation = self._create_conversation(self.website, self.session, "I need a call")
        self.client.force_authenticate(self.owner)

    def _create_session(self, website, name):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/",
            {"name": name, "email": f"{name.lower().replace(' ', '.')}@example.com"},
            format="json",
            HTTP_ORIGIN=f"https://{website.domain}",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def _create_conversation(self, website, session, text):
        response = self.client.post(
            f"/api/v1/support/widget/{website.site_key}/sessions/{session['id']}/conversation/messages/",
            {"text": text},
            format="json",
            HTTP_ORIGIN=f"https://{website.domain}",
            HTTP_AUTHORIZATION=f"Bearer {session['token']}",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return SupportConversation.objects.get(website=website, visitor_id=session["visitor"]["id"])

    def _widget_headers(self, website, session):
        return {
            "HTTP_ORIGIN": f"https://{website.domain}",
            "HTTP_AUTHORIZATION": f"Bearer {session['token']}",
        }

    def _start(self, call_type="voice"):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/support/conversations/{self.conversation.id}/calls/",
            {"call_type": call_type},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_support_call_is_separate_from_messenger_calling_and_participants(self):
        response = self._start("video")
        call = SupportCallSession.objects.get(pk=response.data["id"])
        self.assertEqual(call.support_conversation, self.conversation)
        self.assertEqual(call.participants.count(), 2)
        self.assertEqual(
            set(call.participants.values_list("kind", flat=True)),
            {SupportCallParticipant.Kind.TEAM, SupportCallParticipant.Kind.VISITOR},
        )
        self.assertNotIn("room_key", response.data)
        self.assertFalse(ConversationParticipant.objects.filter(conversation=self.conversation.conversation).exists())
        self.assertEqual(CallSession.objects.count(), 0)
        self.assertEqual(CallParticipant.objects.count(), 0)

    def test_active_call_recovery_and_widget_acceptance_are_identity_bound(self):
        started = self._start()
        recovered = self.client.get(f"/api/v1/support/conversations/{self.conversation.id}/calls/")
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.data["call"]["id"], started.data["id"])
        global_recovery = self.client.get("/api/v1/support/calls/active/")
        self.assertEqual(global_recovery.status_code, 200)
        self.assertEqual(global_recovery.data["call"]["id"], started.data["id"])

        self.client.force_authenticate(user=None)
        accepted = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{self.session['id']}/calls/{started.data['id']}/accept/",
            {},
            format="json",
            **self._widget_headers(self.website, self.session),
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data["status"], SupportCallSession.Status.ONGOING)

        other_website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Other website",
            domain="other.example.com",
            allowed_origins=["https://other.example.com"],
        )
        other_session = self._create_session(other_website, "Other Visitor")
        denied = self.client.get(
            f"/api/v1/support/widget/{other_website.site_key}/sessions/{other_session['id']}/calls/{started.data['id']}/",
            **self._widget_headers(other_website, other_session),
        )
        self.assertEqual(denied.status_code, 404)

    def test_visitor_can_start_call_and_assigned_team_member_can_accept(self):
        self.client.force_authenticate(user=None)
        started = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{self.session['id']}/calls/",
            {"call_type": "video"},
            format="json",
            **self._widget_headers(self.website, self.session),
        )
        self.assertEqual(started.status_code, 201, started.data)
        self.assertEqual(started.data["initiator_kind"], SupportCallSession.InitiatorKind.VISITOR)
        call = SupportCallSession.objects.get(pk=started.data["id"])
        self.assertEqual(call.initiated_by, self.owner)
        self.assertEqual(
            call.participants.get(kind=SupportCallParticipant.Kind.VISITOR).state,
            SupportCallParticipant.State.JOINED,
        )
        self.assertEqual(
            call.participants.get(kind=SupportCallParticipant.Kind.TEAM).state,
            SupportCallParticipant.State.RINGING,
        )

        offer = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{self.session['id']}/calls/{call.id}/signals/",
            {"signal_type": "offer", "payload": {"signal_id": "visitor-offer", "sdp": "visitor-sdp"}},
            format="json",
            **self._widget_headers(self.website, self.session),
        )
        self.assertEqual(offer.status_code, 201, offer.data)

        self.client.force_authenticate(self.owner)
        active = self.client.get("/api/v1/support/calls/active/")
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.data["call"]["id"], str(call.id))
        accepted = self.client.post(f"/api/v1/support/calls/{call.id}/accept/", {}, format="json")
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data["status"], SupportCallSession.Status.ONGOING)
        detail = self.client.get(f"/api/v1/support/calls/{call.id}/")
        self.assertEqual(detail.data["pending_signals"][0]["signal_id"], "visitor-offer")

    def test_agent_from_another_website_cannot_access_or_signal_call(self):
        started = self._start()
        other_website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Agent website",
            domain="agents.example.com",
            allowed_origins=["https://agents.example.com"],
        )
        agent = SupportAgent.objects.create(
            support_account=self.account,
            user=self.agent_user,
            invited_by=self.owner,
            can_view_all_conversations=True,
        )
        SupportWebsiteAgent.objects.create(website=other_website, agent=agent)
        self.client.force_authenticate(self.agent_user)
        detail = self.client.get(f"/api/v1/support/calls/{started.data['id']}/")
        signal = self.client.post(
            f"/api/v1/support/calls/{started.data['id']}/signals/",
            {"signal_type": "answer", "payload": {"sdp": "not-allowed"}},
            format="json",
        )
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(signal.status_code, 404)

    def test_agent_on_same_website_cannot_take_over_another_agents_call(self):
        started = self._start()
        agent = SupportAgent.objects.create(
            support_account=self.account,
            user=self.agent_user,
            invited_by=self.owner,
            can_view_all_conversations=True,
        )
        SupportWebsiteAgent.objects.create(website=self.website, agent=agent)
        self.client.force_authenticate(self.agent_user)
        detail = self.client.get(f"/api/v1/support/calls/{started.data['id']}/")
        signals = self.client.get(f"/api/v1/support/calls/{started.data['id']}/signals/")
        ended = self.client.post(f"/api/v1/support/calls/{started.data['id']}/end/", {}, format="json")
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(signals.status_code, 404)
        self.assertEqual(ended.status_code, 404)
        self.assertEqual(SupportCallSession.objects.get(pk=started.data["id"]).status, SupportCallSession.Status.RINGING)

    def test_signaling_is_persisted_directional_deduplicated_and_scoped(self):
        started = self._start()
        signal_url = f"/api/v1/support/calls/{started.data['id']}/signals/"
        first = self.client.post(
            signal_url,
            {"signal_type": "offer", "payload": {"signal_id": "offer-one", "sdp": "offer-sdp"}},
            format="json",
        )
        replay = self.client.post(
            signal_url,
            {"signal_type": "offer", "payload": {"signal_id": "offer-one", "sdp": "offer-sdp"}},
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(first.data["id"], replay.data["id"])
        self.assertEqual(SupportCallSignal.objects.count(), 1)

        self.client.force_authenticate(user=None)
        visitor_signals = self.client.get(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{self.session['id']}/calls/{started.data['id']}/signals/",
            **self._widget_headers(self.website, self.session),
        )
        self.assertEqual(visitor_signals.status_code, 200)
        self.assertEqual(visitor_signals.data["signals"][0]["signal_id"], "offer-one")
        answer = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{self.session['id']}/calls/{started.data['id']}/signals/",
            {"signal_type": "answer", "payload": {"signal_id": "answer-one", "sdp": "answer-sdp"}},
            format="json",
            **self._widget_headers(self.website, self.session),
        )
        self.assertEqual(answer.status_code, 201, answer.data)

        self.client.force_authenticate(self.owner)
        detail = self.client.get(f"/api/v1/support/calls/{started.data['id']}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["pending_signals"][0]["signal_id"], "answer-one")

        self.client.post(f"/api/v1/support/calls/{started.data['id']}/end/", {}, format="json")
        second_session = self._create_session(self.website, "Visitor Two")
        second_conversation = self._create_conversation(self.website, second_session, "Another call")
        self.client.force_authenticate(self.owner)
        second = self.client.post(
            f"/api/v1/support/conversations/{second_conversation.id}/calls/",
            {"call_type": "voice"},
            format="json",
        )
        collision = self.client.post(
            f"/api/v1/support/calls/{second.data['id']}/signals/",
            {"signal_type": "offer", "payload": {"signal_id": "offer-one", "sdp": "different-call"}},
            format="json",
        )
        self.assertEqual(collision.status_code, 409)
        self.assertEqual(collision.data["code"], "duplicate_signal")

    def test_account_and_website_settings_gate_video_calls(self):
        self.client.force_authenticate(self.owner)
        updated = self.client.patch("/api/v1/support/call-settings/", {"allow_video": False}, format="json")
        self.assertEqual(updated.status_code, 200)
        denied = self.client.post(
            f"/api/v1/support/conversations/{self.conversation.id}/calls/",
            {"call_type": "video"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "video_disabled")

    def test_website_audio_and_video_gates_are_independent_and_initiator_is_unique(self):
        SupportWidgetSettings.objects.update_or_create(
            website=self.website,
            defaults={"allow_audio_calls": False, "allow_video_calls": True},
        )
        self.client.force_authenticate(self.owner)
        voice = self.client.post(
            f"/api/v1/support/conversations/{self.conversation.id}/calls/",
            {"call_type": "voice"},
            format="json",
        )
        self.assertEqual(voice.status_code, 403)
        self.assertEqual(voice.data["code"], "voice_disabled")

        video = self.client.post(
            f"/api/v1/support/conversations/{self.conversation.id}/calls/",
            {"call_type": "video"},
            format="json",
        )
        self.assertEqual(video.status_code, 201, video.data)

        second_session = self._create_session(self.website, "Visitor Three")
        second_conversation = self._create_conversation(self.website, second_session, "Concurrent call")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SupportCallSession.objects.create(
                    support_conversation=second_conversation,
                    initiated_by=self.owner,
                    call_type=SupportCallSession.CallType.VIDEO,
                    status=SupportCallSession.Status.RINGING,
                    room_key="support-concurrent-owner-call",
                )

    def test_widget_turn_credentials_use_support_visitor_identity(self):
        self.client.force_authenticate(user=None)
        url = f"/api/v1/support/widget/{self.website.site_key}/sessions/{self.session['id']}/calls/turn-credentials/"
        denied = self.client.get(url, **self._widget_headers(self.website, self.session))
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(denied.data["code"], "no_active_call")

        self._start()
        self.client.force_authenticate(user=None)
        response = self.client.get(url, **self._widget_headers(self.website, self.session))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["configured"])
        self.assertIn("support-visitor-", response.data["username"])
        self.assertTrue(response.data["ice_servers"])

    def test_team_turn_credentials_require_an_active_owned_call(self):
        self.client.force_authenticate(self.owner)
        denied = self.client.get("/api/v1/support/calls/turn-credentials/")
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(denied.data["code"], "no_active_call")
        self._start()
        allowed = self.client.get("/api/v1/support/calls/turn-credentials/")
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertTrue(allowed.data["ice_servers"])

    @override_settings(SUPPORT_CALL_RING_TIMEOUT_SECONDS=15)
    def test_call_maintenance_expires_ringing_caps_duration_and_cleans_signals(self):
        started = self._start()
        call = SupportCallSession.objects.get(pk=started.data["id"])
        SupportCallSession.objects.filter(pk=call.pk).update(started_at=timezone.now() - timedelta(minutes=2))
        result = maintain_support_calls()
        call.refresh_from_db()
        self.assertEqual(call.status, SupportCallSession.Status.MISSED)
        self.assertEqual(result["missed"], 1)

        second = self._start()
        call = SupportCallSession.objects.get(pk=second.data["id"])
        self.client.force_authenticate(user=None)
        accepted = self.client.post(
            f"/api/v1/support/widget/{self.website.site_key}/sessions/{self.session['id']}/calls/{call.id}/accept/",
            {},
            format="json",
            **self._widget_headers(self.website, self.session),
        )
        self.assertEqual(accepted.status_code, 200)
        SupportCallSettings.objects.update_or_create(
            support_account=self.account,
            defaults={"enabled": True, "allow_video": True, "max_duration_minutes": 5},
        )
        SupportCallSession.objects.filter(pk=call.pk).update(answered_at=timezone.now() - timedelta(minutes=6))
        signal = SupportCallSignal.objects.create(
            call=call,
            sender_kind=SupportCallParticipant.Kind.TEAM,
            sender_user=self.owner,
            recipient_kind=SupportCallParticipant.Kind.VISITOR,
            signal_id="old-consumed-signal",
            signal_type="offer",
            payload={"sdp": "old"},
            consumed_at=timezone.now() - timedelta(days=2),
        )
        result = maintain_support_calls()
        call.refresh_from_db()
        self.assertEqual(call.status, SupportCallSession.Status.ENDED)
        self.assertEqual(call.ended_reason, "max_duration")
        self.assertEqual(result["duration_ended"], 1)
        self.assertFalse(SupportCallSignal.objects.filter(pk=signal.pk).exists())
    @override_settings(
        SUPPORT_WIDGET_SCRIPT_URL="https://messenger.example.com/support-widget/v1/widget.js",
        FRONTEND_BASE_URL="https://messenger.example.com",
    )
    def test_support_readiness_command_includes_guest_call_maintenance(self):
        output = StringIO()
        call_command("check_support_readiness", stdout=output)
        text = output.getvalue()
        self.assertIn("Support Chat readiness summary", text)
        self.assertIn("Guest calls: enabled", text)
        self.assertIn("passed readiness checks", text)
