"""Safe company-scoped write endpoints for the Home Management client.

All business objects are resolved through the active company scope installed by
``CompanyContextMiddleware``.  Mutation endpoints never accept a company id.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.pigs.models import (
    DailySale,
    Expense,
    FarmRecord,
    FoodItem,
    FoodSaleRecord,
    MeatStock,
    Pig,
    Purchase,
    SlaughterBatch,
)
from apps.pigs.services import (
    create_pigs_for_purchase,
    create_purchase as create_purchase_record,
    create_slaughter_batch,
    finalize_meat_for_daily_sale,
)
from apps.users.models import AuditLog
from apps.users.utils import create_audit_log

from .views import _json
from .workspace_views import ZERO, _business_context, _date, _money


MAX_PURCHASE_PIGS = 250
MAX_FOOD_ROWS = 30


def _error(request, code, message, status=400, *, fields=None):
    payload = {"ok": False, "code": code, "message": message}
    if fields:
        payload["fields"] = fields
    return _json(request, payload, status_code=status)


def _exception_message(error):
    if isinstance(error, ValidationError):
        if hasattr(error, "message_dict"):
            messages = []
            for field, values in error.message_dict.items():
                for value in values:
                    messages.append(f"{field}: {value}")
            if messages:
                return " ".join(messages)
        if error.messages:
            return " ".join(str(value) for value in error.messages)
    return str(error) or "The request could not be completed."


def _context(request, *permissions):
    company, membership, codes, error = _business_context(
        request,
        permissions=permissions,
    )
    return company, membership, codes, error


def _text(request, key, *, required=False, maximum=None):
    value = (request.POST.get(key) or "").strip()
    if required and not value:
        raise ValidationError({key: "This field is required."})
    if maximum is not None and len(value) > maximum:
        raise ValidationError({key: f"Use at most {maximum} characters."})
    return value


def _decimal(request, key, *, minimum=None, required=True):
    raw = (request.POST.get(key) or "").strip()
    if not raw and not required:
        return None
    if not raw:
        raise ValidationError({key: "This field is required."})
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError({key: "Enter a valid amount."}) from error
    if not value.is_finite():
        raise ValidationError({key: "Enter a finite amount."})
    if minimum is not None and value < minimum:
        raise ValidationError({key: f"Value must be at least {minimum}."})
    return value.quantize(Decimal("0.01"))


def _integer(request, key, *, minimum=0, maximum=None):
    raw = (request.POST.get(key) or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValidationError({key: "Enter a valid whole number."}) from error
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and at most {maximum}" if maximum is not None else ""
        raise ValidationError(
            {key: f"Value must be at least {minimum}{upper}."}
        )
    return value


def _iso_date(request, key, *, default_today=False):
    raw = (request.POST.get(key) or "").strip()
    if not raw and default_today:
        return timezone.localdate()
    if not raw:
        raise ValidationError({key: "This field is required."})
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise ValidationError({key: "Use a valid YYYY-MM-DD date."}) from error


def _boolean(request, key):
    return (request.POST.get(key) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "finished",
    }


def _operation_id(request):
    value = (request.POST.get("operation_id") or "").strip()
    if len(value) > 100:
        raise ValidationError(
            {"operation_id": "Operation id is too long."}
        )
    return value


def _duplicate_operation(request, operation_id):
    if not operation_id:
        return False
    company = getattr(request, "company", None)
    return AuditLog.objects.filter(
        user=request.user,
        company_public_id=getattr(company, "public_id", None),
        metadata__operation_id=operation_id,
        result="SUCCESS",
    ).exists()


def _audit(request, action, model_name, instance, description, operation_id):
    return create_audit_log(
        request,
        action,
        model_name,
        instance.pk,
        description,
        metadata={"operation_id": operation_id} if operation_id else {},
    )


def _mutation_guard(request, operation_id):
    expected_company_id = (request.POST.get("expected_company_id") or "").strip()
    company = getattr(request, "company", None)
    if expected_company_id and expected_company_id != str(getattr(company, "public_id", "")):
        return _error(
            request,
            "company_context_changed",
            "The selected company changed. Select the original company before syncing.",
            409,
        )
    # Serialise attempts from this account before the audit receipt check.
    # The whole endpoint runs in a transaction, including the business write
    # and its receipt; concurrent retries cannot both pass the same check.
    if operation_id:
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
    if _duplicate_operation(request, operation_id):
        return _error(
            request,
            "duplicate_operation",
            "This action was already saved. Refresh to see the latest data.",
            409,
        )
    return None


def _capabilities(codes):
    return {
        "purchaseCreate": "pigs.add_purchase" in codes,
        "purchaseArchive": "pigs.delete_purchase" in codes,
        "pigEdit": "pigs.change_pig" in codes,
        "pigArchive": "pigs.delete_pig" in codes,
        "batchCreate": "pigs.add_slaughterbatch" in codes,
        "saleCreate": (
            "pigs.add_dailysale" in codes
            and "pigs.change_meatstock" in codes
        ),
        "stockFinish": "pigs.change_meatstock" in codes,
        "expenseCreate": "pigs.add_expense" in codes,
        "expenseEdit": "pigs.change_expense" in codes,
        "expenseArchive": "pigs.delete_expense" in codes,
    }


@never_cache
@require_GET
def operation_options(request):
    company, _membership, codes, error = _context(request)
    if error is not None:
        return error

    available_pigs = [
        {
            "id": str(pig.pk),
            "tagNumber": pig.tag_number,
            "gender": pig.gender or "",
            "purchaseCost": _money(pig.get_purchase_cost()),
        }
        for pig in Pig.objects.filter(status=Pig.AVAILABLE)
        .select_related("purchase")
        .order_by("tag_number", "pk")
    ]
    active_batches = []
    for stock in (
        MeatStock.objects.filter(is_finished=False)
        .select_related("slaughter_batch")
        .order_by("-slaughter_batch__slaughter_date", "-pk")
    ):
        batch = stock.slaughter_batch
        active_batches.append(
            {
                "id": str(batch.pk),
                "stockId": str(stock.pk),
                "batchNumber": batch.batch_number,
                "slaughterDate": _date(batch.slaughter_date),
                "pigCount": batch.pigs.count(),
            }
        )

    food_items = [
        {
            "id": str(item.pk),
            "name": item.name,
            "sellingPrice": _money(item.selling_price),
        }
        for item in FoodItem.objects.filter(is_active=True).order_by("name")
    ]

    return _json(
        request,
        {
            "ok": True,
            "company": {
                "id": str(company.public_id),
                "name": company.name,
            },
            "today": timezone.localdate().isoformat(),
            "capabilities": _capabilities(codes),
            "availablePigs": available_pigs,
            "activeBatches": active_batches,
            "foodItems": food_items,
        },
    )


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def purchase_create(request):
    _company, _membership, _codes, error = _context(
        request, "pigs.add_purchase"
    )
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate

        number_of_pigs = _integer(
            request,
            "number_of_pigs",
            minimum=1,
            maximum=MAX_PURCHASE_PIGS,
        )
        purchase_date = _iso_date(request, "purchase_date", default_today=True)
        total_cost = _decimal(request, "total_cost", minimum=Decimal("0.01"))
        supplier_name = _text(
            request, "supplier_name", required=True, maximum=100
        )
        supplier_phone = _text(request, "supplier_phone", maximum=20)
        supplier_location = _text(
            request, "supplier_location", maximum=100
        )
        notes = _text(request, "notes")

        raw_pigs = (request.POST.get("pigs_data") or "").strip()
        if raw_pigs:
            try:
                submitted_pigs = json.loads(raw_pigs)
            except (TypeError, ValueError) as exc:
                raise ValidationError({"pigs_data": "Invalid pig details."}) from exc
            if not isinstance(submitted_pigs, list) or len(submitted_pigs) != number_of_pigs:
                raise ValidationError({"pigs_data": "Provide details for every pig."})
            pigs_data = []
            specified_total = Decimal("0.00")
            for index, item in enumerate(submitted_pigs, start=1):
                if not isinstance(item, dict):
                    raise ValidationError({"pigs_data": f"Pig {index} must be an object."})
                gender = str(item.get("gender") or "").upper().strip()
                if gender not in {Pig.MALE, Pig.FEMALE}:
                    raise ValidationError({"pigs_data": f"Choose gender for pig {index}."})
                raw_price = str(item.get("purchase_price") or "").strip()
                if number_of_pigs > 1 and not raw_price:
                    raise ValidationError({"pigs_data": f"Enter price for pig {index}."})
                price = None
                if raw_price:
                    try:
                        price = Decimal(raw_price)
                        if not price.is_finite() or price <= 0:
                            raise ValueError()
                        price = price.quantize(Decimal("0.01"))
                    except (InvalidOperation, ValueError) as exc:
                        raise ValidationError({"pigs_data": f"Invalid price for pig {index}."}) from exc
                    specified_total += price
                if number_of_pigs == 1:
                    price = total_cost
                pig_notes = str(item.get("notes") or "").strip()
                if len(pig_notes) > 1000:
                    raise ValidationError({"pigs_data": f"Notes for pig {index} are too long."})
                pigs_data.append({"gender": gender, "purchase_price": price, "notes": pig_notes})
            if number_of_pigs > 1 and specified_total > total_cost:
                raise ValidationError({"pigs_data": "Pig prices cannot exceed the purchase total."})
        else:
            # Older clients still create pigs with optional details unset.
            pigs_data = [
                {"gender": None, "purchase_price": None, "notes": ""}
                for _ in range(number_of_pigs)
            ]

        with transaction.atomic():
            purchase = create_purchase_record(
                supplier_name=supplier_name,
                supplier_phone=supplier_phone,
                supplier_location=supplier_location,
                purchase_date=purchase_date,
                number_of_pigs=number_of_pigs,
                total_cost=total_cost,
                notes=notes,
            )
            purchase.full_clean()
            purchase.save()
            pigs = create_pigs_for_purchase(
                purchase=purchase,
                pigs_data=pigs_data,
            )
            _audit(
                request,
                "CREATE",
                "Purchase",
                purchase,
                f"Created purchase from {supplier_name} with {number_of_pigs} pigs.",
                operation_id,
            )

        return _json(
            request,
            {
                "ok": True,
                "message": "Purchase and pig records were saved.",
                "purchase": {
                    "id": str(purchase.pk),
                    "supplierName": purchase.supplier_name,
                    "pigCount": len(pigs),
                    "totalCost": _money(purchase.total_cost),
                },
            },
            status_code=201,
        )
    except (ValidationError, ValueError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)
    except IntegrityError:
        return _error(
            request,
            "purchase_conflict",
            "The purchase could not be saved because its data conflicts with an existing record.",
            409,
        )


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def pig_update(request, pig_id):
    _company, _membership, _codes, error = _context(
        request, "pigs.change_pig"
    )
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        with transaction.atomic():
            pig = (
                Pig.objects.select_for_update()
                .select_related("purchase")
                .get(pk=pig_id)
            )
            if pig.status != Pig.AVAILABLE:
                raise ValidationError(
                    "Only an available pig can have purchase details edited."
                )
            gender = _text(request, "gender").upper()
            if gender and gender not in {Pig.MALE, Pig.FEMALE}:
                raise ValidationError({"gender": "Choose MALE or FEMALE."})
            pig.gender = gender or None
            pig.purchase_price = _decimal(
                request,
                "purchase_price",
                minimum=Decimal("0.01"),
                required=False,
            )
            pig.notes = _text(request, "notes")
            pig.full_clean()
            pig.save()
            _audit(
                request,
                "EDIT",
                "Pig",
                pig,
                f"Updated pig {pig.tag_number}.",
                operation_id,
            )
        return _json(
            request,
            {
                "ok": True,
                "message": "Pig details were updated.",
                "pig": {
                    "id": str(pig.pk),
                    "tagNumber": pig.tag_number,
                    "gender": pig.gender or "",
                    "purchasePrice": (
                        _money(pig.purchase_price)
                        if pig.purchase_price is not None
                        else ""
                    ),
                },
            },
        )
    except ObjectDoesNotExist:
        return _error(request, "pig_not_found", "Pig was not found.", 404)
    except (ValidationError, ValueError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def pig_archive(request, pig_id):
    _company, _membership, _codes, error = _context(
        request, "pigs.delete_pig"
    )
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        with transaction.atomic():
            pig = Pig.objects.select_for_update().get(pk=pig_id)
            label = pig.tag_number
            pig.archive()
            _audit(
                request,
                "ARCHIVE",
                "Pig",
                pig,
                f"Archived pig {label}.",
                operation_id,
            )
        return _json(request, {"ok": True, "message": "Pig was archived."})
    except ObjectDoesNotExist:
        return _error(request, "pig_not_found", "Pig was not found.", 404)
    except (ValidationError, ValueError) as exc:
        return _error(request, "archive_blocked", _exception_message(exc), 409)


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def purchase_archive(request, purchase_id):
    _company, _membership, _codes, error = _context(
        request, "pigs.delete_purchase"
    )
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        with transaction.atomic():
            purchase = Purchase.objects.select_for_update().get(pk=purchase_id)
            purchase.archive()
            _audit(
                request,
                "ARCHIVE",
                "Purchase",
                purchase,
                f"Archived purchase #{purchase.pk}.",
                operation_id,
            )
        return _json(
            request, {"ok": True, "message": "Purchase was archived."}
        )
    except ObjectDoesNotExist:
        return _error(
            request, "purchase_not_found", "Purchase was not found.", 404
        )
    except (ValidationError, ValueError) as exc:
        return _error(request, "archive_blocked", _exception_message(exc), 409)


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def slaughter_create(request):
    _company, _membership, _codes, error = _context(
        request, "pigs.add_slaughterbatch"
    )
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        raw_ids = (request.POST.get("pig_ids") or "").split(",")
        pig_ids = []
        for raw in raw_ids:
            raw = raw.strip()
            if not raw:
                continue
            try:
                pig_id = int(raw)
            except ValueError as exc:
                raise ValidationError({"pig_ids": "Invalid pig selection."}) from exc
            if pig_id not in pig_ids:
                pig_ids.append(pig_id)
        if not pig_ids:
            raise ValidationError({"pig_ids": "Choose at least one pig."})
        slaughter_date = _iso_date(
            request, "slaughter_date", default_today=True
        )
        notes = _text(request, "notes")

        with transaction.atomic():
            if MeatStock.objects.select_for_update().filter(
                is_finished=False
            ).exists():
                raise ValidationError(
                    "Finish the current meat stock before creating another batch."
                )
            pigs = list(
                Pig.objects.select_for_update()
                .filter(pk__in=pig_ids, status=Pig.AVAILABLE)
                .order_by("pk")
            )
            if len(pigs) != len(pig_ids):
                raise ValidationError(
                    "One or more selected pigs are unavailable. Refresh and try again."
                )
            batch = create_slaughter_batch(
                pigs=pigs,
                slaughter_date=slaughter_date,
                notes=notes,
            )
            batch.full_clean()
            _audit(
                request,
                "CREATE",
                "SlaughterBatch",
                batch,
                f"Created slaughter batch {batch.batch_number} with {len(pigs)} pigs.",
                operation_id,
            )
        return _json(
            request,
            {
                "ok": True,
                "message": "Slaughter batch and meat stock were created.",
                "batch": {
                    "id": str(batch.pk),
                    "batchNumber": batch.batch_number,
                    "pigCount": len(pigs),
                },
            },
            status_code=201,
        )
    except (ValidationError, ValueError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)
    except IntegrityError:
        return _error(
            request,
            "batch_conflict",
            "The batch could not be created. Refresh and try again.",
            409,
        )


def _food_rows(request):
    raw = (request.POST.get("foods") or "[]").strip() or "[]"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError({"foods": "Food rows are invalid."}) from exc
    if not isinstance(payload, list) or len(payload) > MAX_FOOD_ROWS:
        raise ValidationError(
            {"foods": f"Use no more than {MAX_FOOD_ROWS} food rows."}
        )
    rows = []
    for index, raw_row in enumerate(payload, start=1):
        if not isinstance(raw_row, dict):
            raise ValidationError({"foods": f"Food row {index} is invalid."})
        name = str(raw_row.get("name") or "").strip()
        if not name or len(name) > 100:
            raise ValidationError(
                {"foods": f"Food row {index} needs a valid name."}
            )
        try:
            quantity = int(raw_row.get("quantity"))
            unit_price = Decimal(str(raw_row.get("unitPrice"))).quantize(
                Decimal("0.01")
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError(
                {"foods": f"Food row {index} has invalid quantity or price."}
            ) from exc
        if not unit_price.is_finite() or quantity < 1 or unit_price <= ZERO:
            raise ValidationError(
                {"foods": f"Food row {index} must have positive values."}
            )
        rows.append(
            {"name": name, "quantity": quantity, "unit_price": unit_price}
        )
    return rows


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def sale_create(request):
    company, _membership, codes, error = _context(
        request,
        "pigs.add_dailysale",
    )
    if error is not None:
        return error
    if "pigs.change_meatstock" not in codes:
        return _error(
            request,
            "permission_denied",
            "You need permission to update meat stock before saving a sale.",
            403,
        )
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        sale_date = _iso_date(request, "sale_date", default_today=True)
        total_money = _decimal(
            request, "total_money_received", minimum=Decimal("0.01")
        )
        price_per_kg = _decimal(
            request, "price_per_kg", minimum=Decimal("0.01")
        )
        batch_id = _integer(request, "batch_id", minimum=1)
        notes = _text(request, "notes")
        meat_finished = _boolean(request, "meat_finished")
        food_rows = _food_rows(request)
        food_total = sum(
            (row["unit_price"] * row["quantity"] for row in food_rows),
            ZERO,
        ).quantize(Decimal("0.01"))
        meat_total = (total_money - food_total).quantize(Decimal("0.01"))
        if meat_total <= ZERO:
            raise ValidationError(
                "Total money must be greater than the food total so meat sales remain positive."
            )
        calculated_kg = (meat_total / price_per_kg).quantize(Decimal("0.01"))
        if calculated_kg <= ZERO:
            raise ValidationError("Calculated meat weight must be positive.")

        with transaction.atomic():
            batch = SlaughterBatch.objects.select_for_update().get(pk=batch_id)
            stock = MeatStock.objects.select_for_update().get(
                slaughter_batch=batch
            )
            if stock.is_finished:
                raise ValidationError(
                    f"Batch {batch.batch_number} is already finished."
                )
            daily_sale = DailySale(
                company=company,
                sale_date=sale_date,
                slaughter_batch=batch,
                total_money_received=total_money,
                total_food_sales=food_total,
                total_meat_sales=meat_total,
                meat_price_per_kg=price_per_kg,
                total_meat_weight_kg=calculated_kg,
                notes=notes,
            )
            daily_sale.full_clean()
            daily_sale.save()

            for row in food_rows:
                food_item = FoodItem.objects.filter(name=row["name"]).first()
                if food_item is None:
                    food_item = FoodItem(
                        company=company,
                        name=row["name"],
                        selling_price=row["unit_price"],
                        is_active=True,
                    )
                else:
                    food_item.selling_price = row["unit_price"]
                    food_item.is_active = True
                food_item.full_clean()
                food_item.save()
                food_record = FoodSaleRecord(
                    company=company,
                    daily_sale=daily_sale,
                    food_item=food_item,
                    quantity=row["quantity"],
                    unit_price=row["unit_price"],
                )
                food_record.full_clean()
                food_record.save()

            daily_sale.refresh_totals()
            report = daily_sale.create_report()
            profit_report = None
            if meat_finished:
                profit_report = finalize_meat_for_daily_sale(
                    daily_sale=daily_sale,
                    slaughter_batch=batch,
                )
            else:
                stock.needs_confirmation = False
                stock.save(
                    update_fields=["needs_confirmation", "updated_at"]
                )
            _audit(
                request,
                "CREATE",
                "DailySale",
                daily_sale,
                f"Recorded daily sale for {sale_date.isoformat()} on batch {batch.batch_number}.",
                operation_id,
            )

        return _json(
            request,
            {
                "ok": True,
                "message": (
                    "Sale saved and stock finalized."
                    if meat_finished
                    else "Sale saved. Meat stock remains available."
                ),
                "sale": {
                    "id": str(daily_sale.pk),
                    "date": daily_sale.sale_date.isoformat(),
                    "total": _money(daily_sale.total_money_received),
                    "foodTotal": _money(daily_sale.total_food_sales),
                    "meatTotal": _money(daily_sale.total_meat_sales),
                    "meatWeightKg": _money(daily_sale.total_meat_weight_kg),
                    "reportId": str(report.pk),
                    "profit": (
                        _money(profit_report.total_profit)
                        if profit_report is not None
                        else None
                    ),
                },
            },
            status_code=201,
        )
    except ObjectDoesNotExist:
        return _error(
            request,
            "batch_not_found",
            "The selected active batch was not found.",
            404,
        )
    except (ValidationError, ValueError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)
    except IntegrityError:
        return _error(
            request,
            "sale_date_conflict",
            "A sale for this date already exists.",
            409,
        )


def _expense_values(request):
    return {
        "expense_date": _iso_date(
            request, "expense_date", default_today=True
        ),
        "title": _text(request, "title", required=True, maximum=200),
        "amount": _decimal(request, "amount", minimum=ZERO),
        "description": _text(request, "description"),
    }


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def expense_create(request):
    company, _membership, _codes, error = _context(
        request, "pigs.add_expense"
    )
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        values = _expense_values(request)
        with transaction.atomic():
            expense = Expense(company=company, **values)
            expense.full_clean()
            expense.save()
            _audit(
                request,
                "CREATE",
                "Expense",
                expense,
                f"Created expense {expense.title}.",
                operation_id,
            )
        return _json(
            request,
            {
                "ok": True,
                "message": "Expense was saved.",
                "expense": {
                    "id": str(expense.pk),
                    "title": expense.title,
                    "amount": _money(expense.amount),
                },
            },
            status_code=201,
        )
    except (ValidationError, ValueError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def expense_update(request, expense_id):
    _company, _membership, _codes, error = _context(
        request, "pigs.change_expense"
    )
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        values = _expense_values(request)
        with transaction.atomic():
            expense = Expense.objects.select_for_update().get(pk=expense_id)
            for key, value in values.items():
                setattr(expense, key, value)
            expense.full_clean()
            expense.save()
            _audit(
                request,
                "EDIT",
                "Expense",
                expense,
                f"Updated expense {expense.title}.",
                operation_id,
            )
        return _json(
            request, {"ok": True, "message": "Expense was updated."}
        )
    except ObjectDoesNotExist:
        return _error(
            request, "expense_not_found", "Expense was not found.", 404
        )
    except (ValidationError, ValueError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def expense_archive(request, expense_id):
    _company, _membership, _codes, error = _context(
        request, "pigs.delete_expense"
    )
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        with transaction.atomic():
            expense = Expense.objects.select_for_update().get(pk=expense_id)
            expense.archive()
            _audit(
                request,
                "ARCHIVE",
                "Expense",
                expense,
                f"Archived expense {expense.title}.",
                operation_id,
            )
        return _json(
            request, {"ok": True, "message": "Expense was archived."}
        )
    except ObjectDoesNotExist:
        return _error(
            request, "expense_not_found", "Expense was not found.", 404
        )


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def stock_finish(request, stock_id):
    _company, _membership, _codes, error = _context(
        request, "pigs.change_meatstock"
    )
    if error is not None:
        return error
    if (request.POST.get("confirmation") or "").strip() != "FINISH":
        return _error(
            request,
            "confirmation_required",
            "Explicit FINISH confirmation is required.",
            400,
        )
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        with transaction.atomic():
            stock = (
                MeatStock.objects.select_for_update()
                .select_related("slaughter_batch")
                .get(pk=stock_id)
            )
            if stock.is_finished:
                raise ValidationError("This meat stock is already finished.")
            stock.mark_finished()
            _audit(
                request,
                "EDIT",
                "MeatStock",
                stock,
                f"Finished meat stock for {stock.slaughter_batch.batch_number}.",
                operation_id,
            )
        return _json(
            request,
            {"ok": True, "message": "Meat stock and batch were finalized."},
        )
    except ObjectDoesNotExist:
        return _error(request, "stock_not_found", "Stock was not found.", 404)
    except (ValidationError, ValueError) as exc:
        return _error(request, "finish_blocked", _exception_message(exc), 409)


def _farm_values(request):
    kind = _text(request, "kind", required=True, maximum=16).upper()
    if kind not in dict(FarmRecord.KIND_CHOICES):
        raise ValidationError({"kind": "Select a supported farm record type."})
    return {
        "kind": kind,
        "record_date": _iso_date(request, "record_date"),
        "title": _text(request, "title", required=True, maximum=120),
        "quantity": _decimal(request, "quantity", minimum=Decimal("0.01"), required=False),
        "unit": _text(request, "unit", maximum=20).lower(),
        "cost": _decimal(request, "cost", minimum=ZERO, required=False),
        "notes": _text(request, "notes", maximum=2000),
    }


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def farm_create(request):
    company, _membership, _codes, error = _context(request, "pigs.change_pig")
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        values = _farm_values(request)
        raw_pig_id = _text(request, "pig_id")
        pig = None
        if raw_pig_id:
            pig_id = _integer(request, "pig_id", minimum=1)
            pig = Pig.objects.select_for_update().get(pk=pig_id, company=company)
            if pig.status != Pig.AVAILABLE:
                raise ValidationError({"pig_id": "Pig is no longer available."})
        with transaction.atomic():
            record = FarmRecord(company=company, pig=pig, **values)
            record.full_clean()
            record.save()
            if record.kind == FarmRecord.MORTALITY:
                pig.status = Pig.DECEASED
                pig.save(update_fields=["status", "updated_at"])
            _audit(request, "CREATE", "FarmRecord", record,
                   f"Recorded {record.kind.lower()}: {record.title}.", operation_id)
        return _json(request, {
            "ok": True, "message": "Farm record was saved.",
            "record": {"id": str(record.pk), "kind": record.kind},
        }, status_code=201)
    except ObjectDoesNotExist:
        return _error(request, "pig_not_found", "Pig was not found.", 404)
    except (ValidationError, ValueError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def farm_update(request, record_id):
    company, _membership, _codes, error = _context(request, "pigs.change_pig")
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        record = FarmRecord.objects.select_for_update().get(
            pk=record_id, company=company,
        )
        if record.kind == FarmRecord.MORTALITY:
            return _error(request, "immutable_record",
                          "Mortality records need an audited correction workflow.", 409)
        values = _farm_values(request)
        if values["kind"] != record.kind or (
            request.POST.get("pig_id") or ""
        ).strip() != (str(record.pig_id) if record.pig_id else ""):
            return _error(request, "immutable_identity",
                          "Record type and pig cannot be changed.", 409)
        with transaction.atomic():
            for key, value in values.items():
                setattr(record, key, value)
            record.full_clean()
            record.save()
            _audit(request, "EDIT", "FarmRecord", record,
                   f"Updated {record.kind.lower()}: {record.title}.", operation_id)
        return _json(request, {"ok": True, "message": "Farm record was updated."})
    except ObjectDoesNotExist:
        return _error(request, "record_not_found", "Farm record was not found.", 404)
    except (ValidationError, ValueError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def farm_archive(request, record_id):
    company, _membership, _codes, error = _context(request, "pigs.delete_pig")
    if error is not None:
        return error
    try:
        operation_id = _operation_id(request)
        duplicate = _mutation_guard(request, operation_id)
        if duplicate is not None:
            return duplicate
        record = FarmRecord.objects.select_for_update().get(
            pk=record_id, company=company,
        )
        if record.kind == FarmRecord.MORTALITY:
            return _error(request, "immutable_record",
                          "Mortality records cannot be archived.", 409)
        with transaction.atomic():
            record.archive()
            _audit(request, "ARCHIVE", "FarmRecord", record,
                   f"Archived {record.kind.lower()}: {record.title}.", operation_id)
        return _json(request, {"ok": True, "message": "Farm record was archived."})
    except ObjectDoesNotExist:
        return _error(request, "record_not_found", "Farm record was not found.", 404)
