from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import CompanyMembership
from apps.companies.services import create_company
from apps.control_center.middleware import (
    SESSION_LAST_ACTIVITY_AT_KEY,
    SESSION_STARTED_AT_KEY,
)
from apps.control_center.models import (
    SecurityAuditEvent,
    SecuritySettings,
)

User = get_user_model()


class AdminPolicyConsoleV1Tests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="policy-admin",
            email="policy-admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
            preferred_language="en",
        )

        self.owner = User.objects.create_user(
            username="policy-owner",
            email="policy-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
            preferred_language="en",
        )

        self.company = create_company(
            actor=self.admin,
            name="Policy Company",
            code="policy-company",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

        self.membership = (
            CompanyMembership.objects
            .get(
                company=self.company,
                user=self.owner,
            )
        )

        self.membership.approved_language = "sw"
        self.membership.save(
            update_fields=[
                "approved_language",
                "updated_at",
            ]
        )

    def test_security_policy_has_session_defaults(self):
        policy = SecuritySettings.get_solo()
        self.assertEqual(
            policy.idle_timeout_minutes,
            30,
        )
        self.assertEqual(
            policy.absolute_session_timeout_minutes,
            720,
        )

    def test_policy_console_platform_admin_only(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("users:policy_console")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("users:security_settings"),
        )
        self.assertContains(
            response,
            "cc-policy-summary",
        )

        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("users:policy_console")
        )
        self.assertEqual(response.status_code, 403)

    def test_user_preference_wins_over_membership_language_when_allowed(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("users:company_hub")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.wsgi_request.LANGUAGE_CODE,
            "en",
        )

    def test_language_switch_updates_preference_and_cookie(self):
        self.owner.preferred_language = "sw"
        self.owner.save(
            update_fields=[
                "preferred_language",
                "updated_at",
            ]
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("users:language_switch"),
            {
                "language": "en",
                "next": reverse(
                    "users:company_hub"
                ),
            },
        )

        self.assertEqual(response.status_code, 302)

        self.owner.refresh_from_db()
        self.assertEqual(
            self.owner.preferred_language,
            "en",
        )

        self.assertEqual(
            response.cookies[
                "django_language"
            ].value,
            "en",
        )

    def test_idle_timeout_logs_out_and_audits(self):
        policy = SecuritySettings.get_solo()
        policy.idle_timeout_minutes = 5
        policy.absolute_session_timeout_minutes = 60
        policy.save()

        self.client.force_login(self.owner)

        session = self.client.session
        now_ts = int(
            timezone.now().timestamp()
        )
        session[
            SESSION_STARTED_AT_KEY
        ] = now_ts - 60
        session[
            SESSION_LAST_ACTIVITY_AT_KEY
        ] = now_ts - (6 * 60)
        session.save()

        response = self.client.get(
            reverse("users:company_hub")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("users:login"),
        )
        self.assertNotIn(
            "_auth_user_id",
            self.client.session,
        )
        self.assertTrue(
            SecurityAuditEvent.objects.filter(
                action="SESSION_IDLE_TIMEOUT"
            ).exists()
        )

    def test_absolute_timeout_logs_out_even_when_recently_active(self):
        policy = SecuritySettings.get_solo()
        policy.idle_timeout_minutes = 30
        policy.absolute_session_timeout_minutes = 60
        policy.save()

        self.client.force_login(self.owner)

        session = self.client.session
        now_ts = int(
            timezone.now().timestamp()
        )
        session[
            SESSION_STARTED_AT_KEY
        ] = now_ts - (61 * 60)
        session[
            SESSION_LAST_ACTIVITY_AT_KEY
        ] = now_ts - 10
        session.save()

        response = self.client.get(
            reverse("users:company_hub")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("users:login"),
        )
        self.assertTrue(
            SecurityAuditEvent.objects.filter(
                action="SESSION_ABSOLUTE_TIMEOUT"
            ).exists()
        )

    def test_company_admin_page_exposes_governance_controls(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse(
                "users:company_detail",
                args=[self.company.public_id],
            )
        )

        self.assertEqual(response.status_code, 200)

        self.assertContains(
            response,
            reverse(
                "users:company_owner_user_management_policy",
                args=[self.company.public_id],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "users:company_roles",
                args=[self.company.public_id],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "users:company_languages",
                args=[self.company.public_id],
            ),
        )
        self.assertContains(
            response,
            "cc-owner-toggle-card",
        )

    def test_swahili_catalog_renders_translated_policy_labels(self):
        self.admin.preferred_language = "sw"
        self.admin.save(
            update_fields=[
                "preferred_language",
                "updated_at",
            ]
        )

        self.client.force_login(self.admin)
        self.client.cookies["django_language"] = "sw"

        policy_response = self.client.get(
            reverse("users:policy_console")
        )
        self.assertEqual(
            policy_response.status_code,
            200,
        )
        self.assertContains(
            policy_response,
            "Kituo cha Sera",
        )

        company_response = self.client.get(
            reverse(
                "users:company_detail",
                args=[self.company.public_id],
            )
        )
        self.assertEqual(
            company_response.status_code,
            200,
        )
        self.assertContains(
            company_response,
            "Usimamizi wa Users kwa Owner",
        )
