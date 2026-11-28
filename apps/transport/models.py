"""Company-scoped transport operations for Home Management.

The models reuse the existing Company/CompanyOwnedModel boundary.  They do
not create a second User, Company, role, or authentication system.
"""

import uuid
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.companies.models import (
    Company,
    CompanyOwnedModel,
    CompanyScopedManager,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _cargo_line_amount(quantity, unit_price):
    """Round a computed line total once, consistently with Pig money rules."""
    try:
        value = Decimal(str(quantity)) * Decimal(str(unit_price))
        if not value.is_finite():
            raise InvalidOperation
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    except (DecimalException, ValueError, TypeError) as exc:
        raise ValidationError({"original_amount": "Enter a valid monetary amount."}) from exc


def generate_booking_reference():
    return f"HM-{uuid.uuid4().hex[:10].upper()}"


class TransportProfile(models.Model):
    """Service switches for a Transport company.

    Turning a service off never deletes its records. The API blocks new writes
    for that service while retaining historical reads and reporting.
    """

    company = models.OneToOneField(
        Company,
        on_delete=models.PROTECT,
        related_name="transport_profile",
    )
    cargo_enabled = models.BooleanField(default=True)
    passenger_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("manage_transport_services", "Can manage Transport services"),
        ]

    def clean(self):
        super().clean()
        if self.company_id and self.company.business_type != Company.BusinessType.TRANSPORT:
            raise ValidationError({"company": "Transport services require a Transport company."})
        if not self.cargo_enabled and not self.passenger_enabled:
            raise ValidationError("At least one Transport service must remain enabled.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Transport services: {self.company.name}"


class TransportRecord(CompanyOwnedModel):
    """Timestamped base for tenant-owned transport records."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CompanyScopedManager()

    class Meta:
        abstract = True
        default_manager_name = "objects"
        base_manager_name = "all_objects"


class Vehicle(TransportRecord):
    name = models.CharField(max_length=100)
    plate_number = models.CharField(max_length=20)
    vehicle_type = models.CharField(max_length=50, default="Truck")
    make = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=50, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    purchase_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(ZERO)],
    )
    current_value = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(ZERO)],
    )
    current_odometer = models.PositiveIntegerField(default=0)
    passenger_capacity = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("plate_number",)
        constraints = [
            models.UniqueConstraint(
                fields=("company", "plate_number"),
                condition=Q(is_archived=False),
                name="tr_vehicle_plate_live_uq",
            ),
        ]
        permissions = [
            ("manage_vehicle_records", "Can manage vehicle records"),
        ]

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        self.plate_number = (self.plate_number or "").strip().upper()
        self.vehicle_type = (self.vehicle_type or "").strip()
        self.make = (self.make or "").strip()
        self.model = (self.model or "").strip()
        if not self.name:
            raise ValidationError({"name": "Vehicle name is required."})
        if not self.plate_number:
            raise ValidationError({"plate_number": "Registration number is required."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def last_service(self):
        return self.maintenance_records.filter(is_archived=False).order_by("-service_date", "-pk").first()

    def __str__(self):
        return f"{self.name} - {self.plate_number}"


class Route(TransportRecord):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        ACTIVE = "ACTIVE", "Active"
        COMPLETED = "COMPLETED", "Completed"
        STOPPED = "STOPPED", "Stopped"
        CANCELLED = "CANCELLED", "Cancelled"

    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="routes")
    route_number = models.PositiveIntegerField(editable=False)
    start_location = models.CharField(max_length=100, default="Ikonda")
    end_location = models.CharField(max_length=100, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.TextField(blank=True)
    total_income = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_expenses = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    profit = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-route_number",)
        constraints = [
            models.UniqueConstraint(fields=("vehicle", "route_number"), name="tr_route_vehicle_no_uq"),
        ]

    def clean(self):
        super().clean()
        if not self.vehicle_id:
            raise ValidationError({"vehicle": "A vehicle is required."})
        if self.vehicle.company_id != self.company_id:
            raise ValidationError({"vehicle": "The vehicle must belong to this company."})
        if self._state.adding and not self.vehicle.is_active:
            raise ValidationError({"vehicle": "An inactive vehicle cannot be assigned to a new route."})
        self.start_location = (self.start_location or "").strip()
        self.end_location = (self.end_location or "").strip()
        if self.start_location and self.end_location and self.start_location.casefold() == self.end_location.casefold():
            # A round trip is valid only after the last recorded trip returned home.
            last = (Trip.all_objects.filter(company_id=self.company_id, route_id=self.pk, is_archived=False)
                    .exclude(status=Trip.Status.CANCELLED).order_by("-trip_number").first()) if self.pk and self.status == self.Status.COMPLETED else None
            if not (last and last.status == Trip.Status.COMPLETED and last.destination.strip().casefold() == self.start_location.casefold() and not self._has_open_trips()):
                raise ValidationError({"end_location": "A round trip requires confirmed arrival at the starting location."})
        if self.total_income < ZERO or self.total_expenses < ZERO:
            raise ValidationError("Route income and expenses cannot be negative.")

    def save(self, *args, **kwargs):
        self.full_clean()
        if not self.route_number:
            with transaction.atomic():
                Vehicle.all_objects.select_for_update().get(pk=self.vehicle_id, company_id=self.company_id)
                last = Route.all_objects.filter(vehicle_id=self.vehicle_id).order_by("-route_number").first()
                self.route_number = last.route_number + 1 if last else 1
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def calculate_total_income(self):
        cargo_total = sum(
            (amount or ZERO for amount in Cargo.all_objects.filter(
                company_id=self.company_id, is_archived=False, trip__route=self
            ).values_list("agreed_amount", flat=True)),
            ZERO,
        )
        passenger_total = sum(
            (amount or ZERO for amount in PassengerBooking.all_objects.filter(
                company_id=self.company_id,
                is_archived=False,
                schedule__trip__route=self,
            ).exclude(status=PassengerBooking.Status.CANCELLED).values_list("fare_amount", flat=True)),
            ZERO,
        )
        return cargo_total + passenger_total

    def calculate_total_expenses(self):
        return Expense.all_objects.filter(
            company_id=self.company_id,
            is_archived=False,
        ).filter(Q(route=self) | Q(trip__route=self)).aggregate(total=Sum("amount"))["total"] or ZERO

    def calculate_financials(self):
        self.total_income = self.calculate_total_income()
        self.total_expenses = self.calculate_total_expenses()
        self.profit = self.total_income - self.total_expenses
        return self.profit

    def get_trip_count(self):
        return Trip.all_objects.filter(company_id=self.company_id, is_archived=False, route=self).count()

    def get_unpaid_cargo(self):
        return Cargo.all_objects.filter(
            company_id=self.company_id, is_archived=False, trip__route=self,
            agreed_amount__gt=ZERO,
        ).exclude(payment_status=Cargo.PaymentStatus.PAID)

    def _has_open_trips(self):
        return Trip.all_objects.filter(
            company_id=self.company_id, is_archived=False, route=self,
        ).exclude(status__in=(Trip.Status.COMPLETED, Trip.Status.CANCELLED, Trip.Status.STOPPED)).exists()

    def start_route(self):
        if self.status != self.Status.PLANNED:
            raise ValidationError("Only a planned route can be started.")
        if not self.vehicle.is_active:
            raise ValidationError("An inactive vehicle cannot start a route.")
        self.status = self.Status.ACTIVE
        self.started_at = timezone.now()
        self.save(update_fields=("status", "started_at", "updated_at"))

    def complete_route(self, end_location=None):
        if self.status != self.Status.ACTIVE:
            raise ValidationError("Only an active route can be completed.")
        if end_location:
            self.end_location = end_location.strip()
        if not self.end_location:
            raise ValidationError("End location is required.")
        if self.start_location and self.end_location.casefold() == self.start_location.casefold():
            raise ValidationError("End location cannot equal the start location.")
        if self._has_open_trips():
            raise ValidationError("Complete, stop, or cancel all open trips before completing this route.")
        self.completed_at = timezone.now()
        self.status = self.Status.COMPLETED
        self.calculate_financials()
        self.save(update_fields=("end_location", "completed_at", "status", "total_income", "total_expenses", "profit", "updated_at"))

    def stop_route(self, reason):
        reason = (reason or "").strip()
        if self.status != self.Status.ACTIVE or not reason:
            raise ValidationError("An active route and a stop reason are required.")
        if self._has_open_trips():
            raise ValidationError("Complete, stop, or cancel all open trips before stopping this route.")
        self.stop_reason = reason
        self.stopped_at = timezone.now()
        self.status = self.Status.STOPPED
        self.calculate_financials()
        self.save(update_fields=("stop_reason", "stopped_at", "status", "total_income", "total_expenses", "profit", "updated_at"))

    def cancel_route(self, reason):
        reason = (reason or "").strip()
        if self.status in (self.Status.COMPLETED, self.Status.CANCELLED) or not reason:
            raise ValidationError("This route cannot be cancelled or a reason is missing.")
        if self._has_open_trips():
            raise ValidationError("Complete, stop, or cancel all open trips before cancelling this route.")
        self.status = self.Status.CANCELLED
        self.stop_reason = reason
        self.stopped_at = timezone.now()
        self.calculate_financials()
        self.save(update_fields=("status", "stop_reason", "stopped_at", "total_income", "total_expenses", "profit", "updated_at"))

    def __str__(self):
        return f"Route #{self.route_number}: {self.start_location} → {self.end_location or 'In progress'}"


class Trip(TransportRecord):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"
        STOPPED = "STOPPED", "Stopped"
        CANCELLED = "CANCELLED", "Cancelled"

    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name="trips")
    trip_number = models.PositiveIntegerField(editable=False)
    origin = models.CharField(max_length=100)
    destination = models.CharField(max_length=100)
    departure_at = models.DateTimeField(null=True, blank=True)
    arrival_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("route_id", "trip_number")
        constraints = [models.UniqueConstraint(fields=("route", "trip_number"), name="tr_trip_route_no_uq")]

    def clean(self):
        super().clean()
        if not self.route_id:
            raise ValidationError({"route": "A route is required."})
        if self.route.company_id != self.company_id:
            raise ValidationError({"route": "The route must belong to this company."})
        if self._state.adding and self.route.status not in (Route.Status.PLANNED, Route.Status.ACTIVE):
            raise ValidationError({"route": "New trips can only be created on planned or active routes."})
        self.origin = (self.origin or "").strip()
        self.destination = (self.destination or self.route.end_location or "").strip()
        if self.origin and self.destination and self.origin.casefold() == self.destination.casefold():
            raise ValidationError({"destination": "Destination cannot equal origin."})

    def save(self, *args, **kwargs):
        self.full_clean()
        if not self.trip_number:
            with transaction.atomic():
                Route.all_objects.select_for_update().get(pk=self.route_id, company_id=self.company_id)
                last = Trip.all_objects.filter(route_id=self.route_id).order_by("-trip_number").first()
                self.trip_number = last.trip_number + 1 if last else 1
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def start_trip(self):
        if self.status != self.Status.PLANNED or self.route.status != Route.Status.ACTIVE:
            raise ValidationError("A planned trip can start only on an active route.")
        self.status = self.Status.IN_PROGRESS
        self.departure_at = self.departure_at or timezone.now()
        self.save(update_fields=("status", "departure_at", "updated_at"))
        schedule = PassengerSchedule.all_objects.filter(
            company_id=self.company_id, trip=self, is_archived=False
        ).first()
        if schedule:
            schedule.status = PassengerSchedule.Status.DEPARTED
            schedule.booking_open = False
            schedule.save(update_fields=("status", "booking_open", "updated_at"))

    def complete_trip(self):
        if self.status != self.Status.IN_PROGRESS:
            raise ValidationError("Only an in-progress trip can be completed.")
        self.status = self.Status.COMPLETED
        self.arrival_at = timezone.now()
        self.save(update_fields=("status", "arrival_at", "updated_at"))
        schedule = PassengerSchedule.all_objects.filter(
            company_id=self.company_id, trip=self, is_archived=False
        ).first()
        if schedule:
            schedule.status = PassengerSchedule.Status.COMPLETED
            schedule.booking_open = False
            schedule.save(update_fields=("status", "booking_open", "updated_at"))

    def stop_trip(self, reason):
        reason = (reason or "").strip()
        if self.status != self.Status.IN_PROGRESS or not reason:
            raise ValidationError("An in-progress trip and a stop reason are required.")
        self.status = self.Status.STOPPED
        self.stopped_at = timezone.now()
        self.stop_reason = reason
        self.save(update_fields=("status", "stopped_at", "stop_reason", "updated_at"))
        schedule = PassengerSchedule.all_objects.filter(
            company_id=self.company_id, trip=self, is_archived=False
        ).first()
        if schedule:
            schedule.status = PassengerSchedule.Status.DEPARTED
            schedule.booking_open = False
            schedule.save(update_fields=("status", "booking_open", "updated_at"))

    def cancel_trip(self, reason=""):
        if self.status not in (self.Status.PLANNED, self.Status.IN_PROGRESS):
            raise ValidationError("Only planned or in-progress trips can be cancelled.")
        schedule = PassengerSchedule.all_objects.filter(
            company_id=self.company_id, trip=self, is_archived=False
        ).first()
        if schedule and PassengerBooking.all_objects.filter(
            company_id=self.company_id, schedule=schedule, is_archived=False
        ).exclude(status=PassengerBooking.Status.CANCELLED).exists():
            raise ValidationError("Cancel or resolve all active passenger bookings before cancelling this trip.")
        self.status = self.Status.CANCELLED
        self.stop_reason = (reason or "").strip()
        self.save(update_fields=("status", "stop_reason", "updated_at"))
        if schedule:
            schedule.status = PassengerSchedule.Status.CANCELLED
            schedule.booking_open = False
            schedule.save(update_fields=("status", "booking_open", "updated_at"))

    def __str__(self):
        return f"Trip #{self.trip_number}: {self.origin} → {self.destination}"


class Cargo(TransportRecord):
    class PaymentStatus(models.TextChoices):
        PRICE_PENDING = "PRICE_PENDING", "Price pending"
        PAID = "PAID", "Paid"
        PARTIAL = "PARTIAL", "Partially paid"
        UNPAID = "UNPAID", "Unpaid"
        FREE = "FREE", "Free"

    class PricingMethod(models.TextChoices):
        PER_UNIT = "PER_UNIT", "Per unit"
        TOTAL = "TOTAL", "Total price"

    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name="cargo")
    cargo_date = models.DateField(default=timezone.localdate)
    cargo_type = models.CharField(max_length=100)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("1.00"), validators=[MinValueValidator(Decimal("0.01"))])
    unit = models.CharField(max_length=50, default="Unit")
    pricing_method = models.CharField(max_length=20, choices=PricingMethod.choices, default=PricingMethod.PER_UNIT)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)])
    original_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)])
    agreed_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(ZERO)])
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)])
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PRICE_PENDING, db_index=True)
    destination = models.CharField(max_length=100, blank=True)
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=30, blank=True)
    delivered = models.BooleanField(default=False)
    delivered_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-cargo_date", "-pk")
        indexes = [models.Index(fields=("company", "cargo_date"), name="tr_cargo_company_date_idx")]

    def clean(self):
        super().clean()
        if not self.trip_id or self.trip.company_id != self.company_id:
            raise ValidationError({"trip": "Select a trip belonging to this company."})
        if self._state.adding and self.trip.status not in (Trip.Status.PLANNED, Trip.Status.IN_PROGRESS) and not (self.trip.status == Trip.Status.COMPLETED and getattr(self, "_allow_completed_trip_entry", False)):
            raise ValidationError({"trip": "Cargo can only be added to a planned or active trip."})
        self.cargo_type = (self.cargo_type or "").strip()
        self.description = (self.description or "").strip()
        self.unit = (self.unit or "").strip()
        self.customer_name = (self.customer_name or "").strip()
        self.customer_phone = (self.customer_phone or "").strip()
        self.destination = (self.destination or "").strip()
        if self.pricing_method == self.PricingMethod.PER_UNIT:
            self.original_amount = _cargo_line_amount(self.quantity, self.unit_price)
        if self.agreed_amount is not None and self.agreed_amount > self.original_amount:
            raise ValidationError({"agreed_amount": "Agreed amount cannot exceed the original amount."})
        if self.agreed_amount is not None and self.amount_paid > self.agreed_amount:
            raise ValidationError({"amount_paid": "Amount paid cannot exceed the agreed amount."})
        if self.delivered and not self.delivered_at:
            self.delivered_at = timezone.now()
        if not self.delivered:
            self.delivered_at = None

    def save(self, *args, **kwargs):
        if not self.destination and self.trip_id:
            self.destination = self.trip.destination
        if self.pricing_method == self.PricingMethod.PER_UNIT:
            self.original_amount = _cargo_line_amount(self.quantity, self.unit_price)
        self.update_payment_status(save=False)
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def calculated_amount(self):
        return _cargo_line_amount(self.quantity, self.unit_price) if self.pricing_method == self.PricingMethod.PER_UNIT else self.original_amount

    @property
    def discount_amount(self):
        return max((self.original_amount - self.agreed_amount), ZERO) if self.agreed_amount is not None else ZERO

    @property
    def balance_due(self):
        return max((self.agreed_amount - self.amount_paid), ZERO) if self.agreed_amount is not None else ZERO

    def update_payment_status(self, *, save=True):
        if self.agreed_amount is None:
            self.payment_status = self.PaymentStatus.PRICE_PENDING
        elif self.agreed_amount == ZERO:
            self.payment_status = self.PaymentStatus.FREE
        elif self.amount_paid >= self.agreed_amount:
            self.payment_status = self.PaymentStatus.PAID
        elif self.amount_paid > ZERO:
            self.payment_status = self.PaymentStatus.PARTIAL
        else:
            self.payment_status = self.PaymentStatus.UNPAID
        if save and self.pk:
            super().save(update_fields=("amount_paid", "payment_status", "updated_at"))

    def mark_delivered(self):
        if self.delivered:
            return False
        self.delivered = True
        self.delivered_at = timezone.now()
        self.save(update_fields=("delivered", "delivered_at", "payment_status", "updated_at"))
        return True

    def __str__(self):
        return f"{self.description} - {self.customer_name} - {self.agreed_amount}"


class CargoSize(TransportRecord):
    cargo = models.ForeignKey(Cargo, on_delete=models.PROTECT, related_name="size_details")
    size = models.CharField(max_length=50)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ("pk",)
        constraints = [models.UniqueConstraint(fields=("cargo", "size"), name="tr_cargo_size_uq")]

    def clean(self):
        super().clean()
        self.size = (self.size or "").strip()
        if not self.size:
            raise ValidationError({"size": "Size is required."})
        if not self.cargo_id or self.cargo.company_id != self.company_id:
            raise ValidationError({"cargo": "Select cargo belonging to this company."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.size} - {self.quantity}"


class CargoPayment(TransportRecord):
    cargo = models.ForeignKey(Cargo, on_delete=models.PROTECT, related_name="payments")
    payment_date = models.DateTimeField(default=timezone.now)
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    notes = models.TextField(blank=True)
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ("-payment_date", "-pk")

    def clean(self):
        super().clean()
        self.notes = (self.notes or "").strip()
        self.reference = (self.reference or "").strip()
        if not self.cargo_id or self.cargo.company_id != self.company_id:
            raise ValidationError({"cargo": "Select cargo belonging to this company."})
        if self.cargo.agreed_amount is None:
            raise ValidationError({"amount": "Set the cargo fare before recording payment."})
        if self.cargo.agreed_amount == ZERO:
            raise ValidationError({"amount": "Free cargo does not require payment."})

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            cargo = Cargo.all_objects.select_for_update().get(pk=self.cargo_id, company_id=self.company_id)
            paid_before = CargoPayment.all_objects.filter(company_id=self.company_id, cargo=cargo, is_archived=False).exclude(pk=self.pk).aggregate(total=Sum("amount"))["total"] or ZERO
            if paid_before + self.amount > cargo.agreed_amount:
                raise ValidationError("Payment cannot exceed the remaining cargo balance.")
            result = super().save(*args, **kwargs)
            total_paid = CargoPayment.all_objects.filter(company_id=self.company_id, cargo=cargo, is_archived=False).aggregate(total=Sum("amount"))["total"] or ZERO
            cargo.amount_paid = total_paid
            cargo.update_payment_status(save=False)
            cargo.save(update_fields=("amount_paid", "payment_status", "updated_at"))
            return result

    def archive(self):
        if self.is_archived:
            return False
        with transaction.atomic():
            cargo = Cargo.all_objects.select_for_update().get(pk=self.cargo_id, company_id=self.company_id)
            result = super().archive()
            if result:
                cargo.amount_paid = CargoPayment.all_objects.filter(company_id=self.company_id, cargo=cargo, is_archived=False).aggregate(total=Sum("amount"))["total"] or ZERO
                cargo.update_payment_status(save=False)
                cargo.save(update_fields=("amount_paid", "payment_status", "updated_at"))
            return result

    def __str__(self):
        return f"{self.cargo.customer_name} - {self.amount}"


class Expense(TransportRecord):
    class Category(models.TextChoices):
        PEOPLE = "PEOPLE", "People"
        VEHICLE = "VEHICLE", "Vehicle"
        HOME = "HOME", "Home"

    category = models.CharField(max_length=20, choices=Category.choices)
    expense_type = models.CharField(max_length=100)
    route = models.ForeignKey(Route, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses")
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses")
    expense_date = models.DateTimeField(default=timezone.now)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-expense_date", "-pk")
        indexes = [models.Index(fields=("company", "expense_date"), name="tr_exp_company_date_idx")]

    def clean(self):
        super().clean()
        self.expense_type = (self.expense_type or "").strip()
        self.description = (self.description or "").strip()
        self.notes = (self.notes or "").strip()
        if self.route_id and self.route.company_id != self.company_id:
            raise ValidationError({"route": "Route must belong to the same company."})
        if self.trip_id and self.trip.company_id != self.company_id:
            raise ValidationError({"trip": "Trip must belong to the same company."})
        if self.route_id and self.trip_id and self.trip.route_id != self.route_id:
            raise ValidationError({"trip": "Trip must belong to the selected route."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.expense_type} - {self.amount}"


class Maintenance(TransportRecord):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="maintenance_records")
    service_date = models.DateField()
    service_type = models.CharField(max_length=100)
    description = models.TextField()
    cost = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    odometer = models.PositiveIntegerField(null=True, blank=True)
    next_service_date = models.DateField(null=True, blank=True)
    next_service_odometer = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-service_date", "-pk")

    def clean(self):
        super().clean()
        self.service_type = (self.service_type or "").strip()
        self.description = (self.description or "").strip()
        self.notes = (self.notes or "").strip()
        if not self.vehicle_id or self.vehicle.company_id != self.company_id:
            raise ValidationError({"vehicle": "Select a vehicle belonging to this company."})
        if self.service_date and self.service_date > timezone.localdate():
            raise ValidationError({"service_date": "Service date cannot be in the future."})
        if self.next_service_date and self.next_service_date < self.service_date:
            raise ValidationError({"next_service_date": "Next service date cannot be earlier than service date."})
        if self.odometer is not None and self.next_service_odometer is not None and self.next_service_odometer < self.odometer:
            raise ValidationError({"next_service_odometer": "Next service odometer cannot be lower than current odometer."})

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            vehicle = Vehicle.all_objects.select_for_update().get(pk=self.vehicle_id, company_id=self.company_id)
            result = super().save(*args, **kwargs)
            if self.odometer is not None and self.odometer > vehicle.current_odometer:
                vehicle.current_odometer = self.odometer
                vehicle.save(update_fields=("current_odometer", "updated_at"))
            return result

    def __str__(self):
        return f"{self.service_type} - {self.service_date}"


class PassengerSchedule(TransportRecord):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open for booking"
        CLOSED = "CLOSED", "Booking closed"
        DEPARTED = "DEPARTED", "Departed"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    trip = models.OneToOneField(Trip, on_delete=models.PROTECT, related_name="passenger_schedule")
    scheduled_departure = models.DateTimeField()
    expected_arrival = models.DateTimeField(null=True, blank=True)
    fare_amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    seat_capacity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    booking_open = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("scheduled_departure", "pk")
        indexes = [models.Index(fields=("company", "scheduled_departure"), name="tr_pass_sched_date_idx")]

    def clean(self):
        super().clean()
        if not self.trip_id or self.trip.company_id != self.company_id:
            raise ValidationError({"trip": "Select a trip belonging to this company."})
        if self._state.adding and self.trip.status != Trip.Status.PLANNED:
            raise ValidationError({"trip": "Passenger schedules can only be created for planned trips."})
        vehicle_capacity = self.trip.route.vehicle.passenger_capacity
        if vehicle_capacity and self.seat_capacity > vehicle_capacity:
            raise ValidationError({"seat_capacity": "Schedule capacity cannot exceed the vehicle passenger capacity."})
        if self.expected_arrival and self.expected_arrival <= self.scheduled_departure:
            raise ValidationError({"expected_arrival": "Expected arrival must be after departure."})
        if self.status in (self.Status.CLOSED, self.Status.DEPARTED, self.Status.COMPLETED, self.Status.CANCELLED):
            self.booking_open = False
        elif not self.booking_open and self.status == self.Status.OPEN:
            self.status = self.Status.CLOSED

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def seats_remaining(self):
        booked = PassengerBooking.all_objects.filter(
            company_id=self.company_id,
            schedule=self,
            is_archived=False,
        ).exclude(status=PassengerBooking.Status.CANCELLED).count()
        return max(self.seat_capacity - booked, 0)

    def __str__(self):
        return f"{self.trip} @ {self.scheduled_departure}"


class PassengerBooking(TransportRecord):
    class Status(models.TextChoices):
        BOOKED = "BOOKED", "Booked"
        BOARDED = "BOARDED", "Boarded"
        NO_SHOW = "NO_SHOW", "No show"
        CANCELLED = "CANCELLED", "Cancelled"

    class PaymentStatus(models.TextChoices):
        PAID = "PAID", "Paid"
        PARTIAL = "PARTIAL", "Partially paid"
        UNPAID = "UNPAID", "Unpaid"
        FREE = "FREE", "Free"

    schedule = models.ForeignKey(PassengerSchedule, on_delete=models.PROTECT, related_name="bookings")
    booking_reference = models.CharField(max_length=20, default=generate_booking_reference)
    passenger_name = models.CharField(max_length=150)
    passenger_phone = models.CharField(max_length=30, blank=True)
    seat_number = models.CharField(max_length=12)
    fare_amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)])
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOOKED, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("schedule_id", "seat_number")
        constraints = [
            models.UniqueConstraint(fields=("company", "booking_reference"), name="tr_pass_ref_company_uq"),
            models.UniqueConstraint(
                fields=("schedule", "seat_number"),
                condition=Q(is_archived=False) & ~Q(status="CANCELLED"),
                name="tr_pass_seat_active_uq",
            ),
        ]

    def clean(self):
        super().clean()
        self.passenger_name = (self.passenger_name or "").strip()
        self.passenger_phone = (self.passenger_phone or "").strip()
        self.seat_number = (self.seat_number or "").strip().upper()
        if not self.schedule_id or self.schedule.company_id != self.company_id:
            raise ValidationError({"schedule": "Select a passenger schedule belonging to this company."})
        if not self.passenger_name or not self.seat_number:
            raise ValidationError("Passenger name and seat number are required.")
        if self._state.adding and self.status != self.Status.CANCELLED and self.schedule.status != PassengerSchedule.Status.OPEN:
            raise ValidationError({"schedule": "This passenger schedule is not open."})
        if self._state.adding and self.status != self.Status.CANCELLED and not self.schedule.booking_open:
            raise ValidationError({"schedule": "Bookings are closed for this schedule."})
        if self._state.adding and self.status != self.Status.CANCELLED and self.schedule.trip.status != Trip.Status.PLANNED:
            raise ValidationError({"schedule": "Bookings are available only before the trip departs."})
        if self.fare_amount < ZERO or self.amount_paid < ZERO or self.amount_paid > self.fare_amount:
            raise ValidationError("Passenger payment values are invalid.")

    def save(self, *args, **kwargs):
        if self.schedule_id and not self.fare_amount:
            self.fare_amount = self.schedule.fare_amount
        self.update_payment_status(save=False)
        self.full_clean()
        with transaction.atomic():
            schedule = PassengerSchedule.all_objects.select_for_update().get(pk=self.schedule_id, company_id=self.company_id)
            if self.status != self.Status.CANCELLED:
                occupied = PassengerBooking.all_objects.filter(
                    company_id=self.company_id, schedule=schedule, is_archived=False,
                ).exclude(status=self.Status.CANCELLED).exclude(pk=self.pk).count()
                if occupied >= schedule.seat_capacity:
                    raise ValidationError({"schedule": "This passenger schedule has no seats remaining."})
            return super().save(*args, **kwargs)

    def update_payment_status(self, *, save=True):
        if self.fare_amount == ZERO:
            self.payment_status = self.PaymentStatus.FREE
        elif self.amount_paid >= self.fare_amount:
            self.payment_status = self.PaymentStatus.PAID
        elif self.amount_paid > ZERO:
            self.payment_status = self.PaymentStatus.PARTIAL
        else:
            self.payment_status = self.PaymentStatus.UNPAID
        if save and self.pk:
            super().save(update_fields=("amount_paid", "payment_status", "updated_at"))

    def cancel(self):
        if self.status == self.Status.CANCELLED:
            return False
        if self.amount_paid > ZERO:
            raise ValidationError("A paid booking cannot be cancelled until its refund is recorded.")
        self.status = self.Status.CANCELLED
        self.save(update_fields=("status", "updated_at"))
        return True

    def mark_boarded(self):
        if self.status != self.Status.BOOKED:
            raise ValidationError("Only a booked passenger can be marked as boarded.")
        if self.schedule.trip.status != Trip.Status.IN_PROGRESS:
            raise ValidationError("Passengers can be boarded only after the trip starts.")
        self.status = self.Status.BOARDED
        self.save(update_fields=("status", "updated_at"))

    def mark_no_show(self):
        if self.status != self.Status.BOOKED:
            raise ValidationError("Only a booked passenger can be marked as no-show.")
        if self.schedule.trip.status not in (Trip.Status.IN_PROGRESS, Trip.Status.COMPLETED, Trip.Status.STOPPED):
            raise ValidationError("No-show can be recorded after the trip departs.")
        self.status = self.Status.NO_SHOW
        self.save(update_fields=("status", "updated_at"))

    def __str__(self):
        return f"{self.booking_reference}: {self.passenger_name} ({self.seat_number})"


class PassengerPayment(TransportRecord):
    booking = models.ForeignKey(PassengerBooking, on_delete=models.PROTECT, related_name="payments")
    payment_date = models.DateTimeField(default=timezone.now)
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-payment_date", "-pk")

    def clean(self):
        super().clean()
        self.reference = (self.reference or "").strip()
        self.notes = (self.notes or "").strip()
        if not self.booking_id or self.booking.company_id != self.company_id:
            raise ValidationError({"booking": "Select a booking belonging to this company."})
        if self.booking.status == PassengerBooking.Status.CANCELLED:
            raise ValidationError({"booking": "Cancelled bookings cannot receive payments."})

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            booking = PassengerBooking.all_objects.select_for_update().get(pk=self.booking_id, company_id=self.company_id)
            paid_before = PassengerPayment.all_objects.filter(company_id=self.company_id, booking=booking, is_archived=False).exclude(pk=self.pk).aggregate(total=Sum("amount"))["total"] or ZERO
            if paid_before + self.amount > booking.fare_amount:
                raise ValidationError("Payment cannot exceed the remaining passenger fare.")
            result = super().save(*args, **kwargs)
            booking.amount_paid = PassengerPayment.all_objects.filter(company_id=self.company_id, booking=booking, is_archived=False).aggregate(total=Sum("amount"))["total"] or ZERO
            booking.update_payment_status(save=False)
            booking.save(update_fields=("amount_paid", "payment_status", "updated_at"))
            return result

    def archive(self):
        if self.is_archived:
            return False
        with transaction.atomic():
            booking = PassengerBooking.all_objects.select_for_update().get(pk=self.booking_id, company_id=self.company_id)
            result = super().archive()
            if result:
                booking.amount_paid = PassengerPayment.all_objects.filter(company_id=self.company_id, booking=booking, is_archived=False).aggregate(total=Sum("amount"))["total"] or ZERO
                booking.update_payment_status(save=False)
                booking.save(update_fields=("amount_paid", "payment_status", "updated_at"))
            return result

    def __str__(self):
        return f"{self.booking.booking_reference} - {self.amount}"
