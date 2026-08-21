from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    DailySale,
    DailySaleReport,
    Pig,
    SlaughterBatch,
    MeatStock,
    BatchProfitReport,
    PigSaleRecord,
    Purchase,
    FoodItem,
    FoodSaleRecord,
)


# ============================================================
# CONSTANTS / HELPERS
# ============================================================

ZERO = Decimal("0.00")
TWO_PLACES = Decimal("0.01")


def money(value):
    """
    Convert value to Decimal and keep money
    at 2 decimal places.
    """

    return Decimal(str(value)).quantize(
        TWO_PLACES,
        rounding=ROUND_HALF_UP,
    )


def calculate_meat_weight(total_amount, price_per_kg):
    """
    Calculate meat weight from money received
    and price per kilogram.

    IMPORTANT:
        Kilo ni taarifa iliyohesabiwa kwa ajili ya
        records/reports.

        Kilo HAZITUMIKI kama stock control.

    Example:

        TSh 60,000
        price = TSh 12,000/kg

        60,000 / 12,000 = 5.00 kg
    """

    total_amount = money(total_amount)
    price_per_kg = money(price_per_kg)

    if price_per_kg <= ZERO:
        raise ValueError(
            "Bei kwa kilo lazima iwe zaidi ya sifuri."
        )

    return (
        total_amount / price_per_kg
    ).quantize(
        TWO_PLACES,
        rounding=ROUND_HALF_UP,
    )


# ============================================================
# PURCHASE SERVICES
# ============================================================

@transaction.atomic
def create_purchase(
    *,
    supplier_name,
    supplier_phone=None,
    supplier_location=None,
    purchase_date=None,
    number_of_pigs,
    total_cost,
    notes=None,
):
    """
    Tengeneza Purchase mpya.

    Hii service inahusika na ununuzi pekee.
    Nguruwe wanaweza kuundwa kupitia
    create_pigs_for_purchase().
    """

    if not supplier_name or not supplier_name.strip():
        raise ValueError(
            "Jina la muuzaji linahitajika."
        )

    if number_of_pigs < 1:
        raise ValueError(
            "Idadi ya nguruwe lazima iwe angalau mmoja."
        )

    total_cost = money(total_cost)

    if total_cost <= ZERO:
        raise ValueError(
            "Gharama ya ununuzi lazima iwe zaidi ya sifuri."
        )

    purchase = Purchase.objects.create(
        supplier_name=supplier_name.strip(),
        supplier_phone=(
            supplier_phone.strip()
            if supplier_phone
            else None
        ),
        supplier_location=(
            supplier_location.strip()
            if supplier_location
            else None
        ),
        purchase_date=(
            purchase_date
            or timezone.localdate()
        ),
        number_of_pigs=number_of_pigs,
        total_cost=total_cost,
        notes=notes,
    )

    return purchase


# ============================================================
# PURCHASE PRICE VALIDATION
# ============================================================

def validate_purchase_prices(purchase):
    """
    Hakikisha individual pig prices hazizidi
    gharama ya purchase nzima.

    Rule:

        Sum(Pig.purchase_price)
        <= Purchase.total_cost

    Baadhi ya pigs wanaweza kuwa hawajawekewa
    purchase_price bado.
    """

    prices_total = (
        purchase.pigs
        .exclude(purchase_price__isnull=True)
        .aggregate(
            total=Sum("purchase_price")
        )["total"]
        or ZERO
    )

    prices_total = money(prices_total)

    purchase_total = money(
        purchase.total_cost
    )

    if prices_total > purchase_total:
        raise ValueError(
            "Jumla ya bei za nguruwe haiwezi kuzidi "
            "gharama ya ununuzi."
        )

    return prices_total


# ============================================================
# PIG CREATION
# ============================================================

