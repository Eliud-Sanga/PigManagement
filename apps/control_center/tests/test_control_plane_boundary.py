from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.companies.models import CompanyMembership
from apps.companies.services import create_company


User = get_user_model()


class ControlPlaneBoundaryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="boundary-admin",
            email="boundary-admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.owner = User.objects.create_user(
            username="boundary-owner",
            email="boundary-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        self.company = create_company(
            actor=self.admin,
            name="Boundary Company",
            code="boundary-company",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

    def test_platform_admin_business_permission_is_always_false(self):
        self.assertFalse(
            self.admin.has_perm("pigs.view_pig")
        )

    def test_platform_admin_business_dashboard_redirects_to_control_center(self):
        self.client.force_login(
            self.admin,
            backend=(
                "apps.companies.backends."
                "CompanyRoleBackend"
            ),
        )

        response = self.client.get(
            reverse("pigs:dashboard")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("users:control_center"),
        )

    def test_owner_user_management_disabled_blocks_owner(self):
        self.assertFalse(
            self.company.owner_user_management_enabled
        )

        self.client.force_login(
            self.owner,
            backend=(
                "apps.companies.backends."
                "CompanyRoleBackend"
            ),
        )

        response = self.client.get(
            reverse("users:company_users")
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_can_enable_owner_user_management(self):
        self.company.owner_user_management_enabled = True
        self.company.save(
            update_fields=[
                "owner_user_management_enabled",
                "updated_at",
            ]
        )

        self.client.force_login(
            self.owner,
            backend=(
                "apps.companies.backends."
                "CompanyRoleBackend"
            ),
        )

        response = self.client.get(
            reverse("users:company_users")
        )

        self.assertEqual(response.status_code, 200)

    def test_owner_can_create_only_company_pending_user_when_enabled(self):
        self.company.owner_user_management_enabled = True
        self.company.save(
            update_fields=[
                "owner_user_management_enabled",
                "updated_at",
            ]
        )

        role = self.company.roles.get(code="STAFF")

        self.client.force_login(
            self.owner,
            backend=(
                "apps.companies.backends."
                "CompanyRoleBackend"
            ),
        )

        response = self.client.post(
            reverse("users:company_user_request"),
            {
                "username": "company-staff",
                "email": "company-staff@example.test",
                "first_name": "Company",
                "last_name": "Staff",
                "phone": "0712345678",
                "role": role.pk,
                "requested_language": "sw",
                "current_password": "OwnerPassword123!",
            },
        )

        self.assertEqual(response.status_code, 302)

        membership = (
            CompanyMembership.objects
            .get(
                company=self.company,
                user__email=(
                    "company-staff@example.test"
                ),
            )
        )

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.PENDING_APPROVAL,
        )
        self.assertFalse(membership.user.is_active)
        self.assertEqual(membership.user.first_name, "Company")
        self.assertEqual(membership.user.last_name, "Staff")
        self.assertEqual(membership.user.phone, "0712345678")

    def test_owner_remove_archives_only_membership_not_global_identity(self):
        self.company.owner_user_management_enabled = True
        self.company.save(
            update_fields=[
                "owner_user_management_enabled",
                "updated_at",
            ]
        )

        second_owner = User.objects.create_user(
            username="second-company-owner",
            email="second-company-owner@example.test",
            password="SecondOwnerPassword123!",
            is_active=True,
        )

        second_company = create_company(
            actor=self.admin,
            name="Second Company",
            code="second-company",
            owner=second_owner,
            allowed_languages=["sw"],
            default_language="sw",
            step_up_verified=True,
        )

        shared = User.objects.create_user(
            username="shared-user",
            email="shared-user@example.test",
            password="SharedPassword123!",
            is_active=True,
        )

        membership_a = CompanyMembership.objects.create(
            company=self.company,
            user=shared,
            role=self.company.roles.get(code="STAFF"),
            status=CompanyMembership.Status.ACTIVE,
            requested_language="sw",
            approved_language="sw",
            created_by=self.admin,
            approved_by=self.admin,
        )

        membership_b = CompanyMembership.objects.create(
            company=second_company,
            user=shared,
            role=second_company.roles.get(code="STAFF"),
            status=CompanyMembership.Status.ACTIVE,
            requested_language="sw",
            approved_language="sw",
            created_by=self.admin,
            approved_by=self.admin,
        )

        self.client.force_login(
            self.owner,
            backend=(
                "apps.companies.backends."
                "CompanyRoleBackend"
            ),
        )

        response = self.client.post(
            reverse(
                "users:company_user_remove",
                args=[membership_a.public_id],
            ),
            {
                "current_password":
                    "OwnerPassword123!",
            },
        )

        self.assertEqual(response.status_code, 302)

        membership_a.refresh_from_db()
        membership_b.refresh_from_db()
        shared.refresh_from_db()

        self.assertEqual(
            membership_a.status,
            CompanyMembership.Status.ARCHIVED,
        )
        self.assertEqual(
            membership_b.status,
            CompanyMembership.Status.ACTIVE,
        )
        self.assertTrue(shared.is_active)
