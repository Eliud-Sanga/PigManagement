from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.companies.models import Company, CompanyMembership
from apps.companies.services import approve_company_membership, create_company
from apps.control_center.company_forms import CompanyCreateForm

BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class AccountReactivationGuardTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.admin = user.objects.create_user(username='guard-admin', email='admin@example.test',
            password='fixture-pass', is_platform_admin=True)
        self.owner = user.objects.create_user(username='guard-owner', email='owner@example.test', password='fixture-pass')
        self.company = create_company(actor=self.admin, name='Fixture', code='fixture', owner=self.owner,
            allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        self.role = self.company.roles.filter(is_active=True).exclude(code='OWNER').first()
        self.target = user.objects.create_user(username='guard-target', email='target@example.test',
            password='fixture-pass', is_active=False)
        self.client.force_login(self.admin, backend=BACKEND)

    def pending(self, target):
        return CompanyMembership.objects.create(company=self.company, user=target, role=self.role,
            status=CompanyMembership.Status.PENDING_APPROVAL, requested_language='sw', created_by=self.admin)

    def form_fields(self, email):
        return {'name': 'Second fixture', 'code': 'second-fixture', 'timezone': 'Africa/Dar_es_Salaam',
            'allowed_languages': ['sw'], 'default_language': 'sw', 'owner_email': email,
            'admin_password': 'fixture-pass'}

    def test_approval_does_not_enable_disabled_existing_identity(self):
        member = self.pending(self.target)
        with self.assertRaises(ValidationError):
            approve_company_membership(actor=self.admin, membership=member,
                approved_language='sw', step_up_verified=True)
        self.target.refresh_from_db()
        member.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertEqual(member.status, CompanyMembership.Status.PENDING_APPROVAL)

    def test_mobile_approval_rejects_disabled_identity_without_modifying_membership(self):
        member = self.pending(self.target)
        response = self.client.post(f'/api/mobile/v1/platform/actions/requests/{member.public_id}/decision/',
            {'decision': 'approve', 'approved_language': 'sw', 'admin_password': 'fixture-pass'})
        self.assertEqual(response.status_code, 400)
        self.target.refresh_from_db()
        member.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertEqual(member.status, CompanyMembership.Status.PENDING_APPROVAL)

    def test_active_identity_can_still_be_approved(self):
        self.target.is_active = True
        self.target.save(update_fields=['is_active'])
        member = self.pending(self.target)
        approve_company_membership(actor=self.admin, membership=member,
            approved_language='sw', step_up_verified=True)
        member.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(member.status, CompanyMembership.Status.ACTIVE)
        self.assertTrue(self.target.is_active)

    def test_new_identity_with_unusable_password_remains_pending_activation(self):
        user = get_user_model()(username='new-guard', email='new@example.test', is_active=False)
        user.set_unusable_password()
        user.save()
        member = self.pending(user)
        approve_company_membership(actor=self.admin, membership=member,
            approved_language='sw', step_up_verified=True)
        user.refresh_from_db()
        member.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(member.status, CompanyMembership.Status.ACTIVE)

    def test_company_form_rejects_disabled_existing_owner(self):
        form = CompanyCreateForm(self.form_fields(self.target.email))
        self.assertFalse(form.is_valid())
        self.assertIn('owner_email', form.errors)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertFalse(Company.objects.filter(code='second-fixture').exists())

    def test_mobile_company_create_cannot_enable_disabled_owner(self):
        data = self.form_fields(self.target.email)
        data['allowed_languages'] = 'sw'
        response = self.client.post('/api/mobile/v1/platform/actions/companies/create/', data)
        self.assertEqual(response.status_code, 400)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertFalse(Company.objects.filter(code='second-fixture').exists())

    def test_existing_active_owner_is_still_allowed_by_company_form(self):
        form = CompanyCreateForm(self.form_fields(self.owner.email))
        self.assertTrue(form.is_valid(), form.errors)
