import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .onboarding_models import (
    CompanyInvitationThrottle,
    CompanyOnboardingEvent,
    CompanyOwnerInvitation,
)
from .services import require_platform_admin


TOKEN_BYTES = 32
PUBLIC_FAILURE_LIMIT = 8
PUBLIC_FAILURE_WINDOW_MINUTES = 15
PUBLIC_LOCK_MINUTES = 30
MAX_RESENDS = 5


def _hash(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _client_ip(request):
    # Forwarded headers are client-controlled unless a trusted reverse proxy
    # is explicitly configured to replace them.
    return request.META.get("REMOTE_ADDR")


def _user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")[:500]


def append_onboarding_event(
    *,
    invitation=None,
    actor=None,
    request=None,
    action,
    result="SUCCESS",
    description="",
    metadata=None,
):
    return CompanyOnboardingEvent.objects.create(
        invitation=invitation,
        actor=actor,
        action=action,
        result=result,
        description=description,
        metadata=metadata or {},
        ip_address=_client_ip(request) if request is not None else None,
        user_agent=_user_agent(request) if request is not None else "",
    )


def generate_token():
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    return raw, _hash(raw)


def token_matches(invitation, raw_token):
    return secrets.compare_digest(invitation.token_hash, _hash(raw_token))


def _throttle_key(request):
    ip = _client_ip(request) or "unknown"
    return _hash("company-onboarding-ip:" + ip)


def public_throttle_locked(request):
    row = CompanyInvitationThrottle.objects.filter(
        key_hash=_throttle_key(request)
    ).first()
    return bool(row and row.is_locked())


@transaction.atomic
def register_public_failure(request):
    now = timezone.now()
    row, _ = (
        CompanyInvitationThrottle.objects
        .select_for_update()
        .get_or_create(key_hash=_throttle_key(request))
    )
    cutoff = now - timedelta(minutes=PUBLIC_FAILURE_WINDOW_MINUTES)
    if row.window_started_at is None or row.window_started_at < cutoff:
        row.failed_attempts = 0
        row.window_started_at = now
        row.locked_until = None

    row.failed_attempts += 1
    row.last_attempt_at = now
    if row.failed_attempts >= PUBLIC_FAILURE_LIMIT:
        row.locked_until = now + timedelta(minutes=PUBLIC_LOCK_MINUTES)

    row.save(
        update_fields=[
            "failed_attempts",
            "window_started_at",
            "last_attempt_at",
            "locked_until",
        ]
    )
    return row.locked_until


def clear_public_failures(request):
    CompanyInvitationThrottle.objects.filter(
        key_hash=_throttle_key(request)
    ).delete()


@transaction.atomic
def create_owner_invitation(*, actor, email, expires_hours):
    require_platform_admin(actor)
    email = (email or "").strip().lower()
    expires_hours = int(expires_hours)

    if not email:
        raise ValidationError("Owner email is required.")
    if expires_hours not in {24, 48, 72, 168}:
        raise ValidationError("Unsupported invitation validity.")

    now = timezone.now()
    blocking = (
        CompanyOwnerInvitation.objects
        .filter(
            email__iexact=email,
            status__in=[
                CompanyOwnerInvitation.Status.SENT,
                CompanyOwnerInvitation.Status.OPENED,
                CompanyOwnerInvitation.Status.SUBMITTED,
                CompanyOwnerInvitation.Status.APPROVED,
            ],
        )
        .order_by("-created_at")
        .first()
    )

    if blocking is not None:
        if (
            blocking.status
            in {
                CompanyOwnerInvitation.Status.SENT,
                CompanyOwnerInvitation.Status.OPENED,
            }
            and blocking.is_expired(now=now)
        ):
            blocking.status = CompanyOwnerInvitation.Status.EXPIRED
            blocking.save(update_fields=["status", "updated_at"])
        else:
            raise ValidationError(
                "This email already has an active onboarding request."
            )

    raw_token, token_hash = generate_token()
    invitation = CompanyOwnerInvitation(
        email=email,
        token_hash=token_hash,
        status=CompanyOwnerInvitation.Status.SENT,
        expires_at=now + timedelta(hours=expires_hours),
        created_by=actor,
        sent_at=now,
        last_sent_at=now,
    )
    invitation.full_clean()
    invitation.save()

    append_onboarding_event(
        invitation=invitation,
        actor=actor,
        action="INVITATION_CREATED",
        description="Platform Admin created a prospective Owner invitation.",
        metadata={"expires_hours": expires_hours, "email": email},
    )
    return invitation, raw_token


def send_invitation_email(*, invitation, raw_token, request):
    url = request.build_absolute_uri(
        reverse(
            "users:company_onboarding_register",
            kwargs={
                "invitation_id": invitation.public_id,
                "token": raw_token,
            },
        )
    )
    count = send_mail(
        "Home Management - Company Owner Invitation",
        (
            "You have been invited to register a company on Home Management.\n\n"
            f"Registration link:\n{url}\n\n"
            "This link is private, single-purpose and expires at "
            f"{invitation.expires_at:%Y-%m-%d %H:%M %Z}.\n\n"
            "If you were not expecting this invitation, ignore this email."
        ),
        None,
        [invitation.email],
        fail_silently=False,
    )
    if count != 1:
        raise ValidationError("Invitation email could not be delivered.")

    append_onboarding_event(
        invitation=invitation,
        actor=getattr(request, "user", None),
        request=request,
        action="INVITATION_EMAIL_SENT",
        description="Invitation email accepted by email backend.",
    )
    return url


@transaction.atomic
def rotate_invitation(*, actor, invitation, expires_hours=72):
    require_platform_admin(actor)
    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )
    if invitation.status not in {
        CompanyOwnerInvitation.Status.SENT,
        CompanyOwnerInvitation.Status.OPENED,
        CompanyOwnerInvitation.Status.EXPIRED,
    }:
        raise ValidationError("This invitation can no longer be resent.")
    if invitation.resend_count >= MAX_RESENDS:
        raise ValidationError("Maximum resend limit reached.")

    raw_token, token_hash = generate_token()
    now = timezone.now()
    invitation.token_hash = token_hash
    invitation.status = CompanyOwnerInvitation.Status.SENT
    invitation.expires_at = now + timedelta(hours=int(expires_hours))
    invitation.last_sent_at = now
    invitation.opened_at = None
    invitation.resend_count += 1
    invitation.save(
        update_fields=[
            "token_hash",
            "status",
            "expires_at",
            "last_sent_at",
            "opened_at",
            "resend_count",
            "updated_at",
        ]
    )
    append_onboarding_event(
        invitation=invitation,
        actor=actor,
        action="INVITATION_ROTATED",
        description="Invitation token rotated before resend.",
        metadata={"resend_count": invitation.resend_count},
    )
    return invitation, raw_token


