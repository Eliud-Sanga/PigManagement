from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.companies.activation_identity import has_established_sign_in
from apps.companies.activation_services import (
    issue_company_user_activation,
    send_company_user_activation_email,
)
from apps.companies.authorization import get_effective_permission_codes
from apps.companies.constants import ROLE_OWNER
from apps.companies.models import (
    Company,
    CompanyAuditEvent,
    CompanyMembership,
    CompanyRole,
    CompanyUserActivation,
)
from apps.companies.owner_safety import is_last_active_owner
from apps.companies.services import (
    approve_company_membership,
    create_company,
    request_company_user,
    set_company_language_policy,
    set_company_role_permissions,
    set_company_status,
    set_membership_permission_overrides,
)

from .company_forms import (
    CompanyCreateForm,
    CompanyEditForm,
    CompanyLanguageForm,
    CompanyMemberRequestForm,
    MembershipApprovalForm,
    MembershipEditForm,
    MembershipOverridesForm,
    RoleCreateForm,
    RolePermissionsForm,
    permission_code,
)
from .models import SecurityAuditEvent
from .services import append_security_event


User = get_user_model()


def _platform_admin_required(request):
    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or not getattr(
            request.user,
            "is_platform_admin",
            False,
        )
    ):
        raise PermissionDenied(
            "Platform Administrator authority is required."
        )


def _verify_admin_password(
    request,
    password,
    *,
    action,
    company=None,
    subject_user=None,
):
    valid = request.user.check_password(password or "")

    if not valid:
        append_security_event(
            request=request,
            category=SecurityAuditEvent.Category.ADMIN,
            action=action,
            result=SecurityAuditEvent.Result.DENIED,
            severity=SecurityAuditEvent.Severity.HIGH,
            actor=request.user,
            subject_user=subject_user,
            company=company,
            reason_code="ADMIN_PASSWORD_CONFIRMATION_FAILED",
            description=(
                "Sensitive administrator action denied because "
                "current administrator password confirmation failed."
            ),
        )

    return valid


def _security_success(
    request,
    *,
    action,
    company=None,
    subject_user=None,
    description="",
    metadata=None,
):
    return append_security_event(
        request=request,
        category=SecurityAuditEvent.Category.ADMIN,
        action=action,
        result=SecurityAuditEvent.Result.SUCCESS,
        severity=SecurityAuditEvent.Severity.HIGH,
        actor=request.user,
        subject_user=subject_user,
        company=company,
        reason_code="ADMIN_GOVERNANCE_ACTION",
        description=description,
        metadata=metadata or {},
    )


def _form_service_error(form, exc):
    if hasattr(exc, "message_dict"):
        for field, errors in exc.message_dict.items():
            for error in errors:
                form.add_error(
                    field if field in form.fields else None,
                    error,
                )
        return

    messages_list = getattr(exc, "messages", None)
    if messages_list:
        for message in messages_list:
            form.add_error(None, message)
        return

    form.add_error(None, str(exc))


def _owner_is_last_active_owner(membership):
    return is_last_active_owner(membership)


def _permission_set(queryset):
    return {
        permission_code(permission)
        for permission
        in queryset.select_related("content_type")
    }


@login_required
def company_list(request):
    _platform_admin_required(request)

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    companies = (
        Company.objects
        .annotate(
            membership_count=Count(
                "memberships",
                distinct=True,
            ),
            active_membership_count=Count(
                "memberships",
                filter=Q(
                    memberships__status=CompanyMembership.Status.ACTIVE
                ),
                distinct=True,
            ),
        )
        .order_by("name")
    )

    if query:
        companies = companies.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        )

    if status:
        companies = companies.filter(status=status)

    return render(
        request,
        "control_center/company_list.html",
        {
            "companies": companies,
            "query": query,
            "selected_status": status,
            "statuses": Company.Status.choices,
        },
    )


