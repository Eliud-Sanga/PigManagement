from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponseRedirect
from django.test import RequestFactory, SimpleTestCase, override_settings

from . import views


class MobileAuthContractTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_session_denies_anonymous_user(self):
        request = self.factory.get("/api/mobile/v1/auth/session/")
        request.user = AnonymousUser()

        response = views.session(request)

        self.assertEqual(response.status_code, 401)
        self.assertIn(b"authentication_required", response.content)

    def test_csrf_bootstrap_sets_configured_cookie(self):
        request = self.factory.get("/api/mobile/v1/auth/csrf/")

        response = views.csrf(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"csrfToken", response.content)
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)

    def test_login_without_csrf_is_rejected(self):
        request = self.factory.post(
            "/api/mobile/v1/auth/login/",
            data={
                "identifier": "test",
                "password": "test",
            },
        )
        request.user = AnonymousUser()

        response = views.login(request)

        self.assertEqual(response.status_code, 403)

    @patch("apps.mobile_api.views.get_user")
    @patch("apps.mobile_api.views.secure_login")
    def test_success_delegates_to_existing_login(
        self,
        existing_login,
        get_user,
    ):
        request = self.factory.post(
            "/api/mobile/v1/auth/login/",
            data={
                "identifier": "test",
                "password": "test",
            },
        )
        request._dont_enforce_csrf_checks = True
        request.user = AnonymousUser()

        existing_login.return_value = HttpResponseRedirect(
            "/accounts/company/"
        )
        existing_login.return_value.set_cookie(
            "pigs_device",
            "test-device-cookie",
        )

        get_user.return_value = SimpleNamespace(
            is_authenticated=True,
            must_change_password=False,
        )

        response = views.login(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"authenticated": true', response.content)
        self.assertEqual(
            response.cookies["pigs_device"].value,
            "test-device-cookie",
        )
        existing_login.assert_called_once_with(request)
        get_user.assert_called_once_with(request)

    @override_settings(DEBUG=True)
    def test_dev_web_origin_gets_exact_cors_headers(self):
        request = self.factory.get(
            "/api/mobile/v1/auth/csrf/",
            HTTP_ORIGIN="http://localhost:5173",
        )

        response = views.csrf(request)

        self.assertEqual(
            response["Access-Control-Allow-Origin"],
            "http://localhost:5173",
        )
        self.assertEqual(
            response["Access-Control-Allow-Credentials"],
            "true",
        )

    @override_settings(
        DEBUG=True,
        CSRF_TRUSTED_ORIGINS=["http://10.107.214.168:5173"],
    )
    def test_trusted_lan_web_origin_gets_cors_headers(self):
        request = self.factory.get(
            "/api/mobile/v1/auth/csrf/",
            HTTP_ORIGIN="http://10.107.214.168:5173",
        )
        response = views.csrf(request)
        self.assertEqual(
            response["Access-Control-Allow-Origin"],
            "http://10.107.214.168:5173",
        )
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")

    @override_settings(
        DEBUG=False,
        CSRF_TRUSTED_ORIGINS=["http://10.107.214.168:5173"],
    )
    def test_trusted_lan_web_origin_not_enabled_in_production(self):
        request = self.factory.get(
            "/api/mobile/v1/auth/csrf/",
            HTTP_ORIGIN="http://10.107.214.168:5173",
        )
        response = views.csrf(request)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    @override_settings(DEBUG=True)
    def test_untrusted_origin_gets_no_cors_header(self):
        request = self.factory.get(
            "/api/mobile/v1/auth/csrf/",
            HTTP_ORIGIN="https://example.invalid",
        )

        response = views.csrf(request)

        self.assertNotIn(
            "Access-Control-Allow-Origin",
            response,
        )