@transaction.atomic
def revoke_invitation(*, actor, invitation):
    require_platform_admin(actor)
    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )
    if invitation.status in {
        CompanyOwnerInvitation.Status.COMPLETED,
        CompanyOwnerInvitation.Status.REJECTED,
        CompanyOwnerInvitation.Status.REVOKED,
    }:
        raise ValidationError("This invitation cannot be revoked.")

    invitation.status = CompanyOwnerInvitation.Status.REVOKED
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["status", "revoked_at", "updated_at"])
    append_onboarding_event(
        invitation=invitation,
        actor=actor,
        action="INVITATION_REVOKED",
        description="Platform Admin revoked invitation.",
    )
    return invitation


@transaction.atomic
def mark_expired(invitation):
    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )
    if invitation.status in {
        CompanyOwnerInvitation.Status.SENT,
        CompanyOwnerInvitation.Status.OPENED,
    }:
        invitation.status = CompanyOwnerInvitation.Status.EXPIRED
        invitation.save(update_fields=["status", "updated_at"])
        append_onboarding_event(
            invitation=invitation,
            action="INVITATION_EXPIRED",
            description="Invitation expired before submission.",
        )
    return invitation


@transaction.atomic
def mark_opened(*, invitation, request):
    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )
    if invitation.status == CompanyOwnerInvitation.Status.SENT:
        invitation.status = CompanyOwnerInvitation.Status.OPENED
        invitation.opened_at = timezone.now()
        invitation.save(update_fields=["status", "opened_at", "updated_at"])
        append_onboarding_event(
            invitation=invitation,
            request=request,
            action="INVITATION_OPENED",
            description="Prospective Owner opened invitation link.",
        )
    return invitation


