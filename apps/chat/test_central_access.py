from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory

from apps.chat.central_access import get_product_access_decision, require_product_access
from config.centralization import CentralServiceResponse


class CentralAccessTests(SimpleTestCase):
    @override_settings(CENTRAL_ACCESS_MODE="enforce", CENTRAL_PAYMENTS_ENABLED=True)
    def test_require_product_access_records_usage_payload(self):
        request = APIRequestFactory().post("/", HTTP_AUTHORIZATION="Bearer central-token")
        with patch("apps.chat.central_access.check_product_access") as check:
            check.return_value = CentralServiceResponse(200, {"allowed": True, "reason": "allowed"})

            decision = require_product_access(
                request,
                "send_message",
                record_usage=True,
                idempotency_key="client-1",
                metadata={"conversation_id": "abc"},
            )

        self.assertTrue(decision["allowed"])
        check.assert_called_once_with(
            request,
            "send_message",
            metadata={"conversation_id": "abc"},
            quantity=1,
            record_usage=True,
            idempotency_key="client-1",
        )

    @override_settings(CENTRAL_ACCESS_MODE="enforce", CENTRAL_PAYMENTS_ENABLED=True)
    def test_require_product_access_denies_rejected_decision(self):
        request = APIRequestFactory().post("/", HTTP_AUTHORIZATION="Bearer central-token")
        with patch("apps.chat.central_access.check_product_access") as check:
            check.return_value = CentralServiceResponse(200, {"allowed": False, "reason": "limit_exceeded"})

            with self.assertRaises(PermissionDenied):
                require_product_access(request, "send_message")

    @override_settings(CENTRAL_ACCESS_MODE="observe", CENTRAL_PAYMENTS_ENABLED=True)
    def test_observe_mode_allows_without_blocking_on_central_access(self):
        request = APIRequestFactory().post("/", HTTP_AUTHORIZATION="Bearer central-token")
        with patch("apps.chat.central_access.check_product_access") as check:
            decision = get_product_access_decision(request, "send_message", record_usage=True)

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason"], "central_access_observe")
        check.assert_not_called()
