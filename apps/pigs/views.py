from decimal import Decimal, InvalidOperation
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db import IntegrityError

from apps.users.utils import create_audit_log

from .forms import (
    PurchaseForm,
    SlaughterBatchForm,
    DailySaleForm,
    FoodItemForm,
    FoodSaleRecordForm,
    PigSaleRecordForm,
    PigForm,
)

from .models import (
    Purchase,
    Pig,
    SlaughterBatch,
    MeatStock,
    DailySale,
    DailySaleReport,
    FoodItem,
    FoodSaleRecord,
    PigSaleRecord,
    BatchProfitReport,
    Expense,
)

from .services import (
    record_meat_sale,
    finalize_meat_for_daily_sale,
    edit_meat_sale,
    edit_food_sale,
)

ZERO = Decimal("0")


# ============================================================
# BUSINESS DEFAULTS
# ============================================================

DEFAULT_RAW_MEAT_PRICE = Decimal("13000")
DEFAULT_COOKED_MEAT_PRICE = Decimal("14000")

DEFAULT_AVERAGE_MEAT_PRICE = (
    DEFAULT_RAW_MEAT_PRICE +
    DEFAULT_COOKED_MEAT_PRICE
) / Decimal("2")


# ============================================================
# DASHBOARD
# ============================================================

@login_required
def dashboard(request):
    """Dashboard kuu ya mfumo"""

    ZERO = Decimal("0.00")

    # ============================================================
    # TAREHE YA LEO
    # ============================================================

    today = timezone.localdate()
    month_name = today.strftime("%B %Y")

    # ============================================================
    # NGURUWE
    # ============================================================

    total_pigs = Pig.objects.count()

    available_pigs = Pig.objects.filter(
        status=Pig.AVAILABLE
    ).count()

    slaughtered_pigs = Pig.objects.filter(
        status=Pig.SLAUGHTERED
    ).count()

    finished_pigs = Pig.objects.filter(
        status=Pig.FINISHED
    ).count()

    # ============================================================
    # UNUNUZI
    # ============================================================

    total_purchases = Purchase.objects.count()

    # ============================================================
    # MAUZO
    # ============================================================

    total_daily_sales = DailySale.objects.count()

    total_food_income = (
        FoodSaleRecord.objects
        .aggregate(
            total=Sum("total_price")
        )["total"]
        or ZERO
    )

    total_business_income = (
        DailySale.objects
        .aggregate(
            total=Sum("total_money_received")
        )["total"]
        or ZERO
    )

    total_meat_income = (
        total_business_income - total_food_income
    )

    if total_meat_income < ZERO:
        total_meat_income = ZERO

    # ============================================================
    # MEAT STOCK ACTIVE
    # ============================================================

    active_meat_stock = (
        MeatStock.objects
        .filter(
            is_finished=False
        )
        .count()
    )

    # ============================================================
    # MAUZO YA LEO
    # ============================================================

    today_sale = (
        DailySale.objects
        .filter(
            sale_date=today
        )
        .first()
    )

    today_income = (
        today_sale.total_money_received
        if today_sale
        else ZERO
    )

    # ============================================================
    # PROFIT YA MWEZI
    # ============================================================

    monthly_profit = (
        BatchProfitReport.objects
        .filter(
            finalized_date__year=today.year,
            finalized_date__month=today.month
        )
        .aggregate(
            total=Sum("total_profit")
        )["total"]
        or ZERO
    )

    # ============================================================
    # MATUMIZI YA MWEZI HUU
    # ============================================================

    monthly_expenses = (
        Expense.objects
        .filter(
            expense_date__year=today.year,
            expense_date__month=today.month
        )
        .aggregate(
            total=Sum("amount")
        )["total"]
        or ZERO
    )

    # ============================================================
    # MATUMIZI YOTE
    # ============================================================

    total_expenses = (
        Expense.objects
        .aggregate(
            total=Sum("amount")
        )["total"]
        or ZERO
    )

    # ============================================================
    # MAUZO YA HIVI KARIBUNI - 2 TU
    #
    # Ya tatu ikiongezeka:
    # - ya zamani zaidi haitatokea dashboard
    # - lakini data yenyewe haifutwi database
    # ============================================================

    recent_sales = (
        DailySale.objects
        .order_by(
            "-created_at",
            "-id"
        )[:2]
    )

    # ============================================================
    # MATUMIZI YA HIVI KARIBUNI - 2 TU
    #
    # Ya tatu ikiongezeka:
    # - ya zamani zaidi haitatokea dashboard
    # - lakini data yenyewe haifutwi database
    # ============================================================

    recent_expenses = (
        Expense.objects
        .order_by(
            "-created_at",
            "-id"
        )[:2]
    )

    # ============================================================
    # ACTIVE MEAT BATCHES
    # ============================================================

    active_batches = (
        MeatStock.objects
        .filter(
            is_finished=False
        )
        .select_related(
            "slaughter_batch"
        )
        .order_by(
            "-created_at"
        )[:5]
    )

    # ============================================================
    # CONTEXT
    # ============================================================

    context = {

        # --------------------------------------------------------
        # NGURUWE
        # --------------------------------------------------------

        "total_pigs": total_pigs,
        "available_pigs": available_pigs,
        "slaughtered_pigs": slaughtered_pigs,
        "finished_pigs": finished_pigs,

        # --------------------------------------------------------
        # UNUNUZI
        # --------------------------------------------------------

        "total_purchases": total_purchases,

        # --------------------------------------------------------
        # MAUZO
        # --------------------------------------------------------

        "total_daily_sales": total_daily_sales,
        "total_meat_income": total_meat_income,
        "total_food_income": total_food_income,
        "total_business_income": total_business_income,
        "today_income": today_income,

        # --------------------------------------------------------
        # MEAT STOCK
        # --------------------------------------------------------

        "active_meat_stock": active_meat_stock,
        "active_batches": active_batches,

        # --------------------------------------------------------
        # PROFIT
        # --------------------------------------------------------

        "monthly_profit": monthly_profit,

        # --------------------------------------------------------
        # EXPENSES
        # --------------------------------------------------------

        "monthly_expenses": monthly_expenses,
        "total_expenses": total_expenses,

        # --------------------------------------------------------
        # RECENT SALES
        # --------------------------------------------------------

        "recent_sales": recent_sales,

        # --------------------------------------------------------
        # RECENT EXPENSES
        # --------------------------------------------------------

        "recent_expenses": recent_expenses,

        # --------------------------------------------------------
        # DATE / TIME
        # --------------------------------------------------------

        "month_name": month_name,
        "today": today,
        "now": timezone.now(),
    }

    return render(
        request,
        "pigs/dashboard.html",
        context
    )


# ============================================================
# PURCHASE
# ============================================================

@login_required
def purchase_create(request):
    """Kununua nguruwe mpya"""

    # ========================================================
    # PERMISSION: ADD PURCHASE
    # ========================================================

    if not request.user.has_perm(
        "pigs.add_purchase"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuongeza manunuzi."
        )
        return redirect(
            "pigs:dashboard"
        )

    if request.method == "POST":
        form = PurchaseForm(request.POST)

        if form.is_valid():
            with transaction.atomic():
                purchase = form.save()

                create_audit_log(
                    request,
                    "CREATE",
                    "Purchase",
                    purchase.id,
                    f"Aliongeza manunuzi ya nguruwe kutoka {purchase.supplier_name}",
                )

                # Hesabu tag number ya mwisho
                last_pig = (
                    Pig.objects
                    .filter(tag_number__startswith="P")
                    .order_by("-id")
                    .first()
                )

                if last_pig and last_pig.tag_number:
                    try:
                        last_number = int(
                            last_pig.tag_number[1:]
                        )
                    except (ValueError, TypeError):
                        last_number = 0
                else:
                    last_number = 0

                # Unda nguruwe kwa idadi iliyowekwa
                pigs = []
                for i in range(
                    purchase.number_of_pigs
                ):
                    next_number = (
                        last_number + i + 1
                    )
                    tag_number = (
                        f"P{next_number:03d}"
                    )

                    pigs.append(
                        Pig(
                            purchase=purchase,
                            tag_number=tag_number,
                            gender=None,
                            purchase_price=None,
                            notes="",
                        )
                    )

                Pig.objects.bulk_create(
                    pigs
                )

                purchase.pigs_created = True

                purchase.save(
                    update_fields=[
                        "pigs_created",
                        "updated_at"
                    ]
                )

            messages.success(
                request,
                f"✅ Nguruwe {purchase.number_of_pigs} "
                f"zimenunuliwa! "
                f"Sasa jaza taarifa zao."
            )

            return redirect(
                "pigs:pig_bulk_edit",
                purchase_id=purchase.id
            )

    else:
        form = PurchaseForm()

    return render(
        request,
        "pigs/purchase_form.html",
        {
            "form": form
        }
    )


@login_required
def purchase_list(request):
    """Historia ya ununuzi"""

    # ========================================================
    # PERMISSION: VIEW PURCHASE
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_purchase"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona manunuzi."
        )

        return redirect(
            "pigs:dashboard"
        )

    purchases = (
        Purchase.objects
        .prefetch_related("pigs")
        .order_by(
            "-purchase_date",
            "-id"
        )
    )

    return render(
        request,
        "pigs/purchase_list.html",
        {
            "purchases": purchases
        }
    )


# ============================================================
# PIGS
# ============================================================

