from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .authorization import (
    get_effective_permission_codes,
    membership_has_permission,
)
from .constants import (
    DELEGABLE_COMPANY_PERMISSION_CODENAMES,
    PERMISSION_MANAGE_COMPANY_ROLES,
    PERMISSION_MANAGE_COMPANY_USERS,
    PERMISSION_MANAGE_MEMBER_PERMISSIONS,
    ROLE_MANAGER,
    ROLE_OWNER,
    ROLE_STAFF,
    ROLE_VIEWER,
    SUPPORTED_LANGUAGE_CODES,
)
from .models import (
    Company,
    CompanyAuditEvent,
    CompanyMembership,
    CompanyRole,
)


from .activation_identity import has_established_sign_in
from .owner_safety import is_last_active_owner

User = get_user_model()


def require_platform_admin(actor):
    if (
        actor is None
        or not actor.is_authenticated
        or not actor.is_active
        or not getattr(actor, "is_platform_admin", False)
    ):
        raise PermissionDenied(
            "Platform Administrator authority is required."
        )


def require_step_up(step_up_verified):
    if not step_up_verified:
        raise PermissionDenied(
            "Recent password confirmation is required."
        )


def normalize_languages(values):
    normalized = []

    for value in values or []:
        code = str(value).strip().lower()

        if code not in SUPPORTED_LANGUAGE_CODES:
            raise ValidationError(
                f"Unsupported language: {value}."
            )

        if code not in normalized:
            normalized.append(code)

    if not normalized:
        raise ValidationError(
            "At least one language must be enabled."
        )

    return normalized


def _delegable_permissions():
    return (
        Permission.objects
        .filter(
            Q(content_type__app_label__in=("pigs", "transport"))
            |
            Q(
                content_type__app_label="companies",
                codename__in=(
                    DELEGABLE_COMPANY_PERMISSION_CODENAMES
                ),
            )
        )
        .select_related("content_type")
    )


def _permission_codes(permissions):
    return {
        f"{permission.content_type.app_label}.{permission.codename}"
        for permission in permissions
    }


def _permissions_from_codes(codes):
    codes = set(codes or ())

    if not codes:
        return Permission.objects.none()

    ids = []

    for permission in _delegable_permissions():
        code = (
            f"{permission.content_type.app_label}."
            f"{permission.codename}"
        )

        if code in codes:
            ids.append(permission.pk)

    return Permission.objects.filter(
        pk__in=ids
    )


def _default_role_permission_codes(role_code, business_type="pig"):
    app_label = "transport" if business_type == "transport" else "pigs"
    permissions = list(_delegable_permissions())
    business_permissions = [
        permission for permission in permissions
        if permission.content_type.app_label == app_label
    ]
    company_permissions = [
        permission for permission in permissions
        if permission.content_type.app_label == "companies"
    ]

    if role_code == ROLE_OWNER:
        return _permission_codes([*business_permissions, *company_permissions])

    if role_code == ROLE_MANAGER:
        return _permission_codes(
            permission for permission in business_permissions
            if not permission.codename.startswith("delete_")
            and permission.codename not in {
                "manage_data_management",
                "manage_transport_services",
            }
        )

    if role_code == ROLE_STAFF:
        writable_models = (
            {
                "vehicle", "route", "trip", "cargo", "cargopayment",
                "expense", "maintenance", "passengerschedule",
                "passengerbooking", "passengerpayment",
            }
            if app_label == "transport"
            else {
                "dailysale", "pigsalerecord", "meatpartsale",
                "foodsalerecord", "expense",
            }
        )
        selected = []
        for permission in business_permissions:
            codename = permission.codename
            if codename.startswith("view_"):
                selected.append(permission)
                continue
            for prefix in ("add_", "change_"):
                if codename.startswith(prefix):
                    model_code = codename[len(prefix):]
                    if model_code in writable_models:
                        selected.append(permission)
        return _permission_codes(selected)

    if role_code == ROLE_VIEWER:
        return _permission_codes(
            permission for permission in business_permissions
            if permission.codename.startswith("view_")
        )

    return set()


