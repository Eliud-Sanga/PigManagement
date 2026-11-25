"""Password-confirmed, audited account status changes for platform admins."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.control_center.account_state import (StaleAccountError, lock_and_validate,
                                                revision as _revision, sign_in_method as _sign_in_method)
from apps.control_center.company_views import _security_success, _verify_admin_password
from .platform_views import _deny
from .views import _json


@never_cache
@require_GET
def detail(request, user_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    user = get_user_model().objects.filter(public_id=user_id).first()
    if user is None:
        return _json(request, {'ok': False, 'code': 'not_found'}, status_code=404)
    return _json(request, {'ok': True, 'username': user.get_username(), 'active': user.is_active,
                           'is_self': user.pk == request.user.pk,
                           'has_sign_in_method': _sign_in_method(user), 'revision': _revision(user)})


@never_cache
@require_POST
def update(request, user_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    action = request.POST.get('action', '')
    if action not in ('activate', 'deactivate'):
        return _json(request, {'ok': False, 'code': 'invalid_action'}, status_code=400)
    target = get_user_model().objects.filter(public_id=user_id).first()
    if target is None:
        return _json(request, {'ok': False, 'code': 'not_found'}, status_code=404)
    if not _verify_admin_password(request, request.POST.get('admin_password'),
                                  action='PLATFORM_ACCOUNT_STATUS', subject_user=target):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'}, status_code=403)
    try:
        with transaction.atomic():
            desired = action == 'activate'
            target = lock_and_validate(request.user.pk, target.pk, desired, request.POST.get('revision'))
            if target.is_active == desired:
                return _json(request, {'ok': False, 'code': 'already_in_state',
                                       'message': 'Account is already in this state.'}, status_code=409)
            before = target.is_active
            target.is_active = desired
            target.save(update_fields=['is_active', 'updated_at'])
            _security_success(request, action='PLATFORM_ACCOUNT_STATUS', subject_user=target,
                description='Global account status updated.', metadata={'before_active': before, 'after_active': desired})
            return _json(request, {'ok': True, 'active': target.is_active})
    except StaleAccountError as exc:
        return _json(request, {'ok': False, 'code': 'stale_account',
                               'message': '; '.join(exc.messages)}, status_code=409)
    except PermissionDenied:
        return _json(request, {'ok': False, 'code': 'platform_admin_required'}, status_code=403)
    except ValidationError as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
                               'message': '; '.join(exc.messages)}, status_code=400)
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'busy',
                               'message': 'Account is busy. Reload before retrying.'}, status_code=409)
