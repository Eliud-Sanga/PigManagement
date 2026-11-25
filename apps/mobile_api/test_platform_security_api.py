from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from apps.control_center.models import SecurityAuditEvent, SecuritySettings
from apps.companies.services import create_company


BASE = '/api/mobile/v1/platform/security/'
BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class PlatformSecurityApiTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username='security-admin', email='admin@example.test',
                                               password='fixture-pass', is_platform_admin=True)
        self.other = users.objects.create_user(username='security-user', email='user@example.test',
                                               password='fixture-pass')
        create_company(actor=self.admin, name='Security Test', code='security-test', owner=self.other,
                       allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        self.policy = SecuritySettings.get_solo()
        self.client.force_login(self.admin, backend=BACKEND)

    def payload(self):
        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        fields = {k: '' if v is None else str(v).lower() if isinstance(v, bool) else str(v)
                  for k, v in result['settings'].items()}
        fields.update(revision=result['revision'], admin_password='fixture-pass')
        return fields

    def test_non_admin_cannot_read_or_modify(self):
        self.client.force_login(self.other, backend=BACKEND)
        self.assertEqual(self.client.get(BASE).status_code, 403)
        self.assertEqual(self.client.post(BASE + 'update/', {}).status_code, 403)
        self.assertEqual(self.client.get('/api/mobile/v1/platform/policy-overview/').status_code, 403)

    def test_policy_overview_contains_aggregate_counts(self):
        response = self.client.get('/api/mobile/v1/platform/policy-overview/')
        self.assertEqual(response.status_code, 200)
        data = response.json()['policy']
        self.assertEqual(data['company_total'], 1)
        self.assertEqual(set(data), {'company_total', 'owner_delegation_enabled',
            'sw_default_companies', 'en_default_companies', 'pending_memberships', 'maintenance_active'})

    def test_wrong_password_does_not_change_settings(self):
        data = self.payload()
        data.update(admin_password='wrong', login_lock_minutes='7')
        self.assertEqual(self.client.post(BASE + 'update/', data).status_code, 403)
        self.policy.refresh_from_db()
        self.assertNotEqual(self.policy.login_lock_minutes, 7)
        self.assertTrue(SecurityAuditEvent.objects.filter(action='SECURITY_SETTINGS_SAVE',
            result=SecurityAuditEvent.Result.DENIED).exists())

    def test_valid_update_is_audited_and_stale_revision_is_rejected(self):
        data = self.payload()
        data['login_lock_minutes'] = '7'
        self.assertEqual(self.client.post(BASE + 'update/', data).status_code, 200)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.login_lock_minutes, 7)
        self.assertTrue(SecurityAuditEvent.objects.filter(action='SECURITY_SETTINGS_SAVE',
            result=SecurityAuditEvent.Result.SUCCESS).exists())
        data['login_lock_minutes'] = '8'
        self.assertEqual(self.client.post(BASE + 'update/', data).status_code, 409)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.login_lock_minutes, 7)

    def test_invalid_and_missing_fields_do_not_change_settings(self):
        data = self.payload()
        data['idle_timeout_minutes'] = '100'
        data['absolute_session_timeout_minutes'] = '30'
        self.assertEqual(self.client.post(BASE + 'update/', data).status_code, 400)
        data = self.payload()
        del data['maintenance_enabled']
        self.assertEqual(self.client.post(BASE + 'update/', data).status_code, 400)

    def test_post_requires_csrf(self):
        data = self.payload()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin, backend=BACKEND)
        self.assertEqual(client.post(BASE + 'update/', data).status_code, 403)
