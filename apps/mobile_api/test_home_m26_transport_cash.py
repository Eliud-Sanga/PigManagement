from decimal import Decimal
from datetime import timedelta
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from apps.companies.models import Company
from apps.companies.scope import company_scope
from apps.transport.models import Vehicle, Route, Trip, Cargo, CargoPayment
from apps.transport.round_trip import depart
from .transport_views import _monthly_cash_summary

class CashLedgerTests(TestCase):
    def test_receipts_from_old_cargo_and_company_isolation(self):
        user = get_user_model().objects.create(username='cash-test', email='cash-test@example.invalid')
        now = timezone.now()
        previous = timezone.localdate().replace(day=1)-timedelta(days=1)
        companies = [Company.objects.create(name=f'Cash {i}', code=f'cash-{i}', created_by=user, business_type='transport') for i in range(2)]
        for i, company in enumerate(companies):
            with company_scope(company):
                v = Vehicle.objects.create(company=company, name='Truck', plate_number=f'CASH{i}')
                r = Route.objects.create(company=company, vehicle=v, start_location='Ikonda', end_location='Mbeya')
                t = Trip.objects.create(company=company, route=r, origin='Ikonda', destination='Mbeya')
                cargo = Cargo.objects.create(company=company, trip=t, cargo_date=previous, cargo_type='GOODS', description='Goods', customer_name='Test', pricing_method='TOTAL', original_amount=Decimal('1000'), agreed_amount=Decimal('1000'))
                CargoPayment.objects.create(company=company, cargo=cargo, amount=Decimal('200') if i==0 else Decimal('900'), payment_date=now)
        codes={'transport.view_cargo','transport.view_cargopayment'}
        profile=SimpleNamespace(cargo_enabled=True, passenger_enabled=False)
        cash=_monthly_cash_summary(companies[0],codes,profile,timezone.localdate())
        self.assertEqual(Decimal(cash['monthlyCargoCash']),Decimal('200'))
        self.assertEqual(Decimal(cash['cargoOutstanding']),Decimal('800'))
        self.assertNotIn('monthlyCargoCash',_monthly_cash_summary(companies[0],{'transport.view_cargo'},profile,timezone.localdate()))

    def test_explicit_previous_arrival_and_late_entry(self):
        user = get_user_model().objects.create(username='trip-test',email='trip-test@example.invalid')
        company = Company.objects.create(name='Trip test',code='trip-test',created_by=user,business_type='transport')
        with company_scope(company):
            v=Vehicle.objects.create(company=company,name='Truck',plate_number='TRIPTEST')
            first=depart(company,v.pk,'Ikonda','Mbeya')
            second=depart(company,v.pk,'Mbeya','Ikonda',close_previous=True)
            first.refresh_from_db()
            self.assertEqual(first.status,Trip.Status.COMPLETED)
            self.assertEqual(second.status,Trip.Status.IN_PROGRESS)
            cargo=Cargo(company=company,trip=first,cargo_type='GOODS',description='Forgotten cargo',customer_name='Test',pricing_method='TOTAL',original_amount=100,agreed_amount=100)
            cargo._allow_completed_trip_entry=True
            cargo.save()
            self.assertEqual(cargo.trip_id,first.pk)


class CargoIntakeReportTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Permission
        from django.test import Client
        from django.urls import reverse
        from apps.companies.models import CompanyRole, CompanyMembership
        from apps.transport.models import TransportProfile
        self.reverse = reverse
        self.user = get_user_model().objects.create(username='intake-owner',email='intake-owner@example.invalid')
        self.company = Company.objects.create(name='Intake company',code='intake-company',created_by=self.user,business_type='transport')
        self.other = Company.objects.create(name='Other fleet',code='other-fleet',created_by=self.user,business_type='transport')
        role = CompanyRole.objects.create(company=self.company,code='INTAKE_TEST',name='Intake Test')
        role.permissions.add(*Permission.objects.filter(content_type__app_label='transport'))
        CompanyMembership.objects.create(company=self.company,user=self.user,role=role,status=CompanyMembership.Status.ACTIVE,created_by=self.user,approved_by=self.user,approved_at=timezone.now())
        TransportProfile.objects.update_or_create(company=self.company,defaults={'cargo_enabled':True,'passenger_enabled':False})
        with company_scope(self.company):
            self.vehicle = Vehicle.objects.create(company=self.company,name='Truck A',plate_number='INTAKE-A')
            self.vehicle2 = Vehicle.objects.create(company=self.company,name='Truck B',plate_number='INTAKE-B')
            self.route = Route.objects.create(company=self.company,vehicle=self.vehicle,start_location='Ikonda',end_location='Mbeya')
            self.trip = Trip.objects.create(company=self.company,route=self.route,origin='Ikonda',destination='Mbeya')
            route2 = Route.objects.create(company=self.company,vehicle=self.vehicle2,start_location='Ikonda',end_location='Njombe')
            self.trip2 = Trip.objects.create(company=self.company,route=route2,origin='Ikonda',destination='Njombe')
        with company_scope(self.other):
            self.foreign = Vehicle.objects.create(company=self.other,name='Other truck',plate_number='INTAKE-X')
        self.client = Client()
        self.client.force_login(self.user)
        result = self.client.post(reverse('mobile_api:select_company'),{'company':str(self.company.public_id)})
        self.assertEqual(result.status_code,200,result.content)

    def intake(self, trip, cargo_type='TIMBER', **fields):
        response = self.client.post(self.reverse('mobile_api:transport_create',args=('cargo',)), {'trip_id':str(trip.pk),'cargo_type':cargo_type,'customer_name':'Synthetic customer','description':'Cargo name','intake':'1','late_entry':'1',**fields})
        self.assertEqual(response.status_code,201,response.content)
        return Cargo.all_objects.get(pk=response.json()['item']['id'])

    def settle(self, cargo, sizes, amount='1000'):
        import json
        return self.client.post(self.reverse('mobile_api:transport_action',args=('cargo',cargo.pk,'settle')),{'sizes':json.dumps(sizes),'agreed_amount':amount})

    def test_intake_settlement_and_payment_ledger(self):
        from apps.transport.models import CargoSize
        cargo=self.intake(self.trip)
        self.assertEqual(cargo.payment_status,Cargo.PaymentStatus.PRICE_PENDING)
        self.assertIsNone(cargo.agreed_amount)
        premature=self.client.post(self.reverse('mobile_api:transport_create',args=('cargo_payments',)),{'cargo_id':str(cargo.pk),'amount':'100'})
        self.assertEqual(premature.status_code,400)
        response=self.settle(cargo,[{'size':'2x4','quantity':15},{'size':'2x6','quantity':10}])
        self.assertEqual(response.status_code,200,response.content)
        cargo.refresh_from_db()
        self.assertTrue(cargo.delivered)
        self.assertEqual(cargo.quantity,25)
        self.assertEqual(cargo.balance_due,Decimal('1000'))
        self.assertEqual(CargoSize.all_objects.filter(cargo=cargo).count(),2)
        payment=self.client.post(self.reverse('mobile_api:transport_create',args=('cargo_payments',)),{'cargo_id':str(cargo.pk),'amount':'300'})
        self.assertEqual(payment.status_code,201,payment.content)
        cargo.refresh_from_db()
        self.assertEqual(cargo.balance_due,Decimal('700'))
        self.assertEqual(cargo.payment_status,Cargo.PaymentStatus.PARTIAL)
        report=self.client.get(self.reverse('mobile_api:transport_module',args=('cargo',)),{'report':'1','vehicle':self.vehicle.pk}).json()
        self.assertEqual(Decimal(report['reportSummary']['monthAmount']),Decimal('300'))
        self.assertEqual(len(report['items'][0]['sizeDetails']),2)
        self.assertFalse(report['canCreate'])
        self.assertFalse(report['canChange'])
        self.assertEqual(report['formOptions'],{})

    def test_invalid_settlement_is_atomic_and_cannot_reprice(self):
        from apps.transport.models import CargoSize
        cargo=self.intake(self.trip)
        invalid=self.settle(cargo,[{'size':'2x4','quantity':10},{'size':'2x4','quantity':5}])
        self.assertEqual(invalid.status_code,400)
        self.assertFalse(CargoSize.all_objects.filter(cargo=cargo).exists())
        cargo.refresh_from_db()
        self.assertIsNone(cargo.agreed_amount)
        self.assertFalse(cargo.delivered)
        good=self.settle(cargo,[{'size':'2x4','quantity':10}])
        self.assertEqual(good.status_code,200,good.content)
        repeat=self.settle(cargo,[{'size':'2x6','quantity':99}],amount='9999')
        self.assertEqual(repeat.status_code,400)
        cargo.refresh_from_db()
        self.assertEqual(cargo.agreed_amount,Decimal('1000'))
        self.assertEqual(CargoSize.all_objects.filter(cargo=cargo).count(),1)

    def test_vehicle_reports_and_debt_filter_are_company_scoped(self):
        first=self.intake(self.trip,'GOODS')
        second=self.intake(self.trip2,'GOODS')
        self.assertEqual(self.settle(first,[]).status_code,200)
        endpoint=self.reverse('mobile_api:transport_module',args=('cargo',))
        debts=self.client.get(endpoint,{'paymentStatus':'debt'}).json()
        self.assertEqual([item['id'] for item in debts['items']],[first.pk])
        report=self.client.get(endpoint,{'report':'1','vehicle':self.vehicle2.pk}).json()
        self.assertEqual([item['id'] for item in report['items']],[second.pk])
        self.assertEqual(report['reportSummary']['pending'],1)
        self.assertEqual(self.client.get(endpoint,{'report':'1','vehicle':self.foreign.pk}).status_code,404)
        self.assertEqual(self.client.get(endpoint,{'vehicle':self.vehicle.pk}).status_code,403)
        from django.contrib.auth.models import Permission
        from apps.companies.models import CompanyMembership
        role=CompanyMembership.objects.get(company=self.company,user=self.user).role
        role.permissions.remove(Permission.objects.get(content_type__app_label='transport',codename='view_vehicle'))
        self.assertEqual(self.client.get(endpoint,{'report':'1','vehicle':self.vehicle.pk}).status_code,403)

    def test_intake_sizes_saved_without_price_and_invalid_sizes_atomic(self):
        import json
        from apps.transport.models import CargoSize
        sizes=[{'size':'2x4','quantity':12},{'size':'2x6','quantity':8}]
        cargo=self.intake(self.trip,sizes=json.dumps(sizes))
        self.assertIsNone(cargo.agreed_amount)
        self.assertEqual(cargo.quantity,20)
        self.assertEqual(CargoSize.all_objects.filter(cargo=cargo).count(),2)
        self.assertEqual(cargo.payment_status,Cargo.PaymentStatus.PRICE_PENDING)
        before=Cargo.all_objects.filter(company=self.company).count()
        invalid=self.client.post(self.reverse('mobile_api:transport_create',args=('cargo',)),{'trip_id':str(self.trip.pk),'cargo_type':'TIMBER','customer_name':'Invalid','intake':'1','sizes':json.dumps([{'size':'2x4','quantity':0}])})
        self.assertEqual(invalid.status_code,400)
        self.assertEqual(Cargo.all_objects.filter(company=self.company).count(),before)
        self.assertEqual(self.settle(cargo,sizes).status_code,200)
        cargo.refresh_from_db()
        self.assertEqual(cargo.quantity,20)
        self.assertEqual(CargoSize.all_objects.filter(cargo=cargo).count(),2)