@transaction.atomic
def create_pigs_for_purchase(
    *,
    purchase,
    pigs_data,
):
    """
    Tengeneza pigs kwa Purchase moja.

    pigs_data mfano:

        [
            {
                "gender": Pig.MALE,
                "purchase_price": 250000,
                "notes": "",
            },
            ...
        ]

    Rule:
        Idadi ya pigs lazima ilingane na
        Purchase.number_of_pigs.
    """

    if purchase.pigs_created:
        raise ValueError(
            "Nguruwe wa purchase hii tayari wameundwa."
        )

    if not pigs_data:
        raise ValueError(
            "Hakuna taarifa za nguruwe zilizotumwa."
        )

    if len(pigs_data) != purchase.number_of_pigs:
        raise ValueError(
            "Idadi ya taarifa za nguruwe lazima ilingane "
            "na idadi ya nguruwe kwenye purchase."
        )

    prices_total = ZERO

    for data in pigs_data:

        price = data.get("purchase_price")

        if price is not None:

            price = money(price)

            if price <= ZERO:
                raise ValueError(
                    "Bei ya nguruwe lazima iwe zaidi ya sifuri."
                )

            prices_total += price

    prices_total = money(prices_total)

    if prices_total > money(purchase.total_cost):
        raise ValueError(
            "Jumla ya bei za nguruwe haiwezi kuzidi "
            "gharama ya purchase."
        )

    pigs = []

    for data in pigs_data:

        purchase_price = data.get(
            "purchase_price"
        )

        if purchase_price is not None:
            purchase_price = money(
                purchase_price
            )

        pig = Pig.objects.create(
            purchase=purchase,
            gender=data.get("gender"),
            purchase_price=purchase_price,
            notes=data.get("notes"),
        )

        pigs.append(pig)

    purchase.pigs_created = True

    purchase.save(
        update_fields=[
            "pigs_created",
            "updated_at",
        ]
    )

    return pigs


# ============================================================
# SLAUGHTER SERVICES
# ============================================================

@transaction.atomic
def create_slaughter_batch(
    *,
    pigs,
    slaughter_date=None,
    notes=None,
):
    """
    Tengeneza batch ya machinjio.

    Rules:
        - lazima kuwe na pig angalau mmoja
        - pig lazima awe AVAILABLE
        - pig status inakuwa SLAUGHTERED
        - MeatStock inatengenezwa
        - Kilo hazihitajiki hapa
    """

    pigs = list(pigs)

    if not pigs:
        raise ValueError(
            "Chagua angalau nguruwe mmoja."
        )

    pig_ids = [pig.pk for pig in pigs]

    locked_pigs = list(
        Pig.objects
        .select_for_update()
        .filter(pk__in=pig_ids)
    )

    if len(locked_pigs) != len(pigs):
        raise ValueError(
            "Baadhi ya nguruwe hawapatikani tena."
        )

    for pig in locked_pigs:

        if pig.status != Pig.AVAILABLE:
            raise ValueError(
                f"Nguruwe {pig.tag_number} "
                f"hawezi kuchinjwa."
            )

    batch = SlaughterBatch.objects.create(
        slaughter_date=(
            slaughter_date
            or timezone.localdate()
        ),
        notes=notes,
    )

    batch.pigs.add(*locked_pigs)

    Pig.objects.filter(
        pk__in=pig_ids
    ).update(
        status=Pig.SLAUGHTERED
    )

    MeatStock.objects.create(
        slaughter_batch=batch,
        is_finished=False,
        needs_confirmation=False,
    )

    return batch


# ============================================================
# ACTIVE BATCH
# ============================================================

def get_active_batch():
    """
    Rudisha batch moja ambayo bado haijafungwa.

    Business rule:
        Kwa sasa tunaruhusu batch moja active.
    """

    active_batches = list(
        SlaughterBatch.objects
        .filter(
            stock__is_finished=False
        )
        .order_by(
            "-slaughter_date",
            "-id",
        )
    )

    if not active_batches:
        return None

    if len(active_batches) > 1:
        raise ValueError(
            "Kuna batch zaidi ya moja ambazo bado "
            "hazijafungwa."
        )

    return active_batches[0]


# ============================================================
# DAILY SALE
# ============================================================

