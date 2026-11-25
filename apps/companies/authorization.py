from django.core.exceptions import PermissionDenied

from .constants import (
    DELEGABLE_COMPANY_PERMISSION_CODENAMES,
)
from .models import CompanyMembership


def _is_local_permission(permission):
    app_label = permission.content_type.app_label

    if app_label in {"pigs", "transport"}:
        return True

    if app_label == "companies":
        return (
            permission.codename
            in DELEGABLE_COMPANY_PERMISSION_CODENAMES
        )

    return False


def _permission_codes(queryset):
    return {
        f"{permission.content_type.app_label}.{permission.codename}"
        for permission
        in queryset.select_related("content_type")
        if _is_local_permission(permission)
    }


def get_effective_permission_codes(membership):
    """
    Permission-first authorization contract.

    Effective permissions are resolved per CompanyMembership:

        role base permissions
        + explicit member grants
        - explicit member denials

    Role names never act as hidden permission bypasses. This means one
    Owner/Manager can be restricted or extended without changing the
    shared role template for other memberships.
    """

    if membership is None:
        return frozenset()

    if membership.status != CompanyMembership.Status.ACTIVE:
        return frozenset()

    if not membership.company.is_operational:
        return frozenset()

    if not membership.role.is_active:
        return frozenset()

    base = _permission_codes(
        membership.role.permissions.all()
    )

    granted = _permission_codes(
        membership.granted_permissions.all()
    )

    denied = _permission_codes(
        membership.denied_permissions.all()
    )

    return frozenset(
        (base | granted) - denied
    )


def membership_has_permission(
    *,
    membership,
    permission_code,
):
    permission_code = (
        permission_code
        or ""
    ).strip().lower()

    return (
        permission_code
        in get_effective_permission_codes(
            membership
        )
    )


def require_company_permission(
    *,
    membership,
    permission_code,
):
    if not membership_has_permission(
        membership=membership,
        permission_code=permission_code,
    ):
        raise PermissionDenied(
            "You do not have permission for this company operation."
        )

    return True