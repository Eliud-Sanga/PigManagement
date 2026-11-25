import re

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Company
from apps.companies.onboarding_models import CompanyOwnerInvitation
from apps.companies.onboarding_services import create_owner_invitation
from apps.users.models import User


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Home Management <noreply@example.test>",
)
class CompanyOwnerOnboardingStage2Tests(TestCase):
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
        self.invitation, _ = create_owner_invitation(
            actor=self.admin,
            email="owner@example.test",
            expires_hours=72,
        )
        self.invitation.status = CompanyOwnerInvitation.Status.SUBMITTED
        self.invitation.company_name = "Mwanzo Pork House"
        self.invitation.company_code = "mwanzo-pork"
        self.invitation.company_email = "office@example.test"
        self.invitation.company_phone = "+255 712 345 678"
        self.invitation.company_address = "Njombe, Tanzania"
        self.invitation.company_timezone = "Africa/Dar_es_Salaam"
        self.invitation.allowed_languages = ["sw", "en"]
        self.invitation.default_language = "sw"
        self.invitation.submitted_at = timezone.now()
        self.invitation.save()

    def _approve(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse(
                "users:owner_invitation_approve",
                kwargs={"invitation_id": self.invitation.public_id},
            ),
            {
                "continuation_hours": "72",
                "admin_password": "AdminPassword123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.invitation.refresh_from_db()

    def _token(self):
        self.assertTrue(mail.outbox)
        match = re.search(
            r"/accounts/company-onboarding/([0-9a-f-]+)/continue/([^/\s]+)/",
            mail.outbox[-1].body,
        )
        self.assertIsNotNone(match)
        return match.group(2)

    def test_approval_requires_admin_password(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse(
                "users:owner_invitation_approve",
                kwargs={"invitation_id": self.invitation.public_id},
            ),
            {
                "continuation_hours": "72",
                "admin_password": "WrongPassword123!",
            },
        )
        self.invitation.refresh_from_db()
        self.assertEqual(
            self.invitation.status,
            CompanyOwnerInvitation.Status.SUBMITTED,
        )

    def test_approval_creates_hashed_continuation_token(self):
        self._approve()
        raw = self._token()
        self.assertEqual(
            self.invitation.status,
            CompanyOwnerInvitation.Status.APPROVED,
        )
        self.assertEqual(self.invitation.reviewed_by, self.admin)
        self.assertIsNotNone(self.invitation.approved_at)
        self.assertEqual(len(self.invitation.continuation_token_hash), 64)
        self.assertNotEqual(self.invitation.continuation_token_hash, raw)
        self.assertIsNotNone(self.invitation.continuation_expires_at)

    def test_approval_does_not_create_company_or_owner(self):
        users_before = User.objects.count()
        companies_before = Company.objects.count()
        self._approve()
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(Company.objects.count(), companies_before)

    def test_valid_continuation_link_is_accessible(self):
        self._approve()
        raw = self._token()
        self.client.logout()
        response = self.client.get(
            reverse(
                "users:company_onboarding_continue",
                kwargs={
                    "invitation_id": self.invitation.public_id,
                    "token": raw,
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "registration has been approved")

    def test_wrong_continuation_token_is_rejected(self):
        self._approve()
        self.client.logout()
        response = self.client.get(
            reverse(
                "users:company_onboarding_continue",
                kwargs={
                    "invitation_id": self.invitation.public_id,
                    "token": "wrong-token",
                },
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_rejection_requires_reason_and_password(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse(
                "users:owner_invitation_reject",
                kwargs={"invitation_id": self.invitation.public_id},
            ),
            {
                "decision_reason": "Company details need verification.",
                "admin_password": "AdminPassword123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.invitation.refresh_from_db()
        self.assertEqual(
            self.invitation.status,
            CompanyOwnerInvitation.Status.REJECTED,
        )
        self.assertEqual(
            self.invitation.decision_reason,
            "Company details need verification.",
        )
        self.assertEqual(self.invitation.reviewed_by, self.admin)
        self.assertIsNotNone(self.invitation.rejected_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_wrong_password_cannot_reject(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse(
                "users:owner_invitation_reject",
                kwargs={"invitation_id": self.invitation.public_id},
            ),
            {
                "decision_reason": "Reject.",
                "admin_password": "WrongPassword123!",
            },
        )
        self.invitation.refresh_from_db()
        self.assertEqual(
            self.invitation.status,
            CompanyOwnerInvitation.Status.SUBMITTED,
        )

    def test_continuation_resend_rotates_token(self):
        self._approve()
        old_token = self._token()
        old_hash = self.invitation.continuation_token_hash

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse(
                "users:owner_invitation_continuation_resend",
                kwargs={"invitation_id": self.invitation.public_id},
            ),
            {"admin_password": "AdminPassword123!"},
        )
        self.assertEqual(response.status_code, 302)
        self.invitation.refresh_from_db()
        new_token = self._token()

        self.assertNotEqual(
            self.invitation.continuation_token_hash,
            old_hash,
        )
        self.assertNotEqual(new_token, old_token)
        self.assertEqual(
            self.invitation.continuation_resend_count,
            1,
        )

        self.client.logout()
        old_response = self.client.get(
            reverse(
                "users:company_onboarding_continue",
                kwargs={
                    "invitation_id": self.invitation.public_id,
                    "token": old_token,
                },
            )
        )
        self.assertEqual(old_response.status_code, 404)

        new_response = self.client.get(
            reverse(
                "users:company_onboarding_continue",
                kwargs={
                    "invitation_id": self.invitation.public_id,
                    "token": new_token,
                },
            )
        )
        self.assertEqual(new_response.status_code, 200)

    def test_platform_admin_business_boundary_remains_closed(self):
        self.assertFalse(self.admin.has_perm("pigs.view_pig"))
