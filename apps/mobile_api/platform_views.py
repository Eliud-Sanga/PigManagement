"""Mobile platform control plane with explicit audited administrator actions."""
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.urls import reverse
from uuid import UUID
import hashlib
import json

from apps.companies.activation_identity import has_established_sign_in
from apps.companies.models import Company, CompanyMembership, CompanyRole
from apps.companies.models import CompanyAuditEvent
from apps.companies.constants import ROLE_OWNER
from apps.companies.services import (
    approve_company_membership, set_company_status, create_company,
    set_company_language_policy, request_company_user, set_membership_permission_overrides,
    set_company_role_permissions,
)
from apps.companies.activation_services import (
    issue_company_user_activation, send_company_user_activation_email,
)
from apps.control_center.company_forms import (
    MembershipApprovalForm, CompanyCreateForm, CompanyEditForm, CompanyLanguageForm,
    CompanyMemberRequestForm, MembershipEditForm, MembershipOverridesForm,
    RoleCreateForm, RolePermissionsForm,
    permission_choices, permission_code,
)
from apps.control_center.company_views import _verify_admin_password, _security_success
from apps.control_center.company_views import _owner_is_last_active_owner
from apps.control_center.forms import SecureUserIdentityForm
from apps.control_center.models import SecurityAuditEvent
from apps.control_center.services import append_security_event
from apps.users.models import AuditLog
from .member_activation_guard import eligible as _activation_eligible
from .views import _json


@never_cache
@require_GET
def overview(request):
    denied = _deny(request)
    if denied is not None:
        return denied

    companies = Company.objects.aggregate(
        total=Count('pk'),
        active=Count('pk', filter=Q(status=Company.Status.ACTIVE)),
        suspended=Count('pk', filter=Q(status=Company.Status.SUSPENDED)),
    )
    return _json(request, {
        'ok': True,
        'companies': companies,
        'memberships': {
            'pending': CompanyMembership.objects.filter(
                status=CompanyMembership.Status.PENDING_APPROVAL,
            ).count(),
        },
    })


