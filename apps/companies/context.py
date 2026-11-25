from dataclasses import dataclass

from django.core.exceptions import PermissionDenied

from .models import (
    Company,
    CompanyMembership,
)


@dataclass(frozen=True)
class CompanyContext:
    company: Company
    membership: CompanyMembership | None


def resolve_company_context(
    *,
    user,
    company_public_id,
):
    if user is None or not user.is_authenticated:
        raise PermissionDenied("Authentication is required.")

    if not company_public_id:
        raise PermissionDenied("Company context is required.")

    if getattr(user, "is_platform_admin", False):
        company = Company.objects.filter(
            public_id=company_public_id,
        ).first()

        if company is None:
            raise PermissionDenied("Company does not exist.")

        return CompanyContext(
            company=company,
            membership=None,
        )

    membership = (
        CompanyMembership.objects
        .active()
        .select_related("company", "role")
        .filter(
            user=user,
            company__public_id=company_public_id,
        )
        .first()
    )

    if membership is None:
        raise PermissionDenied(
            "You do not have access to this company."
        )

    return CompanyContext(
        company=membership.company,
        membership=membership,
    )