@transaction.atomic
def ensure_default_roles(company):
    definitions = (
        (ROLE_OWNER, "Owner", "Full company authority template."),
        (ROLE_MANAGER, "Manager", "Operational management template."),
        (ROLE_STAFF, "Staff", "Operational staff template."),
        (ROLE_VIEWER, "Viewer", "Read-oriented access template."),
    )

    roles = {}

    for code, name, description in definitions:
        role, created = CompanyRole.objects.get_or_create(
            company=company,
            code=code,
            defaults={
                "name": name,
                "description": description,
                "is_reserved": True,
                "is_active": True,
            },
        )

        if not created:
            changed = []

            if not role.is_reserved:
                role.is_reserved = True
                changed.append("is_reserved")

            if not role.is_active:
                role.is_active = True
                changed.append("is_active")

            if changed:
                changed.append("updated_at")
                role.save(
                    update_fields=changed
                )

        # Seed only newly created role templates. Existing role permissions
        # are never reset here, preserving deliberate admin customization.
        if created:
            role.permissions.set(
                _permissions_from_codes(
                    _default_role_permission_codes(
                        code,
                        company.business_type,
                    )
                )
            )

        roles[code] = role

    return roles


def _active_actor_membership(
    *,
    actor,
    company,
):
    return (
        CompanyMembership.objects
        .active()
        .select_related(
            "company",
            "role",
            "user",
        )
        .prefetch_related(
            "role__permissions__content_type",
            "granted_permissions__content_type",
            "denied_permissions__content_type",
        )
        .filter(
            user=actor,
            company=company,
        )
        .first()
    )


def actor_has_company_permission(
    *,
    actor,
    company,
    permission_code,
):
    if (
        actor is not None
        and actor.is_authenticated
        and actor.is_active
        and getattr(
            actor,
            "is_platform_admin",
            False,
        )
    ):
        return True

    membership = _active_actor_membership(
        actor=actor,
        company=company,
    )

    return membership_has_permission(
        membership=membership,
        permission_code=permission_code,
    )


def require_actor_company_permission(
    *,
    actor,
    company,
    permission_code,
):
    if not actor_has_company_permission(
        actor=actor,
        company=company,
        permission_code=permission_code,
    ):
        raise PermissionDenied(
            "You do not have the required company permission."
        )


@transaction.atomic
def create_company(
    *,
    actor,
    name,
    code,
    owner,
    allowed_languages,
    default_language,
    step_up_verified,
    email="",
    phone="",
    address="",
    business_type="pig",
    transport_cargo_enabled=True,
    transport_passenger_enabled=False,
):
    require_platform_admin(actor)
    require_step_up(step_up_verified)

    # A previously disabled global account must be explicitly reactivated.
    # Refresh and lock it inside the company creation transaction.
    owner = User.objects.select_for_update().get(pk=owner.pk)
    if not owner.is_active:
        raise ValidationError("This account is inactive. Reactivate it before adding it as a company owner.")

    allowed_languages = normalize_languages(
        allowed_languages
    )

    default_language = (
        default_language or ""
    ).strip().lower()

    if default_language not in allowed_languages:
        raise ValidationError(
            "Default language must be enabled for the company."
        )

    company = Company(
        name=(name or "").strip(),
        code=(code or "").strip().lower(),
        email=(email or "").strip(),
        phone=(phone or "").strip(),
        address=(address or "").strip(),
        allowed_languages=allowed_languages,
        default_language=default_language,
        business_type=business_type,
        created_by=actor,
    )

    company.full_clean()
    company.save()

    roles = ensure_default_roles(company)

    if company.business_type == Company.BusinessType.TRANSPORT:
        from apps.transport.models import TransportProfile

        TransportProfile.objects.create(
            company=company,
            cargo_enabled=bool(transport_cargo_enabled),
            passenger_enabled=bool(transport_passenger_enabled),
        )

    membership = CompanyMembership(
        company=company,
        user=owner,
        role=roles[ROLE_OWNER],
        status=CompanyMembership.Status.ACTIVE,
        requested_language=default_language,
        approved_language=default_language,
        created_by=actor,
        approved_by=actor,
        approved_at=timezone.now(),
    )

    membership.full_clean()
    membership.save()

    CompanyAuditEvent.objects.create(
        company=company,
        actor=actor,
        action="COMPANY_CREATED",
        description=(
            f"Company created with owner {owner.username}."
        ),
        metadata={
            "owner_user_id": owner.pk,
            "business_type": company.business_type,
            "transport_cargo_enabled": bool(transport_cargo_enabled) if company.business_type == Company.BusinessType.TRANSPORT else False,
            "transport_passenger_enabled": bool(transport_passenger_enabled) if company.business_type == Company.BusinessType.TRANSPORT else False,
            "allowed_languages": allowed_languages,
            "default_language": default_language,
        },
    )

    return company


