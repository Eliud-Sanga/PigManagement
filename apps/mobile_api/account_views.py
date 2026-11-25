"""Mobile account/team adapters for the existing company governance services.

No company/user id supplied in a form can select an account or grant access.
Identity and company edits retain the web forms' password confirmation rules.
"""

from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.companies.authorization import get_effective_permission_codes
from apps.companies.constants import (
    DELEGABLE_COMPANY_PERMISSION_CODENAMES,
    ROLE_OWNER,
)
from apps.companies.forms_user_management import CompanyUserRequestForm
from apps.companies.models import Company, CompanyAuditEvent, CompanyMembership, CompanyRole
from apps.companies.services import (
    archive_company_membership,
    request_company_user,
    set_membership_permission_overrides,
)
from apps.companies.user_management_views import (
    _require_owner_user_management,
    _security_event,
)
from apps.control_center.forms import SecurePasswordChangeForm
from apps.control_center.models import AccountLoginThrottle, SecurityAuditEvent
from apps.control_center.services import append_security_event
from apps.users.forms_profile import OwnerCompanySettingsForm, PersonalSettingsForm
from apps.users.security import clear_step_up

from .operation_views import _error, _exception_message
from .views import _json
from .workspace_views import _business_context, _positive_int


User = get_user_model()
PROFILE_FIELDS = (
    "username", "email", "phone", "first_name", "last_name",
    "preferred_language", "theme_preference",
)
COMPANY_FIELDS = ("name", "email", "phone", "address")


def _values(instance, fields):
    return {field: getattr(instance, field) for field in fields}


def _form_error(request, form):
    return _error(
        request, "validation_error", "Check the highlighted fields.", 400,
        fields={field: [str(value) for value in errors]
                for field, errors in form.errors.items()},
    )


def _can_manage(request):
    try:
        _require_owner_user_management(request)
        return True
    except PermissionDenied:
        return False


def _team_context(request):
    company, membership, codes, error = _business_context(request)
    if error is not None:
        return company, codes, error
    try:
        _require_owner_user_management(request)
    except PermissionDenied as exc:
        return company, codes, _error(request, "team_management_denied", str(exc), 403)
    return company, codes, None


def _member_payload(member, actor, actor_codes=frozenset()):
    role_codes = {
        f"{p.content_type.app_label}.{p.codename}"
        for p in member.role.permissions.all()
    }
    grants = {
        f"{p.content_type.app_label}.{p.codename}"
        for p in member.granted_permissions.all()
    }
    denials = {
        f"{p.content_type.app_label}.{p.codename}"
        for p in member.denied_permissions.all()
    }
    effective = get_effective_permission_codes(member)
    editable = (
        member.user_id != actor.pk
        and member.role.code != ROLE_OWNER
        and member.status == CompanyMembership.Status.ACTIVE
        and not member.user.is_platform_admin
        and effective.issubset(actor_codes)
    )
    return {
        "id": str(member.public_id),
        "username": member.user.username,
        "email": member.user.email,
        "first_name": member.user.first_name,
        "last_name": member.user.last_name,
        "role": member.role.name,
        "roleCode": member.role.code,
        "roleId": str(member.role_id),
        "status": member.status,
        "language": member.approved_language or member.requested_language,
        "rolePermissions": sorted(role_codes),
        "grantedPermissions": sorted(grants),
        "deniedPermissions": sorted(denials),
        "effectivePermissions": sorted(effective),
        "canEditRole": editable and role_codes.issubset(actor_codes),
        "canEditPermissions": (
            editable and role_codes.issubset(actor_codes)
            and "companies.manage_member_permissions" in actor_codes
        ),
        "canArchive": (
            member.user_id != actor.pk and member.role.code != ROLE_OWNER
            and member.status != CompanyMembership.Status.ARCHIVED
        ),
    }