@login_required
def pig_list(request):
    """Orodha ya nguruwe zote"""

    # ========================================================
    # PERMISSION: VIEW PIG
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_pig"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona taarifa za nguruwe."
        )

        return redirect(
            "pigs:dashboard"
        )

    purchases = (
        Purchase.objects
        .prefetch_related("pigs")
        .order_by(
            "-purchase_date",
            "-id"
        )
    )

    return render(
        request,
        "pigs/pig_list.html",
        {
            "purchases": purchases
        }
    )


@login_required
def pig_detail(request, pig_id):
    """Edit taarifa za nguruwe mmoja"""

    # ========================================================
    # PERMISSION: CHANGE PIG
    # ========================================================

    if not request.user.has_perm(
        "pigs.change_pig"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuhariri taarifa za nguruwe."
        )

        return redirect(
            "pigs:pig_list"
        )

    pig = get_object_or_404(
        Pig,
        id=pig_id
    )

    if pig.status in [
        Pig.SLAUGHTERED,
        Pig.FINISHED
    ]:

        messages.warning(
            request,
            f"Nguruwe {pig.tag_number} "
            f"imechinjwa tayari. "
            "Huwezi kubadilisha taarifa zake."
        )

        return redirect(
            "pigs:pig_list"
        )

    if request.method == "POST":

        form = PigForm(
            request.POST,
            instance=pig
        )

        if form.is_valid():

            form.save()

            messages.success(
                request,
                f"✅ Taarifa za "
                f"{pig.tag_number} "
                f"zimebadilishwa!"
            )

            return redirect(
                "pigs:pig_list"
            )

    else:

        form = PigForm(
            instance=pig
        )

    return render(
        request,
        "pigs/pig_detail.html",
        {
            "pig": pig,
            "form": form
        }
    )


@login_required
def pig_bulk_edit(request, purchase_id):
    """Edit taarifa za nguruwe zote za ununuzi mmoja."""

    # ========================================================
    # PERMISSION: CHANGE PIG
    # ========================================================

    if not request.user.has_perm(
        "pigs.change_pig"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuhariri taarifa za nguruwe."
        )

        return redirect(
            "pigs:dashboard"
        )

    purchase = get_object_or_404(
        Purchase,
        id=purchase_id
    )

    # Nguruwe wote wa purchase hii
    all_pigs = (
        purchase.pigs
        .all()
        .order_by("tag_number")
    )

    # Nguruwe ambao bado wanaweza kuhaririwa
    pigs = all_pigs.filter(
        status=Pig.AVAILABLE
    )

    # Nguruwe ambao tayari wamechinjwa
    slaughtered_pigs = all_pigs.exclude(
        status=Pig.AVAILABLE
    )

    if slaughtered_pigs.exists():

        messages.warning(
            request,
            f"Baadhi ya nguruwe "
            f"({slaughtered_pigs.count()}) "
            "zimechinjwa tayari. "
            "Hazitaonekana kwenye orodha ya kuhaririwa."
        )

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        try:

            # ------------------------------------------------
            # 1. TOTAL YA BEI ZA NGURUWE AMBAO HAWAJA HARIRIWA
            # ------------------------------------------------

            locked_total = Decimal("0.00")

            for pig in slaughtered_pigs:

                if pig.purchase_price is not None:

                    locked_total += (
                        pig.purchase_price
                    )

            # ------------------------------------------------
            # 2. CHUKUA BEI ZILIZOWEKWA
            # ------------------------------------------------

            entered_prices = {}
            entered_total = Decimal("0.00")
            blank_pigs = []

            for pig in pigs:

                price_value = request.POST.get(
                    f"purchase_price_{pig.id}",
                    ""
                ).strip()

                if price_value == "":

                    blank_pigs.append(
                        pig
                    )

                    continue

                try:

                    price = Decimal(
                        price_value
                    )

                except (
                    InvalidOperation,
                    TypeError,
                    ValueError
                ):

                    messages.error(
                        request,
                        f"❌ Bei ya {pig.tag_number} "
                        "si sahihi."
                    )

                    return render(
                        request,
                        "pigs/pig_bulk_edit.html",
                        {
                            "purchase": purchase,
                            "pigs": pigs,
                            "error": True,
                        }
                    )

                # Bei haiwezi kuwa negative au zero
                if price <= Decimal("0.00"):

                    messages.error(
                        request,
                        f"❌ Bei ya {pig.tag_number} "
                        "lazima iwe zaidi ya sifuri."
                    )

                    return render(
                        request,
                        "pigs/pig_bulk_edit.html",
                        {
                            "purchase": purchase,
                            "pigs": pigs,
                            "error": True,
                        }
                    )

                entered_prices[pig.id] = price

                entered_total += price

            # ------------------------------------------------
            # 3. JUMLA YA BEI ISIZIDI TOTAL YA PURCHASE
            # ------------------------------------------------

            total_cost = Decimal(
                purchase.total_cost
            )

            current_total = (
                locked_total
                + entered_total
            )

            if current_total > total_cost:

                messages.error(
                    request,
                    "❌ Bei ulizoingiza zimezidi "
                    "gharama jumla ya ununuzi. "
                    "Tafadhali rekebisha bei za "
                    "nguruwe au gharama jumla ya ununuzi."
                )

                return render(
                    request,
                    "pigs/pig_bulk_edit.html",
                    {
                        "purchase": purchase,
                        "pigs": pigs,
                        "error": True,
                    }
                )

            # ------------------------------------------------
            # 4. FEDHA ILIYOSALIA
            # ------------------------------------------------

            remaining_amount = (
                total_cost
                - locked_total
                - entered_total
            )

            # ------------------------------------------------
            # 5. KAMA KUNA NGURUWE WANA BEI TUPU
            # ------------------------------------------------

            if blank_pigs:

                number_of_blank_pigs = len(
                    blank_pigs
                )

                # Gawanya fedha iliyobaki
                # kwa nguruwe ambao hawana bei.
                base_price = (
                    remaining_amount
                    / number_of_blank_pigs
                ).quantize(
                    Decimal("0.01")
                )

                # ------------------------------------------------
                # FIX ROUNDING
                # ------------------------------------------------

                for index, pig in enumerate(
                    blank_pigs
                ):

                    if index == (
                        number_of_blank_pigs - 1
                    ):

                        # Nguruwe wa mwisho anachukua
                        # balance yote ili total ilingane
                        assigned_price = (
                            remaining_amount
                            - (
                                base_price
                                * (
                                    number_of_blank_pigs
                                    - 1
                                )
                            )
                        )

                    else:

                        assigned_price = base_price

                    entered_prices[
                        pig.id
                    ] = assigned_price

            # ------------------------------------------------
            # 6. KAMA HAKUNA NGURUWE TUPU
            # ------------------------------------------------

            else:

                # Nguruwe wote wamepewa bei.
                # Lazima total ilingane kabisa.

                final_total = (
                    locked_total
                    + entered_total
                )

                if final_total != total_cost:

                    messages.error(
                        request,
                        "❌ Jumla ya bei za nguruwe "
                        f"ni TSh {final_total:,.2f}, "
                        f"lakini gharama ya ununuzi ni "
                        f"TSh {total_cost:,.2f}. "
                        "Tafadhali rekebisha bei za "
                        "nguruwe au gharama jumla ya ununuzi."
                    )

                    return render(
                        request,
                        "pigs/pig_bulk_edit.html",
                        {
                            "purchase": purchase,
                            "pigs": pigs,
                            "error": True,
                        }
                    )

            # ------------------------------------------------
            # 7. SAVE
            # ------------------------------------------------

            with transaction.atomic():

                updated_count = 0

                for pig in pigs:

                    gender = request.POST.get(
                        f"gender_{pig.id}",
                        ""
                    ).strip()

                    # Bei iliyohesabiwa au iliyoingizwa
                    purchase_price = (
                        entered_prices.get(
                            pig.id
                        )
                    )

                    if gender:

                        pig.gender = gender

                    if purchase_price is not None:

                        pig.purchase_price = (
                            purchase_price
                        )

                    pig.save(
                        update_fields=[
                            "gender",
                            "purchase_price",
                            "updated_at",
                        ]
                    )

                    updated_count += 1

            messages.success(
                request,
                f"✅ Taarifa za nguruwe "
                f"{updated_count} "
                "zimehifadhiwa. "
                "Bei zimehakikisha zinaendana "
                "na gharama jumla ya ununuzi."
            )

            return redirect(
                "pigs:pig_list"
            )

        except Exception as e:

            messages.error(
                request,
                f"❌ Kumetokea tatizo wakati "
                f"wa kuhifadhi taarifa: {e}"
            )

            return render(
                request,
                "pigs/pig_bulk_edit.html",
                {
                    "purchase": purchase,
                    "pigs": pigs,
                    "error": True,
                }
            )

    # ========================================================
    # GET
    # ========================================================

    return render(
        request,
        "pigs/pig_bulk_edit.html",
        {
            "purchase": purchase,
            "pigs": pigs,
        }
    )


# ============================================================
# SLAUGHTER
# ============================================================

