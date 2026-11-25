from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Company, CompanyMembership, CompanyRole
from apps.companies.services import create_company
from apps.control_center.company_views import _owner_is_last_active_owner

BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class OwnerManagementGuardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username='guard-admin', email='guard-admin@example.test',
            password='fixture-pass', is_platform_admin=True, is_staff=True, is_superuser=True)
        self.owner = User.objects.create_user(username='guard-owner', email='guard-owner@example.test',
            password='fixture-pass')
        self.other = User.objects.create_user(username='guard-other', email='guard-other@example.test',
            password='fixture-pass')
        self.company = create_company(actor=self.admin, name='Guard', code='guard', owner=self.owner,
            allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        self.owner_role = self.company.roles.get(code='OWNER')
        self.owner_membership = CompanyMembership.objects.get(company=self.company, user=self.owner)
        self.other_membership = CompanyMembership.objects.create(company=self.company, user=self.other,
            role=self.owner_role, status=CompanyMembership.Status.ACTIVE, requested_language='sw',
            approved_language='sw', approved_at=timezone.now(), created_by=self.admin, approved_by=self.admin)
        self.client.force_login(self.admin, backend=BACKEND)

    def disable_other(self):
        self.other.is_active = False
        self.other.save(update_fields=['is_active', 'updated_at'])

    def test_inactive_alternative_is_not_counted(self):
        self.disable_other()
        self.assertTrue(_owner_is_last_active_owner(self.owner_membership))
        url = f'/api/mobile/v1/platform/actions/members/{self.owner_membership.public_id}/status/'
        response = self.client.post(url, {'action': 'suspend', 'admin_password': 'fixture-pass'})
        self.assertEqual(response.status_code, 400)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.status, CompanyMembership.Status.ACTIVE)

    def test_web_also_rejects_suspension_if_alternative_inactive(self):
        self.disable_other()
        url = reverse('users:membership_status', args=[self.owner_membership.public_id, 'suspend'])
        response = self.client.post(url, {'admin_password': 'fixture-pass'})
        self.assertEqual(response.status_code, 302)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.status, CompanyMembership.Status.ACTIVE)

    def test_eligible_second_owner_allows_suspension(self):
        self.assertFalse(_owner_is_last_active_owner(self.owner_membership))
        url = f'/api/mobile/v1/platform/actions/members/{self.owner_membership.public_id}/status/'
        response = self.client.post(url, {'action': 'suspend', 'admin_password': 'fixture-pass'})
        self.assertEqual(response.status_code, 200, response.content)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.status, CompanyMembership.Status.SUSPENDED)

    def test_cli_does_not_reactivate_inactive_user_or_alter_membership(self):
        self.disable_other()
        with self.assertRaisesMessage(CommandError, 'account is inactive'):
            call_command('provision_company_owner', company=self.company.code, actor=self.admin.username,
                username=self.other.username, email=self.other.email, stdout=StringIO())
        self.other.refresh_from_db()
        self.other_membership.refresh_from_db()
        self.assertFalse(self.other.is_active)
        self.assertEqual(self.other_membership.status, CompanyMembership.Status.ACTIVE)

    def test_cli_can_provision_existing_active_user(self):
        call_command('provision_company_owner', company=self.company.code, actor=self.admin.username,
            username=self.other.username, email=self.other.email, stdout=StringIO())
        self.other.refresh_from_db()
        self.other_membership.refresh_from_db()
        self.assertTrue(self.other.is_active)
        self.assertEqual(self.other_membership.role.code, 'OWNER')
        self.assertEqual(self.other_membership.status, CompanyMembership.Status.ACTIVE)

    def test_admin_governed_records_are_read_only_and_company_cannot_be_deleted(self):
        for model, obj in [('companymembership', self.owner_membership), ('companyrole', self.owner_role)]:
            change = reverse(f'admin:companies_{model}_change', args=[obj.pk])
            self.assertEqual(self.client.get(change).status_code, 200)
            self.assertEqual(self.client.post(change, {}).status_code, 403)
            add = reverse(f'admin:companies_{model}_add')
            self.assertEqual(self.client.post(add, {}).status_code, 403)
            delete = reverse(f'admin:companies_{model}_delete', args=[obj.pk])
            self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 403)
        delete = reverse('admin:companies_company_delete', args=[self.company.pk])
        self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 403)
        self.assertTrue(Company.objects.filter(pk=self.company.pk).exists())
        self.assertTrue(CompanyMembership.objects.filter(pk=self.owner_membership.pk).exists())
        self.assertTrue(CompanyRole.objects.filter(pk=self.owner_role.pk).exists())