@transaction.atomic
def submit_registration(*, invitation, form, request):
    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )
    if invitation.is_expired():
        raise ValidationError("Invitation has expired.")
    if invitation.status not in {
        CompanyOwnerInvitation.Status.SENT,
        CompanyOwnerInvitation.Status.OPENED,
    }:
        raise PermissionDenied(
            "Invitation is not available for registration."
        )

    invitation.business_type = form.cleaned_data["business_type"]
    invitation.transport_cargo_enabled = form.cleaned_data.get("transport_cargo_enabled", False)
    invitation.transport_passenger_enabled = form.cleaned_data.get("transport_passenger_enabled", False)
    invitation.company_name = form.cleaned_data["company_name"]
    invitation.company_code = form.cleaned_data["company_code"]
    invitation.company_email = form.cleaned_data.get("company_email") or ""
    invitation.company_phone = form.cleaned_data.get("company_phone") or ""
    invitation.company_address = form.cleaned_data.get("company_address") or ""
    invitation.company_timezone = form.cleaned_data["company_timezone"]
    invitation.allowed_languages = list(form.cleaned_data["allowed_languages"])
    invitation.default_language = form.cleaned_data["default_language"]
    invitation.status = CompanyOwnerInvitation.Status.SUBMITTED
    invitation.submitted_at = timezone.now()
    invitation.submitted_ip = _client_ip(request)
    invitation.full_clean()
    invitation.save(
        update_fields=[
            "business_type",
            "transport_cargo_enabled",
            "transport_passenger_enabled",
            "company_name",
            "company_code",
            "company_email",
            "company_phone",
            "company_address",
            "company_timezone",
            "allowed_languages",
            "default_language",
            "status",
            "submitted_at",
            "submitted_ip",
            "updated_at",
        ]
    )
    append_onboarding_event(
        invitation=invitation,
        request=request,
        action="REGISTRATION_SUBMITTED",
        description="Prospective Owner submitted company registration request.",
        metadata={"company_code": invitation.company_code},
    )
    return invitation

def continuation_token_matches(invitation, raw_token):
    if not invitation.continuation_token_hash:
        return False
    return secrets.compare_digest(
        invitation.continuation_token_hash,
        _hash(raw_token),
    )


def _validate_company_code_still_available(invitation):
    from .models import Company

    if Company.objects.filter(
        code__iexact=invitation.company_code
    ).exists():
        raise ValidationError("Company code is no longer available.")

    collision = (
        CompanyOwnerInvitation.objects
        .filter(
            company_code__iexact=invitation.company_code,
            status__in=[
                CompanyOwnerInvitation.Status.APPROVED,
                CompanyOwnerInvitation.Status.COMPLETED,
            ],
        )
        .exclude(pk=invitation.pk)
        .exists()
    )
    if collision:
        raise ValidationError(
            "Company code is reserved by another approved request."
        )