@login_required
def slaughter_create(request):
    """Kuchinja nguruwe"""

    # ========================================================
    # PERMISSION: ADD SLAUGHTER BATCH
    # ========================================================

    if not request.user.has_perm(
        "pigs.add_slaughterbatch"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuanza batch ya machinjio."
        )

        return redirect(
            "pigs:dashboard"
        )

    if request.method == "POST":

        form = SlaughterBatchForm(
            request.POST
        )

        if form.is_valid():

            selected_pigs = list(
                form.cleaned_data["pigs"]
            )

            with transaction.atomic():

                batch = form.save(
                    commit=False
                )

                batch.save()

                batch.add_pigs(
                    selected_pigs
                )

                form.save_m2m()

                MeatStock.objects.get_or_create(
                    slaughter_batch=batch
                )

            messages.success(
                request,
                "✅ Batch ya machinjio "
                "imehifadhiwa."
            )

            return redirect(
                "pigs:slaughter_list"
            )

    else:

        form = SlaughterBatchForm()

    available_pigs = (
        Pig.objects
        .filter(
            status=Pig.AVAILABLE
        )
        .select_related(
            "purchase"
        )
        .order_by(
            "id"
        )
    )

    return render(
        request,
        "pigs/slaughter_form.html",
        {
            "form": form,
            "available_pigs": available_pigs
        }
    )


@login_required
def slaughter_list(request):
    """Orodha ya machinjio"""

    # ========================================================
    # PERMISSION: VIEW SLAUGHTER BATCH
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_slaughterbatch"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona taarifa za machinjio."
        )

        return redirect(
            "pigs:dashboard"
        )

    batches = (
        SlaughterBatch.objects
        .prefetch_related(
            "pigs"
        )
        .order_by(
            "-slaughter_date",
            "-id"
        )
    )

    return render(
        request,
        "pigs/slaughter_list.html",
        {
            "batches": batches
        }
    )


# ============================================================
# MEAT STOCK
# ============================================================

@login_required
def meat_list(request):
    """Orodha ya nyama"""

    # ========================================================
    # PERMISSION: VIEW MEAT STOCK
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_meatstock"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona stock ya nyama."
        )

        return redirect(
            "pigs:dashboard"
        )

    meat_stocks = (
        MeatStock.objects
        .select_related(
            "slaughter_batch"
        )
        .prefetch_related(
            "slaughter_batch__pigs"
        )
        .order_by(
            "-id"
        )
    )

    return render(
        request,
        "pigs/meat_list.html",
        {
            "meat_stocks": meat_stocks
        }
    )


# ============================================================
# DAILY SALE CREATE
# ============================================================

@login_required
def daily_sale_create(request):
    """
    Unda mauzo ya siku.

    MUHIMU:

        DailySale HAIHIFADHIWI wakati form
        inajazwa.

    Mfumo:

        1. Validate mauzo
        2. Pata active batch
        3. Hifadhi data temporary kwenye session
        4. Fungua Meat Status
        5. Mama achague:
              - BADO IPO
              - NYAMA IMEISHA
        6. NDIPO DailySale ina-save kwenye database

    Pia:

        pending sale inahifadhi active_batch_id
        ili sale ijulikane ilitoka batch gani.
    """

    # ========================================================
    # PERMISSION: ADD DAILY SALE
    # ========================================================

    if not request.user.has_perm(
        "pigs.add_dailysale"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kurekodi mauzo ya siku."
        )

        return redirect(
            "pigs:dashboard"
        )

    ZERO = Decimal("0.00")

    food_total = ZERO

    # ========================================================
    # EDIT PENDING SALE
    # ========================================================

    edit_pending = (
        request.GET.get("edit") == "pending"
    )

    pending_data = request.session.get(
        "pending_daily_sale"
    )

    # ========================================================
    # GET - EDIT PENDING
    # ========================================================

    if request.method == "GET":

        if edit_pending and pending_data:

            try:

                sale_date = pending_data.get(
                    "sale_date"
                )

                total_money_received = (
                    pending_data.get(
                        "total_money_received",
                        "0.00"
                    )
                )

                notes = pending_data.get(
                    "notes",
                    ""
                )

                food_enabled = pending_data.get(
                    "food_enabled",
                    False
                )

                food_rows = pending_data.get(
                    "food_rows",
                    []
                )

                initial_data = {

                    "sale_date": sale_date,

                    "total_money_received": (
                        total_money_received
                    ),

                    "notes": notes,

                }

                form = DailySaleForm(
                    initial=initial_data
                )

                total_preview = Decimal(
                    str(
                        total_money_received
                        or "0.00"
                    )
                )

                food_total = ZERO

                for row in food_rows:

                    try:

                        unit_price = Decimal(
                            str(
                                row.get(
                                    "unit_price",
                                    "0"
                                )
                            )
                        )

                        quantity = int(
                            row.get(
                                "quantity",
                                0
                            )
                        )

                        if (
                            row.get("name")
                            and unit_price > ZERO
                            and quantity > 0
                        ):

                            food_total += (
                                unit_price
                                * Decimal(quantity)
                            )

                    except (
                        InvalidOperation,
                        TypeError,
                        ValueError,
                    ):

                        continue

                meat_preview = (
                    total_preview
                    - food_total
                )

                if meat_preview < ZERO:

                    meat_preview = ZERO

                return render(
                    request,
                    "pigs/daily_sale_form.html",
                    {
                        "form": form,
                        "food_rows": food_rows,
                        "food_enabled": food_enabled,
                        "food_total_preview": food_total,
                        "meat_total_preview": meat_preview,
                        "daily_total_preview": total_preview,
                        "editing_pending": True,
                    }
                )

            except Exception:

                request.session.pop(
                    "pending_daily_sale",
                    None
                )

                request.session.modified = True

                messages.warning(
                    request,
                    "Taarifa za mauzo ya muda "
                    "zilikuwa hazisomeki. "
                    "Tafadhali anza tena."
                )

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        form = DailySaleForm(
            request.POST
        )

        food_enabled = (
            request.POST.get(
                "food_enabled",
                "no"
            ) == "yes"
        )

        food_names = request.POST.getlist(
            "food_name[]"
        )

        food_prices = request.POST.getlist(
            "unit_price[]"
        )

        food_quantities = request.POST.getlist(
            "quantity[]"
        )

        food_rows = []

        valid_food_rows = []

        food_total = ZERO

        row_count = max(
            len(food_names),
            len(food_prices),
            len(food_quantities),
            1
        )

        # ====================================================
        # VALIDATE FOOD ROWS
        # ====================================================

        for index in range(row_count):

            name = (
                food_names[index].strip()
                if index < len(food_names)
                else ""
            )

            price_raw = (
                food_prices[index].strip()
                if index < len(food_prices)
                else ""
            )

            quantity_raw = (
                food_quantities[index].strip()
                if index < len(food_quantities)
                else ""
            )

            row = {

                "name": name,

                "unit_price": price_raw,

                "quantity": quantity_raw,

                "error": "",

            }

            # ------------------------------------------------
            # EMPTY ROW
            # ------------------------------------------------

            if (
                not name
                and not price_raw
                and not quantity_raw
            ):

                food_rows.append(
                    row
                )

                continue

            # ------------------------------------------------
            # INCOMPLETE ROW
            # ------------------------------------------------

            if (
                not name
                or not price_raw
                or not quantity_raw
            ):

                row["error"] = (
                    "Jaza aina ya chakula, "
                    "bei na idadi."
                )

                food_rows.append(
                    row
                )

                continue

            # ------------------------------------------------
            # PRICE
            # ------------------------------------------------

            try:

                unit_price = Decimal(
                    price_raw
                )

            except (
                InvalidOperation,
                TypeError,
                ValueError,
            ):

                row["error"] = (
                    "Bei ya chakula si sahihi."
                )

                food_rows.append(
                    row
                )

                continue

            if unit_price <= ZERO:

                row["error"] = (
                    "Bei lazima iwe zaidi ya sifuri."
                )

                food_rows.append(
                    row
                )

                continue

            # ------------------------------------------------
            # QUANTITY
            # ------------------------------------------------

            try:

                quantity = int(
                    quantity_raw
                )

            except (
                TypeError,
                ValueError,
            ):

                row["error"] = (
                    "Idadi lazima iwe namba kamili."
                )

                food_rows.append(
                    row
                )

                continue

            if quantity < 1:

                row["error"] = (
                    "Idadi lazima iwe angalau 1."
                )

                food_rows.append(
                    row
                )

                continue

            # ------------------------------------------------
            # ROW TOTAL
            # ------------------------------------------------

            row_total = (
                unit_price
                * Decimal(quantity)
            )

            food_total += row_total

            valid_food_rows.append({

                "name": name,

                "unit_price": str(
                    unit_price
                ),

                "quantity": quantity,

            })

            food_rows.append(
                row
            )

        # ====================================================
        # FOOD VALIDATION
        # ====================================================

        if food_enabled:

            if not valid_food_rows:

                form.add_error(
                    None,
                    "Umechagua NDIO kwa chakula, "
                    "lakini hujaweka chakula."
                )

            invalid_rows = [
                row
                for row in food_rows
                if row["error"]
            ]

            if invalid_rows:

                form.add_error(
                    None,
                    "Kuna taarifa za chakula "
                    "zisizo sahihi."
                )

        else:

            valid_food_rows = []

            food_total = ZERO

            for row in food_rows:

                row["error"] = ""

        # ====================================================
        # FORM VALIDATION
        # ====================================================

        if form.is_valid():

            total_money_received = (
                form.cleaned_data[
                    "total_money_received"
                ]
            )

            if food_total > total_money_received:

                form.add_error(
                    None,
                    "Jumla ya chakula haiwezi "
                    "kuzidi jumla ya fedha za siku."
                )

        # ====================================================
        # GET ACTIVE BATCH
        # ====================================================

        active_batch = None

        if form.is_valid():

            try:

                from .services import (
                    get_active_batch
                )

                active_batch = (
                    get_active_batch()
                )

            except Exception:

                active_batch = None

            if active_batch is None:

                form.add_error(
                    None,
                    "❌ Hakuna batch active "
                    "yenye nyama. "
                    "Tafadhali chinja nguruwe kwanza."
                )

        # ====================================================
        # SAVE TEMPORARY DATA ONLY
        # ====================================================

        if (
            form.is_valid()
            and active_batch
        ):

            try:

                sale_date = (
                    form.cleaned_data[
                        "sale_date"
                    ]
                )

                total_money_received = (
                    form.cleaned_data[
                        "total_money_received"
                    ]
                )

                notes = (
                    form.cleaned_data.get(
                        "notes"
                    )
                    or ""
                )

                # ============================================
                # HAKUNA DATABASE SAVE HAPA
                # ============================================

                pending_data = {

                    # -------------------------------
                    # SALE DATA
                    # -------------------------------

                    "sale_date": (
                        sale_date.isoformat()
                    ),

                    "total_money_received": str(
                        total_money_received
                    ),

                    "notes": notes,

                    # -------------------------------
                    # BATCH YA SALE
                    # -------------------------------

                    "active_batch_id": (
                        active_batch.id
                    ),

                    "active_batch_number": (
                        active_batch.batch_number
                    ),

                    # -------------------------------
                    # FOOD
                    # -------------------------------

                    "food_enabled": (
                        food_enabled
                    ),

                    "food_rows": (
                        valid_food_rows
                    ),

                    "food_total": str(
                        food_total
                    ),

                }

                # ==========================================
                # SAVE TEMPORARILY IN SESSION
                # ==========================================

                request.session[
                    "pending_daily_sale"
                ] = pending_data

                request.session.modified = True

                messages.info(
                    request,
                    "✅ Mauzo yameandaliwa. "
                    "Thibitisha hali ya nyama "
                    "ili yahifadhiwe."
                )

                # ==========================================
                # GO TO MEAT STATUS
                #
                # MUHIMU:
                # Hakuna sale_id kwa sababu DailySale
                # bado haija-save.
                # ==========================================

                return redirect(
                    "pigs:meat_status"
                )

            except Exception as error:

                form.add_error(
                    None,
                    "Taarifa za mauzo "
                    "hazikuweza kuhifadhiwa "
                    f"kwa muda. {error}"
                )

    # ========================================================
    # GET DEFAULT
    # ========================================================

    else:

        form = DailySaleForm()

        food_enabled = False

        food_rows = [

            {
                "name": "",
                "unit_price": "",
                "quantity": "",
                "error": "",
            }

        ]

    # ========================================================
    # PREVIEW
    # ========================================================

    if request.method == "POST":

        total_raw = request.POST.get(
            "total_money_received",
            "0"
        )

        try:

            total_preview = Decimal(
                total_raw or "0"
            )

        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ):

            total_preview = ZERO

    else:

        total_preview = ZERO

    meat_preview = (
        total_preview
        - food_total
    )

    if meat_preview < ZERO:

        meat_preview = ZERO

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        "pigs/daily_sale_form.html",
        {
            "form": form,
            "food_rows": food_rows,
            "food_enabled": food_enabled,
            "food_total_preview": food_total,
            "meat_total_preview": meat_preview,
            "daily_total_preview": total_preview,
            "editing_pending": False,
        }
    )


