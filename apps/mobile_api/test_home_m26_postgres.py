"""Exercise real concurrent governance requests on PostgreSQL, not SQLite."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from secrets import token_urlsafe

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.constants import ROLE_OWNER
from apps.companies.models import Company, CompanyMembership, CompanyRole, CompanyAuditEvent
from apps.control_center import company_views
from apps.control_center.account_state import revision
from apps.control_center.models import SecurityAuditEvent
from . import platform_account_state_views, platform_views


class PostgreSQLGovernanceTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql', 'A4 requires PostgreSQL')
        self.secret = token_urlsafe(24)
        self.a = self.user('a', admin=True)
        self.b = self.user('b', admin=True)

    def user(self, suffix, admin=False):
        return get_user_model().objects.create_user(
            username=f'm26-a4-{suffix}', email=f'm26-a4-{suffix}@example.invalid',
            password=self.secret, is_platform_admin=admin,
        )

    def race(self, calls, modules):
        # Real password checks and real requests; synchronize only the point
        # immediately after authentication, before the database locks are taken.
        barrier = Barrier(2, timeout=30)
        original = company_views._verify_admin_password

        def synchronized(*args, **kwargs):
            valid = original(*args, **kwargs)
            if not valid:
                raise AssertionError('Synthetic administrator authentication failed')
            barrier.wait()
            return valid

        def worker(call):
            actor_id, url, data = call
            try:
                conn = connections['default']
                with conn.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '30s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pid = cursor.fetchone()[0]
                actor = get_user_model().objects.get(pk=actor_id)
                client = Client()
                client.force_login(actor)
                response = client.post(url, data, secure=True)
                payload = response.json() if response.get('Content-Type', '').startswith('application/json') else None
                return backend_pid, response.status_code, payload
            finally:
                connections.close_all()

        from contextlib import ExitStack
        with ExitStack() as stack:
            for module in modules:
                stack.enter_context(patch.object(module, '_verify_admin_password', side_effect=synchronized))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker, call) for call in calls]
                results = [future.result(timeout=75) for future in futures]
        self.assertNotEqual(results[0][0], results[1][0])
        return [(status, payload) for _, status, payload in results]

    def test_two_admins_cannot_deactivate_each_other(self):
        calls = []
        for actor, target in ((self.a, self.b), (self.b, self.a)):
            calls.append((actor.pk, reverse('mobile_api:platform_account_state_update', args=[target.public_id]),
                          {'action': 'deactivate', 'admin_password': self.secret, 'revision': revision(target)}))
        results = self.race(calls, [platform_account_state_views])
        self.assertEqual(sorted(status for status, _ in results), [200, 403], results)
        self.assertEqual([data['code'] for status, data in results if status == 403], ['platform_admin_required'])
        self.assertEqual(get_user_model().objects.filter(pk__in=[self.a.pk, self.b.pk], is_active=True).count(), 1)
        self.assertEqual(SecurityAuditEvent.objects.filter(action='PLATFORM_ACCOUNT_STATUS', result='SUCCESS').count(), 1)

    def test_web_and_mobile_cannot_suspend_last_two_owners(self):
        company = Company.objects.create(name='Synthetic M26 company', code='m26-a4-co', created_by=self.a)
        role, _ = CompanyRole.objects.get_or_create(company=company, code=ROLE_OWNER, defaults={'name': 'Owner', 'is_active': True})
        self.assertTrue(role.is_active)
        members = [CompanyMembership.objects.create(company=company, role=role, user=self.user(suffix),
                    status=CompanyMembership.Status.ACTIVE, created_by=self.a, approved_by=self.a,
                    approved_at=timezone.now(), approved_language='en', requested_language='en') for suffix in ('owner1', 'owner2')]
        web = reverse('users:membership_status', args=[members[0].public_id, 'suspend'])
        mobile = reverse('mobile_api:platform_company_member_status', args=[members[1].public_id])
        results = self.race([
            (self.a.pk, web, {'admin_password': self.secret}),
            (self.b.pk, mobile, {'action': 'suspend', 'admin_password': self.secret}),
        ], [company_views, platform_views])
        self.assertEqual(results[0][0], 302, results)
        self.assertIn(results[1][0], (200, 400), results)
        if results[1][0] == 400:
            self.assertEqual(results[1][1]['code'], 'action_denied')
        statuses = dict(CompanyMembership.objects.filter(pk__in=[m.pk for m in members]).values_list('pk', 'status'))
        self.assertCountEqual(statuses.values(), [CompanyMembership.Status.ACTIVE, CompanyMembership.Status.SUSPENDED])
        self.assertEqual(statuses[members[1].pk] == CompanyMembership.Status.SUSPENDED, results[1][0] == 200)
        self.assertEqual(CompanyAuditEvent.objects.filter(company=company, action='MEMBERSHIP_STATUS_CHANGED').count(), 1)
        self.assertEqual(SecurityAuditEvent.objects.filter(company=company, action='MEMBERSHIP_STATUS_CHANGE', result='SUCCESS').count(), 1)
