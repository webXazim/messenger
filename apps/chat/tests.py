from io import BytesIO, StringIO
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import AsyncMock, Mock, patch
import json
from PIL import Image

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase, APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from config.throttling import UnsafeUserRateThrottle
from apps.chat.antivirus import AntivirusResult, antivirus_healthcheck
from apps.accounts.models import AuthActionToken, FriendRequest, SocialAccount, UserSession
from apps.chat.models import (
    Conversation,
    ConversationParticipant,
    MessageDelivery,
    MessageEditHistory,
    MessageAttachment,
    MessageReport,
    ModerationAction,
    CallSession,
    CallParticipant,
    Message,
    ChatAuditLog,
    ConversationDraft,
    NotificationPreference,
    PendingUpload,
    UserE2EEDeviceKey,
    UserBlock,
    UserDevice,
)
from apps.chat.services import accept_call, clear_presence, create_direct_conversation, get_presence_snapshot, presence_recipient_ids, send_call_signal, send_message, set_presence, start_call

User = get_user_model()


class PushConfigurationTests(TestCase):
    def test_resolve_firebase_service_account_path_falls_back_to_base_dir_snm(self):
        from apps.chat.push import resolve_firebase_service_account_path

        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            service_account = base_dir / "SNM" / "firebase-service-account.json"
            service_account.parent.mkdir(parents=True, exist_ok=True)
            service_account.write_text("{}", encoding="utf-8")
            with override_settings(
                BASE_DIR=base_dir,
                FIREBASE_SERVICE_ACCOUNT_PATH=r"E:\Framework\django\messenger\SNM\firebase-service-account.json",
            ):
                resolved = resolve_firebase_service_account_path()
        self.assertEqual(resolved, service_account)

    def test_integration_health_snapshot_reports_missing_service_account_as_unconfigured(self):
        from apps.chat.tasks import integration_health_snapshot

        with TemporaryDirectory() as temp_dir:
            with override_settings(
                BASE_DIR=Path(temp_dir),
                FIREBASE_SERVICE_ACCOUNT_PATH="/missing/firebase-service-account.json",
                FIREBASE_PROJECT_ID="sn-messenger-faa15",
            ):
                payload = integration_health_snapshot()
        self.assertFalse(payload["push"]["configured"])
        self.assertEqual(payload["push"]["service_account_path"], "")


class DeployCheckTests(TestCase):
    def test_deploy_checks_require_turn_and_firebase_for_production_calling(self):
        from apps.chat.checks import enterprise_deploy_checks

        with override_settings(
            DEBUG=False,
            SECRET_KEY="x" * 40,
            ALLOWED_HOSTS=["chat.example.com"],
            SECURE_SSL_REDIRECT=True,
            SECURE_HSTS_SECONDS=31536000,
            SESSION_COOKIE_SECURE=True,
            CSRF_COOKIE_SECURE=True,
            TURN_URIS_JSON="",
            TURN_SHARED_SECRET="",
            TURN_STATIC_USERNAME="",
            TURN_STATIC_PASSWORD="",
            FIREBASE_PROJECT_ID="",
            FIREBASE_SERVICE_ACCOUNT_PATH="",
            FCM_DRY_RUN=True,
            CHAT_USE_S3_STORAGE=True,
        ):
            issues = enterprise_deploy_checks(None)

        issue_ids = {issue.id for issue in issues}
        self.assertIn("chat.E006", issue_ids)
        self.assertIn("chat.E007", issue_ids)
        self.assertIn("chat.E009", issue_ids)
        self.assertIn("chat.E010", issue_ids)

    def test_deploy_checks_reject_dev_turn_secret(self):
        from apps.chat.checks import enterprise_deploy_checks

        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            service_account = base_dir / "SNM" / "firebase-service-account.json"
            service_account.parent.mkdir(parents=True, exist_ok=True)
            service_account.write_text("{}", encoding="utf-8")
            with override_settings(
                BASE_DIR=base_dir,
                DEBUG=False,
                SECRET_KEY="x" * 40,
                ALLOWED_HOSTS=["chat.example.com"],
                SECURE_SSL_REDIRECT=True,
                SECURE_HSTS_SECONDS=31536000,
                SESSION_COOKIE_SECURE=True,
                CSRF_COOKIE_SECURE=True,
                TURN_URIS_JSON='["turns:turn.example.com:5349?transport=tcp"]',
                TURN_SHARED_SECRET="dev-turn-shared-secret-change-me",
                FIREBASE_PROJECT_ID="sn-messenger-faa15",
                FIREBASE_SERVICE_ACCOUNT_PATH=str(service_account),
                FCM_DRY_RUN=False,
                CHAT_USE_S3_STORAGE=True,
            ):
                issues = enterprise_deploy_checks(None)

        issue_ids = {issue.id for issue in issues}
        self.assertIn("chat.E011", issue_ids)

    def test_deploy_checks_accept_valid_turn_and_firebase_configuration(self):
        from apps.chat.checks import enterprise_deploy_checks

        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            service_account = base_dir / "SNM" / "firebase-service-account.json"
            service_account.parent.mkdir(parents=True, exist_ok=True)
            service_account.write_text("{}", encoding="utf-8")
            with override_settings(
                BASE_DIR=base_dir,
                DEBUG=False,
                SECRET_KEY="x" * 40,
                ALLOWED_HOSTS=["chat.example.com"],
                SECURE_SSL_REDIRECT=True,
                SECURE_HSTS_SECONDS=31536000,
                SESSION_COOKIE_SECURE=True,
                CSRF_COOKIE_SECURE=True,
                TURN_URIS_JSON='["turns:turn.example.com:5349?transport=tcp"]',
                TURN_SHARED_SECRET="super-secret-turn",
                TURN_STATIC_USERNAME="",
                TURN_STATIC_PASSWORD="",
                FIREBASE_PROJECT_ID="sn-messenger-faa15",
                FIREBASE_SERVICE_ACCOUNT_PATH=str(service_account),
                FCM_DRY_RUN=False,
                CHAT_USE_S3_STORAGE=True,
            ):
                issues = enterprise_deploy_checks(None)

        issue_ids = {issue.id for issue in issues}
        self.assertNotIn("chat.E006", issue_ids)
        self.assertNotIn("chat.E007", issue_ids)
        self.assertNotIn("chat.E008", issue_ids)
        self.assertNotIn("chat.E009", issue_ids)
        self.assertNotIn("chat.E010", issue_ids)

    def test_deploy_checks_reject_non_shared_runtime_backends(self):
        from apps.chat.checks import enterprise_deploy_checks

        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            service_account = base_dir / "SNM" / "firebase-service-account.json"
            service_account.parent.mkdir(parents=True, exist_ok=True)
            service_account.write_text("{}", encoding="utf-8")
            with override_settings(
                BASE_DIR=base_dir,
                DEBUG=False,
                SECRET_KEY="x" * 40,
                ALLOWED_HOSTS=["chat.example.com"],
                SECURE_SSL_REDIRECT=True,
                SECURE_HSTS_SECONDS=31536000,
                SESSION_COOKIE_SECURE=True,
                CSRF_COOKIE_SECURE=True,
                DB_ENGINE="sqlite",
                CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
                CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test"}},
                CELERY_TASK_ALWAYS_EAGER=True,
                EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
                TURN_URIS_JSON='["turns:turn.example.com:5349?transport=tcp"]',
                TURN_SHARED_SECRET="super-secret-turn",
                TURN_STATIC_USERNAME="",
                TURN_STATIC_PASSWORD="",
                FIREBASE_PROJECT_ID="sn-messenger-faa15",
                FIREBASE_SERVICE_ACCOUNT_PATH=str(service_account),
                FCM_DRY_RUN=False,
                CHAT_USE_S3_STORAGE=True,
            ):
                issues = enterprise_deploy_checks(None)

        issue_ids = {issue.id for issue in issues}
        self.assertIn("chat.E012", issue_ids)
        self.assertIn("chat.E013", issue_ids)
        self.assertIn("chat.E014", issue_ids)
        self.assertIn("chat.E015", issue_ids)
        self.assertIn("chat.W007", issue_ids)


class ChatApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="xim", email="xim@example.com", password="pass12345")
        self.other = User.objects.create_user(username="other", email="other@example.com", password="pass12345")
        self.third = User.objects.create_user(username="third", email="third@example.com", password="pass12345")
        self.staff = User.objects.create_user(username="admin", email="admin@example.com", password="pass12345", is_staff=True)
        self.client.force_authenticate(self.user)

    def create_direct_conversation(self):
        response = self.client.post(reverse("conversation-list-create"), {"type": "direct", "participant_ids": [str(self.other.id)]}, format="json")
        self.assertEqual(response.status_code, 200)
        return response.data

    def create_group_conversation(self):
        response = self.client.post(
            reverse("conversation-list-create"),
            {"type": "group", "title": "Builders", "participant_ids": [str(self.other.id), str(self.third.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        return response.data

    def test_direct_conversation_resolves_by_case_insensitive_username(self):
        conversation = self.create_direct_conversation()

        response = self.client.get(reverse("conversation-by-username", kwargs={"username": "OtHeR"}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.data["id"]), str(conversation["id"]))

    def test_direct_conversation_username_route_does_not_create_conversation(self):
        response = self.client.get(reverse("conversation-by-username", kwargs={"username": self.third.username}))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_clean_route_resolves_direct_conversation_without_at_sign(self):
        conversation = self.create_direct_conversation()

        response = self.client.get(reverse("conversation-by-route", kwargs={"route_key": "other"}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.data["id"]), str(conversation["id"]))

    def test_group_receives_unique_name_route(self):
        first = self.create_group_conversation()
        second = self.create_group_conversation()

        self.assertEqual(first["slug"], "builders")
        self.assertEqual(second["slug"], "builders-2")
        response = self.client.get(reverse("conversation-by-route", kwargs={"route_key": first["slug"]}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.data["id"]), str(first["id"]))

    def test_group_unique_name_availability_and_custom_name(self):
        availability_url = reverse("group-name-availability")
        available = self.client.get(availability_url, {"name": "Core Team"})
        self.assertEqual(available.status_code, 200)
        self.assertTrue(available.data["available"])
        self.assertEqual(available.data["normalized"], "core-team")

        created = self.client.post(
            reverse("conversation-list-create"),
            {"type": "group", "title": "Builders", "slug": "core-team", "participant_ids": [str(self.other.id)]},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["slug"], "core-team")
        unavailable = self.client.get(availability_url, {"name": "core-team"})
        self.assertFalse(unavailable.data["available"])

    def test_safe_requests_do_not_consume_global_write_throttle(self):
        cache.clear()

        class OnePerMinuteUnsafeThrottle(UnsafeUserRateThrottle):
            rate = "1/min"

        factory = APIRequestFactory()
        view = Mock()
        safe_request = factory.get("/api/v1/chat/conversations/123/")
        safe_request.user = self.user
        for _ in range(3):
            self.assertTrue(OnePerMinuteUnsafeThrottle().allow_request(safe_request, view))

        unsafe_request = factory.post("/api/v1/chat/conversations/", {}, format="json")
        unsafe_request.user = self.user
        self.assertTrue(OnePerMinuteUnsafeThrottle().allow_request(unsafe_request, view))
        self.assertFalse(OnePerMinuteUnsafeThrottle().allow_request(unsafe_request, view))

    def test_duplicate_pending_friend_request_returns_existing_request(self):
        existing = FriendRequest.objects.create(sender=self.user, receiver=self.other, message="hello")

        response = self.client.post("/api/v1/chat/friends/requests/", {"user_id": self.other.id}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(existing.id))
        self.assertEqual(response.data["status"], FriendRequest.Status.PENDING)
        self.assertEqual(FriendRequest.objects.count(), 1)

    def test_reciprocal_pending_friend_request_returns_existing_request(self):
        existing = FriendRequest.objects.create(sender=self.other, receiver=self.user, message="hello")

        response = self.client.post("/api/v1/chat/friends/requests/", {"user_id": self.other.id}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(existing.id))
        self.assertEqual(str(response.data["sender"]["id"]), str(self.other.id))
        self.assertEqual(str(response.data["receiver"]["id"]), str(self.user.id))
        self.assertEqual(FriendRequest.objects.count(), 1)

    def test_friend_request_accepts_target_user_aliases(self):
        response = self.client.post("/api/v1/chat/friends/requests/", {"receiver_id": self.other.id}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data["receiver"]["id"]), str(self.other.id))
        self.assertTrue(FriendRequest.objects.filter(sender=self.user, receiver=self.other, status=FriendRequest.Status.PENDING).exists())

    @override_settings(CENTRAL_AUTH_ENABLED=True)
    def test_central_jwt_authentication_maps_shadow_user_profile(self):
        from config.authentication import CentralJWTAuthentication

        token_payload = {
            "user_id": "central-123",
            "email": "central-shadow@example.com",
            "username": "central_shadow",
            "display_name": "Central Shadow",
            "email_verified": True,
            "is_staff": False,
            "is_superuser": False,
        }

        shadow = CentralJWTAuthentication().get_user(token_payload)

        self.assertEqual(shadow.email, "central-shadow@example.com")
        self.assertEqual(shadow.username, "central_shadow")
        self.assertTrue(shadow.email_verified)
        self.assertEqual(shadow.profile.display_name, "Central Shadow")



    def test_text_message_entities_and_mentions(self):
        conversation = self.create_direct_conversation()
        response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {
                "text": "Hello @other visit https://example.com",
                "entities": [
                    {"type": "mention", "offset": 6, "length": 6, "user_id": str(self.other.id), "username": "other"},
                    {"type": "link", "offset": 19, "length": 19, "url": "https://example.com"},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["entities"][0]["type"], "mention")
        self.assertIn(str(self.other.id), response.data["mentioned_user_ids"])
        self.assertIn("https://example.com", response.data["links"])

    def test_capabilities_endpoint_exposes_frontend_contract(self):
        self.client.force_authenticate(None)
        response = self.client.get(reverse("chat-capabilities"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["features"]["client_encryption_envelopes"])
        self.assertTrue(response.data["features"]["end_to_end_encryption"])
        self.assertTrue(response.data["features"]["attachments"])
        self.assertFalse(response.data["features"]["server_drafts"])
        self.assertIn("max_upload_bytes", response.data["limits"])
        self.assertIn("allowed_mime_types", response.data["media"])
        self.assertTrue(response.data["security"]["media_requires_signed_tokens"])
        self.assertFalse(response.data["security"]["device_private_keys_server_side"])
        self.assertTrue(response.data["security"]["e2ee_conversations_use_local_drafts_only"])
        self.assertTrue(response.data["security"]["encrypted_message_forwarding_requires_client_reencryption"])
        self.assertFalse(response.data["security"]["call_signaling_persisted_server_side"])
        self.assertNotIn("secret", str(response.data).lower())

    def test_e2ee_device_key_registration_and_list(self):
        payload = {
            "device_id": "browser-main",
            "key_id": "rsa-oaep:browser-main:key-1",
            "label": "Main browser",
            "algorithm": "RSA-OAEP-256",
            "public_key_jwk": {"kty": "RSA", "n": "abc", "e": "AQAB"},
        }
        response = self.client.post(reverse("e2ee-device-keys"), payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["key_id"], payload["key_id"])
        self.assertEqual(response.data["label"], payload["label"])
        self.assertTrue(response.data["fingerprint"])
        self.assertTrue(response.data["security_changed"])
        self.assertEqual(UserE2EEDeviceKey.objects.filter(user=self.user, is_active=True).count(), 1)

        repeat_response = self.client.post(reverse("e2ee-device-keys"), payload, format="json")
        self.assertEqual(repeat_response.status_code, 201)
        self.assertFalse(repeat_response.data["security_changed"])
        self.assertEqual(UserE2EEDeviceKey.objects.filter(user=self.user, is_active=True).count(), 1)

        list_response = self.client.get(reverse("e2ee-device-keys"))
        self.assertEqual(list_response.status_code, 200)
        items = list_response.data.get("results", list_response.data) if isinstance(list_response.data, dict) else list_response.data
        matching = [item for item in items if item["key_id"] == payload["key_id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["device_id"], "browser-main")

    def test_conversation_e2ee_keys_lists_active_participant_keys(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id="rsa-oaep:other-browser:key-1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )
        response = self.client.get(reverse("conversation-e2ee-keys", kwargs={"conversation_id": conversation["id"]}))
        self.assertEqual(response.status_code, 200)
        self.assertIn(str(self.user.id), response.data["participants"])
        self.assertIn(str(self.other.id), response.data["participants"])
        self.assertEqual(response.data["key_version"], 1)
        self.assertFalse(response.data["rekey_required"])
        self.assertEqual(response.data["participants"][str(self.other.id)][0]["key_id"], "rsa-oaep:other-browser:key-1")
        self.assertEqual(response.data["participants"][str(self.other.id)][0]["fingerprint"], "other-fingerprint")

    def test_conversation_e2ee_keys_requires_membership(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        outsider = APIClient()
        outsider.force_authenticate(self.third)

        response = outsider.get(reverse("conversation-e2ee-keys", kwargs={"conversation_id": conversation["id"]}))

        self.assertEqual(response.status_code, 404)

    def test_e2ee_device_key_can_be_revoked(self):
        key = UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        response = self.client.post(reverse("e2ee-device-key-revoke", kwargs={"key_id": key.id}), format="json")
        self.assertEqual(response.status_code, 200)
        key.refresh_from_db()
        self.assertFalse(key.is_active)
        self.assertIsNotNone(key.revoked_at)

    def test_registering_new_device_marks_conversation_for_rekey(self):
        conversation = self.create_direct_conversation()
        response = self.client.post(
            reverse("e2ee-device-keys"),
            {
                "device_id": "browser-main",
                "key_id": "rsa-oaep:browser-main:key-1",
                "algorithm": "RSA-OAEP-256",
                "public_key_jwk": {"kty": "RSA", "n": "abc", "e": "AQAB"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        conversation_detail = self.client.get(reverse("conversation-detail", kwargs={"pk": conversation["id"]}))
        self.assertEqual(conversation_detail.status_code, 200)
        self.assertTrue(conversation_detail.data["e2ee_rekey_required"])
        self.assertEqual(conversation_detail.data["e2ee_key_version"], 2)

    def test_membership_change_marks_group_for_rekey(self):
        conversation = self.create_group_conversation()
        add_response = self.client.post(
            reverse("group-participant-manage", kwargs={"conversation_id": conversation["id"]}),
            {"participant_ids": [str(User.objects.create_user(username="fourth", email="fourth@example.com", password="pass12345").id)]},
            format="json",
        )
        self.assertEqual(add_response.status_code, 200)
        self.assertTrue(add_response.data["e2ee_rekey_required"])
        self.assertEqual(add_response.data["e2ee_key_version"], 2)

    def test_encrypted_message_stores_envelope_without_plaintext(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id=f"user:{self.other.id}:device:1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )
        payload = {
            "is_encrypted": True,
            "encryption": {
                "version": "v1",
                "algorithm": "xchacha20-poly1305",
                "ciphertext": "base64-ciphertext",
                "nonce": "base64-nonce",
                "sender_key_id": "rsa-oaep:self-browser:key-1",
                "sender_device_id": "self-browser",
                "key_version": 1,
                "recipient_key_ids": ["rsa-oaep:self-browser:key-1", f"user:{self.other.id}:device:1"],
                "encrypted_keys": [{"key_id": "rsa-oaep:self-browser:key-1", "wrapped_key": "base64-wrapped-key-self"}, {"key_id": f"user:{self.other.id}:device:1", "wrapped_key": "base64-wrapped-key"}],
                "aad": {"conversation_id": conversation["id"]},
            },
            "client_temp_id": "encrypted-1",
        }
        response = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["text"], "")
        self.assertTrue(response.data["is_encrypted"])
        self.assertEqual(response.data["encryption"]["ciphertext"], "base64-ciphertext")
        wrapped_keys = {item["key_id"]: item["wrapped_key"] for item in response.data["encryption"]["encrypted_keys"]}
        self.assertEqual(wrapped_keys[f"user:{self.other.id}:device:1"], "base64-wrapped-key")
        self.assertEqual(response.data["metadata"]["raw_text"], "")

        message = Message.objects.get(id=response.data["id"])
        self.assertEqual(message.text, "")
        self.assertTrue(message.metadata["encrypted"])

        edit_response = self.client.patch(reverse("message-manage", kwargs={"message_id": response.data["id"]}), {"text": "leak"}, format="json")
        self.assertEqual(edit_response.status_code, 400)

    def test_encrypted_message_with_current_key_version_clears_rekey_required(self):
        conversation = self.create_direct_conversation()
        self.client.post(
            reverse("e2ee-device-keys"),
            {
                "device_id": "self-browser",
                "key_id": "rsa-oaep:self-browser:key-1",
                "algorithm": "RSA-OAEP-256",
                "public_key_jwk": {"kty": "RSA", "n": "abc", "e": "AQAB"},
            },
            format="json",
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id=f"user:{self.other.id}:device:1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )
        conversation_obj = Conversation.objects.get(id=conversation["id"])
        response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {
                "is_encrypted": True,
                "encryption": {
                    "version": "v1",
                    "algorithm": "xchacha20-poly1305",
                    "ciphertext": "base64-ciphertext",
                    "nonce": "base64-nonce",
                    "sender_key_id": "rsa-oaep:self-browser:key-1",
                    "sender_device_id": "self-browser",
                    "key_version": conversation_obj.e2ee_key_version,
                    "recipient_key_ids": ["rsa-oaep:self-browser:key-1", f"user:{self.other.id}:device:1"],
                    "encrypted_keys": [{"key_id": "rsa-oaep:self-browser:key-1", "wrapped_key": "wrapped-self"}, {"key_id": f"user:{self.other.id}:device:1", "wrapped_key": "wrapped-other"}],
                    "aad": {"conversation_id": conversation["id"]},
                },
                "client_temp_id": "encrypted-2",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        conversation_obj.refresh_from_db()
        self.assertFalse(conversation_obj.e2ee_rekey_required)
        self.assertIsNotNone(conversation_obj.e2ee_last_key_rotation_at)

    def test_encrypted_message_rejects_plaintext_text(self):
        conversation = self.create_direct_conversation()
        response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {
                "text": "do not store this",
                "is_encrypted": True,
                "encryption": {
                    "algorithm": "xchacha20-poly1305",
                    "ciphertext": "base64-ciphertext",
                    "nonce": "base64-nonce",
                    "sender_key_id": "rsa-oaep:self-browser:key-1",
                    "recipient_key_ids": [f"user:{self.other.id}:device:1"],
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("text", response.data["errors"])

    def test_encrypted_message_requires_secure_device_for_every_participant(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {
                "is_encrypted": True,
                "encryption": {
                    "version": "v2",
                    "algorithm": "aes-256-gcm+rsa-oaep-256",
                    "ciphertext": "ciphertext",
                    "nonce": "nonce",
                    "sender_key_id": "rsa-oaep:self-browser:key-1",
                    "sender_device_id": "self-browser",
                    "key_version": 1,
                    "recipient_key_ids": ["rsa-oaep:self-browser:key-1"],
                    "encrypted_keys": [{"key_id": "rsa-oaep:self-browser:key-1", "wrapped_key": "wrapped-self"}],
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("e2ee_participant_device_missing", str(response.data))

    def test_encrypted_message_requires_wrapped_key_for_every_active_device(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id=f"user:{self.other.id}:device:1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )
        response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {
                "is_encrypted": True,
                "encryption": {
                    "version": "v2",
                    "algorithm": "aes-256-gcm+rsa-oaep-256",
                    "ciphertext": "ciphertext",
                    "nonce": "nonce",
                    "sender_key_id": "rsa-oaep:self-browser:key-1",
                    "sender_device_id": "self-browser",
                    "key_version": 1,
                    "recipient_key_ids": ["rsa-oaep:self-browser:key-1", f"user:{self.other.id}:device:1"],
                    "encrypted_keys": [{"key_id": "rsa-oaep:self-browser:key-1", "wrapped_key": "wrapped-self"}],
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("e2ee_device_coverage_incomplete", str(response.data))

    def test_encrypted_message_edit_replaces_envelope_without_plaintext(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id=f"user:{self.other.id}:device:1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )
        keys = ["rsa-oaep:self-browser:key-1", f"user:{self.other.id}:device:1"]
        create_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {
                "is_encrypted": True,
                "encryption": {
                    "version": "v2",
                    "algorithm": "aes-256-gcm+rsa-oaep-256",
                    "ciphertext": "ciphertext-before",
                    "nonce": "nonce-before",
                    "sender_key_id": "rsa-oaep:self-browser:key-1",
                    "sender_device_id": "self-browser",
                    "key_version": 1,
                    "recipient_key_ids": keys,
                    "encrypted_keys": [{"key_id": key_id, "wrapped_key": f"wrapped-{index}"} for index, key_id in enumerate(keys)],
                },
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        edit_response = self.client.patch(
            reverse("message-manage", kwargs={"message_id": create_response.data["id"]}),
            {
                "text": "",
                "is_encrypted": True,
                "encryption": {
                    "version": "v2",
                    "algorithm": "aes-256-gcm+rsa-oaep-256",
                    "ciphertext": "ciphertext-after",
                    "nonce": "nonce-after",
                    "sender_key_id": "rsa-oaep:self-browser:key-1",
                    "sender_device_id": "self-browser",
                    "key_version": 1,
                    "recipient_key_ids": keys,
                    "encrypted_keys": [{"key_id": key_id, "wrapped_key": f"rewrapped-{index}"} for index, key_id in enumerate(keys)],
                },
            },
            format="json",
        )
        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(edit_response.data["text"], "")
        self.assertTrue(edit_response.data["is_encrypted"])
        self.assertEqual(edit_response.data["encryption"]["ciphertext"], "ciphertext-after")
        message = Message.objects.get(id=create_response.data["id"])
        self.assertEqual(message.text, "")
        self.assertEqual(message.metadata["raw_text"], "")
        history = message.edit_history.latest("created_at")
        self.assertEqual(history.previous_text, "")
        self.assertEqual(history.new_text, "")

    def test_revoked_e2ee_key_cannot_be_registered_again(self):
        payload = {
            "device_id": "browser-main",
            "key_id": "rsa-oaep:browser-main:key-revoked",
            "label": "Main browser",
            "algorithm": "RSA-OAEP-256",
            "public_key_jwk": {"kty": "RSA", "n": "abc", "e": "AQAB"},
        }
        created = self.client.post(reverse("e2ee-device-keys"), payload, format="json")
        self.assertEqual(created.status_code, 201)
        revoked = self.client.post(reverse("e2ee-device-key-revoke", kwargs={"key_id": created.data["id"]}), format="json")
        self.assertEqual(revoked.status_code, 200)
        response = self.client.post(reverse("e2ee-device-keys"), payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("e2ee_device_key_revoked", str(response.data))

    def test_encrypted_attachment_metadata_is_stored_on_attachment(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id=f"user:{self.other.id}:device:1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )
        encrypted_file = SimpleUploadedFile("encrypted-photo.png", b"ciphertext-bytes", content_type="image/png")
        upload_response = self.client.post(reverse("upload-create"), {"file": encrypted_file, "original_name": "encrypted-photo.png", "mime_type": "image/png"}, format="multipart")
        self.assertEqual(upload_response.status_code, 201)
        response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {
                "attachment_ids": [str(upload_response.data["id"])],
                "attachment_encryption": [
                    {
                        "upload_id": str(upload_response.data["id"]),
                        "version": "v2",
                        "algorithm": "aes-256-gcm+rsa-oaep-256:file",
                        "nonce": "file-nonce",
                        "sender_key_id": "rsa-oaep:self-browser:key-1",
                        "sender_device_id": "self-browser",
                        "key_version": 1,
                        "recipient_key_ids": ["rsa-oaep:self-browser:key-1", f"user:{self.other.id}:device:1"],
                        "encrypted_keys": [
                            {"key_id": "rsa-oaep:self-browser:key-1", "wrapped_key": "wrapped-file-key-self"},
                            {"key_id": f"user:{self.other.id}:device:1", "wrapped_key": "wrapped-file-key"},
                        ],
                        "metadata_ciphertext": "encrypted-manifest",
                        "metadata_nonce": "manifest-nonce",
                        "preview_ciphertext": "encrypted-thumbnail",
                        "preview_nonce": "thumbnail-nonce",
                        "preview_mime_type": "image/jpeg",
                        "aad": {"conversation_id": conversation["id"], "kind": "attachment"},
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["attachments"][0]["is_encrypted"])
        self.assertEqual(response.data["attachments"][0]["encryption"]["metadata_ciphertext"], "encrypted-manifest")
        self.assertEqual(response.data["attachments"][0]["encryption"]["preview_ciphertext"], "encrypted-thumbnail")
        self.assertEqual(response.data["attachments"][0]["encryption"]["preview_nonce"], "thumbnail-nonce")
        self.assertEqual(response.data["attachments"][0]["encryption"]["preview_mime_type"], "image/jpeg")

        attachment = MessageAttachment.objects.get(id=response.data["attachments"][0]["id"])
        self.assertTrue(attachment.metadata["encrypted_attachment"])
        self.assertEqual(attachment.metadata["encryption"]["metadata_nonce"], "manifest-nonce")
        self.assertEqual(attachment.metadata["encryption"]["preview_ciphertext"], "encrypted-thumbnail")

    def test_message_text_is_sanitized(self):
        conversation = self.create_direct_conversation()
        response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "hello   world\r\nsecond line   "},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["text"], "hello world\nsecond line")

    def test_voice_note_transcript_upsert(self):
        good_file = SimpleUploadedFile("voice.ogg", b"voice-bytes", content_type="audio/ogg")
        upload_response = self.client.post(reverse("upload-create"), {"file": good_file}, format="multipart")
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload_response.data["id"])], "is_voice_note": True, "transcript_text": "hello there", "transcript_language_code": "en"},
            format="json",
        ).data
        self.assertTrue(message["voice_note"]["transcript_available"])
        patch_response = self.client.post(
            reverse("message-transcript", kwargs={"message_id": message["id"]}),
            {"text": "hello there updated", "language_code": "en", "status": "completed"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data["transcript"]["text"], "hello there updated")

    def test_voice_note_message_create(self):
        good_file = SimpleUploadedFile("voice.ogg", b"voice-bytes", content_type="audio/ogg")
        upload_response = self.client.post(reverse("upload-create"), {"file": good_file}, format="multipart")
        self.assertEqual(upload_response.status_code, 201)
        conversation = self.create_direct_conversation()
        response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload_response.data["id"])], "is_voice_note": True, "duration_seconds": "3.50", "waveform": [5, 10, 20]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["type"], "audio")
        self.assertTrue(response.data["voice_note"]["is_voice_note"])

    def test_recent_calls_filter(self):
        conversation = self.create_direct_conversation()
        ringing = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")
        self.assertEqual(ringing.status_code, 201)
        response = self.client.get(reverse("call-recent"), {"status": "ringing"})
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 1)

    def test_other_participant_can_accept_ringing_call(self):
        conversation = self.create_direct_conversation()
        ringing = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")
        self.assertEqual(ringing.status_code, 201)

        self.client.force_authenticate(self.other)
        response = self.client.post(reverse("call-accept", kwargs={"call_id": ringing.data["id"]}), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], CallSession.Status.ONGOING)
        self.assertEqual(str(response.data["answered_by"]["id"]), str(self.other.id))
        self.assertEqual(
            CallParticipant.objects.get(call_id=ringing.data["id"], user=self.other).state,
            CallParticipant.State.JOINED,
        )

    def test_call_start_exposes_ringing_timer_and_offline_state(self):
        cache.clear()
        conversation = self.create_direct_conversation()

        response = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["call_state"], "calling_offline")
        self.assertGreaterEqual(response.data["ringing_seconds"], 0)
        self.assertGreater(response.data["ring_timeout_seconds"], 0)
        self.assertEqual(response.data["participant_summary"]["online"], 0)
        self.assertGreaterEqual(response.data["participant_summary"]["offline"], 1)
        remote = next(item for item in response.data["participants"] if str(item["user"]["id"]) == str(self.other.id))
        self.assertFalse(remote["user"]["is_online"])

    def test_call_start_exposes_online_ringing_state(self):
        cache.clear()
        set_presence(self.other, "android-test-device")
        conversation = self.create_direct_conversation()

        response = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["call_state"], "ringing")
        self.assertGreaterEqual(response.data["ringing_seconds"], 0)
        self.assertGreater(response.data["ring_timeout_seconds"], 0)
        self.assertGreaterEqual(response.data["participant_summary"]["online"], 1)
        remote = next(item for item in response.data["participants"] if str(item["user"]["id"]) == str(self.other.id))
        self.assertTrue(remote["user"]["is_online"])
        self.assertEqual(remote["user"]["presence_label"], "online")

    def test_direct_call_end_closes_call_for_both_participants(self):
        conversation = self.create_direct_conversation()
        ringing = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")
        self.assertEqual(ringing.status_code, 201)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(reverse("call-accept", kwargs={"call_id": ringing.data["id"]}), format="json").status_code, 200)

        ended = self.client.post(reverse("call-end", kwargs={"call_id": ringing.data["id"]}), {"reason": "user_left"}, format="json")

        self.assertEqual(ended.status_code, 200)
        self.assertEqual(ended.data["status"], CallSession.Status.ENDED)
        self.assertFalse(CallSession.objects.filter(id=ringing.data["id"], status=CallSession.Status.ONGOING).exists())
        self.client.force_authenticate(self.user)
        fresh = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")
        self.assertEqual(fresh.status_code, 201)
        self.assertNotEqual(fresh.data["id"], ringing.data["id"])

    def test_direct_call_decline_closes_call_and_redial_creates_fresh_session(self):
        conversation = self.create_direct_conversation()
        ringing = self.client.post(
            reverse("call-start", kwargs={"conversation_id": conversation["id"]}),
            {"call_type": "video"},
            format="json",
        )
        self.assertEqual(ringing.status_code, 201)

        self.client.force_authenticate(self.other)
        declined = self.client.post(
            reverse("call-decline", kwargs={"call_id": ringing.data["id"]}),
            {"reason": "declined"},
            format="json",
        )
        self.assertEqual(declined.status_code, 200)
        self.assertEqual(declined.data["status"], CallSession.Status.DECLINED)
        self.assertEqual(
            CallParticipant.objects.get(call_id=ringing.data["id"], user=self.user).state,
            CallParticipant.State.LEFT,
        )

        self.client.force_authenticate(self.user)
        redial = self.client.post(
            reverse("call-start", kwargs={"conversation_id": conversation["id"]}),
            {"call_type": "video"},
            format="json",
        )
        self.assertEqual(redial.status_code, 201)
        self.assertNotEqual(redial.data["id"], ringing.data["id"])
        self.assertEqual(str(redial.data["initiated_by"]["id"]), str(self.user.id))
        remote = next(item for item in redial.data["participants"] if str(item["user"]["id"]) == str(self.other.id))
        self.assertEqual(remote["state"], CallParticipant.State.RINGING)

    def test_direct_call_start_reports_busy_callee(self):
        self.client.force_authenticate(self.third)
        busy_conversation = self.client.post(
            reverse("conversation-list-create"),
            {"type": "direct", "participant_ids": [str(self.other.id)]},
            format="json",
        )
        self.assertEqual(busy_conversation.status_code, 200)
        busy_call = self.client.post(
            reverse("call-start", kwargs={"conversation_id": busy_conversation.data["id"]}),
            {"call_type": "voice"},
            format="json",
        )
        self.assertEqual(busy_call.status_code, 201)

        self.client.force_authenticate(self.user)
        conversation = self.create_direct_conversation()
        blocked = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")

        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data["code"], "callee_busy")
        self.assertIn(str(self.other.id), blocked.data["busy_user_ids"])

        self.client.force_authenticate(self.third)
        self.assertEqual(self.client.post(reverse("call-end", kwargs={"call_id": busy_call.data["id"]}), {"reason": "ended"}, format="json").status_code, 200)
        self.client.force_authenticate(self.user)
        fresh = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")
        self.assertEqual(fresh.status_code, 201)

    def test_caller_cannot_start_a_second_active_call(self):
        first_conversation = self.create_direct_conversation()
        first_call = self.client.post(
            reverse("call-start", kwargs={"conversation_id": first_conversation["id"]}),
            {"call_type": "voice"},
            format="json",
        )
        self.assertEqual(first_call.status_code, 201)

        second_conversation = self.client.post(
            reverse("conversation-list-create"),
            {"type": "direct", "participant_ids": [str(self.third.id)]},
            format="json",
        )
        self.assertEqual(second_conversation.status_code, 200)
        blocked = self.client.post(
            reverse("call-start", kwargs={"conversation_id": second_conversation.data["id"]}),
            {"call_type": "video"},
            format="json",
        )

        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data["code"], "active_call_exists")
        self.assertEqual(str(blocked.data["active_call_id"]), str(first_call.data["id"]))
        self.assertIn(str(self.user.id), blocked.data["busy_user_ids"])

    def test_call_lifecycle_updates_single_chat_system_message(self):
        conversation = self.create_direct_conversation()
        ringing = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")
        self.assertEqual(ringing.status_code, 201)

        started_message = Message.objects.filter(conversation_id=conversation["id"], type=Message.MessageType.SYSTEM).latest("created_at")
        self.assertEqual(started_message.metadata["system_event"], "call")
        self.assertEqual(started_message.metadata["call_outcome"], "ringing")
        self.assertEqual(started_message.text, "Outgoing call")

        self.client.force_authenticate(self.other)
        accepted = self.client.post(reverse("call-accept", kwargs={"call_id": ringing.data["id"]}), format="json")
        self.assertEqual(accepted.status_code, 200)

        accepted_message = Message.objects.filter(conversation_id=conversation["id"], type=Message.MessageType.SYSTEM).latest("created_at")
        self.assertEqual(accepted_message.id, started_message.id)
        self.assertEqual(accepted_message.metadata["call_outcome"], "received")
        self.assertEqual(accepted_message.text, "Call connected")

        CallSession.objects.filter(id=ringing.data["id"]).update(answered_at=timezone.now() - timedelta(seconds=125))
        ended = self.client.post(reverse("call-end", kwargs={"call_id": ringing.data["id"]}), {"reason": "user_left"}, format="json")
        self.assertEqual(ended.status_code, 200)

        ended_message = Message.objects.filter(conversation_id=conversation["id"], type=Message.MessageType.SYSTEM).latest("created_at")
        self.assertEqual(ended_message.id, started_message.id)
        self.assertEqual(ended_message.metadata["call_outcome"], "completed")
        self.assertGreaterEqual(int(ended_message.metadata["duration_seconds"]), 125)
        self.assertIn("Call ended", ended_message.text)

    def test_expired_ringing_call_updates_chat_with_missed_summary(self):
        from apps.chat.tasks import expire_stale_calls

        conversation = self.create_direct_conversation()
        call = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json").data
        CallSession.objects.filter(id=call["id"]).update(started_at=timezone.now() - timedelta(seconds=120), status=CallSession.Status.RINGING)
        expire_stale_calls()

        event_message = Message.objects.filter(conversation_id=conversation["id"], type=Message.MessageType.SYSTEM).latest("created_at")
        self.assertEqual(event_message.metadata["call_outcome"], "missed")
        self.assertEqual(event_message.text, "Missed call")

    def test_group_admin_can_mute_and_ban_participant(self):
        conversation = self.create_group_conversation()
        mute = self.client.post(reverse("group-participant-mute", kwargs={"conversation_id": conversation["id"], "user_id": self.other.id}), {"minutes": 30}, format="json")
        self.assertEqual(mute.status_code, 200)
        participant = ConversationParticipant.objects.get(conversation_id=conversation["id"], user=self.other)
        self.assertIsNotNone(participant.moderation_muted_until)
        ban = self.client.post(reverse("group-participant-ban", kwargs={"conversation_id": conversation["id"], "user_id": self.third.id}), {"reason": "spam"}, format="json")
        self.assertEqual(ban.status_code, 200)
        participant = ConversationParticipant.objects.get(conversation_id=conversation["id"], user=self.third)
        self.assertIsNotNone(participant.banned_at)
        unban = self.client.delete(reverse("group-participant-ban", kwargs={"conversation_id": conversation["id"], "user_id": self.third.id}))
        self.assertEqual(unban.status_code, 200)

    def test_message_fail_and_retry(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "retry me"}, format="json").data
        fail = self.client.post(reverse("message-fail", kwargs={"message_id": message["id"]}), {"reason": "network_timeout"}, format="json")
        self.assertEqual(fail.status_code, 200)
        self.assertEqual(fail.data["delivery_status"], "failed")
        retry = self.client.post(reverse("message-retry", kwargs={"message_id": message["id"]}), format="json")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["delivery_status"], "sent")
        self.assertEqual(retry.data["retry_count"], 1)

    def test_archiving_last_retained_direct_chat_deletes_conversation_and_media_files(self):
        conversation = self.create_direct_conversation()
        with TemporaryDirectory() as temp_dir:
            with override_settings(
                PRIVATE_MEDIA_ROOT=temp_dir,
                PRIVATE_MEDIA_URL="/private-media/",
                CHAT_USE_S3_STORAGE=False,
            ):
                message = Message.objects.create(
                    conversation=Conversation.objects.get(id=conversation["id"]),
                    sender=self.user,
                    type=Message.MessageType.IMAGE,
                    text="photo",
                )
                attachment = MessageAttachment.objects.create(
                    message=message,
                    file=SimpleUploadedFile("photo.jpg", b"image-bytes", content_type="image/jpeg"),
                    original_name="photo.jpg",
                    media_kind=MessageAttachment.MediaKind.IMAGE,
                    mime_type="image/jpeg",
                    size=11,
                )
                attachment.thumbnail.save(
                    "photo-thumb.jpg",
                    SimpleUploadedFile("photo-thumb.jpg", b"thumb-bytes", content_type="image/jpeg"),
                    save=True,
                )
                file_name = attachment.file.name
                thumbnail_name = attachment.thumbnail.name
                self.assertTrue(attachment.file.storage.exists(file_name))
                self.assertTrue(attachment.thumbnail.storage.exists(thumbnail_name))

                first_archive = self.client.post(
                    reverse("conversation-archive", kwargs={"conversation_id": conversation["id"]}),
                    format="json",
                )
                self.assertEqual(first_archive.status_code, 200)
                self.assertTrue(Conversation.objects.filter(id=conversation["id"]).exists())

                self.client.force_authenticate(self.other)
                second_archive = self.client.post(
                    reverse("conversation-archive", kwargs={"conversation_id": conversation["id"]}),
                    format="json",
                )
                self.assertEqual(second_archive.status_code, 200)

                self.assertFalse(Conversation.objects.filter(id=conversation["id"]).exists())
                self.assertFalse(attachment.file.storage.exists(file_name))
                self.assertFalse(attachment.thumbnail.storage.exists(thumbnail_name))

    def test_conversation_list_includes_unread_count(self):
        conversation = self.create_direct_conversation()
        self.client.force_authenticate(self.other)
        self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "unread one"}, format="json")
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse("conversation-list-create"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["unread_count"], 1)

    def test_message_reaction_summary_is_exposed(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "react to me"}, format="json").data
        self.client.post(reverse("message-reactions", kwargs={"message_id": message["id"]}), {"emoji": "🔥"}, format="json")
        self.client.post(reverse("message-reactions", kwargs={"message_id": message["id"]}), {"emoji": "❤️"}, format="json")
        self.client.force_authenticate(self.other)
        self.client.post(reverse("message-reactions", kwargs={"message_id": message["id"]}), {"emoji": "🔥"}, format="json")
        detail = self.client.get(reverse("message-detail", kwargs={"pk": message["id"]}))
        self.assertEqual(detail.status_code, 200)
        summary = {item["emoji"]: item["count"] for item in detail.data["reaction_summary"]}
        self.assertEqual(summary["🔥"], 1)
        self.assertEqual(summary["❤️"], 1)
        self.assertEqual(len([reaction for reaction in detail.data["reactions"] if reaction["user"]["id"] == str(self.user.id)]), 1)

    def test_forward_message_to_another_conversation(self):
        direct = self.create_direct_conversation()
        group = self.create_group_conversation()
        message = self.client.post(reverse("message-list-create", kwargs={"conversation_id": direct["id"]}), {"text": "forward me"}, format="json").data
        response = self.client.post(reverse("message-forward", kwargs={"message_id": message["id"]}), {"conversation_id": group["id"]}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["text"], "forward me")
        self.assertEqual(response.data["forwarded_from"], message["id"])
        edit_response = self.client.patch(
            reverse("message-manage", kwargs={"message_id": message["id"]}),
            {"text": "changed after forwarding"},
            format="json",
        )
        self.assertEqual(edit_response.status_code, 400)
        self.assertEqual(edit_response.data["errors"]["code"], "message_was_forwarded")

    def test_encrypted_message_forwarding_requires_client_reencryption(self):
        direct = self.create_direct_conversation()
        group = self.create_group_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id=f"user:{self.other.id}:device:1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": direct["id"]}),
            {
                "is_encrypted": True,
                "encryption": {
                    "version": "v1",
                    "algorithm": "xchacha20-poly1305",
                    "ciphertext": "base64-ciphertext",
                    "nonce": "base64-nonce",
                    "sender_key_id": "rsa-oaep:self-browser:key-1",
                    "sender_device_id": "self-browser",
                    "key_version": 1,
                    "recipient_key_ids": ["rsa-oaep:self-browser:key-1", f"user:{self.other.id}:device:1"],
                    "encrypted_keys": [
                        {"key_id": "rsa-oaep:self-browser:key-1", "wrapped_key": "wrapped-self"},
                        {"key_id": f"user:{self.other.id}:device:1", "wrapped_key": "wrapped-other"},
                    ],
                },
            },
            format="json",
        ).data
        response = self.client.post(reverse("message-forward", kwargs={"message_id": message["id"]}), {"conversation_id": group["id"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("re-encrypted client-side", str(response.data))

    def test_expire_stale_calls_marks_call_missed(self):
        from apps.chat.tasks import expire_stale_calls
        conversation = self.create_direct_conversation()
        call = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json").data
        CallSession.objects.filter(id=call["id"]).update(started_at=timezone.now() - timedelta(seconds=120), status=CallSession.Status.RINGING)
        expired = expire_stale_calls()
        self.assertGreaterEqual(expired, 1)
        self.assertEqual(CallSession.objects.get(id=call["id"]).status, CallSession.Status.MISSED)

    def test_start_call_rejects_self_only_conversation(self):
        conversation = Conversation.objects.create(type=Conversation.ConversationType.DIRECT)
        ConversationParticipant.objects.create(conversation=conversation, user=self.user)
        response = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation.id}), {"call_type": "voice"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one other", str(response.data))

    def test_recent_calls_expires_stale_ringing_before_response(self):
        conversation = self.create_direct_conversation()
        call = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json").data
        CallSession.objects.filter(id=call["id"]).update(started_at=timezone.now() - timedelta(seconds=120), status=CallSession.Status.RINGING)
        response = self.client.get(reverse("call-recent"))
        self.assertEqual(response.status_code, 200)
        refreshed = CallSession.objects.get(id=call["id"])
        self.assertEqual(refreshed.status, CallSession.Status.MISSED)
        self.assertEqual(response.data["results"][0]["status"], CallSession.Status.MISSED)

    def test_message_search(self):
        conversation = self.create_direct_conversation()
        self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "project falcon ready"}, format="json")
        response = self.client.get(reverse("message-search"), {"q": "falcon"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["text"], "project falcon ready")

    def test_conversation_draft_can_be_saved_loaded_and_cleared(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "anchor"},
            format="json",
        ).data

        initial = self.client.get(reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}))
        self.assertEqual(initial.status_code, 200)
        self.assertFalse(initial.data["has_draft"])
        self.assertEqual(initial.data["text"], "")

        saved = self.client.patch(
            reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}),
            {"text": "reply later", "reply_to_id": message["id"], "metadata": {"composer_mode": "reply"}},
            format="json",
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.data["has_draft"])
        self.assertEqual(saved.data["text"], "reply later")
        self.assertEqual(saved.data["reply_to"]["id"], message["id"])
        self.assertEqual(saved.data["metadata"]["composer_mode"], "reply")
        self.assertTrue(ConversationDraft.objects.filter(conversation_id=conversation["id"], user=self.user).exists())

        loaded = self.client.get(reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}))
        self.assertEqual(loaded.status_code, 200)
        self.assertTrue(loaded.data["has_draft"])
        self.assertEqual(loaded.data["text"], "reply later")

        cleared = self.client.patch(
            reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}),
            {"text": "", "reply_to_id": None, "metadata": {}},
            format="json",
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(cleared.data["has_draft"])
        self.assertFalse(ConversationDraft.objects.filter(conversation_id=conversation["id"], user=self.user).exists())

    def test_conversation_draft_reply_target_must_belong_to_same_conversation(self):
        conversation = self.create_direct_conversation()
        other_conversation = self.create_group_conversation()
        other_message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": other_conversation["id"]}),
            {"text": "elsewhere"},
            format="json",
        ).data

        response = self.client.patch(
            reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}),
            {"text": "bad reply target", "reply_to_id": other_message["id"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("reply_to_id", response.data["errors"])

    def test_conversation_list_and_detail_include_current_user_draft(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "context"},
            format="json",
        ).data
        self.client.patch(
            reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}),
            {"text": "finish this later", "reply_to_id": message["id"], "metadata": {"source": "composer"}},
            format="json",
        )

        listing = self.client.get(reverse("conversation-list-create"))
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data["results"][0]["draft"]["text"], "finish this later")
        self.assertEqual(listing.data["results"][0]["draft"]["reply_to"]["id"], message["id"])

        detail = self.client.get(reverse("conversation-detail", kwargs={"pk": conversation["id"]}))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["draft"]["text"], "finish this later")
        self.assertEqual(detail.data["draft"]["metadata"]["source"], "composer")

    def test_e2ee_conversation_draft_storage_is_rejected_and_hidden(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id=f"user:{self.other.id}:device:1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )

        blocked = self.client.patch(
            reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}),
            {"text": "keep this local"},
            format="json",
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.data["code"], "e2ee_local_drafts_only")
        self.assertFalse(ConversationDraft.objects.filter(conversation_id=conversation["id"], user=self.user).exists())

        hidden = ConversationDraft.objects.create(
            conversation_id=conversation["id"],
            user=self.user,
            text="legacy plaintext draft",
            metadata={"source": "server"},
        )

        fetched = self.client.get(reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}))
        self.assertEqual(fetched.status_code, 200)
        self.assertFalse(fetched.data["has_draft"])
        self.assertEqual(fetched.data["text"], "")

        listing = self.client.get(reverse("conversation-list-create"))
        self.assertEqual(listing.status_code, 200)
        self.assertIsNone(listing.data["results"][0]["draft"])

        detail = self.client.get(reverse("conversation-detail", kwargs={"pk": conversation["id"]}))
        self.assertEqual(detail.status_code, 200)
        self.assertIsNone(detail.data["draft"])

        hidden.delete()

    def test_block_user_prevents_direct_conversation(self):
        block_response = self.client.post(reverse("block-list-create"), {"blocked_user_id": str(self.other.id), "reason": "spam"}, format="json")
        self.assertEqual(block_response.status_code, 201)
        self.assertTrue(UserBlock.objects.filter(blocker=self.user, blocked=self.other).exists())
        response = self.client.post(reverse("conversation-list-create"), {"type": "direct", "participant_ids": [str(self.other.id)]}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_report_message(self):
        conversation = self.create_direct_conversation()
        message_response = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "bad message"}, format="json")
        report_response = self.client.post(reverse("message-report", kwargs={"message_id": message_response.data["id"]}), {"reason": "spam", "details": "unsolicited"}, format="json")
        self.assertEqual(report_response.status_code, 201)
        self.assertTrue(MessageReport.objects.filter(message_id=message_response.data["id"], reporter=self.user, reason="spam").exists())

    def test_device_register_and_deactivate(self):
        register_response = self.client.post(reverse("device-list-create"), {"platform": "android", "push_token": "fcm-token-1"}, format="json")
        self.assertEqual(register_response.status_code, 201)
        self.assertTrue(UserDevice.objects.filter(user=self.user, push_token="fcm-token-1", is_active=True).exists())
        deactivate_response = self.client.post(reverse("device-deactivate"), {"push_token": "fcm-token-1"}, format="json")
        self.assertEqual(deactivate_response.status_code, 200)
        self.assertFalse(UserDevice.objects.get(user=self.user, push_token="fcm-token-1").is_active)

    def test_message_edit_history(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "draft"}, format="json").data
        self.assertTrue(message["can_edit"])
        self.assertTrue(message["edit_deadline"])
        response = self.client.patch(reverse("message-manage", kwargs={"message_id": message["id"]}), {"text": "final"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_edited"])
        self.assertTrue(MessageEditHistory.objects.filter(message_id=message["id"], previous_text="draft", new_text="final").exists())

    def test_message_edit_is_locked_after_window_expires(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "too old to change"},
            format="json",
        ).data
        Message.objects.filter(id=message["id"]).update(created_at=timezone.now() - timedelta(minutes=16))

        response = self.client.patch(reverse("message-manage", kwargs={"message_id": message["id"]}), {"text": "changed"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["errors"]["code"], "edit_window_expired")
        self.assertEqual(Message.objects.get(id=message["id"]).text, "too old to change")

    def test_message_edit_is_locked_after_reaction(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "engaged message"},
            format="json",
        ).data
        self.client.force_authenticate(self.other)
        self.client.post(reverse("message-reactions", kwargs={"message_id": message["id"]}), {"emoji": "👍"}, format="json")
        self.client.delete(reverse("message-reactions", kwargs={"message_id": message["id"]}), {"emoji": "👍"}, format="json")
        self.client.force_authenticate(self.user)

        response = self.client.patch(reverse("message-manage", kwargs={"message_id": message["id"]}), {"text": "changed"}, format="json")
        detail = self.client.get(reverse("message-detail", kwargs={"pk": message["id"]}))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["errors"]["code"], "message_has_reactions")
        self.assertFalse(detail.data["can_edit"])
        self.assertIn("reacted", detail.data["edit_locked_reason"])

    def test_message_edit_is_locked_after_reply(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "reply target"},
            format="json",
        ).data
        self.client.force_authenticate(self.other)
        self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "a reply", "reply_to_id": message["id"]},
            format="json",
        )
        self.client.force_authenticate(self.user)

        response = self.client.patch(reverse("message-manage", kwargs={"message_id": message["id"]}), {"text": "changed"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["errors"]["code"], "message_has_replies")

    def test_message_edit_history_endpoint_returns_revisions(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "draft"},
            format="json",
        ).data
        self.client.patch(reverse("message-manage", kwargs={"message_id": message["id"]}), {"text": "final"}, format="json")

        response = self.client.get(reverse("message-edit-history", kwargs={"message_id": message["id"]}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["previous_text"], "draft")
        self.assertEqual(response.data[0]["new_text"], "final")
        self.assertEqual(response.data[0]["edited_by"]["id"], str(self.user.id))

    def test_message_edit_history_endpoint_requires_conversation_access(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "private edit"},
            format="json",
        ).data
        self.client.patch(reverse("message-manage", kwargs={"message_id": message["id"]}), {"text": "private final"}, format="json")

        outsider = APIClient()
        outsider.force_authenticate(User.objects.create_user(username="outsider", email="outsider@example.com", password="pass12345"))
        response = outsider.get(reverse("message-edit-history", kwargs={"message_id": message["id"]}))
        self.assertEqual(response.status_code, 404)

    def test_notification_preferences_update(self):
        response = self.client.get(reverse("notification-preferences"))
        self.assertEqual(response.status_code, 200)
        patch_response = self.client.patch(reverse("notification-preferences"), {"push_enabled": False, "mute_all": True}, format="json")
        self.assertEqual(patch_response.status_code, 200)
        self.assertTrue(NotificationPreference.objects.get(user=self.user).mute_all)

    def test_sync_endpoint(self):
        conversation = self.create_direct_conversation()
        self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "sync me"}, format="json")
        response = self.client.get(reverse("chat-sync"))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["conversations"]), 1)
        self.assertGreaterEqual(len(response.data["messages"]), 1)
        self.assertIn("drafts", response.data)
        self.assertIn("next_since", response.data)
        self.assertIn("has_more_conversations", response.data)
        self.assertIn("has_more_messages", response.data)
        self.assertIn("has_more_drafts", response.data)
        self.assertIn("active_calls", response.data)

    def test_sync_endpoint_includes_drafts_even_without_updated_conversations(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "sync draft context"},
            format="json",
        ).data
        self.client.patch(
            reverse("conversation-draft", kwargs={"conversation_id": conversation["id"]}),
            {"text": "draft in sync", "reply_to_id": message["id"]},
            format="json",
        )

        response = self.client.get(reverse("chat-sync"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["drafts"]), 1)
        self.assertEqual(response.data["drafts"][0]["conversation"], conversation["id"])
        self.assertEqual(response.data["drafts"][0]["text"], "draft in sync")
        self.assertFalse(response.data["has_more_drafts"])

    def test_sync_omits_server_drafts_for_e2ee_conversations(self):
        conversation = self.create_direct_conversation()
        UserE2EEDeviceKey.objects.create(
            user=self.user,
            device_id="self-browser",
            key_id="rsa-oaep:self-browser:key-1",
            fingerprint="self-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "self", "e": "AQAB"},
        )
        UserE2EEDeviceKey.objects.create(
            user=self.other,
            device_id="other-browser",
            key_id=f"user:{self.other.id}:device:1",
            fingerprint="other-fingerprint",
            algorithm="RSA-OAEP-256",
            public_key_jwk={"kty": "RSA", "n": "other", "e": "AQAB"},
        )
        ConversationDraft.objects.create(
            conversation_id=conversation["id"],
            user=self.user,
            text="legacy plaintext draft",
            metadata={"source": "server"},
        )

        response = self.client.get(reverse("chat-sync"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["drafts"], [])
        self.assertFalse(response.data["has_more_drafts"])

    def test_sync_includes_active_calls(self):
        conversation = self.create_direct_conversation()
        call = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")
        self.assertEqual(call.status_code, 201)
        response = self.client.get(reverse("chat-sync"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["active_calls"]), 1)
        self.assertEqual(response.data["active_calls"][0]["id"], call.data["id"])

    def test_duplicate_message_spam_is_blocked(self):
        conversation = self.create_direct_conversation()
        url = reverse("message-list-create", kwargs={"conversation_id": conversation["id"]})
        for _ in range(3):
            response = self.client.post(url, {"text": "buy now"}, format="json")
            self.assertEqual(response.status_code, 201)
        blocked = self.client.post(url, {"text": "buy now"}, format="json")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Duplicate messages", str(blocked.data))

    def test_message_with_too_many_links_is_blocked(self):
        conversation = self.create_direct_conversation()
        url = reverse("message-list-create", kwargs={"conversation_id": conversation["id"]})
        text = " ".join(f"https://example{i}.com" for i in range(6))
        response = self.client.post(url, {"text": text}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Too many links", str(response.data))

    def test_message_auto_hidden_after_multiple_reports(self):
        fourth = User.objects.create_user(username="fourth", email="fourth@example.com", password="pass12345")
        conversation = self.client.post(
            reverse("conversation-list-create"),
            {"type": "group", "title": "Reports", "participant_ids": [str(self.other.id), str(self.third.id), str(fourth.id)]},
            format="json",
        ).data
        self.client.force_authenticate(self.other)
        message = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"text": "reportable"},
            format="json",
        ).data
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.post(reverse("message-report", kwargs={"message_id": message["id"]}), {"reason": "spam"}, format="json").status_code,
            201,
        )
        self.client.force_authenticate(self.third)
        self.assertEqual(
            self.client.post(reverse("message-report", kwargs={"message_id": message["id"]}), {"reason": "harassment"}, format="json").status_code,
            201,
        )
        self.client.force_authenticate(fourth)
        third_report = self.client.post(reverse("message-report", kwargs={"message_id": message["id"]}), {"reason": "hate"}, format="json")
        self.assertEqual(third_report.status_code, 201)
        flagged = Message.objects.get(id=message["id"])
        self.assertTrue(flagged.is_deleted)
        self.assertTrue(ModerationAction.objects.filter(message=flagged, action_type="hide_message").exists())
        self.client.force_authenticate(self.user)

    def test_staff_moderation_resolve_report(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "abuse"}, format="json").data
        report = self.client.post(reverse("message-report", kwargs={"message_id": message["id"]}), {"reason": "harassment"}, format="json").data
        self.client.force_authenticate(self.staff)
        response = self.client.post(reverse("moderation-report-resolve", kwargs={"report_id": report["id"]}), {"notes": "handled", "hide_message": True}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ModerationAction.objects.filter(report_id=report["id"]).exists())

    def test_upload_scan_blocks_executable(self):
        bad_file = SimpleUploadedFile("danger.exe", b"MZ-test", content_type="application/x-msdownload")
        response = self.client.post(reverse("upload-create"), {"file": bad_file}, format="multipart")
        self.assertEqual(response.status_code, 201)
        upload = PendingUpload.objects.get(id=response.data["id"])
        self.assertEqual(upload.scan_status, PendingUpload.ScanStatus.INFECTED)
        self.assertEqual(upload.status, PendingUpload.UploadStatus.REJECTED)

    def test_upload_scan_allows_clean_file_and_message_attach(self):
        good_file = SimpleUploadedFile("note.txt", b"hello world", content_type="text/plain")
        upload_response = self.client.post(reverse("upload-create"), {"file": good_file}, format="multipart")
        self.assertEqual(upload_response.status_code, 201)
        upload = PendingUpload.objects.get(id=upload_response.data["id"])
        self.assertEqual(upload.scan_status, PendingUpload.ScanStatus.CLEAN)
        conversation = self.create_direct_conversation()
        message_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload.id)]},
            format="json",
        )
        self.assertEqual(message_response.status_code, 201)
        self.assertEqual(message_response.data["type"], "file")
        self.assertEqual(len(message_response.data["attachments"]), 1)

    def test_upload_allows_common_document_and_spreadsheet_files(self):
        examples = [
            ("contacts.csv", b"name,email\nUser,user@example.com\n", "text/csv"),
            ("brief.docx", b"fake-docx-bytes", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("sheet.xlsx", b"fake-xlsx-bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("slides.pptx", b"fake-pptx-bytes", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ]
        for name, content, content_type in examples:
            with self.subTest(name=name):
                upload_response = self.client.post(
                    reverse("upload-create"),
                    {"file": SimpleUploadedFile(name, content, content_type=content_type)},
                    format="multipart",
                )
                self.assertEqual(upload_response.status_code, 201)
                self.assertEqual(upload_response.data["media_kind"], PendingUpload.MediaKind.FILE)

    def test_upload_normalizes_legacy_pdf_media_kind_to_file(self):
        upload_response = self.client.post(
            reverse("upload-create"),
            {
                "file": SimpleUploadedFile(
                    "document.pdf",
                    b"%PDF-1.4\n%%EOF\n",
                    content_type="application/pdf",
                ),
                "media_kind": "pdf",
            },
            format="multipart",
        )

        self.assertEqual(upload_response.status_code, 201)
        self.assertEqual(upload_response.data["media_kind"], PendingUpload.MediaKind.FILE)

    @patch("apps.chat.api.views.dispatch_pending_upload_scan")
    @override_settings(UPLOAD_SCAN_ASYNC=True)
    def test_upload_endpoint_dispatches_async_scan_when_enabled(self, mock_dispatch_pending_upload_scan):
        response = self.client.post(
            reverse("upload-create"),
            {"file": SimpleUploadedFile("note.txt", b"hello world", content_type="text/plain")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(mock_dispatch_pending_upload_scan.call_count, 1)
        upload = mock_dispatch_pending_upload_scan.call_args.args[0]
        self.assertEqual(str(upload.id), response.data["id"])

    @patch("apps.chat.services.dispatch_message_notifications")
    def test_message_send_scans_pending_upload_before_attaching(self, mock_dispatch_message_notifications):
        with TemporaryDirectory() as temp_dir:
            storages = [
                PendingUpload._meta.get_field("file").storage,
                PendingUpload._meta.get_field("thumbnail").storage,
                MessageAttachment._meta.get_field("file").storage,
                MessageAttachment._meta.get_field("thumbnail").storage,
            ]
            originals = [(storage.location, storage.base_url) for storage in storages]
            try:
                for storage in storages:
                    storage.location = temp_dir
                    storage.base_url = "/private-media/"
                with override_settings(PRIVATE_MEDIA_ROOT=temp_dir, PRIVATE_MEDIA_URL="/private-media/", CHAT_USE_S3_STORAGE=False):
                    upload = PendingUpload.objects.create(
                        user=self.user,
                        file=SimpleUploadedFile("note.txt", b"hello world", content_type="text/plain"),
                        original_name="note.txt",
                        media_kind=PendingUpload.MediaKind.FILE,
                        mime_type="text/plain",
                        size=11,
                    )
                    self.assertEqual(upload.scan_status, PendingUpload.ScanStatus.PENDING)

                    conversation = create_direct_conversation(self.user, self.other)
                    message = send_message(self.user, conversation, attachment_ids=[str(upload.id)])

                    upload.refresh_from_db()
                    self.assertEqual(upload.scan_status, PendingUpload.ScanStatus.CLEAN)
                    self.assertEqual(upload.status, PendingUpload.UploadStatus.ATTACHED)
                    self.assertEqual(message.attachments.count(), 1)
            finally:
                for storage, (location, base_url) in zip(storages, originals):
                    storage.location = location
                    storage.base_url = base_url

    def test_upload_endpoint_persists_media_metadata_and_thumbnail(self):
        video_file = SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4")
        thumbnail_file = SimpleUploadedFile(
            "clip-thumb.gif",
            (
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
                b"\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        response = self.client.post(
            reverse("upload-create"),
            {
                "file": video_file,
                "mime_type": "video/mp4",
                "media_kind": "video",
                "width": "1080",
                "height": "1920",
                "rotation": "90",
                "duration_seconds": "5.20",
                "thumbnail": thumbnail_file,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["media_kind"], "video")
        self.assertEqual(response.data["width"], 1080)
        self.assertEqual(response.data["height"], 1920)
        self.assertEqual(response.data["rotation"], 90)
        self.assertEqual(str(response.data["duration_seconds"]), "5.20")
        self.assertAlmostEqual(float(response.data["aspect_ratio"]), 1080 / 1920, places=4)
        self.assertIn("/thumbnail/", response.data["thumbnail_url"])
        self.assertIn("/download/", response.data["download_url"])

        upload = PendingUpload.objects.get(id=response.data["id"])
        self.assertEqual(upload.media_kind, "video")
        self.assertEqual(upload.width, 1080)
        self.assertEqual(upload.height, 1920)
        self.assertEqual(upload.rotation, 90)
        self.assertEqual(str(upload.duration_seconds), "5.20")
        self.assertTrue(bool(upload.thumbnail))

    def test_message_attach_preserves_declared_media_metadata_and_thumbnail(self):
        video_file = SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4")
        thumbnail_file = SimpleUploadedFile(
            "clip-thumb.gif",
            (
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
                b"\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        upload_response = self.client.post(
            reverse("upload-create"),
            {
                "file": video_file,
                "mime_type": "video/mp4",
                "media_kind": "video",
                "width": "1080",
                "height": "1920",
                "rotation": "90",
                "duration_seconds": "5.20",
                "thumbnail": thumbnail_file,
            },
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, 201)
        conversation = self.create_direct_conversation()

        message_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload_response.data["id"])]},
            format="json",
        )

        self.assertEqual(message_response.status_code, 201)
        self.assertEqual(message_response.data["type"], "video")
        attachment = message_response.data["attachments"][0]
        self.assertEqual(attachment["media_kind"], "video")
        self.assertEqual(attachment["width"], 1080)
        self.assertEqual(attachment["height"], 1920)
        self.assertEqual(attachment["rotation"], 90)
        self.assertEqual(str(attachment["duration_seconds"]), "5.20")
        self.assertAlmostEqual(float(attachment["aspect_ratio"]), 1080 / 1920, places=4)
        self.assertIn("/thumbnail/", attachment["thumbnail_url"])
        self.assertIn("/download/", attachment["download_url"])

        thumbnail_response = self.client.get(reverse("attachment-thumbnail", kwargs={"attachment_id": attachment["id"]}))
        self.assertEqual(thumbnail_response.status_code, 200)

        db_attachment = MessageAttachment.objects.get(id=attachment["id"])
        self.assertEqual(db_attachment.media_kind, "video")
        self.assertEqual(db_attachment.width, 1080)
        self.assertEqual(db_attachment.height, 1920)
        self.assertEqual(db_attachment.rotation, 90)
        self.assertEqual(str(db_attachment.duration_seconds), "5.20")
        self.assertTrue(bool(db_attachment.thumbnail))

    def test_conversation_list_includes_last_message_attachment_layout_metadata(self):
        video_file = SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4")
        thumbnail_file = SimpleUploadedFile(
            "clip-thumb.gif",
            (
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
                b"\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        upload_response = self.client.post(
            reverse("upload-create"),
            {
                "file": video_file,
                "mime_type": "video/mp4",
                "media_kind": "video",
                "width": "1080",
                "height": "1920",
                "rotation": "90",
                "duration_seconds": "5.20",
                "thumbnail": thumbnail_file,
                "metadata": json.dumps({"display_width": 1920, "display_height": 1080}),
            },
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, 201)
        conversation = self.create_direct_conversation()
        message_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload_response.data["id"])]},
            format="json",
        )
        self.assertEqual(message_response.status_code, 201)

        response = self.client.get(reverse("conversation-list-create"))
        self.assertEqual(response.status_code, 200)
        payload = response.data["results"][0]["last_message"]["attachments"][0]
        self.assertEqual(payload["media_kind"], "video")
        self.assertEqual(payload["width"], 1080)
        self.assertEqual(payload["height"], 1920)
        self.assertEqual(payload["rotation"], 90)
        self.assertEqual(str(payload["duration_seconds"]), "5.20")
        self.assertEqual(payload["metadata"]["display_width"], 1920)
        self.assertEqual(payload["metadata"]["display_height"], 1080)
        self.assertIn("/thumbnail/", payload["thumbnail_url"])
        self.assertIn("/download/", payload["download_url"])
        self.assertIn("/preview/", payload["preview_url"])

    def test_sync_includes_attachment_layout_metadata_and_thumbnail(self):
        video_file = SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4")
        thumbnail_file = SimpleUploadedFile(
            "clip-thumb.gif",
            (
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
                b"\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        upload_response = self.client.post(
            reverse("upload-create"),
            {
                "file": video_file,
                "mime_type": "video/mp4",
                "media_kind": "video",
                "width": "1080",
                "height": "1920",
                "rotation": "90",
                "duration_seconds": "5.20",
                "thumbnail": thumbnail_file,
                "metadata": json.dumps({"display_width": 1920, "display_height": 1080}),
            },
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, 201)
        conversation = self.create_direct_conversation()
        message_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload_response.data["id"])]},
            format="json",
        )
        self.assertEqual(message_response.status_code, 201)

        response = self.client.get(reverse("chat-sync"))
        self.assertEqual(response.status_code, 200)
        attachments = response.data["messages"][0]["attachments"]
        self.assertEqual(len(attachments), 1)
        payload = attachments[0]
        self.assertEqual(payload["media_kind"], "video")
        self.assertEqual(payload["width"], 1080)
        self.assertEqual(payload["height"], 1920)
        self.assertEqual(payload["rotation"], 90)
        self.assertEqual(str(payload["duration_seconds"]), "5.20")
        self.assertEqual(payload["metadata"]["display_width"], 1920)
        self.assertEqual(payload["metadata"]["display_height"], 1080)
        self.assertIn("/thumbnail/", payload["thumbnail_url"])
        self.assertIn("/download/", payload["download_url"])
        self.assertIn("/preview/", payload["preview_url"])

    def test_upload_and_attachment_expose_public_media_metadata(self):
        image_file = SimpleUploadedFile("photo.jpg", b"fake-image-bytes", content_type="image/jpeg")
        metadata = {
            "display_width": 1440,
            "display_height": 1080,
            "aspect_ratio": 1.333333,
            "camera": {"make": "Google", "model": "Pixel"},
            "tags": ["cover", "gallery"],
        }
        upload_response = self.client.post(
            reverse("upload-create"),
            {
                "file": image_file,
                "mime_type": "image/jpeg",
                "media_kind": "image",
                "width": "1440",
                "height": "1080",
                "metadata": json.dumps(metadata),
            },
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, 201)
        self.assertEqual(upload_response.data["metadata"]["display_width"], 1440)
        self.assertEqual(upload_response.data["metadata"]["camera"]["model"], "Pixel")

        conversation = self.create_direct_conversation()
        message_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload_response.data["id"])]},
            format="json",
        )
        self.assertEqual(message_response.status_code, 201)
        attachment = message_response.data["attachments"][0]
        self.assertEqual(attachment["metadata"]["display_height"], 1080)
        self.assertEqual(attachment["metadata"]["tags"], ["cover", "gallery"])

        db_attachment = MessageAttachment.objects.get(id=attachment["id"])
        self.assertEqual(db_attachment.metadata["display_width"], 1440)
        self.assertEqual(db_attachment.metadata["camera"]["make"], "Google")

    def test_upload_rejects_reserved_media_metadata_keys(self):
        image_file = SimpleUploadedFile("photo.jpg", b"fake-image-bytes", content_type="image/jpeg")
        response = self.client.post(
            reverse("upload-create"),
            {
                "file": image_file,
                "mime_type": "image/jpeg",
                "metadata": json.dumps({"encryption": {"secret": "x"}}),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("metadata", response.data.get("errors", {}))

    def test_attachment_public_metadata_hides_reserved_encryption_keys(self):
        conversation = self.create_direct_conversation()
        message = Message.objects.create(
            conversation=Conversation.objects.get(id=conversation["id"]),
            sender=self.user,
            type=Message.MessageType.FILE,
            text="",
        )
        attachment = MessageAttachment.objects.create(
            message=message,
            original_name="secret.bin",
            media_kind="file",
            mime_type="application/octet-stream",
            size=16,
            scan_status=MessageAttachment.ScanStatus.CLEAN,
            metadata={
                "checksum": "abc123",
                "encrypted_attachment": True,
                "encryption": {"metadata_ciphertext": "x", "metadata_nonce": "y"},
            },
            file=SimpleUploadedFile("secret.bin", b"secret-bytes", content_type="application/octet-stream"),
        )
        response = self.client.get(reverse("message-detail", kwargs={"pk": message.id}))
        self.assertEqual(response.status_code, 200)
        payload = response.data["attachments"][0]
        self.assertEqual(payload["metadata"], {"checksum": "abc123"})
        self.assertTrue(payload["is_encrypted"])
        self.assertEqual(payload["encryption"]["metadata_ciphertext"], "x")
        attachment.delete()

    def test_image_upload_generates_server_thumbnail_and_dimensions(self):
        image = Image.new("RGB", (2, 1), color=(255, 0, 0))
        output = BytesIO()
        image.save(output, format="PNG")
        response = self.client.post(
            reverse("upload-create"),
            {"file": SimpleUploadedFile("tiny.png", output.getvalue(), content_type="image/png"), "media_kind": "image"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["width"], 2)
        self.assertEqual(response.data["height"], 1)
        self.assertIsNotNone(response.data["thumbnail_url"])
        self.assertTrue(response.data["metadata"]["server_metadata_verified"])
        self.assertEqual(response.data["metadata"]["thumbnail_source"], "server_generated")

    def test_upload_rejects_invalid_thumbnail_image(self):
        response = self.client.post(
            reverse("upload-create"),
            {
                "file": SimpleUploadedFile("clip.mp4", b"video", content_type="video/mp4"),
                "media_kind": "video",
                "thumbnail": SimpleUploadedFile("bad-thumb.jpg", b"not-an-image", content_type="image/jpeg"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("thumbnail", response.data.get("errors", {}))

    @patch("apps.chat.services.subprocess.run")
    @patch("apps.chat.services.shutil.which")
    def test_video_upload_is_server_verified_with_probe_metadata(self, mock_which, mock_run):
        mock_which.side_effect = lambda binary: {
            "ffprobe": "/usr/bin/ffprobe",
            "ffmpeg": "/usr/bin/ffmpeg",
        }.get(binary)
        thumbnail_output = BytesIO()
        Image.new("RGB", (8, 4), color=(0, 255, 0)).save(thumbnail_output, format="PNG")

        def subprocess_side_effect(command, **kwargs):
            if command[0] == "/usr/bin/ffprobe":
                return Mock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "codec_type": "video",
                                    "codec_name": "h264",
                                    "width": 1080,
                                    "height": 1920,
                                    "avg_frame_rate": "30000/1001",
                                    "tags": {"rotate": "90"},
                                },
                                {
                                    "codec_type": "audio",
                                    "codec_name": "aac",
                                },
                            ],
                            "format": {
                                "duration": "5.20",
                                "bit_rate": "712345",
                            },
                        }
                    ),
                    stderr="",
                )
            if command[0] == "/usr/bin/ffmpeg":
                return Mock(returncode=0, stdout=thumbnail_output.getvalue(), stderr=b"")
            raise AssertionError(f"Unexpected command: {command}")

        mock_run.side_effect = subprocess_side_effect

        response = self.client.post(
            reverse("upload-create"),
            {
                "file": SimpleUploadedFile("clip.mp4", b"fake-video-bytes", content_type="video/mp4"),
                "media_kind": "video",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["width"], 1080)
        self.assertEqual(response.data["height"], 1920)
        self.assertEqual(response.data["rotation"], 90)
        self.assertEqual(str(response.data["duration_seconds"]), "5.20")
        self.assertEqual(response.data["metadata"]["display_width"], 1920)
        self.assertEqual(response.data["metadata"]["display_height"], 1080)
        self.assertAlmostEqual(float(response.data["metadata"]["aspect_ratio"]), 1920 / 1080, places=4)
        self.assertEqual(response.data["metadata"]["codec_name"], "h264")
        self.assertEqual(response.data["metadata"]["bit_rate"], 712345)
        self.assertTrue(response.data["metadata"]["has_audio_stream"])
        self.assertEqual(response.data["metadata"]["server_probe_status"], "ffprobe_verified")
        self.assertEqual(response.data["metadata"]["thumbnail_source"], "server_generated")
        self.assertEqual(response.data["metadata"]["thumbnail_generation_status"], "generated")
        self.assertTrue(response.data["metadata"]["server_metadata_verified"])
        self.assertIsNotNone(response.data["thumbnail_url"])

    @patch("apps.chat.services.subprocess.run")
    @patch("apps.chat.services.shutil.which", return_value="/usr/bin/ffprobe")
    def test_audio_upload_extracts_duration_and_codec_from_probe(self, _mock_which, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "audio",
                            "codec_name": "opus",
                            "sample_rate": "48000",
                            "channels": 1,
                            "duration": "4.80",
                        }
                    ],
                    "format": {
                        "duration": "4.80",
                        "bit_rate": "64000",
                    },
                }
            ),
            stderr="",
        )

        response = self.client.post(
            reverse("upload-create"),
            {
                "file": SimpleUploadedFile("note.ogg", b"fake-audio-bytes", content_type="audio/ogg"),
                "media_kind": "audio",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data["duration_seconds"]), "4.80")
        self.assertEqual(response.data["metadata"]["codec_name"], "opus")
        self.assertEqual(response.data["metadata"]["sample_rate"], 48000)
        self.assertEqual(response.data["metadata"]["channels"], 1)
        self.assertEqual(response.data["metadata"]["bit_rate"], 64000)
        self.assertEqual(response.data["metadata"]["server_probe_status"], "ffprobe_verified")
        self.assertTrue(response.data["metadata"]["server_metadata_verified"])

    def test_group_role_update_and_transfer_ownership(self):
        conversation = self.create_group_conversation()
        promote = self.client.patch(
            reverse("group-participant-role-update", kwargs={"conversation_id": conversation["id"], "user_id": self.other.id}),
            {"role": "admin"},
            format="json",
        )
        self.assertEqual(promote.status_code, 200)
        self.assertEqual(promote.data["role"], "admin")
        transfer = self.client.post(
            reverse("group-ownership-transfer", kwargs={"conversation_id": conversation["id"]}),
            {"target_user_id": str(self.other.id)},
            format="json",
        )
        self.assertEqual(transfer.status_code, 200)
        self.assertEqual(ConversationParticipant.objects.get(conversation_id=conversation["id"], user=self.other, left_at__isnull=True).role, "owner")

    def test_non_owner_cannot_change_roles(self):
        conversation = self.create_group_conversation()
        self.client.force_authenticate(self.other)
        response = self.client.patch(
            reverse("group-participant-role-update", kwargs={"conversation_id": conversation["id"], "user_id": self.third.id}),
            {"role": "admin"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_list_group_invite_links(self):
        conversation = self.create_group_conversation()
        self.client.post(
            reverse("conversation-invite-links", kwargs={"conversation_id": conversation["id"]}),
            {"expires_in_hours": 24},
            format="json",
        )
        self.client.force_authenticate(self.other)
        response = self.client.get(reverse("conversation-invite-links", kwargs={"conversation_id": conversation["id"]}))
        self.assertEqual(response.status_code, 403)

    def test_banned_user_cannot_rejoin_group_via_invite(self):
        conversation = self.create_group_conversation()
        invite = self.client.post(
            reverse("conversation-invite-links", kwargs={"conversation_id": conversation["id"]}),
            {"expires_in_hours": 24},
            format="json",
        ).data
        ban = self.client.post(
            reverse("group-participant-ban", kwargs={"conversation_id": conversation["id"], "user_id": self.other.id}),
            {"reason": "spam"},
            format="json",
        )
        self.assertEqual(ban.status_code, 200)
        self.client.force_authenticate(self.other)
        join = self.client.post(reverse("conversation-invite-join"), {"token": invite["token"]}, format="json")
        self.assertEqual(join.status_code, 403)

    def test_blocked_user_cannot_be_added_during_group_creation(self):
        self.client.post(reverse("block-list-create"), {"blocked_user_id": str(self.other.id), "reason": "spam"}, format="json")
        response = self.client.post(
            reverse("conversation-list-create"),
            {"type": "group", "title": "Builders", "participant_ids": [str(self.other.id), str(self.third.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_mark_delivered_creates_delivery_records(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "deliver me"}, format="json").data
        self.client.force_authenticate(self.other)
        response = self.client.post(reverse("conversation-mark-delivered", kwargs={"conversation_id": conversation["id"]}), {"message_id": message["id"]}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(MessageDelivery.objects.filter(message_id=message["id"], user=self.other).exists())

    def test_mark_read_also_updates_delivered_pointer(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "read me"}, format="json").data
        self.client.force_authenticate(self.other)
        response = self.client.post(reverse("conversation-mark-read", kwargs={"conversation_id": conversation["id"]}), {"message_id": message["id"]}, format="json")
        self.assertEqual(response.status_code, 200)
        participant = ConversationParticipant.objects.get(conversation_id=conversation["id"], user=self.other)
        self.assertEqual(str(participant.last_read_message_id), message["id"])
        self.assertEqual(str(participant.last_delivered_message_id), message["id"])

    def test_pending_android_receipt_ids_are_ignored(self):
        conversation = self.create_direct_conversation()
        self.client.force_authenticate(self.other)
        pending_id = "pending-android-6e043f2a-f155-42bb-9336-b970e6ad6adf"
        delivered = self.client.post(
            reverse("conversation-mark-delivered", kwargs={"conversation_id": conversation["id"]}),
            {"message_id": pending_id},
            format="json",
        )
        read = self.client.post(
            reverse("conversation-mark-read", kwargs={"conversation_id": conversation["id"]}),
            {"message_id": pending_id},
            format="json",
        )
        participant = ConversationParticipant.objects.get(conversation_id=conversation["id"], user=self.other)
        self.assertEqual(delivered.status_code, 200)
        self.assertEqual(read.status_code, 200)
        self.assertIsNone(participant.last_delivered_message_id)
        self.assertIsNone(participant.last_read_message_id)


    def test_media_token_download_for_attachment(self):
        good_file = SimpleUploadedFile("note.txt", b"hello world", content_type="text/plain")
        upload_response = self.client.post(reverse("upload-create"), {"file": good_file}, format="multipart")
        conversation = self.create_direct_conversation()
        message_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload_response.data["id"])]},
            format="json",
        )
        attachment_id = message_response.data["attachments"][0]["id"]
        token_response = self.client.post(reverse("attachment-media-token", kwargs={"resource_id": attachment_id}), format="json")
        self.assertEqual(token_response.status_code, 200)
        token = token_response.data["token"]
        download = self.client.get(reverse("attachment-download", kwargs={"attachment_id": attachment_id}), {"token": token})
        self.assertEqual(download.status_code, 200)

    def test_view_once_media_grants_exactly_one_recipient_session(self):
        image_bytes = BytesIO()
        Image.new("RGB", (8, 6), color=(25, 50, 75)).save(image_bytes, format="PNG")
        upload_response = self.client.post(
            reverse("upload-create"),
            {"file": SimpleUploadedFile("secret.png", image_bytes.getvalue(), content_type="image/png")},
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, 201)
        conversation = self.create_direct_conversation()
        message_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {
                "attachment_ids": [str(upload_response.data["id"])],
                "view_once_attachment_ids": [str(upload_response.data["id"])],
            },
            format="json",
        )
        self.assertEqual(message_response.status_code, 201)
        attachment = message_response.data["attachments"][0]
        self.assertTrue(attachment["view_once"])
        self.assertEqual(attachment["preview_url"], "")
        self.assertIsNone(attachment["thumbnail_url"])
        self.assertIsNone(attachment["signed_preview"])

        attachment_id = attachment["id"]
        sender_open = self.client.post(reverse("attachment-view-once-open", kwargs={"attachment_id": attachment_id}), format="json")
        self.assertEqual(sender_open.status_code, 403)
        standard_token = self.client.post(reverse("attachment-media-token", kwargs={"resource_id": attachment_id}), format="json")
        self.assertEqual(standard_token.status_code, 403)

        self.client.force_authenticate(self.other)
        opened = self.client.post(reverse("attachment-view-once-open", kwargs={"attachment_id": attachment_id}), format="json")
        self.assertEqual(opened.status_code, 200)
        preview = self.client.get(
            reverse("attachment-preview", kwargs={"attachment_id": attachment_id}),
            {"token": opened.data["token"]},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("no-store", preview.headers.get("Cache-Control", ""))
        second_open = self.client.post(reverse("attachment-view-once-open", kwargs={"attachment_id": attachment_id}), format="json")
        self.assertEqual(second_open.status_code, 403)
        download = self.client.get(
            reverse("attachment-download", kwargs={"attachment_id": attachment_id}),
            {"token": opened.data["token"]},
        )
        self.assertEqual(download.status_code, 403)

    def test_user_search_online_status_uses_presence(self):
        set_presence(self.other, device_id="mobile")
        response = self.client.get("/api/v1/chat/users/search/", {"q": "other"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data[0]["is_online"])

    def test_email_change_requires_fresh_verification(self):
        self.user.email_verified = True
        self.user.email_verified_at = timezone.now()
        self.user.save(update_fields=["email_verified", "email_verified_at"])
        response = self.client.patch(reverse("me"), {"email": "new-xim@example.com"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new-xim@example.com")
        self.assertFalse(self.user.email_verified)
        self.assertIsNone(self.user.email_verified_at)

    def test_multi_device_presence_registry_removes_only_disconnected_device(self):
        set_presence(self.other, device_id="mobile")
        set_presence(self.other, device_id="desktop")
        self.assertEqual(get_presence_snapshot(self.other.id)["active_devices"], 2)
        clear_presence(self.other, device_id="mobile")
        snapshot = get_presence_snapshot(self.other.id)
        self.assertTrue(snapshot["is_online"])
        self.assertEqual(snapshot["active_devices"], 1)

    def test_conversation_serialization_hides_private_presence(self):
        set_presence(self.other, device_id="mobile")
        self.other.profile.show_online_status = False
        self.other.profile.save(update_fields=["show_online_status", "updated_at"])
        conversation = self.client.post(
            reverse("conversation-list-create"),
            {"type": "direct", "participant_ids": [str(self.other.id)]},
            format="json",
        ).data
        detail = self.client.get(reverse("conversation-detail", kwargs={"pk": conversation["id"]}))
        self.assertEqual(detail.status_code, 200)
        peer = next(item["user"] for item in detail.data["participants"] if str(item["user"]["id"]) == str(self.other.id))
        self.assertFalse(peer["is_online"])
        self.assertEqual(peer["active_devices"], 0)
        self.assertIsNone(peer["last_seen_at"])
        self.assertEqual(peer["presence_label"], "offline")

    def test_profile_privacy_hides_discovery_presence_and_nearby_location(self):
        set_presence(self.other, device_id="mobile")
        self.other.profile.latitude = "24.713600"
        self.other.profile.longitude = "46.675300"
        self.other.profile.show_online_status = False
        self.other.profile.nearby_discovery_enabled = False
        self.other.profile.save(
            update_fields=[
                "latitude",
                "longitude",
                "show_online_status",
                "nearby_discovery_enabled",
                "updated_at",
            ]
        )

        search = self.client.get("/api/v1/chat/users/search/", {"q": "other"})
        self.assertEqual(search.status_code, 200)
        self.assertFalse(search.data[0]["is_online"])
        nearby = self.client.get("/api/v1/chat/users/nearby/", {"latitude": "24.713600", "longitude": "46.675300"})
        self.assertEqual(nearby.status_code, 200)
        self.assertEqual(nearby.data, [])

        self.other.profile.is_discoverable = False
        self.other.profile.save(update_fields=["is_discoverable", "updated_at"])
        hidden = self.client.get("/api/v1/chat/users/search/", {"q": "other"})
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.data, [])

    def test_nearby_users_validates_coordinate_range(self):
        response = self.client.get("/api/v1/chat/users/nearby/", {"latitude": "120", "longitude": "46"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Valid latitude and longitude", response.data["detail"])

    def test_nearby_users_can_share_current_location_for_discovery(self):
        response = self.client.get(
            "/api/v1/chat/users/nearby/",
            {"latitude": "24.713600", "longitude": "46.675300", "share_location": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_discoverable)
        self.assertTrue(self.user.profile.nearby_discovery_enabled)
        self.assertEqual(str(self.user.profile.latitude), "24.713600")
        self.assertEqual(str(self.user.profile.longitude), "46.675300")
        self.assertIsNotNone(self.user.profile.location_updated_at)

    def test_security_headers_allow_same_origin_geolocation(self):
        response = self.client.get("/api/v1/health/live/")
        self.assertIn("geolocation=(self)", response.headers["Permissions-Policy"])

    def test_media_token_is_bearer_and_rejects_mismatched_authenticated_user(self):
        good_file = SimpleUploadedFile("note.txt", b"hello world", content_type="text/plain")
        upload_response = self.client.post(reverse("upload-create"), {"file": good_file}, format="multipart")
        conversation = self.create_direct_conversation()
        message_response = self.client.post(
            reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}),
            {"attachment_ids": [str(upload_response.data["id"])]},
            format="json",
        )
        attachment_id = message_response.data["attachments"][0]["id"]
        token = self.client.post(reverse("attachment-media-token", kwargs={"resource_id": attachment_id}), format="json").data["token"]

        anonymous = APIClient()
        anonymous_response = anonymous.get(reverse("attachment-download", kwargs={"attachment_id": attachment_id}), {"token": token})
        self.assertEqual(anonymous_response.status_code, 200)

        other_client = APIClient()
        other_client.force_authenticate(self.other)
        other_response = other_client.get(reverse("attachment-download", kwargs={"attachment_id": attachment_id}), {"token": token})
        self.assertEqual(other_response.status_code, 403)

    def test_pending_upload_expires_from_secure_queryset(self):
        upload = PendingUpload.objects.create(
            user=self.user,
            file=SimpleUploadedFile("note.txt", b"hello", content_type="text/plain"),
            original_name="note.txt",
            mime_type="text/plain",
            size=5,
            extension="txt",
            scan_status=PendingUpload.ScanStatus.CLEAN,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        response = self.client.get(reverse("pending-upload-download", kwargs={"upload_id": upload.id}))
        self.assertEqual(response.status_code, 404)

    def test_attachment_rescan_uses_antivirus_result(self):
        conversation = self.create_direct_conversation()
        message = Message.objects.create(
            conversation=Conversation.objects.get(id=conversation["id"]),
            sender=self.user,
            type=Message.MessageType.FILE,
            text="",
        )
        attachment = MessageAttachment.objects.create(
            message=message,
            original_name="eicar.txt",
            mime_type="text/plain",
            size=68,
            scan_status=MessageAttachment.ScanStatus.CLEAN,
            file=SimpleUploadedFile(
                "eicar.txt",
                b"attachment-bytes",
                content_type="text/plain",
            ),
        )

        from apps.chat.tasks import rescan_attachment

        with patch(
            "apps.chat.tasks.scan_file_field",
            return_value=AntivirusResult(False, "infected", "test signature detected", "signature"),
        ):
            status_after_rescan = rescan_attachment(str(attachment.id))
        self.assertEqual(status_after_rescan, MessageAttachment.ScanStatus.INFECTED)
        attachment.refresh_from_db()
        self.assertEqual(attachment.scan_status, MessageAttachment.ScanStatus.INFECTED)

    def test_audit_log_created_for_message_edit(self):
        conversation = self.create_direct_conversation()
        message = self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "draft"}, format="json").data
        self.client.patch(reverse("message-manage", kwargs={"message_id": message["id"]}), {"text": "final"}, format="json")
        self.assertTrue(ChatAuditLog.objects.filter(event_type="message_edited", message_id=message["id"]).exists())

    def test_staff_can_view_integration_health(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("integration-health"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("antivirus", response.data)
        self.assertIn("push", response.data)

    def test_admin_can_view_audit_logs(self):
        conversation = self.create_direct_conversation()
        self.client.post(reverse("message-list-create", kwargs={"conversation_id": conversation["id"]}), {"text": "audit me"}, format="json")
        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("chat-audit-log-list"))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 1)


try:
    from channels.testing import WebsocketCommunicator
    CHANNELS_AVAILABLE = True
except Exception:
    CHANNELS_AVAILABLE = False


@skipUnless(CHANNELS_AVAILABLE, "channels testing not available")
@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class ChatWebsocketTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wsuser", email="wsuser@example.com", password="pass12345")
        self.other = User.objects.create_user(username="wsother", email="wsother@example.com", password="pass12345")
        client = APIClient()
        client.force_authenticate(self.user)
        self.conversation = client.post(reverse("conversation-list-create"), {"type": "direct", "participant_ids": [str(self.other.id)]}, format="json").data
        other_client = APIClient()
        other_client.force_authenticate(self.other)
        self.message_id = other_client.post(reverse("message-list-create", kwargs={"conversation_id": self.conversation["id"]}), {"text": "hello"}, format="json").data["id"]

    async def _connect(self):
        from config.asgi import application
        token = str(AccessToken.for_user(self.user))
        communicator = WebsocketCommunicator(application, f"/ws/chat/?token={token}")
        connected, _ = await communicator.connect()
        return communicator, connected

    async def _receive_event(self, communicator, expected_event, *, max_events=12):
        observed = []
        for _ in range(max_events):
            payload = await communicator.receive_json_from(timeout=2)
            observed.append(payload.get("event"))
            if payload.get("event") == expected_event:
                return payload
        self.fail(f"Expected websocket event {expected_event!r}; observed {observed!r}")

    def test_websocket_subscribe_and_delivered_event(self):
        import asyncio

        async def runner():
            communicator, connected = await self._connect()
            self.assertTrue(connected)
            await communicator.send_json_to({"event": "conversation.subscribe", "data": {"conversation_id": self.conversation["id"]}})
            first = await communicator.receive_json_from()
            self.assertEqual(first["event"], "conversation.subscribed")
            delivered = await communicator.receive_json_from()
            self.assertEqual(delivered["event"], "message.delivered")
            await communicator.disconnect()

        asyncio.run(runner())

    def test_websocket_send_edit_react_and_presence(self):
        import asyncio

        async def runner():
            communicator, connected = await self._connect()
            self.assertTrue(connected)
            await communicator.send_json_to({"event": "conversation.subscribe", "data": {"conversation_id": self.conversation["id"]}})
            await communicator.receive_json_from()
            await communicator.receive_json_from()
            await communicator.send_json_to({"event": "message.send", "data": {"conversation_id": self.conversation["id"], "text": "from ws", "client_temp_id": "tmp-1"}})
            created = await communicator.receive_json_from()
            self.assertEqual(created["event"], "message.created")
            self.assertTrue(created.get("event_id"))
            self.assertTrue(created.get("occurred_at"))
            created_id = created["data"]["id"]

            await communicator.send_json_to({"event": "message.edit", "data": {"conversation_id": self.conversation["id"], "message_id": created_id, "text": "edited"}})
            updated = await self._receive_event(communicator, "message.updated")
            self.assertEqual(updated["event"], "message.updated")
            self.assertEqual(updated["data"]["text"], "edited")

            await communicator.send_json_to({"event": "message.react", "data": {"conversation_id": self.conversation["id"], "message_id": self.message_id, "emoji": "👍"}})
            reacted = await self._receive_event(communicator, "message.reaction_updated")
            self.assertEqual(reacted["event"], "message.reaction_updated")

            await communicator.send_json_to({"event": "presence.ping", "data": {}})
            pong = await self._receive_event(communicator, "presence.pong")
            self.assertEqual(pong["event"], "presence.pong")
            await communicator.disconnect()

        asyncio.run(runner())

    def test_consumer_typing_does_not_echo_to_sender(self):
        import asyncio

        async def runner():
            from apps.chat.consumers import ChatConsumer

            consumer = ChatConsumer()
            consumer.user = self.user
            consumer.send_json = AsyncMock()

            await consumer.chat_event({"event": "typing.started", "data": {"user_id": str(self.user.id)}})
            consumer.send_json.assert_not_awaited()

            await consumer.chat_event({"event": "typing.started", "data": {"user_id": str(self.other.id)}})
            consumer.send_json.assert_awaited_once()

        asyncio.run(runner())


class ChatBackgroundTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="taskuser", password="pass12345")
        self.other = User.objects.create_user(username="taskother", password="pass12345")
        client = APIClient()
        client.force_authenticate(self.user)
        self.conversation = client.post(reverse("conversation-list-create"), {"type": "direct", "participant_ids": [str(self.other.id)]}, format="json").data
        self.message_id = client.post(reverse("message-list-create", kwargs={"conversation_id": self.conversation["id"]}), {"text": "background"}, format="json").data["id"]
        UserDevice.objects.create(user=self.other, platform="android", push_token="push-1", is_active=True)

    @patch("apps.chat.tasks.send_push")
    def test_fanout_push_notifications_uses_device_tokens(self, mock_send_push):
        from apps.chat.tasks import fanout_push_notifications

        mock_send_push.return_value = type("Result", (), {"attempted": 1, "sent": 1, "failed": 0})()
        result = fanout_push_notifications(self.message_id)
        self.assertEqual(result["attempted"], 1)
        self.assertTrue(mock_send_push.called)

    @patch("apps.chat.tasks.send_push")
    def test_fanout_push_notifications_deactivates_invalid_tokens(self, mock_send_push):
        from apps.chat.tasks import fanout_push_notifications

        mock_send_push.return_value = type("Result", (), {
            "attempted": 1,
            "sent": 0,
            "failed": 1,
            "invalid_tokens": ["push-1"],
            "transient_failures": [],
        })()
        result = fanout_push_notifications(self.message_id)
        self.assertEqual(result["invalidated_devices"], 1)
        self.assertFalse(UserDevice.objects.get(user=self.other, push_token="push-1").is_active)

    @patch("apps.chat.tasks.send_push")
    def test_fanout_push_notifications_respects_mentions_only_setting(self, mock_send_push):
        from apps.chat.tasks import fanout_push_notifications
        from apps.chat.models import ConversationNotificationSetting

        ConversationNotificationSetting.objects.create(
            conversation_id=self.conversation["id"],
            user=self.other,
            mentions_only=True,
        )
        mock_send_push.return_value = type("Result", (), {"attempted": 1, "sent": 1, "failed": 0, "invalid_tokens": []})()

        result = fanout_push_notifications(self.message_id)
        self.assertEqual(result["attempted"], 0)
        self.assertFalse(mock_send_push.called)

        message = Message.objects.get(id=self.message_id)
        message.metadata = {"mentioned_user_ids": [str(self.other.id)]}
        message.save(update_fields=["metadata", "updated_at"])

        result = fanout_push_notifications(self.message_id)
        self.assertEqual(result["attempted"], 1)
        self.assertTrue(mock_send_push.called)

    @patch("apps.chat.tasks.send_push")
    def test_fanout_push_notifications_respects_conversation_mute(self, mock_send_push):
        from apps.chat.tasks import fanout_push_notifications
        from apps.chat.models import ConversationNotificationSetting

        ConversationNotificationSetting.objects.create(
            conversation_id=self.conversation["id"],
            user=self.other,
            muted_until=timezone.now() + timedelta(hours=1),
        )
        mock_send_push.return_value = type("Result", (), {"attempted": 1, "sent": 1, "failed": 0, "invalid_tokens": []})()

        result = fanout_push_notifications(self.message_id)
        self.assertEqual(result["attempted"], 0)
        self.assertFalse(mock_send_push.called)

    @patch("apps.chat.tasks.send_push")
    def test_fanout_push_notifications_hides_encrypted_message_preview(self, mock_send_push):
        from apps.chat.tasks import fanout_push_notifications

        message = Message.objects.get(id=self.message_id)
        message.text = ""
        message.metadata = {
            "encrypted": True,
            "encryption": {
                "version": "v1",
                "algorithm": "xchacha20-poly1305",
                "ciphertext": "ciphertext",
                "nonce": "nonce",
                "sender_key_id": "sender-key-1",
                "recipient_key_ids": ["recipient-key-1"],
            },
        }
        message.save(update_fields=["text", "metadata", "updated_at"])

        mock_send_push.return_value = type("Result", (), {"attempted": 1, "sent": 1, "failed": 0, "invalid_tokens": []})()
        result = fanout_push_notifications(self.message_id)

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(mock_send_push.call_args.kwargs["body"], "New message")

    @patch("apps.chat.tasks.send_push")
    def test_fanout_push_notifications_hides_encrypted_attachment_preview(self, mock_send_push):
        from apps.chat.tasks import fanout_push_notifications

        message = Message.objects.get(id=self.message_id)
        attachment = MessageAttachment.objects.create(
            message=message,
            original_name="encrypted.bin",
            mime_type="application/octet-stream",
            size=16,
            scan_status=MessageAttachment.ScanStatus.CLEAN,
            metadata={"encrypted_attachment": True, "encryption": {"metadata_ciphertext": "x", "metadata_nonce": "y"}},
        )
        message.text = "plaintext that should not preview"
        message.save(update_fields=["text", "updated_at"])

        mock_send_push.return_value = type("Result", (), {"attempted": 1, "sent": 1, "failed": 0, "invalid_tokens": []})()
        result = fanout_push_notifications(self.message_id)

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(mock_send_push.call_args.kwargs["body"], "New message")
        attachment.delete()

    def test_fcm_error_classification_includes_exception_name(self):
        from apps.chat.push import _classify_fcm_error

        class DummyUnregisteredError(Exception):
            code = "NOT_FOUND"

        result = _classify_fcm_error(DummyUnregisteredError("Requested entity was not found."))

        self.assertIn("DummyUnregisteredError", result)
        self.assertIn("NOT_FOUND", result)

    def test_invalid_token_error_detects_not_found_unregistered_responses(self):
        from apps.chat.push import _is_invalid_token_error

        self.assertTrue(_is_invalid_token_error("UnregisteredError NOT_FOUND Requested entity was not found."))
        self.assertTrue(_is_invalid_token_error("registration-token-not-registered"))
        self.assertFalse(_is_invalid_token_error("internal server error"))

    @patch("apps.chat.tasks.send_push_with_options")
    @patch("apps.chat.tasks.send_push")
    def test_fanout_incoming_call_notifications_uses_device_tokens(self, mock_send_push, mock_send_push_with_options):
        from apps.chat.tasks import fanout_incoming_call_notifications

        call = start_call(self.user, Conversation.objects.get(id=self.conversation["id"]), CallSession.CallType.VIDEO)
        mock_send_push.return_value = type("Result", (), {"attempted": 0, "sent": 0, "failed": 0, "invalid_tokens": []})()
        mock_send_push_with_options.return_value = type("Result", (), {"attempted": 1, "sent": 1, "failed": 0, "invalid_tokens": []})()

        result = fanout_incoming_call_notifications(call.id)

        self.assertEqual(result["attempted"], 1)
        self.assertTrue(mock_send_push_with_options.called)
        self.assertEqual(mock_send_push_with_options.call_args.kwargs["data"]["event"], "incoming_call")
        self.assertEqual(mock_send_push_with_options.call_args.kwargs["data"]["mode"], "incoming")
        self.assertFalse(mock_send_push_with_options.call_args.kwargs["include_notification"])
        self.assertEqual(mock_send_push_with_options.call_args.kwargs["android_priority"], "high")
        self.assertEqual(mock_send_push_with_options.call_args.kwargs["android_ttl_seconds"], 45)
        self.assertEqual(
            mock_send_push_with_options.call_args.kwargs["android_collapse_key"],
            f"incoming-call-{call.id}",
        )

    @patch("apps.chat.tasks.send_push_with_options")
    @patch("apps.chat.tasks.send_push")
    def test_fanout_incoming_call_notifications_respects_call_notification_setting(self, mock_send_push, mock_send_push_with_options):
        from apps.chat.tasks import fanout_incoming_call_notifications
        from apps.chat.models import ConversationNotificationSetting

        ConversationNotificationSetting.objects.create(
            conversation_id=self.conversation["id"],
            user=self.other,
            call_notifications_enabled=False,
        )
        call = start_call(self.user, Conversation.objects.get(id=self.conversation["id"]), CallSession.CallType.VOICE)
        mock_send_push.return_value = type("Result", (), {"attempted": 1, "sent": 1, "failed": 0, "invalid_tokens": []})()

        result = fanout_incoming_call_notifications(call.id)

        self.assertEqual(result["attempted"], 0)
        self.assertFalse(mock_send_push.called)
        self.assertFalse(mock_send_push_with_options.called)

    def test_deactivate_stale_devices_task(self):
        from apps.chat.tasks import deactivate_stale_devices

        stale = UserDevice.objects.create(user=self.user, platform="web", push_token="old-token", is_active=True)
        UserDevice.objects.filter(id=stale.id).update(last_seen_at=timezone.now() - timedelta(days=45))
        count = deactivate_stale_devices()
        stale.refresh_from_db()
        self.assertGreaterEqual(count, 1)
        self.assertFalse(stale.is_active)

    def test_eicar_upload_is_rejected(self):
        client = APIClient()
        client.force_authenticate(self.user)
        eicar = b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
        response = client.post(reverse("upload-create"), {"file": SimpleUploadedFile("eicar.txt", eicar, content_type="text/plain")}, format="multipart")
        self.assertEqual(response.status_code, 201)
        upload = PendingUpload.objects.get(id=response.data["id"])
        self.assertEqual(upload.scan_status, PendingUpload.ScanStatus.INFECTED)



@skipUnless(CHANNELS_AVAILABLE, "channels testing not available")
@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class ChatWebsocketHardeningTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="hwsuser", password="pass12345")
        self.other = User.objects.create_user(username="hwsother", password="pass12345")
        client = APIClient()
        client.force_authenticate(self.user)
        self.conversation = client.post(reverse("conversation-list-create"), {"type": "direct", "participant_ids": [str(self.other.id)]}, format="json").data

    async def _connect(self, user):
        from config.asgi import application
        token = str(AccessToken.for_user(user))
        communicator = WebsocketCommunicator(application, f"/ws/chat/?token={token}")
        connected, _ = await communicator.connect()
        return communicator, connected

    async def _receive_event(self, communicator, expected_event, *, max_events=12):
        observed = []
        for _ in range(max_events):
            payload = await communicator.receive_json_from(timeout=2)
            observed.append(payload.get("event"))
            if payload.get("event") == expected_event:
                return payload
        self.fail(f"Expected websocket event {expected_event!r}; observed {observed!r}")

    def test_websocket_read_delete_and_unreact_flow(self):
        import asyncio

        async def runner():
            communicator, connected = await self._connect(self.user)
            self.assertTrue(connected)
            await communicator.send_json_to({"event": "conversation.subscribe", "data": {"conversation_id": self.conversation["id"]}})
            subscribed = await communicator.receive_json_from()
            self.assertEqual(subscribed["event"], "conversation.subscribed")
            await communicator.send_json_to({"event": "message.send", "data": {"conversation_id": self.conversation["id"], "text": "ws msg"}})
            created = await self._receive_event(communicator, "message.created")
            message_id = created["data"]["id"]
            await communicator.send_json_to({"event": "message.react", "data": {"conversation_id": self.conversation["id"], "message_id": message_id, "emoji": "🔥"}})
            reacted = await self._receive_event(communicator, "message.reaction_updated")
            self.assertEqual(reacted["event"], "message.reaction_updated")
            await communicator.send_json_to({"event": "message.unreact", "data": {"conversation_id": self.conversation["id"], "message_id": message_id, "emoji": "🔥"}})
            unreacted = await self._receive_event(communicator, "message.reaction_updated")
            self.assertEqual(unreacted["event"], "message.reaction_updated")
            await communicator.send_json_to({"event": "message.read", "data": {"conversation_id": self.conversation["id"], "message_id": message_id}})
            read = await self._receive_event(communicator, "message.read")
            self.assertEqual(read["event"], "message.read")
            await communicator.send_json_to({"event": "message.delete", "data": {"conversation_id": self.conversation["id"], "message_id": message_id}})
            deleted = await self._receive_event(communicator, "message.deleted")
            self.assertEqual(deleted["event"], "message.deleted")
            await communicator.disconnect()

        asyncio.run(runner())

    def test_two_users_receive_same_created_event(self):
        import asyncio

        async def runner():
            user_ws, connected1 = await self._connect(self.user)
            other_ws, connected2 = await self._connect(self.other)
            self.assertTrue(connected1)
            self.assertTrue(connected2)
            await user_ws.send_json_to({"event": "conversation.subscribe", "data": {"conversation_id": self.conversation["id"]}})
            await other_ws.send_json_to({"event": "conversation.subscribe", "data": {"conversation_id": self.conversation["id"]}})
            await self._receive_event(user_ws, "conversation.subscribed")
            await self._receive_event(other_ws, "conversation.subscribed")
            await user_ws.send_json_to({"event": "message.send", "data": {"conversation_id": self.conversation["id"], "text": "broadcast"}})
            event1 = await self._receive_event(user_ws, "message.created")
            event2 = await self._receive_event(other_ws, "message.created")
            self.assertEqual(event1["data"]["text"], "broadcast")
            self.assertEqual(event2["data"]["text"], "broadcast")
            await user_ws.disconnect()
            await other_ws.disconnect()

        asyncio.run(runner())

    def test_connected_user_receives_conversation_update_without_manual_subscription(self):
        import asyncio
        from asgiref.sync import sync_to_async

        async def runner():
            other_ws, connected = await self._connect(self.other)
            self.assertTrue(connected)
            client = APIClient()
            client.force_authenticate(self.user)
            created = await sync_to_async(client.post)(
                reverse("message-list-create", kwargs={"conversation_id": self.conversation["id"]}),
                {"text": "hello inbox"},
                format="json",
            )
            self.assertEqual(created.status_code, 201)
            event = await self._receive_event(other_ws, "conversation.updated")
            self.assertEqual(event["data"]["id"], self.conversation["id"])
            self.assertEqual(event["data"]["last_message"]["text"], "hello inbox")
            self.assertGreaterEqual(int(event["data"]["unread_count"]), 1)
            await other_ws.disconnect()

        asyncio.run(runner())

    def test_targeted_call_signal_is_not_broadcast_to_non_recipient(self):
        import asyncio

        third = User.objects.create_user(username="hwsthird", password="pass12345")
        client = APIClient()
        client.force_authenticate(self.user)
        conversation = client.post(
            reverse("conversation-list-create"),
            {"type": "group", "title": "Callers", "participant_ids": [str(self.other.id), str(third.id)]},
            format="json",
        ).data
        call = client.post(
            reverse("call-start", kwargs={"conversation_id": conversation["id"]}),
            {"call_type": "video"},
            format="json",
        ).data

        async def runner():
            sender_ws, sender_connected = await self._connect(self.user)
            recipient_ws, recipient_connected = await self._connect(self.other)
            third_ws, third_connected = await self._connect(third)
            self.assertTrue(sender_connected)
            self.assertTrue(recipient_connected)
            self.assertTrue(third_connected)

            for communicator in (sender_ws, recipient_ws, third_ws):
                await communicator.send_json_to({"event": "conversation.subscribe", "data": {"conversation_id": conversation["id"]}})
                await self._receive_event(communicator, "conversation.subscribed")

            await sender_ws.send_json_to(
                {
                    "event": "call.signal",
                    "data": {
                        "call_id": call["id"],
                        "to_user_id": str(self.other.id),
                        "signal_type": "offer",
                        "payload": {"sdp": "secret-offer"},
                    },
                }
            )

            recipient_event = await self._receive_event(recipient_ws, "call.signal")
            self.assertEqual(recipient_event["data"]["payload"]["sdp"], "secret-offer")
            self.assertEqual(recipient_event["data"]["to_user_id"], str(self.other.id))

            async def assert_no_call_signal(communicator):
                deadline = asyncio.get_running_loop().time() + 0.3
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return
                    try:
                        event = await asyncio.wait_for(communicator.receive_json_from(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return
                    self.assertNotEqual(event.get("event"), "call.signal")

            await assert_no_call_signal(sender_ws)
            await assert_no_call_signal(third_ws)

            await sender_ws.disconnect()
            await recipient_ws.disconnect()
            await third_ws.disconnect()

        asyncio.run(runner())

    def test_websocket_rejects_unsubscribed_conversation_access(self):
        import asyncio

        async def runner():
            communicator, connected = await self._connect(self.other)
            self.assertTrue(connected)
            await communicator.send_json_to({"event": "message.send", "data": {"conversation_id": "00000000-0000-0000-0000-000000000000", "text": "bad"}})
            error = await communicator.receive_json_from()
            self.assertEqual(error["event"], "error")
            await communicator.disconnect()

        asyncio.run(runner())


class ChatIntegrationUtilityTests(TestCase):
    def test_antivirus_healthcheck_signature_mode(self):
        result = antivirus_healthcheck()
        self.assertIn(result.engine, {"signature", "clamav"})
        self.assertTrue(hasattr(result, "details"))




class PlatformStabilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="healthuser", email="healthuser@example.com", password="pass12345")
        self.other = User.objects.create_user(username="healthother", email="healthother@example.com", password="pass12345")
        self.third = User.objects.create_user(username="healththird", email="healththird@example.com", password="pass12345")
        self.staff = User.objects.create_user(username="healthadmin", email="healthadmin@example.com", password="pass12345", is_staff=True)
        self.client.force_authenticate(self.user)

    def create_direct_conversation(self):
        response = self.client.post(reverse("conversation-list-create"), {"type": "direct", "participant_ids": [str(self.other.id)]}, format="json")
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_live_health_endpoint(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")
        self.assertIn("service", response.data)
        self.assertIn("version", response.data)

    def test_ready_health_endpoint(self):
        response = self.client.get(reverse("health-ready"))
        self.assertIn(response.status_code, (200, 503))
        self.assertIn("database", response.data["checks"])
        self.assertIn("cache", response.data["checks"])
        self.assertIn("migrations", response.data["checks"])

    def test_deep_health_requires_staff(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse("health-deep"))
        self.assertEqual(response.status_code, 403)
        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("health-deep"))
        self.assertIn(response.status_code, (200, 503))
        self.assertIn("integrations", response.data["checks"])

    def test_error_envelope_and_request_id(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("conversation-list-create"),
            {"type": "direct", "participant_ids": [str(self.user.id)]},
            format="json",
            HTTP_X_REQUEST_ID="req-12345",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["X-Request-ID"], "req-12345")
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["request_id"], "req-12345")
        self.assertIn("participant_ids", response.data["errors"])

    def test_security_headers_present(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")


    def test_start_voice_call_and_accept(self):
        conversation = self.create_direct_conversation()
        response = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "voice"}, format="json")
        self.assertEqual(response.status_code, 201)
        call_id = response.data["id"]
        self.assertEqual(response.data["status"], "ringing")
        self.client.force_authenticate(self.other)
        accept = self.client.post(reverse("call-accept", kwargs={"call_id": call_id}), format="json")
        self.assertEqual(accept.status_code, 200)
        self.assertEqual(accept.data["status"], "ongoing")
        self.assertEqual(CallSession.objects.get(id=call_id).status, CallSession.Status.ONGOING)

    def test_call_signal_requires_participant(self):
        conversation = self.create_direct_conversation()
        response = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "video"}, format="json")
        call_id = response.data["id"]
        outsider = APIClient()
        outsider.force_authenticate(self.third)
        signal = outsider.post(reverse("call-signal", kwargs={"call_id": call_id}), {"signal_type": "offer", "payload": {"sdp": "x"}}, format="json")
        self.assertEqual(signal.status_code, 403)

    def test_call_signal_can_target_only_call_participants(self):
        conversation = self.create_direct_conversation()
        response = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "video"}, format="json")
        call_id = response.data["id"]
        signal = self.client.post(
            reverse("call-signal", kwargs={"call_id": call_id}),
            {"signal_type": "offer", "payload": {"sdp": "x", "to_user_id": str(self.other.id)}},
            format="json",
        )
        self.assertEqual(signal.status_code, 202)
        self.assertEqual(signal.data["to_user_id"], str(self.other.id))

        outsider_target = self.client.post(
            reverse("call-signal", kwargs={"call_id": call_id}),
            {"signal_type": "offer", "payload": {"sdp": "x", "to_user_id": str(self.third.id)}},
            format="json",
        )
        self.assertEqual(outsider_target.status_code, 400)
        self.assertIn("to_user_id", outsider_target.data["errors"])

    def test_calling_config_endpoint(self):
        response = self.client.get(reverse("calling-config"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("ice_servers", response.data)
        self.assertIn("offer_timeout_seconds", response.data)
        self.assertIn("network_profiles", response.data)
        self.assertIn("reconnect_grace_seconds", response.data)



    def test_permissions_policy_header_present(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response["Permissions-Policy"], "camera=(self), microphone=(self), geolocation=(self)")

    def test_readiness_management_command_runs(self):
        out = StringIO()
        call_command("check_chat_readiness", stdout=out)
        output = out.getvalue()
        self.assertIn("Messenger backend readiness summary", output)
        self.assertIn("service:", output)
        self.assertIn("migrations:", output)

    @override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "health-tests"}},
        CELERY_TASK_ALWAYS_EAGER=True,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    )
    def test_readiness_management_command_reports_local_runtime_backends(self):
        out = StringIO()
        call_command("check_chat_readiness", stdout=out)
        output = out.getvalue()
        self.assertIn("channel layer hosts: in-memory", output)
        self.assertIn("Readiness warnings:", output)
        self.assertIn("Channel layer is in-memory", output)
        self.assertIn("Cache backend is local memory", output)
        self.assertIn("Celery tasks are running eagerly", output)
        self.assertIn("Email backend is console", output)
    def test_calling_quality_preference_is_saved_and_used_in_config(self):
        pref = self.client.patch(
            reverse("notification-preferences"),
            {"call_quality_preference": "mid"},
            format="json",
        )
        self.assertEqual(pref.status_code, 200)
        self.assertEqual(pref.data["call_quality_preference"], "mid")

        config = self.client.get(reverse("calling-config"))
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.data["selected_quality_preset"], "mid")
        self.assertIn("available_quality_presets", config.data)
        self.assertEqual(config.data["applied_quality_profile"]["video"]["max_width"], 640)

    def test_login_lockout_after_repeated_failures(self):
        bad_client = APIClient()
        for _ in range(8):
            response = bad_client.post(reverse("login"), {"username": self.user.username, "password": "wrong-pass"}, format="json")
            self.assertEqual(response.status_code, 401)
        locked = bad_client.post(reverse("login"), {"username": self.user.username, "password": "wrong-pass"}, format="json")
        self.assertEqual(locked.status_code, 401)
        self.assertIn("Too many failed login attempts", str(locked.data))

    def test_login_supports_email_identifier(self):
        client = APIClient()
        response = client.post(reverse("login"), {"username": self.user.email, "password": "pass12345"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    @override_settings(AUTH_IP_FAILURE_THRESHOLD=3, AUTH_IP_BLOCK_TTL_SECONDS=60, AUTH_IP_FAILURE_WINDOW_SECONDS=60)
    def test_login_ip_block_after_repeated_failures(self):
        blocked_ip = "203.0.113.10"
        bad_client = APIClient()
        for _ in range(3):
            response = bad_client.post(
                reverse("login"),
                {"username": self.user.username, "password": "wrong-pass"},
                format="json",
                REMOTE_ADDR=blocked_ip,
            )
            self.assertEqual(response.status_code, 401)
        blocked = bad_client.post(
            reverse("login"),
            {"username": self.user.username, "password": "pass12345"},
            format="json",
            REMOTE_ADDR=blocked_ip,
        )
        self.assertEqual(blocked.status_code, 401)
        self.assertIn("Too many failed attempts from this IP", str(blocked.data))

        other_ip = APIClient()
        allowed = other_ip.post(
            reverse("login"),
            {"username": self.user.username, "password": "pass12345"},
            format="json",
            REMOTE_ADDR="203.0.113.11",
        )
        self.assertEqual(allowed.status_code, 200)

    @patch("secrets.token_urlsafe", return_value="verify-token-123")
    def test_email_verification_request_and_confirm(self, _mock_token):
        response = self.client.post(reverse("email-verify-request"), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AuthActionToken.objects.filter(user=self.user, purpose="email_verify", used_at__isnull=True).exists())

        confirm = APIClient().post(reverse("email-verify-confirm"), {"token": "verify-token-123"}, format="json")
        self.assertEqual(confirm.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    @patch("secrets.token_urlsafe", return_value="reset-token-123")
    def test_password_reset_request_and_confirm(self, _mock_token):
        request_client = APIClient()
        request_response = request_client.post(reverse("password-reset-request"), {"email": self.user.email}, format="json")
        self.assertEqual(request_response.status_code, 200)
        self.assertTrue(AuthActionToken.objects.filter(user=self.user, purpose="password_reset", used_at__isnull=True).exists())

        confirm_response = request_client.post(
            reverse("password-reset-confirm"),
            {"token": "reset-token-123", "new_password": "NewPass12345!"},
            format="json",
        )
        self.assertEqual(confirm_response.status_code, 200)

        login = request_client.post(reverse("login"), {"username": self.user.username, "password": "NewPass12345!"}, format="json")
        self.assertEqual(login.status_code, 200)

    def test_password_change_endpoint(self):
        response = self.client.post(
            reverse("password-change"),
            {"current_password": "pass12345", "new_password": "ChangedPass12345!"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        client = APIClient()
        login = client.post(reverse("login"), {"username": self.user.username, "password": "ChangedPass12345!"}, format="json")
        self.assertEqual(login.status_code, 200)

    def test_me_update_sanitizes_profile_fields(self):
        response = self.client.patch(
            reverse("me"),
            {
                "first_name": "  Ali   ",
                "last_name": "  Test\r\n",
                "profile": {
                    "display_name": "  Dev   User ",
                    "bio": "hello  \nworld  ",
                    "status_message": " online   now ",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Ali")
        self.assertEqual(self.user.last_name, "Test")
        self.assertEqual(self.user.profile.display_name, "Dev User")
        self.assertEqual(self.user.profile.bio, "hello\nworld")
        self.assertEqual(self.user.profile.status_message, "online now")

    @patch("apps.accounts.api.views._verify_social_id_token")
    def test_social_login_creates_verified_account(self, mock_verify_social):
        mock_verify_social.return_value = {
            "provider_user_id": "google-sub-1",
            "email": "socialuser@example.com",
            "email_verified": True,
            "first_name": "Social",
            "last_name": "User",
            "display_name": "Social User",
            "picture": "",
            "claims": {"sub": "google-sub-1", "email": "socialuser@example.com"},
        }

        client = APIClient()
        response = client.post(
            reverse("social-login"),
            {"provider": "google", "id_token": "mock-id-token"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertTrue(User.objects.filter(email="socialuser@example.com", email_verified=True).exists())
        self.assertTrue(SocialAccount.objects.filter(provider="google", provider_user_id="google-sub-1").exists())

    def test_session_list_and_revoke_blocks_refresh(self):
        login_client = APIClient()
        login = login_client.post(
            reverse("login"),
            {"username": self.user.username, "password": "pass12345"},
            format="json",
            HTTP_X_DEVICE_ID="phone-1",
        )
        self.assertEqual(login.status_code, 200)
        session_id = login.data["session_id"]
        self.client.force_authenticate(self.user)
        sessions = self.client.get(reverse("session-list"))
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(sessions.data[0]["device_id"], "phone-1")
        revoke = self.client.post(reverse("session-revoke", kwargs={"session_id": session_id}), format="json")
        self.assertEqual(revoke.status_code, 200)
        refresh = login_client.post(reverse("refresh"), {"refresh": login.data["refresh"]}, format="json")
        self.assertEqual(refresh.status_code, 401)

    @override_settings(CENTRAL_AUTH_ENABLED=False)
    def test_revoked_session_blocks_existing_access_token(self):
        client = APIClient()
        login = client.post(
            reverse("login"),
            {"username": self.user.username, "password": "pass12345"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        self.assertEqual(client.get(reverse("me")).status_code, 200)

        UserSession.objects.filter(id=login.data["session_id"]).update(revoked_at=timezone.now())
        rejected = client.get(reverse("me"))
        self.assertEqual(rejected.status_code, 401)
        self.assertIn("Session expired", str(rejected.data))

    def test_refresh_rotation_rejects_reuse_of_previous_refresh_token(self):
        client = APIClient()
        login = client.post(
            reverse("login"),
            {"username": self.user.username, "password": "pass12345"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        first_refresh = login.data["refresh"]

        rotated = client.post(reverse("refresh"), {"refresh": first_refresh}, format="json")
        self.assertEqual(rotated.status_code, 200)
        self.assertIn("refresh", rotated.data)
        self.assertNotEqual(first_refresh, rotated.data["refresh"])

        replay = client.post(reverse("refresh"), {"refresh": first_refresh}, format="json")
        self.assertEqual(replay.status_code, 401)

    def test_account_export_and_delete(self):
        login_client = APIClient()
        login = login_client.post(reverse("login"), {"username": self.user.username, "password": "pass12345"}, format="json")
        self.assertEqual(login.status_code, 200)
        self.create_direct_conversation()
        AuthActionToken.objects.create(
            user=self.user,
            purpose=AuthActionToken.Purpose.PASSWORD_RESET,
            token_hash="a" * 64,
            email=self.user.email,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        SocialAccount.objects.create(
            user=self.user,
            provider=SocialAccount.Provider.GOOGLE,
            provider_user_id="google-delete-test",
            email=self.user.email,
        )
        UserDevice.objects.create(user=self.user, platform="web", push_token="delete-token", is_active=True)
        PendingUpload.objects.create(
            user=self.user,
            file=SimpleUploadedFile("note.txt", b"hello", content_type="text/plain"),
            original_name="note.txt",
            mime_type="text/plain",
            size=5,
            extension="txt",
            scan_status=PendingUpload.ScanStatus.CLEAN,
        )
        self.client.force_authenticate(self.user)
        export = self.client.get(reverse("account-export"))
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export.data["user"]["username"], self.user.username)
        self.assertIn("nearby_discovery_enabled", export.data["profile"])
        self.assertEqual(len(export.data["sessions"]), 1)
        delete = self.client.post(reverse("account-delete"), {"password": "pass12345"}, format="json")
        self.assertEqual(delete.status_code, 204)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(self.user.username.startswith("deleted_"))
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.is_discoverable)
        self.assertFalse(self.user.profile.show_online_status)
        self.assertFalse(self.user.profile.nearby_discovery_enabled)
        self.assertTrue(UserSession.objects.filter(user=self.user, revoked_at__isnull=False).exists())
        self.assertFalse(AuthActionToken.objects.filter(user=self.user).exists())
        self.assertFalse(SocialAccount.objects.filter(user=self.user).exists())
        self.assertFalse(UserDevice.objects.filter(user=self.user, is_active=True).exists())
        self.assertTrue(PendingUpload.objects.filter(user=self.user, status=PendingUpload.UploadStatus.REJECTED).exists())
        self.assertFalse(ConversationParticipant.objects.filter(user=self.user, left_at__isnull=True).exists())

    def test_calling_config_query_param_can_override_saved_quality_preference(self):
        self.client.patch(
            reverse("notification-preferences"),
            {"call_quality_preference": "low"},
            format="json",
        )
        response = self.client.get(reverse("calling-config"), {"quality": "clear"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["selected_quality_preset"], "clear")
        self.assertEqual(response.data["applied_quality_profile"]["video"]["max_height"], 720)

    def test_call_quality_update_signal_updates_participant_state(self):
        conversation = self.create_direct_conversation()
        response = self.client.post(reverse("call-start", kwargs={"conversation_id": conversation["id"]}), {"call_type": "video"}, format="json")
        self.assertEqual(response.status_code, 201)
        call_id = response.data["id"]
        self.client.force_authenticate(self.other)
        accept = self.client.post(reverse("call-accept", kwargs={"call_id": call_id}), format="json")
        self.assertEqual(accept.status_code, 200)
        quality = self.client.post(
            reverse("call-signal", kwargs={"call_id": call_id}),
            {
                "signal_type": "quality_update",
                "payload": {
                    "network_quality": "poor",
                    "preferred_video_quality": "low",
                    "audio_enabled": True,
                    "video_enabled": False,
                    "metrics": {"packet_loss": 0.18, "rtt_ms": 520},
                },
            },
            format="json",
        )
        self.assertEqual(quality.status_code, 202)
        self.assertEqual(quality.data["payload"]["network_quality"], "poor")
        self.assertEqual(quality.data["payload"]["recommendation"]["mode"], "audio_only")
        participant = CallParticipant.objects.get(call_id=call_id, user=self.other)
        self.assertEqual(participant.network_quality, "poor")
        self.assertFalse(participant.video_enabled)


class CallingV15UpgradeTests(APITestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="caller_v15", email="caller_v15@example.com", password="pass1234")
        self.user2 = User.objects.create_user(username="callee_v15", email="callee_v15@example.com", password="pass1234")
        self.client.force_authenticate(self.user1)
        self.conversation = Conversation.objects.create(type=Conversation.ConversationType.DIRECT, created_by=self.user1, direct_key=f"{self.user1.id}:{self.user2.id}")
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.user1, role=ConversationParticipant.Role.OWNER)
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.user2, role=ConversationParticipant.Role.MEMBER)
        self.call = start_call(self.user1, self.conversation, CallSession.CallType.VIDEO)

    def test_call_heartbeat_endpoint(self):
        url = reverse("chat:call-heartbeat", kwargs={"call_id": self.call.id})
        response = self.client.post(url, {"network_quality": "good", "metrics": {"rtt_ms": 120}}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["network_quality"], "good")

    def test_call_media_state_endpoint(self):
        accept_call(self.user2, self.call)
        url = reverse("chat:call-media-state", kwargs={"call_id": self.call.id})
        response = self.client.post(url, {"audio_enabled": False, "is_on_hold": True, "preferred_video_quality": "low"}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.data["audio_enabled"])
        self.assertTrue(response.data["is_on_hold"])

    def test_turn_credentials_endpoint(self):
        url = "/api/v1/chat/calls/turn-credentials/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("configured", response.data)

    @override_settings(
        TURN_URIS_JSON='["turn:turn.example.com:3478?transport=udp"]',
        TURN_SHARED_SECRET="",
        TURN_STATIC_USERNAME="turn-user",
        TURN_STATIC_PASSWORD="turn-pass",
    )
    def test_turn_credentials_endpoint_returns_static_ice_servers(self):
        url = "/api/v1/chat/calls/turn-credentials/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["configured"])
        self.assertEqual(response.data["ice_servers"][0]["username"], "turn-user")
        self.assertEqual(response.data["ice_servers"][0]["credential"], "turn-pass")

    def test_call_diagnostics_endpoint(self):
        url = reverse("chat:call-diagnostics", kwargs={"call_id": self.call.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(self.call.id), str(response.data["call_id"]))



class CallingV16UpgradeTests(APITestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(email="v16a@example.com", password="pass12345")
        self.user2 = User.objects.create_user(email="v16b@example.com", password="pass12345")
        self.conversation = create_direct_conversation(self.user1, self.user2)
        self.call = start_call(self.user1, self.conversation, CallSession.CallType.VIDEO)
        accept_call(self.user2, self.call)

    def test_speaker_state_endpoint_updates_orchestration(self):
        self.client.force_authenticate(self.user1)
        url = reverse("chat:call-speaker-state", kwargs={"call_id": self.call.id})
        response = self.client.post(url, {"speaking_level": 74, "is_speaking": True}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user_id"], str(self.user1.id))
        self.assertTrue(response.data["orchestration"]["active_speaker_user_id"])

    def test_orchestration_endpoint_returns_payload(self):
        self.client.force_authenticate(self.user1)
        url = reverse("chat:call-orchestration", kwargs={"call_id": self.call.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["call_id"], str(self.call.id))
        self.assertIn("layout_mode", response.data)
        self.assertIn("participants", response.data)

    def test_orchestration_does_not_persist_signal_payloads_server_side(self):
        send_call_signal(
            self.user1,
            self.call,
            "offer",
            {
                "to_user_id": str(self.user2.id),
                "sdp": "secret-offer",
                "description": {"type": "offer", "sdp": "secret-offer"},
            },
        )

        self.client.force_authenticate(self.user2)
        response = self.client.get(reverse("chat:call-orchestration", kwargs={"call_id": self.call.id}))
        self.assertEqual(response.status_code, 200)
        self.assertIn("signals", response.data)
        self.assertEqual(response.data["signals"][0]["payload"]["sdp"], "secret-offer")

        self.call.refresh_from_db()
        persisted = (self.call.metadata or {}).get("orchestration", {})
        self.assertNotIn("signals", persisted)
        self.assertNotIn("secret-offer", str(self.call.metadata or {}))


class CallingV17UpgradeTests(APITestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(email="v17a@example.com", password="pass12345")
        self.user2 = User.objects.create_user(email="v17b@example.com", password="pass12345")
        self.conversation = create_direct_conversation(self.user1, self.user2)
        self.call = start_call(self.user1, self.conversation, CallSession.CallType.VIDEO)
        accept_call(self.user2, self.call)

    def test_media_state_can_raise_hand_and_share_screen(self):
        self.client.force_authenticate(self.user1)
        url = reverse("chat:call-media-state", kwargs={"call_id": self.call.id})
        response = self.client.post(url, {
            "screen_share_enabled": True,
            "hand_raised": True,
            "connection_state": "connected",
            "audio_route": "speaker",
        }, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.data["screen_share_enabled"])
        self.assertTrue(response.data["hand_raised"])
        self.assertEqual(response.data["connection_state"], "connected")
        self.assertEqual(response.data["audio_route"], "speaker")

    def test_orchestration_exposes_primary_content_user(self):
        self.client.force_authenticate(self.user1)
        url = reverse("chat:call-media-state", kwargs={"call_id": self.call.id})
        self.client.post(url, {"screen_share_enabled": True}, format="json")
        orch = self.client.get(reverse("chat:call-orchestration", kwargs={"call_id": self.call.id}))
        self.assertEqual(orch.status_code, 200)
        self.assertEqual(orch.data["primary_content_user_id"], str(self.user1.id))

class MediaRangeParsingTests(TestCase):
    def test_parses_standard_open_and_suffix_ranges(self):
        from apps.chat.api.views import _parse_single_byte_range

        self.assertEqual(_parse_single_byte_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(_parse_single_byte_range("bytes=90-", 100), (90, 99))
        self.assertEqual(_parse_single_byte_range("bytes=-10", 100), (90, 99))

    def test_rejects_unsatisfiable_ranges(self):
        from apps.chat.api.views import _parse_single_byte_range

        self.assertEqual(_parse_single_byte_range("bytes=100-120", 100), "unsatisfiable")
        self.assertEqual(_parse_single_byte_range("bytes=20-10", 100), "unsatisfiable")


class PresenceAndAvatarProductionHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="privacy-a", email="privacy-a@example.com", password="pass12345")
        self.other = User.objects.create_user(username="privacy-b", email="privacy-b@example.com", password="pass12345")
        self.client.force_authenticate(self.user)
        self.conversation = create_direct_conversation(self.user, self.other)
        FriendRequest.objects.create(sender=self.user, receiver=self.other, status=FriendRequest.Status.ACCEPTED)

    def tearDown(self):
        clear_presence(self.user, device_id="test")
        clear_presence(self.other, device_id="test")
        cache.clear()

    def test_block_relationship_hides_presence_in_conversation_and_friend_list(self):
        set_presence(self.other, device_id="test")
        UserBlock.objects.create(blocker=self.user, blocked=self.other, reason="privacy test")

        detail = self.client.get(reverse("conversation-detail", kwargs={"pk": self.conversation.id}))
        self.assertEqual(detail.status_code, 200)
        peer = next(
            item["user"]
            for item in detail.data["participants"]
            if str(item["user"]["id"]) == str(self.other.id)
        )
        self.assertFalse(peer["is_online"])
        self.assertEqual(peer["active_devices"], 0)
        self.assertIsNone(peer["last_seen_at"])
        self.assertEqual(peer["presence_visibility"], "hidden")

        friends = self.client.get(reverse("friend-request-list-create"), {"scope": "friends"})
        self.assertEqual(friends.status_code, 200)
        rows = friends.data.get("results", friends.data) if isinstance(friends.data, dict) else friends.data
        self.assertEqual(list(rows), [])

    def test_presence_recipients_exclude_block_relationships(self):
        self.assertIn(str(self.other.id), presence_recipient_ids(self.user))
        UserBlock.objects.create(blocker=self.other, blocked=self.user)
        self.assertNotIn(str(self.other.id), presence_recipient_ids(self.user))
        self.assertNotIn(str(self.user.id), presence_recipient_ids(self.other))

    def test_avatar_upload_replaces_old_file_and_removal_cleans_storage(self):
        def make_image(name, size, color):
            output = BytesIO()
            Image.new("RGB", size, color=color).save(output, format="PNG")
            return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")

        with TemporaryDirectory() as temp_dir, override_settings(MEDIA_ROOT=Path(temp_dir)):
            with override_settings(PROFILE_AVATAR_MAX_PIXELS=10_000):
                oversized = self.client.put(
                    reverse("me-avatar"),
                    {"avatar": make_image("oversized.png", (200, 200), "black")},
                    format="multipart",
                )
                self.assertEqual(oversized.status_code, 400)

            first = self.client.put(
                reverse("me-avatar"),
                {"avatar": make_image("first.png", (1800, 1200), "red")},
                format="multipart",
            )
            self.assertEqual(first.status_code, 200)
            self.user.profile.refresh_from_db()
            first_name = self.user.profile.avatar.name
            first_path = Path(temp_dir) / first_name
            self.assertTrue(first_name.endswith(".webp"))
            self.assertTrue(first_path.exists())
            with Image.open(first_path) as avatar:
                self.assertLessEqual(max(avatar.size), 1024)

            second = self.client.put(
                reverse("me-avatar"),
                {"avatar": make_image("second.png", (320, 320), "blue")},
                format="multipart",
            )
            self.assertEqual(second.status_code, 200)
            self.user.profile.refresh_from_db()
            second_path = Path(temp_dir) / self.user.profile.avatar.name
            self.assertTrue(second_path.exists())
            self.assertFalse(first_path.exists())

            removed = self.client.delete(reverse("me-avatar"))
            self.assertEqual(removed.status_code, 200)
            self.user.profile.refresh_from_db()
            self.assertFalse(self.user.profile.avatar)
            self.assertFalse(second_path.exists())
