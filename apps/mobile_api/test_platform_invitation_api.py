from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings

from apps.companies.onboarding_models import CompanyOwnerInvitation
from apps.companies.onboarding_services import create_owner_invitation
from apps.companies.services import create_company

BASE = '/api/mobile/v1/platform/invitations/'
BACKEND = 'apps.companies.backends.CompanyRoleBackend'


@override_settings(DEBUG=True)
class PlatformInvitationApiTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.admin = user.objects.create_user(username='invite-admin', email='admin@example.test',
            password='fixture-pass', is_platform_admin=True)
        self.owner = user.objects.create_user(username='invite-user', email='user@example.test', password='fixture-pass')
        create_company(actor=self.admin, name='Test', code='test-company', owner=self.owner,
                       allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        self.client.force_login(self.admin, backend=BACKEND)

    def create_invite(self):
        item, _ = create_owner_invitation(actor=self.admin, email='owner@example.test', expires_hours=72)
        return item

    def details(self, item):
        response = self.client.get(BASE + f'{item.public_id}/')
        self.assertEqual(response.status_code, 200)
        return response.json()['invitation']

    def test_non_admin_and_csrf_protection(self):
        self.client.force_login(self.owner, backend=BACKEND)
        self.assertEqual(self.client.get(BASE).status_code, 403)
        self.assertEqual(self.client.post(BASE + 'create/', {}).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin, backend=BACKEND)
        self.assertEqual(client.post(BASE + 'create/', {}).status_code, 403)

    def test_create_does_not_expose_token_and_email_failure_is_explicit(self):
        with patch('apps.mobile_api.platform_invitation_views.send_invitation_email', side_effect=OSError('mail failed')):
            response = self.client.post(BASE + 'create/', {'email': 'new@example.test',
                'expires_hours': '72', 'admin_password': 'fixture-pass'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['delivery'], 'failed')
        self.assertTrue(CompanyOwnerInvitation.objects.filter(email='new@example.test').exists())
        self.assertNotIn('token', str(data).lower())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_real_email_path_is_https_and_token_is_not_returned_in_api(self):
        with patch('apps.companies.onboarding_services.reverse', return_value='/invitation/test-link/'):
            response = self.client.post(BASE + 'create/', {'email': 'new@example.test',
                'expires_hours': '72', 'admin_password': 'fixture-pass'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['delivery'], 'accepted')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['new@example.test'])
        self.assertIn('https://testserver/invitation/test-link/', mail.outbox[0].body)
        self.assertNotIn('test-link', str(response.json()))

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False)
    def test_plain_http_cannot_create_email_invitation_in_production(self):
        response = self.client.post(BASE + 'create/', {'email': 'new@example.test',
            'expires_hours': '72', 'admin_password': 'fixture-pass'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CompanyOwnerInvitation.objects.filter(email='new@example.test').exists())

    def test_wrong_password_and_stale_revision_do_not_revoke(self):
        item = self.create_invite()
        rev = self.details(item)['revision']
        url = BASE + f'{item.public_id}/revoke/'
        self.assertEqual(self.client.post(url, {'revision': rev, 'admin_password': 'wrong'}).status_code, 403)
        self.assertEqual(self.client.post(url, {'revision': 'stale', 'admin_password': 'fixture-pass'}).status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.status, CompanyOwnerInvitation.Status.SENT)
        self.assertEqual(self.client.post(url, {'revision': rev, 'admin_password': 'fixture-pass'}).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, CompanyOwnerInvitation.Status.REVOKED)

    def test_resend_changes_token_and_does_not_disclose_it(self):
        item = self.create_invite()
        old = item.token_hash
        with patch('apps.mobile_api.platform_invitation_views.send_invitation_email'):
            response = self.client.post(BASE + f'{item.public_id}/resend/',
                {'revision': self.details(item)['revision'], 'admin_password': 'fixture-pass'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('token', str(response.json()).lower())
        item.refresh_from_db()
        self.assertNotEqual(item.token_hash, old)

    def test_approve_and_resend_setup(self):
        item = self.create_invite()
        item.status = CompanyOwnerInvitation.Status.SUBMITTED
        item.company_name = 'Example Business'
        item.company_code = 'example-business'
        item.save(update_fields=['status', 'company_name', 'company_code', 'updated_at'])
        with patch('apps.mobile_api.platform_invitation_views.send_approval_email'):
            response = self.client.post(BASE + f'{item.public_id}/approve/', {
                'revision': self.details(item)['revision'], 'admin_password': 'fixture-pass',
                'continuation_hours': '72'})
            self.assertEqual(response.status_code, 200)
            item.refresh_from_db()
            self.assertEqual(item.status, CompanyOwnerInvitation.Status.APPROVED)
            response = self.client.post(BASE + f'{item.public_id}/resend_setup/', {
                'revision': self.details(item)['revision'], 'admin_password': 'fixture-pass'})
            self.assertEqual(response.status_code, 200)

    def test_reject_requires_reason_and_is_audited(self):
        item = self.create_invite()
        item.status = CompanyOwnerInvitation.Status.SUBMITTED
        item.save(update_fields=['status', 'updated_at'])
        data = {'revision': self.details(item)['revision'], 'admin_password': 'fixture-pass',
                'decision_reason': ''}
        self.assertEqual(self.client.post(BASE + f'{item.public_id}/reject/', data).status_code, 400)
        data['decision_reason'] = 'Please provide complete details.'
        with patch('apps.mobile_api.platform_invitation_views.send_rejection_email', return_value=1):
            self.assertEqual(self.client.post(BASE + f'{item.public_id}/reject/', data).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, CompanyOwnerInvitation.Status.REJECTED)
        self.assertTrue(item.events.filter(action='REGISTRATION_REJECTED').exists())

    def test_details_exclude_token_hashes_and_list_is_paginated(self):
        item = self.create_invite()
        self.assertNotIn('token', str(self.details(item)).lower())
        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'][0]['email'], 'owner@example.test')

    def test_actions_reflect_status_and_resend_limit(self):
        item = self.create_invite()
        self.assertEqual(set(self.details(item)['allowed_actions']), {'resend', 'revoke'})
        item.resend_count = 5
        item.save(update_fields=['resend_count', 'updated_at'])
        self.assertEqual(self.details(item)['allowed_actions'], ['revoke'])
        item.status = CompanyOwnerInvitation.Status.REVOKED
        item.save(update_fields=['status', 'updated_at'])
        self.assertEqual(self.details(item)['allowed_actions'], [])