@transaction.atomic
def approve_registration_request(
    *,
    actor,
    invitation,
    continuation_hours=72,
):
    require_platform_admin(actor)
    continuation_hours = int(continuation_hours)
    if continuation_hours not in {24, 48, 72, 168}:
        raise ValidationError("Unsupported continuation validity.")

    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )
    if invitation.status != CompanyOwnerInvitation.Status.SUBMITTED:
        raise ValidationError(
            "Only submitted registrations can be approved."
        )

    _validate_company_code_still_available(invitation)

    raw_token, token_hash = generate_token()
    now = timezone.now()

    invitation.status = CompanyOwnerInvitation.Status.APPROVED
    invitation.approved_at = now
    invitation.rejected_at = None
    invitation.reviewed_by = actor
    invitation.decision_reason = ""
    invitation.continuation_token_hash = token_hash
    invitation.continuation_expires_at = (
        now + timedelta(hours=continuation_hours)
    )
    invitation.continuation_last_sent_at = now
    invitation.continuation_resend_count = 0
    invitation.save(
        update_fields=[
            "status",
            "approved_at",
            "rejected_at",
            "reviewed_by",
            "decision_reason",
            "continuation_token_hash",
            "continuation_expires_at",
            "continuation_last_sent_at",
            "continuation_resend_count",
            "updated_at",
        ]
    )

    append_onboarding_event(
        invitation=invitation,
        actor=actor,
        action="REGISTRATION_APPROVED",
        description="Platform Admin approved company registration.",
        metadata={
            "company_code": invitation.company_code,
            "continuation_hours": continuation_hours,
        },
    )
    return invitation, raw_token


@transaction.atomic
def reject_registration_request(
    *,
    actor,
    invitation,
    decision_reason,
):
    require_platform_admin(actor)
    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )
    if invitation.status != CompanyOwnerInvitation.Status.SUBMITTED:
        raise ValidationError(
            "Only submitted registrations can be rejected."
        )

    reason = (decision_reason or "").strip()
    if not reason:
        raise ValidationError("A rejection reason is required.")

    invitation.status = CompanyOwnerInvitation.Status.REJECTED
    invitation.rejected_at = timezone.now()
    invitation.reviewed_by = actor
    invitation.decision_reason = reason
    invitation.continuation_token_hash = ""
    invitation.continuation_expires_at = None
    invitation.continuation_last_sent_at = None
    invitation.save(
        update_fields=[
            "status",
            "rejected_at",
            "reviewed_by",
            "decision_reason",
            "continuation_token_hash",
            "continuation_expires_at",
            "continuation_last_sent_at",
            "updated_at",
        ]
    )

    append_onboarding_event(
        invitation=invitation,
        actor=actor,
        action="REGISTRATION_REJECTED",
        description="Platform Admin rejected company registration.",
        metadata={"company_code": invitation.company_code},
    )
    return invitation


def send_approval_email(
    *,
    invitation,
    raw_token,
    request,
):
    url = request.build_absolute_uri(
        reverse(
            "users:company_onboarding_continue",
            kwargs={
                "invitation_id": invitation.public_id,
                "token": raw_token,
            },
        )
    )
    count = send_mail(
        "Home Management - Company Registration Approved",
        (
            "Your company registration has been approved.\n\n"
            f"Company: {invitation.company_name}\n"
            f"Secure Owner setup link:\n{url}\n\n"
            "This private link is required to complete the Owner identity "
            "before business access can be activated."
        ),
        None,
        [invitation.email],
        fail_silently=False,
    )
    if count != 1:
        raise ValidationError("Approval email could not be delivered.")

    append_onboarding_event(
        invitation=invitation,
        actor=getattr(request, "user", None),
        request=request,
        action="APPROVAL_EMAIL_SENT",
        description="Secure Owner continuation email accepted by email backend.",
    )
    return url


def send_rejection_email(
    *,
    invitation,
    request,
):
    count = send_mail(
        "Home Management - Company Registration Review",
        (
            "Your company registration request was not approved.\n\n"
            f"Company: {invitation.company_name}\n"
            f"Review note: {invitation.decision_reason}\n\n"
            "Contact the Platform Administrator if you need clarification "
            "or a fresh invitation."
        ),
        None,
        [invitation.email],
        fail_silently=False,
    )
    if count == 1:
        append_onboarding_event(
            invitation=invitation,
            actor=getattr(request, "user", None),
            request=request,
            action="REJECTION_EMAIL_SENT",
            description="Registration decision email accepted by email backend.",
        )
    return count