@never_cache
@require_GET
def overview(request):
    company, membership, codes, error = _business_context(request)
    if error is not None:
        return error
    can_manage = _can_manage(request)
    roles = []
    if can_manage:
        for role in company.roles.filter(is_active=True).exclude(code=ROLE_OWNER).prefetch_related("permissions__content_type").order_by("name"):
            required = {f"{p.content_type.app_label}.{p.codename}" for p in role.permissions.all()}
            if required.issubset(codes):
                roles.append({
                    "id": str(role.pk), "name": role.name, "code": role.code,
                    "permissions": sorted(required),
                })
    permissions = []
    if can_manage and "companies.manage_member_permissions" in codes:
        permissions = [
            {"code": f"{p.content_type.app_label}.{p.codename}", "name": p.name}
            for p in Permission.objects.filter(
                Q(content_type__app_label__in=("pigs", "transport"))
                | Q(content_type__app_label="companies",
                    codename__in=DELEGABLE_COMPANY_PERMISSION_CODENAMES)
            ).select_related("content_type").order_by(
                "content_type__app_label", "content_type__model", "codename"
            )
            if f"{p.content_type.app_label}.{p.codename}" in codes
        ]
    return _json(request, {
        "ok": True,
        "profile": _values(request.user, PROFILE_FIELDS),
        "company": {"id": str(company.public_id), "code": company.code,
                    **_values(company, COMPANY_FIELDS)},
        "languages": list(company.allowed_languages),
        "roles": roles,
        "permissions": permissions,
        "capabilities": {
            "editCompany": membership.role.code == ROLE_OWNER,
            "manageUsers": can_manage,
            "manageMemberPermissions": (
                can_manage and "companies.manage_member_permissions" in codes
            ),
            "confirmPassword": request.user.has_usable_password(),
        },
    })


@never_cache
@require_POST
@csrf_protect
def profile_update(request):
    company, _membership, _codes, error = _business_context(request)
    if error is not None:
        return error
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            # Partial updates retain omitted fields and never touch privilege flags.
            data = {**_values(user, PROFILE_FIELDS), **{
                key: request.POST[key] for key in PROFILE_FIELDS if key in request.POST
            }, "current_password": request.POST.get("current_password", "")}
            form = PersonalSettingsForm(data, instance=user, actor=user, company=company)
            if not form.is_valid():
                return _form_error(request, form)
            form.save()
            CompanyAuditEvent.objects.create(
                company=company, actor=user, action="USER_SELF_PROFILE_UPDATED",
                description="Mobile profile updated after password confirmation.",
                metadata={"fields": [key for key in PROFILE_FIELDS if key in request.POST]},
            )
    except IntegrityError:
        return _error(request, "identity_conflict", "Username or email is already in use.", 409)
    return _json(request, {"ok": True, "profile": _values(user, PROFILE_FIELDS)})


@never_cache
@require_POST
@csrf_protect
def company_update(request):
    company, membership, _codes, error = _business_context(request)
    if error is not None:
        return error
    if membership.role.code != ROLE_OWNER:
        return _error(request, "owner_required", "Only the Company Owner can edit company details.", 403)
    with transaction.atomic():
        company = Company.objects.select_for_update().get(pk=company.pk)
        data = {**_values(company, COMPANY_FIELDS), **{
            key: request.POST[key] for key in COMPANY_FIELDS if key in request.POST
        }, "current_password": request.POST.get("current_password", "")}
        form = OwnerCompanySettingsForm(data, instance=company, actor=request.user)
        if not form.is_valid():
            return _form_error(request, form)
        form.save()
        CompanyAuditEvent.objects.create(
            company=company, actor=request.user, action="OWNER_COMPANY_PROFILE_UPDATED",
            description="Mobile company details updated after password confirmation.",
            metadata={"fields": [key for key in COMPANY_FIELDS if key in request.POST]},
        )
    return _json(request, {"ok": True, "company": _values(company, COMPANY_FIELDS)})