def _deny(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return _json(request, {
            'ok': False, 'code': 'authentication_required',
            'message': 'Sign in before opening the platform overview.',
        }, status_code=401)
    if not user.is_active or not getattr(user, 'is_platform_admin', False):
        return _json(request, {
            'ok': False, 'code': 'platform_admin_required',
            'message': 'Platform Administrator authority is required.',
        }, status_code=403)

    return None


@never_cache
@require_GET
def records(request, kind):
    denied = _deny(request)
    if denied is not None:
        return denied
    if kind not in ('companies', 'requests', 'users', 'audit', 'security_audit'):
        return _json(request, {'ok': False, 'code': 'unknown_list'}, status_code=404)
    query = request.GET.get('q', '').strip()[:80]
    if kind == 'companies':
        qs = Company.objects.order_by('name', 'pk').only('public_id', 'name', 'code', 'status')
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
        def item(obj):
            return {'id': str(obj.public_id), 'title': obj.name,
                    'subtitle': obj.code, 'status': obj.status}
    elif kind == 'requests':
        qs = CompanyMembership.objects.filter(
            status=CompanyMembership.Status.PENDING_APPROVAL,
        ).select_related('company', 'user').order_by('-created_at', '-pk')
        if query:
            qs = qs.filter(Q(company__name__icontains=query) |
                           Q(user__username__icontains=query))
        def item(obj):
            return {'id': str(obj.public_id), 'title': obj.user.get_username(),
                    'subtitle': obj.company.name, 'status': obj.status}
    elif kind == 'users':
        qs = get_user_model().objects.order_by('username', 'pk').only('public_id', 'username', 'email', 'is_active')
        if query:
            qs = qs.filter(Q(username__icontains=query) | Q(email__icontains=query))
        def item(obj):
            return {'id': str(obj.public_id), 'title': obj.get_username(),
                    'subtitle': obj.email, 'status': 'ACTIVE' if obj.is_active else 'INACTIVE'}
    elif kind == 'security_audit':
        qs = SecurityAuditEvent.objects.order_by('-occurred_at', '-pk')
        if query:
            qs = qs.filter(Q(action__icontains=query) | Q(category__icontains=query))
        def item(obj):
            return {'id': str(obj.event_id), 'title': obj.action,
                    'subtitle': obj.category, 'status': obj.result,
                    'timestamp': obj.occurred_at.isoformat()}
    else:
        qs = AuditLog.objects.order_by('-timestamp', '-pk').only('event_id', 'action', 'model_name', 'result', 'timestamp')
        if query:
            qs = qs.filter(Q(action__icontains=query) | Q(model_name__icontains=query))
        def item(obj):
            return {'id': str(obj.event_id), 'title': obj.action,
                    'subtitle': obj.model_name, 'status': obj.result,
                    'timestamp': obj.timestamp.isoformat()}
    page = Paginator(qs, 20).get_page(request.GET.get('page', '1'))
    return _json(request, {'ok': True, 'items': [item(obj) for obj in page],
                           'page': page.number, 'has_next': page.has_next()})


@never_cache
@require_GET
def record_detail(request, kind, record_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    if kind not in ('companies', 'requests', 'users', 'audit', 'security_audit'):
        return _json(request, {'ok': False, 'code': 'unknown_record'}, status_code=404)
    try:
        identifier = UUID(str(record_id))
    except ValueError:
        return _json(request, {'ok': False, 'code': 'invalid_record'}, status_code=404)
    try:
        return _record_detail(request, kind, identifier)
    except Http404:
        return _json(request, {'ok': False, 'code': 'record_not_found'}, status_code=404)


def _record_detail(request, kind, identifier):
    if kind == 'companies':
        obj = get_object_or_404(Company.objects, public_id=identifier)
        fields = {
            'name': obj.name, 'code': obj.code, 'status': obj.status,
            'email': obj.email, 'phone': obj.phone, 'address': obj.address,
            'timezone': obj.timezone,
            'allowed_languages': list(obj.allowed_languages or []),
            'default_language': obj.default_language,
            'created_at': obj.created_at.isoformat(),
            'members': obj.memberships.count(),
        }
        url = reverse('users:company_detail', kwargs={'company_id': identifier})
    elif kind == 'requests':
        obj = get_object_or_404(CompanyMembership.objects.select_related(
            'company', 'user', 'role'), public_id=identifier,
            status=CompanyMembership.Status.PENDING_APPROVAL)
        fields = {'user': obj.user.get_username(), 'company': obj.company.name,
                  'role': obj.role.name, 'status': obj.status,
                  'created_at': obj.created_at.isoformat(),
                  'requested_language': obj.requested_language,
                  'allowed_languages': list(obj.company.allowed_languages or [])}
        url = reverse('users:approval_review', kwargs={'membership_id': identifier})
    elif kind == 'users':
        obj = get_object_or_404(get_user_model().objects, public_id=identifier)
        fields = {'username': obj.get_username(), 'email': obj.email,
                  'first_name': obj.first_name, 'last_name': obj.last_name, 'phone': obj.phone,
                  'active': obj.is_active, 'platform_admin': obj.is_platform_admin,
                  'memberships': obj.company_memberships.count(),
                  'identity_revision': _identity_revision(obj)}
        url = reverse('users:user_detail', kwargs={'user_id': obj.pk})
    elif kind == 'security_audit':
        obj = get_object_or_404(SecurityAuditEvent.objects.select_related('actor', 'company'), event_id=identifier)
        fields = {'action': obj.action, 'category': obj.category, 'result': obj.result,
                  'severity': obj.severity, 'description': obj.description,
                  'actor': obj.actor.get_username() if obj.actor else '',
                  'company': obj.company.name if obj.company else '',
                  'timestamp': obj.occurred_at.isoformat()}
        url = reverse('users:audit_detail', kwargs={'event_id': identifier})
    else:
        obj = get_object_or_404(AuditLog.objects.select_related('user'), event_id=identifier)
        fields = {'action': obj.action, 'result': obj.result,
                  'model': obj.model_name, 'description': obj.description,
                  'actor': obj.user.get_username() if obj.user else '',
                  'timestamp': obj.timestamp.isoformat()}
        # The Control Center has no detail page for legacy AuditLog events.
        url = reverse('users:audit_logs')
    return _json(request, {'ok': True, 'kind': kind, 'fields': fields,
                           'control_path': url})


@never_cache
@require_GET
def tools(request):
    denied = _deny(request)
    if denied is not None:
        return denied
    destinations = (
        ('companies', 'users:company_list'),
        ('company_create', 'users:company_create'),
        ('approvals', 'users:approvals'),
        ('invitations', 'users:owner_invitation_list'),
        ('users', 'users:user_list'),
        ('audit', 'users:audit_logs'),
        ('security', 'users:security_settings'),
        ('policies', 'users:policy_console'),
    )
    return _json(request, {'ok': True, 'tools': [
        {'key': key, 'path': reverse(route)} for key, route in destinations
    ]})


@never_cache
@require_POST
def company_status(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    action = request.POST.get('action', '').strip().lower()
    status_map = {'suspend': Company.Status.SUSPENDED,
                  'activate': Company.Status.ACTIVE,
                  'archive': Company.Status.ARCHIVED}
    if action not in status_map:
        return _json(request, {'ok': False, 'code': 'invalid_action'}, status_code=400)
    company = get_object_or_404(Company, public_id=company_id)
    if not _verify_admin_password(request, request.POST.get('admin_password'),
                                  action='COMPANY_STATUS_CHANGE', company=company):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'},
                     status_code=403)
    try:
        with transaction.atomic():
            company = Company.objects.select_for_update().get(pk=company.pk)
            before = company.status
            set_company_status(actor=request.user, company=company,
                               status=status_map[action], step_up_verified=True)
            _security_success(request, action='COMPANY_STATUS_CHANGE',
                              company=company, description='Company lifecycle status changed.',
                              metadata={'before': before, 'after': company.status})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
                               'message': '; '.join(getattr(exc, 'messages', [str(exc)]))},
                     status_code=400)
    return _json(request, {'ok': True, 'status': company.status})