@transaction.atomic
def create_daily_sale(
    *,
    sale_date=None,
    total_money_received,
    notes=None,
):
    """
    Tengeneza DailySale.

    Mama anaingiza jumla ya fedha alizopokea.

    Food na meat totals zitahesabiwa na system.
    """

    sale_date = (
        sale_date
        or timezone.localdate()
    )

    total_money_received = money(
        total_money_received
    )

    if total_money_received <= ZERO:
        raise ValueError(
            "Jumla ya mauzo lazima iwe zaidi ya sifuri."
        )

    if DailySale.objects.filter(
        sale_date=sale_date
    ).exists():
        raise ValueError(
            "Tayari kuna mauzo ya tarehe hii."
        )

    sale = DailySale.objects.create(
        sale_date=sale_date,
        total_money_received=total_money_received,
        total_food_sales=ZERO,
        total_meat_sales=total_money_received,
        notes=notes,
    )

    refresh_daily_sale(
        daily_sale=sale
    )

    return sale


# ============================================================
# DAILY SALE RECALCULATION
# ============================================================

@transaction.atomic
def refresh_daily_sale(
    *,
    daily_sale,
):
    """
    Recalculate:

        Food total
        Meat total

    Formula:

        Meat =
        total_money_received - food sales
    """

    food_total = (
        FoodSaleRecord.objects
        .filter(
            daily_sale=daily_sale
        )
        .aggregate(
            total=Sum("total_price")
        )["total"]
        or ZERO
    )

    food_total = money(
        food_total
    )

    meat_total = money(
        daily_sale.total_money_received
        - food_total
    )

    if meat_total < ZERO:
        raise ValueError(
            "Jumla ya chakula haiwezi kuzidi "
            "jumla ya fedha za siku."
        )

    daily_sale.total_food_sales = food_total
    daily_sale.total_meat_sales = meat_total

    daily_sale.save(
        update_fields=[
            "total_food_sales",
            "total_meat_sales",
            "updated_at",
        ]
    )

    refresh_daily_report(
        daily_sale=daily_sale
    )

    return daily_sale


# ============================================================
# DAILY REPORT
# ============================================================

@transaction.atomic
def refresh_daily_report(
    *,
    daily_sale,
):
    """
    DailySaleReport iwe reflection ya DailySale.

    DailySale ndiyo source of truth.
    """

    report, created = (
        DailySaleReport.objects.update_or_create(
            daily_sale=daily_sale,
            defaults={
                "total_pig_income": (
                    daily_sale.total_meat_sales
                ),
                "total_food_income": (
                    daily_sale.total_food_sales
                ),
                "total_income": (
                    daily_sale.total_money_received
                ),
            },
        )
    )

    return report


# ============================================================
# MEAT SALE
# ============================================================

