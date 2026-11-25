from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.companies.scope import company_scope
from apps.companies.services import create_company
from apps.pigs.models import DailySale, Expense, Purchase, BatchProfitReport, SlaughterBatch
from apps.users.models import User
from .test_operation_api import MobileOperationApiTests

BASE='/api/mobile/v1/pig/reports/'
PERIOD={'start':'2026-01-01','end':'2026-01-31','group':'day'}

class PeriodReportTests(TestCase):
    setUp=MobileOperationApiTests.setUp

    def seed_sales(self, days=31):
        for index in range(days):
            DailySale.all_objects.create(company=self.company, sale_date=date(2026,1,1)+timedelta(days=index), total_money_received=Decimal('100.00'), total_meat_sales=Decimal('70.00'), total_food_sales=Decimal('30.00'))

    def test_period_totals_and_dates_ignore_pagination_and_search(self):
        self.seed_sales()
        for extra in ({},{'page':2},{'q':'2026-01-15'}):
            data=self.client.get(BASE+'sales/',{**PERIOD,**extra}).json()
            self.assertEqual(Decimal(data['summary']['sales']['total']),3100)
            self.assertEqual(data['summary']['sales']['count'],31)
            self.assertEqual(len(data['series']['sales']),31)
        page=self.client.get(BASE+'sales/',{**PERIOD,'page':2}).json()
        self.assertEqual(len(page['items']),6)
        self.assertFalse(page['hasNext'])
        self.assertEqual(page['items'][0]['date'],'2026-01-06')

    def test_empty_is_real_zero_not_missing_permissions(self):
        data=self.client.get(BASE+'all/',PERIOD).json()
        self.assertEqual(Decimal(data['summary']['profit']['total']),0)
        self.assertEqual(data['series']['profit'],[])
        self.member = self.company.memberships.get(user=self.owner)
        self.member.denied_permissions.add(Permission.objects.get(content_type__app_label='pigs',codename='view_batchprofitreport'))
        data=self.client.get(BASE+'all/',PERIOD).json()
        self.assertFalse(data['available']['profit'])
        self.assertNotIn('profit',data['summary'])
        self.assertEqual(self.client.get(BASE+'profit/',PERIOD).status_code,403)

    def test_date_validation_and_post_rejection(self):
        for period in ({}, {'start':'bad','end':'2026-01-01'}, {'start':'2026-02-01','end':'2026-01-01'}, {**PERIOD,'page':'NaN'}, {**PERIOD,'group':'sql'}, {'start':'2000-01-01','end':'2026-01-01'}):
            self.assertEqual(self.client.get(BASE+'sales/',period).status_code,400)
        self.assertEqual(self.client.post(BASE+'sales/',PERIOD).status_code,405)

    def test_cross_company_data_never_enters_totals(self):
        self.seed_sales(1)
        owner=User.objects.create_user(username='second',email='second@example.test',password='test-only-password')
        company=create_company(actor=self.admin,name='Other',code='other-report',owner=owner,allowed_languages=['sw'],default_language='sw',step_up_verified=True)
        DailySale.all_objects.create(company=company,sale_date=date(2026,1,1),total_money_received=Decimal('999999'))
        data=self.client.get(BASE+'sales/',PERIOD).json()
        self.assertEqual(Decimal(data['summary']['sales']['total']),100)
        self.assertEqual(data['company']['id'],str(self.company.public_id))

    def test_platform_admin_and_anonymous_denied(self):
        self.client.logout()
        self.assertEqual(self.client.get(BASE+'all/',PERIOD).status_code,401)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(BASE+'all/',PERIOD).status_code,403)

    def test_group_month_preserves_authoritative_sum(self):
        self.seed_sales()
        data=self.client.get(BASE+'sales/',{**PERIOD,'group':'month'}).json()
        self.assertEqual(len(data['series']['sales']),1)
        self.assertEqual(Decimal(data['series']['sales'][0]['total']),3100)

    def test_query_count_does_not_grow_with_record_count(self):
        self.seed_sales(1)
        with CaptureQueriesContext(connection) as small:
            self.client.get(BASE+'sales/',PERIOD)
        for index in range(1,31):
            DailySale.all_objects.create(company=self.company,sale_date=date(2026,1,1)+timedelta(days=index),total_money_received=1)
        with CaptureQueriesContext(connection) as large:
            self.client.get(BASE+'sales/',PERIOD)
        self.assertLessEqual(len(large),len(small)+1)

    def test_profit_is_existing_completed_batch_amount_not_sales_minus_expenses(self):
        with company_scope(self.company):
            batch=SlaughterBatch.objects.create(slaughter_date=date(2026,1,1),batch_number='REPORT-BATCH')
            report=BatchProfitReport.objects.create(slaughter_batch=batch,total_pigs=2,total_purchase_cost=Decimal('400'),total_meat_sales=Decimal('750'),finalized_date=date(2026,1,20))
            Expense.objects.create(expense_date=date(2026,1,20),title='Transport',amount=999)
        data=self.client.get(BASE+'profit/',PERIOD).json()
        self.assertEqual(Decimal(data['summary']['profit']['total']),350)
        detail=self.client.get(BASE+f'profit/{report.pk}/').json()
        self.assertEqual(detail['report']['pigs'],2)
        self.assertNotIn('perPigProfit',detail['report'])
