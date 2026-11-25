"""Native platform-admin access to the existing owner onboarding workflow."""
import hashlib
import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models import Q
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.companies.onboarding_forms import (
    OwnerInvitationCreateForm, RegistrationApprovalForm, RegistrationRejectionForm,
)
from apps.companies.onboarding_models import CompanyOwnerInvitation
from apps.companies.onboarding_services import (
    MAX_RESENDS,
    append_onboarding_event, approve_registration_request, create_owner_invitation,
    reject_registration_request, revoke_invitation, rotate_continuation_token,
    rotate_invitation, send_approval_email, send_invitation_email, send_rejection_email,
)
from apps.companies.onboarding_views import _admin_password_ok
from .platform_views import _deny, _form_error
from .views import _json

logger = logging.getLogger(__name__)


def _revision(invitation):
    value = f'{invitation.public_id}:{invitation.status}:{invitation.updated_at.isoformat()}'
    return hashlib.sha256(value.encode()).hexdigest()


def _summary(item):
    return {'id': str(item.public_id), 'email': item.email, 'status': item.status,
            'company_name': item.company_name, 'created_at': item.created_at.isoformat(),
            'expires_at': item.expires_at.isoformat()}


def _details(item):
    actions = []
    if item.status in ('SENT', 'OPENED', 'EXPIRED') and item.resend_count < MAX_RESENDS:
        actions.append('resend')
    if item.status == 'SUBMITTED':
        actions.extend(('approve', 'reject'))
    if item.status == 'APPROVED' and item.continuation_resend_count < MAX_RESENDS:
        actions.append('resend_setup')
    if item.status in ('SENT', 'OPENED', 'EXPIRED', 'SUBMITTED', 'APPROVED'):
        actions.append('revoke')
    return {**_summary(item), 'revision': _revision(item), 'company_code': item.company_code,
            'company_email': item.company_email, 'company_phone': item.company_phone,
            'company_address': item.company_address, 'company_timezone': item.company_timezone,
            'allowed_languages': item.allowed_languages, 'default_language': item.default_language,
            'decision_reason': item.decision_reason, 'resend_count': item.resend_count,
            'continuation_resend_count': item.continuation_resend_count,
            'continuation_expires_at': item.continuation_expires_at.isoformat()
                if item.continuation_expires_at else None,
            'allowed_actions': actions,
            'events': [{'action': event.action, 'result': event.result,
                        'actor': event.actor.get_username() if event.actor else '',
                        'created_at': event.created_at.isoformat()}
                       for event in item.events.select_related('actor').order_by('-created_at', '-pk')[:20]]}


def _https_required(request):
    return not request.is_secure() and not settings.DEBUG


def _deliver(request, invitation, action, raw_token=None):
    try:
        if action in ('create', 'resend'):
            send_invitation_email(invitation=invitation, raw_token=raw_token, request=request)
        elif action in ('approve', 'resend_setup'):
            send_approval_email(invitation=invitation, raw_token=raw_token, request=request)
        elif action == 'reject':
            if send_rejection_email(invitation=invitation, request=request) != 1:
                raise ValidationError('Email backend did not accept the message.')
        else:
            return 'not_required'
        return 'accepted'
    except Exception:
        try:
            append_onboarding_event(invitation=invitation, actor=request.user, request=request,
                action='ADMIN_EMAIL_SEND_FAILED', result='FAILURE',
                description='The onboarding action was saved but the email was not confirmed.')
        except Exception:
            logger.error('Could not record onboarding email failure.')
        return 'failed'


@never_cache
@require_GET
def invitations(request):
    denied = _deny(request)
    if denied is not None:
        return denied
    query = request.GET.get('q', '').strip()[:80]
    items = CompanyOwnerInvitation.objects.order_by('-created_at', '-pk')
    if query:
        items = items.filter(Q(email__icontains=query) | Q(company_name__icontains=query))
    page = Paginator(items, 20).get_page(request.GET.get('page', 1))
    return _json(request, {'ok': True, 'items': [_summary(item) for item in page],
                           'page': page.number, 'has_next': page.has_next()})


