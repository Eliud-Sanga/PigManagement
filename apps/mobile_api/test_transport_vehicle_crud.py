"""Security regression tests for permissioned, tenant-scoped vehicle edits."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from apps.companies.models import Company, CompanyAuditEvent
from apps.companies.services import create_company
from apps.transport.models import Maintenance, Route, Vehicle

User = get_user_model()


class MobileVehicleCrudTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="vehicle-crud-admin", email="vehicle-crud-admin@example.test",
            password="Test-Admin-Pass-2026!", is_active=True, is_platform_admin=True,
        )
        cls.owner = User.objects.create_user(
            username="vehicle-crud-owner", email="vehicle-crud-owner@example.test",
            password="Test-Owner-Pass-2026!", is_active=True,
        )
        cls.company = create_company(
            actor=cls.admin, name="Vehicle CRUD Transport", code="vehicle-crud-transport",
            owner=cls.owner, allowed_languages=["sw", "en"], default_language="sw",
            step_up_verified=True, business_type=Company.BusinessType.TRANSPORT,
            transport_cargo_enabled=True, transport_passenger_enabled=False,
        )

    def setUp(self):
        self.client.force_login(self.owner)
        self.vehicle = Vehicle.objects.create(
            company=self.company, name="Highland Truck", plate_number="T 321 XYZ", vehicle_type="Truck",
        )
        self.root = f"/api/mobile/v1/transport/vehicles/{self.vehicle.pk}/"

    def version(self):
        result = self.client.get(self.root)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["canEdit"])
        return result.json()["updatedAt"]

    def test_vehicle_edit_requires_fresh_version_and_records_audit(self):
        before = self.version()
        response = self.client.post(self.root + "update/", {
            "expected_updated_at": before, "name": "Highland Express",
            "plate_number": "t 321 xyz", "current_odometer": "1200",
        })
        self.assertEqual(response.status_code, 200)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.name, "Highland Express")
        self.assertEqual(self.vehicle.plate_number, "T 321 XYZ")
        self.assertEqual(self.vehicle.current_odometer, 1200)
        self.assertTrue(CompanyAuditEvent.objects.filter(
            company=self.company, action="TRANSPORT_VEHICLE_UPDATED",
        ).exists())
        stale = self.client.post(self.root + "update/", {
            "expected_updated_at": before, "name": "Wrong stale write",
        })
        self.assertEqual(stale.status_code, 409)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.name, "Highland Express")

    def test_vehicle_edit_rejects_mass_assignment(self):
        response = self.client.post(self.root + "update/", {
            "expected_updated_at": self.version(), "name": "Try override",
            "is_archived": "true",
        })
        self.assertEqual(response.status_code, 400)
        self.vehicle.refresh_from_db()
        self.assertFalse(self.vehicle.is_archived)

    def test_unlinked_vehicle_archives_without_physical_deletion(self):
        response = self.client.post(self.root + "archive/", {
            "expected_updated_at": self.version(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Vehicle.all_objects.get(pk=self.vehicle.pk).is_archived)
        self.assertEqual(self.client.get(self.root).status_code, 404)
        self.assertTrue(CompanyAuditEvent.objects.filter(
            company=self.company, action="TRANSPORT_VEHICLE_ARCHIVED",
        ).exists())

    def test_linked_vehicle_archive_is_blocked(self):
        Route.objects.create(company=self.company, vehicle=self.vehicle, start_location="Njombe")
        response = self.client.post(self.root + "archive/", {
            "expected_updated_at": self.version(),
        })
        self.assertEqual(response.status_code, 409)
        self.vehicle.refresh_from_db()
        self.assertFalse(self.vehicle.is_archived)

    def test_cross_company_records_are_not_read_or_changed(self):
        other_user = User.objects.create_user(
            username="vehicle-crud-other", email="vehicle-crud-other@example.test",
            password="Test-Other-Pass-2026!", is_active=True,
        )
        create_company(
            actor=self.admin, name="Other Vehicle Company", code="vehicle-crud-other",
            owner=other_user, allowed_languages=["sw"], default_language="sw",
            step_up_verified=True, business_type=Company.BusinessType.TRANSPORT,
            transport_cargo_enabled=True, transport_passenger_enabled=False,
        )
        current_version = self.version()
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(self.root).status_code, 404)
        response = self.client.post(self.root + "update/", {
            "expected_updated_at": current_version, "name": "Illegal edit",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post(self.root + "archive/", {
            "expected_updated_at": current_version,
        }).status_code, 404)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.name, "Highland Truck")

    def test_csrf_protects_vehicle_write(self):
        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(self.owner)
        self.assertEqual(strict_client.post(self.root + "archive/", {
            "expected_updated_at": self.version(),
        }).status_code, 403)