@login_required
def daily_sale_detail(request, sale_id):
    """Maelezo ya mauzo ya siku"""

    # ========================================================
    # PERMISSION: VIEW DAILY SALE
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_dailysale"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona mauzo ya siku."
        )

        return redirect(
            "pigs:dashboard"
        )

    daily_sale = get_object_or_404(
        DailySale,
        id=sale_id
    )

    food_records = (
        daily_sale.food_records
        .select_related("food_item")
        .order_by("created_at")
    )

    pig_records = (
        daily_sale.pig_records
        .select_related(
            "slaughter_batch",
            "slaughter_batch__stock"
        )
        .order_by("created_at")
    )

    total_food = (
        food_records.aggregate(
            total=Sum("total_price")
        )["total"]
        or Decimal("0.00")
    )

    total_meat = (
        daily_sale.total_money_received
        - total_food
    )

    if total_meat < Decimal("0.00"):
        total_meat = Decimal("0.00")

    if (
        daily_sale.total_food_sales != total_food
        or daily_sale.total_meat_sales != total_meat
    ):
        daily_sale.total_food_sales = total_food
        daily_sale.total_meat_sales = total_meat

        daily_sale.save(
            update_fields=[
                "total_food_sales",
                "total_meat_sales",
                "updated_at"
            ]
        )

    total_day = daily_sale.total_money_received

    active_batches = (
        SlaughterBatch.objects
        .filter(
            stock__is_finished=False
        )
        .select_related("stock")
        .prefetch_related("pigs")
        .order_by(
            "-slaughter_date",
            "-id"
        )
    )

    context = {
        "daily_sale": daily_sale,
        "food_records": food_records,
        "pig_records": pig_records,
        "total_food": total_food,
        "total_meat": total_meat,
        "total_day": total_day,
        "has_food_sales": food_records.exists(),
        "active_batches": active_batches,
        "now": timezone.now(),
    }

    return render(
        request,
        "pigs/daily_sale_detail.html",
        context
    )


# ============================================================
# MEAT FINISHED CONFIRMATION
# ============================================================

@login_required
def meat_finished_confirm(request, sale_id):
    """Kuthibitisha kuwa nyama imeisha"""

    # ========================================================
    # PERMISSION: CHANGE MEAT STOCK
    # ========================================================

    if not request.user.has_perm(
        "pigs.change_meatstock"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kubadilisha hali ya stock ya nyama."
        )

        return redirect(
            "pigs:dashboard"
        )

    daily_sale = get_object_or_404(
        DailySale,
        id=sale_id
    )

    if request.method != "POST":

        return redirect(
            "pigs:daily_sale_detail",
            sale_id=daily_sale.id
        )

    try:

        report = finalize_meat_for_daily_sale(
            daily_sale=daily_sale
        )

    except ValueError as error:

        messages.error(
            request,
            str(error)
        )

    else:

        messages.success(
            request,
            "✅ Nyama imefungwa kikamilifu. "
            "Batch imekamilika na ripoti ya profit "
            "imetengenezwa."
        )

    return redirect(
        "pigs:daily_sale_detail",
        sale_id=daily_sale.id
    )


# ============================================================
# FOOD SALE
# ============================================================

@login_required
def food_sale_create(request, sale_id):
    """Kuongeza mauzo ya chakula"""

    # ========================================================
    # PERMISSION: ADD FOOD SALE
    # ========================================================

    if not request.user.has_perm(
        "pigs.add_foodsalerecord"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuongeza mauzo ya chakula."
        )

        return redirect(
            "pigs:dashboard"
        )

    # ========================================================
    # FOOD SALE CREATES / UPDATES FOOD ITEM AUTOMATICALLY
    # ========================================================

    if not request.user.has_perm(
        "pigs.add_fooditem"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kusimamia aina za chakula."
        )

        return redirect(
            "pigs:dashboard"
        )

    daily_sale = get_object_or_404(
        DailySale,
        id=sale_id
    )

    if request.method == "POST":

        form = FoodSaleRecordForm(
            request.POST
        )

        if form.is_valid():

            food_name = (
                form.cleaned_data[
                    "food_name"
                ].strip()
            )

            unit_price = (
                form.cleaned_data[
                    "unit_price_input"
                ]
            )

            quantity = (
                form.cleaned_data[
                    "quantity"
                ]
            )

            with transaction.atomic():

                food_item, created = (
                    FoodItem.objects.get_or_create(
                        name=food_name,
                        defaults={
                            "selling_price": unit_price,
                            "is_active": True,
                        }
                    )
                )

                if not created:

                    food_item.selling_price = (
                        unit_price
                    )

                    food_item.is_active = True

                    food_item.save(
                        update_fields=[
                            "selling_price",
                            "is_active",
                            "updated_at"
                        ]
                    )

                FoodSaleRecord.objects.create(
                    daily_sale=daily_sale,
                    food_item=food_item,
                    quantity=quantity,
                    unit_price=unit_price,
                )

                food_total = (
                    daily_sale.food_records
                    .aggregate(
                        total=Sum(
                            "total_price"
                        )
                    )["total"]
                    or Decimal("0.00")
                )

                meat_total = (
                    daily_sale.total_money_received
                    - food_total
                )

                if meat_total < Decimal("0.00"):

                    raise ValueError(
                        "Jumla ya chakula haiwezi "
                        "kuzidi jumla ya mauzo ya siku."
                    )

                daily_sale.total_food_sales = (
                    food_total
                )

                daily_sale.total_meat_sales = (
                    meat_total
                )

                daily_sale.save(
                    update_fields=[
                        "total_food_sales",
                        "total_meat_sales",
                        "updated_at"
                    ]
                )

            messages.success(
                request,
                "✅ Mauzo ya chakula "
                "yamehifadhiwa kikamilifu."
            )

            return redirect(
                "pigs:daily_sale_detail",
                sale_id=daily_sale.id
            )

    else:

        form = FoodSaleRecordForm()

    return render(
        request,
        "pigs/food_sale_form.html",
        {
            "form": form,
            "daily_sale": daily_sale
        }
    )



# ============================================================
# FOOD SALE EDIT
# ============================================================