@never_cache
@require_POST
@csrf_protect
def password_change(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return _error(request, "authentication_required", "Sign in before changing your password.", 401)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        form = SecurePasswordChangeForm(request.POST, actor=user, target=user)
        if not form.is_valid():
            return _form_error(request, form)
        user.set_password(form.cleaned_data["new_password1"])
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password", "must_change_password", "password_changed_at", "updated_at"])
        AccountLoginThrottle.objects.filter(user=user).update(
            failed_attempts=0, locked_until=None, last_failed_at=None,
        )
        append_security_event(
            request=request, category=SecurityAuditEvent.Category.IDENTITY,
            action="PASSWORD_CHANGE", result=SecurityAuditEvent.Result.SUCCESS,
            severity=SecurityAuditEvent.Severity.HIGH, actor=user, subject_user=user,
            reason_code="SELF_PASSWORD_CHANGE",
            description="Mobile password changed after current password confirmation.",
        )
        update_session_auth_hash(request, user)
        clear_step_up(request)
    return _json(request, {"ok": True, "message": "Password changed. Other sessions must sign in again."})


@never_cache
@require_GET
def team(request):
    company, codes, error = _team_context(request)
    if error is not None:
        return error
    members = CompanyMembership.objects.filter(company=company).select_related(
        "user", "role"
    ).prefetch_related(
        "role__permissions__content_type",
        "granted_permissions__content_type",
        "denied_permissions__content_type",
    )
    query = request.GET.get("q", "").strip()[:150]
    if query:
        members = members.filter(Q(user__username__icontains=query) | Q(user__email__icontains=query))
    paginator = Paginator(members.order_by("user__username", "pk"), 25)
    page = paginator.get_page(_positive_int(request, "page", 1, 100000))
    return _json(request, {
        "ok": True, "page": page.number, "count": paginator.count,
        "hasNext": page.has_next(),
        "items": [_member_payload(m, request.user, codes) for m in page],
    })


def _team_member(company, membership_id):
    return CompanyMembership.objects.filter(
        company=company, public_id=membership_id
    ).select_related("company", "user", "role").prefetch_related(
        "role__permissions__content_type",
        "granted_permissions__content_type",
        "denied_permissions__content_type",
    ).first()


def _editable_member(request, company, codes, membership_id):
    member = _team_member(company, membership_id)
    if member is None:
        return None, _error(request, "membership_not_found", "Membership not found.", 404)
    if not _member_payload(member, request.user, codes)["canEditRole"]:
        return None, _error(
            request, "membership_edit_denied",
            "Only an active non-Owner member within your authority can be changed.", 403
        )
    return member, None


def _locked_team_codes(request, company):
    """Recheck policy and authority under the same transaction as a write."""
    company = Company.objects.select_for_update().get(pk=company.pk)
    actor = CompanyMembership.objects.select_for_update().select_related(
        "company", "role"
    ).filter(company=company, user=request.user).first()
    if (company.status != Company.Status.ACTIVE
            or not company.owner_user_management_enabled
            or actor is None or actor.status != CompanyMembership.Status.ACTIVE
            or not actor.role.is_active or actor.role.code != ROLE_OWNER):
        raise PermissionDenied("Company Owner user management is unavailable.")
    codes = get_effective_permission_codes(actor)
    if "companies.manage_company_users" not in codes:
        raise PermissionDenied("Company user management permission is required.")
    return codes


@never_cache
@require_GET
def team_member(request, membership_id):
    company, codes, error = _team_context(request)
    if error is not None:
        return error
    member = _team_member(company, membership_id)
    if member is None:
        return _error(request, "membership_not_found", "Membership not found.", 404)
    return _json(request, {"ok": True, "member": _member_payload(member, request.user, codes)})


def _confirmed_password(request):
    if request.user.check_password(request.POST.get("current_password", "")):
        return None
    return _error(
        request, "validation_error", "Current password is incorrect.", 400,
        fields={"current_password": ["Current password is incorrect."]},
    )


@never_cache
@require_POST
@csrf_protect
def team_role_update(request, membership_id):
    company, codes, error = _team_context(request)
    if error is not None:
        return error
    error = _confirmed_password(request)
    if error is not None:
        return error
    try:
        role_id = int(request.POST.get("role", ""))
    except (TypeError, ValueError):
        return _error(request, "validation_error", "Select a valid role.", 400,
                      fields={"role": ["Select a valid role."]})
    try:
        with transaction.atomic():
            codes = _locked_team_codes(request, company)
            member = CompanyMembership.objects.select_for_update().filter(
                company=company, public_id=membership_id
            ).first()
            if member is None:
                return _error(request, "membership_not_found", "Membership not found.", 404)
            member = _team_member(company, membership_id)
            if not _member_payload(member, request.user, codes)["canEditRole"]:
                return _error(request, "membership_edit_denied", "Member cannot be changed.", 403)
            role = CompanyRole.objects.filter(
                pk=role_id, company=company, is_active=True
            ).exclude(code=ROLE_OWNER).prefetch_related("permissions__content_type").first()
            if role is None:
                return _error(request, "validation_error", "Select a company role.", 400,
                              fields={"role": ["Select a company role."]})
            required = {
                f"{p.content_type.app_label}.{p.codename}" for p in role.permissions.all()
            }
            if not required.issubset(codes):
                return _error(request, "permission_denied",
                              "Cannot assign a role exceeding your permissions.", 403)
            before = member.role.code
            member.role = role
            member.full_clean()
            member.save(update_fields=["role", "updated_at"])
            CompanyAuditEvent.objects.create(
                company=company, actor=request.user, action="OWNER_MEMBER_ROLE_CHANGED",
                description="Company Owner changed a member role on mobile.",
                metadata={"membership_id": member.pk, "before": before, "after": role.code},
            )
            _security_event(
                request=request, action="OWNER_MEMBER_ROLE_CHANGED",
                subject_user=member.user, description="Member role changed on mobile.",
            )
    except PermissionDenied as exc:
        return _error(request, "permission_denied", str(exc), 403)
    except (ValidationError, IntegrityError) as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)
    return _json(request, {"ok": True, "member": _member_payload(member, request.user, codes)})


