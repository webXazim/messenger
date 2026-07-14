import re

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User


@override_settings(AUTH_REQUIRE_EMAIL_VERIFICATION=False)
class UsernameUniquenessTests(APITestCase):
    availability_url = "/api/v1/auth/username-availability/"
    register_url = "/api/v1/auth/register/"
    login_url = "/api/v1/auth/token/"

    def setUp(self):
        self.user = User.objects.create_user(
            username="Azim",
            email="azim-existing@example.com",
            password="Strong-password-8472!",
            email_verified=True,
        )

    def test_availability_is_case_insensitive(self):
        unavailable = self.client.get(self.availability_url, {"username": "azim"})
        self.assertEqual(unavailable.status_code, status.HTTP_200_OK)
        self.assertFalse(unavailable.data["available"])

        available = self.client.get(self.availability_url, {"username": "azim-new"})
        self.assertEqual(available.status_code, status.HTTP_200_OK)
        self.assertTrue(available.data["available"])

    def test_registration_rejects_case_variant(self):
        response = self.client.post(
            self.register_url,
            {"username": "aZiM", "email": "azim-new@example.com", "password": "Strong-password-8472!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data.get("errors", {}))

    def test_login_accepts_case_variant(self):
        response = self.client.post(
            self.login_url,
            {"username": "azIM", "password": "Strong-password-8472!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_database_constraint_rejects_case_variant_race(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username="AZIM",
                    email="azim-race@example.com",
                    password="Strong-password-8472!",
                )


@override_settings(
    AUTH_REQUIRE_EMAIL_VERIFICATION=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@crescentsphere.com",
    EMAIL_VERIFY_OTP_TTL_SECONDS=600,
    EMAIL_VERIFY_OTP_MAX_ATTEMPTS=5,
)
class RegistrationOtpTests(APITestCase):
    register_url = "/api/v1/auth/register/"
    login_url = "/api/v1/auth/token/"
    confirm_url = "/api/v1/accounts/email/verify/confirm/"
    resend_url = "/api/v1/accounts/email/verify/request/"

    def register(self):
        return self.client.post(
            self.register_url,
            {
                "username": "otp-user",
                "email": "otp@example.com",
                "password": "Strong-password-8472!",
            },
            format="json",
        )

    def test_registration_requires_six_digit_code_before_login(self):
        response = self.register()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["email_verification_required"])
        self.assertEqual(len(mail.outbox), 1)
        code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)

        blocked_login = self.client.post(
            self.login_url,
            {"username": "otp-user", "password": "Strong-password-8472!"},
            format="json",
        )
        self.assertEqual(blocked_login.status_code, status.HTTP_401_UNAUTHORIZED)

        verified = self.client.post(
            self.confirm_url,
            {"email": "otp@example.com", "code": code},
            format="json",
        )
        self.assertEqual(verified.status_code, status.HTTP_200_OK)
        self.assertTrue(User.objects.get(username="otp-user").email_verified)

        login = self.client.post(
            self.login_url,
            {"username": "otp-user", "password": "Strong-password-8472!"},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_resend_invalidates_the_previous_code(self):
        self.register()
        first_code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

        response = self.client.post(self.resend_url, {"email": "otp@example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        second_code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

        old_code = self.client.post(
            self.confirm_url,
            {"email": "otp@example.com", "code": first_code},
            format="json",
        )
        self.assertEqual(old_code.status_code, status.HTTP_401_UNAUTHORIZED)

        new_code = self.client.post(
            self.confirm_url,
            {"email": "otp@example.com", "code": second_code},
            format="json",
        )
        self.assertEqual(new_code.status_code, status.HTTP_200_OK)
