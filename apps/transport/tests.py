from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.companies.models import Company, CompanyMembership
from apps.companies.scope import company_scope
from apps.companies.authorization import get_effective_permission_codes
from apps.companies.services import create_company
from apps.users.models import User

from .models import (
    Cargo,
    PassengerBooking,
    PassengerPayment,
    PassengerSchedule,
    Route,
    TransportProfile,
    Trip,
    Vehicle,
)


class TransportCompanyIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.platform_admin = User.objects.create_user(
            username="transport-platform-admin",
            email="transport-admin@example.test",
            password="StrongAdminPass!2026",
            is_active=True,
            is_platform_admin=True,
        )
        cls.owner_a = User.objects.create_user(
            username="transport-owner-a",
            email="transport-a@example.test",
            password="StrongOwnerPass!2026",
            is_active=True,
        )
        cls.owner_b = User.objects.create_user(
            username="transport-owner-b",
            email="transport-b@example.test",
            password="StrongOwnerPass!2026",
            is_active=True,
        )
        cls.company_a = create_company(
            actor=cls.platform_admin,
            name="Highland Transport A",
            code="highland-transport-a",
            owner=cls.owner_a,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
            business_type=Company.BusinessType.TRANSPORT,
            transport_cargo_enabled=True,
            transport_passenger_enabled=True,
        )
        cls.company_b = create_company(
            actor=cls.platform_admin,
            name="Highland Transport B",
            code="highland-transport-b",
            owner=cls.owner_b,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
            business_type=Company.BusinessType.TRANSPORT,
            transport_cargo_enabled=True,
            transport_passenger_enabled=False,
        )

    def _vehicle(self, company, *, plate="T 123 ABC", capacity=30):
        with company_scope(company):
            return Vehicle.objects.create(
                name="Highland Coach",
                plate_number=plate,
                vehicle_type="Bus",
                passenger_capacity=capacity,
            )

    def _route_and_trip(self, company, vehicle=None):
        vehicle = vehicle or self._vehicle(company)
        with company_scope(company):
            route = Route.objects.create(
                vehicle=vehicle,
                start_location="Njombe",
                end_location="Dar es Salaam",
            )
            trip = Trip.objects.create(
                route=route,
                origin="Njombe",
                destination="Dar es Salaam",
            )
        return route, trip

    def test_transport_company_is_provisioned_with_selected_services(self):
        profile_a = TransportProfile.objects.get(company=self.company_a)
        profile_b = TransportProfile.objects.get(company=self.company_b)
        self.assertTrue(profile_a.cargo_enabled)
        self.assertTrue(profile_a.passenger_enabled)
        self.assertTrue(profile_b.cargo_enabled)
        self.assertFalse(profile_b.passenger_enabled)

    def test_company_scoped_manager_does_not_mix_vehicle_records(self):
        self._vehicle(self.company_a, plate="T 111 AAA")
        self._vehicle(self.company_b, plate="T 222 BBB")
        with company_scope(self.company_a):
            self.assertEqual(Vehicle.objects.count(), 1)
            self.assertEqual(Vehicle.objects.first().plate_number, "T 111 AAA")
        with company_scope(self.company_b):
            self.assertEqual(Vehicle.objects.count(), 1)
            self.assertEqual(Vehicle.objects.first().plate_number, "T 222 BBB")

    def test_route_rejects_vehicle_from_another_company(self):
        vehicle_a = self._vehicle(self.company_a, plate="T 333 CCC")
        with company_scope(self.company_b):
            with self.assertRaises(ValidationError):
                Route.objects.create(
                    vehicle=vehicle_a,
                    start_location="Makete",
                    end_location="Mbeya",
                )

    def test_price_pending_cargo_does_not_break_route_income(self):
        route, trip = self._route_and_trip(self.company_a)
        with company_scope(self.company_a):
            Cargo.objects.create(
                trip=trip,
                cargo_type="GOODS",
                description="Unpriced parcel",
                customer_name="Customer One",
                agreed_amount=None,
            )
        self.assertEqual(route.calculate_total_income(), Decimal("0.00"))

    def test_passenger_schedule_and_seat_booking_are_company_scoped(self):
        vehicle = self._vehicle(self.company_a, plate="T 444 DDD", capacity=2)
        route, trip = self._route_and_trip(self.company_a, vehicle)
        with company_scope(self.company_a):
            schedule = PassengerSchedule.objects.create(
                trip=trip,
                scheduled_departure=timezone.now() + timedelta(days=1),
                fare_amount=Decimal("25000.00"),
                seat_capacity=2,
            )
            first = PassengerBooking.objects.create(
                schedule=schedule,
                passenger_name="Passenger One",
                seat_number="A1",
                fare_amount=schedule.fare_amount,
            )
            self.assertEqual(schedule.seats_remaining, 1)
            with self.assertRaises(ValidationError):
                PassengerBooking.objects.create(
                    schedule=schedule,
                    passenger_name="Passenger Two",
                    seat_number="A1",
                    fare_amount=schedule.fare_amount,
                )
            self.assertEqual(first.company_id, self.company_a.pk)

    def test_passenger_payment_cannot_exceed_fare_and_updates_balance(self):
        vehicle = self._vehicle(self.company_a, plate="T 555 EEE", capacity=2)
        _route, trip = self._route_and_trip(self.company_a, vehicle)
        with company_scope(self.company_a):
            schedule = PassengerSchedule.objects.create(
                trip=trip,
                scheduled_departure=timezone.now() + timedelta(days=1),
                fare_amount=Decimal("30000.00"),
                seat_capacity=2,
            )
            booking = PassengerBooking.objects.create(
                schedule=schedule,
                passenger_name="Passenger Three",
                seat_number="B1",
                fare_amount=schedule.fare_amount,
            )
            with self.assertRaises(ValidationError):
                PassengerPayment.objects.create(
                    booking=booking,
                    amount=Decimal("30001.00"),
                )
            PassengerPayment.objects.create(
                booking=booking,
                amount=Decimal("30000.00"),
            )
            booking.refresh_from_db()
            self.assertEqual(booking.amount_paid, Decimal("30000.00"))
            self.assertEqual(booking.payment_status, PassengerBooking.PaymentStatus.PAID)


    def test_transport_owner_role_receives_transport_not_pig_permissions(self):
        membership = CompanyMembership.objects.get(company=self.company_a, user=self.owner_a)
        codes = get_effective_permission_codes(membership)
        self.assertIn("transport.view_vehicle", codes)
        self.assertIn("transport.manage_transport_services", codes)
        self.assertNotIn("pigs.view_pig", codes)

    def test_trip_departure_closes_passenger_bookings(self):
        vehicle = self._vehicle(self.company_a, plate="T 666 FFF", capacity=2)
        route, trip = self._route_and_trip(self.company_a, vehicle)
        with company_scope(self.company_a):
            schedule = PassengerSchedule.objects.create(
                trip=trip,
                scheduled_departure=timezone.now() + timedelta(days=1),
                fare_amount=Decimal("20000.00"),
                seat_capacity=2,
            )
            PassengerBooking.objects.create(
                schedule=schedule,
                passenger_name="Passenger Four",
                seat_number="C1",
                fare_amount=schedule.fare_amount,
            )
            route.start_route()
            trip.start_trip()
            schedule.refresh_from_db()
            self.assertEqual(schedule.status, PassengerSchedule.Status.DEPARTED)
            self.assertFalse(schedule.booking_open)

    def test_transport_profile_requires_at_least_one_service(self):
        profile = TransportProfile.objects.get(company=self.company_a)
        profile.cargo_enabled = False
        profile.passenger_enabled = False
        with self.assertRaises(ValidationError):
            profile.full_clean()
