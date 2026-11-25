from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .onboarding_forms import (
    CompanyRegistrationRequestForm,
    OwnerInvitationCreateForm,
    OwnerCompletionForm,
    RegistrationApprovalForm,
    RegistrationRejectionForm,
)
from .onboarding_models import CompanyOwnerInvitation
from .models import CompanyMembership
from .onboarding_services import (
    append_onboarding_event,
    approve_registration_request,
    clear_public_failures,
    continuation_token_matches,
    complete_owner_onboarding,
    consume_login_now_token,
    create_owner_invitation,
    mark_expired,
    mark_opened,
    public_throttle_locked,
    register_public_failure,
    reject_registration_request,
    revoke_invitation,
    rotate_continuation_token,
    rotate_invitation,
    send_approval_email,
    send_invitation_email,
    send_rejection_email,
    submit_registration,
    token_matches,
)


User = get_user_model()


def _platform_admin_required(request):
    user = request.user
    if (
        not user.is_authenticated
        or not user.is_active
        or not getattr(user, "is_platform_admin", False)
    ):
        raise PermissionDenied(
            "Platform Administrator authority is required."
        )


def _admin_password_ok(request, password, *, invitation=None, action):
    valid = request.user.check_password(password or "")
    if not valid:
        append_onboarding_event(
            invitation=invitation,
            actor=request.user,
            request=request,
            action=action,
            result="DENIED",
            description="Administrator password confirmation failed.",
        )
    return valid


def _service_error(form, exc):
    for message in getattr(exc, "messages", [str(exc)]):
        form.add_error(None, message)


@login_required
def admin_invitation_list(request):
    _platform_admin_required(request)
    invitations = (
        CompanyOwnerInvitation.objects
        .select_related("created_by")
        .order_by("-created_at")
    )
    return render(
        request,
        "control_center/invitation_list.html",
        {"invitations": invitations},
    )