@login_required
def food_sale_edit(request, record_id):
    """Kuhariri mauzo ya chakula."""

    if not request.user.has_perm(
        "pigs.change_foodsalerecord"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuhariri mauzo ya chakula."
        )

        return redirect(
            "pigs:dashboard"
        )

    record = get_object_or_404(
        FoodSaleRecord,
        id=record_id
    )

    if request.method == "POST":

        form = FoodSaleRecordForm(
            request.POST
        )

        if form.is_valid():

            try:

                edit_food_sale(
                    sale=record,
                    food_name=form.cleaned_data[
                        "food_name"
                    ],
                    quantity=form.cleaned_data[
                        "quantity"
                    ],
                    unit_price=form.cleaned_data[
                        "unit_price_input"
                    ],
                )

            except ValueError as error:

                form.add_error(
                    None,
                    str(error)
                )

            else:

                messages.success(
                    request,
                    "✅ Mauzo ya chakula "
                    "yamehaririwa kikamilifu."
                )

                return redirect(
                    "pigs:daily_sale_detail",
                    sale_id=record.daily_sale_id
                )

    else:

        form = FoodSaleRecordForm(
            initial={
                "food_name": record.food_item.name,
                "unit_price_input": record.unit_price,
                "quantity": record.quantity,
            }
        )

    return render(
        request,
        "pigs/edit_food_sale.html",
        {
            "form": form,
            "record": record,
        }
    )


# ============================================================
# PIG / MEAT SALE
# ============================================================

@login_required
def pig_sale_create(request, sale_id):
    """Kuongeza mauzo ya nyama"""

    # ========================================================
    # PERMISSION: ADD PIG / MEAT SALE
    # ========================================================

    if not request.user.has_perm(
        "pigs.add_pigsalerecord"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuongeza mauzo ya nyama."
        )

        return redirect(
            "pigs:dashboard"
        )

    daily_sale = get_object_or_404(
        DailySale,
        id=sale_id
    )

    has_meat = (
        MeatStock.objects
        .filter(
            is_finished=False
        )
        .exists()
    )

    if not has_meat:

        messages.error(
            request,
            "❌ Hakuna nyama iliyopo kwenye stock. "
            "Tafadhali chinja nguruwe kwanza "
            "kabla ya kuuza nyama."
        )

        return redirect(
            "pigs:daily_sale_detail",
            sale_id=daily_sale.id
        )

    if request.method == "POST":

        form = PigSaleRecordForm(
            request.POST
        )

        if form.is_valid():

            batch = (
                form.cleaned_data[
                    "slaughter_batch"
                ]
            )

            meat_weight = (
                form.cleaned_data[
                    "meat_weight_sold"
                ]
            )

            price_per_kg = (
                form.cleaned_data[
                    "price_per_kg"
                ]
            )

            notes = form.cleaned_data.get(
                "notes"
            )

            try:

                record_meat_sale(
                    daily_sale=daily_sale,
                    slaughter_batch=batch,
                    meat_weight_sold=meat_weight,
                    price_per_kg=price_per_kg,
                    notes=notes,
                )

            except ValueError as error:

                form.add_error(
                    None,
                    str(error)
                )

            else:

                messages.success(
                    request,
                    "✅ Mauzo ya nyama "
                    "yamehifadhiwa."
                )

                return redirect(
                    "pigs:daily_sale_detail",
                    sale_id=daily_sale.id
                )

    else:

        form = PigSaleRecordForm()

    food_records = (
        daily_sale.food_records
        .select_related(
            "food_item"
        )
        .order_by(
            "created_at"
        )
    )

    pig_records = (
        daily_sale.pig_records
        .select_related(
            "slaughter_batch",
            "slaughter_batch__stock"
        )
        .order_by(
            "created_at"
        )
    )

    context = {

        "daily_sale": daily_sale,

        "food_records": food_records,

        "pig_records": pig_records,

        "food_form": FoodSaleRecordForm(),

        "pig_form": form,

        "has_food_sales": (
            food_records.exists()
        ),

        "now": timezone.now(),

    }

    return render(
        request,
        "pigs/daily_sale_detail.html",
        context
    )



# ============================================================
# MEAT SALE EDIT
# ============================================================

@login_required
def meat_sale_edit(request, record_id):
    """Kuhariri mauzo ya nyama."""

    if not request.user.has_perm(
        "pigs.change_pigsalerecord"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuhariri mauzo ya nyama."
        )

        return redirect(
            "pigs:dashboard"
        )

    record = get_object_or_404(
        PigSaleRecord,
        id=record_id
    )

    if request.method == "POST":

        form = PigSaleRecordForm(
            request.POST
        )

        if form.is_valid():

            try:

                edit_meat_sale(
                    sale=record,
                    total_amount=form.cleaned_data[
                        "total_amount"
                    ],
                    price_per_kg=form.cleaned_data[
                        "price_per_kg"
                    ],
                    notes=form.cleaned_data.get(
                        "notes"
                    ),
                )

            except ValueError as error:

                form.add_error(
                    None,
                    str(error)
                )

            else:

                messages.success(
                    request,
                    "✅ Mauzo ya nyama "
                    "yamehaririwa kikamilifu."
                )

                return redirect(
                    "pigs:daily_sale_detail",
                    sale_id=record.daily_sale_id
                )

    else:

        form = PigSaleRecordForm(
            instance=record
        )

    return render(
        request,
        "pigs/edit_meat_sale.html",
        {
            "form": form,
            "record": record,
        }
    )


# ============================================================
# FOOD ITEM
# ============================================================

@login_required
def food_item_create(request):
    """Kuongeza aina mpya ya chakula"""

    # ========================================================
    # PERMISSION: ADD FOOD ITEM
    # ========================================================

    if not request.user.has_perm(
        "pigs.add_fooditem"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuongeza aina za chakula."
        )

        return redirect(
            "pigs:dashboard"
        )

    if request.method == "POST":

        form = FoodItemForm(
            request.POST
        )

        if form.is_valid():

            form.save()

            messages.success(
                request,
                "✅ Chakula kimehifadhiwa."
            )

            return redirect(
                "pigs:dashboard"
            )

    else:

        form = FoodItemForm()

    return render(
        request,
        "pigs/food_item_form.html",
        {
            "form": form
        }
    )


# ============================================================
# DAILY REPORT
# ============================================================

@login_required
def daily_report(request, sale_id):
    """Ripoti ya mauzo ya siku"""

    if not request.user.has_perm(
        "pigs.view_dailysalereport"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona daily reports."
        )

        return redirect(
            "pigs:dashboard"
        )

    daily_sale = get_object_or_404(
        DailySale,
        id=sale_id
    )

    try:
        report = daily_sale.report

    except DailySaleReport.DoesNotExist:
        report = daily_sale.create_report()

    food_total = (
        daily_sale.food_records
        .aggregate(
            total=Sum("total_price")
        )["total"]
        or Decimal("0.00")
    )

    meat_total = (
        daily_sale.total_money_received
        - food_total
    )

    if meat_total < Decimal("0.00"):
        meat_total = Decimal("0.00")

    total_meat_weight_kg = (
        daily_sale.pig_records
        .aggregate(
            total=Sum("meat_weight_sold")
        )["total"]
        or Decimal("0.00")
    )

    total_income = (
        meat_total
        + food_total
    )

    return render(
        request,
        "pigs/daily_report.html",
        {
            "daily_sale": daily_sale,
            "report": report,
            "total_food_income": food_total,
            "total_pig_income": meat_total,
            "total_meat_weight_kg": total_meat_weight_kg,
            "total_income": total_income,
            "now": timezone.now(),
        }
    )


# ============================================================
# DAILY SALES LIST
# ============================================================

@login_required
def daily_sale_list(request):
    """Historia ya mauzo yote"""

    # ========================================================
    # PERMISSION: VIEW DAILY SALE
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_dailysale"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona historia ya mauzo."
        )

        return redirect(
            "pigs:dashboard"
        )

    sales = (
        DailySale.objects
        .order_by(
            "-sale_date",
            "-id"
        )
    )

    return render(
        request,
        "pigs/daily_sale_list.html",
        {
            "sales": sales,
            "now": timezone.now(),
        }
    )


# ============================================================
# EXPENSES / MATUMIZI
# ============================================================

@login_required
def expense_list(request):
    """
    Orodha ya kumbukumbu zote za matumizi.

    Expenses hizi ni kumbukumbu huru.
    Hazihusiki na:
        - DailySale
        - PigSaleRecord
        - FoodSaleRecord
        - BatchProfitReport
        - Profit calculations
    """

    # ========================================================
    # PERMISSION: VIEW EXPENSE
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_expense"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona matumizi."
        )

        return redirect(
            "pigs:dashboard"
        )

    expenses = (
        Expense.objects
        .all()
        .order_by(
            "-expense_date",
            "-created_at"
        )
    )

    total_expenses = (
        expenses.aggregate(
            total=Sum("amount")
        )["total"] or 0
    )

    return render(
        request,
        "pigs/expense_list.html",
        {
            "expenses": expenses,
            "total_expenses": total_expenses,
        }
    )


