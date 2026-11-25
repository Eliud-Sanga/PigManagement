"""Permission boundary and data minimization for the mobile platform overview."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from .platform_views import (
    overview, records, record_detail, tools, company_status, membership_decision,
    company_create_action, company_profile_action, company_language_action,
    company_member_options, company_member_request_action,
    company_members, company_member_detail, company_member_status, company_member_edit,
    member_permissions_detail, member_permissions_update,
    company_roles_data, role_editor_data, role_create_options, company_role_create, company_role_update,
    platform_user_profile_update, _identity_form_values, _identity_revision,
)


class PlatformOverviewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get(self, *, authenticated, active=True, platform=False):
        request = self.factory.get('/api/mobile/v1/platform/overview/')
        request.user = SimpleNamespace(
            is_authenticated=authenticated,
            is_active=active,
            is_platform_admin=platform,
        )
        return overview(request)

    def test_unauthenticated_is_rejected(self):
        response = self._get(authenticated=False)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('companies', response.content.decode())

    def test_company_user_and_inactive_admin_are_rejected(self):
        for active, platform in ((True, False), (False, True)):
            with self.subTest(active=active, platform=platform):
                response = self._get(authenticated=True, active=active, platform=platform)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('companies', response.content.decode())

    @patch('apps.mobile_api.platform_views.CompanyMembership.objects')
    @patch('apps.mobile_api.platform_views.Company.objects')
    def test_admin_receives_aggregate_counts_only(self, companies, memberships):
        companies.count.return_value = 8
        companies.filter.return_value.count.side_effect = (5, 2)
        memberships.filter.return_value.count.return_value = 3
        response = self._get(authenticated=True, platform=True)
        self.assertEqual(response.status_code, 200)
        import json
        self.assertEqual(json.loads(response.content), {
            'ok': True,
            'companies': {'total': 8, 'active': 5, 'suspended': 2},
            'memberships': {'pending': 3},
        })
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_all_lists_reject_other_identities_before_query(self):
        for kind in ('companies', 'requests', 'users', 'audit'):
            for authenticated, active, platform, expected in (
                (False, True, False, 401), (True, True, False, 403),
                (True, False, True, 403),
            ):
                with self.subTest(kind=kind, authenticated=authenticated,
                                  active=active, platform=platform):
                    req = self.factory.get(f'/api/mobile/v1/platform/lists/{kind}/')
                    req.user = SimpleNamespace(is_authenticated=authenticated,
                                               is_active=active, is_platform_admin=platform)
                    self.assertEqual(records(req, kind).status_code, expected)

    def test_unknown_list_rejected_for_admin(self):
        req = self.factory.get('/api/mobile/v1/platform/lists/other/')
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=True)
        self.assertEqual(records(req, 'other').status_code, 404)

    def test_details_reject_other_identities_before_lookup(self):
        from uuid import uuid4
        for kind in ('companies', 'requests', 'users', 'audit'):
            for authenticated, active, platform, expected in (
                (False, True, False, 401), (True, True, False, 403),
                (True, False, True, 403),
            ):
                req = self.factory.get('/api/mobile/v1/platform/lists/')
                req.user = SimpleNamespace(is_authenticated=authenticated,
                    is_active=active, is_platform_admin=platform)
                with self.subTest(kind=kind, expected=expected):
                    self.assertEqual(record_detail(req, kind, uuid4()).status_code, expected)

    def test_detail_rejects_unknown_kind_and_invalid_id(self):
        req = self.factory.get('/api/mobile/v1/platform/lists/')
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=True)
        self.assertEqual(record_detail(req, 'other', 'bad').status_code, 404)
        self.assertEqual(record_detail(req, 'companies', 'bad').status_code, 404)

    @patch('apps.mobile_api.platform_views.reverse', side_effect=lambda name: '/accounts/' + name.split(':')[1] + '/')
    def test_tools_are_allowlisted_and_admin_only(self, reverse_url):
        import json
        req = self.factory.get('/api/mobile/v1/platform/tools/')
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=False)
        self.assertEqual(tools(req).status_code, 403)
        reverse_url.assert_not_called()
        req.user.is_platform_admin = True
        data = json.loads(tools(req).content)
        self.assertEqual(len(data['tools']), 8)
        self.assertIn({'key': 'security', 'path': '/accounts/security_settings/'}, data['tools'])
        self.assertEqual(reverse_url.call_count, 8)

    def test_mutations_deny_non_admin_before_model_access(self):
        from uuid import uuid4
        for view in (company_status, membership_decision):
            req = self.factory.post('/api/mobile/v1/platform/actions/', {'action': 'suspend', 'decision': 'approve'})
            req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                       is_platform_admin=False)
            self.assertEqual(view(req, uuid4()).status_code, 403)
        req = self.factory.post('/api/mobile/v1/platform/actions/', {})
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=False)
        self.assertEqual(company_create_action(req).status_code, 403)
        self.assertEqual(company_profile_action(req, uuid4()).status_code, 403)
        self.assertEqual(company_language_action(req, uuid4()).status_code, 403)
        self.assertEqual(company_member_request_action(req, uuid4()).status_code, 403)
        get_req = self.factory.get('/api/mobile/v1/platform/actions/companies/')
        get_req.user = req.user
        self.assertEqual(company_member_options(get_req, uuid4()).status_code, 403)

    @patch('apps.mobile_api.platform_views.CompanyMemberRequestForm')
    @patch('apps.mobile_api.platform_views.get_object_or_404')
    def test_invalid_member_request_never_creates_identity(self, lookup, form):
        from uuid import uuid4
        req = self.factory.post('/api/mobile/v1/platform/actions/members/request/', {})
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=True)
        form.return_value.is_valid.return_value = False
        form.return_value.errors.as_text.return_value = 'Invalid form'
        with patch('apps.mobile_api.platform_views.request_company_user') as create:
            self.assertEqual(company_member_request_action(req, uuid4()).status_code, 400)
            create.assert_not_called()

    def test_member_management_requires_platform_admin_before_lookup(self):
        from uuid import uuid4
        identifier = uuid4()
        post = self.factory.post('/api/mobile/v1/platform/actions/members/',
                                 {'action': 'suspend', 'admin_password': 'secret'})
        post.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                    is_platform_admin=False)
        get = self.factory.get('/api/mobile/v1/platform/actions/members/')
        get.user = post.user
        self.assertEqual(company_members(get, identifier).status_code, 403)
        self.assertEqual(company_member_detail(get, identifier).status_code, 403)
        self.assertEqual(company_member_status(post, identifier).status_code, 403)
        self.assertEqual(company_member_edit(post, identifier).status_code, 403)

    def test_unknown_member_action_rejected_before_record_lookup(self):
        from uuid import uuid4
        post = self.factory.post('/api/mobile/v1/platform/actions/members/',
                                 {'action': 'delete', 'admin_password': 'secret'})
        post.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                    is_platform_admin=True)
        self.assertEqual(company_member_status(post, uuid4()).status_code, 400)

    def test_member_edit_rejects_non_admin_before_lookup(self):
        from uuid import uuid4
        request = self.factory.post('/api/mobile/v1/platform/actions/members/edit/', {})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=False)
        with patch('apps.mobile_api.platform_views.get_object_or_404') as lookup:
            self.assertEqual(company_member_edit(request, uuid4()).status_code, 403)
            lookup.assert_not_called()

    def test_member_permissions_require_platform_admin_before_lookup(self):
        from uuid import uuid4
        get = self.factory.get('/api/mobile/v1/platform/actions/members/permissions/')
        post = self.factory.post('/api/mobile/v1/platform/actions/members/permissions/update/', {})
        get.user = post.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=False)
        with patch('apps.mobile_api.platform_views.get_object_or_404') as lookup:
            self.assertEqual(member_permissions_detail(get, uuid4()).status_code, 403)
            self.assertEqual(member_permissions_update(post, uuid4()).status_code, 403)
            lookup.assert_not_called()

    def test_role_endpoints_require_platform_admin_before_lookup(self):
        from uuid import uuid4
        company = uuid4()
        get = self.factory.get('/api/mobile/v1/platform/actions/companies/roles/')
        post = self.factory.post('/api/mobile/v1/platform/actions/companies/roles/', {})
        get.user = post.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=False)
        with patch('apps.mobile_api.platform_views.get_object_or_404') as lookup:
            self.assertEqual(company_roles_data(get, company).status_code, 403)
            self.assertEqual(role_create_options(get, company).status_code, 403)
            self.assertEqual(role_editor_data(get, company, 1).status_code, 403)
            self.assertEqual(company_role_create(post, company).status_code, 403)
            self.assertEqual(company_role_update(post, company, 1).status_code, 403)
            lookup.assert_not_called()

    def test_user_profile_update_requires_platform_admin(self):
        from uuid import uuid4
        request = self.factory.post('/api/mobile/v1/platform/actions/users/profile/', {})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=False)
        with patch('apps.mobile_api.platform_views.get_object_or_404') as lookup:
            self.assertEqual(platform_user_profile_update(request, uuid4()).status_code, 403)
            lookup.assert_not_called()

    def test_user_profile_wrong_admin_password_cannot_save(self):
        from uuid import uuid4
        request = self.factory.post('/api/mobile/v1/platform/actions/users/profile/',
                                    {'admin_password': 'wrong'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        with patch('apps.mobile_api.platform_views.get_object_or_404'), \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=False), \
             patch('apps.mobile_api.platform_views.SecureUserIdentityForm') as form:
            self.assertEqual(platform_user_profile_update(request, uuid4()).status_code, 403)
            form.assert_not_called()

    def test_profile_endpoint_cannot_change_account_active_state(self):
        request = self.factory.post('/api/mobile/v1/platform/actions/users/profile/', {'is_active': 'on'})
        self.assertNotIn('is_active', _identity_form_values(request, SimpleNamespace(is_active=False)))
        request = self.factory.post('/api/mobile/v1/platform/actions/users/profile/', {})
        self.assertEqual(_identity_form_values(request, SimpleNamespace(is_active=True))['is_active'], 'on')

    def test_profile_revision_changes_when_user_details_change(self):
        user = SimpleNamespace(username='staff', email='staff@example.test', phone='',
            first_name='A', last_name='B', is_active=True, is_platform_admin=False)
        old = _identity_revision(user)
        user.phone = '123'
        self.assertNotEqual(old, _identity_revision(user))

    def test_stale_user_profile_cannot_overwrite_newer_details(self):
        from uuid import uuid4
        from unittest.mock import MagicMock
        request = self.factory.post('/api/mobile/v1/platform/actions/users/profile/',
                                    {'admin_password': 'test', 'revision': 'old'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        target = MagicMock(pk=7)
        with patch('apps.mobile_api.platform_views.get_user_model') as user_model, \
             patch('apps.mobile_api.platform_views.get_object_or_404', return_value=target), \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=True), \
             patch('apps.mobile_api.platform_views.transaction.atomic'), \
             patch('apps.mobile_api.platform_views._identity_revision', return_value='new'), \
             patch('apps.mobile_api.platform_views.SecureUserIdentityForm') as form:
            user_model.return_value.objects.select_for_update.return_value.get.return_value = target
            self.assertEqual(platform_user_profile_update(request, uuid4()).status_code, 409)
            form.assert_not_called()

    def test_role_detail_scopes_lookup_to_company(self):
        from uuid import uuid4
        from unittest.mock import MagicMock
        company = uuid4()
        request = self.factory.get('/api/mobile/v1/platform/actions/companies/roles/')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        role = MagicMock(name='role')
        role.name = 'Staff'
        role.code = 'STAFF'
        role.is_reserved = False
        with patch('apps.mobile_api.platform_views.get_object_or_404', return_value=role) as lookup, \
             patch('apps.mobile_api.platform_views.permission_choices', return_value=[]), \
             patch('apps.mobile_api.platform_views._role_permission_revision', return_value=([], 'abc')):
            self.assertEqual(role_editor_data(request, company, 7).status_code, 200)
            self.assertEqual(lookup.call_args.kwargs['company__public_id'], company)
            self.assertEqual(lookup.call_args.kwargs['pk'], 7)

    def test_invalid_role_creation_does_not_create_role(self):
        from uuid import uuid4
        request = self.factory.post('/api/mobile/v1/platform/actions/companies/roles/create/', {})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        with patch('apps.mobile_api.platform_views.get_object_or_404'), \
             patch('apps.mobile_api.platform_views.RoleCreateForm') as form, \
             patch('apps.mobile_api.platform_views.CompanyRole') as role:
            form.return_value.is_valid.return_value = False
            form.return_value.errors.as_text.return_value = 'Invalid form'
            self.assertEqual(company_role_create(request, uuid4()).status_code, 400)
            role.assert_not_called()

    def test_stale_role_permissions_are_not_overwritten(self):
        from uuid import uuid4
        from unittest.mock import MagicMock
        request = self.factory.post('/api/mobile/v1/platform/actions/companies/roles/update/',
                                    {'admin_password': 'test', 'revision': 'old'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        role = MagicMock(pk=7, company_id=8)
        with patch('apps.mobile_api.platform_views.get_object_or_404', return_value=role), \
             patch('apps.mobile_api.platform_views.RolePermissionsForm') as form, \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=True), \
             patch('apps.mobile_api.platform_views.transaction.atomic'), \
             patch('apps.mobile_api.platform_views.Company.objects'), \
             patch('apps.mobile_api.platform_views.CompanyRole.objects') as roles, \
             patch('apps.mobile_api.platform_views._role_permission_revision', return_value=([], 'new')), \
             patch('apps.mobile_api.platform_views.set_company_role_permissions') as update:
            form.return_value.is_valid.return_value = True
            form.return_value.cleaned_data = {'admin_password': 'test'}
            roles.select_for_update.return_value.select_related.return_value.get.return_value = role
            self.assertEqual(company_role_update(request, uuid4(), 7).status_code, 409)
            update.assert_not_called()

    def test_invalid_or_unverified_permission_update_never_calls_service(self):
        from uuid import uuid4
        request = self.factory.post('/api/mobile/v1/platform/actions/members/permissions/update/',
            {'granted_permissions': 'pigs.view_pig', 'denied_permissions': 'pigs.view_pig'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        with patch('apps.mobile_api.platform_views.get_object_or_404'), \
             patch('apps.mobile_api.platform_views.MembershipOverridesForm') as form, \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=False) as verify, \
             patch('apps.mobile_api.platform_views.set_membership_permission_overrides') as update:
            form.return_value.is_valid.return_value = False
            form.return_value.errors.as_text.return_value = 'Conflicting permissions'
            self.assertEqual(member_permissions_update(request, uuid4()).status_code, 400)
            verify.assert_not_called()
            form.return_value.is_valid.return_value = True
            form.return_value.cleaned_data = {'admin_password': 'wrong'}
            self.assertEqual(member_permissions_update(request, uuid4()).status_code, 403)
            update.assert_not_called()

    def test_permission_form_rejects_same_grant_and_deny(self):
        from unittest.mock import MagicMock
        from django.http import QueryDict
        from apps.control_center.company_forms import MembershipOverridesForm
        values = QueryDict('', mutable=True)
        values.setlist('granted_permissions', ['pigs.view_pig'])
        values.setlist('denied_permissions', ['pigs.view_pig'])
        values['admin_password'] = 'test'
        with patch('apps.control_center.company_forms.permission_choices',
                   return_value=[('pigs.view_pig', 'View pigs')]):
            form = MembershipOverridesForm(values, membership=MagicMock())
            self.assertFalse(form.is_valid())
            self.assertIn('__all__', form.errors)

    def test_stale_permission_snapshot_cannot_overwrite_newer_changes(self):
        from uuid import uuid4
        from unittest.mock import MagicMock
        request = self.factory.post('/api/mobile/v1/platform/actions/members/permissions/update/',
            {'admin_password': 'test', 'revision': 'old'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        member = MagicMock(pk=7, company_id=8, status='ACTIVE', approved_at=object())
        with patch('apps.mobile_api.platform_views.get_object_or_404', return_value=member), \
             patch('apps.mobile_api.platform_views.MembershipOverridesForm') as form, \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=True), \
             patch('apps.mobile_api.platform_views.transaction.atomic'), \
             patch('apps.mobile_api.platform_views.Company.objects'), \
             patch('apps.mobile_api.platform_views.CompanyMembership.objects') as members, \
             patch('apps.mobile_api.platform_views._member_permissions_snapshot', return_value=({}, 'new')), \
             patch('apps.mobile_api.platform_views.set_membership_permission_overrides') as update:
            form.return_value.is_valid.return_value = True
            form.return_value.cleaned_data = {'admin_password': 'test'}
            members.select_for_update.return_value.select_related.return_value.get.return_value = member
            self.assertEqual(member_permissions_update(request, uuid4()).status_code, 409)
            update.assert_not_called()

    def test_member_edit_rejects_invalid_form_and_wrong_password(self):
        from uuid import uuid4
        request = self.factory.post('/api/mobile/v1/platform/actions/members/edit/', {})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        with patch('apps.mobile_api.platform_views.get_object_or_404'), \
             patch('apps.mobile_api.platform_views.MembershipEditForm') as form, \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=False) as verify:
            form.return_value.is_valid.return_value = False
            form.return_value.errors.as_text.return_value = 'Invalid form'
            self.assertEqual(company_member_edit(request, uuid4()).status_code, 400)
            verify.assert_not_called()
            form.return_value.is_valid.return_value = True
            form.return_value.cleaned_data = {'admin_password': 'wrong'}
            self.assertEqual(company_member_edit(request, uuid4()).status_code, 403)

    def test_member_edit_preserves_last_active_owner(self):
        from uuid import uuid4
        from unittest.mock import MagicMock
        request = self.factory.post('/api/mobile/v1/platform/actions/members/edit/', {})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        member = MagicMock(pk=7, company_id=8, status='ACTIVE', approved_at=object(),
                           approved_language='sw')
        member.role.code = 'OWNER'
        role = SimpleNamespace(code='STAFF')
        with patch('apps.mobile_api.platform_views.get_object_or_404', return_value=member), \
             patch('apps.mobile_api.platform_views.MembershipEditForm') as form, \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=True), \
             patch('apps.mobile_api.platform_views.transaction.atomic'), \
             patch('apps.mobile_api.platform_views.Company.objects'), \
             patch('apps.mobile_api.platform_views.CompanyMembership.objects') as memberships, \
             patch('apps.mobile_api.platform_views._owner_is_last_active_owner', return_value=True):
            form.return_value.is_valid.return_value = True
            form.return_value.cleaned_data = {'admin_password': 'test', 'role': role, 'approved_language': 'en'}
            memberships.select_for_update.return_value.select_related.return_value.get.return_value = member
            response = company_member_edit(request, uuid4())
            self.assertEqual(response.status_code, 400)
            member.save.assert_not_called()
            self.assertEqual(member.role.code, 'OWNER')

    def test_member_edit_saves_valid_role_and_audits(self):
        from uuid import uuid4
        from unittest.mock import MagicMock
        request = self.factory.post('/api/mobile/v1/platform/actions/members/edit/', {})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        member = MagicMock(pk=7, company_id=8, status='ACTIVE', approved_at=object(),
                           approved_language='sw', public_id=uuid4())
        member.role.code = 'STAFF'
        member.user.preferred_language = 'sw'
        role = SimpleNamespace(code='MANAGER')
        with patch('apps.mobile_api.platform_views.get_object_or_404', return_value=member), \
             patch('apps.mobile_api.platform_views.MembershipEditForm') as form, \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=True), \
             patch('apps.mobile_api.platform_views.transaction.atomic'), \
             patch('apps.mobile_api.platform_views.Company.objects'), \
             patch('apps.mobile_api.platform_views.CompanyMembership.objects') as memberships, \
             patch('apps.mobile_api.platform_views.CompanyAuditEvent.objects') as audit, \
             patch('apps.mobile_api.platform_views._security_success') as security:
            form.return_value.is_valid.return_value = True
            form.return_value.cleaned_data = {'admin_password': 'test', 'role': role, 'approved_language': 'en'}
            memberships.select_for_update.return_value.select_related.return_value.get.return_value = member
            response = company_member_edit(request, member.public_id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(member.role.code, 'MANAGER')
            self.assertEqual(member.approved_language, 'en')
            member.save.assert_called_once()
            audit.create.assert_called_once()
            security.assert_called_once()

    def test_unapproved_member_cannot_use_lifecycle_action(self):
        from unittest.mock import MagicMock
        from uuid import uuid4
        request = self.factory.post('/api/mobile/v1/platform/actions/members/',
                                    {'action': 'activate', 'admin_password': 'test'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                       is_platform_admin=True)
        for status in ('PENDING_APPROVAL', 'REJECTED'):
            member = MagicMock(pk=7, company_id=8, status=status,
                               approved_at=None, approved_language='sw')
            with self.subTest(status=status), \
                patch('apps.mobile_api.platform_views.get_object_or_404', return_value=member), \
                patch('apps.mobile_api.platform_views._verify_admin_password', return_value=True), \
                patch('apps.mobile_api.platform_views.transaction.atomic'), \
                patch('apps.mobile_api.platform_views.Company.objects') as companies, \
                patch('apps.mobile_api.platform_views.CompanyMembership.objects') as memberships:
                companies.select_for_update.return_value.get.return_value.status = 'ACTIVE'
                memberships.select_for_update.return_value.select_related.return_value.get.return_value = member
                response = company_member_status(request, uuid4())
                self.assertEqual(response.status_code, 400)
                member.save.assert_not_called()
                self.assertEqual(member.status, status)

    def test_last_owner_cannot_be_suspended(self):
        from unittest.mock import MagicMock
        from uuid import uuid4
        request = self.factory.post('/api/mobile/v1/platform/actions/members/',
                                    {'action': 'suspend', 'admin_password': 'test'})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                       is_platform_admin=True)
        member = MagicMock(pk=7, company_id=8, status='ACTIVE',
                           approved_at=object(), approved_language='sw')
        with patch('apps.mobile_api.platform_views.get_object_or_404', return_value=member), \
             patch('apps.mobile_api.platform_views._verify_admin_password', return_value=True), \
             patch('apps.mobile_api.platform_views.transaction.atomic'), \
             patch('apps.mobile_api.platform_views.Company.objects') as companies, \
             patch('apps.mobile_api.platform_views.CompanyMembership.objects') as memberships, \
             patch('apps.mobile_api.platform_views._owner_is_last_active_owner', return_value=True):
            companies.select_for_update.return_value.get.return_value.status = 'ACTIVE'
            memberships.select_for_update.return_value.select_related.return_value.get.return_value = member
            response = company_member_status(request, uuid4())
            self.assertEqual(response.status_code, 400)
            member.save.assert_not_called()
            self.assertEqual(member.status, 'ACTIVE')

    @patch('apps.mobile_api.platform_views.CompanyCreateForm')
    def test_invalid_company_form_cannot_create_owner_or_company(self, form):
        req = self.factory.post('/api/mobile/v1/platform/actions/companies/create/',
                                {'allowed_languages': 'sw,en'})
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=True)
        form.return_value.is_valid.return_value = False
        form.return_value.errors.as_text.return_value = 'Invalid form'
        self.assertEqual(company_create_action(req).status_code, 400)

    def test_mutations_reject_unrecognized_action(self):
        from uuid import uuid4
        req = self.factory.post('/api/mobile/v1/platform/actions/', {'action': 'delete', 'decision': 'delete'})
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=True)
        self.assertEqual(company_status(req, uuid4()).status_code, 400)
        self.assertEqual(membership_decision(req, uuid4()).status_code, 400)

    @patch('apps.mobile_api.platform_views._verify_admin_password', return_value=False)
    @patch('apps.mobile_api.platform_views.get_object_or_404')
    def test_company_status_rejects_bad_step_up_without_write(self, lookup, verify):
        from uuid import uuid4
        req = self.factory.post('/api/mobile/v1/platform/actions/',
                                {'action': 'suspend', 'admin_password': 'wrong'})
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=True)
        self.assertEqual(company_status(req, uuid4()).status_code, 403)
        verify.assert_called_once()
        lookup.assert_called_once()

    @patch('apps.mobile_api.platform_views.transaction.atomic')
    @patch('apps.mobile_api.platform_views._security_success')
    @patch('apps.mobile_api.platform_views.set_company_status')
    @patch('apps.mobile_api.platform_views._verify_admin_password', return_value=True)
    @patch('apps.mobile_api.platform_views.get_object_or_404')
    @patch('apps.mobile_api.platform_views.Company.objects')
    def test_company_status_calls_guarded_service_and_audit(self, companies, lookup,
                                                             verify, change, audit, atomic):
        from uuid import uuid4
        import json
        original = SimpleNamespace(pk=7, status='ACTIVE')
        locked = SimpleNamespace(pk=7, status='ACTIVE')
        lookup.return_value = original
        companies.select_for_update.return_value.get.return_value = locked
        req = self.factory.post('/api/mobile/v1/platform/actions/',
                                {'action': 'suspend', 'admin_password': 'valid'})
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=True)
        response = company_status(req, uuid4())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['status'], 'ACTIVE')
        change.assert_called_once()
        self.assertTrue(change.call_args.kwargs['step_up_verified'])
        audit.assert_called_once()

    @patch('apps.mobile_api.platform_views.reverse', return_value='/accounts/control-center/companies/123/')
    @patch('apps.mobile_api.platform_views.get_object_or_404')
    def test_company_detail_has_only_allowed_fields(self, lookup, reverse_url):
        from datetime import datetime, timezone
        from uuid import uuid4
        import json
        lookup.return_value = SimpleNamespace(name='Njombe', code='njombe',
            status='ACTIVE', email='owner@example.test', phone='', address='Njombe',
            timezone='Africa/Dar_es_Salaam', allowed_languages=['sw', 'en'],
            default_language='sw',
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            memberships=SimpleNamespace(count=lambda: 3))
        req = self.factory.get('/api/mobile/v1/platform/lists/companies/')
        req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_platform_admin=True)
        response = record_detail(req, 'companies', uuid4())
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['fields']['members'], 3)
        self.assertEqual(data['fields']['allowed_languages'], ['sw', 'en'])
        self.assertNotIn('password', data['fields'])
        self.assertTrue(data['control_path'].startswith('/accounts/'))

    @patch('apps.mobile_api.platform_views.Company.objects')
    def test_company_list_is_bounded_and_minimal(self, companies):
        import json
        company = SimpleNamespace(public_id='123', name='Test Company',
                                  code='test-company', status='ACTIVE')
        qs = companies.order_by.return_value
        qs.filter.return_value = qs
        with patch('apps.mobile_api.platform_views.Paginator') as paginator:
            page = paginator.return_value.get_page.return_value
            page.__iter__.return_value = iter([company])
            page.number = 1
            page.has_next.return_value = False
            req = self.factory.get('/api/mobile/v1/platform/lists/companies/', {'q': 'x' * 120})
            req.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                       is_platform_admin=True)
            response = records(req, 'companies')
            self.assertEqual(response.status_code, 200)
            paginator.assert_called_once_with(qs, 20)
            self.assertIn('x' * 80, str(qs.filter.call_args))
            self.assertNotIn('x' * 81, str(qs.filter.call_args))
            self.assertEqual(json.loads(response.content)['items'], [{
                'id': '123', 'title': 'Test Company', 'subtitle': 'test-company',
                'status': 'ACTIVE',
            }])
