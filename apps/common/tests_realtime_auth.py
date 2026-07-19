from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.common.api.views import RealtimeTicketView
from apps.common.realtime_auth import (
    RealtimeCredentialError,
    issue_audience_grant,
    issue_call_grant,
    normalize_realtime_origin,
    realtime_private_key,
    realtime_public_key,
)
from apps.common.realtime import RealtimeAudience
from apps.chat.models import CallParticipant, CallSession, Conversation, ConversationParticipant

User = get_user_model()


class RealtimeCredentialTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        cls.public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def setUp(self):
        realtime_private_key.cache_clear()
        realtime_public_key.cache_clear()
        self.settings_override = override_settings(
            REALTIME_AUTH_ENABLED=True,
            REALTIME_TOKEN_ALGORITHM="RS256",
            REALTIME_TOKEN_ISSUER="test-django",
            REALTIME_TICKET_AUDIENCE="test-realtime",
            REALTIME_GRANT_AUDIENCE="test-realtime-grant",
            REALTIME_TICKET_TTL_SECONDS=45,
            REALTIME_GRANT_TTL_SECONDS=90,
            REALTIME_REQUIRE_ORIGIN=True,
            REALTIME_ALLOWED_ORIGINS=["https://app.example.test"],
            REALTIME_SIGNING_PRIVATE_KEY=self.private_pem,
            REALTIME_SIGNING_PUBLIC_KEY=self.public_pem,
            REALTIME_SIGNING_PRIVATE_KEY_PATH="",
            REALTIME_SIGNING_PUBLIC_KEY_PATH="",
        )
        self.settings_override.enable()

    def tearDown(self):
        realtime_private_key.cache_clear()
        realtime_public_key.cache_clear()
        self.settings_override.disable()
        super().tearDown()

    def decode(self, token, audience):
        return jwt.decode(
            token,
            self.public_pem,
            algorithms=["RS256"],
            issuer="test-django",
            audience=audience,
        )

    @patch("apps.common.realtime_auth.get_support_context")
    def test_ticket_endpoint_issues_origin_bound_single_use_claims(self, support_context):
        support_context.return_value = SimpleNamespace(account=None, agent=None, role="")
        user = SimpleNamespace(id="user-1", pk="user-1", is_authenticated=True)
        request = APIRequestFactory().post(
            "/api/v1/realtime/tickets/",
            {"device_id": "browser-1", "device_type": "desktop"},
            format="json",
            HTTP_ORIGIN="https://app.example.test",
        )
        force_authenticate(request, user=user, token={"session_id": "session-1"})

        with patch("apps.chat.services.presence_recipient_ids", return_value=[]), patch(
            "apps.chat.services.get_public_presence_snapshot",
            return_value={
                "is_online": False,
                "active_devices": 0,
                "last_seen_at": None,
                "presence_status": "offline",
                "presence_label": "offline",
                "device_type": None,
                "device_types": [],
            },
        ):
            response = RealtimeTicketView.as_view()(request)

        self.assertEqual(response.status_code, 201)
        claims = self.decode(response.data["ticket"], "test-realtime")
        self.assertEqual(claims["sub"], "user-1")
        self.assertEqual(claims["origin"], "https://app.example.test")
        self.assertEqual(claims["token_use"], "realtime_ticket")
        self.assertEqual(claims["initial_audiences"], [{"kind": "user", "id": "user-1"}])

    @patch("apps.common.realtime_auth.get_support_context")
    def test_ticket_endpoint_rejects_missing_origin(self, support_context):
        support_context.return_value = SimpleNamespace(account=None, agent=None, role="")
        user = SimpleNamespace(id="user-1", pk="user-1", is_authenticated=True)
        request = APIRequestFactory().post("/api/v1/realtime/tickets/", {}, format="json")
        force_authenticate(request, user=user, token={})

        response = RealtimeTicketView.as_view()(request)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "origin_required")

    def test_grant_is_bound_to_actor_origin_and_exact_audience(self):
        request = APIRequestFactory().post(
            "/api/v1/realtime/grants/",
            {},
            format="json",
            HTTP_ORIGIN="https://app.example.test",
        )
        audience = RealtimeAudience(kind="conversation", identifier="conversation-1")

        issued = issue_audience_grant(
            user=SimpleNamespace(id="user-1"),
            audience=audience,
            request=request,
        )

        claims = self.decode(issued.token, "test-realtime-grant")
        self.assertEqual(claims["sub"], "user-1")
        self.assertEqual(claims["origin"], "https://app.example.test")
        self.assertEqual(
            claims["audience_key"],
            {"kind": "conversation", "id": "conversation-1"},
        )

    def test_origin_normalization_removes_default_ports_and_paths(self):
        self.assertEqual(
            normalize_realtime_origin("https://EXAMPLE.test:443/path/"),
            "https://example.test",
        )
        self.assertEqual(normalize_realtime_origin("javascript:alert(1)"), "")

    def test_auth_disabled_fails_closed(self):
        request = APIRequestFactory().post(
            "/api/v1/realtime/grants/",
            {},
            format="json",
            HTTP_ORIGIN="https://app.example.test",
        )
        with override_settings(REALTIME_AUTH_ENABLED=False):
            with self.assertRaises(RealtimeCredentialError) as error:
                issue_audience_grant(
                    user=SimpleNamespace(id="user-1"),
                    audience=RealtimeAudience(kind="user", identifier="user-1"),
                    request=request,
                )
        self.assertEqual(error.exception.status_code, 503)


class RealtimeCallGrantTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        cls.public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def setUp(self):
        realtime_private_key.cache_clear()
        realtime_public_key.cache_clear()
        self.settings_override = override_settings(
            REALTIME_AUTH_ENABLED=True,
            REALTIME_TOKEN_ALGORITHM="RS256",
            REALTIME_TOKEN_ISSUER="test-django",
            REALTIME_CALL_GRANT_AUDIENCE="test-realtime-call-grant",
            REALTIME_CALL_GRANT_TTL_SECONDS=90,
            REALTIME_REQUIRE_ORIGIN=True,
            REALTIME_ALLOWED_ORIGINS=["https://app.example.test"],
            REALTIME_SIGNING_PRIVATE_KEY=self.private_pem,
            REALTIME_SIGNING_PUBLIC_KEY=self.public_pem,
            REALTIME_SIGNING_PRIVATE_KEY_PATH="",
            REALTIME_SIGNING_PUBLIC_KEY_PATH="",
        )
        self.settings_override.enable()
        self.user = User.objects.create_user(username="caller", password="pass")
        self.peer = User.objects.create_user(username="peer", password="pass")
        self.outsider = User.objects.create_user(username="outsider", password="pass")
        self.conversation = Conversation.objects.create(
            type=Conversation.ConversationType.DIRECT,
            created_by=self.user,
        )
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.user)
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.peer)
        self.call = CallSession.objects.create(
            conversation=self.conversation,
            initiated_by=self.user,
            call_type=CallSession.CallType.VOICE,
            status=CallSession.Status.RINGING,
            room_key="realtime-call-grant-test",
        )
        CallParticipant.objects.create(call=self.call, user=self.user)
        CallParticipant.objects.create(call=self.call, user=self.peer)

    def tearDown(self):
        realtime_private_key.cache_clear()
        realtime_public_key.cache_clear()
        self.settings_override.disable()
        super().tearDown()

    def request(self):
        return APIRequestFactory().post(
            "/api/v1/realtime/call-grants/",
            {"call_id": str(self.call.id)},
            format="json",
            HTTP_ORIGIN="https://app.example.test",
        )

    def test_call_grant_is_bound_to_active_call_and_participants(self):
        issued, participant_ids = issue_call_grant(
            user=self.user,
            call_id=self.call.id,
            request=self.request(),
        )
        claims = jwt.decode(
            issued.token,
            self.public_pem,
            algorithms=["RS256"],
            issuer="test-django",
            audience="test-realtime-call-grant",
        )
        self.assertEqual(claims["token_use"], "realtime_call_grant")
        self.assertEqual(claims["sub"], str(self.user.id))
        self.assertEqual(claims["call_id"], str(self.call.id))
        self.assertEqual(claims["conversation_id"], str(self.conversation.id))
        self.assertEqual(set(claims["participant_ids"]), {str(self.user.id), str(self.peer.id)})
        self.assertEqual(set(participant_ids), {str(self.user.id), str(self.peer.id)})

    def test_call_grant_rejects_non_participant_and_ended_call(self):
        with self.assertRaises(RealtimeCredentialError) as outsider_error:
            issue_call_grant(user=self.outsider, call_id=self.call.id, request=self.request())
        self.assertEqual(outsider_error.exception.code, "call_access_denied")

        self.call.status = CallSession.Status.ENDED
        self.call.save(update_fields=["status", "updated_at"])
        with self.assertRaises(RealtimeCredentialError) as ended_error:
            issue_call_grant(user=self.user, call_id=self.call.id, request=self.request())
        self.assertEqual(ended_error.exception.code, "call_access_denied")