@transaction.atomic
def record_meat_sale(
    *,
    daily_sale,
    slaughter_batch,
    total_amount,
    price_per_kg,
    notes=None,
):
    """
    Rekodi sale ya nyama.

    Mama anaingiza:

        total_amount
        price_per_kg

    System inahesabu:

        meat_weight_sold =
        total_amount / price_per_kg

    IMPORTANT:

        Kilo zinahifadhiwa kwenye PigSaleRecord
        kwa ajili ya reports.

        Kilo HAZITUMIKI kama stock control.
    """

    total_amount = money(
        total_amount
    )

    price_per_kg = money(
        price_per_kg
    )

    if total_amount <= ZERO:
        raise ValueError(
            "Jumla ya mauzo lazima iwe zaidi ya sifuri."
        )

    if price_per_kg <= ZERO:
        raise ValueError(
            "Bei kwa kilo lazima iwe zaidi ya sifuri."
        )

    # --------------------------------------------------------
    # CALCULATE KG
    # --------------------------------------------------------

    meat_weight_sold = calculate_meat_weight(
        total_amount,
        price_per_kg,
    )

    # --------------------------------------------------------
    # LOCK BATCH
    # --------------------------------------------------------

    batch = (
        SlaughterBatch.objects
        .select_for_update()
        .get(
            pk=slaughter_batch.pk
        )
    )

    stock = (
        MeatStock.objects
        .select_for_update()
        .get(
            slaughter_batch=batch
        )
    )

    if stock.is_finished:
        raise ValueError(
            "Nyama ya batch hii imekwisha."
        )

    # --------------------------------------------------------
    # LOCK DAILY SALE
    # --------------------------------------------------------

    daily_sale = (
        DailySale.objects
        .select_for_update()
        .get(
            pk=daily_sale.pk
        )
    )

    # --------------------------------------------------------
    # EXISTING FOOD SALES
    # --------------------------------------------------------

    existing_food = (
        FoodSaleRecord.objects
        .filter(
            daily_sale=daily_sale
        )
        .aggregate(
            total=Sum("total_price")
        )["total"]
        or ZERO
    )

    # --------------------------------------------------------
    # EXISTING MEAT SALES
    # --------------------------------------------------------

    existing_meat = (
        PigSaleRecord.objects
        .filter(
            daily_sale=daily_sale
        )
        .aggregate(
            total=Sum("total_amount")
        )["total"]
        or ZERO
    )

    existing_food = money(
        existing_food
    )

    existing_meat = money(
        existing_meat
    )

    # --------------------------------------------------------
    # DAILY MONEY CHECK
    # --------------------------------------------------------

    if (
        existing_food
        + existing_meat
        + total_amount
        > daily_sale.total_money_received
    ):
        raise ValueError(
            "Jumla ya mauzo ya nyama na chakula "
            "imezidi fedha za mauzo ya siku."
        )

    # --------------------------------------------------------
    # CREATE MEAT SALE
    # --------------------------------------------------------

    sale = PigSaleRecord.objects.create(
        daily_sale=daily_sale,
        slaughter_batch=batch,
        meat_weight_sold=meat_weight_sold,
        price_per_kg=price_per_kg,
        total_amount=total_amount,
        notes=notes,
    )

    # --------------------------------------------------------
    # REFRESH DAILY TOTALS
    # --------------------------------------------------------

    refresh_daily_sale(
        daily_sale=daily_sale
    )

    return sale


# ============================================================
# EDIT MEAT SALE
# ============================================================

@transaction.atomic
def edit_meat_sale(
    *,
    sale,
    total_amount,
    price_per_kg,
    notes=None,
):
    """
    Edit existing meat sale.

    Hii ha-create sale mpya.

    Kilo zitahesabiwa upya baada ya
    total_amount au price_per_kg kubadilishwa.
    """

    sale = (
        PigSaleRecord.objects
        .select_for_update()
        .select_related(
            "daily_sale",
            "slaughter_batch",
        )
        .get(
            pk=sale.pk
        )
    )

    batch = (
        SlaughterBatch.objects
        .select_for_update()
        .get(
            pk=sale.slaughter_batch_id
        )
    )

    stock = (
        MeatStock.objects
        .select_for_update()
        .get(
            slaughter_batch=batch
        )
    )

    if stock.is_finished:
        raise ValueError(
            "Sale hii haiwezi kuhaririwa baada ya "
            "batch kufungwa."
        )

    total_amount = money(
        total_amount
    )

    price_per_kg = money(
        price_per_kg
    )

    if total_amount <= ZERO:
        raise ValueError(
            "Jumla ya mauzo lazima iwe zaidi ya sifuri."
        )

    if price_per_kg <= ZERO:
        raise ValueError(
            "Bei kwa kilo lazima iwe zaidi ya sifuri."
        )

    # --------------------------------------------------------
    # RECALCULATE KG
    # --------------------------------------------------------

    meat_weight_sold = calculate_meat_weight(
        total_amount,
        price_per_kg,
    )

    # --------------------------------------------------------
    # LOCK DAILY SALE
    # --------------------------------------------------------

    daily_sale = (
        DailySale.objects
        .select_for_update()
        .get(
            pk=sale.daily_sale_id
        )
    )

    # --------------------------------------------------------
    # FOOD TOTAL
    # --------------------------------------------------------

    food_total = (
        FoodSaleRecord.objects
        .filter(
            daily_sale=daily_sale
        )
        .aggregate(
            total=Sum("total_price")
        )["total"]
        or ZERO
    )

    # --------------------------------------------------------
    # OTHER MEAT SALES
    # --------------------------------------------------------

    other_meat_total = (
        PigSaleRecord.objects
        .filter(
            daily_sale=daily_sale
        )
        .exclude(
            pk=sale.pk
        )
        .aggregate(
            total=Sum("total_amount")
        )["total"]
        or ZERO
    )

    food_total = money(
        food_total
    )

    other_meat_total = money(
        other_meat_total
    )

    total_possible = (
        food_total
        + other_meat_total
        + total_amount
    )

    # --------------------------------------------------------
    # DAILY MONEY CHECK
    # --------------------------------------------------------

    if total_possible > daily_sale.total_money_received:
        raise ValueError(
            "Baada ya ku-edit, jumla ya mauzo "
            "itazidi fedha za siku."
        )

    # --------------------------------------------------------
    # UPDATE SALE
    # --------------------------------------------------------

    sale.total_amount = total_amount
    sale.price_per_kg = price_per_kg
    sale.meat_weight_sold = meat_weight_sold
    sale.notes = notes

    sale.save(
        update_fields=[
            "total_amount",
            "price_per_kg",
            "meat_weight_sold",
            "notes",
            "updated_at",
        ]
    )

    # --------------------------------------------------------
    # REFRESH DAILY TOTALS
    # --------------------------------------------------------

    refresh_daily_sale(
        daily_sale=daily_sale
    )

    return sale