@transaction.atomic
def rotate_continuation_token(
    *,
    actor,
    invitation,
    continuation_hours=72,
):
    require_platform_admin(actor)
    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )
    if invitation.status != CompanyOwnerInvitation.Status.APPROVED:
        raise ValidationError(
            "Continuation link can only be resent for an approved request."
        )
    if invitation.continuation_resend_count >= MAX_RESENDS:
        raise ValidationError(
            "Maximum continuation resend limit reached."
        )

    _validate_company_code_still_available(invitation)

    raw_token, token_hash = generate_token()
    now = timezone.now()
    invitation.continuation_token_hash = token_hash
    invitation.continuation_expires_at = (
        now + timedelta(hours=int(continuation_hours))
    )
    invitation.continuation_last_sent_at = now
    invitation.continuation_resend_count += 1
    invitation.save(
        update_fields=[
            "continuation_token_hash",
            "continuation_expires_at",
            "continuation_last_sent_at",
            "continuation_resend_count",
            "updated_at",
        ]
    )
    append_onboarding_event(
        invitation=invitation,
        actor=actor,
        action="CONTINUATION_TOKEN_ROTATED",
        description="Approved Owner continuation token rotated.",
        metadata={
            "continuation_resend_count":
                invitation.continuation_resend_count,
        },
    )
    return invitation, raw_token

LOGIN_NOW_TTL_MINUTES = 10


