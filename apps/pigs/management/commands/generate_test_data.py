import random
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.pigs.models import (
    Purchase,
    Pig,
    SlaughterBatch,
    MeatStock,
    DailySale,
    DailySaleReport,
    PigSaleRecord,
    FoodItem,
    FoodSaleRecord,
    MeatPartSale,
    BatchProfitReport,
    Expense,
)


ZERO = Decimal("0.00")
CENT = Decimal("0.01")

MIN_PIG_PRICE = Decimal("400000.00")
MAX_PIG_PRICE = Decimal("700000.00")

MIN_PROFIT = Decimal("150000.00")
MAX_PROFIT = Decimal("700000.00")

MEAT_PRICE_PER_KG = Decimal("13500.00")

BATCH_DAYS_MIN = 1
BATCH_DAYS_MAX = 3

TWO_PIG_BATCH_PERCENT = 6

FOOD_ITEMS = [
    ("Ugali", Decimal("2500.00")),
    ("Ndizi", Decimal("3000.00")),
    ("Chips", Decimal("3500.00")),
]

FOOD_QUANTITY_MIN = 1
FOOD_QUANTITY_MAX = 8

MONEY_STEP = 10000

SEED = 20260823

TOTAL_PERIODS = 3
MONTHS_PER_PERIOD = 4