@login_required
def expense_create(request):
    """
    Tengeneza au hariri rekodi ya matumizi.

    Expenses hizi ni kumbukumbu huru.
    Hazihusiki na calculation yoyote ya biashara.
    """

    edit_id = request.GET.get(
        "edit"
    )

    expense = None

    # ========================================================
    # EDIT EXISTING EXPENSE
    # ========================================================

    if edit_id:

        # ----------------------------------------------------
        # PERMISSION: CHANGE EXPENSE
        # ----------------------------------------------------

        if not request.user.has_perm(
            "pigs.change_expense"
        ):
            messages.error(
                request,
                "❌ Huna ruhusa ya kuhariri matumizi."
            )

            return redirect(
                "pigs:expense_list"
            )

        expense = get_object_or_404(
            Expense,
            id=edit_id
        )

    # ========================================================
    # CREATE NEW EXPENSE
    # ========================================================

    else:

        if not request.user.has_perm(
            "pigs.add_expense"
        ):
            messages.error(
                request,
                "❌ Huna ruhusa ya kuongeza matumizi."
            )

            return redirect(
                "pigs:expense_list"
            )

    if request.method == "POST":

        expense_date = request.POST.get(
            "expense_date"
        )

        title = request.POST.get(
            "title"
        )

        amount = request.POST.get(
            "amount"
        )

        description = request.POST.get(
            "description"
        )

        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        if not expense_date:

            messages.error(
                request,
                "Tafadhali weka tarehe ya matumizi."
            )

            return render(
                request,
                "pigs/expense_form.html",
                {
                    "form_data": request.POST,
                    "expense": expense,
                }
            )

        if not title or not title.strip():

            messages.error(
                request,
                "Tafadhali weka jina la matumizi."
            )

            return render(
                request,
                "pigs/expense_form.html",
                {
                    "form_data": request.POST,
                    "expense": expense,
                }
            )

        if not amount:

            messages.error(
                request,
                "Tafadhali weka kiasi cha matumizi."
            )

            return render(
                request,
                "pigs/expense_form.html",
                {
                    "form_data": request.POST,
                    "expense": expense,
                }
            )

        # ----------------------------------------------------
        # CONVERT AMOUNT
        # ----------------------------------------------------

        try:

            amount = Decimal(
                amount
            )

        except (
            InvalidOperation,
            TypeError,
            ValueError
        ):

            messages.error(
                request,
                "Kiasi cha matumizi si sahihi."
            )

            return render(
                request,
                "pigs/expense_form.html",
                {
                    "form_data": request.POST,
                    "expense": expense,
                }
            )

        # ----------------------------------------------------
        # CHECK NEGATIVE AMOUNT
        # ----------------------------------------------------

        if amount < Decimal("0.00"):

            messages.error(
                request,
                "Kiasi cha matumizi hakiwezi kuwa hasi."
            )

            return render(
                request,
                "pigs/expense_form.html",
                {
                    "form_data": request.POST,
                    "expense": expense,
                }
            )

        # ----------------------------------------------------
        # CREATE / UPDATE
        # ----------------------------------------------------

        try:

            if expense:

                expense.expense_date = (
                    expense_date
                )

                expense.title = (
                    title.strip()
                )

                expense.amount = amount

                expense.description = (
                    description.strip()
                    if description
                    else ""
                )

                expense.save()

                messages.success(
                    request,
                    "Rekodi ya matumizi "
                    "imehaririwa kikamilifu."
                )

            else:

                Expense.objects.create(

                    expense_date=(
                        expense_date
                    ),

                    title=(
                        title.strip()
                    ),

                    amount=amount,

                    description=(
                        description.strip()
                        if description
                        else ""
                    )

                )

                messages.success(
                    request,
                    "Rekodi ya matumizi "
                    "imehifadhiwa kikamilifu."
                )

            return redirect(
                "pigs:expense_list"
            )

        except Exception:

            messages.error(
                request,
                "Kumetokea tatizo wakati wa "
                "kuhifadhi matumizi. "
                "Tafadhali jaribu tena."
            )

            return render(
                request,
                "pigs/expense_form.html",
                {
                    "form_data": request.POST,
                    "expense": expense,
                }
            )

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    return render(
        request,
        "pigs/expense_form.html",
        {
            "expense": expense,
        }
    )


@login_required
def expense_detail(request, expense_id):
    """
    Angalia taarifa kamili za matumizi moja.
    """

    # ========================================================
    # PERMISSION: VIEW EXPENSE
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_expense"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona matumizi."
        )

        return redirect(
            "pigs:dashboard"
        )

    expense = get_object_or_404(
        Expense,
        id=expense_id
    )

    return render(
        request,
        "pigs/expense_detail.html",
        {
            "expense": expense,
        }
    )


@login_required
def expense_delete(request, expense_id):
    """
    Futa rekodi ya matumizi.
    """

    # ========================================================
    # PERMISSION: DELETE EXPENSE
    # ========================================================

    if not request.user.has_perm(
        "pigs.delete_expense"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kufuta matumizi."
        )

        return redirect(
            "pigs:expense_list"
        )

    expense = get_object_or_404(
        Expense,
        id=expense_id
    )

    if request.method == "POST":

        expense.delete()

        messages.success(
            request,
            "Rekodi ya matumizi imefutwa."
        )

        return redirect(
            "pigs:expense_list"
        )

    return render(
        request,
        "pigs/expense_detail.html",
        {
            "expense": expense,
            "confirm_delete": True,
        }
    )


# ============================================================
# MONTHLY REPORT
# ============================================================

@login_required
def monthly_report(request):
    """Ripoti ya mwezi."""

    # ========================================================
    # PERMISSION
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_dailysalereport"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ripoti za mauzo."
        )

        return redirect(
            "pigs:dashboard"
        )

    # ========================================================
    # SELECTED MONTH
    # ========================================================

    today = timezone.localdate()

    try:

        year = int(
            request.GET.get(
                "year",
                today.year
            )
        )

        month = int(
            request.GET.get(
                "month",
                today.month
            )
        )

        if month < 1 or month > 12:
            raise ValueError

    except (
        TypeError,
        ValueError
    ):

        year = today.year
        month = today.month

    # ========================================================
    # DAILY SALES
    # ========================================================

    daily_sales = (
        DailySale.objects
        .filter(
            sale_date__year=year,
            sale_date__month=month
        )
        .prefetch_related(
            "food_records__food_item",
            "pig_records",
        )
        .select_related(
            "slaughter_batch"
        )
        .order_by(
            "sale_date",
            "id"
        )
    )

    #=========================================================
    # MONTHLY BATCHES
    #=========================================================

    monthly_batches = (
        SlaughterBatch.objects
        .filter(
            sales__daily_sale__sale_date__year=year,
            sales__daily_sale__sale_date__month=month,
        )
        .distinct()
    )

    total_batches = monthly_batches.count()

    # ========================================================
    # MONTHLY TOTALS
    # ========================================================

    total_food_income = Decimal("0.00")

    total_meat_income = Decimal("0.00")

    total_food_plates = 0

    total_meat_weight_kg = Decimal("0.00")

    daily_rows = []

    # ========================================================
    # PROCESS DAILY SALES
    # ========================================================

    for sale in daily_sales:

        food_income = sum(
            (
                record.total_price
                for record in sale.food_records.all()
            ),
            Decimal("0.00")
        )

        food_income = (
            food_income.quantize(
                Decimal("0.01")
            )
        )

        meat_income = (
            sale.total_meat_sales
            or Decimal("0.00")
        )

        meat_income = (
            meat_income.quantize(
                Decimal("0.01")
            )
        )

        meat_weight_kg = (
            sale.total_meat_weight_kg
            or Decimal("0.00")
        )

        meat_weight_kg = (
            meat_weight_kg.quantize(
                Decimal("0.01")
            )
        )

        food_plates = sum(
            (
                record.quantity
                for record in sale.food_records.all()
            ),
            0
        )

        total_food_income += food_income

        total_meat_income += meat_income

        total_food_plates += food_plates

        total_meat_weight_kg += meat_weight_kg

        total_income = (
            meat_income
            + food_income
        )

        daily_rows.append({

            "sale": sale,

            "food_income": food_income,

            "meat_income": meat_income,

            "meat_weight_kg": meat_weight_kg,

            "total_income": total_income,

            "food_plates": food_plates,

        })

    # ========================================================
    # TOTAL MONTHLY INCOME
    # ========================================================

    total_income = (
        total_meat_income
        + total_food_income
    )

    # ========================================================
    # MONTHLY BATCH PROFIT
    #
    # MUHIMU:
    #
    # HATUTUMII:
    #
    #     BatchProfitReport.finalized_date
    #
    # kwa sababu batch inaweza kufungwa siku nyingine.
    #
    # PROFIT YA BATCH INAWEKWA KWENYE:
    #
    #     SlaughterBatch.slaughter_date
    #
    # Hivyo:
    #
    # August 2025
    #     -> batches zilizochinjwa August 2025
    #
    # September 2025
    #     -> batches zilizochinjwa September 2025
    #
    # ========================================================

    monthly_profit = (
        BatchProfitReport.objects
        .filter(
            slaughter_batch__slaughter_date__year=year,
            slaughter_batch__slaughter_date__month=month
        )
        .aggregate(
            total=Sum(
                "total_profit"
            )
        )["total"]
        or Decimal("0.00")
    )

    monthly_profit = (
        monthly_profit.quantize(
            Decimal("0.01")
        )
    )

    # ========================================================
    # MONTHLY PROFIT BATCH COUNT
    # ========================================================

    monthly_profit_batches = (
        BatchProfitReport.objects
        .filter(
            slaughter_batch__slaughter_date__year=year,
            slaughter_batch__slaughter_date__month=month
        )
        .count()
    )

    # ========================================================
    # PREVIOUS MONTH
    # ========================================================

    if month == 1:

        previous_month = 12

        previous_year = year - 1

    else:

        previous_month = month - 1

        previous_year = year

    # ========================================================
    # NEXT MONTH
    # ========================================================

    if month == 12:

        next_month = 1

        next_year = year + 1

    else:

        next_month = month + 1

        next_year = year

    # ========================================================
    # MONTH NAME
    # ========================================================

    month_name = date(
        year,
        month,
        1
    ).strftime(
        "%B %Y"
    )

    # ========================================================
    # CONTEXT
    # ========================================================

    context = {

        "year": year,

        "month": month,

        "month_name": month_name,

        "daily_rows": daily_rows,

        "total_meat_income": total_meat_income,

        "total_meat_weight_kg": total_meat_weight_kg,

        "total_food_income": total_food_income,

        "total_income": total_income,

        "total_food_plates": total_food_plates,

        "monthly_profit": monthly_profit,

        "total_batches": total_batches,

        "previous_year": previous_year,

        "previous_month": previous_month,

        "next_year": next_year,

        "next_month": next_month,

        "now": timezone.now(),

    }

    return render(
        request,
        "pigs/monthly_report.html",
        context
    )


