from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import TestCase

from apps.companies.models import Company, CompanyMembership
from apps.companies.scope import company_scope
from apps.pigs.models import (
    BatchProfitReport, DailySale, DailySaleReport, Expense, MeatStock,
    PigSaleRecord, Purchase,
)
from apps.pigs.management.commands import seed_year_demo_2026 as demo


class YearDemoFixtureTests(TestCase):
    def test_complete_year_per_company_has_all_dates_and_distinct_sales(self):
        self.assertTrue(demo.seed_year_demo(
            admin_password="test-admin-secret",
            test_password="test-member-secret",
        ))
        admin = demo.User.objects.get(username="Eliud")
        self.assertTrue(admin.is_platform_admin)
        self.assertTrue(admin.must_change_password)
        totals = []
        for spec in demo.SPECS:
            company = Company.objects.get(code=spec.code)
            with company_scope(company):
                dates = set(DailySale.objects.values_list("sale_date", flat=True))
                self.assertEqual(len(dates), 365)
                self.assertEqual(min(dates), demo.START)
                self.assertEqual(max(dates), demo.END)
                self.assertEqual(DailySaleReport.objects.count(), 365)
                self.assertEqual(PigSaleRecord.objects.count(), 365)
                self.assertEqual(
                    BatchProfitReport.objects.filter(total_profit__gt=0).count(),
                    MeatStock.objects.count(),
                )
                totals.append(sum(DailySale.objects.values_list(
                    "total_money_received", flat=True
                ), Decimal("0")))
        self.assertNotEqual(totals[0], totals[1])

    def test_two_companies_are_isolated_profitable_and_idempotent(self):
        # Full production date span is checked by the command. A short span
        # exercises the database lifecycle and security boundary quickly.
        with patch.object(demo, "END", date(2025, 10, 8)), patch.object(demo, "DAYS", 7):
            self.assertTrue(demo.seed_year_demo(
                admin_password="test-admin-secret",
                test_password="test-member-secret",
            ))
            companies = list(Company.objects.filter(
                code__in=[s.code for s in demo.SPECS]
            ).order_by("code"))
            self.assertEqual(len(companies), 2)
            totals = []
            for company in companies:
                with company_scope(company):
                    self.assertEqual(DailySale.objects.count(), 7)
                    self.assertEqual(DailySaleReport.objects.count(), 7)
                    self.assertEqual(PigSaleRecord.objects.count(), 7)
                    self.assertEqual(Purchase.objects.count(), MeatStock.objects.count())
                    self.assertEqual(
                        BatchProfitReport.objects.filter(total_profit__gt=0).count(),
                        MeatStock.objects.count(),
                    )
                    self.assertGreater(Expense.objects.count(), 0)
                    totals.append(sum(DailySale.objects.values_list(
                        "total_money_received", flat=True
                    ), Decimal("0")))
                    self.assertEqual(CompanyMembership.objects.filter(
                        company=company, status=CompanyMembership.Status.ACTIVE
                    ).count(), 3)
            self.assertNotEqual(totals[0], totals[1])
            self.assertFalse(demo.seed_year_demo(
                admin_password="test-admin-secret",
                test_password="test-member-secret",
            ))
            self.assertEqual(sum(demo._all(DailySale, c).count() for c in companies), 14)

    def test_existing_username_causes_atomic_refusal(self):
        user = demo.User(username="OwnerA", email="unrelated@example.invalid")
        user.set_password("another-password")
        user.save()
        with self.assertRaises(CommandError):
            demo.seed_year_demo(admin_password="a", test_password="b")
        self.assertFalse(Company.objects.filter(code__in=[s.code for s in demo.SPECS]).exists())
        self.assertFalse(demo.User.objects.filter(username="Eliud").exists())

    def test_year_span_is_exactly_365_days(self):
        self.assertEqual((demo.END - demo.START).days + 1, demo.DAYS)