class Command(BaseCommand):

    help = (
        "Generate realistic, consistent and verified "
        "pig-management test data in three four-month periods."
    )

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.random = random.Random(SEED)

        self.food_items = []

        self.period_start = None
        self.period_end = None

    # ========================================================
    # COMMAND ARGUMENTS
    # ========================================================

    def add_arguments(self, parser):

        parser.add_argument(
            "--period",
            type=int,
            choices=[1, 2, 3],
            default=1,
            help=(
                "Four-month period to generate: "
                "1, 2 or 3."
            ),
        )

        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Delete all existing business test data "
                "before generating period 1."
            ),
        )

    # ========================================================
    # HANDLE
    # ========================================================

    def handle(self, *args, **options):

        period = options["period"]
        reset = options["reset"]

        self.stdout.write("")

        self.show_header()

        self.calculate_period_dates(period)

        self.show_period_information()

        if period == 1:

            if reset:

                self.confirm_reset()

                with transaction.atomic():

                    self.clear_business_data()

            else:

                if self.business_data_exists():

                    raise CommandError(
                        "Business data already exists. "
                        "Use --reset for period 1."
                    )

        else:

            if not self.business_data_exists():

                raise CommandError(
                    "No existing business test data found. "
                    "Generate period 1 first."
                )

        self.load_food_items()

        self.prevent_duplicate_period()

        try:

            plan = self.build_period_plan()

            self.show_plan_summary(plan)

            self.generate_data(plan)

            self.verify_period(
                plan,
                self.period_start,
                self.period_end,
            )

            self.show_period_summary()

            if period == TOTAL_PERIODS:

                self.verify_full_year()

                self.show_final_summary()

        except Exception as error:

            raise CommandError(
                "Test data generation failed.\n"
                f"Error: {error}"
            ) from error

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                f"PERIOD {period}/{TOTAL_PERIODS} "
                "COMPLETED SUCCESSFULLY."
            )
        )

    # ========================================================
    # HEADER
    # ========================================================

    def show_header(self):

        self.stdout.write(
            self.style.SUCCESS(
                "\n"
                "============================================================\n"
                "              PIG MANAGEMENT TEST DATA\n"
                "============================================================"
            )
        )

        self.stdout.write(
            "Generation strategy: 4 months × 3 periods"
        )

        self.stdout.write(
            "One DailySale per date"
        )

        self.stdout.write(
            "One batch finishes before the next batch starts"
        )

        self.stdout.write("")

    # ========================================================
    # PERIOD DATES
    # ========================================================

    def calculate_period_dates(self, period):

        today = timezone.localdate()

        first_day_current_month = today.replace(
            day=1
        )

        start_year = (
            first_day_current_month.year - 1
        )

        start_month = (
            first_day_current_month.month
        )

        year_start = date(
            start_year,
            start_month,
            1,
        )

        period_start_month_offset = (
            (period - 1) *
            MONTHS_PER_PERIOD
        )

        period_end_month_offset = (
            period *
            MONTHS_PER_PERIOD
            - 1
        )

        self.period_start = (
            self.add_months(
                year_start,
                period_start_month_offset,
            )
        )

        period_end_month_start = (
            self.add_months(
                year_start,
                period_end_month_offset,
            )
        )

        last_day = monthrange(
            period_end_month_start.year,
            period_end_month_start.month,
        )[1]

        self.period_end = date(
            period_end_month_start.year,
            period_end_month_start.month,
            last_day,
        )

    # ========================================================
    # ADD MONTHS
    # ========================================================

    def add_months(self, source_date, months):

        month_index = (
            source_date.month
            - 1
            + months
        )

        year = (
            source_date.year
            + month_index // 12
        )

        month = (
            month_index % 12
        ) + 1

        day = min(
            source_date.day,
            monthrange(year, month)[1],
        )

        return date(
            year,
            month,
            day,
        )

    # ========================================================
    # PERIOD INFORMATION
    # ========================================================

    def show_period_information(self):

        total_days = (
            self.period_end -
            self.period_start
        ).days + 1

        self.stdout.write(
            self.style.WARNING(
                "------------------------------------------------------------"
            )
        )

        self.stdout.write(
            f"PERIOD START : {self.period_start}"
        )

        self.stdout.write(
            f"PERIOD END   : {self.period_end}"
        )

        self.stdout.write(
            f"TOTAL DAYS   : {total_days}"
        )

        self.stdout.write(
            self.style.WARNING(
                "------------------------------------------------------------"
            )
        )

        self.stdout.write("")

    # ========================================================
    # CONFIRM RESET
    # ========================================================

    def confirm_reset(self):

        self.stdout.write(
            self.style.WARNING(
                "WARNING: Existing business test data "
                "will be deleted."
            )
        )

        self.stdout.write(
            "Users, permissions and migrations "
            "will NOT be touched."
        )

        self.stdout.write("")

        confirmation = input(
            "Type RESET to continue: "
        ).strip()

        if confirmation != "RESET":

            raise CommandError(
                "Operation cancelled."
            )

        self.stdout.write("")

    # ========================================================
    # BUSINESS DATA EXISTS
    # ========================================================

    def business_data_exists(self):

        return (
            Purchase.objects.exists()
            or
            Pig.objects.exists()
            or
            SlaughterBatch.objects.exists()
            or
            DailySale.objects.exists()
        )

    # ========================================================
    # CLEAR BUSINESS DATA
    # ========================================================

    def clear_business_data(self):

        self.stdout.write(
            self.style.WARNING(
                "Clearing existing business test data..."
            )
        )

        models = [
            BatchProfitReport,
            DailySaleReport,
            MeatPartSale,
            PigSaleRecord,
            FoodSaleRecord,
            DailySale,
            Expense,
            MeatStock,
            SlaughterBatch,
            Pig,
            Purchase,
            FoodItem,
        ]

        for model in models:

            count = model.objects.count()

            model.objects.all().delete()

            self.stdout.write(
                f"  Deleted {count} "
                f"{model.__name__} records."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Business data cleared."
            )
        )

        self.stdout.write("")

    # ========================================================
    # LOAD FOOD ITEMS
    # ========================================================

    def load_food_items(self):

        self.food_items = []

        for name, price in FOOD_ITEMS:

            item = (
                FoodItem.objects
                .filter(name=name)
                .first()
            )

            if item is None:

                item = FoodItem.objects.create(
                    name=name,
                    selling_price=price,
                    is_active=True,
                )

            else:

                changed = False

                if (
                    item.selling_price
                    != price
                ):

                    item.selling_price = price

                    changed = True

                if not item.is_active:

                    item.is_active = True

                    changed = True

                if changed:

                    item.save()

            self.food_items.append(item)

    # ========================================================
    # PREVENT DUPLICATE PERIOD
    # ========================================================

    def prevent_duplicate_period(self):

        exists = (
            DailySale.objects
            .filter(
                sale_date__gte=self.period_start,
                sale_date__lte=self.period_end,
            )
            .exists()
        )

        if exists:

            raise CommandError(
                "Daily sales already exist inside this period.\n"
                f"Period: {self.period_start} "
                f"to {self.period_end}\n"
                "This protects the rule that one date "
                "can have only one DailySale."
            )

    # ========================================================
    # MONEY
    # ========================================================

    def money(self, value):

        return Decimal(value).quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )

    # ========================================================
    # RANDOM MONEY
    # ========================================================

    def random_money(
        self,
        minimum,
        maximum,
        step=MONEY_STEP,
    ):

        minimum_int = int(minimum)
        maximum_int = int(maximum)
        step_int = int(step)

        value = self.random.randrange(
            minimum_int,
            maximum_int + 1,
            step_int,
        )

        return self.money(value)

    # ========================================================
    # SPLIT MONEY
    # ========================================================

    def split_amount(
        self,
        amount,
        parts,
    ):

        amount = self.money(amount)

        if parts == 1:

            return [amount]

        base = self.money(
            amount / Decimal(parts)
        )

        values = [
            base
            for _ in range(parts)
        ]

        difference = (
            amount -
            sum(values, ZERO)
        )

        values[-1] = self.money(
            values[-1] + difference
        )

        return values

    # ========================================================
    # BUILD PERIOD PLAN
    # ========================================================

    def build_period_plan(self):

        plan = []

        current_date = self.period_start

        existing_batches = (
            SlaughterBatch.objects.count()
        )

        batch_number = (
            existing_batches + 1
        )

        while current_date <= self.period_end:

            remaining_days = (
                self.period_end -
                current_date
            ).days + 1

            max_batch_days = min(
                BATCH_DAYS_MAX,
                remaining_days,
            )

            batch_days = self.random.randint(
                BATCH_DAYS_MIN,
                max_batch_days,
            )

            if (
                batch_days == 1
                and
                remaining_days > 1
            ):

                batch_days = min(
                    2,
                    remaining_days,
                )

            pig_count = (
                2
                if (
                    self.random.randint(1, 100)
                    <= TWO_PIG_BATCH_PERCENT
                )
                else 1
            )

            pigs = []

            purchase_cost = ZERO

            for pig_index in range(
                pig_count
            ):

                price = self.random_money(
                    MIN_PIG_PRICE,
                    MAX_PIG_PRICE,
                )

                gender = (
                    "MALE"
                    if (
                        (
                            batch_number +
                            pig_index
                        ) % 2 == 0
                    )
                    else "FEMALE"
                )

                pigs.append({
                    "price": price,
                    "gender": gender,
                })

                purchase_cost += price

            purchase_cost = self.money(
                purchase_cost
            )

            target_profit = self.random_money(
                MIN_PROFIT,
                MAX_PROFIT,
            )

            target_meat_sales = self.money(
                purchase_cost +
                target_profit
            )

            daily_meat_sales = (
                self.split_amount(
                    target_meat_sales,
                    batch_days,
                )
            )

            sale_dates = [
                current_date +
                timedelta(days=index)
                for index in range(batch_days)
            ]

            plan.append({
                "batch_sequence": batch_number,
                "slaughter_date": current_date,
                "sale_dates": sale_dates,
                "batch_days": batch_days,
                "pig_count": pig_count,
                "pigs": pigs,
                "purchase_cost": purchase_cost,
                "target_profit": target_profit,
                "target_meat_sales": target_meat_sales,
                "daily_meat_sales": daily_meat_sales,
            })

            current_date += timedelta(
                days=batch_days
            )

            batch_number += 1

        planned_dates = []

        for batch_plan in plan:

            planned_dates.extend(
                batch_plan["sale_dates"]
            )

        expected_days = (
            self.period_end -
            self.period_start
        ).days + 1

        if len(planned_dates) != expected_days:

            raise ValueError(
                "Period plan did not generate "
                "the expected number of days."
            )

        if len(set(planned_dates)) != expected_days:

            raise ValueError(
                "Period plan contains duplicate dates."
            )

        return plan

    # ========================================================
    # PLAN SUMMARY
    # ========================================================

    def show_plan_summary(self, plan):

        total_days = sum(
            item["batch_days"]
            for item in plan
        )

        two_pig_batches = sum(
            1
            for item in plan
            if item["pig_count"] == 2
        )

        self.stdout.write(
            self.style.SUCCESS(
                "PLAN CREATED"
            )
        )

        self.stdout.write(
            f"  Batches planned : {len(plan)}"
        )

        self.stdout.write(
            f"  Sale days      : {total_days}"
        )

        self.stdout.write(
            f"  Two-pig batches: {two_pig_batches}"
        )

        self.stdout.write("")

    # ========================================================
    # GENERATE DATA
    # ========================================================

    def generate_data(self, plan):

        for index, batch_plan in enumerate(
            plan,
            start=1,
        ):

            self.stdout.write(
                self.style.WARNING(
                    "\n============================================================"
                )
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"BATCH {index}/{len(plan)}"
                )
            )

            self.stdout.write(
                self.style.WARNING(
                    "============================================================"
                )
            )

            try:

                with transaction.atomic():

                    batch = self.create_batch(
                        batch_plan
                    )

                    self.show_batch_start(
                        batch,
                        batch_plan,
                    )

                    for sale_index, sale_date in enumerate(
                        batch_plan["sale_dates"]
                    ):

                        self.create_daily_sale(
                            batch=batch,
                            sale_date=sale_date,
                            meat_sales=(
                                batch_plan[
                                    "daily_meat_sales"
                                ][sale_index]
                            ),
                            day_number=sale_index + 1,
                            total_days=batch_plan[
                                "batch_days"
                            ],
                        )

                    report = self.finish_batch(
                        batch
                    )

                    self.show_batch_finished(
                        batch,
                        report,
                    )

            except Exception as error:

                self.stdout.write(
                    self.style.ERROR(
                        f"\nBATCH FAILED: {error}"
                    )
                )

                raise

    # ========================================================
    # SHOW BATCH START
    # ========================================================

    def show_batch_start(
        self,
        batch,
        batch_plan,
    ):

        self.stdout.write(
            f"Batch number : {batch.batch_number}"
        )

        self.stdout.write(
            f"Slaughter    : "
            f"{batch_plan['slaughter_date']}"
        )

        self.stdout.write(
            f"Pigs         : "
            f"{batch_plan['pig_count']}"
        )

        self.stdout.write(
            f"Purchase cost: "
            f"TSh {batch_plan['purchase_cost']:,.0f}"
        )

        self.stdout.write(
            f"Target profit: "
            f"TSh {batch_plan['target_profit']:,.0f}"
        )

        self.stdout.write(
            f"Meat sales   : "
            f"TSh {batch_plan['target_meat_sales']:,.0f}"
        )

        self.stdout.write(
            f"Sale days    : "
            f"{batch_plan['batch_days']}"
        )

        self.stdout.write("")

    # ========================================================
    # CREATE BATCH
    # ========================================================

    def create_batch(self, batch_plan):

        sequence = (
            batch_plan["batch_sequence"]
        )

        purchase = Purchase.objects.create(
            supplier_name=(
                f"Test Supplier {sequence:03d}"
            ),
            supplier_phone=(
                f"0712{sequence:06d}"
            ),
            supplier_location="Njombe",
            purchase_date=(
                batch_plan["slaughter_date"]
            ),
            number_of_pigs=(
                batch_plan["pig_count"]
            ),
            total_cost=(
                batch_plan["purchase_cost"]
            ),
            pigs_created=False,
            notes=(
                f"Generated test purchase "
                f"B{sequence:03d}"
            ),
        )

        pigs = []

        for index, pig_data in enumerate(
            batch_plan["pigs"],
            start=1,
        ):

            pig = Pig.objects.create(
                purchase=purchase,
                gender=pig_data["gender"],
                purchase_price=pig_data["price"],
                status="AVAILABLE",
                notes=(
                    f"Generated test pig "
                    f"B{sequence:03d}-P{index}"
                ),
            )

            pigs.append(pig)

        purchase.pigs_created = True

        purchase.save(
            update_fields=[
                "pigs_created",
                "updated_at",
            ]
        )

        batch = SlaughterBatch.objects.create(
            slaughter_date=(
                batch_plan["slaughter_date"]
            ),
            notes=(
                f"Generated test batch "
                f"B{sequence:03d}"
            ),
        )

        batch.add_pigs(pigs)

        MeatStock.objects.create(
            slaughter_batch=batch,
            is_finished=False,
            needs_confirmation=False,
            finished_date=None,
        )

        return batch

    # ========================================================
    # CREATE DAILY SALE
    # ========================================================

    def create_daily_sale(
        self,
        batch,
        sale_date,
        meat_sales,
        day_number,
        total_days,
    ):

        if DailySale.objects.filter(
            sale_date=sale_date
        ).exists():

            raise ValueError(
                f"DailySale already exists for "
                f"{sale_date}."
            )

        meat_sales = self.money(
            meat_sales
        )

        food_total = (
            self.calculate_food_total(
                sale_date
            )
        )

        total_received = self.money(
            meat_sales +
            food_total
        )

        daily_sale = DailySale.objects.create(
            sale_date=sale_date,
            slaughter_batch=batch,
            total_money_received=total_received,
            total_food_sales=ZERO,
            total_meat_sales=ZERO,
            meat_price_per_kg=(
                MEAT_PRICE_PER_KG
            ),
            total_meat_weight_kg=ZERO,
            notes=(
                f"Generated test sale "
                f"Day {day_number}/{total_days} "
                f"for {batch.batch_number}"
            ),
        )

        self.create_food_sales(
            daily_sale
        )

        self.create_meat_sale(
            daily_sale=daily_sale,
            batch=batch,
            meat_sales=meat_sales,
        )

        daily_sale.calculate_totals()

        daily_sale.save(
            update_fields=[
                "total_food_sales",
                "total_meat_sales",
                "total_meat_weight_kg",
                "updated_at",
            ]
        )

        daily_sale.create_report()

        self.show_daily_sale(
            daily_sale,
            batch,
            day_number,
            total_days,
        )

        return daily_sale

    # ========================================================
    # SHOW DAILY SALE
    # ========================================================

    def show_daily_sale(
        self,
        daily_sale,
        batch,
        day_number,
        total_days,
    ):

        self.stdout.write(
            f"  Day {day_number}/{total_days} "
            f"| {daily_sale.sale_date}"
        )

        self.stdout.write(
            f"    Meat : "
            f"TSh {daily_sale.total_meat_sales:,.0f}"
        )

        self.stdout.write(
            f"    Food : "
            f"TSh {daily_sale.total_food_sales:,.0f}"
        )

        self.stdout.write(
            f"    Total: "
            f"TSh {daily_sale.total_money_received:,.0f}"
        )

        self.stdout.write(
            f"    Meat weight: "
            f"{daily_sale.total_meat_weight_kg:,.2f} kg"
        )

    # ========================================================
    # CALCULATE FOOD TOTAL
    # ========================================================

    def calculate_food_total(
        self,
        sale_date,
    ):

        total = ZERO

        for index, item in enumerate(
            self.food_items
        ):

            quantity = (
                FOOD_QUANTITY_MIN
                +
                (
                    (
                        sale_date.day
                        +
                        sale_date.month
                        +
                        index
                    )
                    %
                    (
                        FOOD_QUANTITY_MAX
                        -
                        FOOD_QUANTITY_MIN
                        +
                        1
                    )
                )
            )

            total += (
                Decimal(quantity)
                *
                item.selling_price
            )

        return self.money(total)

    # ========================================================
    # CREATE FOOD SALES
    # ========================================================

    def create_food_sales(
        self,
        daily_sale,
    ):

        total = ZERO

        for index, item in enumerate(
            self.food_items
        ):

            quantity = (
                FOOD_QUANTITY_MIN
                +
                (
                    (
                        daily_sale.sale_date.day
                        +
                        daily_sale.sale_date.month
                        +
                        index
                    )
                    %
                    (
                        FOOD_QUANTITY_MAX
                        -
                        FOOD_QUANTITY_MIN
                        +
                        1
                    )
                )
            )

            FoodSaleRecord.objects.create(
                daily_sale=daily_sale,
                food_item=item,
                quantity=quantity,
                unit_price=item.selling_price,
            )

            total += (
                Decimal(quantity)
                *
                item.selling_price
            )

        return self.money(total)

    # ========================================================
    # CREATE MEAT SALE
    # ========================================================

    def create_meat_sale(
        self,
        daily_sale,
        batch,
        meat_sales,
    ):

        PigSaleRecord.objects.create(
            daily_sale=daily_sale,
            slaughter_batch=batch,
            price_per_kg=(
                MEAT_PRICE_PER_KG
            ),
            total_amount=self.money(
                meat_sales
            ),
            notes=(
                f"Generated meat sale "
                f"for {batch.batch_number}"
            ),
        )

    # ========================================================
    # FINISH BATCH
    # ========================================================

    def finish_batch(self, batch):

        self.stdout.write("")

        self.stdout.write(
            f"  Finishing {batch.batch_number}..."
        )

        stock = MeatStock.objects.get(
            slaughter_batch=batch
        )

        if stock.is_finished:

            raise ValueError(
                f"{batch.batch_number} "
                "is already finished."
            )

        stock.mark_finished()

        stock.refresh_from_db()

        batch.refresh_from_db()

        if not stock.is_finished:

            raise ValueError(
                f"{batch.batch_number} "
                "failed to finish."
            )

        if stock.needs_confirmation:

            raise ValueError(
                f"{batch.batch_number} "
                "still needs confirmation."
            )

        pig_count = batch.pigs.count()

        finished_pigs = (
            batch.pigs
            .filter(status="FINISHED")
            .count()
        )

        if finished_pigs != pig_count:

            raise ValueError(
                f"{batch.batch_number}: "
                f"Only {finished_pigs}/{pig_count} "
                "pigs are FINISHED."
            )

        try:

            report = batch.profit_report

        except BatchProfitReport.DoesNotExist:

            raise ValueError(
                f"{batch.batch_number} "
                "has no profit report."
            )

        if not (
            MIN_PROFIT
            <= report.total_profit
            <= MAX_PROFIT
        ):

            raise ValueError(
                f"{batch.batch_number} generated "
                f"profit {report.total_profit}, "
                "outside allowed range."
            )

        return report

    # ========================================================
    # SHOW BATCH FINISHED
    # ========================================================

    def show_batch_finished(
        self,
        batch,
        report,
    ):

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                f"  {batch.batch_number} COMPLETED"
            )
        )

        self.stdout.write(
            f"    Purchase cost : "
            f"TSh {report.total_purchase_cost:,.0f}"
        )

        self.stdout.write(
            f"    Meat sales    : "
            f"TSh {report.total_meat_sales:,.0f}"
        )

        self.stdout.write(
            f"    PROFIT        : "
            f"TSh {report.total_profit:,.0f}"
        )

        self.stdout.write(
            f"    Pigs          : "
            f"{report.total_pigs}"
        )

        self.stdout.write(
            "    MeatStock     : FINISHED"
        )

        self.stdout.write(
            "    Pig status    : FINISHED"
        )

    # ========================================================
    # VERIFY PERIOD
    # ========================================================

    def verify_period(
        self,
        plan,
        start_date,
        end_date,
    ):

        errors = []

        expected_days = (
            end_date -
            start_date
        ).days + 1

        # ----------------------------------------------------
        # DAILY SALES
        # ----------------------------------------------------

        daily_sales = list(
            DailySale.objects
            .filter(
                sale_date__gte=start_date,
                sale_date__lte=end_date,
            )
            .order_by("sale_date")
        )

        if len(daily_sales) != expected_days:

            errors.append(
                f"Expected {expected_days} DailySale "
                f"records, found {len(daily_sales)}."
            )

        dates = [
            sale.sale_date
            for sale in daily_sales
        ]

        if len(set(dates)) != len(dates):

            errors.append(
                "Duplicate DailySale dates found."
            )

        if dates:

            expected_dates = [
                start_date +
                timedelta(days=index)
                for index in range(expected_days)
            ]

            if dates != expected_dates:

                errors.append(
                    "DailySale dates are not continuous."
                )

        # ----------------------------------------------------
        # BATCHES
        # ----------------------------------------------------

        batches = list(
            SlaughterBatch.objects
            .filter(
                slaughter_date__gte=start_date,
                slaughter_date__lte=end_date,
            )
            .order_by(
                "slaughter_date",
                "id",
            )
        )

        if len(batches) != len(plan):

            errors.append(
                "Batch count does not match period plan."
            )

        # ----------------------------------------------------
        # BATCH VALIDATION
        # ----------------------------------------------------

        for batch in batches:

            pig_count = batch.pigs.count()

            if pig_count not in (1, 2):

                errors.append(
                    f"{batch.batch_number} has "
                    f"{pig_count} pigs."
                )

            # ------------------------------------------------
            # PIGS
            # ------------------------------------------------

            for pig in batch.pigs.all():

                purchase_price = (
                    pig.get_purchase_cost()
                )

                if not (
                    MIN_PIG_PRICE
                    <= purchase_price
                    <= MAX_PIG_PRICE
                ):

                    errors.append(
                        f"{batch.batch_number}: "
                        f"pig {pig.tag_number} "
                        f"has invalid price "
                        f"{purchase_price}."
                    )

                if pig.status != "FINISHED":

                    errors.append(
                        f"{batch.batch_number}: "
                        f"pig {pig.tag_number} "
                        "is not FINISHED."
                    )

            # ------------------------------------------------
            # PURCHASE COST
            # ------------------------------------------------

            purchase_cost = (
                batch.calculate_total_purchase_cost()
            )

            if purchase_cost <= ZERO:

                errors.append(
                    f"{batch.batch_number} has "
                    "zero purchase cost."
                )

            # ------------------------------------------------
            # STOCK
            # ------------------------------------------------

            try:

                stock = batch.stock

            except MeatStock.DoesNotExist:

                errors.append(
                    f"{batch.batch_number} "
                    "has no MeatStock."
                )

                continue

            if not stock.is_finished:

                errors.append(
                    f"{batch.batch_number} "
                    "stock is not finished."
                )

            if stock.needs_confirmation:

                errors.append(
                    f"{batch.batch_number} "
                    "still needs confirmation."
                )

            if stock.finished_date is None:

                errors.append(
                    f"{batch.batch_number} "
                    "has no finished date."
                )

            # ------------------------------------------------
            # BATCH SALES
            # ------------------------------------------------

            batch_sales = list(
                batch.daily_sales
                .order_by("sale_date")
            )

            if not (
                BATCH_DAYS_MIN
                <= len(batch_sales)
                <= BATCH_DAYS_MAX
            ):

                errors.append(
                    f"{batch.batch_number} has "
                    f"{len(batch_sales)} sale days."
                )

            sale_dates = [
                sale.sale_date
                for sale in batch_sales
            ]

            if sale_dates:

                expected = [
                    sale_dates[0] +
                    timedelta(days=index)
                    for index in range(
                        len(sale_dates)
                    )
                ]

                if sale_dates != expected:

                    errors.append(
                        f"{batch.batch_number} "
                        "sale dates are not continuous."
                    )

            # ------------------------------------------------
            # ONE SALE PER DATE
            # ------------------------------------------------

            for sale_date in sale_dates:

                same_date_count = (
                    DailySale.objects
                    .filter(
                        sale_date=sale_date
                    )
                    .count()
                )

                if same_date_count != 1:

                    errors.append(
                        f"{sale_date} has "
                        f"{same_date_count} DailySale records."
                    )

            # ------------------------------------------------
            # MEAT SALES
            # ------------------------------------------------

            meat_sales = (
                batch.sales.aggregate(
                    total=Sum("total_amount")
                )["total"]
                or ZERO
            )

            meat_sales = self.money(
                meat_sales
            )

            if meat_sales <= ZERO:

                errors.append(
                    f"{batch.batch_number} "
                    "has zero meat sales."
                )

            # ------------------------------------------------
            # PROFIT
            # ------------------------------------------------

            expected_profit = self.money(
                meat_sales -
                purchase_cost
            )

            try:

                report = batch.profit_report

            except BatchProfitReport.DoesNotExist:

                errors.append(
                    f"{batch.batch_number} "
                    "has no profit report."
                )

                continue

            if (
                report.total_purchase_cost
                != self.money(purchase_cost)
            ):

                errors.append(
                    f"{batch.batch_number} "
                    "purchase cost mismatch."
                )

            if (
                report.total_meat_sales
                != meat_sales
            ):

                errors.append(
                    f"{batch.batch_number} "
                    "meat sales mismatch."
                )

            if (
                report.total_profit
                != expected_profit
            ):

                errors.append(
                    f"{batch.batch_number} "
                    "profit calculation mismatch."
                )

            if not (
                MIN_PROFIT
                <= report.total_profit
                <= MAX_PROFIT
            ):

                errors.append(
                    f"{batch.batch_number} "
                    f"profit {report.total_profit} "
                    "outside allowed range."
                )

            if report.total_pigs != pig_count:

                errors.append(
                    f"{batch.batch_number} "
                    "profit report pig count mismatch."
                )

            # ------------------------------------------------
            # DAILY RECORDS
            # ------------------------------------------------

            for sale in batch_sales:

                pig_records = list(
                    sale.pig_records.all()
                )

                food_records = list(
                    sale.food_records.all()
                )

                if not pig_records:

                    errors.append(
                        f"{sale.sale_date} "
                        "has no PigSaleRecord."
                    )

                if len(pig_records) != 1:

                    errors.append(
                        f"{sale.sale_date} has "
                        f"{len(pig_records)} "
                        "PigSaleRecords."
                    )

                if not food_records:

                    errors.append(
                        f"{sale.sale_date} "
                        "has no FoodSaleRecord."
                    )

                pig_total = (
                    sale.pig_records.aggregate(
                        total=Sum("total_amount")
                    )["total"]
                    or ZERO
                )

                food_total = (
                    sale.food_records.aggregate(
                        total=Sum("total_price")
                    )["total"]
                    or ZERO
                )

                pig_total = self.money(
                    pig_total
                )

                food_total = self.money(
                    food_total
                )

                expected_total = self.money(
                    pig_total +
                    food_total
                )

                if (
                    sale.total_money_received
                    != expected_total
                ):

                    errors.append(
                        f"{sale.sale_date} "
                        "total money mismatch."
                    )

                if (
                    sale.total_meat_sales
                    != pig_total
                ):

                    errors.append(
                        f"{sale.sale_date} "
                        "meat sales mismatch."
                    )

                if (
                    sale.total_food_sales
                    != food_total
                ):

                    errors.append(
                        f"{sale.sale_date} "
                        "food sales mismatch."
                    )

                if (
                    sale.total_meat_weight_kg
                    <= ZERO
                ):

                    errors.append(
                        f"{sale.sale_date} "
                        "meat weight is zero."
                    )

                # --------------------------------------------
                # DAILY REPORT
                # --------------------------------------------

                try:

                    daily_report = sale.report

                except DailySaleReport.DoesNotExist:

                    errors.append(
                        f"{sale.sale_date} "
                        "has no DailySaleReport."
                    )

                    continue

                if (
                    daily_report.total_pig_income
                    != sale.total_meat_sales
                ):

                    errors.append(
                        f"{sale.sale_date} "
                        "daily report pig income mismatch."
                    )

                if (
                    daily_report.total_food_income
                    != sale.total_food_sales
                ):

                    errors.append(
                        f"{sale.sale_date} "
                        "daily report food income mismatch."
                    )

                if (
                    daily_report.total_income
                    != sale.total_money_received
                ):

                    errors.append(
                        f"{sale.sale_date} "
                        "daily report total mismatch."
                    )

        # ----------------------------------------------------
        # FOOD ITEMS
        # ----------------------------------------------------

        if (
            FoodItem.objects.count()
            != len(FOOD_ITEMS)
        ):

            errors.append(
                "FoodItem count mismatch."
            )

        # ----------------------------------------------------
        # FOOD SALES FOR PERIOD
        # ----------------------------------------------------

        expected_food_records = (
            expected_days *
            len(FOOD_ITEMS)
        )

        actual_food_records = (
            FoodSaleRecord.objects
            .filter(
                daily_sale__sale_date__gte=start_date,
                daily_sale__sale_date__lte=end_date,
            )
            .count()
        )

        if (
            actual_food_records
            != expected_food_records
        ):

            errors.append(
                f"Expected "
                f"{expected_food_records} "
                f"food records, found "
                f"{actual_food_records}."
            )

        # ----------------------------------------------------
        # PIG SALES FOR PERIOD
        # ----------------------------------------------------

        actual_pig_sales = (
            PigSaleRecord.objects
            .filter(
                daily_sale__sale_date__gte=start_date,
                daily_sale__sale_date__lte=end_date,
            )
            .count()
        )

        if actual_pig_sales != expected_days:

            errors.append(
                f"Expected exactly one PigSaleRecord "
                f"per day. Expected {expected_days}, "
                f"found {actual_pig_sales}."
            )

        # ----------------------------------------------------
        # BATCH PROFIT REPORTS
        # ----------------------------------------------------

        profit_reports = (
            BatchProfitReport.objects
            .filter(
                slaughter_batch__slaughter_date__gte=start_date,
                slaughter_batch__slaughter_date__lte=end_date,
            )
            .count()
        )

        if profit_reports != len(batches):

            errors.append(
                "BatchProfitReport count does not "
                "match batch count."
            )

        # ----------------------------------------------------
        # DAILY REPORTS
        # ----------------------------------------------------

        daily_reports = (
            DailySaleReport.objects
            .filter(
                daily_sale__sale_date__gte=start_date,
                daily_sale__sale_date__lte=end_date,
            )
            .count()
        )

        if daily_reports != expected_days:

            errors.append(
                f"Expected {expected_days} DailySaleReport "
                f"records, found {daily_reports}."
            )

        # ----------------------------------------------------
        # UNFINISHED STOCK
        # ----------------------------------------------------

        unfinished_stock = (
            MeatStock.objects
            .filter(
                slaughter_batch__slaughter_date__gte=start_date,
                slaughter_batch__slaughter_date__lte=end_date,
                is_finished=False,
            )
            .count()
        )

        if unfinished_stock != 0:

            errors.append(
                f"{unfinished_stock} unfinished "
                "MeatStock records."
            )

        # ----------------------------------------------------
        # BATCH OVERLAP
        # ----------------------------------------------------

        for index in range(
            len(batches) - 1
        ):

            current = batches[index]

            next_batch = batches[index + 1]

            current_last_sale = (
                current.daily_sales
                .order_by("-sale_date")
                .values_list(
                    "sale_date",
                    flat=True,
                )
                .first()
            )

            next_start = (
                next_batch.slaughter_date
            )

            if (
                current_last_sale
                and
                next_start <= current_last_sale
            ):

                errors.append(
                    f"Batch overlap between "
                    f"{current.batch_number} "
                    f"and "
                    f"{next_batch.batch_number}."
                )

        # ----------------------------------------------------
        # PLAN VALIDATION
        # ----------------------------------------------------

        planned_days = []

        for batch_plan in plan:

            planned_days.extend(
                batch_plan["sale_dates"]
            )

        if len(planned_days) != expected_days:

            errors.append(
                "Plan does not contain "
                "the expected number of dates."
            )

        if (
            len(set(planned_days))
            != expected_days
        ):

            errors.append(
                "Plan contains duplicate dates."
            )

        if planned_days:

            if planned_days[0] != start_date:

                errors.append(
                    "Plan does not start on period start date."
                )

            if planned_days[-1] != end_date:

                errors.append(
                    "Plan does not end on period end date."
                )

        # ----------------------------------------------------
        # FINAL
        # ----------------------------------------------------

        if errors:

            message = "\n".join(
                f"- {error}"
                for error in errors
            )

            raise ValueError(
                "STRICT PERIOD VERIFICATION FAILED:\n"
                + message
            )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "STRICT PERIOD VERIFICATION PASSED."
            )
        )

    # ========================================================
    # VERIFY FULL YEAR
    # ========================================================

    def verify_full_year(self):

        today = timezone.localdate()

        first_day_current_month = today.replace(
            day=1
        )

        year_start = date(
            first_day_current_month.year - 1,
            first_day_current_month.month,
            1,
        )

        last_month_start = self.add_months(
            year_start,
            11,
        )

        last_day = monthrange(
            last_month_start.year,
            last_month_start.month,
        )[1]

        year_end = date(
            last_month_start.year,
            last_month_start.month,
            last_day,
        )

        expected_days = (
            year_end -
            year_start
        ).days + 1

        errors = []

        # ----------------------------------------------------
        # DAILY SALES
        # ----------------------------------------------------

        daily_sales = list(
            DailySale.objects
            .filter(
                sale_date__gte=year_start,
                sale_date__lte=year_end,
            )
            .order_by("sale_date")
        )

        if len(daily_sales) != expected_days:

            errors.append(
                f"Expected {expected_days} DailySale "
                f"records, found {len(daily_sales)}."
            )

        dates = [
            sale.sale_date
            for sale in daily_sales
        ]

        if len(set(dates)) != len(dates):

            errors.append(
                "Duplicate dates found in full year."
            )

        expected_dates = [
            year_start +
            timedelta(days=index)
            for index in range(expected_days)
        ]

        if dates != expected_dates:

            errors.append(
                "Full-year DailySale dates are not continuous."
            )

        # ----------------------------------------------------
        # PURCHASES
        # ----------------------------------------------------

        purchase_count = (
            Purchase.objects
            .filter(
                purchase_date__gte=year_start,
                purchase_date__lte=year_end,
            )
            .count()
        )

        batch_count = (
            SlaughterBatch.objects
            .filter(
                slaughter_date__gte=year_start,
                slaughter_date__lte=year_end,
            )
            .count()
        )

        if purchase_count != batch_count:

            errors.append(
                "Purchase count does not match "
                "full-year batch count."
            )

        # ----------------------------------------------------
        # PIG SALES
        # ----------------------------------------------------

        pig_sales = (
            PigSaleRecord.objects
            .filter(
                daily_sale__sale_date__gte=year_start,
                daily_sale__sale_date__lte=year_end,
            )
            .count()
        )

        if pig_sales != expected_days:

            errors.append(
                "Full-year PigSaleRecord count "
                "does not equal DailySale count."
            )

        # ----------------------------------------------------
        # FOOD SALES
        # ----------------------------------------------------

        food_sales = (
            FoodSaleRecord.objects
            .filter(
                daily_sale__sale_date__gte=year_start,
                daily_sale__sale_date__lte=year_end,
            )
            .count()
        )

        expected_food_sales = (
            expected_days *
            len(FOOD_ITEMS)
        )

        if food_sales != expected_food_sales:

            errors.append(
                f"Expected {expected_food_sales} "
                f"food sales, found {food_sales}."
            )

        # ----------------------------------------------------
        # REPORTS
        # ----------------------------------------------------

        daily_reports = (
            DailySaleReport.objects
            .filter(
                daily_sale__sale_date__gte=year_start,
                daily_sale__sale_date__lte=year_end,
            )
            .count()
        )

        if daily_reports != expected_days:

            errors.append(
                "Full-year DailySaleReport count "
                "does not equal 365."
            )

        profit_reports = (
            BatchProfitReport.objects
            .filter(
                slaughter_batch__slaughter_date__gte=year_start,
                slaughter_batch__slaughter_date__lte=year_end,
            )
            .count()
        )

        if profit_reports != batch_count:

            errors.append(
                "Full-year BatchProfitReport count "
                "does not match batch count."
            )

        # ----------------------------------------------------
        # STOCK
        # ----------------------------------------------------

        unfinished = (
            MeatStock.objects
            .filter(
                slaughter_batch__slaughter_date__gte=year_start,
                slaughter_batch__slaughter_date__lte=year_end,
                is_finished=False,
            )
            .count()
        )

        if unfinished != 0:

            errors.append(
                f"{unfinished} unfinished "
                "MeatStock records remain."
            )

        # ----------------------------------------------------
        # FINAL
        # ----------------------------------------------------

        if errors:

            message = "\n".join(
                f"- {error}"
                for error in errors
            )

            raise ValueError(
                "FULL YEAR VERIFICATION FAILED:\n"
                + message
            )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "============================================================"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "FULL YEAR VERIFICATION PASSED."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "All 12 months are complete and consistent."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "============================================================"
            )
        )

    # ========================================================
    # PERIOD SUMMARY
    # ========================================================

    def show_period_summary(self):

        start = self.period_start
        end = self.period_end

        days = (
            end - start
        ).days + 1

        daily_sales = (
            DailySale.objects
            .filter(
                sale_date__gte=start,
                sale_date__lte=end,
            )
            .count()
        )

        batches = (
            SlaughterBatch.objects
            .filter(
                slaughter_date__gte=start,
                slaughter_date__lte=end,
            )
            .count()
        )

        pigs = (
            Pig.objects
            .filter(
                purchase__purchase_date__gte=start,
                purchase__purchase_date__lte=end,
            )
            .count()
        )

        pig_sales = (
            PigSaleRecord.objects
            .filter(
                daily_sale__sale_date__gte=start,
                daily_sale__sale_date__lte=end,
            )
            .count()
        )

        food_sales = (
            FoodSaleRecord.objects
            .filter(
                daily_sale__sale_date__gte=start,
                daily_sale__sale_date__lte=end,
            )
            .count()
        )

        reports = (
            BatchProfitReport.objects
            .filter(
                slaughter_batch__slaughter_date__gte=start,
                slaughter_batch__slaughter_date__lte=end,
            )
            .count()
        )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "PERIOD SUMMARY"
            )
        )

        self.stdout.write(
            f"  Dates              : "
            f"{start} → {end}"
        )

        self.stdout.write(
            f"  Days               : {days}"
        )

        self.stdout.write(
            f"  Daily sales        : {daily_sales}"
        )

        self.stdout.write(
            f"  Batches            : {batches}"
        )

        self.stdout.write(
            f"  Pigs               : {pigs}"
        )

        self.stdout.write(
            f"  Pig sales          : {pig_sales}"
        )

        self.stdout.write(
            f"  Food sales         : {food_sales}"
        )

        self.stdout.write(
            f"  Profit reports     : {reports}"
        )

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    def show_final_summary(self):

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "\n============================================================\n"
                "                 FINAL DATABASE SUMMARY\n"
                "============================================================"
            )
        )

        self.stdout.write(
            f"Daily sales         : "
            f"{DailySale.objects.count()}"
        )

        self.stdout.write(
            f"Purchases           : "
            f"{Purchase.objects.count()}"
        )

        self.stdout.write(
            f"Pigs                : "
            f"{Pig.objects.count()}"
        )

        self.stdout.write(
            f"Batches             : "
            f"{SlaughterBatch.objects.count()}"
        )

        self.stdout.write(
            f"Pig sales           : "
            f"{PigSaleRecord.objects.count()}"
        )

        self.stdout.write(
            f"Food sales          : "
            f"{FoodSaleRecord.objects.count()}"
        )

        self.stdout.write(
            f"Daily reports       : "
            f"{DailySaleReport.objects.count()}"
        )

        self.stdout.write(
            f"Batch profit reports: "
            f"{BatchProfitReport.objects.count()}"
        )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "TEST DATA GENERATION COMPLETE."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "365 unique daily-sale dates verified."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "All batches finished."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "All pigs finished."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "All profit reports verified."
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "============================================================"
            )
        )