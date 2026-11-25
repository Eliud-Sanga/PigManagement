from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.services import create_company

from apps.control_center.models import (
    AccountLoginThrottle,
    SecurityAuditEvent,
    SecuritySettings,
)
from apps.control_center.services import (
    append_security_event,
)


User = get_user_model()


class SecurityCoreTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="platform-admin",
            email="admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        create_company(
            actor=self.admin,
            name="Security Company",
            code="security-company",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

        self.policy = (
            SecuritySettings.get_solo()
        )

    def test_login_accepts_email(self):
        response = self.client.post(
            reverse("users:login"),
            {
                "identifier":
                    self.owner.email,
                "password":
                    "OwnerPassword123!",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertTrue(
            SecurityAuditEvent.objects.filter(
                action="LOGIN_ATTEMPT",
                result=(
                    SecurityAuditEvent
                    .Result
                    .SUCCESS
                ),
            ).exists()
        )

    def test_three_failed_passwords_lock_account(self):
        self.policy.max_failed_login_attempts = 3
        self.policy.login_lock_minutes = 30
        self.policy.save()

        for _ in range(3):
            self.client.post(
                reverse("users:login"),
                {
                    "identifier":
                        self.owner.username,
                    "password":
                        "WrongPassword!",
                },
            )

        state = (
            AccountLoginThrottle.objects
            .get(user=self.owner)
        )

        self.assertEqual(
            state.failed_attempts,
            3,
        )

        self.assertGreater(
            state.locked_until,
            timezone.now(),
        )

        response = self.client.post(
            reverse("users:login"),
            {
                "identifier":
                    self.owner.username,
                "password":
                    "OwnerPassword123!",
            },
        )

        self.assertEqual(
            response.status_code,
            429,
        )

    def test_admin_can_login_during_maintenance(self):
        self.policy.maintenance_enabled = True
        self.policy.maintenance_ends_at = (
            timezone.now()
            + timedelta(hours=1)
        )
        self.policy.save()

        response = self.client.post(
            reverse("users:login"),
            {
                "identifier":
                    self.admin.email,
                "password":
                    "AdminPassword123!",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

    def test_normal_user_blocked_during_maintenance(self):
        self.policy.maintenance_enabled = True
        self.policy.maintenance_ends_at = (
            timezone.now()
            + timedelta(hours=1)
        )
        self.policy.save()

        response = self.client.post(
            reverse("users:login"),
            {
                "identifier":
                    self.owner.email,
                "password":
                    "OwnerPassword123!",
            },
        )

        self.assertEqual(
            response.status_code,
            503,
        )

    def test_security_settings_save_requires_admin_password(self):
        self.client.force_login(
            self.admin,
            backend=(
                "apps.companies.backends."
                "CompanyRoleBackend"
            ),
        )

        response = self.client.post(
            reverse(
                "users:security_settings"
            ),
            {
                "max_failed_login_attempts": 4,
                "failed_attempt_window_minutes": 15,
                "login_lock_minutes": 30,
                "device_cookie_days": 365,
                "maintenance_message": "Maintenance",
                "admin_password": "WrongPassword!",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.policy.refresh_from_db()

        self.assertEqual(
            self.policy.max_failed_login_attempts,
            3,
        )

    def test_self_password_change_requires_current_password(self):
        self.client.force_login(
            self.owner,
            backend=(
                "apps.companies.backends."
                "CompanyRoleBackend"
            ),
        )

        url = reverse(
            "users:user_change_password",
            args=[self.owner.pk],
        )

        denied = self.client.post(
            url,
            {
                "current_password":
                    "WrongPassword!",
                "new_password1":
                    "AnotherStrongPassword123!",
                "new_password2":
                    "AnotherStrongPassword123!",
            },
        )

        self.assertEqual(
            denied.status_code,
            200,
        )

        accepted = self.client.post(
            url,
            {
                "current_password":
                    "OwnerPassword123!",
                "new_password1":
                    "AnotherStrongPassword123!",
                "new_password2":
                    "AnotherStrongPassword123!",
            },
        )

        self.assertEqual(
            accepted.status_code,
            302,
        )

        self.owner.refresh_from_db()

        self.assertTrue(
            self.owner.check_password(
                "AnotherStrongPassword123!"
            )
        )

    def test_security_audit_event_cannot_be_changed_or_deleted(self):
        event = append_security_event(
            category=(
                SecurityAuditEvent
                .Category
                .SECURITY
            ),
            action="TEST_EVENT",
            result=(
                SecurityAuditEvent
                .Result
                .SUCCESS
            ),
            actor=self.admin,
        )

        event.description = "tamper"

        with self.assertRaises(
            ValidationError
        ):
            event.save()

        with self.assertRaises(
            ValidationError
        ):
            event.delete()

        with self.assertRaises(
            ValidationError
        ):
            SecurityAuditEvent.objects.filter(
                pk=event.pk
            ).update(
                description="tamper"
            )

        with self.assertRaises(
            ValidationError
        ):
            SecurityAuditEvent.objects.filter(
                pk=event.pk
            ).delete()
