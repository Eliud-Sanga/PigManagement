from django.contrib.auth import get_user_model
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from apps.companies.models import CompanyMembership
from apps.companies.services import create_company
from apps.control_center.models import SecurityAuditEvent
from apps.users.models import GoogleIdentity

BASE = '/api/mobile/v1/platform/accounts/'
BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class PlatformAccountStateTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username='state-admin', email='admin@example.test',
            password='fixture-pass', is_platform_admin=True)
        self.owner = users.objects.create_user(username='state-owner', email='owner@example.test', password='fixture-pass')
        self.target = users.objects.create_user(username='state-target', email='target@example.test', password='fixture-pass')
        self.company = create_company(actor=self.admin, name='Fixture', code='fixture', owner=self.owner,
            allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        role = self.company.roles.filter(is_active=True).exclude(code='OWNER').first()
        CompanyMembership.objects.create(company=self.company, user=self.target, role=role,
            status=CompanyMembership.Status.ACTIVE, requested_language='sw', approved_language='sw',
            approved_at=timezone.now(), created_by=self.admin, approved_by=self.admin)
        self.client.force_login(self.admin, backend=BACKEND)

    def url(self, user):
        return BASE + f'{user.public_id}/state/'

    def state(self, user):
        response = self.client.get(self.url(user))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def change(self, user, action, revision=None, password='fixture-pass'):
        return self.client.post(self.url(user) + 'update/', {'action': action,
            'revision': revision or self.state(user)['revision'], 'admin_password': password})

    def test_non_admin_and_csrf_are_rejected(self):
        self.client.force_login(self.target, backend=BACKEND)
        self.assertEqual(self.client.get(self.url(self.owner)).status_code, 403)
        self.assertEqual(self.client.post(self.url(self.owner) + 'update/', {}).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin, backend=BACKEND)
        self.assertEqual(client.post(self.url(self.target) + 'update/', {}).status_code, 403)

    def test_deactivation_revokes_existing_session_even_after_reactivation(self):
        client = Client()
        client.force_login(self.target, backend=BACKEND)
        self.assertEqual(client.get('/api/mobile/v1/auth/session/').status_code, 200)
        response = self.change(self.target, 'deactivate')
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertEqual(client.get('/api/mobile/v1/auth/session/').status_code, 401)
        self.assertEqual(self.change(self.target, 'activate').status_code, 200)
        self.assertEqual(client.get('/api/mobile/v1/auth/session/').status_code, 401)
        self.assertEqual(SecurityAuditEvent.objects.filter(action='PLATFORM_ACCOUNT_STATUS',
            result=SecurityAuditEvent.Result.SUCCESS, subject_user=self.target).count(), 2)

    def test_wrong_password_and_stale_revision_cannot_change_state(self):
        revision = self.state(self.target)['revision']
        self.assertEqual(self.change(self.target, 'deactivate', revision, 'wrong').status_code, 403)
        self.target.is_active = False
        self.target.save(update_fields=['is_active'])
        self.target.is_active = True
        self.target.save(update_fields=['is_active'])
        self.assertEqual(self.change(self.target, 'deactivate', revision).status_code, 409)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)

    def test_admin_cannot_disable_self(self):
        self.assertEqual(self.change(self.admin, 'deactivate').status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_sole_owner_cannot_be_disabled(self):
        response = self.change(self.owner, 'deactivate')
        self.assertEqual(response.status_code, 400)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_inactive_alternative_owner_does_not_satisfy_owner_guard(self):
        role = self.company.roles.get(code='OWNER')
        self.target.is_active = False
        self.target.save(update_fields=['is_active'])
        CompanyMembership.objects.filter(user=self.target, company=self.company).update(role=role)
        self.assertEqual(self.change(self.owner, 'deactivate').status_code, 400)

    def test_owner_with_another_active_owner_can_be_disabled(self):
        role = self.company.roles.get(code='OWNER')
        CompanyMembership.objects.filter(user=self.target, company=self.company).update(role=role)
        self.assertEqual(self.change(self.owner, 'deactivate').status_code, 200)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)

    def test_unactivated_account_cannot_be_enabled_without_signin_method(self):
        user = get_user_model()(username='new-state', email='new@example.test', is_active=False)
        user.set_unusable_password()
        user.save()
        self.assertFalse(self.state(user)['has_sign_in_method'])
        self.assertEqual(self.change(user, 'activate').status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_previously_linked_google_identity_can_be_reactivated(self):
        user = get_user_model()(username='google-state', email='google@example.test', is_active=False)
        user.set_unusable_password()
        user.save()
        GoogleIdentity.objects.create(user=user, subject='verified-fixture-subject', email_at_link=user.email)
        self.assertTrue(self.state(user)['has_sign_in_method'])
        self.assertEqual(self.change(user, 'activate').status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active)


class ConcurrentPlatformAccountStateTests(TransactionTestCase):
    def test_two_admins_cannot_disable_each_other_concurrently(self):
        if connection.vendor != 'sqlite':
            self.skipTest('Concurrency test uses SQLite locking semantics')
        users = get_user_model()
        first = users.objects.create_user(username='race-admin-a', email='a@example.test',
            password='fixture-pass', is_platform_admin=True)
        second = users.objects.create_user(username='race-admin-b', email='b@example.test',
            password='fixture-pass', is_platform_admin=True)
        clients = [Client(), Client()]
        clients[0].force_login(first, backend=BACKEND)
        clients[1].force_login(second, backend=BACKEND)
        targets = [second, first]
        revisions = []
        for client, target in zip(clients, targets):
            response = client.get(BASE + f'{target.public_id}/state/')
            self.assertEqual(response.status_code, 200)
            revisions.append(response.json()['revision'])
        barrier = Barrier(2, timeout=10)

        def verified(*args, **kwargs):
            barrier.wait()
            return True

        def submit(index):
            try:
                response = clients[index].post(BASE + f'{targets[index].public_id}/state/update/',
                    {'action': 'deactivate', 'revision': revisions[index], 'admin_password': 'fixture-pass'})
                return response.status_code
            finally:
                close_old_connections()

        with patch('apps.mobile_api.platform_account_state_views._verify_admin_password', side_effect=verified):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(submit, range(2)))
        self.assertTrue(all(code in (200, 403, 409) for code in results), results)
        self.assertGreaterEqual(users.objects.filter(is_platform_admin=True, is_active=True).count(), 1)