# ============================================================
# BATCH PROFIT REPORT - ORODHA YA RIPOTI ZOTE
# ============================================================

@login_required
def batch_profit_report_list(request):
    """Orodha ya ripoti zote za faida za batch"""

    # ========================================================
    # PERMISSION: VIEW BATCH PROFIT REPORT
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_batchprofitreport"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona profit reports."
        )

        return redirect(
            "pigs:dashboard"
        )

    reports = (
        BatchProfitReport.objects
        .select_related(
            "slaughter_batch"
        )
        .order_by(
            "-finalized_date",
            "-id"
        )
    )

    total_profit = (
        reports.aggregate(
            total=Sum(
                "total_profit"
            )
        )["total"]
        or Decimal("0.00")
    )

    # Hesabu idadi ya batches
    total_batches = reports.count()

    # Hesabu wastani wa faida
    average_profit = Decimal(
        "0.00"
    )

    if total_batches > 0:

        average_profit = (
            total_profit
            / Decimal(total_batches)
        )

    context = {

        "reports": reports,

        "total_profit": total_profit,

        "total_batches": total_batches,

        "average_profit": average_profit,

        "now": timezone.now(),

    }

    return render(
        request,
        "pigs/batch_profit_report_list.html",
        context
    )


# ============================================================
# BATCH PROFIT REPORT - MAELEZO YA RIPOTI MOJA
# ============================================================

@login_required
def batch_profit_report_detail(
    request,
    batch_id
):
    """Ripoti ya faida ya batch moja"""

    # ========================================================
    # PERMISSION: VIEW BATCH PROFIT REPORT
    # ========================================================

    if not request.user.has_perm(
        "pigs.view_batchprofitreport"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuona profit report."
        )

        return redirect(
            "pigs:dashboard"
        )

    batch = get_object_or_404(
        SlaughterBatch,
        id=batch_id
    )

    # ============================================================
    # HESABU FAIDA - Tangu siku ya kwanza hadi mwisho
    # ============================================================

    # 1. Jumla ya mauzo yote ya nyama kwa batch hii
    total_meat_sales = (
        batch.sales.aggregate(
            total=Sum(
                "total_amount"
            )
        )["total"]
        or Decimal("0.00")
    )

    # 2. Jumla ya gharama za ununuzi wa nguruwe zote kwenye batch
    total_purchase_cost = (
        batch.calculate_total_purchase_cost()
    )

    # 3. Faida = Mauzo yote - Gharama zote
    profit = (
        total_meat_sales
        - total_purchase_cost
    )

    # ============================================================
    # ANGALIA AU UNDA RIPOTI
    # ============================================================

    try:

        report = batch.profit_report

    except BatchProfitReport.DoesNotExist:

        # Unda ripoti ikiwa haipo
        report = (
            BatchProfitReport.objects.create(

                slaughter_batch=batch,

                total_pigs=batch.pigs.count(),

                total_purchase_cost=(
                    total_purchase_cost
                ),

                total_meat_sales=(
                    total_meat_sales
                ),

                total_profit=profit,

                finalized_date=(
                    batch.slaughter_date
                    or timezone.localdate()
                ),

            )
        )

    # ============================================================
    # DATA ZA ZIADA
    # ============================================================

    sales = (
        batch.sales
        .select_related(
            "daily_sale"
        )
        .order_by(
            "-created_at"
        )
    )

    pigs = (
        batch.pigs
        .all()
        .order_by(
            "tag_number"
        )
    )

    # Jumla ya uzito wa nyama iliyouzwa
    total_weight = (
        sales.aggregate(
            total=Sum(
                "meat_weight_sold"
            )
        )["total"]
        or Decimal("0.00")
    )

    # Wastani wa bei kwa kilo
    average_price_per_kg = Decimal(
        "0.00"
    )

    if total_weight > 0:

        average_price_per_kg = (
            total_meat_sales
            / total_weight
        )

    context = {

        "batch": batch,

        "report": report,

        "sales": sales,

        "pigs": pigs,

        "total_meat_sales": (
            total_meat_sales
        ),

        "total_purchase_cost": (
            total_purchase_cost
        ),

        "profit": profit,

        "total_weight": (
            total_weight
        ),

        "average_price_per_kg": (
            average_price_per_kg
        ),

        "total_sales": (
            sales.count()
        ),

        "total_pigs": (
            pigs.count()
        ),

        "now": timezone.now(),

    }

    return render(
        request,
        "pigs/batch_profit_report_detail.html",
        context
    )


# ============================================================
# DELETE PIG
# ============================================================

@login_required
def pig_delete(request, pig_id):
    """Kufuta nguruwe"""

    # ========================================================
    # PERMISSION: DELETE PIG
    # ========================================================

    if not request.user.has_perm(
        "pigs.delete_pig"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kufuta nguruwe."
        )

        return redirect(
            "pigs:pig_list"
        )

    pig = get_object_or_404(
        Pig,
        id=pig_id
    )

    if request.method == "POST":

        password = request.POST.get(
            "password"
        )

        if not request.user.check_password(
            password
        ):
            messages.error(
                request,
                "❌ Nenosiri si sahihi!"
            )

            return redirect(
                "pigs:pig_list"
            )

        # ----------------------------------------------------
        # SUPER ADMIN / AUTHORIZED USER
        #
        # HATUZUII DELETE KWA STATUS.
        #
        # Requirement yetu:
        # User mwenye delete permission anaweza
        # kufanya administrative deletion hata kama
        # pig amechinjwa au nyama imekwisha.
        # ----------------------------------------------------

        tag_number = pig.tag_number

        pig.delete()

        messages.success(
            request,
            f"✅ Nguruwe {tag_number} "
            f"imefutwa kikamilifu!"
        )

        return redirect(
            "pigs:pig_list"
        )

    return redirect(
        "pigs:pig_list"
    )


# ============================================================
# DELETE PURCHASE (BATCH)
# ============================================================

@login_required
def purchase_delete(request, purchase_id):
    """
    Kufuta ununuzi mzima na nguruwe zake.

    Hii ni administrative deletion.

    User mwenye delete_purchase permission anaweza
    kufuta purchase hata kama pigs zake zilifika
    hatua ya slaughter / finished.

    Django database relationships zitaendelea
    kufuata on_delete rules za models.
    """

    # ========================================================
    # PERMISSION: DELETE PURCHASE
    # ========================================================

    if not request.user.has_perm(
        "pigs.delete_purchase"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kufuta manunuzi."
        )

        return redirect(
            "pigs:purchase_list"
        )

    purchase = get_object_or_404(
        Purchase,
        id=purchase_id
    )

    if request.method == "POST":

        password = request.POST.get(
            "password"
        )

        if not request.user.check_password(
            password
        ):

            messages.error(
                request,
                "❌ Nenosiri si sahihi!"
            )

            return redirect(
                "pigs:purchase_list"
            )

        purchase_id_display = purchase.id

        create_audit_log(
            request,
            "DELETE",
            "Purchase",
            purchase.id,
            f"Alifuta manunuzi #{purchase.id} kutoka {purchase.supplier_name}",
        )

        purchase.delete()

        messages.success(
                    request,
            f"✅ Ununuzi #{purchase_id_display} "
            f"na nguruwe zake zimefutwa kikamilifu!"
        )

        return redirect(
            "pigs:purchase_list"
        )

    return redirect(
        "pigs:purchase_list"
    )


# ============================================================
# MEAT STATUS - FINAL SALE CONFIRMATION
# ============================================================

