"""Company-scoped mobile API for Home Management Transport operations."""

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from django.contrib.auth import get_user_model

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, IntegerField, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.companies.models import Company, CompanyAuditEvent
from apps.transport.models import (
    Cargo,
    CargoPayment,
    CargoSize,
    Expense,
    Maintenance,
    PassengerBooking,
    PassengerPayment,
    PassengerSchedule,
    Route,
    TransportProfile,
    Trip,
    Vehicle,
)

from apps.transport import round_trip
from apps.users.models import AuditLog
from apps.users.utils import create_audit_log

from .views import _json
from .workspace_views import _business_context

ZERO = Decimal("0.00")

MODULES = {
    "vehicles": (Vehicle, "vehicle", None),
    "routes": (Route, "route", None),
    "trips": (Trip, "trip", None),
    "cargo": (Cargo, "cargo", "cargo"),
    "cargo_sizes": (CargoSize, "cargosize", "cargo"),
    "cargo_payments": (CargoPayment, "cargopayment", "cargo"),
    "expenses": (Expense, "expense", None),
    "maintenance": (Maintenance, "maintenance", None),
    "passenger_schedules": (PassengerSchedule, "passengerschedule", "passenger"),
    "passenger_bookings": (PassengerBooking, "passengerbooking", "passenger"),
    "passenger_payments": (PassengerPayment, "passengerpayment", "passenger"),
}

CREATE_PERMISSION = {
    "vehicles": "transport.add_vehicle",
    "routes": "transport.add_route",
    "trips": "transport.add_trip",
    "cargo": "transport.add_cargo",
    "cargo_sizes": "transport.add_cargosize",
    "cargo_payments": "transport.add_cargopayment",
    "expenses": "transport.add_expense",
    "maintenance": "transport.add_maintenance",
    "passenger_schedules": "transport.add_passengerschedule",
    "passenger_bookings": "transport.add_passengerbooking",
    "passenger_payments": "transport.add_passengerpayment",
}

VIEW_PERMISSION = {
    "vehicles": "transport.view_vehicle",
    "routes": "transport.view_route",
    "trips": "transport.view_trip",
    "cargo": "transport.view_cargo",
    "cargo_sizes": "transport.view_cargosize",
    "cargo_payments": "transport.view_cargopayment",
    "expenses": "transport.view_expense",
    "maintenance": "transport.view_maintenance",
    "passenger_schedules": "transport.view_passengerschedule",
    "passenger_bookings": "transport.view_passengerbooking",
    "passenger_payments": "transport.view_passengerpayment",
}

CHANGE_PERMISSION = {
    "vehicles": "transport.change_vehicle",
    "routes": "transport.change_route",
    "trips": "transport.change_trip",
    "cargo": "transport.change_cargo",
    "cargo_sizes": "transport.change_cargosize",
    "cargo_payments": "transport.change_cargopayment",
    "expenses": "transport.change_expense",
    "maintenance": "transport.change_maintenance",
    "passenger_schedules": "transport.change_passengerschedule",
    "passenger_bookings": "transport.change_passengerbooking",
    "passenger_payments": "transport.change_passengerpayment",
}


def _error(request, code, message, status):
    return _json(request, {"ok": False, "code": code, "message": message}, status_code=status)


def _transport_context(request, *, permission=None, service=None):
    company, membership, codes, error = _business_context(request)
    if error is not None:
        return None, None, codes, None, error
    if company.business_type != Company.BusinessType.TRANSPORT:
        return None, None, codes, None, _error(
            request, "wrong_business_type", "The selected company is not a Transport business.", 409
        )
    profile = TransportProfile.objects.filter(company=company).first()
    if profile is None:
        return None, None, codes, None, _error(
            request, "transport_profile_missing", "Transport services are not configured for this company.", 409
        )
    if service == "cargo" and not profile.cargo_enabled:
        return None, None, codes, profile, _error(request, "service_disabled", "Cargo service is disabled.", 403)
    if service == "passenger" and not profile.passenger_enabled:
        return None, None, codes, profile, _error(request, "service_disabled", "Passenger service is disabled.", 403)
    if permission and permission not in codes:
        return None, None, codes, profile, _error(request, "permission_denied", "You do not have permission for this operation.", 403)
    return company, membership, codes, profile, None


def _money(value):
    return str(value if value is not None else ZERO)


def _date(value):
    return value.isoformat() if value is not None else None


def _record_payload(module, obj, *, include_sizes=False):
    common = {"id": obj.pk, "createdAt": obj.created_at.isoformat() if getattr(obj, "created_at", None) else None}
    if module == "vehicles":
        return {**common, "title": obj.plate_number, "subtitle": f"{obj.name} • {obj.vehicle_type}", "status": "ACTIVE" if obj.is_active else "INACTIVE", "date": _date(obj.created_at.date()), "amount": _money(obj.current_value), "name": obj.name, "plateNumber": obj.plate_number}
    if module == "routes":
        return {**common, "title": f"Route #{obj.route_number}", "subtitle": f"{obj.start_location} → {obj.end_location or '—'} • {obj.vehicle.plate_number}", "status": obj.status, "date": _date(obj.started_at.date() if obj.started_at else obj.created_at.date()), "amount": _money(obj.profit), "endLocation": obj.end_location}
    if module == "trips":
        return {**common, "title": f"Trip #{obj.trip_number}", "subtitle": f"{obj.origin} → {obj.destination}", "status": obj.status, "date": _date(obj.departure_at.date() if obj.departure_at else obj.created_at.date()), "amount": "0.00"}
    if module == "cargo":
        return {**common, "title": obj.description, "cargoType": obj.cargo_type, **({"sizeDetails": [{"size": row.size, "quantity": row.quantity} for row in obj.size_details.all()]} if include_sizes else {}), "subtitle": f"{obj.customer_name} • {obj.destination or obj.trip.destination}", "status": obj.payment_status, "date": _date(obj.cargo_date), "amount": _money(obj.agreed_amount), "delivered": obj.delivered, "customerName": obj.customer_name, "customerPhone": obj.customer_phone, "destination": obj.destination, "quantity": str(obj.quantity) if obj.agreed_amount is not None or (include_sizes and obj.unit == "pieces") else None, "unit": obj.unit if obj.agreed_amount is not None or (include_sizes and obj.unit == "pieces") else None, "amountPaid": _money(obj.amount_paid), "balanceDue": _money(obj.balance_due) if obj.agreed_amount is not None else None, "tripStatus": obj.trip.status, "tripId": obj.trip_id, "notes": obj.notes}
    if module == "cargo_sizes":
        return {**common, "title": obj.size, "subtitle": f"{obj.cargo.description} • Quantity {obj.quantity}", "status": "RECORDED", "date": _date(obj.created_at.date()), "amount": "0.00"}
    if module == "cargo_payments":
        return {**common, "title": f"Payment • {obj.cargo.customer_name}", "subtitle": obj.reference or obj.cargo.description, "status": "RECORDED", "date": _date(obj.payment_date.date()), "amount": _money(obj.amount)}
    if module == "expenses":
        return {**common, "title": obj.description, "subtitle": f"{obj.get_category_display()} • {obj.expense_type}", "status": "RECORDED", "date": _date(obj.expense_date.date()), "amount": _money(obj.amount)}
    if module == "maintenance":
        return {**common, "title": obj.service_type, "subtitle": f"{obj.vehicle.plate_number} • {obj.description}", "status": "RECORDED", "date": _date(obj.service_date), "amount": _money(obj.cost)}
    if module == "passenger_schedules":
        return {**common, "title": f"{obj.trip.origin} → {obj.trip.destination}", "subtitle": obj.scheduled_departure.isoformat(), "status": obj.status, "date": _date(obj.scheduled_departure.date()), "amount": _money(obj.fare_amount), "seatsRemaining": max(obj.seat_capacity - obj._mobile_booked_count, 0) if hasattr(obj, "_mobile_booked_count") else obj.seats_remaining}
    if module == "passenger_bookings":
        return {**common, "title": obj.passenger_name, "subtitle": f"Seat {obj.seat_number} • {obj.booking_reference}", "status": obj.payment_status, "date": _date(obj.created_at.date()), "amount": _money(obj.fare_amount), "amountPaid": _money(obj.amount_paid), "bookingReference": obj.booking_reference, "bookingStatus": obj.status, "tripStatus": obj.schedule.trip.status}
    if module == "passenger_payments":
        return {**common, "title": f"Payment • {obj.booking.passenger_name}", "subtitle": obj.reference or obj.booking.booking_reference, "status": "RECORDED", "date": _date(obj.payment_date.date()), "amount": _money(obj.amount)}
    return common


