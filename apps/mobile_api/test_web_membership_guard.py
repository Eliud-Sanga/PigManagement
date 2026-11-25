from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import CompanyAuditEvent, CompanyMembership
from apps.companies.services import archive_company_membership, create_company

BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class MembershipGuardTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username='guard-v34-admin', email='guard-v34-admin@example.test',
            password='fixture-pass', is_platform_admin=True)
        self.owner = users.objects.create_user(username='guard-v34-owner', email='guard-v34-owner@example.test',
            password='fixture-pass')
        self.other = users.objects.create_user(username='guard-v34-other', email='guard-v34-other@example.test',
            password='fixture-pass')
        self.company = create_company(actor=self.admin, name='Guard V34', code='guard-v34', owner=self.owner,
            allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        self.owner_role = self.company.roles.get(code='OWNER')
        self.other_role = self.company.roles.exclude(code='OWNER').first()
        self.owner_membership = CompanyMembership.objects.get(company=self.company, user=self.owner)
        self.pending = CompanyMembership.objects.create(company=self.company, user=self.other, role=self.other_role,
            status=CompanyMembership.Status.PENDING_APPROVAL, requested_language='sw', approved_language='sw',
            created_by=self.admin)
        self.client.force_login(self.admin, backend=BACKEND)

    def web_status(self, membership, action):
        return self.client.post(reverse('users:membership_status', args=[membership.public_id, action]),
                                {'admin_password': 'fixture-pass'})

    def test_web_pending_request_cannot_activate(self):
        response = self.web_status(self.pending, 'activate')
        self.assertEqual(response.status_code, 302)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, CompanyMembership.Status.PENDING_APPROVAL)

    def test_web_pending_request_cannot_change_role(self):
        url = reverse('users:company_member_edit', args=[self.pending.public_id])
        response = self.client.post(url, {'admin_password': 'fixture-pass', 'role': self.owner_role.pk,
                                          'approved_language': 'sw'})
        self.assertContains(response, 'Pending or rejected requests')
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.role_id, self.other_role.pk)
        self.assertEqual(self.pending.status, CompanyMembership.Status.PENDING_APPROVAL)

    def test_web_last_owner_cannot_change_role_or_status(self):
        url = reverse('users:company_member_edit', args=[self.owner_membership.public_id])
        response = self.client.post(url, {'admin_password': 'fixture-pass', 'role': self.other_role.pk,
                                          'approved_language': 'sw'})
        self.assertContains(response, 'last active Owner')
        self.assertEqual(self.web_status(self.owner_membership, 'suspend').status_code, 302)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.status, CompanyMembership.Status.ACTIVE)
        self.assertEqual(self.owner_membership.role_id, self.owner_role.pk)

    def test_web_change_succeeds_if_another_owner_is_active(self):
        self.pending.role = self.owner_role
        self.pending.status = CompanyMembership.Status.ACTIVE
        self.pending.approved_at = timezone.now()
        self.pending.save(update_fields=['role', 'status', 'approved_at', 'updated_at'])
        self.assertEqual(self.web_status(self.owner_membership, 'suspend').status_code, 302)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.status, CompanyMembership.Status.SUSPENDED)
        self.assertTrue(CompanyAuditEvent.objects.filter(company=self.company, action='MEMBERSHIP_STATUS_CHANGED').exists())

    def test_archive_service_rejects_last_owner_for_platform_admin(self):
        with self.assertRaisesMessage(ValidationError, 'last active Owner'):
            archive_company_membership(actor=self.admin, membership=self.owner_membership)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.status, CompanyMembership.Status.ACTIVE)

    def test_archive_service_allows_removal_with_active_other_owner(self):
        self.pending.role = self.owner_role
        self.pending.status = CompanyMembership.Status.ACTIVE
        self.pending.approved_at = timezone.now()
        self.pending.save(update_fields=['role', 'status', 'approved_at', 'updated_at'])
        archive_company_membership(actor=self.admin, membership=self.owner_membership)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.status, CompanyMembership.Status.ARCHIVED)

    def test_web_status_and_audit_rollback_together_on_audit_failure(self):
        self.pending.role = self.owner_role
        self.pending.status = CompanyMembership.Status.ACTIVE
        self.pending.approved_at = timezone.now()
        self.pending.save(update_fields=['role', 'status', 'approved_at', 'updated_at'])
        with patch('apps.control_center.company_views._security_success', side_effect=RuntimeError('audit failure')):
            with self.assertRaisesMessage(RuntimeError, 'audit failure'):
                self.web_status(self.owner_membership, 'suspend')
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.status, CompanyMembership.Status.ACTIVE)
        self.assertFalse(CompanyAuditEvent.objects.filter(company=self.company, action='MEMBERSHIP_STATUS_CHANGED').exists())


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from apps.companies.owner_safety import is_last_active_owner


class ConcurrentMembershipTests(TransactionTestCase):
    def test_web_and_mobile_cannot_both_suspend_the_last_two_owners(self):
        if connection.vendor != 'sqlite':
            self.skipTest('This test exercises SQLite locking only.')
        users = get_user_model()
        admin = users.objects.create_user(username='race-v34-admin', email='race-v34-admin@example.test',
            password='fixture-pass', is_platform_admin=True)
        one = users.objects.create_user(username='race-v34-one', email='race-v34-one@example.test', password='fixture-pass')
        two = users.objects.create_user(username='race-v34-two', email='race-v34-two@example.test', password='fixture-pass')
        company = create_company(actor=admin, name='Race V34', code='race-v34', owner=one,
            allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        one_member = CompanyMembership.objects.get(company=company, user=one)
        two_member = CompanyMembership.objects.create(company=company, user=two, role=company.roles.get(code='OWNER'),
            status=CompanyMembership.Status.ACTIVE, requested_language='sw', approved_language='sw',
            approved_at=timezone.now(), created_by=admin, approved_by=admin)
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(admin, backend=BACKEND)
        barrier = Barrier(2, timeout=20)

        def simultaneous_check(member):
            result = is_last_active_owner(member)
            barrier.wait()
            return result

        def submit(index):
            try:
                if index == 0:
                    response = clients[index].post(reverse('users:membership_status',
                        args=[one_member.public_id, 'suspend']), {'admin_password': 'fixture-pass'})
                else:
                    response = clients[index].post(
                        f'/api/mobile/v1/platform/actions/members/{two_member.public_id}/status/',
                        {'action': 'suspend', 'admin_password': 'fixture-pass'})
                return response.status_code
            finally:
                close_old_connections()

        with patch('apps.control_center.company_views._owner_is_last_active_owner', side_effect=simultaneous_check), \
             patch('apps.mobile_api.platform_views._owner_is_last_active_owner', side_effect=simultaneous_check):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, range(2)))
        self.assertEqual(results[0], 302, results)
        self.assertIn(results[1], (200, 409), results)
        self.assertGreaterEqual(CompanyMembership.objects.filter(company=company,
            status=CompanyMembership.Status.ACTIVE, role__code='OWNER', user__is_active=True).count(), 1)