@login_required
def meat_status(request):

    """
    Final confirmation ya DailySale.

    DAILY SALE INASAVE ONLY WHEN:

        BADO IPO
        au
        NYAMA IMEISHA

    Meat KG calculation:

        Meat Sales ÷ Price per KG = Meat Weight KG

    Calculation ya KG inafanywa na Django backend,
    sio kuamini value iliyotumwa na JavaScript.
    """

    # ========================================================
    # PERMISSIONS
    #
    # Hapa action hii:
    #
    # 1. Inahifadhi DailySale
    # 2. Inabadilisha hali ya MeatStock
    #
    # Kwa hiyo user anahitaji permissions zote mbili.
    # ========================================================

    if not request.user.has_perm(
        "pigs.add_dailysale"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kuhifadhi mauzo ya siku."
        )

        return redirect(
            "pigs:dashboard"
        )

    if not request.user.has_perm(
        "pigs.change_meatstock"
    ):
        messages.error(
            request,
            "❌ Huna ruhusa ya kubadilisha hali ya stock ya nyama."
        )

        return redirect(
            "pigs:dashboard"
        )

    # ========================================================
    # GET PENDING SALE
    # ========================================================

    pending_data = request.session.get(
        "pending_daily_sale"
    )

    if not pending_data:

        messages.warning(
            request,
            "Hakuna mauzo ya kuthibitisha."
        )

        return redirect(
            "pigs:daily_sale_create"
        )

    # ========================================================
    # READ SESSION DATA
    # ========================================================

    try:

        sale_date = pending_data.get(
            "sale_date"
        )

        total_money_received = Decimal(
            pending_data.get(
                "total_money_received",
                "0.00"
            )
        )

        active_batch_id = pending_data.get(
            "active_batch_id"
        )

        food_rows = pending_data.get(
            "food_rows",
            []
        )

        food_total = Decimal(
            pending_data.get(
                "food_total",
                "0.00"
            )
        )

        notes = pending_data.get(
            "notes",
            ""
        )

    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):

        request.session.pop(
            "pending_daily_sale",
            None
        )

        request.session.modified = True

        messages.error(
            request,
            "Taarifa za mauzo ya muda zimeharibika. "
            "Tafadhali anza tena."
        )

        return redirect(
            "pigs:daily_sale_create"
        )

    # ========================================================
    # VALIDATE BATCH ID
    # ========================================================

    if not active_batch_id:

        messages.error(
            request,
            "Hakuna batch ya nyama iliyochaguliwa."
        )

        return redirect(
            "pigs:daily_sale_create"
        )

    # ========================================================
    # GET ACTIVE BATCH
    # ========================================================

    batch = get_object_or_404(
        SlaughterBatch,
        pk=active_batch_id
    )

    # ========================================================
    # POST - FINAL CONFIRMATION
    # ========================================================

    if request.method == "POST":

        # ----------------------------------------------------
        # MEAT STATUS
        # ----------------------------------------------------

        meat_status_value = request.POST.get(
            "meat_status"
        )

        if meat_status_value not in [
            "available",
            "finished",
        ]:

            messages.error(
                request,
                "Hali ya nyama haijatambulika."
            )

            return redirect(
                "pigs:meat_status"
            )

        # ----------------------------------------------------
        # PRICE PER KG
        # ----------------------------------------------------

        price_per_kg_raw = request.POST.get(
            "price_per_kg",
            ""
        )

        try:

            price_per_kg = Decimal(
                price_per_kg_raw
            )

        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ):

            messages.error(
                request,
                "Bei ya kilo si sahihi."
            )

            return redirect(
                "pigs:meat_status"
            )

        # ----------------------------------------------------
        # VALIDATE PRICE
        # ----------------------------------------------------

        if price_per_kg <= ZERO:

            messages.error(
                request,
                "❌ Bei ya kilo lazima iwe zaidi ya sifuri."
            )

            return redirect(
                "pigs:meat_status"
            )

        # ====================================================
        # CALCULATE MEAT SALES
        # ====================================================

        meat_total = (
            total_money_received
            - food_total
        )

        # ----------------------------------------------------
        # VALIDATE TOTALS
        # ----------------------------------------------------

        if meat_total < ZERO:

            messages.error(
                request,
                "❌ Jumla ya chakula haiwezi kuzidi "
                "jumla ya mauzo ya siku."
            )

            return redirect(
                "pigs:meat_status"
            )

        # ----------------------------------------------------
        # CALCULATE MEAT KG
        #
        # IMPORTANT:
        #
        # HATUTUMII calculated_kg YA JAVASCRIPT
        #
        # Django ndiyo source of truth.
        # ----------------------------------------------------

        if meat_total > ZERO:

            calculated_kg = (
                meat_total
                / price_per_kg
            ).quantize(
                Decimal("0.01")
            )

        else:

            calculated_kg = ZERO

        # ----------------------------------------------------
        # VALIDATE KG
        # ----------------------------------------------------

        if calculated_kg <= ZERO:

            messages.error(
                request,
                "❌ Kilo za nyama lazima ziwe zaidi ya sifuri."
            )

            return redirect(
                "pigs:meat_status"
            )

        # ====================================================
        # SAVE EVERYTHING
        # ====================================================

        try:

            with transaction.atomic():

                # ============================================
                # LOCK BATCH
                # ============================================

                batch = (
                    SlaughterBatch.objects
                    .select_for_update()
                    .get(
                        pk=active_batch_id
                    )
                )

                # ============================================
                # GET & LOCK MEAT STOCK
                # ============================================

                stock = (
                    MeatStock.objects
                    .select_for_update()
                    .get(
                        slaughter_batch=batch
                    )
                )

                # ============================================
                # CHECK STOCK STATUS
                # ============================================

                if stock.is_finished:

                    raise ValueError(
                        f"Batch {batch.batch_number} "
                        "tayari imefungwa."
                    )

                # ============================================
                # CREATE DAILY SALE
                # ============================================

                daily_sale = (
                    DailySale.objects.create(

                        sale_date=sale_date,

                        slaughter_batch=batch,

                        total_money_received=(
                            total_money_received
                        ),

                        total_food_sales=(
                            food_total
                        ),

                        total_meat_sales=(
                            meat_total
                        ),

                        meat_price_per_kg=(
                            price_per_kg
                        ),

                        total_meat_weight_kg=(
                            calculated_kg
                        ),

                        notes=notes,
                    )
                )

                # ============================================
                # CREATE FOOD SALES
                # ============================================

                for row in food_rows:

                    food_item, created = (
                        FoodItem.objects.get_or_create(

                            name=row["name"],

                            defaults={
                                "selling_price": Decimal(
                                    row["unit_price"]
                                ),
                                "is_active": True,
                            }

                        )
                    )

                    # ----------------------------------------
                    # UPDATE EXISTING FOOD ITEM PRICE
                    # ----------------------------------------

                    if not created:

                        food_item.selling_price = Decimal(
                            row["unit_price"]
                        )

                        food_item.is_active = True

                        food_item.save(
                            update_fields=[
                                "selling_price",
                                "is_active",
                                "updated_at",
                            ]
                        )

                    # ----------------------------------------
                    # CREATE FOOD SALE RECORD
                    # ----------------------------------------

                    FoodSaleRecord.objects.create(

                        daily_sale=daily_sale,

                        food_item=food_item,

                        quantity=int(
                            row["quantity"]
                        ),

                        unit_price=Decimal(
                            row["unit_price"]
                        ),

                    )

                # ============================================
                # REFRESH DAILY TOTALS
                # ============================================

                daily_sale.refresh_totals()

                # ============================================
                # CREATE / UPDATE DAILY REPORT
                # ============================================

                daily_sale.create_report()

                # ============================================
                # MEAT STILL AVAILABLE
                # ============================================

                if meat_status_value == "available":

                    stock.needs_confirmation = False

                    stock.save(
                        update_fields=[
                            "needs_confirmation",
                            "updated_at",
                        ]
                    )

                    message_text = (
                        f"✅ Mauzo yamehifadhiwa. "
                        f"Batch {batch.batch_number} "
                        f"bado ina nyama. "
                        f"Kilo zilizouzwa: "
                        f"{calculated_kg:.2f} kg."
                    )

                # ============================================
                # MEAT FINISHED
                # ============================================

                else:

                    report = (
                        finalize_meat_for_daily_sale(
                            daily_sale=daily_sale,
                            slaughter_batch=batch,
                        )
                    )

                    message_text = (
                        f"✅ Mauzo yamehifadhiwa. "
                        f"Batch {batch.batch_number} "
                        f"imefungwa. "
                        f"Kilo zilizouzwa: "
                        f"{calculated_kg:.2f} kg. "
                        f"Profit: "
                        f"TSh {report.total_profit:,.0f}"
                    )

                # ============================================
                # CLEAR PENDING SESSION
                # ============================================

                request.session.pop(
                    "pending_daily_sale",
                    None
                )

                request.session.modified = True

            # =================================================
            # SUCCESS
            # =================================================

            messages.success(
                request,
                message_text
            )

            return redirect(
                "pigs:daily_sale_detail",
                sale_id=daily_sale.id
            )

        # ====================================================
        # DATABASE INTEGRITY ERROR
        # ====================================================

        except IntegrityError:

            messages.error(
                request,
                "❌ Mauzo ya tarehe hii tayari yamehifadhiwa."
            )

            return redirect(
                "pigs:meat_status"
            )

        # ====================================================
        # OTHER ERROR
        # ====================================================

        except Exception as error:

            messages.error(
                request,
                f"❌ Mauzo hayakuweza kuhifadhiwa: {error}"
            )

            return redirect(
                "pigs:meat_status"
            )

    # ========================================================
    # PREVIEW - GET REQUEST
    # ========================================================

    meat_total = (
        total_money_received
        - food_total
    )

    if meat_total <= Decimal("0.00"):

        meat_total = Decimal("0.00")

    # ========================================================
    # DEFAULT PRICE FOR PREVIEW
    # ========================================================

    default_price_per_kg = Decimal(
        "14000.00"
    )

    if meat_total > Decimal("0.00"):

        preview_kg = (
            meat_total
            / default_price_per_kg
        ).quantize(
            Decimal("0.01")
        )

    else:

        preview_kg = Decimal(
            "0.00"
        )

    # ========================================================
    # RENDER CONFIRMATION PAGE
    # ========================================================

    return render(
        request,
        "pigs/meat_status_popup.html",
        {
            "pending_data": pending_data,

            "sale_date": sale_date,

            "total_money_received": (
                total_money_received
            ),

            "food_total": food_total,

            "meat_total": meat_total,

            "active_batch": batch,

            "price_per_kg": (
                default_price_per_kg
            ),

            "calculated_kg": preview_kg,
        }
    )