@never_cache
@require_GET
def detail(request, invitation_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    item = CompanyOwnerInvitation.objects.filter(public_id=invitation_id).first()
    if item is None:
        return _json(request, {'ok': False, 'code': 'not_found'}, status_code=404)
    return _json(request, {'ok': True, 'invitation': _details(item)})


@never_cache
@require_POST
def create(request):
    denied = _deny(request)
    if denied is not None:
        return denied
    form = OwnerInvitationCreateForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    if not _admin_password_ok(request, form.cleaned_data['admin_password'], action='OWNER_INVITATION_CREATE'):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'}, status_code=403)
    if _https_required(request):
        return _json(request, {'ok': False, 'code': 'secure_url_required',
                               'message': 'A secure HTTPS connection is required.'}, status_code=400)
    try:
        with transaction.atomic():
            item, token = create_owner_invitation(actor=request.user, email=form.cleaned_data['email'],
                                                  expires_hours=form.cleaned_data['expires_hours'])
        delivery = _deliver(request, item, 'create', token)
        return _json(request, {'ok': True, 'invitation': _details(item), 'delivery': delivery})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
                               'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'busy', 'message': 'Try again.'}, status_code=409)


@never_cache
@require_POST
def action(request, invitation_id, operation):
    denied = _deny(request)
    if denied is not None:
        return denied
    if operation not in ('resend', 'revoke', 'approve', 'reject', 'resend_setup'):
        return _json(request, {'ok': False, 'code': 'unknown_action'}, status_code=404)
    item = CompanyOwnerInvitation.objects.filter(public_id=invitation_id).first()
    if item is None:
        return _json(request, {'ok': False, 'code': 'not_found'}, status_code=404)
    if not _admin_password_ok(request, request.POST.get('admin_password'), invitation=item,
                              action='NATIVE_INVITATION_' + operation.upper()):
        return _json(request, {'ok': False, 'code': 'invalid_admin_password',
                               'message': 'Administrator password is incorrect.'}, status_code=403)
    if operation != 'revoke' and _https_required(request):
        return _json(request, {'ok': False, 'code': 'secure_url_required',
                               'message': 'A secure HTTPS connection is required.'}, status_code=400)
    if operation == 'approve':
        form = RegistrationApprovalForm(request.POST)
        if not form.is_valid():
            return _form_error(request, form)
    elif operation == 'reject':
        form = RegistrationRejectionForm(request.POST)
        if not form.is_valid():
            return _form_error(request, form)
    else:
        form = None
    try:
        token = None
        with transaction.atomic():
            item = CompanyOwnerInvitation.objects.select_for_update().get(pk=item.pk)
            if request.POST.get('revision') != _revision(item):
                return _json(request, {'ok': False, 'code': 'stale_invitation',
                                       'message': 'Invitation changed; reload before acting.'}, status_code=409)
            if operation == 'resend':
                item, token = rotate_invitation(actor=request.user, invitation=item)
            elif operation == 'revoke':
                item = revoke_invitation(actor=request.user, invitation=item)
            elif operation == 'approve':
                item, token = approve_registration_request(actor=request.user, invitation=item,
                    continuation_hours=form.cleaned_data['continuation_hours'])
            elif operation == 'reject':
                item = reject_registration_request(actor=request.user, invitation=item,
                    decision_reason=form.cleaned_data['decision_reason'])
            else:
                item, token = rotate_continuation_token(actor=request.user, invitation=item)
        delivery = _deliver(request, item, operation, token)
        return _json(request, {'ok': True, 'invitation': _details(item), 'delivery': delivery})
    except (ValidationError, PermissionDenied) as exc:
        return _json(request, {'ok': False, 'code': 'action_denied',
                               'message': '; '.join(getattr(exc, 'messages', [str(exc)]))}, status_code=400)
    except OperationalError:
        return _json(request, {'ok': False, 'code': 'busy', 'message': 'Reload and retry.'}, status_code=409)