def _posted_codes(request, field):
    raw = request.POST.get(field, "")
    if len(raw) > 12000:
        raise ValidationError("Too many permissions.")
    codes = [item.strip() for item in raw.split(",") if item.strip()]
    if len(codes) > 250 or len(codes) != len(set(codes)):
        raise ValidationError("Invalid permission selection.")
    return set(codes)


@never_cache
@require_POST
@csrf_protect
def team_permissions_update(request, membership_id):
    company, codes, error = _team_context(request)
    if error is not None:
        return error
    if "companies.manage_member_permissions" not in codes:
        return _error(request, "permission_denied",
                      "Member permission management is not allowed.", 403)
    error = _confirmed_password(request)
    if error is not None:
        return error
    try:
        grants = _posted_codes(request, "granted_permissions")
        denials = _posted_codes(request, "denied_permissions")
        if not grants.issubset(codes):
            return _error(request, "permission_denied",
                          "Cannot grant a permission you do not hold.", 403)
        with transaction.atomic():
            codes = _locked_team_codes(request, company)
            if "companies.manage_member_permissions" not in codes:
                raise PermissionDenied("Member permission management is not allowed.")
            if not grants.issubset(codes):
                raise PermissionDenied("Cannot grant a permission you do not hold.")
            locked = CompanyMembership.objects.select_for_update().filter(
                company=company, public_id=membership_id
            ).first()
            if locked is None:
                return _error(request, "membership_not_found", "Membership not found.", 404)
            member, error = _editable_member(request, company, codes, membership_id)
            if error is not None:
                return error
            member = set_membership_permission_overrides(
                actor=request.user, membership=member,
                granted_codes=grants, denied_codes=denials,
                step_up_verified=True,
            )
            _security_event(
                request=request, action="OWNER_MEMBER_PERMISSIONS_CHANGED",
                subject_user=member.user, description="Member permission overrides changed on mobile.",
            )
    except PermissionDenied as exc:
        return _error(request, "permission_denied", str(exc), 403)
    except ValidationError as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)
    member = _team_member(company, membership_id)
    return _json(request, {"ok": True, "member": _member_payload(member, request.user, codes)})


@never_cache
@require_POST
@csrf_protect
def team_request(request):
    company, _codes, error = _team_context(request)
    if error is not None:
        return error
    form = CompanyUserRequestForm(request.POST, company=company)
    if not form.is_valid():
        return _form_error(request, form)
    if not request.user.check_password(form.cleaned_data["current_password"]):
        form.add_error("current_password", "Current password is incorrect.")
        return _form_error(request, form)
    if form.existing_user is not None and form.existing_user.is_platform_admin:
        return _error(request, "platform_admin_control_plane",
                      "Platform Administrator identities cannot be added as company users.", 403)
    try:
        with transaction.atomic():
            fields = form.cleaned_data
            member = request_company_user(
                actor=request.user, company=company, role=fields["role"],
                username=fields["username"], email=fields["email"],
                requested_language=fields["requested_language"],
                first_name=fields["first_name"], last_name=fields["last_name"],
            )
            if form.existing_user is None:
                member.user.phone = fields["phone"]
                member.user.save(update_fields=["phone", "updated_at"])
            _security_event(request=request, action="OWNER_USER_CREATE", subject_user=member.user,
                            description="Mobile Company Owner submitted a pending user request.")
    except PermissionDenied as exc:
        return _error(request, "permission_denied", str(exc), 403)
    except ValidationError as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)
    except IntegrityError:
        return _error(request, "membership_conflict", "This identity or membership already exists. Refresh and check.", 409)
    return _json(request, {"ok": True, "member": _member_payload(member, request.user),
                           "message": "Request saved. Platform Admin approval and activation are required."}, status_code=201)


@never_cache
@require_POST
@csrf_protect
def team_archive(request, membership_id):
    company, _codes, error = _team_context(request)
    if error is not None:
        return error
    member = CompanyMembership.objects.filter(company=company, public_id=membership_id).select_related("user", "role").first()
    if member is None:
        return _error(request, "membership_not_found", "Membership not found.", 404)
    if not request.user.check_password(request.POST.get("current_password", "")):
        return _error(request, "validation_error", "Current password is incorrect.", 400,
                      fields={"current_password": ["Current password is incorrect."]})
    try:
        with transaction.atomic():
            member = archive_company_membership(actor=request.user, membership=member)
            _security_event(request=request, action="OWNER_USER_REMOVE", subject_user=member.user,
                            description="Mobile company membership archived; global identity preserved.")
    except PermissionDenied as exc:
        return _error(request, "permission_denied", str(exc), 403)
    except ValidationError as exc:
        return _error(request, "validation_error", _exception_message(exc), 400)
    return _json(request, {"ok": True, "member": _member_payload(member, request.user)})