@never_cache
@require_POST
def membership_decision(request, membership_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    decision = request.POST.get('decision', '').strip().lower()
    if decision not in ('approve', 'reject'):
        return _json(request, {'ok': False, 'code': 'invalid_decision'}, status_code=400)
    membership = get_object_or_404(
        CompanyMembership.objects.select_related('company', 'user', 'role'),
        public_id=membership_id,
    )
    if membership.status != CompanyMembership.Status.PENDING_APPROVAL:
        return _json(request, {'ok': False, 'code': 'not_pending'}, status_code=409)
    if request.POST.get('temporary_password1') or request.POST.get('temporary_password2'):
        return _json(request, {'ok': False, 'code': 'password_not_allowed'}, status_code=400)
    form = MembershipApprovalForm(request.POST, membership=membership)
    if decision == 'approve' and not form.is_valid():
        return _json(request, {'ok': False, 'code': 'invalid_form',
                               'message': form.errors.as_text()}, status_code=400)
    action = 'COMPANY_MEMBER_APPROVE' if decision == 'approve' else 'COMPANY_MEMBER_REJECT'
    if not _verify_admin_password(request, request.POST.get('admin_password'),
                                  action=action, company=membership.company,
                                  subject_user=membership.user):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'},
                     status_code=403)
    try:
        with transaction.atomic():
            membership = CompanyMembership.objects.select_for_update().select_related(
                'company', 'user', 'role').get(pk=membership.pk)
            if membership.status != CompanyMembership.Status.PENDING_APPROVAL:
                return _json(request, {'ok': False, 'code': 'not_pending'}, status_code=409)
            if decision == 'approve':
                approve_company_membership(
                    actor=request.user, membership=membership,
                    approved_language=form.cleaned_data['approved_language'],
                    step_up_verified=True,
                )
                membership.refresh_from_db()
                needs_activation = not has_established_sign_in(membership.user)
                if needs_activation:
                    activation, raw_token = issue_company_user_activation(
                        membership=membership, actor=request.user)
                    send_company_user_activation_email(
                        activation=activation, raw_token=raw_token,
                        request=request, actor=request.user)
                _security_success(request, action=action,
                    company=membership.company, subject_user=membership.user,
                    description='Pending company membership approved.',
                    metadata={'membership_id': membership.pk,
                              'activation_required': needs_activation})
            else:
                membership.status = CompanyMembership.Status.REJECTED
                membership.approved_language = ''
                membership.approved_by = None
                membership.approved_at = None
                membership.save(update_fields=['status', 'approved_language',
                    'approved_by', 'approved_at', 'updated_at'])
                CompanyAuditEvent.objects.create(
                    company=membership.company, actor=request.user,
                    action='COMPANY_USER_REJECTED',
                    description=f'{membership.user.username} membership rejected.',
                    metadata={'membership_id': membership.pk,
                              'role_code': membership.role.code})
                _security_success(request, action=action,
                    company=membership.company, subject_user=membership.user,
                    description='Pending membership rejected.',
                    metadata={'membership_id': membership.pk})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
                               'message': '; '.join(getattr(exc, 'messages', [str(exc)]))},
                     status_code=400)
    return _json(request, {'ok': True, 'status': membership.status})


def _form_error(request, form):
    return _json(request, {'ok': False, 'code': 'invalid_form',
                           'message': form.errors.as_text()}, status_code=400)


