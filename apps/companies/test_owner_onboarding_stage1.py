import re
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Company
from apps.companies.onboarding_models import CompanyOwnerInvitation
from apps.companies.onboarding_services import (
    create_owner_invitation,
    revoke_invitation,
)
from apps.users.models import User


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Home Management <noreply@example.test>",
)
class CompanyOwnerOnboardingStage1Tests(TestCase):
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

    def _create_via_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("users:owner_invitation_create"),
            {
                "email": "owner@example.test",
                "expires_hours": "72",
                "admin_password": "AdminPassword123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        invitation = CompanyOwnerInvitation.objects.get(
            email="owner@example.test"
        )
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(
            r"/accounts/company-onboarding/([0-9a-f-]+)/([^/\s]+)/",
            mail.outbox[-1].body,
        )
        self.assertIsNotNone(match)
        return invitation, match.group(2)

    def test_admin_invitation_requires_current_password(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("users:owner_invitation_create"),
            {
                "email": "owner@example.test",
                "expires_hours": "72",
                "admin_password": "WrongPassword123!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CompanyOwnerInvitation.objects.exists())

    def test_invitation_email_uses_hashed_token_at_rest(self):
        invitation, raw_token = self._create_via_admin()
        self.assertNotEqual(invitation.token_hash, raw_token)
        self.assertEqual(len(invitation.token_hash), 64)
        self.assertNotIn(raw_token, invitation.token_hash)

    def test_valid_link_marks_invitation_opened(self):
        invitation, raw_token = self._create_via_admin()
        self.client.logout()
        response = self.client.get(
            reverse(
                "users:company_onboarding_register",
                kwargs={
                    "invitation_id": invitation.public_id,
                    "token": raw_token,
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        invitation.refresh_from_db()
        self.assertEqual(
            invitation.status,
            CompanyOwnerInvitation.Status.OPENED,
        )
        self.assertIsNotNone(invitation.opened_at)

    def test_wrong_token_is_rejected(self):
        invitation, _ = self._create_via_admin()
        self.client.logout()
        response = self.client.get(
            reverse(
                "users:company_onboarding_register",
                kwargs={
                    "invitation_id": invitation.public_id,
                    "token": "not-the-real-token",
                },
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_expired_invitation_is_rejected(self):
        invitation, raw_token = self._create_via_admin()
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=["expires_at", "updated_at"])
        self.client.logout()
        response = self.client.get(
            reverse(
                "users:company_onboarding_register",
                kwargs={
                    "invitation_id": invitation.public_id,
                    "token": raw_token,
                },
            )
        )
        self.assertEqual(response.status_code, 410)
        invitation.refresh_from_db()
        self.assertEqual(
            invitation.status,
            CompanyOwnerInvitation.Status.EXPIRED,
        )

    def test_registration_submission_creates_no_company_or_user(self):
        invitation, raw_token = self._create_via_admin()
        self.client.logout()
        users_before = User.objects.count()
        companies_before = Company.objects.count()
        response = self.client.post(
            reverse(
                "users:company_onboarding_register",
                kwargs={
                    "invitation_id": invitation.public_id,
                    "token": raw_token,
                },
            ),
            {
                "company_name": "Mwanzo Pork House",
                "company_code": "mwanzo-pork",
                "company_email": "office@example.test",
                "company_phone": "+255 712 345 678",
                "company_address": "Njombe, Tanzania",
                "company_timezone": "Africa/Dar_es_Salaam",
                "allowed_languages": ["sw", "en"],
                "default_language": "sw",
            },
        )
        self.assertEqual(response.status_code, 200)
        invitation.refresh_from_db()
        self.assertEqual(
            invitation.status,
            CompanyOwnerInvitation.Status.SUBMITTED,
        )
        self.assertEqual(invitation.company_name, "Mwanzo Pork House")
        self.assertEqual(invitation.company_code, "mwanzo-pork")
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(Company.objects.count(), companies_before)

    def test_invited_email_is_not_a_public_form_field(self):
        invitation, raw_token = self._create_via_admin()
        self.client.logout()
        response = self.client.get(
            reverse(
                "users:company_onboarding_register",
                kwargs={
                    "invitation_id": invitation.public_id,
                    "token": raw_token,
                },
            )
        )
        form = response.context["form"]
        self.assertNotIn("email", form.fields)
        self.assertNotIn("owner_email", form.fields)

    def test_revoked_invitation_cannot_be_used(self):
        invitation, raw_token = create_owner_invitation(
            actor=self.admin,
            email="owner@example.test",
            expires_hours=72,
        )
        revoke_invitation(
            actor=self.admin,
            invitation=invitation,
        )
        response = self.client.get(
            reverse(
                "users:company_onboarding_register",
                kwargs={
                    "invitation_id": invitation.public_id,
                    "token": raw_token,
                },
            )
        )
        self.assertEqual(response.status_code, 410)

    def test_platform_admin_business_boundary_remains_closed(self):
        self.assertFalse(self.admin.has_perm("pigs.view_pig"))