@transaction.atomic
def request_company_user(
    *,
    actor,
    company,
    role,
    username,
    email,
    requested_language,
    first_name="",
    last_name="",
):
    require_actor_company_permission(
        actor=actor,
        company=company,
        permission_code=(
            "companies."
            f"{PERMISSION_MANAGE_COMPANY_USERS}"
        ),
    )

    if not getattr(
        actor,
        "is_platform_admin",
        False,
    ):
        actor_membership = _active_actor_membership(
            actor=actor,
            company=company,
        )

        if (
            actor_membership.role.code == ROLE_OWNER
            and not company.owner_user_management_enabled
        ):
            raise PermissionDenied(
                "Owner user management is disabled for this company."
            )

    if company.status != Company.Status.ACTIVE:
        raise ValidationError(
            "Users cannot be added to a non-active company."
        )

    if role.company_id != company.id or not role.is_active:
        raise ValidationError(
            "Selected role is invalid for this company."
        )

    # Creating another Owner is a governance-level action. A local Owner
    # may create Manager/Staff/custom users, while Platform Admin confirms
    # Owner-level authority.
    if (
        role.code == ROLE_OWNER
        and not getattr(
            actor,
            "is_platform_admin",
            False,
        )
    ):
        raise PermissionDenied(
            "Only Platform Administrator can assign the Owner role."
        )

    if not getattr(
        actor,
        "is_platform_admin",
        False,
    ):
        actor_membership = _active_actor_membership(
            actor=actor,
            company=company,
        )

        actor_permissions = (
            get_effective_permission_codes(
                actor_membership
            )
        )

        target_role_permissions = _permission_codes(
            role.permissions.all()
        )

        if not target_role_permissions.issubset(
            actor_permissions
        ):
            raise PermissionDenied(
                "You cannot assign a role containing permissions "
                "that you do not hold."
            )

    requested_language = (
        requested_language or ""
    ).strip().lower()

    if not company.language_is_allowed(
        requested_language
    ):
        raise ValidationError(
            "Selected language is not enabled for this company."
        )

    email = (email or "").strip().lower()
    username = (username or "").strip()

    if not email:
        raise ValidationError(
            "Email is required."
        )

    user = User.objects.filter(
        email__iexact=email,
    ).first()

    created = False

    if user is None:
        user = User(
            username=username,
            email=email,
            first_name=(first_name or "").strip(),
            last_name=(last_name or "").strip(),
            preferred_language=requested_language,
            is_active=False,
        )
        user.set_unusable_password()
        user.full_clean()
        user.save()
        created = True

    elif username and user.username != username:
        raise ValidationError(
            "This email already belongs to another username."
        )

    if CompanyMembership.objects.filter(
        company=company,
        user=user,
    ).exists():
        raise ValidationError(
            "This user already belongs to this company."
        )

    membership = CompanyMembership(
        company=company,
        user=user,
        role=role,
        status=CompanyMembership.Status.PENDING_APPROVAL,
        requested_language=requested_language,
        approved_language="",
        created_by=actor,
    )

    membership.full_clean()
    membership.save()

    CompanyAuditEvent.objects.create(
        company=company,
        actor=actor,
        action="COMPANY_USER_REQUESTED",
        description=(
            f"{user.username} submitted for approval."
        ),
        metadata={
            "membership_id": membership.pk,
            "user_id": user.pk,
            "role_code": role.code,
            "requested_language": requested_language,
            "new_identity": created,
        },
    )

    return membership