@never_cache
@require_POST
def company_create_action(request):
    denied = _deny(request)
    if denied is not None:
        return denied
    fields = request.POST.copy()
    fields.setlist('allowed_languages', [
        value.strip() for value in fields.get('allowed_languages', '').split(',')
        if value.strip()
    ])
    form = CompanyCreateForm(fields)
    if not form.is_valid():
        return _form_error(request, form)
    if not _verify_admin_password(request, form.cleaned_data['admin_password'],
                                  action='COMPANY_CREATE'):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            owner = form.existing_owner
            password = form.cleaned_data['temporary_password1']
            if owner is not None:
                owner = get_user_model().objects.select_for_update().get(pk=owner.pk)
                if not owner.is_active:
                    raise ValidationError('This account is inactive. Reactivate it before adding it as a company owner.')
            if owner is None:
                owner = get_user_model()(
                    username=form.cleaned_data['owner_username'].strip(),
                    email=form.cleaned_data['owner_email'],
                    first_name=form.cleaned_data['owner_first_name'].strip(),
                    last_name=form.cleaned_data['owner_last_name'].strip(),
                    preferred_language=form.cleaned_data['default_language'],
                    is_active=True, must_change_password=True,
                )
                owner.set_password(password)
                owner.full_clean()
                owner.save()
            elif password:
                owner.set_password(password)
                owner.must_change_password = True
                owner.password_changed_at = timezone.now()
                owner.save(update_fields=['password', 'must_change_password',
                                          'password_changed_at'])
            company = create_company(
                actor=request.user, name=form.cleaned_data['name'],
                code=form.cleaned_data['code'], owner=owner,
                allowed_languages=form.cleaned_data['allowed_languages'],
                default_language=form.cleaned_data['default_language'],
                step_up_verified=True, email=form.cleaned_data['email'],
                phone=form.cleaned_data['phone'], address=form.cleaned_data['address'],
                business_type=form.cleaned_data['business_type'],
                transport_cargo_enabled=form.cleaned_data['transport_cargo_enabled'],
                transport_passenger_enabled=form.cleaned_data['transport_passenger_enabled'],
            )
            company.timezone = form.cleaned_data['timezone']
            company.full_clean()
            company.save(update_fields=['timezone', 'updated_at'])
            _security_success(request, action='COMPANY_CREATE',
                company=company, subject_user=owner,
                description='Company created from mobile Platform Admin.',
                metadata={'company_code': company.code, 'owner_user_id': owner.pk,
                          'owner_reused': form.existing_owner is not None,
                          'business_type': company.business_type,
                          'transport_cargo_enabled': form.cleaned_data['transport_cargo_enabled'],
                          'transport_passenger_enabled': form.cleaned_data['transport_passenger_enabled'],
                          'allowed_languages': list(company.allowed_languages or [])})
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'busy',
            'message': 'Account is busy. Reload before retrying.'}, status_code=409)
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
                               'message': '; '.join(getattr(exc, 'messages', [str(exc)]))},
                     status_code=400)
    return _json(request, {'ok': True, 'id': str(company.public_id)})


@never_cache
@require_POST
def company_profile_action(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    company = get_object_or_404(Company, public_id=company_id)
    form = CompanyEditForm(request.POST, instance=company)
    if not form.is_valid():
        return _form_error(request, form)
    if not _verify_admin_password(request, form.cleaned_data['admin_password'],
                                  action='COMPANY_PROFILE_UPDATE', company=company):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            company = Company.objects.select_for_update().get(pk=company.pk)
            before = {name: getattr(company, name) for name in
                      ('name', 'email', 'phone', 'address', 'timezone')}
            locked_form = CompanyEditForm(request.POST, instance=company)
            if not locked_form.is_valid():
                return _form_error(request, locked_form)
            updated = locked_form.save(commit=False)
            updated.full_clean()
            updated.save()
            after = {name: getattr(updated, name) for name in before}
            CompanyAuditEvent.objects.create(
                company=updated, actor=request.user, action='COMPANY_PROFILE_UPDATED',
                description='Company profile updated.',
                metadata={'before': before, 'after': after})
            _security_success(request, action='COMPANY_PROFILE_UPDATE',
                company=updated,
                description='Company profile updated after password confirmation.',
                metadata={'before': before, 'after': after})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
                               'message': '; '.join(getattr(exc, 'messages', [str(exc)]))},
                     status_code=400)
    return _json(request, {'ok': True, 'id': str(company.public_id)})


