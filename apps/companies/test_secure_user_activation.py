import re
from datetime import timedelta
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import (
    CompanyMembership,
    CompanyUserActivation,
)
from apps.companies.services import (
    create_company,
    request_company_user,
)


User = get_user_model()


@override_settings(
    EMAIL_BACKEND=(
        "django.core.mail.backends.locmem.EmailBackend"
    ),
    DEFAULT_FROM_EMAIL="security@example.test",
)
class SecureCompanyUserActivationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="activation-admin",
            email="activation-admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.owner = User.objects.create_user(
            username="activation-owner",
            email="activation-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        self.company = create_company(
            actor=self.admin,
            name="Activation Company",
            code="activation-company",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

        self.company.owner_user_management_enabled = True
        self.company.save(
            update_fields=[
                "owner_user_management_enabled",
                "updated_at",
            ]
        )

        self.role = self.company.roles.get(code="STAFF")
        self.client.force_login(self.admin)

    def _pending(
        self,
        *,
        username="activation-staff",
        email="activation-staff@example.test",
    ):
        return request_company_user(
            actor=self.owner,
            company=self.company,
            role=self.role,
            username=username,
            email=email,
            requested_language="sw",
            first_name="Activation",
            last_name="Staff",
        )

    def _approve(self, membership):
        return self.client.post(
            reverse(
                "users:approval_review",
                args=[membership.public_id],
            ),
            {
                "decision": "approve",
                "approved_language": "sw",
                "admin_password": "AdminPassword123!",
            },
        )

    def _activation_path(self):
        self.assertTrue(mail.outbox)
        body = mail.outbox[-1].body

        match = re.search(
            r"https?://[^\s]+/accounts/activate/"
            r"[0-9a-fA-F-]+/[A-Za-z0-9_-]+/",
            body,
        )

        self.assertIsNotNone(match)
        return urlparse(match.group(0)).path

    def test_new_identity_approval_requires_self_activation(self):
        membership = self._pending()
        response = self._approve(membership)

        self.assertEqual(response.status_code, 302)

        membership.refresh_from_db()
        membership.user.refresh_from_db()

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.ACTIVE,
        )
        self.assertFalse(membership.user.is_active)
        self.assertFalse(
            membership.user.has_usable_password()
        )

        activation = CompanyUserActivation.objects.get(
            membership=membership
        )

        self.assertEqual(len(activation.token_hash), 64)
        self.assertIsNotNone(activation.sent_at)
        self.assertIsNone(activation.used_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_user_sets_own_password_and_activates_identity(self):
        membership = self._pending()
        self._approve(membership)
        path = self._activation_path()

        self.client.logout()

        handoff = self.client.get(path)

        self.assertRedirects(
            handoff,
            reverse(
                "users:company_user_activation_complete"
            ),
        )

        complete = self.client.post(
            reverse(
                "users:company_user_activation_complete"
            ),
            {
                "new_password1":
                    "MyOwnActivationPassword123!",
                "new_password2":
                    "MyOwnActivationPassword123!",
            },
        )

        self.assertRedirects(
            complete,
            reverse("users:login"),
        )

        membership.user.refresh_from_db()

        activation = CompanyUserActivation.objects.get(
            membership=membership
        )

        self.assertTrue(membership.user.is_active)
        self.assertTrue(
            membership.user.check_password(
                "MyOwnActivationPassword123!"
            )
        )
        self.assertFalse(
            membership.user.must_change_password
        )
        self.assertIsNotNone(activation.used_at)

    def test_activation_link_cannot_be_reused(self):
        membership = self._pending()
        self._approve(membership)
        path = self._activation_path()

        self.client.logout()
        self.client.get(path)

        self.client.post(
            reverse(
                "users:company_user_activation_complete"
            ),
            {
                "new_password1":
                    "OneTimeActivationPassword123!",
                "new_password2":
                    "OneTimeActivationPassword123!",
            },
        )

        second = self.client.get(path)
        self.assertEqual(second.status_code, 400)

    def test_wrong_token_is_rejected(self):
        membership = self._pending()
        self._approve(membership)

        activation = CompanyUserActivation.objects.get(
            membership=membership
        )

        response = self.client.get(
            reverse(
                "users:company_user_activation_handoff",
                args=[
                    activation.public_id,
                    "wrong-token",
                ],
            )
        )

        self.assertEqual(response.status_code, 400)

    def test_expired_token_is_rejected(self):
        membership = self._pending()
        self._approve(membership)
        path = self._activation_path()

        activation = CompanyUserActivation.objects.get(
            membership=membership
        )

        activation.expires_at = (
            timezone.now()
            - timedelta(minutes=1)
        )
        activation.save(
            update_fields=[
                "expires_at",
                "updated_at",
            ]
        )

        response = self.client.get(path)
        self.assertEqual(response.status_code, 400)

    def test_existing_identity_password_is_preserved(self):
        existing = User.objects.create_user(
            username="existing-activation-user",
            email="existing-activation-user@example.test",
            password="ExistingIdentityPassword123!",
            is_active=True,
        )

        membership = request_company_user(
            actor=self.owner,
            company=self.company,
            role=self.role,
            username=existing.username,
            email=existing.email,
            requested_language="sw",
        )

        response = self._approve(membership)
        self.assertEqual(response.status_code, 302)

        membership.refresh_from_db()
        existing.refresh_from_db()

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.ACTIVE,
        )
        self.assertTrue(
            existing.check_password(
                "ExistingIdentityPassword123!"
            )
        )
        self.assertFalse(
            CompanyUserActivation.objects
            .filter(membership=membership)
            .exists()
        )

    def test_crafted_admin_password_assignment_is_rejected(self):
        membership = self._pending(
            username="crafted-temp-user",
            email="crafted-temp-user@example.test",
        )

        response = self.client.post(
            reverse(
                "users:approval_review",
                args=[membership.public_id],
            ),
            {
                "decision": "approve",
                "approved_language": "sw",
                "temporary_password1":
                    "AdminChosenPassword123!",
                "temporary_password2":
                    "AdminChosenPassword123!",
                "admin_password":
                    "AdminPassword123!",
            },
        )

        self.assertEqual(response.status_code, 200)

        membership.refresh_from_db()
        membership.user.refresh_from_db()

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.PENDING_APPROVAL,
        )
        self.assertFalse(
            membership.user.has_usable_password()
        )

    def test_resend_rotates_token_and_requires_admin_password(self):
        membership = self._pending(
            username="resend-user",
            email="resend-user@example.test",
        )

        self._approve(membership)

        activation = CompanyUserActivation.objects.get(
            membership=membership
        )

        old_hash = activation.token_hash
        initial_mail_count = len(mail.outbox)

        denied = self.client.post(
            reverse(
                "users:company_member_activation_resend",
                args=[membership.public_id],
            ),
            {
                "admin_password":
                    "WrongPassword123!",
            },
        )

        self.assertEqual(denied.status_code, 302)

        activation.refresh_from_db()
        self.assertEqual(
            activation.token_hash,
            old_hash,
        )

        sent = self.client.post(
            reverse(
                "users:company_member_activation_resend",
                args=[membership.public_id],
            ),
            {
                "admin_password":
                    "AdminPassword123!",
            },
        )

        self.assertEqual(sent.status_code, 302)

        activation.refresh_from_db()

        self.assertNotEqual(
            activation.token_hash,
            old_hash,
        )
        self.assertGreaterEqual(
            activation.resend_count,
            1,
        )
        self.assertEqual(
            len(mail.outbox),
            initial_mail_count + 1,
        )