def _module_queryset(module):
    model = MODULES[module][0]
    queryset = model.objects.all()
    if module == "routes":
        return queryset.select_related("vehicle")
    if module == "trips":
        return queryset.select_related("route", "route__vehicle")
    if module == "cargo":
        return queryset.select_related("trip", "trip__route", "trip__route__vehicle")
    if module == "cargo_sizes":
        return queryset.select_related("cargo", "cargo__trip", "cargo__trip__route", "cargo__trip__route__vehicle")
    if module == "cargo_payments":
        return queryset.select_related("cargo")
    if module == "maintenance":
        return queryset.select_related("vehicle")
    if module == "passenger_schedules":
        booked = PassengerBooking.all_objects.filter(
            company_id=OuterRef("company_id"), schedule_id=OuterRef("pk"),
            is_archived=False,
        ).exclude(status=PassengerBooking.Status.CANCELLED).order_by().values(
            "schedule_id",
        ).annotate(total=Count("pk")).values("total")
        return queryset.select_related("trip").annotate(
            _mobile_booked_count=Coalesce(Subquery(booked, output_field=IntegerField()), Value(0)),
        )
    if module == "passenger_bookings":
        return queryset.select_related("schedule", "schedule__trip", "schedule__trip__route")
    if module == "passenger_payments":
        return queryset.select_related("booking", "booking__schedule")
    return queryset


def _form_options(profile, codes, *, cargo_history=False, payment_options=False):
    options = {
        "vehicles": [],
        "routes": [],
        "trips": [],
        "passengerSchedules": [],
        "passengerBookings": [],
        "cargo": [],
    }
    if "transport.view_vehicle" in codes:
        options["vehicles"] = [
            {"id": row.pk, "label": f"{row.plate_number} • {row.name}"}
            for row in Vehicle.objects.filter(is_active=True).order_by("plate_number")[:200]
        ]
    if round_trip.enabled(profile):
        options["roundTripVehicles"] = options["vehicles"] if round_trip.REQUIRED.issubset(codes) else []
    if "transport.view_route" in codes:
        options["routes"] = [
            {"id": row.pk, "label": f"#{row.route_number} • {row.start_location} → {row.end_location or '—'}"}
            for row in Route.objects.filter(status__in=(Route.Status.PLANNED, Route.Status.ACTIVE)).order_by("-route_number")[:200]
        ]
    if "transport.view_trip" in codes:
        options["trips"] = [
            {"id": row.pk, "label": f"#{row.trip_number} • {row.origin} → {row.destination}"}
            for row in Trip.objects.filter(status__in=(Trip.Status.PLANNED, Trip.Status.IN_PROGRESS)).select_related("route")[:200]
        ]
        if cargo_history:
            if {"transport.add_cargosize", "transport.view_cargosize"}.issubset(codes):
                options["timberIntakeSizes"] = []
            options["cargoTrips"] = [
                {"id": row.pk, "label": f"#{row.trip_number} • {row.origin} → {row.destination} • {row.status}"}
                for row in Trip.objects.filter(status__in=(Trip.Status.IN_PROGRESS, Trip.Status.PLANNED)).order_by("-departure_at", "-pk")[:100]
            ] + [
                {"id": row.pk, "label": f"#{row.trip_number} • {row.origin} → {row.destination} • COMPLETED"}
                for row in Trip.objects.filter(status=Trip.Status.COMPLETED).order_by("-arrival_at", "-pk")[:100]
            ]
            options["activeCargoTrips"] = [
                {"id": row.pk, "label": f"#{row.trip_number}"}
                for row in Trip.objects.filter(status=Trip.Status.IN_PROGRESS).order_by("-departure_at", "-pk")[:2]
            ]
    if profile.passenger_enabled and "transport.view_passengerschedule" in codes:
        options["passengerSchedules"] = [
            {"id": row.pk, "label": f"{row.trip.origin} → {row.trip.destination} • {row.scheduled_departure:%Y-%m-%d %H:%M}"}
            for row in PassengerSchedule.objects.filter(status=PassengerSchedule.Status.OPEN, booking_open=True).select_related("trip")[:200]
        ]
    if profile.passenger_enabled and "transport.view_passengerbooking" in codes:
        options["passengerBookings"] = [
            {"id": row.pk, "label": f"{row.booking_reference} • {row.passenger_name}"}
            for row in PassengerBooking.objects.exclude(status=PassengerBooking.Status.CANCELLED)[:200]
        ]
    if profile.cargo_enabled and "transport.view_cargo" in codes:
        options["cargo"] = [
            {"id": row.pk, "label": f"{row.description} • {row.customer_name}"}
            for row in Cargo.objects.all()[:200]
        ]
        if payment_options:
            options["payableCargo"] = [
                {"id": row.pk, "label": f"{row.description} • {row.customer_name} • {row.balance_due}"}
                for row in Cargo.objects.filter(payment_status__in=(Cargo.PaymentStatus.UNPAID,Cargo.PaymentStatus.PARTIAL)).order_by("-cargo_date","-pk")[:200]
            ]
    return options