@never_cache
@require_POST
def company_language_action(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    company = get_object_or_404(Company, public_id=company_id)
    fields = request.POST.copy()
    fields.setlist('allowed_languages', [
        value.strip() for value in fields.get('allowed_languages', '').split(',')
        if value.strip()
    ])
    form = CompanyLanguageForm(fields, company=company)
    if not form.is_valid():
        return _form_error(request, form)
    if not _verify_admin_password(request, form.cleaned_data['admin_password'],
                                  action='COMPANY_LANGUAGE_POLICY_UPDATE', company=company):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            company = Company.objects.select_for_update().get(pk=company.pk)
            set_company_language_policy(
                actor=request.user, company=company,
                allowed_languages=form.cleaned_data['allowed_languages'],
                default_language=form.cleaned_data['default_language'],
                step_up_verified=True)
            _security_success(request, action='COMPANY_LANGUAGE_POLICY_UPDATE',
                company=company, description='Company language policy updated.',
                metadata={'allowed_languages': form.cleaned_data['allowed_languages'],
                          'default_language': form.cleaned_data['default_language']})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
                               'message': '; '.join(getattr(exc, 'messages', [str(exc)]))},
                     status_code=400)
    return _json(request, {'ok': True, 'id': str(company.public_id)})


@never_cache
@require_GET
def company_member_options(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    company = get_object_or_404(Company, public_id=company_id)
    return _json(request, {'ok': True,
        'roles': list(company.roles.filter(is_active=True)
            .order_by('is_reserved', 'name').values('id', 'name', 'code')),
        'allowed_languages': list(company.allowed_languages or [])})


@never_cache
@require_POST
def company_member_request_action(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    company = get_object_or_404(Company, public_id=company_id)
    form = CompanyMemberRequestForm(request.POST, company=company)
    if not form.is_valid():
        return _form_error(request, form)
    if not _verify_admin_password(request, form.cleaned_data['admin_password'],
                                  action='COMPANY_MEMBER_REQUEST', company=company):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
            'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            membership = request_company_user(
                actor=request.user, company=company,
                role=form.cleaned_data['role'],
                username=form.cleaned_data['username'],
                email=form.cleaned_data['email'],
                requested_language=form.cleaned_data['requested_language'],
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data['last_name'])
            _security_success(request, action='COMPANY_MEMBER_REQUEST',
                company=company, subject_user=membership.user,
                description='Company membership request created.',
                metadata={'membership_id': membership.pk,
                    'role': membership.role.code,
                    'requested_language': membership.requested_language})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
            'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    return _json(request, {'ok': True, 'id': str(membership.public_id),
        'status': membership.status})


