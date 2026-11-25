from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.companies.services import create_company
from apps.users.models import GoogleIdentity, User


class GoogleAuthBridgeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="platform-admin-google",
            email="platform-google@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )
        self.owner = User.objects.create_user(
            username="google-owner",
            email="owner.google@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )
        self.company = create_company(
            actor=self.admin,
            name="Google Login Farm",
            code="google-login-farm",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

    @override_settings(BIZMANAGER_GOOGLE_CLIENT_IDS=())
    def test_provider_is_safely_disabled_without_client_ids(self):
        response = self.client.get("/api/mobile/v1/auth/providers/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["google"]["enabled"])

    @override_settings(BIZMANAGER_GOOGLE_CLIENT_IDS=("web-client-id",))
    @patch("apps.mobile_api.google_views._verify_google_token")
    def test_verified_google_email_links_only_existing_company_user(self, verify):
        verify.return_value = {
            "sub": "google-subject-001",
            "email": self.owner.email,
            "email_verified": True,
            "name": "Company Owner",
            "picture": "https://example.test/avatar.png",
        }

        response = self.client.post(
            "/api/mobile/v1/auth/google/",
            {
                "id_token": "verified-token",
                "remember_me": "on",
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["authenticated"])
        identity = GoogleIdentity.objects.get(user=self.owner)
        self.assertEqual(identity.subject, "google-subject-001")
        session_response = self.client.get("/api/mobile/v1/auth/session/")
        self.assertEqual(session_response.status_code, 200)
        self.assertEqual(
            session_response.json()["user"]["username"],
            self.owner.username,
        )

    @override_settings(BIZMANAGER_GOOGLE_CLIENT_IDS=("web-client-id",))
    @patch("apps.mobile_api.google_views._verify_google_token")
    def test_unknown_google_email_is_not_auto_provisioned(self, verify):
        verify.return_value = {
            "sub": "unknown-subject",
            "email": "unknown@example.test",
            "email_verified": True,
        }

        response = self.client.post(
            "/api/mobile/v1/auth/google/",
            {"id_token": "verified-token"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "google_account_not_linked")

    @override_settings(BIZMANAGER_GOOGLE_CLIENT_IDS=("web-client-id",))
    @patch("apps.mobile_api.google_views._verify_google_token")
    def test_google_transport_failure_returns_stable_service_error(self, verify):
        verify.side_effect = RuntimeError("provider unavailable")

        with patch("apps.mobile_api.google_views.logger.warning"):
            response = self.client.post(
                "/api/mobile/v1/auth/google/",
                {"id_token": "temporarily-unverifiable-token"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["code"],
            "google_verification_unavailable",
        )

    @override_settings(BIZMANAGER_GOOGLE_CLIENT_IDS=("web-client-id",))
    @patch("apps.mobile_api.google_views._verify_google_token")
    def test_platform_admin_google_identity_stays_control_plane_only(self, verify):
        verify.return_value = {
            "sub": "platform-subject",
            "email": self.admin.email,
            "email_verified": True,
        }

        response = self.client.post(
            "/api/mobile/v1/auth/google/",
            {"id_token": "verified-token"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "platform_admin_control_plane")

    @override_settings(BIZMANAGER_GOOGLE_CLIENT_IDS=("web-client-id",))
    @patch("apps.mobile_api.google_views._verify_google_token")
    def test_google_attempts_are_limited_even_with_spoofed_forwarded_ips(self, verify):
        verify.side_effect = ValueError("invalid")
        for attempt in range(10):
            response = self.client.post(
                "/api/mobile/v1/auth/google/",
                {"id_token": "invalid-token"},
                REMOTE_ADDR="192.0.2.1",
                HTTP_X_FORWARDED_FOR=f"198.51.100.{attempt}",
            )
            self.assertEqual(response.status_code, 401)
        blocked = self.client.post(
            "/api/mobile/v1/auth/google/",
            {"id_token": "invalid-token"},
            REMOTE_ADDR="192.0.2.1",
            HTTP_X_FORWARDED_FOR="203.0.113.2",
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["code"], "google_rate_limited")
        self.assertEqual(verify.call_count, 10)
