"""Platform security settings, limited to authenticated platform admins."""
import hashlib
import json

from django.db import OperationalError, transaction
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.companies.models import Company, CompanyMembership
from apps.control_center.company_views import _security_success, _verify_admin_password
from apps.control_center.forms import SecuritySettingsForm
from apps.control_center.models import SecuritySettings
from .platform_views import _deny, _form_error
from .views import _json

FIELDS = (
    'max_failed_login_attempts', 'failed_attempt_window_minutes', 'login_lock_minutes',
    'device_cookie_days', 'idle_timeout_minutes', 'absolute_session_timeout_minutes',
    'maintenance_enabled', 'maintenance_starts_at', 'maintenance_ends_at',
    'maintenance_message',
)


def _values(policy):
    result = {}
    for name in FIELDS:
        value = getattr(policy, name)
        result[name] = value.isoformat() if hasattr(value, 'isoformat') else value
    return result


def _revision(policy):
    # updated_at catches intervening changes even if the values were later restored.
    payload = {'values': _values(policy), 'updated_at': policy.updated_at.isoformat()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@never_cache
@require_GET
def detail(request):
    denied = _deny(request)
    if denied is not None:
        return denied
    policy = SecuritySettings.objects.filter(singleton_key=1).first()
    if policy is None:
        return _json(request, {'ok': False, 'code': 'settings_unavailable',
                               'message': 'Security settings are unavailable.'}, status_code=503)
    return _json(request, {'ok': True, 'settings': _values(policy),
                           'revision': _revision(policy)})


@never_cache
@require_GET
def policy_overview(request):
    denied = _deny(request)
    if denied is not None:
        return denied
    policy = SecuritySettings.objects.filter(singleton_key=1).first()
    if policy is None:
        return _json(request, {'ok': False, 'code': 'settings_unavailable'}, status_code=503)
    return _json(request, {'ok': True, 'policy': {
        'company_total': Company.objects.count(),
        'owner_delegation_enabled': Company.objects.filter(owner_user_management_enabled=True).count(),
        'sw_default_companies': Company.objects.filter(default_language='sw').count(),
        'en_default_companies': Company.objects.filter(default_language='en').count(),
        'pending_memberships': CompanyMembership.objects.filter(
            status=CompanyMembership.Status.PENDING_APPROVAL).count(),
        'maintenance_active': policy.maintenance_is_active(),
    }})


@never_cache
@require_POST
def update(request):
    denied = _deny(request)
    if denied is not None:
        return denied
    if not _verify_admin_password(request, request.POST.get('admin_password'),
                                  action='SECURITY_SETTINGS_SAVE'):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'}, status_code=403)
    if any(field not in request.POST for field in FIELDS):
        return _json(request, {'ok': False, 'code': 'incomplete_settings',
                               'message': 'Reload the complete settings before saving.'}, status_code=400)
    revision = request.POST.get('revision', '')
    try:
        with transaction.atomic():
            policy = SecuritySettings.objects.select_for_update().filter(singleton_key=1).first()
            if policy is None:
                return _json(request, {'ok': False, 'code': 'settings_unavailable'}, status_code=503)
            if not revision or revision != _revision(policy):
                return _json(request, {'ok': False, 'code': 'stale_settings',
                                       'message': 'Settings changed; reload before saving.'}, status_code=409)
            before = _values(policy)
            form = SecuritySettingsForm(request.POST, instance=policy)
            if not form.is_valid():
                return _form_error(request, form)
            updated = form.save(commit=False)
            updated.updated_by = request.user
            updated.save()
            _security_success(request, action='SECURITY_SETTINGS_SAVE',
                              description='Platform security settings updated from app.',
                              metadata={'before': before, 'after': _values(updated)})
            return _json(request, {'ok': True, 'settings': _values(updated),
                                   'revision': _revision(updated)})
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'settings_busy',
                               'message': 'Settings are busy; reload and retry.'}, status_code=409)