@login_required
def company_create_view(request):
    _platform_admin_required(request)

    if request.method == "POST":
        form = CompanyCreateForm(request.POST)

        if form.is_valid():
            if not _verify_admin_password(
                request,
                form.cleaned_data["admin_password"],
                action="COMPANY_CREATE",
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                try:
                    with transaction.atomic():
                        owner = form.existing_owner
                        password = form.cleaned_data["temporary_password1"]
                        if owner is not None:
                            owner = User.objects.select_for_update().get(pk=owner.pk)
                            if not owner.is_active:
                                raise ValidationError("This account is inactive. Reactivate it before adding it as a company owner.")

                        if owner is None:
                            owner = User(
                                username=form.cleaned_data[
                                    "owner_username"
                                ].strip(),
                                email=form.cleaned_data["owner_email"],
                                first_name=form.cleaned_data[
                                    "owner_first_name"
                                ].strip(),
                                last_name=form.cleaned_data[
                                    "owner_last_name"
                                ].strip(),
                                preferred_language=form.cleaned_data[
                                    "default_language"
                                ],
                                is_active=True,
                                must_change_password=True,
                            )
                            owner.set_password(password)
                            owner.full_clean()
                            owner.save()

                        elif password:
                            owner.set_password(password)
                            owner.must_change_password = True
                            owner.password_changed_at = timezone.now()
                            owner.save(
                                update_fields=[
                                    "password",
                                    "must_change_password",
                                    "password_changed_at",
                                ]
                            )

                        company = create_company(
                            actor=request.user,
                            name=form.cleaned_data["name"],
                            code=form.cleaned_data["code"],
                            owner=owner,
                            allowed_languages=form.cleaned_data[
                                "allowed_languages"
                            ],
                            default_language=form.cleaned_data[
                                "default_language"
                            ],
                            step_up_verified=True,
                            email=form.cleaned_data["email"],
                            phone=form.cleaned_data["phone"],
                            address=form.cleaned_data["address"],
                            business_type=form.cleaned_data["business_type"],
                            transport_cargo_enabled=form.cleaned_data["transport_cargo_enabled"],
                            transport_passenger_enabled=form.cleaned_data["transport_passenger_enabled"],
                        )

                        company.timezone = form.cleaned_data["timezone"]
                        company.full_clean()
                        company.save(
                            update_fields=["timezone", "updated_at"]
                        )

                        _security_success(
                            request,
                            action="COMPANY_CREATE",
                            company=company,
                            subject_user=owner,
                            description=(
                                "Company created from Platform Control Center."
                            ),
                            metadata={
                                "company_code": company.code,
                                "owner_user_id": owner.pk,
                                "owner_reused": (
                                    form.existing_owner is not None
                                ),
                                "allowed_languages": list(
                                    company.allowed_languages or []
                                ),
                            },
                        )

                    messages.success(
                        request,
                        f"Company '{company.name}' imetengenezwa.",
                    )
                    return redirect(
                        "users:company_detail",
                        company_id=company.public_id,
                    )

                except (ValidationError, PermissionDenied) as exc:
                    _form_service_error(form, exc)
                except OperationalError:
                    form.add_error(None, "Account is busy. Reload before retrying.")
    else:
        form = CompanyCreateForm(
            initial={
                "allowed_languages": ["sw", "en"],
                "default_language": "sw",
                "timezone": "Africa/Dar_es_Salaam",
            }
        )

    return render(
        request,
        "control_center/company_form.html",
        {
            "form": form,
            "mode": "create",
        },
    )


@login_required
def company_detail(request, company_id):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    memberships = (
        CompanyMembership.objects
        .filter(company=company)
        .select_related(
            "user",
            "role",
            "approved_by",
            "created_by",
        )
        .order_by(
            "status",
            "role__name",
            "user__username",
        )
    )

    roles = (
        company.roles
        .prefetch_related("permissions")
        .order_by("is_reserved", "name")
    )

    pending_count = memberships.filter(
        status=CompanyMembership.Status.PENDING_APPROVAL
    ).count()

    return render(
        request,
        "control_center/company_detail.html",
        {
            "company": company,
            "memberships": memberships,
            "roles": roles,
            "pending_count": pending_count,
        },
    )


@login_required
def company_edit(request, company_id):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    before = {
        "name": company.name,
        "email": company.email,
        "phone": company.phone,
        "address": company.address,
        "timezone": company.timezone,
    }

    if request.method == "POST":
        form = CompanyEditForm(
            request.POST,
            instance=company,
        )

        if form.is_valid():
            if not _verify_admin_password(
                request,
                form.cleaned_data["admin_password"],
                action="COMPANY_PROFILE_UPDATE",
                company=company,
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                updated = form.save(commit=False)
                updated.full_clean()
                updated.save()

                after = {
                    "name": updated.name,
                    "email": updated.email,
                    "phone": updated.phone,
                    "address": updated.address,
                    "timezone": updated.timezone,
                }

                CompanyAuditEvent.objects.create(
                    company=updated,
                    actor=request.user,
                    action="COMPANY_PROFILE_UPDATED",
                    description="Company profile updated.",
                    metadata={
                        "before": before,
                        "after": after,
                    },
                )

                _security_success(
                    request,
                    action="COMPANY_PROFILE_UPDATE",
                    company=updated,
                    description=(
                        "Company profile updated after "
                        "administrator password confirmation."
                    ),
                    metadata={
                        "before": before,
                        "after": after,
                    },
                )

                messages.success(
                    request,
                    "Company details zimehifadhiwa.",
                )

                return redirect(
                    "users:company_detail",
                    company_id=company.public_id,
                )
    else:
        form = CompanyEditForm(instance=company)

    return render(
        request,
        "control_center/company_form.html",
        {
            "form": form,
            "mode": "edit",
            "company": company,
        },
    )


@login_required
def company_languages(request, company_id):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    if request.method == "POST":
        form = CompanyLanguageForm(
            request.POST,
            company=company,
        )

        if form.is_valid():
            if not _verify_admin_password(
                request,
                form.cleaned_data["admin_password"],
                action="COMPANY_LANGUAGE_POLICY_UPDATE",
                company=company,
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                try:
                    set_company_language_policy(
                        actor=request.user,
                        company=company,
                        allowed_languages=form.cleaned_data[
                            "allowed_languages"
                        ],
                        default_language=form.cleaned_data[
                            "default_language"
                        ],
                        step_up_verified=True,
                    )

                    _security_success(
                        request,
                        action="COMPANY_LANGUAGE_POLICY_UPDATE",
                        company=company,
                        description="Company language policy updated.",
                        metadata={
                            "allowed_languages": form.cleaned_data[
                                "allowed_languages"
                            ],
                            "default_language": form.cleaned_data[
                                "default_language"
                            ],
                        },
                    )

                    messages.success(
                        request,
                        "Company language policy imehifadhiwa.",
                    )
                    return redirect(
                        "users:company_detail",
                        company_id=company.public_id,
                    )

                except (ValidationError, PermissionDenied) as exc:
                    _form_service_error(form, exc)
    else:
        form = CompanyLanguageForm(company=company)

    return render(
        request,
        "control_center/company_languages.html",
        {
            "company": company,
            "form": form,
        },
    )


@login_required
@require_POST
def company_status_action(request, company_id, action):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    action_map = {
        "suspend": Company.Status.SUSPENDED,
        "activate": Company.Status.ACTIVE,
        "archive": Company.Status.ARCHIVED,
    }

    target_status = action_map.get(action)
    if target_status is None:
        raise PermissionDenied("Unknown company lifecycle action.")

    if not _verify_admin_password(
        request,
        request.POST.get("admin_password"),
        action="COMPANY_STATUS_CHANGE",
        company=company,
    ):
        messages.error(
            request,
            (
                "Company status haijabadilishwa: "
                "administrator password si sahihi."
            ),
        )
        return redirect(
            "users:company_detail",
            company_id=company.public_id,
        )

    try:
        previous = company.status

        set_company_status(
            actor=request.user,
            company=company,
            status=target_status,
            step_up_verified=True,
        )

        _security_success(
            request,
            action="COMPANY_STATUS_CHANGE",
            company=company,
            description="Company lifecycle status changed.",
            metadata={
                "before": previous,
                "after": target_status,
            },
        )

        messages.success(
            request,
            f"Company status: {previous} → {target_status}.",
        )

    except (ValidationError, PermissionDenied) as exc:
        messages.error(
            request,
            "; ".join(
                getattr(exc, "messages", [str(exc)])
            ),
        )

    return redirect(
        "users:company_detail",
        company_id=company.public_id,
    )


@login_required
def company_member_request(request, company_id):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    if request.method == "POST":
        form = CompanyMemberRequestForm(
            request.POST,
            company=company,
        )

        if form.is_valid():
            if not _verify_admin_password(
                request,
                form.cleaned_data["admin_password"],
                action="COMPANY_MEMBER_REQUEST",
                company=company,
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                try:
                    membership = request_company_user(
                        actor=request.user,
                        company=company,
                        role=form.cleaned_data["role"],
                        username=form.cleaned_data["username"],
                        email=form.cleaned_data["email"],
                        requested_language=form.cleaned_data[
                            "requested_language"
                        ],
                        first_name=form.cleaned_data["first_name"],
                        last_name=form.cleaned_data["last_name"],
                    )

                    _security_success(
                        request,
                        action="COMPANY_MEMBER_REQUEST",
                        company=company,
                        subject_user=membership.user,
                        description="Company membership request created.",
                        metadata={
                            "membership_id": membership.pk,
                            "role": membership.role.code,
                            "requested_language": (
                                membership.requested_language
                            ),
                        },
                    )

                    messages.success(
                        request,
                        (
                            "Membership request created. "
                            "Ipo pending approval."
                        ),
                    )

                    return redirect(
                        "users:approval_review",
                        membership_id=membership.public_id,
                    )

                except (ValidationError, PermissionDenied) as exc:
                    _form_service_error(form, exc)
    else:
        form = CompanyMemberRequestForm(company=company)

    return render(
        request,
        "control_center/member_request.html",
        {
            "company": company,
            "form": form,
        },
    )


@login_required
def approval_list(request):
    _platform_admin_required(request)

    memberships = (
        CompanyMembership.objects
        .filter(status=CompanyMembership.Status.PENDING_APPROVAL)
        .select_related(
            "company",
            "user",
            "role",
            "created_by",
        )
        .order_by("created_at")
    )

    return render(
        request,
        "control_center/approval_list.html",
        {"memberships": memberships},
    )


@login_required
def approval_review(request, membership_id):
    _platform_admin_required(request)

    membership = get_object_or_404(
        CompanyMembership.objects.select_related(
            "company",
            "user",
            "role",
            "created_by",
        ),
        public_id=membership_id,
    )

    if request.method == "POST":
        decision = (
            request.POST.get("decision", "approve")
            .strip()
            .lower()
        )

        form = MembershipApprovalForm(
            request.POST,
            membership=membership,
        )

        if decision == "reject":
            if not _verify_admin_password(
                request,
                request.POST.get("admin_password"),
                action="COMPANY_MEMBER_REJECT",
                company=membership.company,
                subject_user=membership.user,
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            elif (
                membership.status
                != CompanyMembership.Status.PENDING_APPROVAL
            ):
                form.add_error(
                    None,
                    "Only a pending membership can be rejected.",
                )
            else:
                with transaction.atomic():
                    membership.status = CompanyMembership.Status.REJECTED
                    membership.approved_language = ""
                    membership.approved_by = None
                    membership.approved_at = None
                    membership.save(
                        update_fields=[
                            "status",
                            "approved_language",
                            "approved_by",
                            "approved_at",
                            "updated_at",
                        ]
                    )

                    CompanyAuditEvent.objects.create(
                        company=membership.company,
                        actor=request.user,
                        action="COMPANY_USER_REJECTED",
                        description=(
                            f"{membership.user.username} membership rejected."
                        ),
                        metadata={
                            "membership_id": membership.pk,
                            "role_code": membership.role.code,
                        },
                    )

                    _security_success(
                        request,
                        action="COMPANY_MEMBER_REJECT",
                        company=membership.company,
                        subject_user=membership.user,
                        description="Pending membership rejected.",
                        metadata={
                            "membership_id": membership.pk,
                        },
                    )

                messages.success(
                    request,
                    "Membership request rejected.",
                )
                return redirect("users:approvals")

        else:
            attempted_password = (
                request.POST.get("temporary_password1", "")
                or request.POST.get("temporary_password2", "")
            )

            if attempted_password:
                form.add_error(
                    None,
                    (
                        "Platform Admin cannot set this user's password. "
                        "Approve normally; a secure activation link "
                        "will be emailed."
                    ),
                )

            elif form.is_valid():
                if not _verify_admin_password(
                    request,
                    form.cleaned_data["admin_password"],
                    action="COMPANY_MEMBER_APPROVE",
                    company=membership.company,
                    subject_user=membership.user,
                ):
                    form.add_error(
                        "admin_password",
                        "Administrator password is incorrect.",
                    )
                else:
                    try:
                        activation = None

                        with transaction.atomic():
                            approve_company_membership(
                                actor=request.user,
                                membership=membership,
                                approved_language=(
                                    form.cleaned_data["approved_language"]
                                ),
                                step_up_verified=True,
                            )

                            membership.refresh_from_db()
                            activation_required = not has_established_sign_in(membership.user)

                            if activation_required:
                                activation, raw_token = (
                                    issue_company_user_activation(
                                        membership=membership,
                                        actor=request.user,
                                    )
                                )

                                send_company_user_activation_email(
                                    activation=activation,
                                    raw_token=raw_token,
                                    request=request,
                                    actor=request.user,
                                )

                        _security_success(
                            request,
                            action="COMPANY_MEMBER_APPROVE",
                            company=membership.company,
                            subject_user=membership.user,
                            description=(
                                "Pending company membership approved."
                            ),
                            metadata={
                                "membership_id": membership.pk,
                                "role": membership.role.code,
                                "approved_language":
                                    membership.approved_language,
                                "activation_required":
                                    activation_required,
                                "activation_email_sent":
                                    bool(activation),
                            },
                        )

                        if activation_required:
                            messages.success(
                                request,
                                (
                                    f"{membership.user.username} "
                                    "ameidhinishwa. Secure activation "
                                    "email imetumwa."
                                ),
                            )
                        else:
                            messages.success(
                                request,
                                (
                                    f"{membership.user.username} "
                                    "ameidhinishwa bila kubadilisha "
                                    "existing password."
                                ),
                            )

                        return redirect(
                            "users:company_member_detail",
                            membership_id=membership.public_id,
                        )

                    except (
                        ValidationError,
                        PermissionDenied,
                    ) as exc:
                        _form_service_error(form, exc)

    else:
        form = MembershipApprovalForm(
            membership=membership
        )

    return render(
        request,
        "control_center/approval_review.html",
        {
            "membership": membership,
            "form": form,
        },
    )


@require_POST
@login_required
def company_member_activation_resend(
    request,
    membership_id,
):
    _platform_admin_required(request)

    membership = get_object_or_404(
        CompanyMembership.objects.select_related(
            "company",
            "user",
            "role",
        ),
        public_id=membership_id,
    )

    if not _verify_admin_password(
        request,
        request.POST.get("admin_password"),
        action="COMPANY_USER_ACTIVATION_RESEND",
        company=membership.company,
        subject_user=membership.user,
    ):
        messages.error(
            request,
            "Administrator password is incorrect.",
        )
        return redirect(
            "users:company_member_detail",
            membership_id=membership.public_id,
        )

    try:
        with transaction.atomic():
            activation, raw_token = (
                issue_company_user_activation(
                    membership=membership,
                    actor=request.user,
                )
            )

            send_company_user_activation_email(
                activation=activation,
                raw_token=raw_token,
                request=request,
                actor=request.user,
            )

        _security_success(
            request,
            action="COMPANY_USER_ACTIVATION_RESEND",
            company=membership.company,
            subject_user=membership.user,
            description=(
                "Company-user activation link rotated and resent."
            ),
            metadata={
                "membership_id": membership.pk,
                "activation_id": str(activation.public_id),
                "resend_count": activation.resend_count,
            },
        )

        messages.success(
            request,
            "Activation link imezungushwa na kutumwa tena.",
        )

    except (
        ValidationError,
        PermissionDenied,
    ) as exc:
        messages.error(
            request,
            "; ".join(
                getattr(exc, "messages", [str(exc)])
            ),
        )

    return redirect(
        "users:company_member_detail",
        membership_id=membership.public_id,
    )


@login_required
def company_member_detail(request, membership_id):
    _platform_admin_required(request)

    membership = get_object_or_404(
        CompanyMembership.objects
        .select_related(
            "company",
            "user",
            "role",
            "created_by",
            "approved_by",
        )
        .prefetch_related(
            "role__permissions__content_type",
            "granted_permissions__content_type",
            "denied_permissions__content_type",
        ),
        public_id=membership_id,
    )

    base_codes = _permission_set(
        membership.role.permissions.all()
    )
    grant_codes = _permission_set(
        membership.granted_permissions.all()
    )
    denial_codes = _permission_set(
        membership.denied_permissions.all()
    )
    effective_codes = set(
        get_effective_permission_codes(membership)
    )

    return render(
        request,
        "control_center/member_detail.html",
        {
            "membership": membership,
            "base_codes": sorted(base_codes),
            "grant_codes": sorted(grant_codes),
            "denial_codes": sorted(denial_codes),
            "effective_codes": sorted(effective_codes),
            "is_last_active_owner": (
                _owner_is_last_active_owner(membership)
            ),
            "activation": (
                CompanyUserActivation.objects
                .filter(membership=membership)
                .first()
            ),
            "activation_pending": (
                membership.status
                == CompanyMembership.Status.ACTIVE
                and not membership.user.is_active
                and not membership.user.has_usable_password()
            ),
        },
    )


@login_required
def company_member_edit(request, membership_id):
    _platform_admin_required(request)
    membership = get_object_or_404(
        CompanyMembership.objects.select_related('company', 'user', 'role'),
        public_id=membership_id,
    )
    if request.method == 'POST':
        form = MembershipEditForm(request.POST, membership=membership)
        if form.is_valid():
            if not _verify_admin_password(request, form.cleaned_data['admin_password'],
                                          action='COMPANY_MEMBER_EDIT', company=membership.company,
                                          subject_user=membership.user):
                form.add_error('admin_password', 'Administrator password is incorrect.')
            else:
                try:
                    with transaction.atomic():
                        Company.objects.select_for_update().get(pk=membership.company_id)
                        membership = CompanyMembership.objects.select_for_update().select_related(
                            'company', 'user', 'role').get(pk=membership.pk)
                        if (membership.status not in (CompanyMembership.Status.ACTIVE,
                                CompanyMembership.Status.SUSPENDED, CompanyMembership.Status.ARCHIVED)
                                or not membership.approved_at):
                            raise ValidationError('Pending or rejected requests must go through approval.')
                        form = MembershipEditForm(request.POST, membership=membership)
                        if not form.is_valid():
                            raise ValidationError('Membership details changed. Reload and try again.')
                        new_role = form.cleaned_data['role']
                        new_language = form.cleaned_data['approved_language']
                        old_role = membership.role.code
                        old_language = membership.approved_language
                        if old_role == ROLE_OWNER and new_role.code != ROLE_OWNER and _owner_is_last_active_owner(membership):
                            raise ValidationError('The last active Owner cannot be changed to another role.')
                        membership.role = new_role
                        membership.approved_language = new_language
                        membership.full_clean()
                        membership.save(update_fields=['role', 'approved_language', 'updated_at'])
                        if membership.user.preferred_language != new_language:
                            membership.user.preferred_language = new_language
                            membership.user.save(update_fields=['preferred_language'])
                        CompanyAuditEvent.objects.create(
                            company=membership.company, actor=request.user,
                            action='MEMBERSHIP_PROFILE_UPDATED', description='Membership role/language updated.',
                            metadata={'membership_id': membership.pk,
                                'before': {'role': old_role, 'language': old_language},
                                'after': {'role': new_role.code, 'language': new_language}},
                        )
                        _security_success(request, action='COMPANY_MEMBER_EDIT', company=membership.company,
                            subject_user=membership.user, description='Membership role/language updated.',
                            metadata={'membership_id': membership.pk, 'before_role': old_role,
                                      'after_role': new_role.code})
                    messages.success(request, 'Membership settings zimehifadhiwa.')
                    return redirect('users:company_member_detail', membership_id=membership.public_id)
                except ValidationError as exc:
                    form.add_error(None, '; '.join(exc.messages))
                except OperationalError:
                    form.add_error(None, 'Membership is busy. Reload before trying again.')
    else:
        form = MembershipEditForm(membership=membership)
    return render(request, 'control_center/member_edit.html', {'membership': membership, 'form': form})


@login_required
@require_POST
def membership_status_action(request, membership_id, action):
    _platform_admin_required(request)
    membership = get_object_or_404(
        CompanyMembership.objects.select_related('company', 'user', 'role'),
        public_id=membership_id,
    )
    if not _verify_admin_password(request, request.POST.get('admin_password'),
                                  action='MEMBERSHIP_STATUS_CHANGE', company=membership.company,
                                  subject_user=membership.user):
        messages.error(request, 'Membership status haijabadilishwa: administrator password si sahihi.')
        return redirect('users:company_member_detail', membership_id=membership.public_id)
    actions = {'suspend': CompanyMembership.Status.SUSPENDED,
               'activate': CompanyMembership.Status.ACTIVE,
               'archive': CompanyMembership.Status.ARCHIVED}
    target_status = actions.get(action)
    if target_status is None:
        raise PermissionDenied('Unknown membership lifecycle action.')
    try:
        with transaction.atomic():
            company = Company.objects.select_for_update().get(pk=membership.company_id)
            membership = CompanyMembership.objects.select_for_update().select_related(
                'company', 'user', 'role').get(pk=membership.pk)
            if (membership.status not in (CompanyMembership.Status.ACTIVE,
                    CompanyMembership.Status.SUSPENDED, CompanyMembership.Status.ARCHIVED)
                    or not membership.approved_at or not membership.approved_language):
                raise ValidationError('Pending or rejected requests must go through approval.')
            if target_status in (CompanyMembership.Status.SUSPENDED, CompanyMembership.Status.ARCHIVED) and _owner_is_last_active_owner(membership):
                raise ValidationError('The last active Owner cannot be suspended or archived.')
            if target_status == CompanyMembership.Status.ACTIVE and company.status != Company.Status.ACTIVE:
                raise ValidationError('A membership cannot be activated while the company is not active.')
            before = membership.status
            membership.status = target_status
            membership.full_clean()
            membership.save(update_fields=['status', 'updated_at'])
            CompanyAuditEvent.objects.create(
                company=membership.company, actor=request.user, action='MEMBERSHIP_STATUS_CHANGED',
                description=f'Membership status {before} -> {target_status}.',
                metadata={'membership_id': membership.pk, 'user_id': membership.user_id,
                          'before': before, 'after': target_status},
            )
            _security_success(request, action='MEMBERSHIP_STATUS_CHANGE', company=membership.company,
                subject_user=membership.user, description='Company membership lifecycle changed.',
                metadata={'before': before, 'after': target_status})
        messages.success(request, f'Membership status: {before} → {target_status}.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    except OperationalError:
        messages.error(request, 'Membership is busy. Reload before trying again.')
    return redirect('users:company_member_detail', membership_id=membership.public_id)


@login_required
def membership_permissions(request, membership_id):
    _platform_admin_required(request)

    membership = get_object_or_404(
        CompanyMembership.objects
        .select_related(
            "company",
            "user",
            "role",
        )
        .prefetch_related(
            "role__permissions__content_type",
            "granted_permissions__content_type",
            "denied_permissions__content_type",
        ),
        public_id=membership_id,
    )

    if request.method == "POST":
        form = MembershipOverridesForm(
            request.POST,
            membership=membership,
        )

        if form.is_valid():
            if not _verify_admin_password(
                request,
                form.cleaned_data["admin_password"],
                action="MEMBERSHIP_PERMISSION_OVERRIDE",
                company=membership.company,
                subject_user=membership.user,
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                try:
                    set_membership_permission_overrides(
                        actor=request.user,
                        membership=membership,
                        granted_codes=form.cleaned_data[
                            "granted_permissions"
                        ],
                        denied_codes=form.cleaned_data[
                            "denied_permissions"
                        ],
                        step_up_verified=True,
                    )

                    _security_success(
                        request,
                        action="MEMBERSHIP_PERMISSION_OVERRIDE",
                        company=membership.company,
                        subject_user=membership.user,
                        description=(
                            "Per-member permission overrides updated."
                        ),
                        metadata={
                            "membership_id": membership.pk,
                            "grants": sorted(
                                form.cleaned_data[
                                    "granted_permissions"
                                ]
                            ),
                            "denials": sorted(
                                form.cleaned_data[
                                    "denied_permissions"
                                ]
                            ),
                        },
                    )

                    messages.success(
                        request,
                        (
                            "Individual permission overrides "
                            "zimehifadhiwa."
                        ),
                    )

                    return redirect(
                        "users:company_member_detail",
                        membership_id=membership.public_id,
                    )

                except (ValidationError, PermissionDenied) as exc:
                    _form_service_error(form, exc)
    else:
        form = MembershipOverridesForm(
            membership=membership
        )

    return render(
        request,
        "control_center/membership_permissions.html",
        {
            "membership": membership,
            "form": form,
            "base_codes": sorted(
                _permission_set(
                    membership.role.permissions.all()
                )
            ),
            "effective_codes": sorted(
                get_effective_permission_codes(membership)
            ),
        },
    )


@login_required
def company_roles(request, company_id):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    roles = (
        company.roles
        .prefetch_related("permissions")
        .annotate(
            member_count=Count(
                "memberships",
                distinct=True,
            )
        )
        .order_by("is_reserved", "name")
    )

    return render(
        request,
        "control_center/role_list.html",
        {
            "company": company,
            "roles": roles,
        },
    )


@login_required
def role_create(request, company_id):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    if request.method == "POST":
        form = RoleCreateForm(
            request.POST,
            company=company,
        )

        if form.is_valid():
            if not _verify_admin_password(
                request,
                form.cleaned_data["admin_password"],
                action="COMPANY_ROLE_CREATE",
                company=company,
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                with transaction.atomic():
                    role = CompanyRole(
                        company=company,
                        code=form.cleaned_data["code"],
                        name=form.cleaned_data["name"],
                        description=form.cleaned_data[
                            "description"
                        ],
                        is_reserved=False,
                        is_active=True,
                    )
                    role.full_clean()
                    role.save()

                    set_company_role_permissions(
                        actor=request.user,
                        role=role,
                        permission_codes=form.cleaned_data[
                            "permissions"
                        ],
                        step_up_verified=True,
                    )

                    CompanyAuditEvent.objects.create(
                        company=company,
                        actor=request.user,
                        action="COMPANY_ROLE_CREATED",
                        description=(
                            f"Custom role {role.code} created."
                        ),
                        metadata={"role_id": role.pk},
                    )

                    _security_success(
                        request,
                        action="COMPANY_ROLE_CREATE",
                        company=company,
                        description="Custom company role created.",
                        metadata={
                            "role_id": role.pk,
                            "role_code": role.code,
                        },
                    )

                messages.success(
                    request,
                    f"Role '{role.name}' imetengenezwa.",
                )

                return redirect(
                    "users:role_permissions",
                    role_id=role.pk,
                )
    else:
        form = RoleCreateForm(company=company)

    return render(
        request,
        "control_center/role_form.html",
        {
            "company": company,
            "form": form,
        },
    )


@login_required
def role_permissions(request, role_id):
    _platform_admin_required(request)

    role = get_object_or_404(
        CompanyRole.objects
        .select_related("company")
        .prefetch_related(
            "permissions__content_type"
        ),
        pk=role_id,
    )

    if request.method == "POST":
        form = RolePermissionsForm(
            request.POST,
            role=role,
        )

        if form.is_valid():
            if not _verify_admin_password(
                request,
                form.cleaned_data["admin_password"],
                action="COMPANY_ROLE_PERMISSIONS",
                company=role.company,
            ):
                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                try:
                    set_company_role_permissions(
                        actor=request.user,
                        role=role,
                        permission_codes=form.cleaned_data[
                            "permissions"
                        ],
                        step_up_verified=True,
                    )

                    _security_success(
                        request,
                        action="COMPANY_ROLE_PERMISSIONS",
                        company=role.company,
                        description=(
                            "Company role permission template updated."
                        ),
                        metadata={
                            "role_id": role.pk,
                            "role_code": role.code,
                            "permissions": sorted(
                                form.cleaned_data["permissions"]
                            ),
                        },
                    )

                    messages.success(
                        request,
                        (
                            f"Permissions za role '{role.name}' "
                            "zimehifadhiwa."
                        ),
                    )

                    return redirect(
                        "users:company_roles",
                        company_id=role.company.public_id,
                    )

                except (ValidationError, PermissionDenied) as exc:
                    _form_service_error(form, exc)
    else:
        form = RolePermissionsForm(role=role)

    return render(
        request,
        "control_center/role_permissions.html",
        {
            "role": role,
            "company": role.company,
            "form": form,
        },
    )


@login_required
def company_audit(request, company_id):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    queryset = (
        CompanyAuditEvent.objects
        .filter(company=company)
        .select_related("actor")
    )

    query = request.GET.get("q", "").strip()

    if query:
        queryset = queryset.filter(
            Q(action__icontains=query)
            | Q(description__icontains=query)
            | Q(actor__username__icontains=query)
            | Q(actor__email__icontains=query)
        )

    paginator = Paginator(queryset, 50)
    page_obj = paginator.get_page(
        request.GET.get("page")
    )

    return render(
        request,
        "control_center/company_audit.html",
        {
            "company": company,
            "page_obj": page_obj,
            "query": query,
        },
    )


@login_required
@require_POST
def company_owner_user_management_policy(
    request,
    company_id,
):
    _platform_admin_required(request)

    company = get_object_or_404(
        Company,
        public_id=company_id,
    )

    if not _verify_admin_password(
        request,
        request.POST.get(
            "admin_password",
            "",
        ),
        action="OWNER_USER_MANAGEMENT_POLICY",
        company=company,
    ):
        messages.error(
            request,
            (
                "Owner user-management policy haijabadilishwa: "
                "administrator password si sahihi."
            ),
        )

        return redirect(
            "users:company_detail",
            company_id=company.public_id,
        )

    enabled = (
        request.POST.get("enabled")
        == "1"
    )

    before = (
        company
        .owner_user_management_enabled
    )

    company.owner_user_management_enabled = (
        enabled
    )

    company.full_clean()

    company.save(
        update_fields=[
            "owner_user_management_enabled",
            "updated_at",
        ]
    )

    CompanyAuditEvent.objects.create(
        company=company,
        actor=request.user,
        action="OWNER_USER_MANAGEMENT_POLICY_CHANGED",
        description=(
            "Platform Admin changed Owner company-user "
            "management policy."
        ),
        metadata={
            "before": before,
            "after": enabled,
        },
    )

    _security_success(
        request,
        action="OWNER_USER_MANAGEMENT_POLICY",
        company=company,
        description=(
            "Owner company-user management policy changed."
        ),
        metadata={
            "before": before,
            "after": enabled,
        },
    )

    messages.success(
        request,
        (
            "Owner user management "
            + (
                "imeruhusiwa."
                if enabled
                else "imezimwa."
            )
        ),
    )

    return redirect(
        "users:company_detail",
        company_id=company.public_id,
    )
