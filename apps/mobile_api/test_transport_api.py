from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Company
from apps.companies.scope import company_scope
from apps.companies.services import create_company
from apps.transport.models import Cargo, PassengerSchedule, Route, TransportProfile, Trip, Vehicle

User = get_user_model()


class MobileTransportApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="transport-api-admin",
            email="transport-api-admin@example.test",
            password="StrongAdminPass!2026",
            is_active=True,
            is_platform_admin=True,
        )
        cls.owner = User.objects.create_user(
            username="transport-api-owner",
            email="transport-api-owner@example.test",
            password="StrongOwnerPass!2026",
            is_active=True,
        )
        cls.company = create_company(
            actor=cls.admin,
            name="API Highland Transport",
            code="api-highland-transport",
            owner=cls.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
            business_type=Company.BusinessType.TRANSPORT,
            transport_cargo_enabled=True,
            transport_passenger_enabled=True,
        )

    def setUp(self):
        self.client.force_login(self.owner)

    def test_transport_dashboard_returns_real_company_and_services(self):
        response = self.client.get("/api/mobile/v1/transport/dashboard/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["company"]["businessType"], "transport")
        self.assertTrue(payload["services"]["cargo"])
        self.assertTrue(payload["services"]["passenger"])
        self.assertTrue(payload["canManageServices"])

    def test_vehicle_create_and_list_are_company_scoped(self):
        create_response = self.client.post("/api/mobile/v1/transport/modules/vehicles/create/", {
            "name": "Highland Bus",
            "plate_number": "T 888 EEE",
            "vehicle_type": "Bus",
            "passenger_capacity": "45",
        })
        self.assertEqual(create_response.status_code, 201)
        list_response = self.client.get("/api/mobile/v1/transport/modules/vehicles/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["count"], 1)
        self.assertEqual(list_response.json()["items"][0]["title"], "T 888 EEE")

    def test_owner_can_switch_transport_services_without_deleting_history(self):
        response = self.client.post("/api/mobile/v1/transport/services/update/", {
            "cargo_enabled": "false",
            "passenger_enabled": "true",
        })
        self.assertEqual(response.status_code, 200)
        profile = TransportProfile.objects.get(company=self.company)
        self.assertFalse(profile.cargo_enabled)
        self.assertTrue(profile.passenger_enabled)

    def test_transport_company_cannot_disable_its_last_service(self):
        response = self.client.post("/api/mobile/v1/transport/services/update/", {
            "cargo_enabled": "false",
            "passenger_enabled": "false",
        })
        self.assertEqual(response.status_code, 400)
        profile = TransportProfile.objects.get(company=self.company)
        self.assertTrue(profile.cargo_enabled)
        self.assertTrue(profile.passenger_enabled)

    def test_disabled_cargo_service_preserves_history_but_blocks_new_writes(self):
        vehicle = Vehicle.objects.create(
            company=self.company,
            name="Cargo Truck",
            plate_number="T 889 EEE",
            vehicle_type="Truck",
        )
        route = Route.objects.create(company=self.company, vehicle=vehicle, start_location="Njombe", end_location="Mbeya")
        trip = Trip.objects.create(company=self.company, route=route, origin="Njombe", destination="Mbeya")
        Cargo.objects.create(company=self.company, trip=trip, cargo_type="GOODS", description="Old parcel", customer_name="Customer", pricing_method=Cargo.PricingMethod.TOTAL, original_amount=Decimal("10000.00"), agreed_amount=Decimal("10000.00"))
        profile = TransportProfile.objects.get(company=self.company)
        profile.cargo_enabled = False
        profile.save(update_fields=("cargo_enabled", "updated_at"))

        history_response = self.client.get("/api/mobile/v1/transport/modules/cargo/")
        write_response = self.client.post("/api/mobile/v1/transport/modules/cargo/create/", {
            "trip_id": str(trip.pk),
            "cargo_type": "GOODS",
            "description": "New parcel",
            "quantity": "1",
            "unit_price": "10000",
            "agreed_amount": "10000",
            "customer_name": "Customer Two",
        })

        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(history_response.json()["count"], 1)
        self.assertEqual(write_response.status_code, 403)
        self.assertEqual(write_response.json()["code"], "service_disabled")

    def test_passenger_schedule_and_manifest_are_available(self):
        vehicle = Vehicle.objects.create(company=self.company, name="Passenger Bus", plate_number="T 890 EEE", vehicle_type="Bus", passenger_capacity=2)
        route = Route.objects.create(company=self.company, vehicle=vehicle, start_location="Njombe", end_location="Dar es Salaam")
        trip = Trip.objects.create(company=self.company, route=route, origin="Njombe", destination="Dar es Salaam")
        schedule = PassengerSchedule.objects.create(company=self.company, trip=trip, scheduled_departure=timezone.now() + timedelta(days=1), fare_amount=Decimal("25000.00"), seat_capacity=2)
        response = self.client.get(f"/api/mobile/v1/transport/passenger-schedules/{schedule.pk}/manifest/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["schedule"]["seatCapacity"], 2)
        self.assertEqual(response.json()["count"], 0)

    def test_wrong_business_domain_cannot_open_transport_dashboard(self):
        pig_owner = User.objects.create_user(username="transport-pig-owner", email="transport-pig@example.test", password="StrongOwnerPass!2026", is_active=True)
        create_company(actor=self.admin, name="Pig Company", code="transport-pig-company", owner=pig_owner, allowed_languages=["sw"], default_language="sw", step_up_verified=True)
        self.client.force_login(pig_owner)
        response = self.client.get("/api/mobile/v1/transport/dashboard/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "wrong_business_type")