@transaction.atomic
def approve_company_membership(
    *,
    actor,
    membership,
    approved_language,
    step_up_verified,
):
    require_platform_admin(actor)
    require_step_up(step_up_verified)

    membership = (
        CompanyMembership.objects
        .select_for_update()
        .select_related(
            "company",
            "user",
            "role",
        )
        .get(pk=membership.pk)
    )

    # Refresh and lock the global identity before deciding whether activation
    # is permitted; SQLite serializes writes, while row-locking DBs lock it.
    membership.user = User.objects.select_for_update().get(pk=membership.user_id)

    if (
        membership.status
        != CompanyMembership.Status.PENDING_APPROVAL
    ):
        raise ValidationError(
            "Only pending memberships can be approved."
        )

    approved_language = (
        approved_language
        or membership.requested_language
    ).strip().lower()

    if not membership.company.language_is_allowed(
        approved_language
    ):
        raise ValidationError(
            "Approved language is not enabled for this company."
        )

    # A pending membership must never silently reactivate an existing global
    # identity. Newly created identities have an unusable password and follow
    # the separate, token-based activation flow.
    if not membership.user.is_active and has_established_sign_in(membership.user):
        raise ValidationError(
            "This user account is disabled. A platform administrator must "
            "review and reactivate it before approving this membership."
        )

    membership.approved_language = approved_language
    membership.status = CompanyMembership.Status.ACTIVE
    membership.approved_by = actor
    membership.approved_at = timezone.now()
    membership.full_clean()
    membership.save(
        update_fields=[
            "approved_language",
            "status",
            "approved_by",
            "approved_at",
            "updated_at",
        ]
    )

    user = membership.user
    changed = []

    activation_required = not has_established_sign_in(user)

    if activation_required:
        if user.is_active:
            user.is_active = False
            changed.append("is_active")

    if user.preferred_language != approved_language:
        user.preferred_language = approved_language
        changed.append(
            "preferred_language"
        )

    if changed:
        user.save(
            update_fields=changed
        )

    CompanyAuditEvent.objects.create(
        company=membership.company,
        actor=actor,
        action="COMPANY_USER_APPROVED",
        description=(
            f"{user.username} approved."
        ),
        metadata={
            "membership_id": membership.pk,
            "role_code": membership.role.code,
            "requested_language":
                membership.requested_language,
            "approved_language":
                approved_language,
            "activation_required":
                activation_required,
        },
    )

    return membership


@transaction.atomic
def set_company_language_policy(
    *,
    actor,
    company,
    allowed_languages,
    default_language,
    step_up_verified,
):
    require_platform_admin(actor)
    require_step_up(step_up_verified)

    allowed_languages = normalize_languages(
        allowed_languages
    )

    default_language = (
        default_language or ""
    ).strip().lower()

    if default_language not in allowed_languages:
        raise ValidationError(
            "Default language must be enabled for the company."
        )

    incompatible = (
        CompanyMembership.objects
        .filter(
            company=company,
            status=CompanyMembership.Status.ACTIVE,
        )
        .exclude(
            approved_language__in=allowed_languages,
        )
        .exists()
    )

    if incompatible:
        raise ValidationError(
            "An active user currently uses a language "
            "that would be disabled."
        )

    before = {
        "allowed_languages":
            list(company.allowed_languages or []),
        "default_language":
            company.default_language,
    }

    company.allowed_languages = (
        allowed_languages
    )
    company.default_language = (
        default_language
    )
    company.full_clean()
    company.save(
        update_fields=[
            "allowed_languages",
            "default_language",
            "updated_at",
        ]
    )

    CompanyAuditEvent.objects.create(
        company=company,
        actor=actor,
        action="LANGUAGE_POLICY_UPDATED",
        description=(
            "Company language policy updated."
        ),
        metadata={
            "before": before,
            "after": {
                "allowed_languages":
                    allowed_languages,
                "default_language":
                    default_language,
            },
        },
    )

    return company


