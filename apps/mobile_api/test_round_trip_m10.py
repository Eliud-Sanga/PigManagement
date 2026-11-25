from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from apps.companies.models import Company
from apps.companies.services import create_company
from apps.transport.models import Cargo, Expense, Route, Trip, Vehicle


@override_settings(BIZMANAGER_AUTO_CARGO_ROUTE=True)
class AutomaticCargoJourneyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        admin = U.objects.create_user(username='m10admin', email='m10admin@example.test', password='M10-qa-admin!', is_platform_admin=True)
        cls.owner = U.objects.create_user(username='m10owner', email='m10owner@example.test', password='M10-qa-owner!')
        cls.company = create_company(actor=admin, name='M10 Cargo', code='m10-cargo', owner=cls.owner, allowed_languages=['sw'], default_language='sw', step_up_verified=True, business_type=Company.BusinessType.TRANSPORT, transport_cargo_enabled=True, transport_passenger_enabled=False)
        cls.vehicle = Vehicle.objects.create(company=cls.company, name='M10 Truck', plate_number='M10 QA', vehicle_type='Truck')
        outsider = U.objects.create_user(username='m10other', email='m10other@example.test', password='M10-qa-other!')
        other = create_company(actor=admin, name='Other Cargo', code='m10-other', owner=outsider, allowed_languages=['sw'], default_language='sw', step_up_verified=True, business_type=Company.BusinessType.TRANSPORT, transport_cargo_enabled=True, transport_passenger_enabled=False)
        cls.other_vehicle = Vehicle.objects.create(company=other, name='Other Truck', plate_number='M10 OTHER')

    def setUp(self):
        self.client.force_login(self.owner)

    def depart(self, origin, destination, vehicle=None):
        return self.client.post('/api/mobile/v1/transport/modules/trips/create/', {'vehicle_id': (vehicle or self.vehicle).pk, 'origin': origin, 'destination': destination})

    def arrive(self, trip):
        return self.client.post(f'/api/mobile/v1/transport/modules/trips/{trip.pk}/complete/', {})

    def test_journey_closes_only_after_confirmed_arrival_and_keeps_debt(self):
        response = self.depart('Ikonda', 'Mbeya')
        self.assertEqual(response.status_code, 201, response.content)
        trip = Trip.all_objects.get(pk=response.json()['item']['id'])
        route = trip.route
        self.assertEqual(route.status, Route.Status.ACTIVE)
        self.assertEqual(trip.status, Trip.Status.IN_PROGRESS)
        self.assertEqual(self.depart('Ikonda', 'Mbeya').status_code, 400)
        Cargo.objects.create(company=self.company, trip=trip, description='Timber', customer_name='QA customer', cargo_type='TIMBER', pricing_method=Cargo.PricingMethod.TOTAL, original_amount=Decimal('120000.00'), agreed_amount=Decimal('100000.00'))
        Expense.objects.create(company=self.company, route=route, trip=trip, category=Expense.Category.VEHICLE, expense_type='Fuel', description='Fuel', amount=Decimal('30000.00'))
        self.assertEqual(self.arrive(trip).status_code, 200)
        self.assertEqual(self.depart('Wrong place', 'Ikonda').status_code, 400)
        response = self.depart('Mbeya', 'Ikonda')
        self.assertEqual(response.status_code, 201, response.content)
        returning = Trip.all_objects.get(pk=response.json()['item']['id'])
        self.assertEqual(returning.route_id, route.pk)
        route.refresh_from_db()
        self.assertEqual(route.status, Route.Status.ACTIVE)
        self.assertEqual(self.arrive(returning).status_code, 200)
        route.refresh_from_db()
        self.assertEqual(route.status, Route.Status.COMPLETED)
        self.assertEqual(route.end_location, 'Ikonda')
        self.assertEqual(route.profit, Decimal('70000.00'))
        self.assertEqual(route.get_unpaid_cargo().count(), 1)
        self.assertEqual(self.depart('Ikonda', 'Njombe').status_code, 201)
        self.assertEqual(Route.all_objects.filter(company=self.company).count(), 2)

    def test_rejects_foreign_vehicle_and_nonhome_first_trip(self):
        self.assertEqual(self.depart('Ikonda', 'Mbeya', self.other_vehicle).status_code, 400)
        self.assertEqual(self.depart('Mbeya', 'Ikonda').status_code, 400)
        self.assertFalse(Route.all_objects.filter(company=self.company).exists())
        result = self.client.get('/api/mobile/v1/transport/modules/trips/')
        self.assertEqual(result.status_code, 200)
        self.assertIn('roundTripVehicles', result.json()['formOptions'])
        result = self.client.get('/api/mobile/v1/transport/modules/routes/')
        self.assertFalse(result.json()['canCreate'])
