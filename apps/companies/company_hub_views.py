from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .authorization import (
    get_effective_permission_codes,
    membership_has_permission,
)
from .constants import (
    PERMISSION_MANAGE_COMPANY_ROLES,
    PERMISSION_MANAGE_COMPANY_USERS,
    PERMISSION_MANAGE_MEMBER_PERMISSIONS,
    ROLE_OWNER,
)
from .models import Company, CompanyMembership


@login_required
def company_hub(request):
    """
    Business-plane Company hub.

    Platform Admin stays in the control plane.
    Normal users see only the company already resolved by
    CompanyContextMiddleware.

    Owner-only actions remain governed by:
      - active company membership;
      - effective company permissions;
      - owner_user_management_enabled for delegated user management.
    """
    if getattr(request.user, "is_platform_admin", False):
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

    effective_permissions = get_effective_permission_codes(
        membership
    )

    is_owner = membership.role.code == ROLE_OWNER

    manage_users_code = (
        "companies."
        f"{PERMISSION_MANAGE_COMPANY_USERS}"
    )
    manage_roles_code = (
        "companies."
        f"{PERMISSION_MANAGE_COMPANY_ROLES}"
    )
    manage_member_permissions_code = (
        "companies."
        f"{PERMISSION_MANAGE_MEMBER_PERMISSIONS}"
    )

    has_manage_users_permission = membership_has_permission(
        membership=membership,
        permission_code=manage_users_code,
    )

    can_manage_users = bool(
        is_owner
        and company.owner_user_management_enabled
        and has_manage_users_permission
    )

    memberships = (
        CompanyMembership.objects
        .filter(company=company)
        .select_related("user", "role")
    )

    active_members = memberships.filter(
        status=CompanyMembership.Status.ACTIVE,
    ).count()

    pending_members = memberships.filter(
        status=CompanyMembership.Status.PENDING_APPROVAL,
    ).count()

    role_count = company.roles.filter(
        is_active=True,
    ).count()

    context = {
        "company": company,
        "membership": membership,
        "is_owner": is_owner,
        "active_members": active_members,
        "pending_members": pending_members,
        "role_count": role_count,
        "effective_permission_count": len(
            effective_permissions
        ),
        "has_manage_users_permission":
            has_manage_users_permission,
        "can_manage_users": can_manage_users,
        "can_manage_roles": (
            manage_roles_code
            in effective_permissions
        ),
        "can_manage_member_permissions": (
            manage_member_permissions_code
            in effective_permissions
        ),
        "can_manage_data": (
            "pigs.manage_data_management"
            in effective_permissions
        ),
    }

    return render(
        request,
        "companies/company_hub.html",
        context,
    )