def _monthly_cash_summary(company, codes, profile, today):
    """Cash receipts by receipt date; never infer from cargo booking dates."""
    start = today.replace(day=1)
    scope = dict(company=company, is_archived=False)
    summary = {}
    cargo_allowed = profile.cargo_enabled and {"transport.view_cargo", "transport.view_cargopayment"}.issubset(codes)
    passenger_allowed = profile.passenger_enabled and {"transport.view_passengerbooking", "transport.view_passengerpayment"}.issubset(codes)
    if cargo_allowed:
        rows = list(CargoPayment.all_objects.filter(**scope, cargo__company=company, cargo__is_archived=False,
            payment_date__date__range=(start, today)).annotate(day=TruncDate("payment_date"))
            .values("day").annotate(total=Sum("amount")).order_by("day"))
        by_day = {row["day"].day: row["total"] for row in rows}
        summary["monthlyCargoCash"] = _money(sum(by_day.values(), ZERO))
        summary["cashTrend"] = [{"day": day, "cash": _money(by_day.get(day, ZERO))} for day in range(1, today.day+1)]
        debt = Cargo.all_objects.filter(**scope, agreed_amount__isnull=False).aggregate(total=Sum("agreed_amount")-Sum("amount_paid"))["total"]
        summary["cargoOutstanding"] = _money(debt)
    if passenger_allowed:
        summary["monthlyPassengerCash"] = _money(PassengerPayment.all_objects.filter(**scope, booking__company=company, booking__is_archived=False,
            payment_date__date__range=(start,today)).aggregate(total=Sum("amount"))["total"])
    if "transport.view_maintenance" in codes:
        summary["monthlyMaintenance"] = _money(Maintenance.all_objects.filter(**scope, service_date__range=(start,today)).aggregate(total=Sum("cost"))["total"])
    if "transport.view_trip" in codes:
        trips = Trip.all_objects.filter(**scope, departure_at__date__range=(start,today)).exclude(status=Trip.Status.CANCELLED).annotate(day=TruncDate("departure_at")).values("day").annotate(total=Count("pk")).order_by("day")
        summary["tripTrend"] = [{"day": row["day"].day, "trips": row["total"]} for row in trips]
    return summary


@never_cache
@require_GET
def transport_dashboard(request):
    company, _membership, codes, profile, error = _transport_context(request)
    if error is not None:
        return error
    access = {
        module: VIEW_PERMISSION[module] in codes
        for module in MODULES
    }
    if not any(access.values()):
        return _error(request, "permission_denied", "You do not have permission to view Transport modules.", 403)

    today = timezone.localdate()
    month_start = today.replace(day=1)
    can_view_vehicles = "transport.view_vehicle" in codes
    can_view_routes = "transport.view_route" in codes
    can_view_trips = "transport.view_trip" in codes
    can_view_cargo = "transport.view_cargo" in codes
    can_view_passengers = (
        "transport.view_passengerschedule" in codes
        or "transport.view_passengerbooking" in codes
    )
    can_view_expenses = "transport.view_expense" in codes
    vehicles = Vehicle.objects.aggregate(
        total=Count("pk"), active=Count("pk", filter=Q(is_active=True)),
    ) if can_view_vehicles else {"total": 0, "active": 0}
    vehicle_count, active_vehicle_count = vehicles["total"], vehicles["active"]
    route_active_count = Route.objects.filter(status=Route.Status.ACTIVE).count() if can_view_routes else 0
    trip_active_count = Trip.objects.filter(status=Trip.Status.IN_PROGRESS).count() if can_view_trips else 0
    cargo = Cargo.objects.aggregate(
        total=Count("pk"),
        unpaid=Count("pk", filter=Q(payment_status__in=(Cargo.PaymentStatus.UNPAID, Cargo.PaymentStatus.PARTIAL))),
        income=Sum("agreed_amount", filter=Q(cargo_date__range=(month_start, today))),
    ) if can_view_cargo else {"total": 0, "unpaid": 0, "income": ZERO}
    cargo_count, cargo_unpaid = cargo["total"], cargo["unpaid"]
    passenger_schedule_count = PassengerSchedule.objects.count() if can_view_passengers and "transport.view_passengerschedule" in codes else 0
    passenger = PassengerBooking.objects.exclude(status=PassengerBooking.Status.CANCELLED).aggregate(
        total=Count("pk"),
        income=Sum("fare_amount", filter=Q(created_at__date__range=(month_start, today))),
    ) if can_view_passengers and "transport.view_passengerbooking" in codes else {"total": 0, "income": ZERO}
    passenger_booking_count = passenger["total"]
    cargo_income = cargo["income"] or ZERO
    passenger_income = passenger["income"] or ZERO
    expense_total = (Expense.objects.filter(expense_date__date__gte=month_start, expense_date__date__lte=today).aggregate(total=Sum("amount"))["total"] or ZERO) if can_view_expenses else ZERO
    cash = _monthly_cash_summary(company, codes, profile, today)
    cash_summary = {key: value for key, value in cash.items() if key not in ("cashTrend", "tripTrend")}
    if ((not profile.cargo_enabled or "monthlyCargoCash" in cash_summary)
            and (not profile.passenger_enabled or "monthlyPassengerCash" in cash_summary)
            and can_view_expenses and "monthlyMaintenance" in cash_summary):
        cash_summary["monthlyCashProfit"] = _money(Decimal(cash_summary.get("monthlyCargoCash", "0"))
            + Decimal(cash_summary.get("monthlyPassengerCash", "0")) - expense_total - Decimal(cash_summary["monthlyMaintenance"]))
    return _json(request, {
        "ok": True,
        "recentActivity": _recent_transport(company, codes, profile),
        "cashTrend": cash.get("cashTrend", []),
        "tripTrend": cash.get("tripTrend", []),
        "company": {"id": str(company.public_id), "name": company.name, "businessType": company.business_type},
        "services": {"cargo": profile.cargo_enabled, "passenger": profile.passenger_enabled},
        "summary": {
            "vehicles": vehicle_count,
            "activeVehicles": active_vehicle_count,
            "activeRoutes": route_active_count,
            "activeTrips": trip_active_count,
            "cargoRecords": cargo_count,
            "unpaidCargo": cargo_unpaid,
            "passengerSchedules": passenger_schedule_count,
            "passengerBookings": passenger_booking_count,
            "monthlyCargoIncome": _money(cargo_income),
            "monthlyIncome": _money(cargo_income + passenger_income),
            "monthlyExpenses": _money(expense_total),
            "monthlyProfit": _money(cargo_income + passenger_income - expense_total),
            **cash_summary,
        },
        "moduleAccess": access,
        "createAccess": {module: CREATE_PERMISSION[module] in codes for module in MODULES},
        "canManageServices": "transport.manage_transport_services" in codes,
        "moduleServicesEnabled": {
            module: (True if service is None else (profile.cargo_enabled if service == "cargo" else profile.passenger_enabled))
            for module, (_model, _label, service) in MODULES.items()
        },
    })


