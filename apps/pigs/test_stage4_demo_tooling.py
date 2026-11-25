from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase

from apps.companies.constants import (
    ROLE_MANAGER,
    ROLE_OWNER,
    ROLE_STAFF,
)
from apps.companies.models import Company, CompanyMembership
from apps.companies.scope import company_scope
from apps.companies.services import create_company
from apps.pigs.models import (
    BatchProfitReport,
    DailySale,
    DailySaleReport,
    Expense,
    FoodItem,
    FoodSaleRecord,
    MeatPartSale,
    MeatStock,
    Pig,
    PigSaleRecord,
    Purchase,
    SlaughterBatch,
)
from apps.pigs.stage4_demo import (
    DEMO_COMPANIES,
    DEMO_DOMAIN,
    DEMO_MODEL_COUNTS,
    username_for,
)
from apps.users.models import User


class Stage4DemoToolingTests(TransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="stage4-admin",
            email="stage4-admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.tempdir = TemporaryDirectory()
        self.credentials = (
            Path(self.tempdir.name) / "demo_credentials.txt"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _seed(self):
        with patch(
            "apps.pigs.stage4_demo.getpass.getpass",
            return_value="AdminPassword123!",
        ):
            call_command(
                "seed_stage4_demo",
                admin_username=self.admin.username,
                credentials_file=str(self.credentials),
                verbosity=0,
            )

    def _cleanup(self):
        with patch(
            "apps.pigs.stage4_demo.getpass.getpass",
            return_value="AdminPassword123!",
        ):
            call_command(
                "cleanup_stage4_demo",
                admin_username=self.admin.username,
                credentials_file=str(self.credentials),
                confirm_demo_cleanup=True,
                verbosity=0,
            )

    def test_seed_creates_exactly_two_demo_companies(self):
        self._seed()

        companies = Company.objects.filter(
            code__in=[
                spec.code
                for spec in DEMO_COMPANIES
            ]
        )

        self.assertEqual(companies.count(), 2)
        self.assertTrue(self.credentials.exists())
        self.assertEqual(
            self.credentials.stat().st_mode & 0o777,
            0o600,
        )

        for spec in DEMO_COMPANIES:
            company = Company.objects.get(code=spec.code)
            self.assertFalse(
                company.owner_user_management_enabled
            )

            memberships = (
                CompanyMembership.objects
                .filter(
                    company=company,
                    status=CompanyMembership.Status.ACTIVE,
                )
                .select_related("role")
            )

            self.assertEqual(
                set(
                    memberships.values_list(
                        "role__code",
                        flat=True,
                    )
                ),
                {
                    ROLE_OWNER,
                    ROLE_MANAGER,
                    ROLE_STAFF,
                },
            )

    def test_seed_creates_representative_business_lifecycle(self):
        self._seed()

        for spec in DEMO_COMPANIES:
            company = Company.objects.get(code=spec.code)

            for model, expected in DEMO_MODEL_COUNTS.items():
                manager = getattr(
                    model,
                    "all_objects",
                    model._base_manager,
                )
                self.assertEqual(
                    manager.filter(company=company).count(),
                    expected,
                    msg=f"{spec.code} {model.__name__}",
                )

            with company_scope(company):
                self.assertEqual(
                    MeatStock.objects.filter(
                        is_finished=True
                    ).count(),
                    1,
                )
                self.assertEqual(
                    MeatStock.objects.filter(
                        is_finished=False
                    ).count(),
                    1,
                )
                self.assertEqual(
                    Pig.objects.filter(
                        status=Pig.FINISHED
                    ).count(),
                    2,
                )
                self.assertEqual(
                    Pig.objects.filter(
                        status=Pig.SLAUGHTERED
                    ).count(),
                    1,
                )
                self.assertEqual(
                    Pig.objects.filter(
                        status=Pig.AVAILABLE
                    ).count(),
                    1,
                )

                report = BatchProfitReport.objects.get()
                self.assertEqual(
                    report.total_profit,
                    220000,
                )

    def test_seed_is_idempotent(self):
        self._seed()

        before = {
            "companies": Company.objects.count(),
            "users": User.objects.count(),
            "memberships": CompanyMembership.objects.count(),
            "purchases": Purchase.all_objects.count(),
            "sales": DailySale.all_objects.count(),
        }

        self._seed()

        after = {
            "companies": Company.objects.count(),
            "users": User.objects.count(),
            "memberships": CompanyMembership.objects.count(),
            "purchases": Purchase.all_objects.count(),
            "sales": DailySale.all_objects.count(),
        }

        self.assertEqual(before, after)

    def test_cross_company_queries_are_isolated_for_demo_data(self):
        self._seed()

        company_a = Company.objects.get(
            code=DEMO_COMPANIES[0].code
        )
        company_b = Company.objects.get(
            code=DEMO_COMPANIES[1].code
        )

        with company_scope(company_a):
            self.assertEqual(Purchase.objects.count(), 1)
            self.assertEqual(Pig.objects.count(), 4)
            self.assertTrue(
                Purchase.objects.filter(
                    supplier_name__icontains="Njombe"
                ).exists()
            )
            self.assertFalse(
                Purchase.objects.filter(
                    supplier_name__icontains="Dodoma"
                ).exists()
            )

        with company_scope(company_b):
            self.assertEqual(Purchase.objects.count(), 1)
            self.assertEqual(Pig.objects.count(), 4)
            self.assertTrue(
                Purchase.objects.filter(
                    supplier_name__icontains="Dodoma"
                ).exists()
            )
            self.assertFalse(
                Purchase.objects.filter(
                    supplier_name__icontains="Njombe"
                ).exists()
            )

    def test_demo_roles_preserve_governance_and_permissions(self):
        self._seed()

        for spec in DEMO_COMPANIES:
            company = Company.objects.get(code=spec.code)

            owner_membership = CompanyMembership.objects.get(
                company=company,
                role__code=ROLE_OWNER,
            )
            manager_membership = CompanyMembership.objects.get(
                company=company,
                role__code=ROLE_MANAGER,
            )
            staff_membership = CompanyMembership.objects.get(
                company=company,
                role__code=ROLE_STAFF,
            )

            with company_scope(
                company,
                membership=owner_membership,
            ):
                self.assertTrue(
                    owner_membership.user.has_perm(
                        "pigs.view_pig"
                    )
                )

            with company_scope(
                company,
                membership=manager_membership,
            ):
                self.assertTrue(
                    manager_membership.user.has_perm(
                        "pigs.view_purchase"
                    )
                )

            with company_scope(
                company,
                membership=staff_membership,
            ):
                self.assertTrue(
                    staff_membership.user.has_perm(
                        "pigs.add_dailysale"
                    )
                )
                self.assertFalse(
                    staff_membership.user.has_perm(
                        "pigs.add_purchase"
                    )
                )

            self.assertFalse(
                company.owner_user_management_enabled
            )

        self.assertFalse(
            self.admin.has_perm("pigs.view_pig")
        )

    def test_cleanup_preserves_real_company_and_archives_demo_shells(self):
        real_owner = User.objects.create_user(
            username="real-owner-stage4",
            email="real-owner-stage4@example.test",
            password="RealOwnerPassword123!",
            is_active=True,
        )
        real_company = create_company(
            actor=self.admin,
            name="Real Company",
            code="real-company-stage4",
            owner=real_owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

        self._seed()
        self._cleanup()

        real_company.refresh_from_db()
        self.assertEqual(
            real_company.status,
            Company.Status.ACTIVE,
        )
        self.assertTrue(
            User.objects.filter(
                pk=real_owner.pk
            ).exists()
        )

        for spec in DEMO_COMPANIES:
            company = Company.objects.get(code=spec.code)
            self.assertEqual(
                company.status,
                Company.Status.ARCHIVED,
            )
            self.assertFalse(
                company.owner_user_management_enabled
            )
            self.assertFalse(
                CompanyMembership.objects.filter(
                    company=company
                ).exists()
            )

            for model in DEMO_MODEL_COUNTS:
                manager = getattr(
                    model,
                    "all_objects",
                    model._base_manager,
                )
                self.assertFalse(
                    manager.filter(company=company).exists(),
                    msg=f"{spec.code} {model.__name__}",
                )

        self.assertFalse(self.credentials.exists())

        self.assertFalse(
            User.objects.filter(
                email__endswith=f"@{DEMO_DOMAIN}"
            ).exists()
        )

    def test_cleanup_is_idempotent_and_reseedable(self):
        self._seed()
        self._cleanup()
        self._cleanup()

        self._seed()

        for spec in DEMO_COMPANIES:
            company = Company.objects.get(code=spec.code)
            self.assertEqual(
                company.status,
                Company.Status.ACTIVE,
            )
            self.assertEqual(
                CompanyMembership.objects.filter(
                    company=company,
                    status=CompanyMembership.Status.ACTIVE,
                ).count(),
                3,
            )

    def test_wrong_admin_password_is_rejected_without_demo_mutation(self):
        with patch(
            "apps.pigs.stage4_demo.getpass.getpass",
            return_value="WrongPassword!",
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "seed_stage4_demo",
                    admin_username=self.admin.username,
                    credentials_file=str(self.credentials),
                    verbosity=0,
                )

        self.assertFalse(
            Company.objects.filter(
                code__in=[
                    spec.code
                    for spec in DEMO_COMPANIES
                ]
            ).exists()
        )