# ============================================================
# FOOD SALE
# ============================================================

@transaction.atomic
def record_food_sale(
    *,
    daily_sale,
    food_name,
    quantity,
    unit_price,
):
    """
    Rekodi chakula.

    Mama anaweza kuweka bei tofauti kila siku.

    FoodItem selling_price ni catalog/default tu.
    Historical FoodSaleRecord.unit_price ndiyo
    bei halisi iliyotumika siku hiyo.
    """

    if not food_name or not food_name.strip():
        raise ValueError(
            "Weka aina ya chakula."
        )

    if quantity < 1:
        raise ValueError(
            "Idadi lazima iwe angalau moja."
        )

    unit_price = money(
        unit_price
    )

    if unit_price <= ZERO:
        raise ValueError(
            "Bei ya chakula lazima iwe zaidi ya sifuri."
        )

    daily_sale = (
        DailySale.objects
        .select_for_update()
        .get(
            pk=daily_sale.pk
        )
    )

    food_name = food_name.strip()

    # --------------------------------------------------------
    # GET OR CREATE FOOD ITEM
    # --------------------------------------------------------

    food_item, created = (
        FoodItem.objects.get_or_create(
            name=food_name,
            defaults={
                "selling_price": unit_price,
                "is_active": True,
            },
        )
    )

    # Update catalog price for future use.
    if not created:
        food_item.selling_price = unit_price
        food_item.is_active = True

        food_item.save(
            update_fields=[
                "selling_price",
                "is_active",
                "updated_at",
            ]
        )

    # --------------------------------------------------------
    # CREATE FOOD SALE RECORD
    # --------------------------------------------------------

    record = FoodSaleRecord.objects.create(
        daily_sale=daily_sale,
        food_item=food_item,
        quantity=quantity,
        unit_price=unit_price,
    )

    # --------------------------------------------------------
    # REFRESH DAILY TOTALS
    # --------------------------------------------------------

    refresh_daily_sale(
        daily_sale=daily_sale
    )

    return record


# ============================================================
# EDIT FOOD SALE
# ============================================================

