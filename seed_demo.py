import os
import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "pig_management.settings"
)

django.setup()

from datetime import date, timedelta
from decimal import Decimal
import random

from django.db import transaction

from apps.pigs.models import (
    Purchase,
    Pig,
    SlaughterBatch,
    MeatStock,
    DailySale,
    PigSaleRecord,
    MeatPartSale,
    FoodItem,
    FoodSaleRecord,
    MeatDistribution,
    DailySaleReport,
)


random.seed(42)


@transaction.atomic
def create_demo_data():

    print("\n======================================")
    print("   PIG MANAGEMENT DEMO DATA")
    print("======================================\n")

    # ----------------------------------
    # 1. MEAT DISTRIBUTION SETTINGS
    # ----------------------------------

    distribution, _ = MeatDistribution.objects.get_or_create(
        id=1,
        defaults={
            "ribs_percentage": Decimal("40"),
            "thighs_percentage": Decimal("50"),
            "head_percentage": Decimal("5"),
            "internal_organs_percentage": Decimal("5"),
        }
    )

    distribution.ribs_percentage = Decimal("40")
    distribution.thighs_percentage = Decimal("50")
    distribution.head_percentage = Decimal("5")
    distribution.internal_organs_percentage = Decimal("5")
    distribution.save()

    print("✓ Meat distribution settings ready")

    # ----------------------------------
    # 2. FOOD ITEMS
    # ----------------------------------

    food_data = [
        ("Chips", Decimal("3000")),
        ("Ugali Nyama", Decimal("5000")),
        ("Wali", Decimal("3000")),
        ("Ndizi Nyama", Decimal("5000")),
        ("Chips Mayai", Decimal("5000")),
    ]

    food_items = []

    for name, price in food_data:

        item, _ = FoodItem.objects.get_or_create(
            name=name,
            defaults={
                "selling_price": price,
                "is_active": True,
            }
        )

        item.selling_price = price
        item.is_active = True
        item.save()

        food_items.append(item)

    print(f"✓ Food items ready: {len(food_items)}")

    # ----------------------------------
    # 3. PURCHASES
    # ----------------------------------

    suppliers = [
        "Juma",
        "Musa",
        "Amani",
        "John",
        "Baraka",
        "Hassan",
    ]

    start_date = date(2026, 6, 1)

    purchases = []

    for month in range(3):

        purchase_date = (
            start_date +
            timedelta(days=month * 30)
        )

        for i in range(3):

            number_of_pigs = random.randint(4, 7)

            total_cost = (
                number_of_pigs *
                random.randint(220000, 320000)
            )

            purchase = Purchase.objects.create(
                supplier_name=random.choice(suppliers),
                supplier_phone="0712345678",
                supplier_location="Njombe",
                purchase_date=purchase_date + timedelta(days=i * 5),
                number_of_pigs=number_of_pigs,
                total_cost=total_cost,
                notes="DEMO DATA",
                pigs_created=True,
            )

            purchases.append(purchase)

            for _ in range(number_of_pigs):

                Pig.objects.create(
                    purchase=purchase,
                    gender=random.choice(
                        ["MALE", "FEMALE"]
                    ),
                    purchase_price=Decimal(
                        random.randint(220000, 320000)
                    ),
                    weight_kg=Decimal(
                        random.randint(70, 120)
                    ),
                    notes="DEMO DATA",
                )

    print(f"✓ Purchases created: {len(purchases)}")

    # ----------------------------------
    # 4. SELECT PIGS FOR SLAUGHTER
    # ----------------------------------

    pigs = list(
        Pig.objects.filter(
            status="AVAILABLE",
            notes="DEMO DATA",
        ).order_by("id")
    )

    # Leave approximately 25% alive
    random.shuffle(pigs)

    slaughter_count = int(
        len(pigs) * Decimal("0.75")
    )

    pigs_for_slaughter = pigs[:slaughter_count]

    remaining_pigs = pigs[slaughter_count:]

    print(
        f"✓ Pigs available for slaughter: "
        f"{len(pigs_for_slaughter)}"
    )

    print(
        f"✓ Pigs intentionally left un-slaughtered: "
        f"{len(remaining_pigs)}"
    )

    # ----------------------------------
    # 5. SLAUGHTER BATCHES
    # ----------------------------------

    batches = []

    batch_date = start_date

    for i in range(0, len(pigs_for_slaughter), 4):

        selected = pigs_for_slaughter[i:i + 4]

        if not selected:
            break

        batch = SlaughterBatch.objects.create(
            slaughter_date=batch_date,
            total_meat_weight_kg=Decimal(
                random.randint(120, 220)
            ),
            notes="DEMO DATA",
        )

        batch.add_pigs(*selected)

        MeatStock.objects.create(
            slaughter_batch=batch,
            initial_weight_kg=batch.total_meat_weight_kg,
            remaining_weight_kg=batch.total_meat_weight_kg,
        )

        batches.append(batch)

        batch_date += timedelta(days=2)

    print(
        f"✓ Slaughter batches created: "
        f"{len(batches)}"
    )

    # ----------------------------------
    # 6. DAILY SALES
    # ----------------------------------

    sale_start = date(2026, 6, 3)

    total_meat_sales = 0
    total_food_sales = 0

    for day_number in range(85):

        sale_date = (
            sale_start +
            timedelta(days=day_number)
        )

        daily_sale = DailySale.objects.create(
            sale_date=sale_date,
            notes="DEMO DATA",
        )

        # ------------------------------
        # MEAT SALES
        # ------------------------------

        if batches:

            # 55% chance of meat sale
            if random.random() < 0.55:

                available_batches = []

                for batch in batches:

                    stock = batch.stock

                    if (
                        not stock.is_finished
                        and
                        stock.remaining_weight_kg > 0
                    ):
                        available_batches.append(batch)

                if available_batches:

                    batch = random.choice(
                        available_batches
                    )

                    stock = batch.stock

                    # Sell approximately 5-15% of stock
                    requested_weight = (
                        stock.remaining_weight_kg *
                        Decimal(
                            random.randint(5, 15)
                        ) /
                        Decimal("100")
                    )

                    if requested_weight > 0:

                        # Since MeatPartSale currently
                        # uses integer quantities,
                        # sell one part when possible.
                        pig_count = batch.pigs.count()

                        maximum_parts = pig_count * 2

                        ribs = random.randint(
                            0,
                            min(1, maximum_parts)
                        )

                        thighs = random.randint(
                            0,
                            min(1, maximum_parts)
                        )

                        head_sold = random.random() < 0.25

                        organs_sold = random.random() < 0.25

                        sale_record = PigSaleRecord.objects.create(
                            daily_sale=daily_sale,
                            slaughter_batch=batch,
                            price_per_kg=Decimal(
                                random.choice(
                                    [
                                        12000,
                                        13000,
                                        14000,
                                        15000,
                                    ]
                                )
                            ),
                            meat_weight_sold=Decimal("0"),
                            notes="DEMO DATA",
                        )

                        meat_parts = MeatPartSale.objects.create(
                            pig_sale_record=sale_record,
                            ribs=ribs,
                            thighs=thighs,
                            head_sold=head_sold,
                            internal_organs_sold=organs_sold,
                        )

                        try:

                            sold_weight = (
                                meat_parts.apply_sale()
                            )

                            meat_parts.save()

                            total_meat_sales += 1

                        except ValueError:

                            # If selected parts would exceed
                            # available stock, remove the
                            # unsuccessful demo record.
                            meat_parts.delete()
                            sale_record.delete()

        # ------------------------------
        # FOOD SALES
        # ------------------------------

        # 65% chance of food sales
        if random.random() < 0.65:

            item = random.choice(
                food_items
            )

            quantity = random.randint(
                2,
                15
            )

            FoodSaleRecord.objects.create(
                daily_sale=daily_sale,
                food_item=item,
                quantity=quantity,
            )

            total_food_sales += 1

        # ------------------------------
        # GENERATE DAILY REPORT
        # ------------------------------

        daily_sale.create_report()

    print(
        f"✓ Meat sale records created: "
        f"{total_meat_sales}"
    )

    print(
        f"✓ Food sale records created: "
        f"{total_food_sales}"
    )

    # ----------------------------------
    # 7. FINAL SUMMARY
    # ----------------------------------

    print("\n======================================")
    print("       DEMO DATA COMPLETED")
    print("======================================")

    print(
        f"Purchases       : {Purchase.objects.count()}"
    )

    print(
        f"Pigs            : {Pig.objects.count()}"
    )

    print(
        f"Available pigs  : "
        f"{Pig.objects.filter(status='AVAILABLE').count()}"
    )

    print(
        f"Slaughtered pigs: "
        f"{Pig.objects.filter(status='SLAUGHTERED').count()}"
    )

    print(
        f"Finished pigs   : "
        f"{Pig.objects.filter(status='FINISHED').count()}"
    )

    print(
        f"Batches         : "
        f"{SlaughterBatch.objects.count()}"
    )

    print(
        f"Meat stocks     : "
        f"{MeatStock.objects.count()}"
    )

    print(
        f"Daily sales     : "
        f"{DailySale.objects.count()}"
    )

    print(
        f"Meat sales      : "
        f"{PigSaleRecord.objects.count()}"
    )

    print(
        f"Food sales      : "
        f"{FoodSaleRecord.objects.count()}"
    )

    print(
        f"Daily reports   : "
        f"{DailySaleReport.objects.count()}"
    )

    print("\n✓ Some pigs were intentionally left AVAILABLE.")
    print("✓ Demo covers June, July and August 2026.")
    print("✓ Data is ready for browser testing.\n")


if __name__ == "__main__":

    import os
    import django

    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        "pig_management.settings"
    )

    django.setup()

    create_demo_data()
