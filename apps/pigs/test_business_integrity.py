from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.companies.scope import company_scope
from apps.companies.services import create_company
from apps.users.models import User

from apps.pigs.models import (
    BatchProfitReport,
    DailySale,
    FoodItem,
    FoodSaleRecord,
    MeatStock,
    Pig,
    PigSaleRecord,
    Purchase,
    SlaughterBatch,
)

from apps.pigs.services import (
    confirm_meat_status,
    finalize_batch_profit,
    finalize_meat_for_daily_sale,
)


class BusinessIntegrityTests(TestCase):

    def setUp(self):

        self.admin = User.objects.create_user(
            username="business-integrity-admin",
            email="business-integrity-admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.owner = User.objects.create_user(
            username="business-integrity-owner",
            email="business-integrity-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        self.company = create_company(
            actor=self.admin,
            name="Business Integrity Company",
            code="business-integrity",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

        with company_scope(self.company):

            self.purchase = Purchase.objects.create(
                supplier_name="Stage 2 Supplier",
                purchase_date=date(2026, 9, 1),
                number_of_pigs=3,
                total_cost=Decimal("300000.00"),
            )

            pigs = list(
                Pig.objects.filter(
                    purchase=self.purchase
                ).order_by("pk")
            )

            while len(pigs) < 3:
                pigs.append(
                    Pig.objects.create(
                        purchase=self.purchase,
                        purchase_price=Decimal("100000.00"),
                    )
                )

            self.pigs = pigs[:3]

            for pig in self.pigs:
                if pig.purchase_price is None:
                    pig.purchase_price = Decimal("100000.00")
                    pig.save(
                        update_fields=[
                            "purchase_price",
                            "updated_at",
                        ]
                    )

            self.batch_a = SlaughterBatch.objects.create(
                slaughter_date=date(2026, 9, 10),
                notes="Stage 2 Batch A",
            )

            self.batch_a.pigs.add(
                self.pigs[0],
                self.pigs[1],
            )

            self.stock_a, _ = (
                MeatStock.objects.get_or_create(
                    slaughter_batch=self.batch_a
                )
            )

            self.batch_b = SlaughterBatch.objects.create(
                slaughter_date=date(2026, 9, 11),
                notes="Stage 2 Batch B",
            )

            self.batch_b.pigs.add(
                self.pigs[2]
            )

            self.stock_b, _ = (
                MeatStock.objects.get_or_create(
                    slaughter_batch=self.batch_b
                )
            )

    def test_daily_totals_split_food_and_meat_correctly(self):

        with company_scope(self.company):

            daily_sale = DailySale.objects.create(
                sale_date=date(2026, 9, 12),
                slaughter_batch=self.batch_a,
                total_money_received=Decimal("300000.00"),
                meat_price_per_kg=Decimal("10000.00"),
            )

            food_item = FoodItem.objects.create(
                name="Chips",
                selling_price=Decimal("25000.00"),
            )

            food_sale = FoodSaleRecord.objects.create(
                daily_sale=daily_sale,
                food_item=food_item,
                quantity=2,
                unit_price=Decimal("25000.00"),
            )

            daily_sale.refresh_totals()
            daily_sale.refresh_from_db()

            self.assertEqual(
                food_sale.total_price,
                Decimal("50000.00"),
            )

            self.assertEqual(
                daily_sale.total_food_sales,
                Decimal("50000.00"),
            )

            self.assertEqual(
                daily_sale.total_meat_sales,
                Decimal("250000.00"),
            )

            self.assertEqual(
                daily_sale.total_meat_weight_kg,
                Decimal("25.00"),
            )

            report = daily_sale.create_report()

            self.assertEqual(
                report.total_food_income,
                Decimal("50000.00"),
            )

            self.assertEqual(
                report.total_pig_income,
                Decimal("250000.00"),
            )

            self.assertEqual(
                report.total_income,
                Decimal("300000.00"),
            )

    def test_food_price_is_historical_not_catalog_recalculated(self):

        with company_scope(self.company):

            daily_sale = DailySale.objects.create(
                sale_date=date(2026, 9, 13),
                slaughter_batch=self.batch_a,
                total_money_received=Decimal("100000.00"),
                meat_price_per_kg=Decimal("10000.00"),
            )

            item = FoodItem.objects.create(
                name="Rice",
                selling_price=Decimal("5000.00"),
            )

            sale = FoodSaleRecord.objects.create(
                daily_sale=daily_sale,
                food_item=item,
                quantity=2,
                unit_price=Decimal("6000.00"),
            )

            item.selling_price = Decimal("9000.00")
            item.save(
                update_fields=[
                    "selling_price",
                    "updated_at",
                ]
            )

            sale.refresh_from_db()

            self.assertEqual(
                sale.unit_price,
                Decimal("6000.00"),
            )

            self.assertEqual(
                sale.total_price,
                Decimal("12000.00"),
            )

    def test_food_total_cannot_exceed_money_received(self):

        with company_scope(self.company):

            daily_sale = DailySale.objects.create(
                sale_date=date(2026, 9, 14),
                slaughter_batch=self.batch_a,
                total_money_received=Decimal("10000.00"),
                meat_price_per_kg=Decimal("10000.00"),
            )

            item = FoodItem.objects.create(
                name="Soup",
                selling_price=Decimal("6000.00"),
            )

            FoodSaleRecord.objects.create(
                daily_sale=daily_sale,
                food_item=item,
                quantity=2,
                unit_price=Decimal("6000.00"),
            )

            with self.assertRaises(ValueError):
                daily_sale.refresh_totals()

    def test_meat_sale_must_match_daily_sale_exact_batch(self):

        with company_scope(self.company):

            daily_sale = DailySale.objects.create(
                sale_date=date(2026, 9, 15),
                slaughter_batch=self.batch_a,
                total_money_received=Decimal("100000.00"),
                meat_price_per_kg=Decimal("10000.00"),
            )

            record = PigSaleRecord(
                company=self.company,
                daily_sale=daily_sale,
                slaughter_batch=self.batch_b,
                total_amount=Decimal("50000.00"),
                price_per_kg=Decimal("10000.00"),
            )

            with self.assertRaises(ValidationError):
                record.full_clean()

    def test_profit_requires_finished_stock(self):

        with company_scope(self.company):

            DailySale.objects.create(
                sale_date=date(2026, 9, 16),
                slaughter_batch=self.batch_a,
                total_money_received=Decimal("250000.00"),
                meat_price_per_kg=Decimal("10000.00"),
            )

            with self.assertRaises(ValueError):
                finalize_batch_profit(
                    slaughter_batch=self.batch_a
                )

    def test_finish_batch_creates_correct_idempotent_profit(self):

        with company_scope(self.company):

            daily_sale = DailySale.objects.create(
                sale_date=date(2026, 9, 17),
                slaughter_batch=self.batch_a,
                total_money_received=Decimal("300000.00"),
                meat_price_per_kg=Decimal("10000.00"),
            )

            food_item = FoodItem.objects.create(
                name="Ugali",
                selling_price=Decimal("25000.00"),
            )

            FoodSaleRecord.objects.create(
                daily_sale=daily_sale,
                food_item=food_item,
                quantity=2,
                unit_price=Decimal("25000.00"),
            )

            daily_sale.refresh_totals()

            report = confirm_meat_status(
                slaughter_batch=self.batch_a,
                meat_finished=True,
                confirmation_date=date(2026, 9, 17),
            )

            self.stock_a.refresh_from_db()

            self.assertTrue(
                self.stock_a.is_finished
            )

            self.assertEqual(
                self.stock_a.finished_date,
                date(2026, 9, 17),
            )

            for pig in self.pigs[:2]:
                pig.refresh_from_db()
                self.assertEqual(
                    pig.status,
                    Pig.FINISHED,
                )

            report.refresh_from_db()

            self.assertEqual(
                report.total_pigs,
                2,
            )

            self.assertEqual(
                report.total_purchase_cost,
                Decimal("200000.00"),
            )

            self.assertEqual(
                report.total_meat_sales,
                Decimal("250000.00"),
            )

            self.assertEqual(
                report.total_profit,
                Decimal("50000.00"),
            )

            second_report = confirm_meat_status(
                slaughter_batch=self.batch_a,
                meat_finished=True,
                confirmation_date=date(2026, 9, 18),
            )

            self.assertEqual(
                second_report.pk,
                report.pk,
            )

            self.assertEqual(
                BatchProfitReport.objects.filter(
                    slaughter_batch=self.batch_a
                ).count(),
                1,
            )

    def test_finalize_daily_sale_rejects_different_batch(self):

        with company_scope(self.company):

            daily_sale = DailySale.objects.create(
                sale_date=date(2026, 9, 18),
                slaughter_batch=self.batch_a,
                total_money_received=Decimal("100000.00"),
                meat_price_per_kg=Decimal("10000.00"),
            )

            with self.assertRaises(ValueError):
                finalize_meat_for_daily_sale(
                    daily_sale=daily_sale,
                    slaughter_batch=self.batch_b,
                )