@transaction.atomic
def set_company_status(
    *,
    actor,
    company,
    status,
    step_up_verified,
):
    require_platform_admin(actor)
    require_step_up(step_up_verified)

    allowed = {
        Company.Status.ACTIVE,
        Company.Status.SUSPENDED,
        Company.Status.ARCHIVED,
    }

    if status not in allowed:
        raise ValidationError(
            "Invalid company status."
        )

    previous = company.status
    company.status = status

    if status == Company.Status.SUSPENDED:
        company.suspended_at = timezone.now()
        company.archived_at = None

    elif status == Company.Status.ARCHIVED:
        company.archived_at = timezone.now()
        company.suspended_at = None

    elif status == Company.Status.ACTIVE:
        company.suspended_at = None
        company.archived_at = None

    company.full_clean()
    company.save()

    CompanyAuditEvent.objects.create(
        company=company,
        actor=actor,
        action="COMPANY_STATUS_CHANGED",
        description=(
            f"Company status changed "
            f"{previous} -> {status}."
        ),
        metadata={
            "before": previous,
            "after": status,
        },
    )

    return company


@transaction.atomic
def set_company_role_permissions(
    *,
    actor,
    role,
    permission_codes,
    step_up_verified,
):
    require_step_up(step_up_verified)

    require_actor_company_permission(
        actor=actor,
        company=role.company,
        permission_code=(
            "companies."
            f"{PERMISSION_MANAGE_COMPANY_ROLES}"
        ),
    )

    if (
        role.is_reserved
        and not getattr(
            actor,
            "is_platform_admin",
            False,
        )
    ):
        raise PermissionDenied(
            "Reserved role templates can only be changed "
            "by Platform Administrator."
        )

    permissions = _permissions_from_codes(
        permission_codes
    )

    requested_codes = _permission_codes(
        permissions
    )

    if set(permission_codes or ()) != requested_codes:
        raise ValidationError(
            "One or more requested permissions are not delegable "
            "inside a company."
        )

    if not getattr(actor, "is_platform_admin", False):
        actor_membership = _active_actor_membership(
            actor=actor,
            company=role.company,
        )
        actor_permissions = get_effective_permission_codes(
            actor_membership
        )
        if not requested_codes.issubset(actor_permissions):
            raise PermissionDenied(
                "You cannot add a role permission that you do not hold."
            )

    before = _permission_codes(
        role.permissions.all()
    )

    role.permissions.set(
        permissions
    )

    CompanyAuditEvent.objects.create(
        company=role.company,
        actor=actor,
        action="COMPANY_ROLE_PERMISSIONS_CHANGED",
        description=(
            f"Role {role.code} permission template changed."
        ),
        metadata={
            "role_id": role.pk,
            "before": sorted(before),
            "after": sorted(requested_codes),
        },
    )

    return role


