from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from apps.companies.models import CompanyAuditEvent
from apps.companies.services import create_company
from apps.control_center.models import SecurityAuditEvent

BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class OwnerPolicyTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username='policy-admin36', email='policy-admin36@example.test',
            password='test-password', is_platform_admin=True)
        self.owner = get_user_model().objects.create_user(username='policy-owner36', email='policy-owner36@example.test',
            password='test-password')
        self.company = create_company(actor=self.admin, name='Policy company', code='policy-company36', owner=self.owner,
            allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        self.url = f'/api/mobile/v1/platform/actions/companies/{self.company.public_id}/owner-policy/'
        self.client.force_login(self.admin, backend=BACKEND)

    def state(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def update(self, enabled, *, password='test-password', revision=None):
        if revision is None:
            revision = self.state()['revision']
        return self.client.post(self.url + 'update/', {'enabled': enabled,
            'admin_password': password, 'revision': revision})

    def test_change_records_both_audits(self):
        self.assertFalse(self.state()['enabled'])
        self.assertEqual(self.update('1').status_code, 200)
        self.company.refresh_from_db()
        self.assertTrue(self.company.owner_user_management_enabled)
        self.assertTrue(CompanyAuditEvent.objects.filter(company=self.company,
            action='OWNER_USER_MANAGEMENT_POLICY_CHANGED').exists())
        self.assertTrue(SecurityAuditEvent.objects.filter(action='OWNER_USER_MANAGEMENT_POLICY',
            result=SecurityAuditEvent.Result.SUCCESS).exists())
        self.assertEqual(self.update('0').status_code, 200)
        self.company.refresh_from_db()
        self.assertFalse(self.company.owner_user_management_enabled)

    def test_auth_csrf_wrong_password_and_invalid_input(self):
        state = self.state()
        self.assertEqual(self.update('1', password='wrong', revision=state['revision']).status_code, 403)
        self.assertEqual(self.update('x', revision=state['revision']).status_code, 400)
        self.company.refresh_from_db()
        self.assertFalse(self.company.owner_user_management_enabled)
        self.client.force_login(self.owner, backend=BACKEND)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url + 'update/', {}).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin, backend=BACKEND)
        self.assertEqual(csrf.post(self.url + 'update/', {}).status_code, 403)

    def test_stale_revision_cannot_overwrite_policy(self):
        old = self.state()['revision']
        self.company.owner_user_management_enabled = True
        self.company.save(update_fields=['owner_user_management_enabled', 'updated_at'])
        self.assertEqual(self.update('0', revision=old).status_code, 409)
        self.company.refresh_from_db()
        self.assertTrue(self.company.owner_user_management_enabled)

    def test_audit_failure_rolls_back_policy(self):
        revision = self.state()['revision']
        with patch('apps.mobile_api.platform_owner_policy_views._security_success', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.update('1', revision=revision)
        self.company.refresh_from_db()
        self.assertFalse(self.company.owner_user_management_enabled)
        self.assertFalse(CompanyAuditEvent.objects.filter(company=self.company,
            action='OWNER_USER_MANAGEMENT_POLICY_CHANGED').exists())