@never_cache
@require_GET
def company_members(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    company = get_object_or_404(Company, public_id=company_id)
    query = request.GET.get('q', '').strip()[:80]
    rows = CompanyMembership.objects.filter(company=company).select_related('user', 'role').order_by('-created_at', '-pk')
    if query:
        rows = rows.filter(Q(user__username__icontains=query) | Q(user__email__icontains=query))
    page = Paginator(rows, 20).get_page(request.GET.get('page', '1'))
    return _json(request, {'ok': True, 'page': page.number, 'has_next': page.has_next(),
        'items': [{'id': str(item.public_id), 'title': item.user.get_username(),
            'subtitle': item.role.name, 'status': item.status,
            'timestamp': item.created_at.isoformat()} for item in page]})


@never_cache
@require_GET
def company_member_detail(request, membership_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    membership = get_object_or_404(CompanyMembership.objects.select_related('company', 'user', 'role'), public_id=membership_id)
    return _json(request, {'ok': True, 'kind': 'memberships', 'fields': {
        'user': membership.user.get_username(), 'email': membership.user.email,
        'company': membership.company.name, 'role': membership.role.name,
        'role_id': membership.role_id, 'status': membership.status,
        'requested_language': membership.requested_language,
        'approved_language': membership.approved_language,
        'allowed_languages': list(membership.company.allowed_languages or []),
        'company_id': str(membership.company.public_id),
        'activation_pending': _activation_eligible(membership),
    }, 'control_path': reverse('users:company_member_detail', kwargs={'membership_id': membership_id})})


@never_cache
@require_POST
def company_member_status(request, membership_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    action = request.POST.get('action', '')
    actions = {'suspend': CompanyMembership.Status.SUSPENDED,
               'activate': CompanyMembership.Status.ACTIVE,
               'archive': CompanyMembership.Status.ARCHIVED}
    if action not in actions:
        return _json(request, {'ok': False, 'code': 'invalid_action'}, status_code=400)
    membership = get_object_or_404(CompanyMembership.objects.select_related('company', 'user', 'role'), public_id=membership_id)
    if not _verify_admin_password(request, request.POST.get('admin_password'),
                                  action='MEMBERSHIP_STATUS_CHANGE', company=membership.company,
                                  subject_user=membership.user):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password'}, status_code=403)
    try:
        with transaction.atomic():
            # Serialize mobile membership changes for a company on databases
            # that support row locks. Other management paths require the same
            # locking convention for a global last-owner guarantee.
            company = Company.objects.select_for_update().get(pk=membership.company_id)
            membership = CompanyMembership.objects.select_for_update().select_related('company', 'user', 'role').get(pk=membership.pk)
            target = actions[action]
            if membership.status not in (CompanyMembership.Status.ACTIVE,
                                         CompanyMembership.Status.SUSPENDED,
                                         CompanyMembership.Status.ARCHIVED):
                raise ValidationError('Pending or rejected requests must go through the approval workflow.')
            if not membership.approved_at or not membership.approved_language:
                raise ValidationError('A previously approved membership is required.')
            if target in (CompanyMembership.Status.SUSPENDED, CompanyMembership.Status.ARCHIVED) and _owner_is_last_active_owner(membership):
                raise ValidationError('The last active Owner cannot be suspended or archived.')
            if target == CompanyMembership.Status.ACTIVE and company.status != Company.Status.ACTIVE:
                raise ValidationError('An active company and approved language are required.')
            before = membership.status
            membership.status = target
            membership.full_clean()
            membership.save(update_fields=['status', 'updated_at'])
            CompanyAuditEvent.objects.create(company=membership.company, actor=request.user,
                action='MEMBERSHIP_STATUS_CHANGED', description=f'Membership status {before} -> {target}.',
                metadata={'membership_id': membership.pk, 'user_id': membership.user_id,
                          'before': before, 'after': target})
            _security_success(request, action='MEMBERSHIP_STATUS_CHANGE', company=membership.company,
                subject_user=membership.user, description='Company membership lifecycle changed.',
                metadata={'before': before, 'after': target})
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'busy',
            'message': 'Membership is busy. Reload before retrying.'}, status_code=409)
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
            'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    return _json(request, {'ok': True, 'status': membership.status})


@never_cache
@require_POST
def company_member_edit(request, membership_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    membership = get_object_or_404(
        CompanyMembership.objects.select_related('company', 'user', 'role'),
        public_id=membership_id)
    form = MembershipEditForm(request.POST, membership=membership)
    if not form.is_valid():
        return _form_error(request, form)
    if not _verify_admin_password(request, form.cleaned_data['admin_password'],
                                  action='COMPANY_MEMBER_EDIT', company=membership.company,
                                  subject_user=membership.user):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
            'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=membership.company_id)
            membership = CompanyMembership.objects.select_for_update().select_related(
                'company', 'user', 'role').get(pk=membership.pk)
            if membership.status not in (CompanyMembership.Status.ACTIVE,
                                         CompanyMembership.Status.SUSPENDED,
                                         CompanyMembership.Status.ARCHIVED) or not membership.approved_at:
                raise ValidationError('Pending or rejected requests must go through approval.')
            form = MembershipEditForm(request.POST, membership=membership)
            if not form.is_valid():
                return _form_error(request, form)
            new_role = form.cleaned_data['role']
            language = form.cleaned_data['approved_language']
            old_role = membership.role.code
            old_language = membership.approved_language
            if old_role == ROLE_OWNER and new_role.code != ROLE_OWNER and _owner_is_last_active_owner(membership):
                raise ValidationError('The last active Owner cannot be changed to another role.')
            membership.role = new_role
            membership.approved_language = language
            membership.full_clean()
            membership.save(update_fields=['role', 'approved_language', 'updated_at'])
            if membership.user.preferred_language != language:
                membership.user.preferred_language = language
                membership.user.save(update_fields=['preferred_language'])
            CompanyAuditEvent.objects.create(company=membership.company, actor=request.user,
                action='MEMBERSHIP_PROFILE_UPDATED', description='Membership role/language updated.',
                metadata={'membership_id': membership.pk,
                    'before': {'role': old_role, 'language': old_language},
                    'after': {'role': new_role.code, 'language': language}})
            _security_success(request, action='COMPANY_MEMBER_EDIT', company=membership.company,
                subject_user=membership.user, description='Membership role/language updated.',
                metadata={'membership_id': membership.pk, 'before_role': old_role,
                          'after_role': new_role.code})
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'busy',
            'message': 'Membership is busy. Reload before retrying.'}, status_code=409)
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
            'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    return _json(request, {'ok': True, 'id': str(membership.public_id)})


