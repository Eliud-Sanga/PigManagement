"""Safe self-service business registration requests for Home Management clients.

The mobile client never creates a Company or grants an Owner role directly.
It submits an audited request into the existing Company Owner onboarding
workflow.  A Platform Administrator must still review the request, and the
prospective Owner must complete the email-bound continuation flow before any
business access is provisioned.
"""

import hashlib
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from apps.companies.onboarding_forms import CompanyRegistrationRequestForm
from apps.companies.onboarding_models import (
    CompanyInvitationThrottle,
    CompanyOwnerInvitation,
)
from apps.companies.onboarding_services import (
    append_onboarding_event,
    generate_token,
    submit_registration,
)

from .views import _json


User = get_user_model()

AVAILABLE_BUSINESS_TYPES = {
    "pig": "Pig Business",
    "transport": "Transport",
}
REQUEST_LIMIT = 5
REQUEST_WINDOW = timedelta(hours=24)
REQUEST_LOCK = timedelta(hours=24)


def _error(request, code, message, status, *, fields=None):
    payload = {
        "ok": False,
        "code": code,
        "message": message,
    }
    if fields:
        payload["fields"] = fields
    return _json(request, payload, status_code=status)


def _client_ip(request):
    # A browser controls X-Forwarded-For unless a trusted edge strips it.
    # REMOTE_ADDR is the only trustworthy input without explicit proxy config.
    return request.META.get("REMOTE_ADDR") or "unknown"


def _request_key(request, email):
    value = f"bizmanager-registration:{_client_ip(request)}:{email}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@transaction.atomic
def _consume_request_slot(request, email):
    now = timezone.now()
    row, _ = (
        CompanyInvitationThrottle.objects.select_for_update().get_or_create(
            key_hash=_request_key(request, email)
        )
    )

    if row.locked_until and row.locked_until > now:
        return False

    if (
        row.window_started_at is None
        or row.window_started_at < now - REQUEST_WINDOW
    ):
        row.failed_attempts = 0
        row.window_started_at = now
        row.locked_until = None

    if row.failed_attempts >= REQUEST_LIMIT:
        row.locked_until = now + REQUEST_LOCK
        row.last_attempt_at = now
        row.save(
            update_fields=[
                "locked_until",
                "last_attempt_at",
            ]
        )
        return False

    row.failed_attempts += 1
    row.last_attempt_at = now
    row.save(
        update_fields=[
            "failed_attempts",
            "window_started_at",
            "last_attempt_at",
            "locked_until",
        ]
    )
    return True


def _form_errors(form):
    return {
        name: [str(message) for message in messages]
        for name, messages in form.errors.items()
    }


@never_cache
@require_POST
@csrf_protect
def registration_request(request):
    business_type = (request.POST.get("business_type") or "").strip().lower()
    if business_type not in AVAILABLE_BUSINESS_TYPES:
        return _error(
            request,
            "business_type_unavailable",
            "That business type is not available for registration yet.",
            409,
        )

    owner_email = (request.POST.get("owner_email") or "").strip().lower()
    if not owner_email:
        return _error(
            request,
            "owner_email_required",
            "Owner email is required.",
            400,
        )

    owner_full_name = (request.POST.get("owner_full_name") or "").strip()
    owner_phone = (request.POST.get("owner_phone") or "").strip()
    if (len(owner_full_name) > 120 or any(ord(char) < 32 for char in owner_full_name) or "<" in owner_full_name or ">" in owner_full_name):
        return _error(request, "owner_name_invalid", "Check the owner name.", 400)
    if owner_phone and not re.fullmatch(r"[0-9+() -]{5,40}", owner_phone):
        return _error(request, "owner_phone_invalid", "Check the owner phone number.", 400)

    if not _consume_request_slot(request, owner_email):
        return _error(
            request,
            "registration_temporarily_locked",
            "Too many registration requests. Try again later.",
            429,
        )

    current_user = getattr(request, "user", None)
    authenticated = bool(current_user and current_user.is_authenticated)

    if authenticated:
        if getattr(current_user, "is_platform_admin", False):
            return _error(
                request,
                "platform_admin_control_plane",
                "Platform Administrator must use the Control Center.",
                403,
            )
        if (current_user.email or "").strip().lower() != owner_email:
            return _error(
                request,
                "owner_email_mismatch",
                "Use the email belonging to your signed-in account.",
                403,
            )
    elif User.objects.filter(email__iexact=owner_email).exists():
        return _error(
            request,
            "account_exists_sign_in",
            "An account already uses this email. Sign in before adding a business.",
            409,
        )

    platform_admins = User.objects.filter(
        is_active=True,
        is_platform_admin=True,
    ).order_by("pk")
    if platform_admins.count() != 1:
        return _error(
            request,
            "registration_review_unavailable",
            "Business registration review is not available right now.",
            503,
        )
    reviewer = platform_admins.get()

    blocking = CompanyOwnerInvitation.objects.filter(
        email__iexact=owner_email,
        status__in=[
            CompanyOwnerInvitation.Status.SENT,
            CompanyOwnerInvitation.Status.OPENED,
            CompanyOwnerInvitation.Status.SUBMITTED,
            CompanyOwnerInvitation.Status.APPROVED,
        ],
    ).exists()
    if blocking:
        return _error(
            request,
            "registration_already_active",
            "This email already has an active business registration request.",
            409,
        )

    raw_token, token_hash = generate_token()
    del raw_token
    invitation = CompanyOwnerInvitation(
        email=owner_email,
        token_hash=token_hash,
        status=CompanyOwnerInvitation.Status.OPENED,
        expires_at=timezone.now() + timedelta(days=30),
        created_by=reviewer,
    )
    form = CompanyRegistrationRequestForm(
        request.POST,
        invitation=invitation,
        initial={"business_type": business_type},
    )
    if not form.is_valid():
        return _error(
            request,
            "registration_invalid",
            "Check the business registration details.",
            400,
            fields=_form_errors(form),
        )

    try:
        with transaction.atomic():
            invitation.full_clean()
            invitation.save()
            invitation = submit_registration(
                invitation=invitation,
                form=form,
                request=request,
            )
            append_onboarding_event(
                invitation=invitation,
                actor=current_user if authenticated else None,
                request=request,
                action="MOBILE_REGISTRATION_REQUESTED",
                description=(
                    "Home Management client submitted a business registration request."
                ),
                metadata={
                    "business_type": business_type,
                    "authenticated_request": authenticated,
                    "owner_full_name": owner_full_name,
                    "owner_phone": owner_phone,
                },
            )
    except ValidationError as error:
        return _error(
            request,
            "registration_invalid",
            " ".join(error.messages),
            400,
        )

    return _json(
        request,
        {
            "ok": True,
            "status": invitation.status,
            "requestId": str(invitation.public_id),
            "businessType": business_type,
            "message": (
                "Registration request submitted for Platform Administrator review."
            ),
        },
        status_code=201,
    )
