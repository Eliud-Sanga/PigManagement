from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.companies.constants import ROLE_OWNER
from apps.companies.models import CompanyMembership
from apps.companies.services import create_company
from apps.users.models import User


class ProvisionCompanyOwnerCommandTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="eliud12",
            email="eliud12@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )
        first_owner = User.objects.create_user(
            username="first-owner",
            email="first-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )
        self.company = create_company(
            actor=self.admin,
            name="Owner Provision Farm",
            code="owner-provision-farm",
            owner=first_owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

    def test_command_creates_distinct_regular_owner(self):
        stdout = StringIO()
        with patch("sys.stdin", StringIO("StrongOwnerPassword123!\n")):
            call_command(
                "provision_company_owner",
                company=self.company.code,
                actor=self.admin.username,
                username="business-owner",
                email="business-owner@example.test",
                first_name="Business",
                last_name="Owner",
                password_stdin=True,
                stdout=stdout,
            )

        user = User.objects.get(username="business-owner")
        membership = CompanyMembership.objects.select_related("role").get(
            company=self.company,
            user=user,
        )
        self.assertFalse(user.is_platform_admin)
        self.assertFalse(user.is_staff)
        self.assertEqual(membership.role.code, ROLE_OWNER)
        self.assertEqual(membership.status, CompanyMembership.Status.ACTIVE)
        self.assertIn("Company Owner provisioned safely", stdout.getvalue())

    def test_command_refuses_platform_admin_reuse(self):
        with self.assertRaisesMessage(
            CommandError,
            "cannot also be provisioned as a Company Owner",
        ):
            call_command(
                "provision_company_owner",
                company=self.company.code,
                actor=self.admin.username,
                username=self.admin.username,
                email=self.admin.email,
            )