@login_required
def admin_invitation_create(request):
    _platform_admin_required(request)

    if request.method == "POST":
        form = OwnerInvitationCreateForm(request.POST)

        if form.is_valid():
            if not _admin_password_ok(
                request,
                form.cleaned_data["admin_password"],
                action="OWNER_INVITATION_CREATE",
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                try:
                    with transaction.atomic():
                        invitation, raw_token = create_owner_invitation(
                            actor=request.user,
                            email=form.cleaned_data["email"],
                            expires_hours=form.cleaned_data["expires_hours"],
                        )
                        send_invitation_email(
                            invitation=invitation,
                            raw_token=raw_token,
                            request=request,
                        )
                        append_onboarding_event(
                            invitation=invitation,
                            actor=request.user,
                            request=request,
                            action="OWNER_INVITATION_CREATE",
                            description=(
                                "Platform Admin created and sent "
                                "a secure Owner invitation."
                            ),
                        )

                    messages.success(
                        request,
                        "Secure company registration invitation sent.",
                    )
                    return redirect(
                        "users:owner_invitation_detail",
                        invitation_id=invitation.public_id,
                    )
                except (ValidationError, PermissionDenied) as exc:
                    _service_error(form, exc)
    else:
        form = OwnerInvitationCreateForm()

    return render(
        request,
        "control_center/invitation_create.html",
        {"form": form},
    )


@login_required
def admin_invitation_detail(request, invitation_id):
    _platform_admin_required(request)
    invitation = get_object_or_404(
        CompanyOwnerInvitation.objects.select_related("created_by"),
        public_id=invitation_id,
    )
    return render(
        request,
        "control_center/invitation_detail.html",
        {
            "invitation": invitation,
            "events": invitation.events.all()[:80],
            "approval_form": RegistrationApprovalForm(),
            "rejection_form": RegistrationRejectionForm(),
        },
    )


@login_required
@require_POST
def admin_invitation_resend(request, invitation_id):
    _platform_admin_required(request)
    invitation = get_object_or_404(
        CompanyOwnerInvitation,
        public_id=invitation_id,
    )

    if not _admin_password_ok(
        request,
        request.POST.get("admin_password"),
        invitation=invitation,
        action="OWNER_INVITATION_RESEND",
    ):
        messages.error(request, "Administrator password is incorrect.")
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    try:
        with transaction.atomic():
            invitation, raw_token = rotate_invitation(
                actor=request.user,
                invitation=invitation,
            )
            send_invitation_email(
                invitation=invitation,
                raw_token=raw_token,
                request=request,
            )
        messages.success(
            request,
            "Invitation resent with a new secure link.",
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(
            request,
            " ".join(getattr(exc, "messages", [str(exc)])),
        )

    return redirect(
        "users:owner_invitation_detail",
        invitation_id=invitation.public_id,
    )


@login_required
@require_POST
def admin_invitation_revoke(request, invitation_id):
    _platform_admin_required(request)
    invitation = get_object_or_404(
        CompanyOwnerInvitation,
        public_id=invitation_id,
    )

    if not _admin_password_ok(
        request,
        request.POST.get("admin_password"),
        invitation=invitation,
        action="OWNER_INVITATION_REVOKE",
    ):
        messages.error(request, "Administrator password is incorrect.")
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    try:
        revoke_invitation(
            actor=request.user,
            invitation=invitation,
        )
        messages.success(request, "Invitation revoked.")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(
            request,
            " ".join(getattr(exc, "messages", [str(exc)])),
        )

    return redirect(
        "users:owner_invitation_detail",
        invitation_id=invitation.public_id,
    )


def _invalid_invitation(request, *, status=404):
    return render(
        request,
        "companies/onboarding/invalid.html",
        status=status,
    )


@require_http_methods(["GET", "POST"])
def company_onboarding_register(request, invitation_id, token):
    if public_throttle_locked(request):
        return _invalid_invitation(request, status=429)

    invitation = (
        CompanyOwnerInvitation.objects
        .filter(public_id=invitation_id)
        .first()
    )

    if invitation is None or not token_matches(invitation, token):
        register_public_failure(request)
        return _invalid_invitation(request, status=404)

    clear_public_failures(request)

    if invitation.is_expired():
        mark_expired(invitation)
        return _invalid_invitation(request, status=410)

    if invitation.status == CompanyOwnerInvitation.Status.SUBMITTED:
        return render(
            request,
            "companies/onboarding/submitted.html",
            {"invitation": invitation},
        )

    if invitation.status not in {
        CompanyOwnerInvitation.Status.SENT,
        CompanyOwnerInvitation.Status.OPENED,
    }:
        return _invalid_invitation(request, status=410)

    invitation = mark_opened(
        invitation=invitation,
        request=request,
    )

    if request.method == "POST":
        form = CompanyRegistrationRequestForm(
            request.POST,
            invitation=invitation,
            initial={"business_type": invitation.business_type},
        )
        if form.is_valid():
            try:
                invitation = submit_registration(
                    invitation=invitation,
                    form=form,
                    request=request,
                )
                return render(
                    request,
                    "companies/onboarding/submitted.html",
                    {"invitation": invitation},
                )
            except (ValidationError, PermissionDenied) as exc:
                _service_error(form, exc)
    else:
        form = CompanyRegistrationRequestForm(
            invitation=invitation,
        )

    return render(
        request,
        "companies/onboarding/register.html",
        {
            "form": form,
            "invitation": invitation,
        },
    )

@login_required
@require_POST
def admin_invitation_approve(request, invitation_id):
    _platform_admin_required(request)
    invitation = get_object_or_404(
        CompanyOwnerInvitation,
        public_id=invitation_id,
    )
    form = RegistrationApprovalForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Approval form is invalid.")
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    if not _admin_password_ok(
        request,
        form.cleaned_data["admin_password"],
        invitation=invitation,
        action="REGISTRATION_APPROVE",
    ):
        messages.error(request, "Administrator password is incorrect.")
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    try:
        invitation, raw_token = approve_registration_request(
            actor=request.user,
            invitation=invitation,
            continuation_hours=form.cleaned_data["continuation_hours"],
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(
            request,
            " ".join(getattr(exc, "messages", [str(exc)])),
        )
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    try:
        send_approval_email(
            invitation=invitation,
            raw_token=raw_token,
            request=request,
        )
        messages.success(
            request,
            "Registration approved and secure Owner setup link sent.",
        )
    except Exception:
        append_onboarding_event(
            invitation=invitation,
            actor=request.user,
            request=request,
            action="APPROVAL_EMAIL_SEND_FAILED",
            result="FAILURE",
            description=(
                "Registration was approved but continuation email "
                "delivery failed. Admin can resend a new link."
            ),
        )
        messages.error(
            request,
            "Registration approved, but email delivery failed. "
            "Use Resend continuation link.",
        )

    return redirect(
        "users:owner_invitation_detail",
        invitation_id=invitation.public_id,
    )


@login_required
@require_POST
def admin_invitation_reject(request, invitation_id):
    _platform_admin_required(request)
    invitation = get_object_or_404(
        CompanyOwnerInvitation,
        public_id=invitation_id,
    )
    form = RegistrationRejectionForm(request.POST)

    if not form.is_valid():
        messages.error(
            request,
            "Rejection requires a valid review reason.",
        )
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    if not _admin_password_ok(
        request,
        form.cleaned_data["admin_password"],
        invitation=invitation,
        action="REGISTRATION_REJECT",
    ):
        messages.error(request, "Administrator password is incorrect.")
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    try:
        invitation = reject_registration_request(
            actor=request.user,
            invitation=invitation,
            decision_reason=form.cleaned_data["decision_reason"],
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(
            request,
            " ".join(getattr(exc, "messages", [str(exc)])),
        )
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    try:
        send_rejection_email(invitation=invitation, request=request)
    except Exception:
        append_onboarding_event(
            invitation=invitation,
            actor=request.user,
            request=request,
            action="REJECTION_EMAIL_SEND_FAILED",
            result="FAILURE",
            description=(
                "Registration rejection was saved but decision "
                "email delivery failed."
            ),
        )

    messages.success(request, "Registration request rejected.")
    return redirect(
        "users:owner_invitation_detail",
        invitation_id=invitation.public_id,
    )


@login_required
@require_POST
def admin_invitation_continuation_resend(
    request,
    invitation_id,
):
    _platform_admin_required(request)
    invitation = get_object_or_404(
        CompanyOwnerInvitation,
        public_id=invitation_id,
    )

    if not _admin_password_ok(
        request,
        request.POST.get("admin_password"),
        invitation=invitation,
        action="CONTINUATION_RESEND",
    ):
        messages.error(request, "Administrator password is incorrect.")
        return redirect(
            "users:owner_invitation_detail",
            invitation_id=invitation.public_id,
        )

    try:
        invitation, raw_token = rotate_continuation_token(
            actor=request.user,
            invitation=invitation,
        )
        send_approval_email(
            invitation=invitation,
            raw_token=raw_token,
            request=request,
        )
        messages.success(
            request,
            "A new secure continuation link was sent.",
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(
            request,
            " ".join(getattr(exc, "messages", [str(exc)])),
        )

    return redirect(
        "users:owner_invitation_detail",
        invitation_id=invitation.public_id,
    )


@require_http_methods(["GET"])
def company_onboarding_continue(
    request,
    invitation_id,
    token,
):
    if public_throttle_locked(request):
        return _invalid_invitation(request, status=429)

    invitation = (
        CompanyOwnerInvitation.objects
        .filter(public_id=invitation_id)
        .first()
    )

    if (
        invitation is None
        or not continuation_token_matches(invitation, token)
    ):
        register_public_failure(request)
        return _invalid_invitation(request, status=404)

    clear_public_failures(request)

    if invitation.status != CompanyOwnerInvitation.Status.APPROVED:
        return _invalid_invitation(request, status=410)

    if (
        invitation.continuation_expires_at is None
        or invitation.continuation_expires_at <= timezone.now()
    ):
        return _invalid_invitation(request, status=410)

    append_onboarding_event(
        invitation=invitation,
        request=request,
        action="CONTINUATION_LINK_OPENED",
        description="Approved Owner opened secure continuation link.",
    )

    return render(
        request,
        "companies/onboarding/approved.html",
        {"invitation": invitation, "token": token},
    )

@require_http_methods(["GET", "POST"])
def company_onboarding_complete(
    request,
    invitation_id,
    token,
):
    if public_throttle_locked(request):
        return _invalid_invitation(request, status=429)

    invitation = (
        CompanyOwnerInvitation.objects
        .filter(public_id=invitation_id)
        .first()
    )

    if (
        invitation is None
        or not continuation_token_matches(invitation, token)
    ):
        register_public_failure(request)
        return _invalid_invitation(request, status=404)

    clear_public_failures(request)

    if invitation.status != CompanyOwnerInvitation.Status.APPROVED:
        return _invalid_invitation(request, status=410)

    if (
        invitation.continuation_expires_at is None
        or invitation.continuation_expires_at <= timezone.now()
    ):
        return _invalid_invitation(request, status=410)

    existing_user = User.objects.filter(
        email__iexact=invitation.email
    ).first()

    if (
        existing_user is not None
        and (
            existing_user.is_platform_admin
            or not existing_user.is_active
        )
    ):
        return render(
            request,
            "companies/onboarding/invalid.html",
            status=403,
        )

    if request.method == "POST":
        form = OwnerCompletionForm(
            request.POST,
            invitation=invitation,
        )

        if form.is_valid():
            try:
                (
                    invitation,
                    owner,
                    company,
                    login_token,
                ) = complete_owner_onboarding(
                    invitation=invitation,
                    form=form,
                )
            except (ValidationError, PermissionDenied) as exc:
                _service_error(form, exc)
            else:
                return render(
                    request,
                    "companies/onboarding/completed.html",
                    {
                        "invitation": invitation,
                        "login_token": login_token,
                    },
                )
    else:
        form = OwnerCompletionForm(
            invitation=invitation,
        )

    return render(
        request,
        "companies/onboarding/complete.html",
        {
            "form": form,
            "invitation": invitation,
            "existing_user": existing_user,
        },
    )


@require_POST
def company_onboarding_login_now(
    request,
    invitation_id,
):
    from django.contrib.auth import login

    invitation = get_object_or_404(
        CompanyOwnerInvitation.objects.select_related(
            "owner_user",
            "provisioned_company",
        ),
        public_id=invitation_id,
    )

    raw_token = request.POST.get("login_token", "")

    try:
        invitation = consume_login_now_token(
            invitation=invitation,
            raw_token=raw_token,
        )
    except PermissionDenied:
        return _invalid_invitation(
            request,
            status=404,
        )

    owner = invitation.owner_user

    if (
        owner is None
        or not owner.is_active
        or owner.is_platform_admin
        or not CompanyMembership.objects.active().filter(
            company=invitation.provisioned_company,
            user=owner,
            role__code="OWNER",
        ).exists()
    ):
        return _invalid_invitation(
            request,
            status=403,
        )

    login(
        request,
        owner,
        backend="apps.companies.backends.CompanyRoleBackend",
    )

    append_onboarding_event(
        invitation=invitation,
        actor=owner,
        request=request,
        action="OWNER_LOGIN_NOW",
        description=(
            "Owner used one-time handoff and entered "
            "normal authenticated session."
        ),
    )

    return redirect("pigs:dashboard")