@transaction.atomic
def edit_food_sale(
    *,
    sale,
    food_name,
    quantity,
    unit_price,
):
    """
    Edit existing food sale.

    Hii ha-create record mpya.
    """

    sale = (
        FoodSaleRecord.objects
        .select_for_update()
        .get(
            pk=sale.pk
        )
    )

    daily_sale = (
        DailySale.objects
        .select_for_update()
        .get(
            pk=sale.daily_sale_id
        )
    )

    if not food_name or not food_name.strip():
        raise ValueError(
            "Weka aina ya chakula."
        )

    if quantity < 1:
        raise ValueError(
            "Idadi lazima iwe angalau moja."
        )

    unit_price = money(
        unit_price
    )

    if unit_price <= ZERO:
        raise ValueError(
            "Bei ya chakula lazima iwe zaidi ya sifuri."
        )

    food_item, created = (
        FoodItem.objects.get_or_create(
            name=food_name.strip(),
            defaults={
                "selling_price": unit_price,
                "is_active": True,
            },
        )
    )

    if not created:
        food_item.selling_price = unit_price
        food_item.is_active = True

        food_item.save(
            update_fields=[
                "selling_price",
                "is_active",
                "updated_at",
            ]
        )

    sale.food_item = food_item
    sale.quantity = quantity
    sale.unit_price = unit_price

    sale.save(
        update_fields=[
            "food_item",
            "quantity",
            "unit_price",
            "updated_at",
        ]
    )

    # --------------------------------------------------------
    # REFRESH DAILY TOTALS
    # --------------------------------------------------------

    refresh_daily_sale(
        daily_sale=daily_sale
    )

    return sale


# ============================================================
# CONFIRM MEAT STATUS
# ============================================================

@transaction.atomic
def confirm_meat_status(
    *,
    slaughter_batch,
    meat_finished,
    confirmation_date=None,
):
    """
    ============================================================
    CONFIRM MEAT STATUS
    ============================================================

    Hii ndiyo function kuu inayoshughulikia confirmation
    ya hali ya nyama baada ya DailySale.

    meat_finished=False
        -> nyama bado ipo
        -> batch inaendelea
        -> needs_confirmation = False

    meat_finished=True
        -> nyama imeisha
        -> MeatStock inafungwa
        -> pigs zinawekwa FINISHED
        -> BatchProfitReport inatengenezwa / kusasishwa

    MUHIMU:

        Kilo hazitumiki kuamua kama nyama imeisha.

        Mama ndiye anayethibitisha hali halisi ya nyama.
    """

    # ========================================================
    # 1. LOCK BATCH
    # ========================================================

    batch = (
        SlaughterBatch.objects
        .select_for_update()
        .get(pk=slaughter_batch.pk)
    )

    # ========================================================
    # 2. GET + LOCK STOCK
    # ========================================================

    try:

        stock = (
            MeatStock.objects
            .select_for_update()
            .get(slaughter_batch=batch)
        )

    except MeatStock.DoesNotExist:

        raise ValueError(
            f"Batch {batch.batch_number} haina MeatStock."
        )

    # ========================================================
    # 3. CONFIRMATION DATE
    # ========================================================

    confirmation_date = (
        confirmation_date
        or timezone.localdate()
    )

    # ========================================================
    # 4. NYAMA BADO IPO
    # ========================================================

    if not meat_finished:

        # Batch ikiwa tayari imefungwa,
        # haiwezi kurudishwa kuwa available.

        if stock.is_finished:

            raise ValueError(
                f"Batch {batch.batch_number} tayari imefungwa."
            )

        stock.needs_confirmation = False

        stock.save(
            update_fields=[
                "needs_confirmation",
                "updated_at",
            ]
        )

        return {
            "status": "AVAILABLE",
            "batch": batch,
            "stock": stock,
        }

    # ========================================================
    # 5. NYAMA IMEISHA - BATCH ALREADY FINISHED
    # ========================================================

    if stock.is_finished:

        return finalize_batch_profit(
            slaughter_batch=batch,
            finalized_date=(
                stock.finished_date
                or confirmation_date
            ),
        )

    # ========================================================
    # 6. FINISH STOCK
    # ========================================================

    stock.is_finished = True
    stock.needs_confirmation = False
    stock.finished_date = confirmation_date

    stock.save(
        update_fields=[
            "is_finished",
            "needs_confirmation",
            "finished_date",
            "updated_at",
        ]
    )

    # ========================================================
    # 7. FINISH PIGS
    # ========================================================

    batch.pigs.update(
        status=Pig.FINISHED
    )

    # ========================================================
    # 8. CREATE / UPDATE BATCH PROFIT REPORT
    # ========================================================

    report = finalize_batch_profit(
        slaughter_batch=batch,
        finalized_date=confirmation_date,
    )

    return report


