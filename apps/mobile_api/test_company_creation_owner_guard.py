from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.companies.models import Company
from apps.companies.services import create_company
from apps.control_center.company_forms import CompanyCreateForm

BACKEND = 'apps.companies.backends.CompanyRoleBackend'


class CompanyCreationOwnerGuardTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username='v35-admin', email='v35-admin@example.test',
            password='fixture-pass', is_platform_admin=True)
        self.existing = get_user_model().objects.create_user(username='v35-existing', email='v35-existing@example.test',
            password='fixture-pass')
        self.client.force_login(self.admin, backend=BACKEND)

    def payload(self, code, *, new=False, mobile=False):
        values = {'name': 'V35 Business', 'code': code, 'email': '', 'phone': '', 'address': '',
            'timezone': 'Africa/Dar_es_Salaam', 'allowed_languages': 'sw' if mobile else ['sw'],
            'default_language': 'sw', 'admin_password': 'fixture-pass',
            'owner_email': f'{code}@example.test' if new else self.existing.email,
            'owner_username': f'{code}-user' if new else '', 'owner_first_name': 'Owner',
            'owner_last_name': 'Example', 'temporary_password1': 'Robust-Temporary-Password-2026!' if new else '',
            'temporary_password2': 'Robust-Temporary-Password-2026!' if new else ''}
        return values

    def test_stale_owner_is_reloaded_and_disabled_account_remains_disabled(self):
        stale = get_user_model().objects.get(pk=self.existing.pk)
        self.existing.is_active = False
        self.existing.save(update_fields=['is_active', 'updated_at'])
        with self.assertRaisesMessage(ValidationError, 'account is inactive'):
            create_company(actor=self.admin, name='Denied', code='v35-denied', owner=stale,
                allowed_languages=['sw'], default_language='sw', step_up_verified=True)
        self.existing.refresh_from_db()
        self.assertFalse(self.existing.is_active)
        self.assertFalse(Company.objects.filter(code='v35-denied').exists())

    def test_web_can_create_business_with_new_owner(self):
        result = self.client.post(reverse('users:company_create'), self.payload('v35web', new=True))
        self.assertEqual(result.status_code, 302, getattr(result, 'context', None))
        owner = get_user_model().objects.get(email='v35web@example.test')
        self.assertTrue(owner.is_active)
        self.assertTrue(owner.must_change_password)
        self.assertTrue(Company.objects.filter(code='v35web', memberships__user=owner).exists())

    def test_mobile_can_create_business_with_new_owner(self):
        result = self.client.post('/api/mobile/v1/platform/actions/companies/create/',
            self.payload('v35mobile', new=True, mobile=True))
        self.assertEqual(result.status_code, 200, result.content)
        owner = get_user_model().objects.get(email='v35mobile@example.test')
        self.assertTrue(owner.is_active)
        self.assertTrue(owner.must_change_password)
        self.assertTrue(Company.objects.filter(code='v35mobile', memberships__user=owner).exists())

    def test_web_and_mobile_recheck_owner_after_form_validation(self):
        original = CompanyCreateForm.is_valid
        for mobile in (False, True):
            with self.subTest(mobile=mobile):
                self.existing.is_active = True
                self.existing.save(update_fields=['is_active', 'updated_at'])
                old_password = self.existing.password
                def disable_after_validation(form):
                    valid = original(form)
                    if valid:
                        user = get_user_model().objects.get(pk=self.existing.pk)
                        user.is_active = False
                        user.save(update_fields=['is_active', 'updated_at'])
                    return valid
                code = 'v35raceapi' if mobile else 'v35raceweb'
                url = '/api/mobile/v1/platform/actions/companies/create/' if mobile else reverse('users:company_create')
                with patch.object(CompanyCreateForm, 'is_valid', disable_after_validation):
                    response = self.client.post(url, self.payload(code, mobile=mobile))
                self.assertEqual(response.status_code, 400 if mobile else 200)
                self.existing.refresh_from_db()
                self.assertFalse(self.existing.is_active)
                self.assertEqual(self.existing.password, old_password)
                self.assertFalse(Company.objects.filter(code=code).exists())
