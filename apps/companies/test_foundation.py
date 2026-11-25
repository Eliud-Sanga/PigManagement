from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.companies.constants import ROLE_MANAGER, ROLE_OWNER
from apps.companies.context import resolve_company_context
from apps.companies.models import CompanyMembership
from apps.companies.services import (
    approve_company_membership,
    create_company,
    request_company_user,
    set_company_language_policy,
)
from apps.users.models import User


class CompanyFoundationTests(TestCase):
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

        self.company = create_company(
            actor=self.admin,
            name="Demo Pig Company",
            code="demo-pig",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

    def test_owner_membership_is_active(self):
        membership = CompanyMembership.objects.select_related(
            "role"
        ).get(
            company=self.company,
            user=self.owner,
        )

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.ACTIVE,
        )
        self.assertEqual(
            membership.role.code,
            ROLE_OWNER,
        )
        self.assertEqual(
            membership.approved_language,
            "sw",
        )

    def test_owner_request_requires_admin_approval(self):
        manager_role = self.company.roles.get(
            code=ROLE_MANAGER
        )

        # This test exercises the approval workflow after the
        # Platform Admin has enabled Owner-level user management.
        self.company.owner_user_management_enabled = True
        self.company.save(
            update_fields=[
                "owner_user_management_enabled",
            ]
        )

        membership = request_company_user(
            actor=self.owner,
            company=self.company,
            role=manager_role,
            username="manager",
            email="manager@example.test",
            requested_language="en",
        )

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.PENDING_APPROVAL,
        )
        self.assertEqual(
            membership.requested_language,
            "en",
        )
        self.assertEqual(
            membership.approved_language,
            "",
        )
        self.assertFalse(
            membership.user.is_active
        )

        membership = approve_company_membership(
            actor=self.admin,
            membership=membership,
            approved_language="en",
            step_up_verified=True,
        )

        membership.user.refresh_from_db()

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.ACTIVE,
        )
        self.assertEqual(
            membership.approved_language,
            "en",
        )
        self.assertFalse(
            membership.user.is_active
        )
        self.assertFalse(
            membership.user.has_usable_password()
        )
        self.assertEqual(
            membership.user.preferred_language,
            "en",
        )

    def test_owner_cannot_select_disabled_language(self):
        self.company.allowed_languages = ["sw"]
        self.company.default_language = "sw"
        self.company.full_clean()
        self.company.save()

        # Reach the language-policy validation intentionally:
        # Owner user management must first be enabled.
        self.company.owner_user_management_enabled = True
        self.company.save(
            update_fields=[
                "owner_user_management_enabled",
            ]
        )

        manager_role = self.company.roles.get(
            code=ROLE_MANAGER
        )

        with self.assertRaises(ValidationError):
            request_company_user(
                actor=self.owner,
                company=self.company,
                role=manager_role,
                username="manager2",
                email="manager2@example.test",
                requested_language="en",
            )

    def test_platform_admin_must_step_up_for_language_policy(self):
        with self.assertRaises(PermissionDenied):
            set_company_language_policy(
                actor=self.admin,
                company=self.company,
                allowed_languages=["sw", "en"],
                default_language="sw",
                step_up_verified=False,
            )

    def test_cross_company_context_denied(self):
        other_owner = User.objects.create_user(
            username="other-owner",
            email="other-owner@example.test",
            password="OtherPassword123!",
            is_active=True,
        )

        other_company = create_company(
            actor=self.admin,
            name="Other Company",
            code="other-company",
            owner=other_owner,
            allowed_languages=["sw"],
            default_language="sw",
            step_up_verified=True,
        )

        with self.assertRaises(PermissionDenied):
            resolve_company_context(
                user=self.owner,
                company_public_id=other_company.public_id,
            )

    def test_platform_admin_can_enter_company_context(self):
        context = resolve_company_context(
            user=self.admin,
            company_public_id=self.company.public_id,
        )

        self.assertEqual(
            context.company,
            self.company,
        )
        self.assertIsNone(
            context.membership
        )
