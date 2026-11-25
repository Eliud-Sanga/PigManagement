"""Authenticated, company-scoped JSON endpoints for the Home Management client.

The endpoints in this module are deliberately additive.  They reuse the
existing CompanyContextMiddleware scope and the membership permission model;
they never accept a company id for business reads and never fall back to an
unscoped business query.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import (
    Count,
    DecimalField,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Cast, Coalesce, Concat
from django.db.models import CharField
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.companies.authorization import get_effective_permission_codes
from apps.companies.context import resolve_company_context
from apps.companies.middleware import COMPANY_SESSION_KEY
from apps.companies.models import Company, CompanyMembership
from apps.pigs.models import (
    BatchProfitReport,
    DailySale,
    DailySaleReport,
    Expense,
    FarmRecord,
    FoodSaleRecord,
    MeatStock,
    Pig,
    Purchase,
)

from .views import _json


ZERO = Decimal("0.00")
MODULE_PERMISSIONS = {
    "farm": ("pigs.view_pig",),
    "pigs": ("pigs.view_pig",),
    "purchases": ("pigs.view_purchase",),
    "sales": ("pigs.view_dailysale",),
    "stock": ("pigs.view_meatstock",),
    "expenses": ("pigs.view_expense",),
    "reports": (
        "pigs.view_dailysalereport",
        "pigs.view_batchprofitreport",
    ),
}
MODULE_CREATE_PERMISSIONS = {
    "farm": "pigs.change_pig",
    # Pigs are created through a Purchase so costs and head-count stay linked.
    "pigs": None,
    "purchases": "pigs.add_purchase",
    "sales": "pigs.add_dailysale",
    "stock": "pigs.add_slaughterbatch",
    "expenses": "pigs.add_expense",
    "reports": None,
}
MODULE_ACTION_PERMISSIONS = {
    "farm": {"edit": "pigs.change_pig", "archive": "pigs.delete_pig"},
    "pigs": {
        "edit": "pigs.change_pig",
        "archive": "pigs.delete_pig",
    },
    "purchases": {
        "archive": "pigs.delete_purchase",
    },
    "sales": {},
    "stock": {
        "finish": "pigs.change_meatstock",
    },
    "expenses": {
        "edit": "pigs.change_expense",
        "archive": "pigs.delete_expense",
    },
    "reports": {},
}


def _money(value):
    return str(value if value is not None else ZERO)


def _date(value):
    return value.isoformat() if value is not None else None


def _error(request, code, message, status):
    return _json(
        request,
        {"ok": False, "code": code, "message": message},
        status_code=status,
    )


def _authenticated(request):
    user = getattr(request, "user", None)
    return user is not None and user.is_authenticated


def _business_context(request, *, permissions=()):
    if not _authenticated(request):
        return None, None, frozenset(), _error(
            request,
            "authentication_required",
            "Sign in before accessing business data.",
            401,
        )

    if getattr(request.user, "is_platform_admin", False):
        return None, None, frozenset(), _error(
            request,
            "platform_admin_control_plane",
            "Platform Administrator must use the Control Center.",
            403,
        )

    company = getattr(request, "company", None)
    membership = getattr(request, "company_membership", None)

    if company is None or membership is None:
        return None, None, frozenset(), _error(
            request,
            "company_selection_required",
            "Select an active company before accessing business data.",
            409,
        )

    if company.status != Company.Status.ACTIVE:
        return None, None, frozenset(), _error(
            request,
            "company_inactive",
            "The selected company is not active.",
            403,
        )

    if (
        membership.status != CompanyMembership.Status.ACTIVE
        or membership.company_id != company.pk
        or not membership.role.is_active
    ):
        return None, None, frozenset(), _error(
            request,
            "membership_inactive",
            "An active membership in the selected company is required.",
            403,
        )

    codes = get_effective_permission_codes(membership)
    required = tuple(permissions)

    if required and not any(code in codes for code in required):
        return None, None, codes, _error(
            request,
            "permission_denied",
            "You do not have permission to view this business module.",
            403,
        )

    return company, membership, codes, None


def _company_payload(membership, *, selected=False):
    company = membership.company
    transport_services = None
    if company.business_type == Company.BusinessType.TRANSPORT:
        profile = getattr(company, "transport_profile", None)
        transport_services = {
            "cargo": bool(profile and profile.cargo_enabled),
            "passenger": bool(profile and profile.passenger_enabled),
        }
    return {
        "id": str(company.public_id),
        "code": company.code,
        "name": company.name,
        "role": {
            "code": membership.role.code,
            "name": membership.role.name,
        },
        "language": (
            membership.approved_language
            or membership.requested_language
            or company.default_language
        ),
        "businessType": company.business_type,
        "transportServices": transport_services,
        "selected": selected,
    }


@never_cache
@require_GET
def workspace(request):
    if not _authenticated(request):
        return _error(
            request,
            "authentication_required",
            "Sign in before loading the workspace.",
            401,
        )

    user = request.user
    is_platform_admin = bool(
        getattr(user, "is_platform_admin", False)
    )

    if is_platform_admin:
        return _json(
            request,
            {
                "ok": True,
                "platformAdmin": True,
                "businessAccessAllowed": False,
                "companySelectionRequired": False,
                "selectedCompany": None,
                "companies": [],
                "permissions": [],
                "user": {
                    "id": str(user.pk),
                    "username": user.get_username(),
                    "email": getattr(user, "email", ""),
                },
            },
        )

    memberships = list(
        CompanyMembership.objects.active()
        .filter(user=user)
        .select_related("company", "role", "company__transport_profile")
        .order_by("company__name")
    )
    selected_company = getattr(request, "company", None)
    selected_membership = getattr(request, "company_membership", None)
    selected_id = (
        str(selected_company.public_id)
        if selected_company is not None
        else None
    )
    permissions = (
        sorted(get_effective_permission_codes(selected_membership))
        if selected_membership is not None
        else []
    )
    companies = [
        _company_payload(
            membership,
            selected=str(membership.company.public_id) == selected_id,
        )
        for membership in memberships
    ]
    selected_payload = next(
        (item for item in companies if item["selected"]),
        None,
    )

    return _json(
        request,
        {
            "ok": True,
            "platformAdmin": False,
            "businessAccessAllowed": bool(memberships),
            "companySelectionRequired": selected_payload is None,
            "selectedCompany": selected_payload,
            "companies": companies,
            "permissions": permissions,
            "user": {
                "id": str(user.pk),
                "username": user.get_username(),
                "email": getattr(user, "email", ""),
            },
        },
    )


@never_cache
@require_POST
@csrf_protect
def select_company(request):
    if not _authenticated(request):
        return _error(
            request,
            "authentication_required",
            "Sign in before selecting a company.",
            401,
        )

    if getattr(request.user, "is_platform_admin", False):
        return _error(
            request,
            "platform_admin_control_plane",
            "Platform Administrator must use the Control Center.",
            403,
        )

    public_id = (request.POST.get("company") or "").strip()
    if not public_id:
        return _error(
            request,
            "company_required",
            "Choose a company.",
            400,
        )

    try:
        context = resolve_company_context(
            user=request.user,
            company_public_id=public_id,
        )
    except (PermissionDenied, ValidationError, ValueError):
        return _error(
            request,
            "company_access_denied",
            "You do not have access to that company.",
            403,
        )

    request.session[COMPANY_SESSION_KEY] = str(
        context.company.public_id
    )
    request.session.modified = True

    return _json(
        request,
        {
            "ok": True,
            "selectedCompany": {
                "id": str(context.company.public_id),
                "code": context.company.code,
                "name": context.company.name,
            },
        },
    )


@never_cache
@require_POST
@csrf_protect
def clear_company(request):
    if not _authenticated(request):
        return _error(
            request,
            "authentication_required",
            "Sign in before changing the company.",
            401,
        )

    request.session.pop(COMPANY_SESSION_KEY, None)
    request.session.modified = True
    return _json(request, {"ok": True, "selectedCompany": None})


def _module_access(codes):
    return {
        module: any(code in codes for code in required)
        for module, required in MODULE_PERMISSIONS.items()
    }


def _module_actions(module, codes):
    create_permission = MODULE_CREATE_PERMISSIONS[module]
    can_create = bool(create_permission and create_permission in codes)

    # Recording a sale updates both the sale ledger and stock state.
    if module == "sales":
        can_create = (
            "pigs.add_dailysale" in codes
            and "pigs.change_meatstock" in codes
        )

    actions = {
        "create": can_create,
    }
    actions.update(
        {
            name: permission in codes
            for name, permission in MODULE_ACTION_PERMISSIONS[module].items()
        }
    )
    return actions


@never_cache
@require_GET
def pig_dashboard(request):
    company, membership, codes, error = _business_context(request)
    if error is not None:
        return error

    today = timezone.localdate()
    month_start = today.replace(day=1)
    trend_start = today - timedelta(days=13)

    total_pigs = Pig.objects.count()
    available_pigs = Pig.objects.filter(status=Pig.AVAILABLE).count()
    slaughtered_pigs = Pig.objects.filter(status=Pig.SLAUGHTERED).count()
    finished_pigs = Pig.objects.filter(status=Pig.FINISHED).count()
    purchase_count = Purchase.objects.count()
    sales_count = DailySale.objects.count()
    active_stock_count = MeatStock.objects.filter(is_finished=False).count()

    total_income = (
        DailySale.objects.aggregate(total=Sum("total_money_received"))[
            "total"
        ]
        or ZERO
    )
    food_income = (
        FoodSaleRecord.objects.aggregate(total=Sum("total_price"))["total"]
        or ZERO
    )
    meat_income = max(total_income - food_income, ZERO)
    today_income = (
        DailySale.objects.filter(sale_date=today).aggregate(
            total=Sum("total_money_received")
        )["total"]
        or ZERO
    )
    monthly_income = (
        DailySale.objects.filter(
            sale_date__gte=month_start,
            sale_date__lte=today,
        ).aggregate(total=Sum("total_money_received"))["total"]
        or ZERO
    )
    monthly_profit = (
        BatchProfitReport.objects.filter(
            finalized_date__gte=month_start,
            finalized_date__lte=today,
        ).aggregate(total=Sum("total_profit"))["total"]
        or ZERO
    )
    monthly_expenses = (
        Expense.objects.filter(
            expense_date__gte=month_start,
            expense_date__lte=today,
        ).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    total_expenses = (
        Expense.objects.aggregate(total=Sum("amount"))["total"] or ZERO
    )

    sales_by_date = {
        row["sale_date"]: row["total"] or ZERO
        for row in (
            DailySale.objects.filter(sale_date__gte=trend_start)
            .values("sale_date")
            .annotate(total=Sum("total_money_received"))
        )
    }
    expenses_by_date = {
        row["expense_date"]: row["total"] or ZERO
        for row in (
            Expense.objects.filter(expense_date__gte=trend_start)
            .values("expense_date")
            .annotate(total=Sum("amount"))
        )
    }
    trend = []
    for offset in range(14):
        day = trend_start + timedelta(days=offset)
        trend.append(
            {
                "date": day.isoformat(),
                "sales": _money(sales_by_date.get(day, ZERO)),
                "expenses": _money(expenses_by_date.get(day, ZERO)),
            }
        )

    recent_sales = [
        {
            "kind": "sale",
            "id": str(sale.pk),
            "title": f"Mauzo {sale.sale_date.isoformat()}",
            "date": sale.sale_date.isoformat(),
            "amount": _money(sale.total_money_received),
        }
        for sale in DailySale.objects.order_by("-sale_date", "-created_at")[:5]
    ]
    recent_expenses = [
        {
            "kind": "expense",
            "id": str(expense.pk),
            "title": expense.title,
            "date": expense.expense_date.isoformat(),
            "amount": _money(expense.amount),
        }
        for expense in Expense.objects.order_by(
            "-expense_date", "-created_at"
        )[:5]
    ]
    recent_activity = sorted(
        recent_sales + recent_expenses,
        key=lambda item: (item["date"], item["id"]),
        reverse=True,
    )[:6]

    active_batches = []
    stocks = (
        MeatStock.objects.filter(is_finished=False)
        .select_related("slaughter_batch")
        .order_by("-slaughter_batch__slaughter_date", "-created_at")[:5]
    )
    for stock in stocks:
        batch = stock.slaughter_batch
        active_batches.append(
            {
                "id": str(stock.pk),
                "batchNumber": batch.batch_number,
                "slaughterDate": _date(batch.slaughter_date),
                "pigCount": batch.pigs.count(),
                "needsConfirmation": stock.needs_confirmation,
                "isFinished": stock.is_finished,
                "meatSales": _money(batch.get_total_meat_sales()),
                "meatWeightKg": _money(batch.get_total_meat_weight()),
            }
        )

    reports_count = (
        DailySaleReport.objects.count() + BatchProfitReport.objects.count()
    )
    module_access = _module_access(codes)

    return _json(
        request,
        {
            "ok": True,
            "company": {
                "id": str(company.public_id),
                "code": company.code,
                "name": company.name,
            },
            "user": {
                "username": request.user.get_username(),
                "role": membership.role.name,
            },
            "currency": "TZS",
            "generatedAt": timezone.now().isoformat(),
            "summary": {
                "totalPigs": total_pigs,
                "availablePigs": available_pigs,
                "slaughteredPigs": slaughtered_pigs,
                "finishedPigs": finished_pigs,
                "purchases": purchase_count,
                "sales": sales_count,
                "activeStock": active_stock_count,
                "reports": reports_count,
                "todayIncome": _money(today_income),
                "totalIncome": _money(total_income),
                "meatIncome": _money(meat_income),
                "foodIncome": _money(food_income),
                "monthlyIncome": _money(monthly_income),
                "monthlyProfit": _money(monthly_profit),
                "monthlyExpenses": _money(monthly_expenses),
                "totalExpenses": _money(total_expenses),
            },
            "moduleAccess": module_access,
            "recentActivity": recent_activity,
            "activeBatches": active_batches,
            "trend": trend,
        },
    )


def _positive_int(request, key, default, maximum):
    try:
        value = int(request.GET.get(key, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, 1), maximum)


def _query_text(request):
    query = request.GET.get("q", "").strip()
    if len(query) > 100:
        return None, _error(
            request,
            "invalid_query",
            "Search text must be 100 characters or fewer.",
            400,
        )
    return query, None


def _query_date(query):
    try:
        return date.fromisoformat(query)
    except (TypeError, ValueError):
        return None


def _page_queryset(queryset, page, limit):
    total = queryset.count()
    start = (page - 1) * limit
    end = start + limit
    return total, list(queryset[start:end]), end < total


def _search_pigs(queryset, query):
    if not query:
        return queryset
    filters = (
        Q(tag_number__icontains=query)
        | Q(status__icontains=query)
        | Q(gender__icontains=query)
        | Q(notes__icontains=query)
        | Q(purchase__supplier_name__icontains=query)
        | Q(purchase__supplier_location__icontains=query)
        | Q(purchase__supplier_phone__icontains=query)
    )
    date_value = _query_date(query)
    if date_value:
        filters |= Q(purchase__purchase_date=date_value)
    return queryset.filter(filters)


def _search_purchases(queryset, query):
    if not query:
        return queryset
    filters = (
        Q(supplier_name__icontains=query)
        | Q(supplier_phone__icontains=query)
        | Q(supplier_location__icontains=query)
        | Q(notes__icontains=query)
    )
    date_value = _query_date(query)
    if date_value:
        filters |= Q(purchase_date=date_value)
    normalized = query.casefold().replace(" ", "_")
    if normalized == "pending":
        filters |= Q(pigs_created=False)
    if normalized == "pigs_created":
        filters |= Q(pigs_created=True)
    return queryset.filter(filters)


def _search_sales(queryset, query):
    if not query:
        return queryset
    filters = Q(notes__icontains=query) | Q(
        slaughter_batch__batch_number__icontains=query
    )
    date_value = _query_date(query)
    if date_value:
        filters |= Q(sale_date=date_value)
    return queryset.filter(filters)


def _search_stock(queryset, query):
    if not query:
        return queryset
    filters = Q(slaughter_batch__batch_number__icontains=query)
    date_value = _query_date(query)
    if date_value:
        filters |= Q(slaughter_batch__slaughter_date=date_value)
    normalized = query.casefold().replace(" ", "_")
    if normalized in {"finished", "imeisha"}:
        filters |= Q(is_finished=True)
    if normalized in {"available", "inapatikana"}:
        filters |= Q(is_finished=False, needs_confirmation=False)
    if normalized in {"needs_confirmation", "needsconfirmation"}:
        filters |= Q(is_finished=False, needs_confirmation=True)
    return queryset.filter(filters)


def _search_expenses(queryset, query):
    if not query:
        return queryset
    filters = Q(title__icontains=query) | Q(description__icontains=query)
    date_value = _query_date(query)
    if date_value:
        filters |= Q(expense_date=date_value)
    return queryset.filter(filters)


def _pig_item(pig):
    return {
        "id": str(pig.pk),
        "title": pig.tag_number or f"Pig #{pig.pk}",
        "subtitle": pig.purchase.supplier_name,
        "status": "ARCHIVED" if pig.is_archived else pig.status,
        "pigStatus": pig.status,
        "date": _date(pig.archived_at.date()) if pig.is_archived and pig.archived_at else _date(pig.purchase.purchase_date),
        "archivedAt": pig.archived_at.isoformat() if pig.archived_at else "",
        "amount": _money(pig.get_purchase_cost()),
        "purchasePrice": (
            _money(pig.purchase_price)
            if pig.purchase_price is not None
            else ""
        ),
        "gender": pig.gender,
        "notes": pig.notes or "",
    }


def _purchase_item(purchase):
    return {
        "id": str(purchase.pk),
        "title": purchase.supplier_name,
        "subtitle": f"{purchase.number_of_pigs} pigs",
        "status": "PIGS_CREATED" if purchase.pigs_created else "PENDING",
        "date": _date(purchase.purchase_date),
        "amount": _money(purchase.total_cost),
        "phone": purchase.supplier_phone or "",
        "location": purchase.supplier_location or "",
        "notes": purchase.notes or "",
    }


def _sale_item(sale):
    return {
        "id": str(sale.pk),
        "title": sale.sale_date.isoformat(),
        "subtitle": (
            sale.slaughter_batch.batch_number
            if sale.slaughter_batch_id
            else "No batch"
        ),
        "status": "RECORDED",
        "date": _date(sale.sale_date),
        "amount": _money(sale.total_money_received),
        "foodSales": _money(sale.total_food_sales),
        "meatSales": _money(sale.total_meat_sales),
        "meatWeightKg": _money(sale.total_meat_weight_kg),
        "meatPricePerKg": _money(sale.meat_price_per_kg),
        "notes": sale.notes or "",
    }


def _stock_item(stock):
    batch = stock.slaughter_batch
    if stock.is_finished:
        status = "FINISHED"
    elif stock.needs_confirmation:
        status = "NEEDS_CONFIRMATION"
    else:
        status = "AVAILABLE"
    return {
        "id": str(stock.pk),
        "title": batch.batch_number,
        "subtitle": f"{stock.pig_count} pigs",
        "status": status,
        "date": _date(batch.slaughter_date),
        "amount": _money(stock.meat_sales),
        "meatWeightKg": _money(stock.meat_weight),
        "finishedDate": _date(stock.finished_date),
    }


def _expense_item(expense):
    return {
        "id": str(expense.pk),
        "title": expense.title,
        "subtitle": expense.description or "",
        "status": "EXPENSE",
        "date": _date(expense.expense_date),
        "amount": _money(expense.amount),
    }


def _farm_item(record):
    return {
        "id": str(record.pk),
        "title": record.title,
        "subtitle": record.pig.tag_number if record.pig_id else record.get_kind_display(),
        "status": record.kind,
        "date": _date(record.record_date),
        "amount": _money(record.cost),
        "quantity": str(record.quantity) if record.quantity is not None else "",
        "unit": record.unit,
        "pigId": str(record.pig_id) if record.pig_id else "",
        "notes": record.notes,
    }


def _report_item(kind, report):
    if kind == "daily":
        sale = report.daily_sale
        return {
            "id": f"daily-{report.pk}",
            "title": "Daily sales report",
            "subtitle": sale.sale_date.isoformat(),
            "status": "DAILY",
            "date": _date(sale.sale_date),
            "amount": _money(report.total_income),
            "meatIncome": _money(report.total_pig_income),
            "foodIncome": _money(report.total_food_income),
            "meatWeightKg": _money(report.total_meat_weight_kg),
        }
    return {
        "id": f"batch-{report.pk}",
        "title": f"Batch {report.slaughter_batch.batch_number}",
        "subtitle": f"{report.total_pigs} pigs",
        "status": "PROFIT" if report.total_profit >= ZERO else "LOSS",
        "date": _date(report.finalized_date),
        "amount": _money(report.total_profit),
        "purchaseCost": _money(report.total_purchase_cost),
        "meatSales": _money(report.total_meat_sales),
    }


def _report_page(query, codes, page, limit):
    daily = DailySaleReport.objects.none()
    batch = BatchProfitReport.objects.none()
    if "pigs.view_dailysalereport" in codes:
        daily = DailySaleReport.objects.select_related("daily_sale").all()
        if query:
            filters = (
                Q(notes__icontains=query)
                | Q(daily_sale__notes__icontains=query)
            )
            date_value = _query_date(query)
            if date_value:
                filters |= Q(daily_sale__sale_date=date_value)
            if query.casefold() in {"daily", "sales report", "daily sales report"}:
                filters |= Q(pk__isnull=False)
            daily = daily.filter(filters)
    if "pigs.view_batchprofitreport" in codes:
        batch = BatchProfitReport.objects.select_related(
            "slaughter_batch"
        ).all()
        if query:
            filters = Q(slaughter_batch__batch_number__icontains=query)
            date_value = _query_date(query)
            if date_value:
                filters |= Q(finalized_date=date_value)
            normalized = query.casefold()
            if normalized in {"profit", "faida"}:
                filters |= Q(total_profit__gte=ZERO)
            if normalized in {"loss", "hasara"}:
                filters |= Q(total_profit__lt=ZERO)
            batch = batch.filter(filters)

    # Clear each model's Meta.ordering before UNION. SQLite rejects ORDER BY
    # inside a compound query's component SELECTs.
    daily = daily.order_by().annotate(
        _sort_date=F("daily_sale__sale_date"),
        _sort_created=F("created_at"),
        _sort_id=Concat(
            Value("daily-"), Cast("pk", output_field=CharField()),
            output_field=CharField(),
        ),
    ).values("_sort_date", "_sort_created", "_sort_id")
    batch = batch.order_by().annotate(
        _sort_date=F("finalized_date"),
        _sort_created=F("created_at"),
        _sort_id=Concat(
            Value("batch-"), Cast("pk", output_field=CharField()),
            output_field=CharField(),
        ),
    ).values("_sort_date", "_sort_created", "_sort_id")
    combined = daily.union(batch, all=True).order_by(
        "-_sort_date", "-_sort_created", "-_sort_id"
    )
    total = combined.count()
    start = (page - 1) * limit
    keys = list(combined[start : start + limit])
    daily_ids = [
        key["_sort_id"][6:]
        for key in keys
        if key["_sort_id"].startswith("daily-")
    ]
    batch_ids = [
        key["_sort_id"][6:]
        for key in keys
        if key["_sort_id"].startswith("batch-")
    ]
    daily_map = {
        str(item.pk): item
        for item in DailySaleReport.objects.filter(pk__in=daily_ids).select_related(
            "daily_sale"
        )
    }
    batch_map = {
        str(item.pk): item
        for item in BatchProfitReport.objects.filter(pk__in=batch_ids).select_related(
            "slaughter_batch"
        )
    }
    items = []
    for key in keys:
        report_key = key["_sort_id"]
        if report_key.startswith("daily-"):
            report = daily_map.get(report_key[6:])
            if report is not None:
                items.append(_report_item("daily", report))
        else:
            report = batch_map.get(report_key[6:])
            if report is not None:
                items.append(_report_item("batch", report))
    return total, items, (start + limit) < total


def _module_page(module, query, codes, page, limit, *, archived=False, pig_status='', company=None):
    if module == "farm":
        queryset = FarmRecord.objects.select_related("pig").order_by(
            "-record_date", "-created_at", "-pk",
        )
        if query:
            filters = (Q(title__icontains=query) | Q(notes__icontains=query)
                       | Q(kind__icontains=query) | Q(pig__tag_number__icontains=query))
            date_value = _query_date(query)
            if date_value:
                filters |= Q(record_date=date_value)
            queryset = queryset.filter(filters)
        total, records, has_next = _page_queryset(queryset, page, limit)
        items = [_farm_item(record) for record in records]
    elif module == "pigs":
        source = (
            Pig.all_objects.filter(company=company, is_archived=True)
            .select_related("purchase")
            .order_by("-archived_at", "-pk")
            if archived else
            Pig.objects.select_related("purchase")
            .order_by("-purchase__purchase_date", "-created_at", "-pk")
        )
        if pig_status == 'available':
            source = source.filter(status=Pig.AVAILABLE)
        elif pig_status == 'slaughtered':
            source = source.filter(status__in=(Pig.SLAUGHTERED, Pig.FINISHED))
        queryset = _search_pigs(
            source,
            query,
        )
        total, records, has_next = _page_queryset(queryset, page, limit)
        items = [_pig_item(record) for record in records]
    elif module == "purchases":
        queryset = _search_purchases(
            Purchase.objects.order_by("-purchase_date", "-created_at", "-pk"),
            query,
        )
        total, records, has_next = _page_queryset(queryset, page, limit)
        items = [_purchase_item(record) for record in records]
    elif module == "sales":
        queryset = _search_sales(
            DailySale.objects.select_related("slaughter_batch").order_by(
                "-sale_date", "-created_at", "-pk"
            ),
            query,
        )
        total, records, has_next = _page_queryset(queryset, page, limit)
        items = [_sale_item(record) for record in records]
    elif module == "stock":
        sale_totals = DailySale.objects.filter(
            slaughter_batch_id=OuterRef("slaughter_batch_id")
        ).values("slaughter_batch_id").annotate(value=Sum("total_meat_sales")).values(
            "value"
        )
        weight_totals = DailySale.objects.filter(
            slaughter_batch_id=OuterRef("slaughter_batch_id")
        ).values("slaughter_batch_id").annotate(value=Sum("total_meat_weight_kg")).values(
            "value"
        )
        amount_field = DecimalField(max_digits=14, decimal_places=2)
        queryset = MeatStock.objects.select_related("slaughter_batch").annotate(
            pig_count=Count("slaughter_batch__pigs", distinct=True),
            meat_sales=Coalesce(
                Subquery(sale_totals, output_field=amount_field),
                Value(ZERO),
                output_field=amount_field,
            ),
            meat_weight=Coalesce(
                Subquery(weight_totals, output_field=amount_field),
                Value(ZERO),
                output_field=amount_field,
            ),
        ).order_by("-created_at", "-pk")
        queryset = _search_stock(queryset, query)
        total, records, has_next = _page_queryset(queryset, page, limit)
        items = [_stock_item(record) for record in records]
    elif module == "expenses":
        queryset = _search_expenses(
            Expense.objects.order_by("-expense_date", "-created_at", "-pk"),
            query,
        )
        total, records, has_next = _page_queryset(queryset, page, limit)
        items = [_expense_item(record) for record in records]
    else:
        return _report_page(query, codes, page, limit)
    return total, items, has_next


@never_cache
@require_GET
def pig_module(request, module):
    permissions = MODULE_PERMISSIONS.get(module)
    if permissions is None:
        return _error(
            request,
            "module_not_found",
            "That business module does not exist.",
            404,
        )

    company, _membership, codes, error = _business_context(
        request,
        permissions=permissions,
    )
    if error is not None:
        return error

    archived_value = request.GET.get("archived", "0")
    if archived_value not in {"0", "1"} or (archived_value == "1" and module != "pigs"):
        return _error(request, "invalid_archive_filter", "Invalid archive filter.", 400)
    archived = archived_value == "1"
    pig_status = request.GET.get('status', '')
    if pig_status not in ('', 'available', 'slaughtered') or (pig_status and (module != 'pigs' or archived)):
        return _error(request, 'invalid_pig_status', 'Invalid pig status filter.', 400)

    page = _positive_int(request, "page", 1, 100000)
    limit = _positive_int(request, "limit", 25, 100)
    query, query_error = _query_text(request)
    if query_error is not None:
        return query_error
    total, items, has_next = _module_page(
        module,
        query,
        codes,
        page,
        limit,
        archived=archived,
        pig_status=pig_status,
        company=company,
    )
    actions = _module_actions(module, codes)

    pig_summary = None
    if module == 'pigs':
        current = Pig.objects.select_related('purchase')
        counts = current.aggregate(
            all=Count('pk'),
            available=Count('pk', filter=Q(status=Pig.AVAILABLE)),
            slaughtered=Count('pk', filter=Q(status__in=(Pig.SLAUGHTERED, Pig.FINISHED))),
        )
        # Match Pig.get_purchase_cost(), including individually priced pigs
        # and the model's HALF_UP cent rounding for shared purchases.
        purchase_total = sum(
            (pig.get_purchase_cost() for pig in current.iterator(chunk_size=500)),
            ZERO,
        )
        pig_summary = {
            **counts,
            'archived': Pig.all_objects.filter(company=company, is_archived=True).count(),
            'totalPurchaseAmount': _money(purchase_total),
        }

    return _json(
        request,
        {
            "ok": True,
            "module": module,
            "company": {
                "id": str(company.public_id),
                "name": company.name,
            },
            "page": page,
            "limit": limit,
            "query": query,
            "count": total,
            "hasNext": has_next,
            "archived": archived,
            "canCreate": actions["create"],
            "actions": actions,
            **({'pigSummary': pig_summary} if pig_summary is not None else {}),
            "items": items,
        },
    )
