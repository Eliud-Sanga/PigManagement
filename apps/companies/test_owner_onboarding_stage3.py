from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from apps.companies.models import Company, CompanyMembership
from apps.companies.onboarding_models import CompanyOwnerInvitation
from apps.companies.onboarding_services import (
    approve_registration_request,
    create_owner_invitation,
)
from apps.companies.services import create_company
from apps.users.models import User


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Home Management <noreply@example.test>",
)
class CompanyOwnerOnboardingStage3Tests(TestCase):
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

    def _approved_invitation(
        self,
        *,
        email="new-owner@example.test",
        code="mwanzo-pork",
        name="Mwanzo Pork House",
    ):
        invitation, _ = create_owner_invitation(
            actor=self.admin,
            email=email,
            expires_hours=72,
        )
        invitation.status = CompanyOwnerInvitation.Status.SUBMITTED
        invitation.company_name = name
        invitation.company_code = code
        invitation.company_email = "office@example.test"
        invitation.company_phone = "+255 712 345 678"
        invitation.company_address = "Njombe, Tanzania"
        invitation.company_timezone = "Africa/Dar_es_Salaam"
        invitation.allowed_languages = ["sw", "en"]
        invitation.default_language = "sw"
        invitation.submitted_at = timezone.now()
        invitation.save()

        invitation, raw_token = approve_registration_request(
            actor=self.admin,
            invitation=invitation,
            continuation_hours=72,
        )
        return invitation, raw_token

    def _complete_url(self, invitation, raw_token):
        return reverse(
            "users:company_onboarding_complete",
            kwargs={
                "invitation_id": invitation.public_id,
                "token": raw_token,
            },
        )

    def _new_owner_payload(self, *, username="mwanzo-owner"):
        return {
            "username": username,
            "first_name": "Mwanzo",
            "last_name": "Owner",
            "phone": "+255712345678",
            "new_password1": "SecureOwnerPass987!",
            "new_password2": "SecureOwnerPass987!",
        }

    def test_new_owner_completion_provisions_atomically(self):
        invitation, raw = self._approved_invitation()

        response = self.client.post(
            self._complete_url(invitation, raw),
            self._new_owner_payload(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your company is ready")

        invitation.refresh_from_db()
        owner = User.objects.get(email="new-owner@example.test")
        company = Company.objects.get(code="mwanzo-pork")
        membership = CompanyMembership.objects.get(
            company=company,
            user=owner,
        )

        self.assertEqual(
            invitation.status,
            CompanyOwnerInvitation.Status.COMPLETED,
        )
        self.assertEqual(invitation.owner_user, owner)
        self.assertEqual(invitation.provisioned_company, company)
        self.assertEqual(invitation.continuation_token_hash, "")
        self.assertIsNone(invitation.continuation_expires_at)
        self.assertEqual(len(invitation.login_now_token_hash), 64)
        self.assertIsNotNone(invitation.login_now_expires_at)
        self.assertIsNone(invitation.login_now_used_at)
        self.assertTrue(owner.is_active)
        self.assertFalse(owner.must_change_password)
        self.assertTrue(owner.check_password("SecureOwnerPass987!"))
        self.assertEqual(membership.role.code, "OWNER")
        self.assertEqual(
            membership.status,
            CompanyMembership.Status.ACTIVE,
        )
        self.assertFalse(company.owner_user_management_enabled)
        self.assertEqual(company.timezone, "Africa/Dar_es_Salaam")

    def test_weak_password_does_not_provision(self):
        invitation, raw = self._approved_invitation()

        response = self.client.post(
            self._complete_url(invitation, raw),
            {
                "username": "weak-owner",
                "first_name": "Weak",
                "last_name": "Owner",
                "phone": "+255712345678",
                "new_password1": "12345678",
                "new_password2": "12345678",
            },
        )
        self.assertEqual(response.status_code, 200)

        invitation.refresh_from_db()
        self.assertEqual(
            invitation.status,
            CompanyOwnerInvitation.Status.APPROVED,
        )
        self.assertFalse(
            User.objects.filter(email=invitation.email).exists()
        )
        self.assertFalse(
            Company.objects.filter(code=invitation.company_code).exists()
        )

    def test_brand_new_email_is_locked_to_invitation(self):
        invitation, raw = self._approved_invitation()
        response = self.client.get(
            self._complete_url(invitation, raw)
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertNotIn("email", form.fields)
        self.assertNotIn("owner_email", form.fields)
        self.assertContains(response, invitation.email)

    def test_completion_token_cannot_be_reused(self):
        invitation, raw = self._approved_invitation()
        self.client.post(
            self._complete_url(invitation, raw),
            self._new_owner_payload(username="one-use-owner"),
        )

        response = self.client.get(
            self._complete_url(invitation, raw)
        )
        self.assertEqual(response.status_code, 404)

    def test_existing_identity_requires_existing_password(self):
        existing = User.objects.create_user(
            username="existing-owner",
            email="existing@example.test",
            password="ExistingOwner123!",
            first_name="Existing",
            last_name="Identity",
            phone="+255700000001",
            is_active=True,
        )
        invitation, raw = self._approved_invitation(
            email=existing.email,
            code="existing-new-company",
            name="Existing Owner Company",
        )

        response = self.client.post(
            self._complete_url(invitation, raw),
            {"current_password": "WrongPassword123!"},
        )
        self.assertEqual(response.status_code, 200)
        invitation.refresh_from_db()
        self.assertEqual(
            invitation.status,
            CompanyOwnerInvitation.Status.APPROVED,
        )
        self.assertFalse(
            Company.objects.filter(code="existing-new-company").exists()
        )

    def test_existing_identity_is_not_rewritten(self):
        existing = User.objects.create_user(
            username="existing-owner",
            email="existing@example.test",
            password="ExistingOwner123!",
            first_name="Existing",
            last_name="Identity",
            phone="+255700000001",
            is_active=True,
        )
        old_password = existing.password

        invitation, raw = self._approved_invitation(
            email=existing.email,
            code="second-owner-company",
            name="Second Owner Company",
        )

        response = self.client.post(
            self._complete_url(invitation, raw),
            {"current_password": "ExistingOwner123!"},
        )
        self.assertEqual(response.status_code, 200)

        existing.refresh_from_db()
        self.assertEqual(existing.username, "existing-owner")
        self.assertEqual(existing.first_name, "Existing")
        self.assertEqual(existing.last_name, "Identity")
        self.assertEqual(existing.phone, "+255700000001")
        self.assertEqual(existing.password, old_password)
        self.assertTrue(existing.check_password("ExistingOwner123!"))

        company = Company.objects.get(code="second-owner-company")
        self.assertTrue(
            CompanyMembership.objects.filter(
                company=company,
                user=existing,
                role__code="OWNER",
                status=CompanyMembership.Status.ACTIVE,
            ).exists()
        )

    def test_inactive_existing_identity_is_not_silently_reactivated(self):
        existing = User.objects.create_user(
            username="inactive-owner",
            email="inactive@example.test",
            password="ExistingOwner123!",
            is_active=False,
        )

        invitation, raw = self._approved_invitation(
            email=existing.email,
            code="inactive-owner-company",
            name="Inactive Owner Company",
        )

        response = self.client.get(
            self._complete_url(invitation, raw)
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Company.objects.filter(
                code="inactive-owner-company"
            ).exists()
        )

    def test_platform_admin_identity_cannot_become_owner(self):
        invitation, raw = self._approved_invitation(
            email=self.admin.email,
            code="admin-owner-blocked",
            name="Blocked Admin Owner Company",
        )

        response = self.client.get(
            self._complete_url(invitation, raw)
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Company.objects.filter(code="admin-owner-blocked").exists()
        )

    def test_login_now_uses_one_time_handoff(self):
        invitation, raw = self._approved_invitation(
            code="login-now-company",
            name="Login Now Company",
        )

        response = self.client.post(
            self._complete_url(invitation, raw),
            self._new_owner_payload(username="login-now-owner"),
        )
        self.assertEqual(response.status_code, 200)
        login_token = response.context["login_token"]
        self.assertTrue(login_token)

        invitation.refresh_from_db()
        self.assertNotEqual(
            invitation.login_now_token_hash,
            login_token,
        )

        login_response = self.client.post(
            reverse(
                "users:company_onboarding_login_now",
                kwargs={"invitation_id": invitation.public_id},
            ),
            {"login_token": login_token},
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(
            login_response.url,
            reverse("pigs:dashboard"),
        )

        owner = User.objects.get(username="login-now-owner")
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            owner.pk,
        )

        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.login_now_used_at)
        self.assertEqual(invitation.login_now_token_hash, "")

        self.client.logout()
        replay = self.client.post(
            reverse(
                "users:company_onboarding_login_now",
                kwargs={"invitation_id": invitation.public_id},
            ),
            {"login_token": login_token},
        )
        self.assertEqual(replay.status_code, 404)

    def test_login_now_url_is_not_shadowed_by_stage1_token_route(self):
        invitation, _ = self._approved_invitation(
            code="route-resolution-company",
            name="Route Resolution Company",
        )
        path = reverse(
            "users:company_onboarding_login_now",
            kwargs={"invitation_id": invitation.public_id},
        )
        self.assertIn("/action/login-now/", path)
        match = resolve(path)
        self.assertEqual(
            match.url_name,
            "company_onboarding_login_now",
        )
        self.assertEqual(
            match.func.__name__,
            "company_onboarding_login_now",
        )

    def test_wrong_login_now_token_is_rejected(self):
        invitation, raw = self._approved_invitation(
            code="wrong-login-token-company",
            name="Wrong Login Token Company",
        )
        response = self.client.post(
            self._complete_url(invitation, raw),
            self._new_owner_payload(username="wrong-token-owner"),
        )
        self.assertEqual(response.status_code, 200)

        denied = self.client.post(
            reverse(
                "users:company_onboarding_login_now",
                kwargs={"invitation_id": invitation.public_id},
            ),
            {"login_token": "wrong-login-now-token"},
        )
        self.assertEqual(denied.status_code, 404)

    def test_manual_company_flow_still_works(self):
        owner = User.objects.create_user(
            username="manual-owner",
            email="manual-owner@example.test",
            password="ManualOwner123!",
            is_active=True,
        )
        company = create_company(
            actor=self.admin,
            name="Manual Company",
            code="manual-company-stage3",
            owner=owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )
        self.assertEqual(company.code, "manual-company-stage3")

    def test_platform_admin_business_boundary_remains_closed(self):
        self.assertFalse(self.admin.has_perm("pigs.view_pig"))
