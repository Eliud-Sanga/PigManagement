"""Platform admin control of owner team-management policy."""
import hashlib

from django.contrib.auth import get_user_model
from django.db import OperationalError, transaction
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.companies.models import Company, CompanyAuditEvent
from apps.control_center.company_views import _security_success, _verify_admin_password
from .platform_views import _deny
from .views import _json


def _revision(company):
    state = f'{company.public_id}|{company.owner_user_management_enabled}|{company.updated_at}'
    return hashlib.sha256(state.encode()).hexdigest()


@never_cache
@require_GET
def detail(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    company = get_object_or_404(Company, public_id=company_id)
    return _json(request, {'ok': True, 'company': company.name,
        'enabled': company.owner_user_management_enabled, 'revision': _revision(company)})


@never_cache
@require_POST
def update(request, company_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    enabled = request.POST.get('enabled')
    if enabled not in ('0', '1'):
        return _json(request, {'ok': False, 'code': 'invalid_enabled'}, status_code=400)
    company = get_object_or_404(Company, public_id=company_id)
    if not _verify_admin_password(request, request.POST.get('admin_password'),
                                  action='OWNER_USER_MANAGEMENT_POLICY', company=company):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
            'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            actor = get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if not actor.is_active or not actor.is_platform_admin:
                return _json(request, {'ok': False, 'code': 'platform_admin_required'}, status_code=403)
            company = Company.objects.select_for_update().get(pk=company.pk)
            if not request.POST.get('revision') or request.POST.get('revision') != _revision(company):
                return _json(request, {'ok': False, 'code': 'stale_policy',
                    'message': 'Company information changed. Reload before saving.'}, status_code=409)
            before = company.owner_user_management_enabled
            after = enabled == '1'
            if before == after:
                return _json(request, {'ok': True, 'enabled': before})
            company.owner_user_management_enabled = after
            company.save(update_fields=['owner_user_management_enabled', 'updated_at'])
            CompanyAuditEvent.objects.create(company=company, actor=actor,
                action='OWNER_USER_MANAGEMENT_POLICY_CHANGED',
                description='Platform Admin changed Owner company-user management policy.',
                metadata={'before': before, 'after': after})
            _security_success(request, action='OWNER_USER_MANAGEMENT_POLICY', company=company,
                description='Owner company-user management policy changed.',
                metadata={'before': before, 'after': after})
            return _json(request, {'ok': True, 'enabled': after})
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'busy',
            'message': 'Company is busy. Reload before trying again.'}, status_code=409)
