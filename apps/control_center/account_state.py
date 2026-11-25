"""Shared validation for global account status actions.

Call lock_and_validate inside transaction.atomic(). Other account-management
paths must use the same locks before they can claim equivalent guarantees.
"""
import hashlib
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from apps.companies.constants import ROLE_OWNER
from apps.companies.models import Company, CompanyMembership
from apps.users.models import GoogleIdentity


def revision(user):
    fields = {'id': str(user.public_id), 'active': user.is_active,
              'username': user.username, 'email': user.email, 'phone': user.phone,
              'first_name': user.first_name, 'last_name': user.last_name,
              'platform_admin': user.is_platform_admin,
              'revocation_nonce': str(user.session_revocation_nonce or ''),
              'updated_at': user.updated_at.isoformat() if user.updated_at else ''}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def sign_in_method(user):
    return user.has_usable_password() or GoogleIdentity.objects.filter(user_id=user.pk).exists()


class StaleAccountError(ValidationError):
    pass


def lock_and_validate(actor_id, target_id, desired, supplied_revision):
    users = get_user_model().objects
    admin_ids = list(users.select_for_update().filter(is_active=True, is_platform_admin=True)
                     .order_by('pk').values_list('pk', flat=True))
    actor = users.select_for_update().get(pk=actor_id)
    if not actor.is_active or not actor.is_platform_admin:
        raise PermissionDenied('Platform administrator authority is required.')
    target = users.select_for_update().get(pk=target_id)
    if not supplied_revision or supplied_revision != revision(target):
        raise StaleAccountError('The account changed. Reload the page before trying again.')
    if target.is_active == desired:
        return target
    if not desired:
        if target.pk == actor.pk:
            raise ValidationError('You cannot deactivate your own account.')
        if target.is_platform_admin and len(admin_ids) <= 1:
            raise ValidationError('The last active platform administrator cannot be deactivated.')
        owned = list(CompanyMembership.objects.filter(user=target,
            status=CompanyMembership.Status.ACTIVE, role__code=ROLE_OWNER,
            role__is_active=True).values_list('company_id', flat=True).distinct())
        list(Company.objects.select_for_update().filter(pk__in=owned).order_by('pk').values_list('pk', flat=True))
        for company_id in owned:
            other = CompanyMembership.objects.filter(company_id=company_id,
                status=CompanyMembership.Status.ACTIVE, role__code=ROLE_OWNER,
                role__is_active=True, user__is_active=True).exclude(user=target).exists()
            if not other:
                raise ValidationError('This account is the only active owner of a business. Assign another owner first.')
    elif not sign_in_method(target):
        raise ValidationError('This account has no established sign-in method. Complete its secure activation flow.')
    return target
