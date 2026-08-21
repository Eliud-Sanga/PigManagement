import os
from decimal import Decimal

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "pig_management.settings"
)

import django
django.setup()

from django.db import transaction

from apps.pigs.models import (
    MeatStock,
    DailySale,
)


def money(value):
    return f"TSh {Decimal(value or 0):,.2f}"


def finish_all_meat_stock():

    print()
    print("=" * 70)
    print("        KUMALIZA MEAT STOCK ZOTE")
    print("=" * 70)

    stocks = (
        MeatStock.objects
        .select_related("slaughter_batch")
        .order_by("slaughter_batch__id")
    )

    if not stocks.exists():
        print("Hakuna MeatStock iliyopo.")
        return []

    results = []

    with transaction.atomic():

        for stock in stocks:

            batch = stock.slaughter_batch

            old_remaining = stock.remaining_weight_kg

            # Tumia business logic iliyopo kwenye model yako.
            stock.mark_as_finished()

            stock.refresh_from_db()

            results.append({
                "batch": batch.batch_number,
                "initial": batch.total_meat_weight_kg,
                "before": old_remaining,
                "after": stock.remaining_weight_kg,
                "finished": stock.is_finished,
                "finished_date": stock.finished_date,
            })

            print(
                f"{batch.batch_number:<8}"
                f" Mwanzo: {batch.total_meat_weight_kg:>8} kg"
                f" | Kabla: {old_remaining:>8} kg"
                f" | Sasa: {stock.remaining_weight_kg:>8} kg"
                f" | {'FINISHED' if stock.is_finished else 'ERROR'}"
            )

    return results


def generate_reports():

    print()
    print("=" * 70)
    print("             KUTENGENEZA REPORTS")
    print("=" * 70)

    total_pig_income = Decimal("0.00")
    total_food_income = Decimal("0.00")
    report_count = 0

    daily_sales = (
        DailySale.objects
        .order_by("sale_date", "id")
    )

    for daily_sale in daily_sales:

        report = daily_sale.create_report()

        total_pig_income += report.total_pig_income
        total_food_income += report.total_food_income

        report_count += 1

        print(
            f"{daily_sale.sale_date}"
            f" | Nyama: {money(report.total_pig_income)}"
            f" | Chakula: {money(report.total_food_income)}"
            f" | Jumla: {money(report.total_income)}"
        )

    total_income = (
        total_pig_income +
        total_food_income
    )

    print()
    print("-" * 70)

    print(
        f"Reports zilizotengenezwa: {report_count}"
    )

    print(
        f"Mapato ya nyama:   {money(total_pig_income)}"
    )

    print(
        f"Mapato ya chakula: {money(total_food_income)}"
    )

    print(
        f"Mapato yote:       {money(total_income)}"
    )

    return {
        "reports": report_count,
        "pig_income": total_pig_income,
        "food_income": total_food_income,
        "total_income": total_income,
    }


def final_report(results, report):

    print()
    print("=" * 70)
    print("                  FINAL REPORT")
    print("=" * 70)

    total_initial = sum(
        (
            Decimal(str(row["initial"] or 0))
            for row in results
        ),
        Decimal("0.00")
    )

    total_before = sum(
        (
            Decimal(str(row["before"] or 0))
            for row in results
        ),
        Decimal("0.00")
    )

    print()
    print(f"Number of batches:             {len(results)}")
    print(
        f"Total initial meat:            "
        f"{total_initial:,.2f} kg"
    )
    print(
        f"Remaining before finishing:    "
        f"{total_before:,.2f} kg"
    )
    print(
        f"Remaining after finishing:     "
        f"0.00 kg"
    )

    print()
    print(
        f"Total meat income:             "
        f"{money(report['pig_income'])}"
    )

    print(
        f"Total food income:             "
        f"{money(report['food_income'])}"
    )

    print(
        f"Total income:                  "
        f"{money(report['total_income'])}"
    )

    print()
    print("STATUS YA BATCH ZOTE")
    print("-" * 70)

    for row in results:

        status = (
            "FINISHED"
            if row["finished"]
            else "HAIJAMALIZA"
        )

        print(
            f"{row['batch']:<8} "
            f"{status:<15} "
            f"Finished date: {row['finished_date']}"
        )

    print()
    print("=" * 70)
    print("        OPERATION IMEKAMILIKA")
    print("=" * 70)
    print()


def main():

    print()
    print("Pig Management System")
    print("Meat Stock Finalization")
    print()

    results = finish_all_meat_stock()

    report = generate_reports()

    final_report(
        results,
        report
    )


if __name__ == "__main__":
    main()
