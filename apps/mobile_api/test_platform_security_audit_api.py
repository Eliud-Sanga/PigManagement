from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.companies.services import create_company
from apps.control_center.models import SecurityAuditEvent
from apps.control_center.services import append_security_event
from apps.users.models import AuditLog

BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class PlatformSecurityAuditTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username='audit-admin38', email='audit-admin38@example.test',
            password='fixture-password', is_platform_admin=True)
        self.user = get_user_model().objects.create_user(username='audit-user38', email='audit-user38@example.test',
            password='fixture-password')
        create_company(actor=self.admin, name='Audit fixture', code='audit-fixture38', owner=self.user,
            allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        self.event = append_security_event(category=SecurityAuditEvent.Category.IDENTITY,
            action='AUDIT_EXAMPLE_38', result=SecurityAuditEvent.Result.SUCCESS,
            actor=self.admin, description='Example event', metadata={'private_fixture': 'do-not-expose'})
        self.client.force_login(self.admin, backend=BACKEND)
        self.url = '/api/mobile/v1/platform/lists/security_audit/'

    def test_admin_list_search_and_detail_show_security_event(self):
        result = self.client.get(self.url, {'q': 'AUDIT_EXAMPLE_38'})
        self.assertEqual(result.status_code, 200)
        self.assertIn('no-store', result.headers['Cache-Control'])
        self.assertEqual(len(result.json()['items']), 1)
        self.assertEqual(result.json()['items'][0]['id'], str(self.event.event_id))
        result = self.client.get(self.url + f'{self.event.event_id}/')
        self.assertEqual(result.status_code, 200)
        value = result.json()
        self.assertEqual(value['fields']['action'], 'AUDIT_EXAMPLE_38')
        self.assertEqual(value['fields']['description'], 'Example event')
        self.assertEqual(value['control_path'], reverse('users:audit_detail', args=[self.event.event_id]))
        self.assertNotIn('private_fixture', str(value))
        self.assertNotIn('event_hash', value['fields'])

    def test_list_is_paginated_and_bounded(self):
        for number in range(21):
            append_security_event(category=SecurityAuditEvent.Category.IDENTITY,
                action=f'BOUND_38_{number}', result=SecurityAuditEvent.Result.SUCCESS, actor=self.admin)
        response = self.client.get(self.url, {'q': 'BOUND_38_'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['items']), 20)
        self.assertTrue(response.json()['has_next'])
        second = self.client.get(self.url, {'q': 'BOUND_38_', 'page': 2})
        self.assertEqual(len(second.json()['items']), 1)

    def test_legacy_activity_remains_accessible_without_invalid_security_detail_link(self):
        event = AuditLog.objects.create(user=self.admin, action='VIEW', result='SUCCESS',
            model_name='Fixture', description='Legacy event')
        result = self.client.get(f'/api/mobile/v1/platform/lists/audit/{event.event_id}/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['fields']['description'], 'Legacy event')
        self.assertEqual(result.json()['control_path'], reverse('users:audit_logs'))

    def test_non_admin_is_denied_without_exposing_event(self):
        self.client.force_login(self.user, backend=BACKEND)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url + f'{self.event.event_id}/').status_code, 403)
