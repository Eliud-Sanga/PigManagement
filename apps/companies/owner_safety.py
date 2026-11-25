"""Shared owner lookup. Callers doing writes must coordinate company locks."""
from .constants import ROLE_OWNER
from .models import CompanyMembership


def is_last_active_owner(membership):
    if (membership.role.code != ROLE_OWNER or
            membership.status != CompanyMembership.Status.ACTIVE):
        return False
    return not CompanyMembership.objects.filter(
        company_id=membership.company_id, status=CompanyMembership.Status.ACTIVE,
        role__code=ROLE_OWNER, role__is_active=True, user__is_active=True,
    ).exclude(pk=membership.pk).exists()
