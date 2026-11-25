"""Audited, password-confirmed member activation email resend."""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.companies.activation_services import issue_company_user_activation, send_company_user_activation_email
from apps.companies.models import Company, CompanyMembership
from apps.control_center.company_views import _security_success, _verify_admin_password
from .member_activation_guard import eligible
from .platform_views import _deny
from .views import _json

logger = logging.getLogger(__name__)


@never_cache
@require_POST
def resend(request, membership_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    if not request.is_secure() and not settings.DEBUG:
        return _json(request, {'ok': False, 'code': 'https_required',
                               'message': 'A secure connection is required.'}, status_code=400)
    member = get_object_or_404(CompanyMembership.objects.select_related('company', 'user'), public_id=membership_id)
    if not _verify_admin_password(request, request.POST.get('admin_password'),
            action='COMPANY_USER_ACTIVATION_RESEND', company=member.company, subject_user=member.user):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'}, status_code=403)
    delivery = {'status': 'pending'}
    try:
        with transaction.atomic():
            actor = get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if not actor.is_active or not actor.is_platform_admin:
                return _json(request, {'ok': False, 'code': 'platform_admin_required'}, status_code=403)
            Company.objects.select_for_update().get(pk=member.company_id)
            member = CompanyMembership.objects.select_for_update().select_related('company', 'user').get(pk=member.pk)
            user = get_user_model().objects.select_for_update().get(pk=member.user_id)
            member.user = user
            if not eligible(member):
                return _json(request, {'ok': False, 'code': 'not_eligible',
                    'message': 'Activation is not available for this membership.'}, status_code=409)
            activation, token = issue_company_user_activation(membership=member, actor=actor)
            _security_success(request, action='COMPANY_USER_ACTIVATION_RESEND', company=member.company,
                subject_user=user, description='Company-user activation link requested.',
                metadata={'membership_id': member.pk, 'activation_id': str(activation.public_id)})

            def deliver_after_commit():
                try:
                    send_company_user_activation_email(activation=activation, raw_token=token,
                                                       request=request, actor=actor)
                    delivery['status'] = 'accepted'
                except Exception:
                    delivery['status'] = 'unconfirmed'
                    logger.warning('Company activation email was not confirmed.')

            transaction.on_commit(deliver_after_commit)
        return _json(request, {'ok': True, 'email_delivery': delivery['status']})
    except (ValidationError, PermissionDenied):
        return _json(request, {'ok': False, 'code': 'not_eligible',
                               'message': 'Activation is not available for this membership.'}, status_code=409)
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'busy',
                               'message': 'Membership is busy. Reload before retrying.'}, status_code=409)
