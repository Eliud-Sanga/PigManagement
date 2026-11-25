import json
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.companies.scope import company_scope
from apps.companies.services import create_company
from apps.pigs.models import (
    BatchProfitReport,
    DailySale,
    Expense,
    FarmRecord,
    MeatStock,
    Pig,
    Purchase,
    SlaughterBatch,
)
from apps.companies.middleware import COMPANY_SESSION_KEY
from apps.users.models import AuditLog, User


class MobileOperationApiTests(TestCase):
    def test_purchase_details_are_atomic_and_single_pig_uses_full_total(self):
        base = {
            "supplier_name": "Njombe Farmer", "purchase_date": "2026-10-07",
            "number_of_pigs": "1", "total_cost": "250000",
            "pigs_data": json.dumps([{"gender": "FEMALE", "purchase_price": "",
                                      "notes": "Healthy"}]),
        }
        response = self.post('/api/mobile/v1/pig/purchases/create/', base)
        self.assertEqual(response.status_code, 201, response.content)
        pig = Pig.all_objects.get(company=self.company)
        self.assertEqual(pig.gender, Pig.FEMALE)
        self.assertEqual(pig.purchase_price, Decimal('250000.00'))
        self.assertEqual(pig.notes, 'Healthy')

        base.update(number_of_pigs='2', total_cost='300000',
                    pigs_data=json.dumps([
                        {'gender': 'MALE', 'purchase_price': '200000', 'notes': ''},
                        {'gender': 'FEMALE', 'purchase_price': '200000', 'notes': ''},
                    ]))
        invalid = self.post('/api/mobile/v1/pig/purchases/create/', base)
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertEqual(Purchase.all_objects.filter(company=self.company).count(), 1)
        self.assertEqual(Pig.all_objects.filter(company=self.company).count(), 1)

        base['pigs_data'] = json.dumps([
            {'gender': 'MALE', 'purchase_price': '180000', 'notes': 'First'},
            {'gender': 'FEMALE', 'purchase_price': '120000', 'notes': 'Second'},
        ])
        valid = self.post('/api/mobile/v1/pig/purchases/create/', base)
        self.assertEqual(valid.status_code, 201, valid.content)
        pigs = Pig.all_objects.filter(company=self.company, purchase__supplier_name='Njombe Farmer').order_by('pk')
        self.assertEqual(pigs.count(), 3)
        self.assertEqual(pigs[1].purchase_price, Decimal('180000.00'))
        self.assertEqual(pigs[2].purchase_price, Decimal('120000.00'))

    def test_dashboard_exposes_current_month_income(self):
        response = self.client.get("/api/mobile/v1/pig/dashboard/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["summary"]["monthlyIncome"], "0.00")

    def test_queued_write_refuses_stale_company_context(self):
        response = self.post(
            "/api/mobile/v1/pig/farm/create/",
            {
                "operation_id": "company-guard-test",
                "expected_company_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["code"], "company_context_changed")
        self.assertFalse(FarmRecord.all_objects.filter(company=self.company).exists())

    def setUp(self):
        self.admin = User.objects.create_user(
            username="platform-admin",
            email="platform@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )
        self.owner = User.objects.create_user(
            username="company-owner",
            email="owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )
        self.company = create_company(
            actor=self.admin,
            name="Phase Seven Farm",
            code="phase-seven-farm",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )
        self.client.force_login(
            self.owner,
            backend="apps.companies.backends.CompanyRoleBackend",
        )

    def post(self, path, data):
        return self.client.post(path, data=data)

    def test_module_search_and_pagination_are_database_scoped(self):
        for index in range(31):
            Purchase.all_objects.create(
                company=self.company,
                supplier_name=(
                    "Needle Supplier" if index == 7 else f"Supplier {index:02d}"
                ),
                supplier_phone="0712345678",
                supplier_location="Njombe",
                purchase_date=date(2026, 1, 1) + timedelta(days=index),
                number_of_pigs=1,
                total_cost=Decimal("120000.00"),
                notes="Searchable purchase record",
            )

        second_owner = User.objects.create_user(
            username="second-company-owner",
            email="second-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )
        other_company = create_company(
            actor=self.admin,
            name="Other Farm",
            code="phase-seven-other-farm",
            owner=second_owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )
        Purchase.all_objects.create(
            company=other_company,
            supplier_name="Needle Supplier outside selected company",
            purchase_date=date(2026, 9, 1),
            number_of_pigs=1,
            total_cost=Decimal("150000.00"),
        )
        session = self.client.session
        session[COMPANY_SESSION_KEY] = str(self.company.public_id)
        session.save()

        first = self.client.get(
            "/api/mobile/v1/pig/modules/purchases/?page=1&limit=10"
        )
        self.assertEqual(first.status_code, 200, first.content)
        first_payload = json.loads(first.content)
        self.assertEqual(first_payload["count"], 31)
        self.assertEqual(len(first_payload["items"]), 10)
        self.assertTrue(first_payload["hasNext"])

        second = self.client.get(
            "/api/mobile/v1/pig/modules/purchases/?page=2&limit=10"
        )
        second_payload = json.loads(second.content)
        self.assertEqual(second_payload["count"], 31)
        self.assertEqual(len(second_payload["items"]), 10)
        self.assertTrue(second_payload["hasNext"])
        self.assertTrue(
            set(item["id"] for item in first_payload["items"]).isdisjoint(
                item["id"] for item in second_payload["items"]
            )
        )

        searched = self.client.get(
            "/api/mobile/v1/pig/modules/purchases/?q=Needle&limit=10"
        )
        searched_payload = json.loads(searched.content)
        self.assertEqual(searched.status_code, 200, searched.content)
        self.assertEqual(searched_payload["count"], 1)
        self.assertEqual(
            searched_payload["items"][0]["title"],
            "Needle Supplier",
        )

    def test_module_search_length_is_validated(self):
        response = self.client.get(
            "/api/mobile/v1/pig/modules/purchases/?q=" + ("x" * 101)
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)["code"], "invalid_query")

    def test_purchase_creation_is_atomic_scoped_and_idempotent(self):
        data = {
            "operation_id": "purchase-create-001",
            "supplier_name": "Njombe Supplier",
            "supplier_phone": "0712345678",
            "supplier_location": "Njombe",
            "purchase_date": "2026-09-21",
            "number_of_pigs": "3",
            "total_cost": "900000",
            "notes": "Healthy pigs",
        }

        response = self.post("/api/mobile/v1/pig/purchases/create/", data)

        self.assertEqual(response.status_code, 201, response.content)
        purchase = Purchase.all_objects.get(company=self.company)
        self.assertTrue(purchase.pigs_created)
        self.assertEqual(
            Pig.all_objects.filter(company=self.company).count(),
            3,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=self.owner,
                action="CREATE",
                company_public_id=self.company.public_id,
                metadata__operation_id="purchase-create-001",
            ).exists()
        )

        duplicate = self.post("/api/mobile/v1/pig/purchases/create/", data)

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(
            Purchase.all_objects.filter(company=self.company).count(),
            1,
        )

    def test_full_purchase_slaughter_sale_and_profit_workflow(self):
        purchase_response = self.post(
            "/api/mobile/v1/pig/purchases/create/",
            {
                "operation_id": "workflow-purchase",
                "supplier_name": "Makete Farm",
                "purchase_date": "2026-09-20",
                "number_of_pigs": "2",
                "total_cost": "400000",
            },
        )
        self.assertEqual(purchase_response.status_code, 201)
        pig_ids = list(
            Pig.all_objects.filter(company=self.company)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        slaughter_response = self.post(
            "/api/mobile/v1/pig/slaughter/create/",
            {
                "operation_id": "workflow-slaughter",
                "pig_ids": ",".join(str(value) for value in pig_ids),
                "slaughter_date": "2026-09-21",
            },
        )
        self.assertEqual(slaughter_response.status_code, 201)
        batch = SlaughterBatch.all_objects.get(company=self.company)
        stock = MeatStock.all_objects.get(slaughter_batch=batch)

        sale_response = self.post(
            "/api/mobile/v1/pig/sales/create/",
            {
                "operation_id": "workflow-sale",
                "sale_date": "2026-09-21",
                "batch_id": str(batch.pk),
                "total_money_received": "650000",
                "price_per_kg": "12000",
                "foods": json.dumps(
                    [
                        {
                            "name": "Chips",
                            "quantity": 5,
                            "unitPrice": "10000",
                        }
                    ]
                ),
                "meat_finished": "on",
            },
        )

        self.assertEqual(sale_response.status_code, 201, sale_response.content)
        sale = DailySale.all_objects.get(company=self.company)
        stock.refresh_from_db()
        self.assertEqual(str(sale.total_food_sales), "50000.00")
        self.assertEqual(str(sale.total_meat_sales), "600000.00")
        self.assertTrue(stock.is_finished)
        self.assertEqual(
            BatchProfitReport.all_objects.filter(company=self.company).count(),
            1,
        )

        reports = self.client.get('/api/mobile/v1/pig/modules/reports/?page=1&limit=1')
        self.assertEqual(reports.status_code, 200, reports.content)
        self.assertEqual(reports.json()['count'], 2)
        self.assertTrue(reports.json()['hasNext'])
        next_page = self.client.get('/api/mobile/v1/pig/modules/reports/?page=2&limit=1')
        self.assertEqual(next_page.status_code, 200, next_page.content)
        ids = {reports.json()['items'][0]['id'], next_page.json()['items'][0]['id']}
        self.assertEqual({identifier.split('-')[0] for identifier in ids}, {'daily', 'batch'})

    def test_newest_pig_first_and_archived_pigs_are_company_scoped(self):
        for index, date_value in enumerate(('2026-03-01', '2026-09-01')):
            response = self.post('/api/mobile/v1/pig/purchases/create/', {
                'operation_id': f'ordered-purchase-{index}',
                'supplier_name': f'Supplier {index}',
                'purchase_date': date_value,
                'number_of_pigs': '1',
                'total_cost': '120000',
            })
            self.assertEqual(response.status_code, 201, response.content)
        first = self.client.get('/api/mobile/v1/pig/modules/pigs/')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual([item['subtitle'] for item in first.json()['items']],
                         ['Supplier 1', 'Supplier 0'])
        old_pig = Pig.all_objects.get(company=self.company, purchase__supplier_name='Supplier 0')
        archived = self.post(f'/api/mobile/v1/pig/pigs/{old_pig.pk}/archive/', {
            'operation_id': 'archive-old-pig',
        })
        self.assertEqual(archived.status_code, 200, archived.content)
        self.assertEqual(self.client.get('/api/mobile/v1/pig/modules/pigs/').json()['count'], 1)
        archive_page = self.client.get('/api/mobile/v1/pig/modules/pigs/?archived=1')
        self.assertEqual(archive_page.status_code, 200, archive_page.content)
        self.assertEqual(archive_page.json()['count'], 1)
        self.assertEqual(archive_page.json()['items'][0]['id'], str(old_pig.pk))
        self.assertEqual(archive_page.json()['items'][0]['status'], 'ARCHIVED')

        other_owner = User.objects.create_user(
            username='archive-other-owner', email='archive-other@example.test',
            password='OwnerPassword123!', is_active=True,
        )
        other_company = create_company(
            actor=self.admin, name='Other Archive Farm', code='other-archive-farm',
            owner=other_owner, allowed_languages=['sw', 'en'],
            default_language='sw', step_up_verified=True,
        )
        other_purchase = Purchase.all_objects.create(
            company=other_company, supplier_name='Hidden Other Supplier',
            purchase_date=date(2026, 10, 1), number_of_pigs=1,
            total_cost=Decimal('170000.00'),
        )
        with company_scope(other_company):
            other_pig = Pig.all_objects.create(
                company=other_company, purchase=other_purchase,
                status=Pig.AVAILABLE,
            )
            other_pig.archive()

        same_company_archive = self.client.get(
            '/api/mobile/v1/pig/modules/pigs/?archived=1'
        )
        self.assertEqual(same_company_archive.status_code, 200)
        self.assertEqual(same_company_archive.json()['count'], 1)
        self.assertNotEqual(
            same_company_archive.json()['items'][0]['id'], str(other_pig.pk),
        )

    def test_pig_list_filters_and_purchase_summary_are_scoped(self):
        purchase = Purchase.all_objects.create(
            company=self.company, supplier_name='Njombe',
            purchase_date=date(2026, 10, 1), number_of_pigs=3,
            total_cost=Decimal('300000.00'),
        )
        with company_scope(self.company):
            pigs = [Pig.all_objects.create(
                company=self.company, purchase=purchase, status=status,
                purchase_price=price,
            ) for status, price in (
                (Pig.AVAILABLE, Decimal('120000.00')),
                (Pig.SLAUGHTERED, None),
                (Pig.FINISHED, None),
            )]
        other_owner = User.objects.create_user(
            username='summary-other', email='summary-other@example.test',
            password='OwnerPassword123!', is_active=True,
        )
        other_company = create_company(
            actor=self.admin, name='Other Summary Farm', code='other-summary-farm',
            owner=other_owner, allowed_languages=['sw', 'en'],
            default_language='sw', step_up_verified=True,
        )
        other_purchase = Purchase.all_objects.create(
            company=other_company, supplier_name='Other',
            purchase_date=date(2026, 10, 1), number_of_pigs=1,
            total_cost=Decimal('900000.00'),
        )
        with company_scope(other_company):
            Pig.all_objects.create(company=other_company, purchase=other_purchase)

        url = '/api/mobile/v1/pig/modules/pigs/'
        response = self.client.get(url + '?limit=1')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['count'], 3)
        self.assertEqual(response.json()['pigSummary'], {
            'all': 3, 'available': 1, 'slaughtered': 2,
            'archived': 0, 'totalPurchaseAmount': '320000.00',
        })
        self.assertEqual(self.client.get(url + '?status=available').json()['count'], 1)
        slaughtered = self.client.get(url + '?status=slaughtered&q=Njombe')
        self.assertEqual(slaughtered.json()['count'], 2)
        self.assertEqual({item['id'] for item in slaughtered.json()['items']},
                         {str(pigs[1].pk), str(pigs[2].pk)})
        for query in ('?status=bad', '?archived=1&status=available'):
            self.assertEqual(self.client.get(url + query).status_code, 400)
        self.assertEqual(self.client.get('/api/mobile/v1/pig/modules/sales/?status=available').status_code, 400)

    def test_expense_create_update_and_archive(self):
        create_response = self.post(
            "/api/mobile/v1/pig/expenses/create/",
            {
                "operation_id": "expense-create",
                "expense_date": "2026-09-21",
                "title": "Charcoal",
                "amount": "25000",
                "description": "Kitchen fuel",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        expense = Expense.all_objects.get(company=self.company)

        duplicate = self.post(
            "/api/mobile/v1/pig/expenses/create/",
            {
                "operation_id": "expense-create",
                "expense_date": "2026-09-21",
                "title": "Charcoal",
                "amount": "25000",
                "description": "Kitchen fuel",
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(Expense.all_objects.filter(company=self.company).count(), 1)

        update_response = self.post(
            f"/api/mobile/v1/pig/expenses/{expense.pk}/update/",
            {
                "operation_id": "expense-update",
                "expense_date": "2026-09-21",
                "title": "Charcoal and transport",
                "amount": "30000",
                "description": "Kitchen fuel delivered",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        expense.refresh_from_db()
        self.assertEqual(expense.title, "Charcoal and transport")

        archive_response = self.post(
            f"/api/mobile/v1/pig/expenses/{expense.pk}/archive/",
            {"operation_id": "expense-archive"},
        )
        self.assertEqual(archive_response.status_code, 200)
        expense.refresh_from_db()
        self.assertTrue(expense.is_archived)

    def test_platform_admin_cannot_write_company_business_data(self):
        self.client.logout()
        self.client.force_login(
            self.admin,
            backend="apps.companies.backends.CompanyRoleBackend",
        )

        response = self.post(
            "/api/mobile/v1/pig/expenses/create/",
            {
                "expense_date": "2026-09-21",
                "title": "Forbidden",
                "amount": "1",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "platform_admin_control_plane")

    def test_options_never_exposes_another_company_records(self):
        other_owner = User.objects.create_user(
            username="other-owner",
            email="other@example.test",
            password="OtherPassword123!",
            is_active=True,
        )
        other_company = create_company(
            actor=self.admin,
            name="Other Farm",
            code="other-farm",
            owner=other_owner,
            allowed_languages=["sw"],
            default_language="sw",
            step_up_verified=True,
        )
        Purchase.all_objects.create(
            company=other_company,
            supplier_name="Hidden Supplier",
            purchase_date="2026-09-21",
            number_of_pigs=1,
            total_cost="100000",
        )

        response = self.client.get("/api/mobile/v1/pig/operations/options/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["company"]["id"], str(self.company.public_id))
        self.assertEqual(response.json()["availablePigs"], [])

    def test_record_id_from_another_company_cannot_be_modified(self):
        other_owner = User.objects.create_user(
            username="isolated-owner",
            email="isolated@example.test",
            password="OtherPassword123!",
            is_active=True,
        )
        other_company = create_company(
            actor=self.admin,
            name="Isolated Farm",
            code="isolated-farm",
            owner=other_owner,
            allowed_languages=["sw"],
            default_language="sw",
            step_up_verified=True,
        )
        expense = Expense.all_objects.create(
            company=other_company,
            expense_date="2026-09-21",
            title="Private expense",
            amount="5000",
        )

        response = self.post(
            f"/api/mobile/v1/pig/expenses/{expense.pk}/update/",
            {
                "operation_id": "cross-company-update",
                "expense_date": "2026-09-21",
                "title": "Tampered",
                "amount": "1",
            },
        )

        self.assertEqual(response.status_code, 404)
        expense.refresh_from_db()
        self.assertEqual(expense.title, "Private expense")

    def test_farm_ledger_scoped_create_edit_archive_and_duplicate_guard(self):
        source = '/api/mobile/v1/pig/farm/create/'
        payload = {
            'operation_id': 'feed-001', 'kind': 'FEED',
            'record_date': '2026-09-29', 'title': 'Starter feed',
            'quantity': '25', 'unit': 'kg', 'cost': '14000',
            'notes': 'Morning batch',
        }
        created = self.post(source, payload)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(self.post(source, payload).status_code, 409)
        record = FarmRecord.all_objects.get(company=self.company)
        self.assertEqual(record.quantity, Decimal('25.00'))
        page = self.client.get('/api/mobile/v1/pig/modules/farm/?q=Starter')
        self.assertEqual(page.status_code, 200, page.content)
        self.assertEqual(page.json()['count'], 1)
        self.assertEqual(page.json()['items'][0]['id'], str(record.pk))
        updated = self.post(f'/api/mobile/v1/pig/farm/{record.pk}/update/', {
            **payload, 'operation_id': 'feed-002', 'title': 'Grower feed',
        })
        self.assertEqual(updated.status_code, 200, updated.content)
        record.refresh_from_db()
        self.assertEqual(record.title, 'Grower feed')
        archived = self.post(f'/api/mobile/v1/pig/farm/{record.pk}/archive/',
                             {'operation_id': 'feed-003'})
        self.assertEqual(archived.status_code, 200, archived.content)
        self.assertEqual(self.client.get('/api/mobile/v1/pig/modules/farm/').json()['count'], 0)

    def test_mortality_changes_pig_once_and_preserves_record(self):
        purchase = self.post('/api/mobile/v1/pig/purchases/create/', {
            'operation_id': 'farm-purchase', 'supplier_name': 'Njombe',
            'purchase_date': '2026-09-28', 'number_of_pigs': '1',
            'total_cost': '120000',
        })
        self.assertEqual(purchase.status_code, 201, purchase.content)
        pig = Pig.all_objects.get(company=self.company)
        payload = {'operation_id': 'mortality-001', 'kind': 'MORTALITY',
                   'pig_id': str(pig.pk), 'record_date': '2026-09-29',
                   'title': 'Veterinary confirmed loss'}
        created = self.post('/api/mobile/v1/pig/farm/create/', payload)
        self.assertEqual(created.status_code, 201, created.content)
        pig.refresh_from_db()
        self.assertEqual(pig.status, Pig.DECEASED)
        self.assertEqual(self.post('/api/mobile/v1/pig/farm/create/', payload).status_code, 409)
        record = FarmRecord.all_objects.get(company=self.company)
        self.assertEqual(self.post(f'/api/mobile/v1/pig/farm/{record.pk}/archive/',
                                   {'operation_id': 'mortality-archive'}).status_code, 409)
        self.assertEqual(self.post(f'/api/mobile/v1/pig/farm/{record.pk}/update/',
                                   {**payload, 'operation_id': 'mortality-edit'}).status_code, 409)
        self.assertFalse(FarmRecord.all_objects.get(pk=record.pk).is_archived)

    def test_farm_rejects_foreign_pig_and_foreign_record(self):
        other_owner = User.objects.create_user(username='farm-other',
            email='farm-other@example.test', password='OtherPassword123!', is_active=True)
        other = create_company(actor=self.admin, name='Other Farm',
            code='farm-other', owner=other_owner, allowed_languages=['sw'],
            default_language='sw', step_up_verified=True)
        purchase = Purchase.all_objects.create(company=other, supplier_name='Other',
            purchase_date='2026-09-20', number_of_pigs=1, total_cost='200000')
        pig = Pig.all_objects.create(company=other, purchase=purchase, status=Pig.AVAILABLE)
        payload = {'operation_id': 'foreign-pig', 'kind': 'WEIGHT',
            'pig_id': str(pig.pk), 'record_date': '2026-09-29',
            'title': 'Cross tenant', 'quantity': '45', 'unit': 'kg'}
        self.assertEqual(self.post('/api/mobile/v1/pig/farm/create/', payload).status_code, 404)
        self.assertFalse(FarmRecord.all_objects.filter(company=self.company).exists())
        foreign = FarmRecord.all_objects.create(company=other, pig=pig,
            kind=FarmRecord.WEIGHT, record_date='2026-09-29', title='Private',
            quantity=Decimal('45'), unit='kg')
        self.assertEqual(self.post(f'/api/mobile/v1/pig/farm/{foreign.pk}/update/',
            {**payload, 'operation_id': 'foreign-update'}).status_code, 404)
        self.assertEqual(self.post(f'/api/mobile/v1/pig/farm/{foreign.pk}/archive/',
            {'operation_id': 'foreign-archive'}).status_code, 404)
        self.assertEqual(self.client.get('/api/mobile/v1/pig/modules/farm/').json()['count'], 0)