@transaction.atomic
def complete_owner_onboarding(
    *,
    invitation,
    form,
):
    from django.contrib.auth import get_user_model

    from .models import Company
    from .services import create_company

    User = get_user_model()

    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )

    if invitation.status != CompanyOwnerInvitation.Status.APPROVED:
        raise ValidationError(
            "Owner onboarding is no longer available."
        )

    if (
        invitation.continuation_expires_at is None
        or invitation.continuation_expires_at <= timezone.now()
    ):
        raise ValidationError(
            "Owner setup link has expired."
        )

    # Lock the approving identity separately: reviewed_by is nullable,
    # so PostgreSQL cannot lock it through an outer join.
    actor = (
        User.objects.select_for_update().filter(pk=invitation.reviewed_by_id).first()
        if invitation.reviewed_by_id is not None else None
    )
    if (
        actor is None
        or not actor.is_active
        or not getattr(actor, "is_platform_admin", False)
    ):
        raise PermissionDenied(
            "The approving Platform Administrator is no longer valid."
        )

    if Company.objects.filter(
        code__iexact=invitation.company_code
    ).exists():
        raise ValidationError(
            "Company code is no longer available."
        )

    owner = User.objects.select_for_update().filter(
        email__iexact=invitation.email
    ).first()

    if owner is not None:
        if owner.is_platform_admin:
            raise PermissionDenied(
                "Platform Administrator identity cannot become "
                "a Company Owner."
            )
        if not owner.is_active:
            raise PermissionDenied(
                "Inactive global identity cannot be silently "
                "reactivated through company onboarding."
            )

    if owner is None:
        owner = User(
            username=form.cleaned_data["username"].strip(),
            email=invitation.email,
            first_name=form.cleaned_data["first_name"].strip(),
            last_name=form.cleaned_data["last_name"].strip(),
            phone=form.cleaned_data["phone"].strip(),
            preferred_language=invitation.default_language,
            is_active=True,
            must_change_password=False,
            password_changed_at=timezone.now(),
        )
        owner.set_password(
            form.cleaned_data["new_password1"]
        )
        owner.full_clean()
        owner.save()
    else:
        if (
            owner.username != form.cleaned_data["username"]
            or owner.first_name != form.cleaned_data["first_name"]
            or owner.last_name != form.cleaned_data["last_name"]
            or owner.phone != form.cleaned_data["phone"]
        ):
            raise ValidationError(
                "Existing global identity cannot be rewritten "
                "during company onboarding."
            )

        if owner.has_usable_password():
            if not owner.check_password(
                form.cleaned_data["current_password"]
            ):
                raise PermissionDenied(
                    "Current account password is incorrect."
                )
        else:
            owner.set_password(
                form.cleaned_data["new_password1"]
            )
            owner.must_change_password = False
            owner.password_changed_at = timezone.now()
            owner.save(
                update_fields=[
                    "password",
                    "must_change_password",
                    "password_changed_at",
                    "updated_at",
                ]
            )

    company = create_company(
        actor=actor,
        name=invitation.company_name,
        code=invitation.company_code,
        owner=owner,
        allowed_languages=invitation.allowed_languages,
        default_language=invitation.default_language,
        step_up_verified=True,
        email=invitation.company_email,
        phone=invitation.company_phone,
        address=invitation.company_address,
        business_type=invitation.business_type,
        transport_cargo_enabled=invitation.transport_cargo_enabled,
        transport_passenger_enabled=invitation.transport_passenger_enabled,
    )

    company.timezone = invitation.company_timezone

    # Company provisioning does not delegate local user-management
    # authority automatically. Platform Admin retains governance and
    # must explicitly enable owner_user_management_enabled later.
    company.full_clean()
    company.save(
        update_fields=[
            "timezone",
            "updated_at",
        ]
    )

    raw_login_token, login_token_hash = generate_token()
    now = timezone.now()

    invitation.status = CompanyOwnerInvitation.Status.COMPLETED
    invitation.completed_at = now
    invitation.owner_user = owner
    invitation.provisioned_company = company
    invitation.continuation_token_hash = ""
    invitation.continuation_expires_at = None
    invitation.login_now_token_hash = login_token_hash
    invitation.login_now_expires_at = (
        now + timedelta(minutes=LOGIN_NOW_TTL_MINUTES)
    )
    invitation.login_now_used_at = None
    invitation.save(
        update_fields=[
            "status",
            "completed_at",
            "owner_user",
            "provisioned_company",
            "continuation_token_hash",
            "continuation_expires_at",
            "login_now_token_hash",
            "login_now_expires_at",
            "login_now_used_at",
            "updated_at",
        ]
    )

    append_onboarding_event(
        invitation=invitation,
        actor=owner,
        action="OWNER_ONBOARDING_COMPLETED",
        description=(
            "Owner identity and Company were provisioned atomically."
        ),
        metadata={
            "company_public_id": str(company.public_id),
            "owner_user_id": owner.pk,
            "existing_identity":
                form.existing_user is not None,
            "owner_user_management_enabled": company.owner_user_management_enabled,
        },
    )

    return invitation, owner, company, raw_login_token


@transaction.atomic
def consume_login_now_token(
    *,
    invitation,
    raw_token,
):
    invitation = (
        CompanyOwnerInvitation.objects
        .select_for_update()
        .get(pk=invitation.pk)
    )

    if (
        invitation.status
        != CompanyOwnerInvitation.Status.COMPLETED
        or invitation.owner_user_id is None
        or invitation.provisioned_company_id is None
        or invitation.login_now_used_at is not None
        or not invitation.login_now_token_hash
        or invitation.login_now_expires_at is None
        or invitation.login_now_expires_at <= timezone.now()
    ):
        raise PermissionDenied(
            "Login handoff is not available."
        )

    if not secrets.compare_digest(
        invitation.login_now_token_hash,
        _hash(raw_token),
    ):
        raise PermissionDenied(
            "Login handoff token is invalid."
        )

    invitation.login_now_used_at = timezone.now()
    invitation.login_now_token_hash = ""
    invitation.save(
        update_fields=[
            "login_now_used_at",
            "login_now_token_hash",
            "updated_at",
        ]
    )

    return invitation
