"""M26 read-path correctness, query budgets and authorization regression tests."""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from apps.companies.models import Company
from apps.companies.scope import company_scope
from apps.pigs.models import DailySale, Expense, MeatStock, Pig, Purchase, SlaughterBatch
from apps.transport.models import PassengerBooking, PassengerSchedule, Route, Trip, Vehicle
from . import workspace_views as pig_api, transport_views as tr_api, platform_views as admin_api


class PermissionSnapshotTests(SimpleTestCase):
    def test_only_exact_read_scope_reuses_permissions(self):
        company, member = object(), object()
        codes = frozenset({'pigs.view_pig'})
        req = SimpleNamespace(method='GET', company=company, company_membership=member,
                              company_permissions=codes)
        with patch.object(pig_api, 'get_effective_permission_codes', return_value=frozenset()) as resolve:
            with company_scope(company, membership=member, permissions=codes):
                self.assertEqual(pig_api._read_permission_codes(req, company, member), codes)
                resolve.assert_not_called()
                req.method = 'POST'
                self.assertEqual(pig_api._read_permission_codes(req, company, member), frozenset())
                req.method = 'GET'
                req.company_permissions = frozenset()
                self.assertEqual(pig_api._read_permission_codes(req, company, member), frozenset())
            self.assertEqual(resolve.call_count, 2)
            self.assertEqual(pig_api._read_permission_codes(req, company, member), frozenset())
            self.assertEqual(resolve.call_count, 3)

    def test_transport_options_are_opt_in_compatible(self):
        factory = RequestFactory()
        company = SimpleNamespace(public_id='c', name='test')
        profile = SimpleNamespace(cargo_enabled=True, passenger_enabled=False)
        codes = frozenset({'transport.view_vehicle'})
        for value in ('0', '1', 'bad'):
            req = factory.get('/transport/vehicles/', {'includeOptions': value})
            with patch.object(tr_api, '_transport_context', return_value=(company, None, codes, profile, None)), \
                 patch.object(tr_api, '_module_queryset') as query, \
                 patch.object(tr_api, '_form_options', return_value={'vehicles': []}) as options:
                scoped = query.return_value.filter.return_value
                scoped.count.return_value = 0
                scoped.order_by.return_value.__getitem__.return_value = []
                response = tr_api.transport_module(req, 'vehicles')
                if value == 'bad':
                    self.assertEqual(response.status_code, 400)
                    query.assert_not_called()
                else:
                    self.assertEqual(response.status_code, 200)
                    query.assert_called_once_with('vehicles')
                    query.return_value.filter.assert_called_once_with(company=company)
                    scoped.count.assert_called_once_with()
                    scoped.order_by.assert_called_once_with('-created_at', '-pk')
                    payload = json.loads(response.content)
                    self.assertEqual(payload['formOptions'], {'vehicles': []} if value == '1' else {})
                    self.assertEqual(payload['count'], 0)
                    self.assertIs(payload['hasNext'], False)
                    self.assertEqual(payload['items'], [])
                self.assertEqual(options.call_count, int(value == '1'))


class BackendReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create(username='m26-test-actor', email='m26-actor@example.invalid')
        cls.a = Company.objects.create(name='M26 A', code='m26-a', created_by=cls.actor)
        cls.b = Company.objects.create(name='M26 B', code='m26-b', created_by=cls.actor)
        cls.transport = Company.objects.create(name='M26 Transport', code='m26-tr', created_by=cls.actor, business_type='transport')
        cls.today = timezone.localdate()
        for company in (cls.a, cls.b):
            purchase = Purchase.all_objects.create(company=company, supplier_name='Synthetic', purchase_date=cls.today,
                                                     number_of_pigs=3, total_cost=Decimal('100.00'))
            # Read-path test fixture: deliberately use bulk_create to bypass business creation side effects.
            Pig.all_objects.bulk_create([
                Pig(company=company, purchase=purchase, tag_number='M26-1', status=Pig.AVAILABLE),
                Pig(company=company, purchase=purchase, tag_number='M26-2', status=Pig.SLAUGHTERED),
                Pig(company=company, purchase=purchase, tag_number='M26-3', purchase_price=Decimal('20.25'), status=Pig.FINISHED),
                Pig(company=company, purchase=purchase, tag_number='M26-4', purchase_price=Decimal('99.99'), is_archived=True),
            ])
            Expense.all_objects.bulk_create([Expense(company=company, title='Synthetic expense', expense_date=cls.today,
                                                      amount=Decimal('12.00') if company == cls.a else Decimal('999.00'))])
            DailySale.all_objects.bulk_create([DailySale(company=company, sale_date=cls.today,
                                                         total_money_received=Decimal('120.00') if company == cls.a else Decimal('9999.00'))])
        # The schedule list must exclude archived/cancelled bookings and never count another company.
        v = Vehicle.all_objects.bulk_create([Vehicle(company=cls.transport, name='Bus', plate_number='M26 TEST')])[0]
        r = Route.all_objects.bulk_create([Route(company=cls.transport, vehicle=v, route_number=1, start_location='A', end_location='B')])[0]
        trips = Trip.all_objects.bulk_create([Trip(company=cls.transport, route=r, trip_number=i, origin='A', destination='B') for i in (1, 2)])
        schedules = PassengerSchedule.all_objects.bulk_create([PassengerSchedule(company=cls.transport, trip=t, scheduled_departure=timezone.now(), fare_amount=Decimal('10.00'), seat_capacity=10) for t in trips])
        PassengerBooking.all_objects.bulk_create([
            PassengerBooking(company=cls.transport, schedule=schedules[0], passenger_name='Fixture', seat_number='1', fare_amount=10),
            PassengerBooking(company=cls.transport, schedule=schedules[0], passenger_name='Fixture', seat_number='2', fare_amount=10, status=PassengerBooking.Status.CANCELLED),
            PassengerBooking(company=cls.transport, schedule=schedules[0], passenger_name='Fixture', seat_number='3', fare_amount=10, is_archived=True),
        ])

    def test_grouped_pig_cost_preserves_per_pig_rounding_and_scope(self):
        with company_scope(self.a):
            qs = Pig.objects.select_related('purchase')
            reference = sum((p.get_purchase_cost() for p in qs), Decimal('0.00'))
            with self.assertNumQueries(2):
                actual = pig_api._pig_purchase_total(qs)
            self.assertEqual(reference, Decimal('86.91'))
            self.assertEqual(actual, reference)

    def test_pig_dashboard_permission_boundary_and_query_budget(self):
        req = RequestFactory().get('/pig/dashboard/')
        req.user = self.actor
        member = SimpleNamespace(role=SimpleNamespace(name='Fixture role'))
        restricted = frozenset({'pigs.view_pig'})
        with company_scope(self.a), patch.object(pig_api, '_business_context', return_value=(self.a, member, restricted, None)):
            with self.assertNumQueries(1):
                payload = json.loads(pig_api.pig_dashboard(req).content)
            self.assertEqual(payload['summary']['totalPigs'], 3)
            self.assertEqual(Decimal(payload['summary']['totalIncome']), Decimal(0))
            self.assertFalse(payload['summaryAccess']['sales'])
            self.assertFalse(payload['summaryAccess']['expenses'])
            self.assertEqual(payload['recentActivity'], [])
            self.assertEqual(payload['activeBatches'], [])
        all_codes = frozenset(code for codes in pig_api.MODULE_PERMISSIONS.values() for code in codes)
        with company_scope(self.a), patch.object(pig_api, '_business_context', return_value=(self.a, member, all_codes, None)):
            with self.assertNumQueries(14):
                payload = json.loads(pig_api.pig_dashboard(req).content)
            self.assertEqual(Decimal(payload['summary']['totalIncome']), Decimal('120.00'))
            self.assertEqual(Decimal(payload['summary']['totalExpenses']), Decimal('12.00'))
            self.assertTrue(payload['summaryAccess']['sales'])
            self.assertEqual(len(payload['recentActivity']), 2)

    def test_stock_annotation_matches_batch_ledger_without_join_multiplication(self):
        with company_scope(self.a):
            batch = SlaughterBatch.all_objects.bulk_create([
                SlaughterBatch(company=self.a, batch_number='M26-B1', slaughter_date=self.today),
            ])[0]
            pigs = list(Pig.objects.order_by('pk')[:2])
            batch.pigs.add(*pigs)
            MeatStock.all_objects.bulk_create([MeatStock(company=self.a, slaughter_batch=batch)])
            DailySale.all_objects.filter(company=self.a).update(
                slaughter_batch=batch, total_meat_sales=Decimal('80.00'), total_meat_weight_kg=Decimal('4.00'),
            )
            with self.assertNumQueries(1):
                stock = pig_api._stock_summary_queryset().get(slaughter_batch=batch)
                result = pig_api._stock_item(stock)
            self.assertEqual(stock.pig_count, 2)
            self.assertEqual(Decimal(result['amount']), batch.get_total_meat_sales())
            self.assertEqual(Decimal(result['meatWeightKg']), batch.get_total_meat_weight())
            self.assertEqual(Decimal(result['amount']), Decimal('80.00'))

    def test_schedule_list_uses_one_query_and_matches_model(self):
        with company_scope(self.transport):
            reference = {s.pk: s.seats_remaining for s in PassengerSchedule.objects.all()}
            with self.assertNumQueries(1):
                rows = [tr_api._record_payload('passenger_schedules', s) for s in tr_api._module_queryset('passenger_schedules')]
            self.assertEqual({r['id']: r['seatsRemaining'] for r in rows}, reference)
            self.assertEqual(sorted(reference.values()), [9, 10])

    def test_platform_overview_minimum_query_count_and_admin_boundary(self):
        request = RequestFactory().get('/platform/overview/')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=False)
        with self.assertNumQueries(0):
            self.assertEqual(admin_api.overview(request).status_code, 403)
        request.user.is_platform_admin = True
        with self.assertNumQueries(2):
            payload = json.loads(admin_api.overview(request).content)
        self.assertGreaterEqual(payload['companies']['active'], 3)
        with self.assertNumQueries(2):
            response = admin_api.records(request, 'users')
        for row in json.loads(response.content)['items']:
            self.assertEqual(set(row), {'id', 'title', 'subtitle', 'status'})

    def test_transport_create_receipt_replay_conflict_and_wrong_company(self):
        from apps.transport.models import Expense as TransportExpense
        from apps.users.models import AuditLog
        factory = RequestFactory()
        profile = SimpleNamespace(cargo_enabled=True, passenger_enabled=False)
        codes = frozenset({'transport.add_expense'})
        data = {'operation_id': 'm26-expense-1', 'expected_company_id': str(self.transport.public_id),
                'category': 'VEHICLE', 'expense_type': 'Fuel', 'description': 'Synthetic fuel', 'amount': '5.00'}
        def submit(fields):
            request = factory.post('/transport/expenses/create/', fields)
            request.user = self.actor
            request._dont_enforce_csrf_checks = True
            return tr_api.transport_create(request, 'expenses')
        with company_scope(self.transport), patch.object(tr_api, '_transport_context', return_value=(self.transport, None, codes, profile, None)):
            self.assertEqual(submit(data).status_code, 201)
            replay = submit(data)
            self.assertEqual(replay.status_code, 409)
            self.assertEqual(json.loads(replay.content)['code'], 'duplicate_operation')
            conflict = submit({**data, 'amount': '6.00'})
            self.assertEqual(json.loads(conflict.content)['code'], 'operation_id_conflict')
            wrong = submit({**data, 'expected_company_id': str(self.a.public_id)})
            self.assertEqual(json.loads(wrong.content)['code'], 'company_context_changed')
            self.assertEqual(TransportExpense.objects.count(), 1)
            self.assertEqual(AuditLog.objects.filter(user=self.actor, metadata__transport_operation_id='m26-expense-1').count(), 1)
