from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.companies.backends import CompanyRoleBackend
from apps.companies.constants import (
    ROLE_MANAGER,
    ROLE_VIEWER,
)
from apps.companies.models import (
    CompanyMembership,
    CompanyScopeError,
)
from apps.companies.scope import company_scope
from apps.companies.services import (
    create_company,
)
from apps.pigs.models import (
    DailySale,
    FoodItem,
    Pig,
    Purchase,
    SlaughterBatch,
)
from apps.users.models import User


class BusinessTenancyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="platform-admin-tenancy",
            email="platform-tenancy@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.owner_a = User.objects.create_user(
            username="owner-a",
            email="owner-a@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        self.owner_b = User.objects.create_user(
            username="owner-b",
            email="owner-b@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        self.company_a = create_company(
            actor=self.admin,
            name="Company A",
            code="company-a",
            owner=self.owner_a,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

        self.company_b = create_company(
            actor=self.admin,
            name="Company B",
            code="company-b",
            owner=self.owner_b,
            allowed_languages=["sw", "en"],
            default_language="en",
            step_up_verified=True,
        )

        self.membership_a = (
            self.owner_a.company_memberships
            .select_related("role", "company")
            .prefetch_related(
                "role__permissions__content_type",
                "granted_permissions__content_type",
                "denied_permissions__content_type",
            )
            .get(company=self.company_a)
        )

        self.membership_b = (
            self.owner_b.company_memberships
            .select_related("role", "company")
            .prefetch_related(
                "role__permissions__content_type",
                "granted_permissions__content_type",
                "denied_permissions__content_type",
            )
            .get(company=self.company_b)
        )

    def _purchase(self, company, *, supplier):
        with company_scope(company):
            return Purchase.objects.create(
                supplier_name=supplier,
                purchase_date=date(2026, 9, 1),
                number_of_pigs=1,
                total_cost=Decimal("100000.00"),
            )

    def test_platform_admin_can_authenticate_with_zero_companies(self):
        isolated_admin = User.objects.create_user(
            username="empty-platform-admin",
            email="empty-platform@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_platform_admin=True,
        )

        backend = CompanyRoleBackend()

        self.assertTrue(
            backend.user_can_authenticate(
                isolated_admin
            )
        )

    def test_normal_user_without_company_cannot_authenticate(self):
        orphan = User.objects.create_user(
            username="orphan",
            email="orphan@example.test",
            password="OrphanPassword123!",
            is_active=True,
        )

        backend = CompanyRoleBackend()

        self.assertFalse(
            backend.user_can_authenticate(
                orphan
            )
        )

    def test_one_owner_can_own_multiple_companies(self):
        company_c = create_company(
            actor=self.admin,
            name="Company C",
            code="company-c",
            owner=self.owner_a,
            allowed_languages=["sw", "en"],
            default_language="en",
            step_up_verified=True,
        )

        memberships = (
            CompanyMembership.objects
            .active()
            .filter(
                user=self.owner_a,
            )
        )

        self.assertEqual(
            memberships.count(),
            2,
        )

        self.assertTrue(
            memberships.filter(
                company=self.company_a,
            ).exists()
        )

        self.assertTrue(
            memberships.filter(
                company=company_c,
            ).exists()
        )

    def test_no_company_context_is_fail_closed(self):
        self._purchase(
            self.company_a,
            supplier="Supplier A",
        )

        self.assertEqual(
            Purchase.objects.count(),
            0,
        )

        self.assertEqual(
            Purchase.all_objects.count(),
            1,
        )

        with self.assertRaises(CompanyScopeError):
            Purchase.objects.create(
                supplier_name="No Scope",
                purchase_date=date(2026, 9, 2),
                number_of_pigs=1,
                total_cost=Decimal("100000.00"),
            )

    def test_company_queries_are_isolated(self):
        purchase_a = self._purchase(
            self.company_a,
            supplier="Supplier A",
        )
        purchase_b = self._purchase(
            self.company_b,
            supplier="Supplier B",
        )

        with company_scope(self.company_a):
            self.assertEqual(
                list(
                    Purchase.objects.values_list(
                        "pk",
                        flat=True,
                    )
                ),
                [purchase_a.pk],
            )

        with company_scope(self.company_b):
            self.assertEqual(
                list(
                    Purchase.objects.values_list(
                        "pk",
                        flat=True,
                    )
                ),
                [purchase_b.pk],
            )

    def test_same_tag_number_is_allowed_in_different_companies(self):
        purchase_a = self._purchase(
            self.company_a,
            supplier="Supplier A",
        )
        purchase_b = self._purchase(
            self.company_b,
            supplier="Supplier B",
        )

        with company_scope(self.company_a):
            Pig.objects.create(
                purchase=purchase_a,
                tag_number="P001",
            )

        with company_scope(self.company_b):
            Pig.objects.create(
                purchase=purchase_b,
                tag_number="P001",
            )

        self.assertEqual(
            Pig.all_objects.filter(
                tag_number="P001"
            ).count(),
            2,
        )

    def test_duplicate_tag_in_same_company_is_rejected(self):
        purchase = self._purchase(
            self.company_a,
            supplier="Supplier A",
        )

        with company_scope(self.company_a):
            Pig.objects.create(
                purchase=purchase,
                tag_number="P001",
            )

            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Pig.objects.create(
                        purchase=purchase,
                        tag_number="P001",
                    )

    def test_cross_company_foreign_key_is_rejected(self):
        purchase_b = self._purchase(
            self.company_b,
            supplier="Supplier B",
        )

        with company_scope(self.company_a):
            with self.assertRaises(
                (
                    CompanyScopeError,
                    ValidationError,
                )
            ):
                Pig.objects.create(
                    purchase=purchase_b,
                    tag_number="P900",
                )

    def test_cross_company_many_to_many_is_rejected(self):
        purchase_b = self._purchase(
            self.company_b,
            supplier="Supplier B",
        )

        with company_scope(self.company_b):
            pig_b = Pig.objects.create(
                purchase=purchase_b,
                tag_number="P101",
            )

        with company_scope(self.company_a):
            batch_a = SlaughterBatch.objects.create(
                slaughter_date=date(2026, 9, 3),
                batch_number="B001",
            )

            with self.assertRaises(ValidationError):
                batch_a.pigs.add(pig_b)

    def test_batch_number_is_unique_per_company(self):
        with company_scope(self.company_a):
            SlaughterBatch.objects.create(
                slaughter_date=date(2026, 9, 4),
                batch_number="B010",
            )

        with company_scope(self.company_b):
            SlaughterBatch.objects.create(
                slaughter_date=date(2026, 9, 4),
                batch_number="B010",
            )

        self.assertEqual(
            SlaughterBatch.all_objects.filter(
                batch_number="B010"
            ).count(),
            2,
        )

    def test_suspended_company_blocks_new_business_writes(self):
        self.company_a.status = self.company_a.Status.SUSPENDED
        self.company_a.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        with company_scope(self.company_a):
            with self.assertRaises(CompanyScopeError):
                Purchase.objects.create(
                    supplier_name="Blocked Supplier",
                    purchase_date=date(2026, 9, 4),
                    number_of_pigs=1,
                    total_cost=Decimal("100000.00"),
                )

    def test_daily_sale_date_is_unique_per_company(self):
        with company_scope(self.company_a):
            DailySale.objects.create(
                sale_date=date(2026, 9, 5),
                total_money_received=Decimal("10000.00"),
            )

        with company_scope(self.company_b):
            DailySale.objects.create(
                sale_date=date(2026, 9, 5),
                total_money_received=Decimal("20000.00"),
            )

        self.assertEqual(
            DailySale.all_objects.filter(
                sale_date=date(2026, 9, 5)
            ).count(),
            2,
        )

    def test_food_name_is_unique_per_company(self):
        with company_scope(self.company_a):
            FoodItem.objects.create(
                name="Chips",
                selling_price=Decimal("3000.00"),
            )

        with company_scope(self.company_b):
            FoodItem.objects.create(
                name="Chips",
                selling_price=Decimal("4000.00"),
            )

        self.assertEqual(
            FoodItem.all_objects.filter(
                name="Chips"
            ).count(),
            2,
        )

    def test_archive_is_default_delete_behavior(self):
        purchase = self._purchase(
            self.company_a,
            supplier="Supplier Archive",
        )

        with company_scope(self.company_a):
            purchase.delete()

            self.assertEqual(
                Purchase.objects.filter(
                    pk=purchase.pk
                ).count(),
                0,
            )

        archived = Purchase.all_objects.get(
            pk=purchase.pk
        )

        self.assertTrue(
            archived.is_archived
        )

        self.assertIsNotNone(
            archived.archived_at
        )

    def test_owner_role_uses_real_permission_records(self):
        self.assertTrue(
            self.membership_a.role.permissions.filter(
                content_type__app_label="pigs",
                codename="view_pig",
            ).exists()
        )

        with company_scope(
            self.company_a,
            membership=self.membership_a,
        ):
            self.assertTrue(
                self.owner_a.has_perm(
                    "pigs.view_pig"
                )
            )

    def test_member_denial_removes_role_permission_without_changing_role(self):
        view_pig = Permission.objects.get(
            content_type__app_label="pigs",
            codename="view_pig",
        )

        role_permission_count = (
            self.membership_a.role.permissions.count()
        )

        self.membership_a.denied_permissions.add(
            view_pig
        )

        self.membership_a.refresh_from_db()

        with company_scope(
            self.company_a,
            membership=self.membership_a,
        ):
            self.assertFalse(
                self.owner_a.has_perm(
                    "pigs.view_pig"
                )
            )

        self.assertEqual(
            self.membership_a.role.permissions.count(),
            role_permission_count,
        )

        self.assertTrue(
            self.membership_a.role.permissions.filter(
                pk=view_pig.pk,
            ).exists()
        )

    def test_member_grant_adds_permission_without_changing_role(self):
        viewer = User.objects.create_user(
            username="viewer-a",
            email="viewer-a@example.test",
            password="ViewerPassword123!",
            is_active=True,
        )

        viewer_role = self.company_a.roles.get(
            code=ROLE_VIEWER
        )

        membership = CompanyMembership.objects.create(
            company=self.company_a,
            user=viewer,
            role=viewer_role,
            status=CompanyMembership.Status.ACTIVE,
            requested_language="sw",
            approved_language="sw",
            created_by=self.admin,
            approved_by=self.admin,
        )

        add_purchase = Permission.objects.get(
            content_type__app_label="pigs",
            codename="add_purchase",
        )

        self.assertFalse(
            viewer_role.permissions.filter(
                pk=add_purchase.pk,
            ).exists()
        )

        membership.granted_permissions.add(
            add_purchase
        )

        with company_scope(
            self.company_a,
            membership=membership,
        ):
            self.assertTrue(
                viewer.has_perm(
                    "pigs.add_purchase"
                )
            )

        self.assertFalse(
            viewer_role.permissions.filter(
                pk=add_purchase.pk,
            ).exists()
        )

    def test_permission_override_is_company_specific_for_same_user(self):
        company_c = create_company(
            actor=self.admin,
            name="Company C",
            code="company-c-override",
            owner=self.owner_a,
            allowed_languages=["sw", "en"],
            default_language="en",
            step_up_verified=True,
        )

        membership_c = (
            self.owner_a.company_memberships
            .select_related("role", "company")
            .get(company=company_c)
        )

        view_pig = Permission.objects.get(
            content_type__app_label="pigs",
            codename="view_pig",
        )

        self.membership_a.denied_permissions.add(
            view_pig
        )

        with company_scope(
            self.company_a,
            membership=self.membership_a,
        ):
            self.assertFalse(
                self.owner_a.has_perm(
                    "pigs.view_pig"
                )
            )

        with company_scope(
            company_c,
            membership=membership_c,
        ):
            self.assertTrue(
                self.owner_a.has_perm(
                    "pigs.view_pig"
                )
            )

    def test_company_owner_has_local_user_management_permission(self):
        with company_scope(
            self.company_a,
            membership=self.membership_a,
        ):
            self.assertTrue(
                self.owner_a.has_perm(
                    "companies.manage_company_users"
                )
            )

    def test_platform_admin_is_control_plane_only_for_business_permissions(self):
        self.assertFalse(
            self.admin.has_perm(
                "pigs.view_pig"
            )
        )

        with company_scope(self.company_a):
            self.assertFalse(
                self.admin.has_perm(
                    "pigs.view_pig"
                )
            )

    def test_owner_permission_does_not_cross_company_boundary(self):
        with company_scope(
            self.company_b,
            membership=self.membership_b,
        ):
            self.assertFalse(
                self.owner_a.has_perm(
                    "pigs.view_pig"
                )
            )