# ============================================================
# FINALIZE BATCH PROFIT
# ============================================================

@transaction.atomic
def finalize_batch_profit(
    *,
    slaughter_batch,
    finalized_date=None,
):
    """
    ============================================================
    FINAL PROFIT YA BATCH MOJA
    ============================================================

    SOURCE YA MAUZO:

        DailySale

    DailySale lazima iwe imeunganishwa na:

        slaughter_batch

    Profit:

        TOTAL MEAT SALES ZA BATCH
        -
        PURCHASE COST YA BATCH

    SOURCE YA MEAT SALES:

        DailySale.total_meat_sales

    SOURCE YA MEAT WEIGHT:

        DailySale.total_meat_weight_kg

    HATUTUMII:

        sale_date

    kutafuta sales za batch.

    Tunatumia:

        slaughter_batch_id
    """

    # ========================================================
    # 1. LOCK BATCH
    # ========================================================

    batch = (
        SlaughterBatch.objects
        .select_for_update()
        .get(pk=slaughter_batch.pk)
    )

    # ========================================================
    # 2. LOCK STOCK
    # ========================================================

    try:

        stock = (
            MeatStock.objects
            .select_for_update()
            .get(slaughter_batch=batch)
        )

    except MeatStock.DoesNotExist:

        raise ValueError(
            f"Batch {batch.batch_number} haina MeatStock."
        )

    # ========================================================
    # 3. HAKIKISHA NYAMA IMEISHA
    # ========================================================

    if not stock.is_finished:

        raise ValueError(
            "Profit haiwezi kutengenezwa kabla "
            "nyama ya batch haijaisha."
        )

    # ========================================================
    # 4. PURCHASE COST YA BATCH HII
    # ========================================================

    total_purchase_cost = (
        batch.calculate_total_purchase_cost()
    )

    # ========================================================
    # 5. GET DAILY SALES ZA BATCH HII TU
    # ========================================================

    daily_sales = (
        DailySale.objects
        .filter(
            slaughter_batch_id=batch.id
        )
        .order_by(
            "sale_date",
            "id"
        )
    )

    # ========================================================
    # 6. HESABU TOTAL MEAT SALES
    # ========================================================

    total_meat_sales = ZERO

    # ========================================================
    # 7. HESABU TOTAL MEAT WEIGHT
    # ========================================================

    total_meat_weight_kg = ZERO

    # ========================================================
    # 8. COUNT SALES
    # ========================================================

    sales_count = daily_sales.count()

    # ========================================================
    # 9. HAKUNA SALE
    # ========================================================

    if sales_count == 0:

        raise ValueError(
            f"Batch {batch.batch_number} imefungwa, "
            "lakini hakuna DailySale iliyounganishwa "
            "na batch hii. Profit haijatengenezwa."
        )

    # ========================================================
    # 10. LOOP SALES
    # ========================================================

    for sale in daily_sales:

        # ----------------------------------------------------
        # MEAT INCOME
        # ----------------------------------------------------

        meat_income = (
            sale.total_meat_sales
            or ZERO
        )

        # ----------------------------------------------------
        # PROTECT AGAINST NEGATIVE VALUE
        # ----------------------------------------------------

        if meat_income < ZERO:

            meat_income = ZERO

        # ----------------------------------------------------
        # ADD MEAT INCOME
        # ----------------------------------------------------

        total_meat_sales += meat_income

        # ----------------------------------------------------
        # MEAT WEIGHT
        # ----------------------------------------------------

        meat_weight = (
            sale.total_meat_weight_kg
            or ZERO
        )

        if meat_weight < ZERO:

            meat_weight = ZERO

        total_meat_weight_kg += meat_weight

    # ========================================================
    # 11. MONEY / WEIGHT FORMAT
    # ========================================================

    total_purchase_cost = money(
        total_purchase_cost
    )

    total_meat_sales = money(
        total_meat_sales
    )

    total_meat_weight_kg = (
        total_meat_weight_kg.quantize(
            Decimal("0.01")
        )
    )

    # ========================================================
    # 12. CALCULATE PROFIT
    # ========================================================

    total_profit = money(
        total_meat_sales
        - total_purchase_cost
    )

    # ========================================================
    # 13. FINALIZED DATE
    # ========================================================

    finalized_date = (
        finalized_date
        or stock.finished_date
        or timezone.localdate()
    )

    # ========================================================
    # 14. CREATE / UPDATE PROFIT REPORT
    # ========================================================

    report, _ = (
        BatchProfitReport.objects
        .update_or_create(
            slaughter_batch=batch,
            defaults={
                "total_pigs": batch.pigs.count(),

                "total_purchase_cost": (
                    total_purchase_cost
                ),

                "total_meat_sales": (
                    total_meat_sales
                ),

                "total_profit": (
                    total_profit
                ),

                "finalized_date": (
                    finalized_date
                ),
            }
        )
    )

    return report


