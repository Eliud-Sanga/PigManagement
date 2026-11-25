from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.companies.activation_services import issue_company_user_activation
from apps.companies.models import CompanyMembership, CompanyUserActivation
from apps.companies.services import create_company
from apps.users.models import GoogleIdentity

BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class MemberActivationApiTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username='member-admin39', email='member-admin39@example.test',
            password='fixture-password', is_platform_admin=True)
        self.owner = users.objects.create_user(username='member-owner39', email='member-owner39@example.test',
            password='fixture-password')
        self.pending = users(username='member-pending39', email='member-pending39@example.test', is_active=False)
        self.pending.set_unusable_password()
        self.pending.save()
        self.company = create_company(actor=self.admin, name='Activation Fixture', code='member-activation39',
            owner=self.owner, allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        role = self.company.roles.exclude(code='OWNER').first()
        self.member = CompanyMembership.objects.create(company=self.company, user=self.pending, role=role,
            status=CompanyMembership.Status.ACTIVE, requested_language='sw', approved_language='sw',
            approved_at=timezone.now(), created_by=self.admin, approved_by=self.admin)
        self.url = f'/api/mobile/v1/platform/actions/members/{self.member.public_id}/'
        self.client.force_login(self.admin, backend=BACKEND)

    def post(self, password='fixture-password'):
        return self.client.post(self.url + 'activation/resend/', {'admin_password': password}, secure=True)

    def test_resend_is_post_commit_and_token_is_not_returned(self):
        self.assertTrue(self.client.get(self.url).json()['fields']['activation_pending'])
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.post()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['email_delivery'], 'pending')
            self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        activation = CompanyUserActivation.objects.get(membership=self.member)
        self.assertIsNotNone(activation.sent_at)
        self.assertNotIn(activation.token_hash, response.content.decode())
        self.assertNotIn('activate/', response.content.decode())

    def test_wrong_password_and_unprivileged_or_csrf_are_denied(self):
        self.assertEqual(self.post('wrong').status_code, 403)
        self.assertFalse(CompanyUserActivation.objects.filter(membership=self.member).exists())
        self.client.force_login(self.owner, backend=BACKEND)
        self.assertEqual(self.post().status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin, backend=BACKEND)
        self.assertEqual(csrf.post(self.url + 'activation/resend/', {}, secure=True).status_code, 403)

    @override_settings(DEBUG=False)
    def test_production_requires_https(self):
        response = self.client.post(self.url + 'activation/resend/', {'admin_password': 'fixture-password'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CompanyUserActivation.objects.filter(membership=self.member).exists())

    def test_existing_google_identity_is_not_reactivated(self):
        GoogleIdentity.objects.create(user=self.pending, subject='v39-test-subject', email_at_link=self.pending.email)
        self.assertFalse(self.client.get(self.url).json()['fields']['activation_pending'])
        self.assertEqual(self.post().status_code, 409)
        self.assertFalse(CompanyUserActivation.objects.filter(membership=self.member).exists())

    def test_previously_used_activation_is_not_reissued(self):
        activation, _ = issue_company_user_activation(membership=self.member, actor=self.admin)
        activation.used_at = timezone.now()
        activation.save(update_fields=['used_at', 'updated_at'])
        self.assertFalse(self.client.get(self.url).json()['fields']['activation_pending'])
        self.assertEqual(self.post().status_code, 409)
        activation.refresh_from_db()
        self.assertIsNotNone(activation.used_at)

    def test_approval_of_active_google_only_identity_does_not_disable_it(self):
        GoogleIdentity.objects.create(user=self.pending, subject='v39-google-active', email_at_link=self.pending.email)
        self.pending.is_active = True
        self.pending.save(update_fields=['is_active', 'updated_at'])
        self.member.status = CompanyMembership.Status.PENDING_APPROVAL
        self.member.approved_at = None
        self.member.save(update_fields=['status', 'approved_at', 'updated_at'])
        response = self.client.post(f'/api/mobile/v1/platform/actions/requests/{self.member.public_id}/decision/',
            {'decision': 'approve', 'approved_language': 'sw', 'admin_password': 'fixture-password'}, secure=True)
        self.assertEqual(response.status_code, 200, response.content)
        self.pending.refresh_from_db()
        self.member.refresh_from_db()
        self.assertTrue(self.pending.is_active)
        self.assertEqual(self.member.status, CompanyMembership.Status.ACTIVE)
        self.assertFalse(CompanyUserActivation.objects.filter(user=self.pending).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_web_approval_preserves_active_google_only_identity(self):
        from django.urls import reverse
        GoogleIdentity.objects.create(user=self.pending, subject='v39-google-active-web', email_at_link=self.pending.email)
        self.pending.is_active = True
        self.pending.save(update_fields=['is_active', 'updated_at'])
        self.member.status = CompanyMembership.Status.PENDING_APPROVAL
        self.member.approved_at = None
        self.member.save(update_fields=['status', 'approved_at', 'updated_at'])
        response = self.client.post(reverse('users:approval_review', args=[self.member.public_id]),
            {'decision': 'approve', 'approved_language': 'sw', 'admin_password': 'fixture-password'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.pending.refresh_from_db()
        self.member.refresh_from_db()
        self.assertTrue(self.pending.is_active)
        self.assertEqual(self.member.status, CompanyMembership.Status.ACTIVE)
        self.assertFalse(CompanyUserActivation.objects.filter(user=self.pending).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_approval_cannot_reactivate_disabled_google_identity(self):
        GoogleIdentity.objects.create(user=self.pending, subject='v39-google-disabled', email_at_link=self.pending.email)
        self.member.status = CompanyMembership.Status.PENDING_APPROVAL
        self.member.approved_at = None
        self.member.save(update_fields=['status', 'approved_at', 'updated_at'])
        response = self.client.post(f'/api/mobile/v1/platform/actions/requests/{self.member.public_id}/decision/',
            {'decision': 'approve', 'approved_language': 'sw', 'admin_password': 'fixture-password'}, secure=True)
        self.assertEqual(response.status_code, 400)
        self.pending.refresh_from_db()
        self.member.refresh_from_db()
        self.assertFalse(self.pending.is_active)
        self.assertEqual(self.member.status, CompanyMembership.Status.PENDING_APPROVAL)

    def test_web_resend_rejects_google_identity(self):
        from django.urls import reverse
        GoogleIdentity.objects.create(user=self.pending, subject='v39-google-web', email_at_link=self.pending.email)
        response = self.client.post(reverse('users:company_member_activation_resend', args=[self.member.public_id]),
            {'admin_password': 'fixture-password'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CompanyUserActivation.objects.filter(user=self.pending).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_email_failure_preserves_committed_activation_without_exposing_exception(self):
        with patch('apps.mobile_api.platform_member_activation_views.send_company_user_activation_email',
                   side_effect=RuntimeError('fixture-private-error')):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['email_delivery'], 'pending')
        self.assertNotIn('fixture-private-error', response.content.decode())
        self.assertTrue(CompanyUserActivation.objects.filter(membership=self.member).exists())


class MemberActivationCommitTests(TransactionTestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username='member-commit-admin39', email='member-commit-admin39@example.test',
            password='fixture-password', is_platform_admin=True)
        owner = users.objects.create_user(username='member-commit-owner39', email='member-commit-owner39@example.test',
            password='fixture-password')
        self.pending = users(username='member-commit-user39', email='member-commit-user39@example.test', is_active=False)
        self.pending.set_unusable_password()
        self.pending.save()
        company = create_company(actor=self.admin, name='Commit Fixture', code='member-commit39',
            owner=owner, allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        role = company.roles.exclude(code='OWNER').first()
        self.member = CompanyMembership.objects.create(company=company, user=self.pending, role=role,
            status=CompanyMembership.Status.ACTIVE, requested_language='sw', approved_language='sw',
            approved_at=timezone.now(), created_by=self.admin, approved_by=self.admin)
        self.url = f'/api/mobile/v1/platform/actions/members/{self.member.public_id}/activation/resend/'
        self.client.force_login(self.admin, backend=BACKEND)

    def test_response_reports_backend_acceptance_after_commit(self):
        response = self.client.post(self.url, {'admin_password': 'fixture-password'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['email_delivery'], 'accepted')
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn('activate/', response.content.decode())

    def test_response_reports_unconfirmed_email_without_reverting_token(self):
        with patch('apps.mobile_api.platform_member_activation_views.send_company_user_activation_email',
                   side_effect=RuntimeError('private-fixture-error')):
            response = self.client.post(self.url, {'admin_password': 'fixture-password'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['email_delivery'], 'unconfirmed')
        self.assertNotIn('private-fixture-error', response.content.decode())
        self.assertTrue(CompanyUserActivation.objects.filter(membership=self.member).exists())
