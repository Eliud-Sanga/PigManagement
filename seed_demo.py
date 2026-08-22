import os
import django
import random

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "pig_management.settings"
)

django.setup()

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction

from apps.pigs.models import (
    Purchase,
    Pig,
    SlaughterBatch,
    MeatStock,
    DailySale,
    PigSaleRecord,
    FoodItem,
    FoodSaleRecord,
    DailySaleReport,
    BatchProfitReport,
)


random.seed(42)


@transaction.atomic
def create_demo_data():

    print("\n======================================")
    print("       PIG MANAGEMENT DEMO DATA")
    print("======================================\n")

    # ========================================================
    # FOOD ITEMS
    # ========================================================

    food_data = [
        ("Chips", Decimal("3000.00")),
        ("Ugali Nyama", Decimal("5000.00")),
        ("Wali", Decimal("3000.00")),
        ("Ndizi Nyama", Decimal("5000.00")),
        ("Chips Mayai", Decimal("5000.00")),
    ]

    food_items = []

    for name, price in food_data:

        item, _ = FoodItem.objects.update_or_create(
            name=name,
            defaults={
                "selling_price": price,
                "is_active": True,
            }
        )

        food_items.append(item)

    print(
        f"✓ Food items created/updated: "
        f"{len(food_items)}"
    )

    # ========================================================
    # PURCHASES
    # ========================================================

    suppliers = [
        "Juma",
        "Musa",
        "Amani",
        "John",
        "Baraka",
        "Hassan",
    ]

    purchase_start = date(2026, 6, 1)

    purchases = []
    all_pigs = []

    for i in range(9):

        purchase_date = (
            purchase_start +
            timedelta(days=i * 4)
        )

        number_of_pigs = random.randint(
            4,
            7
        )

        pig_prices = [
            Decimal(
                random.randint(
                    220000,
                    320000
                )
            )
            for _ in range(number_of_pigs)
        ]

        total_cost = sum(
            pig_prices,
            Decimal("0.00")
        )

        purchase = Purchase.objects.create(
            supplier_name=random.choice(
                suppliers
            ),
            supplier_phone="0712345678",
            supplier_location="Njombe",
            purchase_date=purchase_date,
            number_of_pigs=number_of_pigs,
            total_cost=total_cost,
            notes="DEMO DATA",
            pigs_created=True,
        )

        purchases.append(purchase)

        for price in pig_prices:

            pig = Pig.objects.create(
                purchase=purchase,
                gender=random.choice(
                    [
                        Pig.MALE,
                        Pig.FEMALE,
                    ]
                ),
                purchase_price=price,
                notes="DEMO DATA",
            )

            all_pigs.append(pig)

    print(
        f"✓ Purchases: "
        f"{len(purchases)}"
    )

    print(
        f"✓ Pigs: "
        f"{len(all_pigs)}"
    )

    # ========================================================
    # SELECT PIGS FOR SLAUGHTER
    # ========================================================

    slaughter_pigs = all_pigs.copy()

    random.shuffle(
        slaughter_pigs
    )

    slaughter_count = int(
        len(slaughter_pigs) * 0.75
    )

    slaughter_pigs = (
        slaughter_pigs[:slaughter_count]
    )

    print(
        f"✓ Pigs selected for slaughter: "
        f"{len(slaughter_pigs)}"
    )

    # ========================================================
    # SLAUGHTER BATCHES
    # ========================================================

    batches = []

    batch_start = date(2026, 6, 5)

    for i in range(
        0,
        len(slaughter_pigs),
        4
    ):

        selected_pigs = slaughter_pigs[
            i:i + 4
        ]

        if not selected_pigs:
            continue

        batch = SlaughterBatch.objects.create(
            slaughter_date=(
                batch_start +
                timedelta(days=len(batches) * 3)
            ),
            notes="DEMO DATA",
        )

        batch.add_pigs(
            selected_pigs
        )

        MeatStock.objects.create(
            slaughter_batch=batch
        )

        batches.append(batch)

    print(
        f"✓ Slaughter batches: "
        f"{len(batches)}"
    )

    # ========================================================
    # DAILY SALES
    # ========================================================

    sale_start = date(2026, 6, 6)

    meat_records_count = 0
    food_records_count = 0

    open_batches = list(
        batches
    )

    for day_number in range(60):

        sale_date = (
            sale_start +
            timedelta(days=day_number)
        )

        meat_amount = Decimal("0.00")
        food_amount = Decimal("0.00")
        selected_batch = None
        meat_price = Decimal("0.00")

        # ----------------------------------------------------
        # MEAT SALE
        # ----------------------------------------------------

        available_batches = [
            batch
            for batch in open_batches
            if not batch.is_finished()
        ]

        if (
            available_batches
            and random.random() < 0.70
        ):

            selected_batch = random.choice(
                available_batches
            )

            meat_price = random.choice(
                [
                    Decimal("12000.00"),
                    Decimal("13000.00"),
                    Decimal("14000.00"),
                    Decimal("15000.00"),
                ]
            )

            meat_amount = Decimal(
                random.randint(
                    80000,
                    350000
                )
            )

        # ----------------------------------------------------
        # FOOD SALE
        # ----------------------------------------------------

        selected_food = None

        if random.random() < 0.70:

            selected_food = random.choice(
                food_items
            )

            quantity = random.randint(
                2,
                15
            )

            food_record_price = (
                selected_food.selling_price
            )

            food_amount = (
                Decimal(quantity) *
                food_record_price
            )

        # ----------------------------------------------------
        # TOTAL DAILY MONEY
        # ----------------------------------------------------

        total_received = (
            meat_amount +
            food_amount
        )

        # ----------------------------------------------------
        # DAILY SALE
        # ----------------------------------------------------

        daily_sale = DailySale.objects.create(
            sale_date=sale_date,
            slaughter_batch=selected_batch,
            total_money_received=total_received,
            meat_price_per_kg=meat_price,
            notes="DEMO DATA",
        )

        # ----------------------------------------------------
        # PIG SALE RECORD
        # ----------------------------------------------------

        if (
            selected_batch
            and meat_amount > 0
        ):

            PigSaleRecord.objects.create(
                daily_sale=daily_sale,
                slaughter_batch=selected_batch,
                price_per_kg=meat_price,
                total_amount=meat_amount,
                notes="DEMO DATA",
            )

            meat_records_count += 1

        # ----------------------------------------------------
        # FOOD SALE RECORD
        # ----------------------------------------------------

        if selected_food:

            FoodSaleRecord.objects.create(
                daily_sale=daily_sale,
                food_item=selected_food,
                quantity=quantity,
                unit_price=(
                    selected_food.selling_price
                ),
            )

            food_records_count += 1

        # ----------------------------------------------------
        # CALCULATE + SAVE DAILY TOTALS
        # ----------------------------------------------------

        daily_sale.refresh_totals()

        # ----------------------------------------------------
        # DAILY REPORT
        # ----------------------------------------------------

        daily_sale.create_report()

    print(
        f"✓ Daily sales: "
        f"{DailySale.objects.count()}"
    )

    print(
        f"✓ Meat sale records: "
        f"{meat_records_count}"
    )

    print(
        f"✓ Food sale records: "
        f"{food_records_count}"
    )

    # ========================================================
    # FINISH SOME BATCHES
    # ========================================================

    batches_to_finish = batches[
        :max(1, len(batches) // 3)
    ]

    finished_count = 0

    for batch in batches_to_finish:

        try:

            batch.finish_batch()

            finished_count += 1

        except ValueError as error:

            print(
                f"! Could not finish "
                f"{batch.batch_number}: "
                f"{error}"
            )

    print(
        f"✓ Finished batches: "
        f"{finished_count}"
    )

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    print("\n======================================")
    print("       DEMO DATA COMPLETED")
    print("======================================")

    print(
        f"Purchases        : "
        f"{Purchase.objects.count()}"
    )

    print(
        f"Pigs             : "
        f"{Pig.objects.count()}"
    )

    print(
        f"Available pigs   : "
        f"{Pig.objects.filter(status=Pig.AVAILABLE).count()}"
    )

    print(
        f"Slaughtered pigs : "
        f"{Pig.objects.filter(status=Pig.SLAUGHTERED).count()}"
    )

    print(
        f"Finished pigs    : "
        f"{Pig.objects.filter(status=Pig.FINISHED).count()}"
    )

    print(
        f"Slaughter batches : "
        f"{SlaughterBatch.objects.count()}"
    )

    print(
        f"Meat stocks      : "
        f"{MeatStock.objects.count()}"
    )

    print(
        f"Daily sales      : "
        f"{DailySale.objects.count()}"
    )

    print(
        f"Meat sales       : "
        f"{PigSaleRecord.objects.count()}"
    )

    print(
        f"Food sales       : "
        f"{FoodSaleRecord.objects.count()}"
    )

    print(
        f"Daily reports    : "
        f"{DailySaleReport.objects.count()}"
    )

    print(
        f"Profit reports   : "
        f"{BatchProfitReport.objects.count()}"
    )

    print("\n✓ Demo data is ready.")
    print("✓ Some pigs remain AVAILABLE.")
    print("✓ Some pigs are SLAUGHTERED.")
    print("✓ Some pigs are FINISHED.")
    print("✓ Some batches remain open.")
    print("✓ Some batches are FINISHED.")
    print("✓ Daily sales contain meat and food.")
    print("✓ Reports are generated from model logic.\n")


if __name__ == "__main__":
    create_demo_data()