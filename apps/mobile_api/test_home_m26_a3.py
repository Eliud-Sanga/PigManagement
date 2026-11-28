"""PostgreSQL, money, localization and mobile session policy regressions."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone, translation
from django.urls import reverse

from apps.control_center.middleware import (
    SESSION_STARTED_AT_KEY, SESSION_LAST_ACTIVITY_AT_KEY,
)
from apps.control_center.models import SecuritySettings, SecurityAuditEvent
from apps.transport.models import Cargo


class MoneyAndCatalogTests(SimpleTestCase):
    def test_cargo_line_rounding(self):
        for quantity, unit_price, expected in (
            ("1.00", "0.00", "0.00"),
            ("1.25", "2.50", "3.13"),
            ("1.20", "100.00", "120.00"),
            ("0.01", "0.49", "0.00"),
        ):
            cargo = Cargo(quantity=Decimal(quantity), unit_price=Decimal(unit_price))
            self.assertEqual(cargo.calculated_amount, Decimal(expected))
            self.assertEqual(cargo.calculated_amount.as_tuple().exponent, -2)
        cargo = Cargo(pricing_method=Cargo.PricingMethod.TOTAL, original_amount=Decimal("12.34"))
        self.assertEqual(cargo.calculated_amount, Decimal("12.34"))

    def test_catalog_available_and_english_restored(self):
        with translation.override("sw"):
            self.assertEqual(translation.gettext("Policy Console"), "Kituo cha Sera")
        with translation.override("en"):
            self.assertEqual(translation.gettext("Policy Console"), "Policy Console")

    def test_security_middleware_enabled(self):
        self.assertIn("apps.control_center.middleware.SessionPolicyMiddleware", settings.MIDDLEWARE)
        self.assertIn("django.middleware.locale.LocaleMiddleware", settings.MIDDLEWARE)


class MobileSessionPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create(username="m26-a3-admin", email="m26-a3@example.invalid", is_platform_admin=True)
        policy = SecuritySettings.get_solo()
        policy.idle_timeout_minutes = 5
        policy.absolute_session_timeout_minutes = 30
        policy.save()

    def setUp(self):
        self.client.force_login(self.admin)

    def test_login_initializes_clocks(self):
        session = self.client.session
        self.assertIn(SESSION_STARTED_AT_KEY, session)
        self.assertEqual(session[SESSION_STARTED_AT_KEY], session[SESSION_LAST_ACTIVITY_AT_KEY])

    def _set_age(self, start, idle):
        session = self.client.session
        now = int(timezone.now().timestamp())
        session[SESSION_STARTED_AT_KEY] = now - start
        session[SESSION_LAST_ACTIVITY_AT_KEY] = now - idle
        session.save()

    def test_mobile_idle_expiration_is_json_and_logged_out(self):
        self._set_age(600, 301)
        response = self.client.get(reverse("mobile_api:session"), secure=True)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "authentication_required")
        self.assertEqual(response.json()["reason"], "SESSION_IDLE_LIMIT_REACHED")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(SecurityAuditEvent.objects.filter(action="SESSION_IDLE_TIMEOUT").exists())

    def test_mobile_absolute_expiration_is_not_extended_by_activity(self):
        self._set_age(1801, 1)
        response = self.client.get(reverse("mobile_api:session"), secure=True)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["reason"], "SESSION_MAX_LIFETIME_REACHED")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_browser_close_cookie_preserved(self):
        session = self.client.session
        session.set_expiry(0)
        session.save()
        response = self.client.get(reverse("mobile_api:session"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.session.get_expire_at_browser_close())
