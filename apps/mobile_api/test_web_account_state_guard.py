from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import CompanyMembership
from apps.companies.services import create_company
from apps.control_center.models import SecurityAuditEvent

BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class WebAccountStateGuardTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username='web-admin', email='web-admin@example.test',
            password='fixture-pass', is_platform_admin=True)
        self.owner = users.objects.create_user(username='web-owner', email='web-owner@example.test',
            password='fixture-pass')
        self.user = users.objects.create_user(username='web-member', email='web-member@example.test',
            password='fixture-pass')
        self.company = create_company(actor=self.admin, name='Fixture', code='fixture', owner=self.owner,
            allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        role = self.company.roles.exclude(code='OWNER').first()
        CompanyMembership.objects.create(company=self.company, user=self.user, role=role,
            status=CompanyMembership.Status.ACTIVE, requested_language='sw', approved_language='sw',
            approved_at=timezone.now(), created_by=self.admin, approved_by=self.admin)
        self.client.force_login(self.admin, backend=BACKEND)

    def url(self, user):
        return reverse('users:user_edit', args=[user.pk])

    def get_form(self, user):
        response = self.client.get(self.url(user))
        self.assertEqual(response.status_code, 200)
        return response.context['form']

    def payload(self, user, *, active=True, revision=None, password='fixture-pass'):
        if revision is None:
            revision = self.get_form(user).initial['account_revision']
        result = {'username': user.username, 'email': user.email, 'phone': user.phone or '',
            'first_name': user.first_name, 'last_name': user.last_name,
            'account_revision': revision, 'admin_password': password}
        if active:
            result['is_active'] = 'on'
        return result

    def test_edit_page_renders_revision_as_hidden_input(self):
        response = self.client.get(self.url(self.user))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="account_revision"')
        self.assertContains(response, 'type="hidden"')

    def test_sole_owner_cannot_be_disabled(self):
        data = self.payload(self.owner, active=False)
        response = self.client.post(self.url(self.owner), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'only active owner')
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_active_alternative_owner_allows_deactivation(self):
        CompanyMembership.objects.filter(user=self.user, company=self.company).update(
            role=self.company.roles.get(code='OWNER'))
        response = self.client.post(self.url(self.owner), self.payload(self.owner, active=False))
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)

    def test_self_deactivation_is_rejected(self):
        response = self.client.post(self.url(self.admin), self.payload(self.admin, active=False))
        self.assertContains(response, 'cannot deactivate your own')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_status_and_session_revocation(self):
        other = Client()
        other.force_login(self.user, backend=BACKEND)
        self.assertEqual(other.get('/api/mobile/v1/auth/session/').status_code, 200)
        self.assertEqual(self.client.post(self.url(self.user), self.payload(self.user, active=False)).status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(other.get('/api/mobile/v1/auth/session/').status_code, 401)
        self.assertEqual(self.client.post(self.url(self.user), self.payload(self.user, active=True)).status_code, 302)
        self.assertEqual(other.get('/api/mobile/v1/auth/session/').status_code, 401)
        self.assertEqual(SecurityAuditEvent.objects.filter(action='USER_IDENTITY_EDIT',
            result=SecurityAuditEvent.Result.SUCCESS, subject_user=self.user).count(), 2)

    def test_stale_form_does_not_reactivate_or_overwrite(self):
        data = self.payload(self.user, active=True)
        self.user.is_active = False
        self.user.save(update_fields=['is_active', 'updated_at'])
        response = self.client.post(self.url(self.user), data)
        self.assertContains(response, 'account changed')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_profile_change_without_updated_at_stales_web_form(self):
        data = self.payload(self.user)
        get_user_model().objects.filter(pk=self.user.pk).update(first_name='Changed elsewhere')
        response = self.client.post(self.url(self.user), data)
        self.assertContains(response, 'account changed')
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Changed elsewhere')

    def test_missing_revision_and_wrong_password_are_rejected(self):
        response = self.client.post(self.url(self.user), self.payload(self.user, active=False, revision=''))
        self.assertContains(response, 'account changed')
        response = self.client.post(self.url(self.user), self.payload(self.user, active=False, password='bad'))
        self.assertContains(response, 'password is incorrect')
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_activation_without_sign_in_method_is_rejected(self):
        self.user.is_active = False
        self.user.set_unusable_password()
        self.user.save(update_fields=['is_active', 'password', 'updated_at'])
        response = self.client.post(self.url(self.user), self.payload(self.user))
        self.assertContains(response, 'no established sign-in method')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_non_admin_and_csrf_cannot_modify(self):
        self.client.force_login(self.user, backend=BACKEND)
        self.assertEqual(self.client.post(self.url(self.owner), {}).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin, backend=BACKEND)
        self.assertEqual(client.post(self.url(self.owner), {}).status_code, 403)

    def test_profile_only_edit_is_preserved(self):
        data = self.payload(self.user)
        data['first_name'] = 'Edited'
        response = self.client.post(self.url(self.user), data)
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.first_name, 'Edited')
