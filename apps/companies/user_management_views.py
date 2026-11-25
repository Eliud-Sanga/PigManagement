from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .authorization import membership_has_permission
from .constants import (
    PERMISSION_MANAGE_COMPANY_USERS,
    ROLE_OWNER,
)
from .forms_user_management import CompanyUserRequestForm
from .models import Company, CompanyMembership
from .services import (
    archive_company_membership,
    request_company_user,
)


MANAGE_USERS_PERMISSION = (
    "companies."
    f"{PERMISSION_MANAGE_COMPANY_USERS}"
)


def _require_owner_user_management(request):
    if getattr(
        request.user,
        "is_platform_admin",
        False,
    ):
        raise PermissionDenied(
            "Platform Administrator must use the Control Center."
        )

    company = getattr(request, "company", None)
    membership = getattr(
        request,
        "company_membership",
        None,
    )

    if company is None or membership is None:
        raise PermissionDenied(
            "An active company context is required."
        )

    if company.status != Company.Status.ACTIVE:
        raise PermissionDenied(
            "Company is not active."
        )

    # Role is not an authorization bypass. The effective permission
    # check below is mandatory, so explicit denial still wins.
    if membership.role.code != ROLE_OWNER:
        raise PermissionDenied(
            "Only the Company Owner can use delegated user management."
        )

    if not company.owner_user_management_enabled:
        raise PermissionDenied(
            "Owner user management has been disabled by Platform Admin."
        )

    if not membership_has_permission(
        membership=membership,
        permission_code=MANAGE_USERS_PERMISSION,
    ):
        raise PermissionDenied(
            "Your effective permissions do not allow company user management."
        )

    return company, membership


def _security_event(
    *,
    request,
    action,
    subject_user=None,
    description="",
    result="SUCCESS",
    reason_code="COMPANY_OWNER_USER_MANAGEMENT",
):
    from apps.control_center.models import SecurityAuditEvent
    from apps.control_center.services import append_security_event

    severity = (
        SecurityAuditEvent.Severity.HIGH
        if result in {
            SecurityAuditEvent.Result.DENIED,
            SecurityAuditEvent.Result.FAILURE,
        }
        else SecurityAuditEvent.Severity.INFO
    )

    append_security_event(
        request=request,
        category=SecurityAuditEvent.Category.ADMIN,
        action=action,
        result=result,
        severity=severity,
        actor=request.user,
        subject_user=subject_user,
        company=request.company,
        reason_code=reason_code,
        description=description,
    )


@login_required
def company_user_list(request):
    company, membership = (
        _require_owner_user_management(request)
    )

    memberships = (
        CompanyMembership.objects
        .filter(company=company)
        .select_related(
            "user",
            "role",
            "created_by",
            "approved_by",
        )
        .order_by(
            "status",
            "role__name",
            "user__username",
        )
    )

    return render(
        request,
        "companies/company_user_list.html",
        {
            "company": company,
            "membership": membership,
            "memberships": memberships,
        },
    )


@login_required
def company_user_request(request):
    company, membership = (
        _require_owner_user_management(request)
    )

    if request.method == "POST":
        form = CompanyUserRequestForm(
            request.POST,
            company=company,
        )

        if form.is_valid():
            if not request.user.check_password(
                form.cleaned_data["current_password"]
            ):
                _security_event(
                    request=request,
                    action="OWNER_USER_CREATE",
                    result="DENIED",
                    reason_code=(
                        "OWNER_PASSWORD_CONFIRMATION_FAILED"
                    ),
                    description=(
                        "Company Owner user creation denied because "
                        "password confirmation failed."
                    ),
                )

                form.add_error(
                    "current_password",
                    "Your password is incorrect.",
                )
            else:
                try:
                    target = request_company_user(
                        actor=request.user,
                        company=company,
                        role=form.cleaned_data["role"],
                        username=form.cleaned_data[
                            "username"
                        ],
                        email=form.cleaned_data["email"],
                        requested_language=(
                            form.cleaned_data[
                                "requested_language"
                            ]
                        ),
                        first_name=form.cleaned_data[
                            "first_name"
                        ],
                        last_name=form.cleaned_data[
                            "last_name"
                        ],
                    )

                    # PIGS-SYSTEM-LOGIC-CLOSURE-V3: persist new-user phone
                    if getattr(form, "existing_user", None) is None:
                        target.user.phone = form.cleaned_data["phone"]
                        target.user.save(
                            update_fields=["phone", "updated_at"]
                        )

                    _security_event(
                        request=request,
                        action="OWNER_USER_CREATE",
                        subject_user=target.user,
                        description=(
                            "Company Owner created a pending "
                            "company-user request."
                        ),
                    )

                    messages.success(
                        request,
                        (
                            "User request imetengenezwa. "
                            "Platform Admin approval inahitajika."
                        ),
                    )

                    return redirect(
                        "users:company_users"
                    )
                except (
                    ValidationError,
                    PermissionDenied,
                ) as exc:
                    for message in getattr(
                        exc,
                        "messages",
                        [str(exc)],
                    ):
                        form.add_error(
                            None,
                            message,
                        )
    else:
        form = CompanyUserRequestForm(
            company=company
        )

    return render(
        request,
        "companies/company_user_form.html",
        {
            "company": company,
            "membership": membership,
            "form": form,
        },
    )


@login_required
@require_POST
def company_user_remove(
    request,
    membership_id,
):
    company, _ = (
        _require_owner_user_management(request)
    )

    target = get_object_or_404(
        CompanyMembership.objects
        .select_related(
            "company",
            "user",
            "role",
        ),
        public_id=membership_id,
        company=company,
    )

    if not request.user.check_password(
        request.POST.get(
            "current_password",
            "",
        )
    ):
        _security_event(
            request=request,
            action="OWNER_USER_REMOVE",
            subject_user=target.user,
            result="DENIED",
            reason_code=(
                "OWNER_PASSWORD_CONFIRMATION_FAILED"
            ),
            description=(
                "Company Owner user removal denied because "
                "password confirmation failed."
            ),
        )

        messages.error(
            request,
            (
                "User hajaondolewa: password "
                "confirmation si sahihi."
            ),
        )

        return redirect(
            "users:company_users"
        )

    try:
        archive_company_membership(
            actor=request.user,
            membership=target,
        )

        _security_event(
            request=request,
            action="OWNER_USER_REMOVE",
            subject_user=target.user,
            description=(
                "Company Owner archived a company membership. "
                "Global identity was preserved."
            ),
        )

        messages.success(
            request,
            (
                f"{target.user.username} ameondolewa kwenye "
                f"{company.name}. Global account haijafutwa."
            ),
        )
    except (
        ValidationError,
        PermissionDenied,
    ) as exc:
        messages.error(
            request,
            "; ".join(
                getattr(
                    exc,
                    "messages",
                    [str(exc)],
                )
            ),
        )

    return redirect(
        "users:company_users"
    )