def _member_permissions_snapshot(member):
    def codes(relation):
        return sorted(permission_code(item) for item in relation.select_related('content_type').all())
    state = {'role_id': member.role_id, 'base': codes(member.role.permissions),
             'grants': codes(member.granted_permissions), 'denials': codes(member.denied_permissions)}
    raw = json.dumps(state, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return state, hashlib.sha256(raw).hexdigest()


@never_cache
@require_GET
def member_permissions_detail(request, membership_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    member = get_object_or_404(CompanyMembership.objects.select_related('company', 'user', 'role'), public_id=membership_id)
    choices = permission_choices()
    state, revision = _member_permissions_snapshot(member)
    return _json(request, {'ok': True,
        'permissions': [{'code': code, 'label': label} for code, label in choices],
        'base': state['base'], 'grants': state['grants'], 'denials': state['denials'],
        'revision': revision, 'status': member.status})


@never_cache
@require_POST
def member_permissions_update(request, membership_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    member = get_object_or_404(CompanyMembership.objects.select_related('company', 'user', 'role'), public_id=membership_id)
    values = request.POST.copy()
    for name in ('granted_permissions', 'denied_permissions'):
        values.setlist(name, [part.strip() for part in values.get(name, '').split(',') if part.strip()])
    form = MembershipOverridesForm(values, membership=member)
    if not form.is_valid():
        return _form_error(request, form)
    if not _verify_admin_password(request, form.cleaned_data['admin_password'],
            action='MEMBERSHIP_PERMISSION_OVERRIDE', company=member.company, subject_user=member.user):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
            'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=member.company_id)
            member = CompanyMembership.objects.select_for_update().select_related('company', 'user', 'role').get(pk=member.pk)
            if member.status not in (CompanyMembership.Status.ACTIVE, CompanyMembership.Status.SUSPENDED,
                                     CompanyMembership.Status.ARCHIVED) or not member.approved_at:
                raise ValidationError('Pending or rejected requests must go through approval.')
            _, revision = _member_permissions_snapshot(member)
            if request.POST.get('revision', '') != revision:
                return _json(request, {'ok': False, 'code': 'stale_permissions',
                    'message': 'Permissions have changed. Reload before saving.'}, status_code=409)
            form = MembershipOverridesForm(values, membership=member)
            if not form.is_valid():
                return _form_error(request, form)
            allowed = {code for code, _ in permission_choices()}
            existing = {permission_code(p) for p in member.granted_permissions.select_related('content_type').all()}
            existing.update(permission_code(p) for p in member.denied_permissions.select_related('content_type').all())
            if existing - allowed:
                raise ValidationError('Existing overrides contain unavailable permissions; review them in Control Center.')
            grants = form.cleaned_data['granted_permissions']
            denials = form.cleaned_data['denied_permissions']
            set_membership_permission_overrides(actor=request.user, membership=member,
                granted_codes=grants, denied_codes=denials, step_up_verified=True)
            _security_success(request, action='MEMBERSHIP_PERMISSION_OVERRIDE', company=member.company,
                subject_user=member.user, description='Per-member permission overrides updated.',
                metadata={'membership_id': member.pk, 'grants': sorted(grants), 'denials': sorted(denials)})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
            'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    return _json(request, {'ok': True, 'id': str(member.public_id)})


def _role_permission_revision(role):
    codes = sorted(permission_code(p) for p in role.permissions.select_related('content_type').all())
    state = {'id': role.pk, 'codes': codes, 'active': role.is_active}
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return codes, digest


@never_cache
@require_GET
def company_roles_data(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    company = get_object_or_404(Company, public_id=company_id)
    roles = company.roles.order_by('is_reserved', 'name')
    return _json(request, {'ok': True, 'roles': [{'id': role.pk, 'name': role.name,
        'code': role.code, 'reserved': role.is_reserved, 'active': role.is_active}
        for role in roles]})


@never_cache
@require_GET
def role_editor_data(request, company_id, role_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    role = get_object_or_404(CompanyRole.objects.select_related('company'),
                             pk=role_id, company__public_id=company_id)
    choices = permission_choices()
    codes, revision = _role_permission_revision(role)
    return _json(request, {'ok': True, 'name': role.name, 'code': role.code,
        'permissions': [{'code': code, 'label': label} for code, label in choices],
        'selected': codes, 'revision': revision, 'reserved': role.is_reserved})


@never_cache
@require_GET
def role_create_options(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    get_object_or_404(Company, public_id=company_id)
    return _json(request, {'ok': True, 'permissions': [
        {'code': code, 'label': label} for code, label in permission_choices()]})


def _role_form_values(request):
    values = request.POST.copy()
    values.setlist('permissions', [p.strip() for p in values.get('permissions', '').split(',') if p.strip()])
    return values


@never_cache
@require_POST
def company_role_create(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    company = get_object_or_404(Company, public_id=company_id)
    values = _role_form_values(request)
    form = RoleCreateForm(values, company=company)
    if not form.is_valid():
        return _form_error(request, form)
    if not _verify_admin_password(request, form.cleaned_data['admin_password'],
            action='COMPANY_ROLE_CREATE', company=company):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
            'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            company = Company.objects.select_for_update().get(pk=company.pk)
            form = RoleCreateForm(values, company=company)
            if not form.is_valid():
                return _form_error(request, form)
            role = CompanyRole(company=company, code=form.cleaned_data['code'],
                name=form.cleaned_data['name'], description=form.cleaned_data['description'],
                is_reserved=False, is_active=True)
            role.full_clean()
            role.save()
            set_company_role_permissions(actor=request.user, role=role,
                permission_codes=form.cleaned_data['permissions'], step_up_verified=True)
            CompanyAuditEvent.objects.create(company=company, actor=request.user,
                action='COMPANY_ROLE_CREATED', description=f'Custom role {role.code} created.',
                metadata={'role_id': role.pk})
            _security_success(request, action='COMPANY_ROLE_CREATE', company=company,
                description='Custom company role created.', metadata={'role_id': role.pk, 'role_code': role.code})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
            'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    return _json(request, {'ok': True, 'id': role.pk})


@never_cache
@require_POST
def company_role_update(request, company_id, role_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    role = get_object_or_404(CompanyRole.objects.select_related('company'),
                             pk=role_id, company__public_id=company_id)
    values = _role_form_values(request)
    form = RolePermissionsForm(values, role=role)
    if not form.is_valid():
        return _form_error(request, form)
    if not _verify_admin_password(request, form.cleaned_data['admin_password'],
            action='COMPANY_ROLE_PERMISSIONS', company=role.company):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
            'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=role.company_id)
            role = CompanyRole.objects.select_for_update().select_related('company').get(pk=role.pk, company_id=role.company_id)
            current, revision = _role_permission_revision(role)
            if request.POST.get('revision', '') != revision:
                return _json(request, {'ok': False, 'code': 'stale_permissions',
                    'message': 'Role permissions have changed. Reload before saving.'}, status_code=409)
            allowed = {code for code, _ in permission_choices()}
            if set(current) - allowed:
                raise ValidationError('Existing role permissions are unavailable here; review them in Control Center.')
            form = RolePermissionsForm(values, role=role)
            if not form.is_valid():
                return _form_error(request, form)
            set_company_role_permissions(actor=request.user, role=role,
                permission_codes=form.cleaned_data['permissions'], step_up_verified=True)
            _security_success(request, action='COMPANY_ROLE_PERMISSIONS', company=role.company,
                description='Company role permission template updated.',
                metadata={'role_id': role.pk, 'role_code': role.code,
                          'permissions': sorted(form.cleaned_data['permissions'])})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
            'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    return _json(request, {'ok': True, 'id': role.pk})


def _identity_fields(target):
    return {'username': target.username, 'email': target.email, 'phone': target.phone,
            'first_name': target.first_name, 'last_name': target.last_name,
            'is_active': target.is_active, 'is_platform_admin': target.is_platform_admin}


def _identity_revision(target):
    return hashlib.sha256(json.dumps(_identity_fields(target), sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _identity_form_values(request, target):
    values = request.POST.copy()
    # Account status is deliberately not editable through this profile endpoint.
    if target.is_active:
        values['is_active'] = 'on'
    else:
        values.pop('is_active', None)
    return values


@never_cache
@require_POST
def platform_user_profile_update(request, user_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    target = get_object_or_404(get_user_model().objects, public_id=user_id)
    if not _verify_admin_password(request, request.POST.get('admin_password'),
            action='USER_IDENTITY_EDIT', subject_user=target):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
            'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            target = get_user_model().objects.select_for_update().get(pk=target.pk)
            if request.POST.get('revision', '') != _identity_revision(target):
                return _json(request, {'ok': False, 'code': 'stale_identity',
                    'message': 'User information has changed. Reload before saving.'}, status_code=409)
            before = _identity_fields(target)
            form = SecureUserIdentityForm(_identity_form_values(request, target), instance=target)
            if not form.is_valid():
                return _form_error(request, form)
            updated = form.save()
            append_security_event(request=request, category=SecurityAuditEvent.Category.IDENTITY,
                action='USER_IDENTITY_EDIT', result=SecurityAuditEvent.Result.SUCCESS,
                severity=SecurityAuditEvent.Severity.HIGH, actor=request.user,
                subject_user=updated, reason_code='IDENTITY_UPDATED',
                description='User profile updated after administrator password confirmation.',
                metadata={'before': before, 'after': _identity_fields(updated)})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
            'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    return _json(request, {'ok': True, 'id': str(updated.public_id)})
