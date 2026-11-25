from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.control_center.models import SecurityAuditEvent
from apps.control_center.services import append_security_event


User = get_user_model()


class AdminHeaderDashboardPolishTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="header-admin",
            email="header-admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        for index in range(12):
            append_security_event(
                category=SecurityAuditEvent.Category.ADMIN,
                action=f"HEADER_TEST_{index:02d}",
                result=SecurityAuditEvent.Result.SUCCESS,
                severity=SecurityAuditEvent.Severity.INFO,
                actor=self.admin,
                reason_code="UI_TEST",
                description="Admin header/dashboard regression.",
            )

        self.client.force_login(self.admin)

    def test_header_structure(self):
        response = self.client.get(reverse("users:control_center"))
        self.assertEqual(response.status_code, 200)
        for marker in (
            'class="control-nav-toggle cc-hamburger"',
            'class="cc-header-title"',
            'class="cc-language-switch"',
            'class="cc-header-profile"',
            'class="nav cc-admin-drawer"',
        ):
            self.assertContains(response, marker)

    def test_dashboard_recent_audit_is_ten(self):
        response = self.client.get(reverse("users:control_center"))
        events = list(response.context["recent_security_events"])
        self.assertEqual(len(events), 10)
        self.assertEqual(events[0].action, "HEADER_TEST_11")
        self.assertEqual(events[-1].action, "HEADER_TEST_02")

    def test_three_info_cards_share_responsive_row(self):
        response = self.client.get(reverse("users:control_center"))
        self.assertContains(response, 'class="cc-f3-status-row"')
        self.assertContains(response, "data-status-card", count=3)