def _report_summary(module, queryset, company, codes):
    today = timezone.localdate()
    start = today.replace(day=1)
    result = {"month": start.isoformat(), "count": queryset.count()}
    if module == "cargo":
        result.update(queryset.aggregate(paid=Count("pk", filter=Q(payment_status=Cargo.PaymentStatus.PAID)), debt=Count("pk", filter=Q(payment_status__in=(Cargo.PaymentStatus.PARTIAL, Cargo.PaymentStatus.UNPAID))), pending=Count("pk", filter=Q(payment_status=Cargo.PaymentStatus.PRICE_PENDING))))
        result["outstanding"] = _money(queryset.filter(agreed_amount__isnull=False).aggregate(total=Sum("agreed_amount")-Sum("amount_paid"))["total"])
        if "transport.view_cargopayment" not in codes:
            return result
        records = CargoPayment.all_objects.filter(company=company, is_archived=False, cargo_id__in=queryset.order_by().values("pk"), payment_date__date__range=(start,today))
        date_field, amount_field = "payment_date", "amount"
    elif module == "cargo_payments":
        records = queryset.filter(payment_date__date__range=(start,today))
        date_field, amount_field = "payment_date", "amount"
    elif module == "expenses":
        records = queryset.filter(expense_date__date__range=(start,today))
        date_field, amount_field = "expense_date", "amount"
    elif module == "maintenance":
        records = queryset.filter(service_date__range=(start,today))
        date_field, amount_field = "service_date", "cost"
    else:
        records = queryset.filter(started_at__date__range=(start,today))
        result["monthCount"] = records.count()
        result.update(queryset.aggregate(active=Count("pk",filter=Q(status=Route.Status.ACTIVE)), completed=Count("pk",filter=Q(status=Route.Status.COMPLETED))))
        rows = records.order_by().annotate(day=TruncDate("started_at")).values("day").annotate(total=Count("pk")).order_by("day")
        result["trend"] = [{"day": row["day"].day, "value": row["total"]} for row in rows]
        return result
    day_expression = date_field if date_field == "service_date" else TruncDate(date_field)
    rows = list(records.order_by().annotate(day=F(day_expression) if isinstance(day_expression,str) else day_expression).values("day").annotate(total=Sum(amount_field)).order_by("day"))
    result["monthAmount"] = _money(sum((row["total"] for row in rows), ZERO))
    result["trend"] = [{"day": row["day"].day, "value": _money(row["total"])} for row in rows]
    return result


def _recent_transport(company, codes, profile):
    entries = []
    sources = [("routes", Route, "created_at"), ("trips", Trip, "created_at"), ("cargo", Cargo, "created_at"), ("cargo_payments", CargoPayment, "payment_date"), ("expenses", Expense, "expense_date"), ("maintenance", Maintenance, "created_at")]
    for module, model, date_field in sources:
        if VIEW_PERMISSION[module] not in codes or (MODULES[module][2] == "cargo" and not profile.cargo_enabled):
            continue
        for item in _module_queryset(module).filter(company=company).order_by("-"+date_field,"-pk")[:5]:
            entry = _record_payload(module,item)
            entries.append((getattr(item,date_field), {"module":module,"title":entry["title"],"subtitle":entry["subtitle"],"date":entry["date"],"amount":entry["amount"] if module in ("cargo_payments","expenses","maintenance") else None}))
    return [item for _,item in sorted(entries,key=lambda pair: pair[0],reverse=True)[:8]]


@never_cache
@require_GET
def transport_module(request, module):
    if module not in MODULES:
        return _error(request, "module_not_found", "Transport module not found.", 404)
    _model, _label, service = MODULES[module]
    company, _membership, codes, profile, error = _transport_context(
        request, permission=VIEW_PERMISSION[module]
    )
    if error is not None:
        return error
    try:
        page = max(1, min(int(request.GET.get("page", "1")), 100000))
        limit = max(1, min(int(request.GET.get("limit", "25")), 100))
    except ValueError:
        return _error(request, "invalid_pagination", "Page and limit must be positive integers.", 400)
    include_options = request.GET.get("includeOptions", "1")
    if include_options not in ("0", "1"):
        return _error(request, "invalid_options", "includeOptions must be 0 or 1.", 400)
    query = (request.GET.get("q") or "").strip()[:100]
    queryset = _module_queryset(module).filter(company=company)
    report = request.GET.get("report") == "1"
    vehicle = request.GET.get("vehicle")
    payment_filter = request.GET.get("paymentStatus", "all")
    if payment_filter not in ("all", "paid", "debt", "pending") or (payment_filter != "all" and module != "cargo"):
        return _error(request, "invalid_filter", "Invalid payment filter.", 400)
    if payment_filter != "all":
        states = {"paid": (Cargo.PaymentStatus.PAID,), "debt": (Cargo.PaymentStatus.UNPAID, Cargo.PaymentStatus.PARTIAL), "pending": (Cargo.PaymentStatus.PRICE_PENDING,)}
        queryset = queryset.filter(payment_status__in=states[payment_filter])
    if report and module not in ("routes", "cargo", "expenses", "maintenance", "cargo_payments"):
        return _error(request, "invalid_report", "Report not supported.", 400)
    if vehicle:
        if not report or "transport.view_vehicle" not in codes:
            return _error(request, "permission_denied", "Vehicle report permission required.", 403)
        try:
            vehicle_id = int(vehicle)
            if not Vehicle.objects.filter(pk=vehicle_id, company=company).exists():
                return _error(request, "record_not_found", "Vehicle not found.", 404)
        except (ValueError, OverflowError):
            return _error(request, "invalid_vehicle", "Invalid vehicle.", 400)
        paths = {"routes": "vehicle_id", "cargo": "trip__route__vehicle_id", "maintenance": "vehicle_id", "cargo_payments": "cargo__trip__route__vehicle_id"}
        if module == "expenses":
            queryset = queryset.filter(Q(route__vehicle_id=vehicle_id) | Q(trip__route__vehicle_id=vehicle_id))
        else:
            queryset = queryset.filter(**{paths[module]: vehicle_id})
    if query:
        searchable = {
            "vehicles": Q(name__icontains=query) | Q(plate_number__icontains=query),
            "routes": Q(start_location__icontains=query) | Q(end_location__icontains=query) | Q(vehicle__plate_number__icontains=query),
            "trips": Q(origin__icontains=query) | Q(destination__icontains=query),
            "cargo": Q(description__icontains=query) | Q(customer_name__icontains=query) | Q(customer_phone__icontains=query),
            "cargo_sizes": Q(size__icontains=query),
            "cargo_payments": Q(reference__icontains=query) | Q(cargo__customer_name__icontains=query),
            "expenses": Q(description__icontains=query) | Q(expense_type__icontains=query),
            "maintenance": Q(service_type__icontains=query) | Q(vehicle__plate_number__icontains=query),
            "passenger_schedules": Q(trip__origin__icontains=query) | Q(trip__destination__icontains=query),
            "passenger_bookings": Q(passenger_name__icontains=query) | Q(passenger_phone__icontains=query) | Q(booking_reference__icontains=query),
            "passenger_payments": Q(reference__icontains=query) | Q(booking__passenger_name__icontains=query),
        }
        queryset = queryset.filter(searchable[module])
    report_summary = _report_summary(module, queryset, company, codes) if report else None
    total = queryset.count()
    start = (page - 1) * limit
    include_sizes = module == "cargo" and "transport.view_cargosize" in codes
    if include_sizes:
        queryset = queryset.prefetch_related(Prefetch("size_details", queryset=CargoSize.all_objects.filter(company=company, is_archived=False)))
    rows = list(queryset.order_by("-created_at", "-pk")[start:start + limit])
    service_enabled = service is None or (profile.cargo_enabled if service == "cargo" else profile.passenger_enabled)
    can_create = CREATE_PERMISSION[module] in codes and service_enabled
    if round_trip.enabled(profile):
        if module == "routes":
            can_create = False
        elif module == "trips":
            can_create = can_create and round_trip.REQUIRED.issubset(codes)
    return _json(request, {
        "ok": True,
        "module": module,
        "company": {"id": str(company.public_id), "name": company.name},
        "page": page,
        "limit": limit,
        "count": total,
        "hasNext": start + limit < total,
        "canCreate": can_create and not report,
        "canPayCargo": module == "cargo" and not report and service_enabled and "transport.add_cargopayment" in codes and "transport.view_cargopayment" in codes,
        "canSettleTimber": module == "cargo" and not report and service_enabled and "transport.change_cargo" in codes and "transport.add_cargosize" in codes and "transport.view_cargosize" in codes,
        "canChange": CHANGE_PERMISSION[module] in codes and service_enabled and not report,
        "canArchive": module == "vehicles" and "transport.delete_vehicle" in codes and not report,
        **({"reportSummary": report_summary} if report else {}),
        "serviceEnabled": service_enabled,
        "items": [_record_payload(module, row, include_sizes=include_sizes) for row in rows],
        "formOptions": _form_options(profile, codes, cargo_history=module == "cargo", payment_options=module == "cargo_payments") if include_options == "1" and not report else {},
    })