# ============================================================
# FINALIZE MEAT FOR DAILY SALE
# ============================================================

@transaction.atomic
def finalize_meat_for_daily_sale(
    *,
    daily_sale,
    slaughter_batch=None,
):
    """
    Compatibility service inayotumiwa na view
    ya meat_finished_confirm.

    Mama akithibitisha:

        "NYAMA IMEISHA"

    Mfumo:

        1. Unapata active batch
        2. Unafunga MeatStock
        3. Pig status -> FINISHED
        4. Profit report inatengenezwa
        5. DailySale inarefreshiwa

    IMPORTANT:

        Kilo hazitumiki kuamua nyama imeisha.

        Kilo ni calculated information tu
        kwa reports.
    """

    # --------------------------------------------------------
    # GET ACTIVE BATCH
    # --------------------------------------------------------

    if slaughter_batch is None:

        slaughter_batch = get_active_batch()

    if slaughter_batch is None:
        raise ValueError(
            "Hakuna batch yenye nyama inayopatikana."
        )

    # --------------------------------------------------------
    # LOCK BATCH
    # --------------------------------------------------------

    batch = (
        SlaughterBatch.objects
        .select_for_update()
        .get(
            pk=slaughter_batch.pk
        )
    )

    # --------------------------------------------------------
    # GET STOCK
    # --------------------------------------------------------

    try:

        stock = (
            MeatStock.objects
            .select_for_update()
            .get(
                slaughter_batch=batch
            )
        )

    except MeatStock.DoesNotExist:

        raise ValueError(
            "Batch hii haina taarifa ya MeatStock."
        )

    # --------------------------------------------------------
    # ALREADY FINISHED
    # --------------------------------------------------------

    if stock.is_finished:

        return finalize_batch_profit(
            slaughter_batch=batch,
            finalized_date=(
                stock.finished_date
                or (
                    daily_sale.sale_date
                    if daily_sale
                    else timezone.localdate()
                )
            ),
        )

    # --------------------------------------------------------
    # FINISH BATCH
    # --------------------------------------------------------

    finish_date = (
        daily_sale.sale_date
        if daily_sale
        else timezone.localdate()
    )

    stock.is_finished = True
    stock.needs_confirmation = False
    stock.finished_date = finish_date

    stock.save(
        update_fields=[
            "is_finished",
            "needs_confirmation",
            "finished_date",
            "updated_at",
        ]
    )

    # --------------------------------------------------------
    # FINISH PIGS
    # --------------------------------------------------------

    batch.pigs.update(
        status=Pig.FINISHED
    )

    # --------------------------------------------------------
    # FINAL PROFIT REPORT
    # --------------------------------------------------------

    report = finalize_batch_profit(
        slaughter_batch=batch,
        finalized_date=finish_date,
    )

    # --------------------------------------------------------
    # REFRESH DAILY SALE
    # --------------------------------------------------------

    if daily_sale:

        refresh_daily_sale(
            daily_sale=daily_sale
        )

    return report