@transaction.atomic
def set_membership_permission_overrides(
    *,
    actor,
    membership,
    granted_codes,
    denied_codes,
    step_up_verified,
):
    require_step_up(step_up_verified)

    require_actor_company_permission(
        actor=actor,
        company=membership.company,
        permission_code=(
            "companies."
            f"{PERMISSION_MANAGE_MEMBER_PERMISSIONS}"
        ),
    )

    if (
        membership.role.code == ROLE_OWNER
        and not getattr(
            actor,
            "is_platform_admin",
            False,
        )
    ):
        raise PermissionDenied(
            "Only Platform Administrator can change "
            "an Owner membership permission override."
        )

    granted_codes = set(
        granted_codes or ()
    )
    denied_codes = set(
        denied_codes or ()
    )

    overlap = granted_codes & denied_codes

    if overlap:
        raise ValidationError(
            "A permission cannot be both granted and denied: "
            f"{sorted(overlap)}"
        )

    granted = _permissions_from_codes(
        granted_codes
    )
    denied = _permissions_from_codes(
        denied_codes
    )

    if _permission_codes(granted) != granted_codes:
        raise ValidationError(
            "One or more grant permissions are invalid."
        )

    if _permission_codes(denied) != denied_codes:
        raise ValidationError(
            "One or more denied permissions are invalid."
        )

    if not getattr(
        actor,
        "is_platform_admin",
        False,
    ):
        actor_membership = _active_actor_membership(
            actor=actor,
            company=membership.company,
        )

        actor_permissions = (
            get_effective_permission_codes(
                actor_membership
            )
        )

        if not granted_codes.issubset(
            actor_permissions
        ):
            raise PermissionDenied(
                "You cannot grant a permission you do not hold."
            )

    before = {
        "granted": sorted(
            _permission_codes(
                membership.granted_permissions.all()
            )
        ),
        "denied": sorted(
            _permission_codes(
                membership.denied_permissions.all()
            )
        ),
    }

    membership.granted_permissions.set(
        granted
    )
    membership.denied_permissions.set(
        denied
    )

    CompanyAuditEvent.objects.create(
        company=membership.company,
        actor=actor,
        action="MEMBER_PERMISSION_OVERRIDES_CHANGED",
        description=(
            f"Permission overrides changed for "
            f"{membership.user.username}."
        ),
        metadata={
            "membership_id": membership.pk,
            "before": before,
            "after": {
                "granted": sorted(
                    granted_codes
                ),
                "denied": sorted(
                    denied_codes
                ),
            },
        },
    )

    return membership

@transaction.atomic
def archive_company_membership(
    *,
    actor,
    membership,
):
    # Align with platform membership actions: lock the company first.
    Company.objects.select_for_update().get(pk=membership.company_id)
    membership = (
        CompanyMembership.objects
        .select_for_update()
        .select_related(
            "company",
            "user",
            "role",
        )
        .get(pk=membership.pk)
    )

    company = membership.company

    require_actor_company_permission(
        actor=actor,
        company=company,
        permission_code=(
            "companies."
            f"{PERMISSION_MANAGE_COMPANY_USERS}"
        ),
    )

    is_platform_admin = bool(
        getattr(
            actor,
            "is_platform_admin",
            False,
        )
    )

    if not is_platform_admin:
        actor_membership = _active_actor_membership(
            actor=actor,
            company=company,
        )

        if actor_membership.role.code != ROLE_OWNER:
            raise PermissionDenied(
                "Only the Company Owner can remove company users."
            )

        if not company.owner_user_management_enabled:
            raise PermissionDenied(
                "Owner user management is disabled for this company."
            )

        if membership.user_id == actor.pk:
            raise PermissionDenied(
                "Owner cannot remove their own company membership."
            )

        if membership.role.code == ROLE_OWNER:
            raise PermissionDenied(
                "Owner-level memberships are managed by Platform Admin."
            )

    if is_last_active_owner(membership):
        raise ValidationError("The last active Owner cannot be archived.")

    previous = membership.status

    membership.status = (
        CompanyMembership.Status.ARCHIVED
    )

    membership.full_clean()
    membership.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    CompanyAuditEvent.objects.create(
        company=company,
        actor=actor,
        action="COMPANY_USER_REMOVED",
        description=(
            f"{membership.user.username} removed from company. "
            "Global identity preserved."
        ),
        metadata={
            "membership_id": membership.pk,
            "user_id": membership.user_id,
            "previous_status": previous,
            "new_status": membership.status,
        },
    )

    return membership