def _get_related(model, pk, company):
    if not pk:
        return None
    return model.all_objects.get(pk=int(pk), company=company, is_archived=False)


def _decimal(value, field, *, required=True, default=None):
    raw = (value or "").strip()
    if not raw:
        if required:
            raise ValidationError({field: "This value is required."})
        return default
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise ValidationError({field: "Enter a valid amount."}) from exc
    if not result.is_finite():
        raise ValidationError({field: "Enter a finite amount."})
    return result


def _integer(value, field, *, required=True, default=None):
    raw = (value or "").strip()
    if not raw:
        if required:
            raise ValidationError({field: "This value is required."})
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValidationError({field: "Enter a whole number."}) from exc


def _date_value(value, field, *, default=None):
    raw = (value or "").strip()
    if not raw:
        return default
    parsed = parse_date(raw)
    if parsed is None:
        raise ValidationError({field: "Use YYYY-MM-DD date format."})
    return parsed


def _datetime_value(value, field, *, default=None):
    raw = (value or "").strip()
    if not raw:
        return default
    parsed = parse_datetime(raw)
    if parsed is None:
        raise ValidationError({field: "Use ISO date-time format."})
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _transport_create_receipt(request, company, module):
    """Opt-in retry protection. Must be called inside the mutation transaction."""
    operation_id = (request.POST.get("operation_id") or "").strip()
    expected = (request.POST.get("expected_company_id") or "").strip()
    if (expected or operation_id) and expected != str(company.public_id):
        return None, _error(request, "company_context_changed", "Select the original company before retrying.", 409)
    if not operation_id:
        return None, None  # Legacy Build 14 requests remain compatible.
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", operation_id):
        return None, _error(request, "invalid_operation_id", "Invalid operation identifier.", 400)
    body = {key: request.POST.getlist(key) for key in sorted(request.POST)
            if key not in {"csrfmiddlewaretoken", "operation_id", "expected_company_id"}}
    fingerprint = hashlib.sha256(json.dumps([module, body], ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    # PostgreSQL serializes retries by the same account, including the audit receipt.
    actor = get_user_model().objects.select_for_update().only("is_active").get(pk=request.user.pk)
    if not actor.is_active:
        return None, _error(request, "account_inactive", "This account is inactive.", 403)
    prior = AuditLog.objects.filter(user=request.user, company_public_id=company.public_id,
        result="SUCCESS", metadata__transport_operation_id=operation_id).values_list("metadata", flat=True).first()
    if prior is not None:
        if prior.get("transport_fingerprint") != fingerprint:
            return None, _error(request, "operation_id_conflict", "Operation identifier is already bound to a different request.", 409)
        return None, _error(request, "duplicate_operation", "This action was already saved. Refresh to see the latest data.", 409)
    return {"transport_operation_id": operation_id, "transport_fingerprint": fingerprint}, None


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def transport_create(request, module):
    if module not in MODULES:
        return _error(request, "module_not_found", "Transport module not found.", 404)
    _model, _label, service = MODULES[module]
    company, _membership, codes, _profile, error = _transport_context(
        request, permission=CREATE_PERMISSION[module], service=service
    )
    if error is not None:
        return error
    receipt, receipt_error = _transport_create_receipt(request, company, module)
    if receipt_error is not None:
        return receipt_error
    data = request.POST
    try:
        if module == "vehicles":
            obj = Vehicle.objects.create(
                company=company,
                name=data.get("name", ""),
                plate_number=data.get("plate_number", ""),
                vehicle_type=data.get("vehicle_type", "Truck"),
                make=data.get("make", ""),
                model=data.get("model", ""),
                year=_integer(data.get("year"), "year", required=False),
                passenger_capacity=_integer(data.get("passenger_capacity"), "passenger_capacity", required=False, default=0),
                current_odometer=_integer(data.get("current_odometer"), "current_odometer", required=False, default=0),
                purchase_price=_decimal(data.get("purchase_price"), "purchase_price", required=False),
                current_value=_decimal(data.get("current_value"), "current_value", required=False),
                notes=data.get("notes", ""),
            )
        elif module == "routes":
            if round_trip.enabled(_profile):
                return _error(request, "automatic_route", "Start a trip to open a journey automatically.", 409)
            vehicle = _get_related(Vehicle, data.get("vehicle_id"), company)
            obj = Route.objects.create(company=company, vehicle=vehicle, start_location=data.get("start_location") or "Ikonda", end_location=data.get("end_location", ""), notes=data.get("notes", ""))
        elif module == "trips":
            if round_trip.enabled(_profile):
                if not round_trip.REQUIRED.issubset(codes):
                    return _error(request, "permission_denied", "Journey and trip permissions are required.", 403)
                obj = round_trip.depart(company, data.get("vehicle_id"), data.get("origin"), data.get("destination"), data.get("notes", ""), close_previous=data.get("close_previous") == "1")
            else:
                route = _get_related(Route, data.get("route_id"), company)
                obj = Trip.objects.create(company=company, route=route, origin=data.get("origin", ""), destination=data.get("destination", ""), notes=data.get("notes", ""))
        elif module == "cargo":
            trip = _get_related(Trip, data.get("trip_id"), company)
            intake = data.get("intake") == "1"
            if intake and data.get("cargo_type") not in {"TIMBER", "BANANAS", "GOODS", "OTHER"}:
                raise ValidationError({"cargo_type": "Select a cargo type."})
            pricing_method = Cargo.PricingMethod.TOTAL if intake else data.get("pricing_method", Cargo.PricingMethod.PER_UNIT)
            quantity = _decimal(data.get("quantity", "1"), "quantity")
            unit_price = _decimal(data.get("unit_price", "0"), "unit_price")
            original = _decimal(data.get("original_amount"), "original_amount", required=False, default=ZERO)
            agreed = _decimal(data.get("agreed_amount"), "agreed_amount", required=False)
            obj = Cargo(company=company, trip=trip, cargo_date=_date_value(data.get("cargo_date"), "cargo_date", default=timezone.localdate()), cargo_type=data.get("cargo_type", "GOODS"), description=data.get("description", ""), quantity=quantity, unit=data.get("unit", "Unit"), pricing_method=pricing_method, unit_price=unit_price, original_amount=original, agreed_amount=agreed, destination=data.get("destination", ""), customer_name=data.get("customer_name", ""), customer_phone=data.get("customer_phone", ""), notes=data.get("notes", ""))
            intake_sizes = {}
            if intake:
                obj.quantity = Decimal("1")  # internal placeholder, not displayed for pending cargo
                obj.unit = "Unit"
                obj.unit_price = obj.original_amount = ZERO
                obj.agreed_amount = None
                if obj.cargo_type == "TIMBER":
                    obj.description = "TIMBER"
                if not obj.description.strip() or not obj.customer_name.strip():
                    raise ValidationError("Cargo and customer name are required.")
                if data.get("sizes") is not None:
                    if obj.cargo_type != "TIMBER" or not {"transport.add_cargosize", "transport.view_cargosize"}.issubset(codes):
                        raise ValidationError("Permission to record timber sizes is required.")
                    intake_sizes = _clean_timber_sizes(data.get("sizes"))
                    obj.quantity = sum(intake_sizes.values())
                    obj.unit = "pieces"
            # Late entry is explicit, audited and never allowed for cancelled/stopped trips.
            obj._allow_completed_trip_entry = data.get("late_entry") == "1"
            obj.save()
            for size, quantity in intake_sizes.items():
                CargoSize.objects.create(company=company, cargo=obj, size=size, quantity=quantity)
            if trip.status == Trip.Status.COMPLETED:
                route = Route.all_objects.select_for_update().get(pk=trip.route_id, company=company, is_archived=False)
                if route.status == Route.Status.COMPLETED:
                    route.calculate_financials()
                    route.save(update_fields=("total_income", "total_expenses", "profit", "updated_at"))
        elif module == "cargo_sizes":
            cargo = _get_related(Cargo, data.get("cargo_id"), company)
            obj = CargoSize.objects.create(company=company, cargo=cargo, size=data.get("size", ""), quantity=_integer(data.get("quantity"), "quantity"))
        elif module == "cargo_payments":
            cargo = _get_related(Cargo, data.get("cargo_id"), company)
            payment_date = _datetime_value(data.get("payment_date"), "payment_date", default=timezone.now())
            if payment_date > timezone.now():
                raise ValidationError({"payment_date": "Payment date cannot be in the future."})
            obj = CargoPayment.objects.create(company=company, cargo=cargo, amount=_decimal(data.get("amount"), "amount"), payment_date=payment_date, reference=data.get("reference", ""), notes=data.get("notes", ""))
        elif module == "expenses":
            route = _get_related(Route, data.get("route_id"), company) if data.get("route_id") else None
            trip = _get_related(Trip, data.get("trip_id"), company) if data.get("trip_id") else None
            obj = Expense.objects.create(company=company, category=data.get("category", Expense.Category.VEHICLE), expense_type=data.get("expense_type", ""), route=route, trip=trip, expense_date=_datetime_value(data.get("expense_date"), "expense_date", default=timezone.now()), description=data.get("description", ""), amount=_decimal(data.get("amount"), "amount"), notes=data.get("notes", ""))
        elif module == "maintenance":
            vehicle = _get_related(Vehicle, data.get("vehicle_id"), company)
            obj = Maintenance.objects.create(company=company, vehicle=vehicle, service_date=_date_value(data.get("service_date"), "service_date", default=timezone.localdate()), service_type=data.get("service_type", ""), description=data.get("description", ""), cost=_decimal(data.get("cost"), "cost"), odometer=_integer(data.get("odometer"), "odometer", required=False), next_service_date=_date_value(data.get("next_service_date"), "next_service_date"), next_service_odometer=_integer(data.get("next_service_odometer"), "next_service_odometer", required=False), notes=data.get("notes", ""))
        elif module == "passenger_schedules":
            trip = _get_related(Trip, data.get("trip_id"), company)
            obj = PassengerSchedule.objects.create(company=company, trip=trip, scheduled_departure=_datetime_value(data.get("scheduled_departure"), "scheduled_departure"), expected_arrival=_datetime_value(data.get("expected_arrival"), "expected_arrival"), fare_amount=_decimal(data.get("fare_amount"), "fare_amount"), seat_capacity=_integer(data.get("seat_capacity"), "seat_capacity"), booking_open=(data.get("booking_open", "true").lower() == "true"), notes=data.get("notes", ""))
        elif module == "passenger_bookings":
            schedule = _get_related(PassengerSchedule, data.get("schedule_id"), company)
            obj = PassengerBooking.objects.create(company=company, schedule=schedule, passenger_name=data.get("passenger_name", ""), passenger_phone=data.get("passenger_phone", ""), seat_number=data.get("seat_number", ""), fare_amount=schedule.fare_amount, notes=data.get("notes", ""))
        elif module == "passenger_payments":
            booking = _get_related(PassengerBooking, data.get("booking_id"), company)
            obj = PassengerPayment.objects.create(company=company, booking=booking, amount=_decimal(data.get("amount"), "amount"), reference=data.get("reference", ""), notes=data.get("notes", ""))
        else:
            return _error(request, "module_not_supported", "This Transport module is not writable yet.", 405)
    except (ValidationError, ObjectDoesNotExist, ValueError, InvalidOperation) as exc:
        transaction.set_rollback(True)
        messages = getattr(exc, "messages", None) or [str(exc)]
        return _json(request, {"ok": False, "code": "validation_error", "message": " ".join(messages)}, status_code=400)
    except IntegrityError:
        transaction.set_rollback(True)
        return _error(request, "record_conflict", "The record conflicts with existing data. Check duplicate numbers or seat allocations.", 409)
    if receipt is not None or (module == "cargo" and data.get("late_entry") == "1" and obj.trip.status == Trip.Status.COMPLETED):
        create_audit_log(request, "CREATE", type(obj).__name__, obj.pk,
                         "Transport mobile operation committed.", metadata=receipt or {"late_entry": True}, company=company)
    return _json(request, {"ok": True, "module": module, "item": _record_payload(module, obj)}, status_code=201)



# Vehicle edits are deliberately separate from lifecycle actions and money
# operations. The client never receives a generic model-mutation endpoint.
def _live_vehicle(company, record_id, *, lock=False):
    queryset = Vehicle.all_objects.filter(
        pk=record_id, company=company, is_archived=False
    )
    if lock:
        queryset = queryset.select_for_update()
    return queryset.first()


def _vehicle_form_values(vehicle):
    return {
        "name": vehicle.name,
        "plate_number": vehicle.plate_number,
        "vehicle_type": vehicle.vehicle_type,
        "make": vehicle.make,
        "model": vehicle.model,
        "year": "" if vehicle.year is None else str(vehicle.year),
        "passenger_capacity": str(vehicle.passenger_capacity),
        "current_odometer": str(vehicle.current_odometer),
        "purchase_price": "" if vehicle.purchase_price is None else str(vehicle.purchase_price),
        "current_value": "" if vehicle.current_value is None else str(vehicle.current_value),
        "notes": vehicle.notes,
    }


@never_cache
@require_GET
def transport_vehicle_detail(request, record_id):
    company, _membership, codes, _profile, error = _transport_context(
        request, permission=VIEW_PERMISSION["vehicles"]
    )
    if error is not None:
        return error
    vehicle = _live_vehicle(company, record_id)
    if vehicle is None:
        return _error(request, "record_not_found", "Vehicle not found.", 404)
    return _json(request, {
        "ok": True,
        "company": {"id": str(company.public_id)},
        "item": _record_payload("vehicles", vehicle),
        "fields": _vehicle_form_values(vehicle),
        "updatedAt": vehicle.updated_at.isoformat(),
        "canEdit": "transport.change_vehicle" in codes,
        "canArchive": "transport.delete_vehicle" in codes,
    })


def _require_vehicle_version(request, vehicle):
    """Return an error for a stale/missing version (otherwise None)."""
    provided = (request.POST.get("expected_updated_at") or "").strip()
    if not provided:
        return _error(request, "version_required", "Reload the vehicle before changing it.", 400)
    if provided != vehicle.updated_at.isoformat():
        return _error(request, "record_changed", "Vehicle changed. Reload before continuing.", 409)
    return None


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def transport_vehicle_update(request, record_id):
    company, _membership, _codes, _profile, error = _transport_context(
        request, permission=CHANGE_PERMISSION["vehicles"]
    )
    if error is not None:
        return error
    vehicle = _live_vehicle(company, record_id, lock=True)
    if vehicle is None:
        return _error(request, "record_not_found", "Vehicle not found.", 404)
    version_error = _require_vehicle_version(request, vehicle)
    if version_error is not None:
        return version_error

    fields = {
        "name", "plate_number", "vehicle_type", "make", "model",
        "year", "passenger_capacity", "current_odometer",
        "purchase_price", "current_value", "notes",
    }
    unexpected = set(request.POST) - fields - {"expected_updated_at", "csrfmiddlewaretoken"}
    if unexpected:
        return _error(request, "invalid_fields", "Unsupported vehicle fields supplied.", 400)
    if not any(key in request.POST for key in fields):
        return _error(request, "empty_update", "No vehicle fields were supplied.", 400)

    try:
        values = {}
        for key in ("name", "plate_number", "vehicle_type", "make", "model", "notes"):
            if key in request.POST:
                values[key] = request.POST[key]
        for key in ("year", "passenger_capacity", "current_odometer"):
            if key in request.POST:
                values[key] = _integer(
                    request.POST.get(key), key, required=False,
                    default=None if key == "year" else 0,
                )
        for key in ("purchase_price", "current_value"):
            if key in request.POST:
                values[key] = _decimal(request.POST.get(key), key, required=False)
        capacity = values.get("passenger_capacity", vehicle.passenger_capacity)
        if PassengerSchedule.all_objects.filter(
            company=company, trip__route__vehicle=vehicle, is_archived=False,
            seat_capacity__gt=capacity,
        ).exists():
            return _error(
                request, "vehicle_capacity_in_use",
                "Passenger capacity cannot be lower than an existing schedule's seats.", 409,
            )
        odometer = values.get("current_odometer", vehicle.current_odometer)
        if odometer < vehicle.current_odometer and Maintenance.all_objects.filter(
            company=company, vehicle=vehicle, is_archived=False,
        ).exists():
            return _error(
                request, "vehicle_odometer_conflict",
                "Odometer cannot be reduced after maintenance history exists.", 409,
            )
        for key, value in values.items():
            setattr(vehicle, key, value)
        vehicle.save()
    except (ValidationError, ValueError, InvalidOperation) as exc:
        messages = getattr(exc, "messages", None) or [str(exc)]
        return _json(request, {
            "ok": False, "code": "validation_error", "message": " ".join(messages),
        }, status_code=400)
    except IntegrityError:
        return _error(request, "record_conflict", "Vehicle details conflict with existing records.", 409)

    CompanyAuditEvent.objects.create(
        company=company, actor=request.user, action="TRANSPORT_VEHICLE_UPDATED",
        description="Vehicle details updated through the mobile API.",
        metadata={"vehicle_id": vehicle.pk, "fields": sorted(values)},
    )
    return _json(request, {
        "ok": True, "item": _record_payload("vehicles", vehicle),
        "updatedAt": vehicle.updated_at.isoformat(),
    })


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def transport_vehicle_archive(request, record_id):
    company, _membership, _codes, _profile, error = _transport_context(
        request, permission="transport.delete_vehicle"
    )
    if error is not None:
        return error
    vehicle = _live_vehicle(company, record_id, lock=True)
    if vehicle is None:
        return _error(request, "record_not_found", "Vehicle not found.", 404)
    version_error = _require_vehicle_version(request, vehicle)
    if version_error is not None:
        return version_error
    # An archived vehicle with linked history would break normal historical
    # navigation; preserve references instead of offering dangerous deletion.
    if Route.all_objects.filter(company=company, vehicle=vehicle).exists() or \
            Maintenance.all_objects.filter(company=company, vehicle=vehicle).exists():
        return _error(request, "vehicle_has_history", "This vehicle has route or maintenance history and cannot be archived.", 409)
    vehicle.archive()  # Soft archive, never a physical DELETE.
    CompanyAuditEvent.objects.create(
        company=company, actor=request.user, action="TRANSPORT_VEHICLE_ARCHIVED",
        description="Unlinked vehicle archived through the mobile API.",
        metadata={"vehicle_id": vehicle.pk},
    )
    return _json(request, {"ok": True, "archived": True, "id": record_id})

@never_cache
@require_GET
def transport_services(request):
    company, _membership, codes, profile, error = _transport_context(request)
    if error is not None:
        return error
    return _json(request, {
        "ok": True,
        "services": {"cargo": profile.cargo_enabled, "passenger": profile.passenger_enabled},
        "canManage": "transport.manage_transport_services" in codes,
    })


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def update_transport_services(request):
    company, _membership, codes, profile, error = _transport_context(request)
    if error is not None:
        return error
    if "transport.manage_transport_services" not in codes:
        return _error(request, "permission_denied", "Only an authorized company owner can change Transport services.", 403)
    cargo_enabled = (request.POST.get("cargo_enabled", "false").lower() == "true")
    passenger_enabled = (request.POST.get("passenger_enabled", "false").lower() == "true")
    profile.cargo_enabled = cargo_enabled
    profile.passenger_enabled = passenger_enabled
    try:
        profile.full_clean()
    except ValidationError as exc:
        return _json(request, {"ok": False, "code": "invalid_services", "message": " ".join(exc.messages)}, status_code=400)
    profile.save(update_fields=("cargo_enabled", "passenger_enabled", "updated_at"))
    CompanyAuditEvent.objects.create(
        company=company,
        actor=request.user,
        action="TRANSPORT_SERVICES_UPDATED",
        description="Transport services configuration updated.",
        metadata={"cargo_enabled": cargo_enabled, "passenger_enabled": passenger_enabled},
    )
    return _json(request, {"ok": True, "services": {"cargo": cargo_enabled, "passenger": passenger_enabled}})


def _clean_timber_sizes(raw):
    try:
        sizes = json.loads(raw)
    except (ValueError, TypeError):
        raise ValidationError("Invalid timber sizes.")
    if not isinstance(sizes,list) or not 1 <= len(sizes) <= 100:
        raise ValidationError("Enter 1 to 100 timber sizes.")
    clean = {}
    for row in sizes:
        if not isinstance(row,dict):
            raise ValidationError("Invalid timber size.")
        size = str(row.get("size", "")).strip()
        quantity = row.get("quantity")
        if not size or len(size)>50 or type(quantity) is not int or not 1<=quantity<=10000000 or size in clean:
            raise ValidationError("Enter unique sizes and positive whole quantities.")
        clean[size] = quantity
    return clean


@transaction.atomic
def _settle_pending_cargo(company, cargo, data, codes):
    if cargo.agreed_amount is not None or cargo.amount_paid != ZERO:
        raise ValidationError("Cargo price has already been set.")
    amount = _decimal(data.get("agreed_amount"), "agreed_amount")
    if amount < ZERO:
        raise ValidationError("Amount must not be negative.")
    if cargo.cargo_type == "TIMBER":
        if not {"transport.add_cargosize", "transport.view_cargosize"}.issubset(codes):
            raise ValidationError("Permission to record timber sizes is required.")
        clean = _clean_timber_sizes(data.get("sizes", "[]"))
        existing = list(CargoSize.all_objects.select_for_update().filter(company=company,cargo=cargo))
        if existing:
            if any(row.is_archived for row in existing) or {row.size:row.quantity for row in existing} != clean:
                raise ValidationError("Existing timber sizes differ; review them before settlement.")
        else:
            for size, quantity in clean.items():
                CargoSize.objects.create(company=company,cargo=cargo,size=size,quantity=quantity)
        cargo.quantity = sum(clean.values())
        cargo.unit = "pieces"
    cargo.pricing_method = Cargo.PricingMethod.TOTAL
    cargo.unit_price = ZERO
    cargo.original_amount = cargo.agreed_amount = amount
    cargo.delivered = True
    cargo.delivered_at = cargo.delivered_at or timezone.now()
    cargo.save()
    route = Route.all_objects.select_for_update().get(pk=cargo.trip.route_id,company=company,is_archived=False)
    if route.status == Route.Status.COMPLETED:
        route.calculate_financials()
        route.save(update_fields=("total_income","total_expenses","profit","updated_at"))
    return cargo


@never_cache
@require_POST
@csrf_protect
@transaction.atomic
def transport_action(request, module, record_id, action):
    if module not in MODULES or module not in {"routes", "trips", "cargo", "passenger_schedules", "passenger_bookings"}:
        return _error(request, "action_not_supported", "This action is not supported.", 404)
    _model, _label, service = MODULES[module]
    company, _membership, codes, _profile, error = _transport_context(
        request, permission=CHANGE_PERMISSION[module], service=service
    )
    if error is not None:
        return error
    model = MODULES[module][0]
    try:
        if round_trip.enabled(_profile):
            if module == "routes" and action in {"start", "complete"}:
                return _error(request, "automatic_route", "Start a trip or confirm arrival at Ikonda instead.", 409)
            if module == "trips" and action == "complete":
                if "transport.change_route" not in codes:
                    return _error(request, "permission_denied", "Journey change permission is required.", 403)
                obj = round_trip.arrive(company, record_id)
                return _json(request, {"ok": True, "module": module, "item": _record_payload(module, obj)})
        obj = model.objects.select_for_update().get(pk=record_id, company=company)
        if module == "routes":
            if action == "start": obj.start_route()
            elif action == "complete": obj.complete_route(request.POST.get("end_location"))
            elif action == "stop": obj.stop_route(request.POST.get("reason", ""))
            elif action == "cancel": obj.cancel_route(request.POST.get("reason", ""))
            elif action == "refresh_financials":
                obj.calculate_financials()
                obj.save(update_fields=("total_income", "total_expenses", "profit", "updated_at"))
            else: return _error(request, "action_not_supported", "Route action not supported.", 404)
        elif module == "trips":
            if action == "start": obj.start_trip()
            elif action == "complete": obj.complete_trip()
            elif action == "stop": obj.stop_trip(request.POST.get("reason", ""))
            elif action == "cancel": obj.cancel_trip(request.POST.get("reason", ""))
            else: return _error(request, "action_not_supported", "Trip action not supported.", 404)
        elif module == "cargo" and action == "settle":
            obj = _settle_pending_cargo(company, obj, request.POST, codes)
            create_audit_log(request, "UPDATE", "Cargo", obj.pk, "Cargo price and delivery confirmed.", company=company)
        elif module == "cargo" and action == "deliver":
            obj.mark_delivered()
        elif module == "passenger_bookings" and action == "cancel":
            obj.cancel()
        elif module == "passenger_bookings" and action == "board":
            obj.mark_boarded()
        elif module == "passenger_bookings" and action == "no_show":
            obj.mark_no_show()
        elif module == "passenger_schedules" and action in {"close", "open"}:
            obj.booking_open = action == "open"
            obj.status = PassengerSchedule.Status.OPEN if action == "open" else PassengerSchedule.Status.CLOSED
            obj.save(update_fields=("booking_open", "status", "updated_at"))
        else:
            return _error(request, "action_not_supported", "This action is not supported for the selected module.", 404)
    except ObjectDoesNotExist:
        return _error(request, "record_not_found", "Record not found.", 404)
    except ValidationError as exc:
        transaction.set_rollback(True)
        return _json(request, {"ok": False, "code": "invalid_transition", "message": " ".join(exc.messages)}, status_code=400)
    return _json(request, {"ok": True, "module": module, "item": _record_payload(module, obj)})


@never_cache
@require_GET
def passenger_manifest(request, schedule_id):
    company, _membership, _codes, _profile, error = _transport_context(
        request, permission="transport.view_passengerbooking"
    )
    if error is not None:
        return error
    try:
        schedule = PassengerSchedule.objects.select_related(
            "trip", "trip__route", "trip__route__vehicle"
        ).get(pk=schedule_id, company=company)
    except PassengerSchedule.DoesNotExist:
        return _error(request, "schedule_not_found", "Passenger schedule not found.", 404)
    bookings = PassengerBooking.objects.filter(
        schedule=schedule
    ).exclude(status=PassengerBooking.Status.CANCELLED).order_by("seat_number")
    return _json(request, {
        "ok": True,
        "schedule": {
            "id": schedule.pk,
            "route": f"{schedule.trip.origin} → {schedule.trip.destination}",
            "vehicle": schedule.trip.route.vehicle.plate_number,
            "departure": schedule.scheduled_departure.isoformat(),
            "status": schedule.status,
            "seatCapacity": schedule.seat_capacity,
            "seatsRemaining": schedule.seats_remaining,
            "fareAmount": _money(schedule.fare_amount),
        },
        "count": bookings.count(),
        "passengers": [
            {
                "id": booking.pk,
                "reference": booking.booking_reference,
                "name": booking.passenger_name,
                "phone": booking.passenger_phone,
                "seat": booking.seat_number,
                "status": booking.status,
                "paymentStatus": booking.payment_status,
                "fare": _money(booking.fare_amount),
                "amountPaid": _money(booking.amount_paid),
            }
            for booking in bookings
        ],
    })
