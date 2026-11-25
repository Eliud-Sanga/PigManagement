"""Rules for issuing a new company-member activation link."""
from apps.companies.activation_identity import has_established_sign_in
from apps.companies.models import Company, CompanyMembership


def eligible(membership):
    user = membership.user
    return (membership.status == CompanyMembership.Status.ACTIVE
            and membership.company.status == Company.Status.ACTIVE
            and bool(membership.approved_at) and bool(membership.approved_language)
            and bool(user.email) and not user.is_active and not has_established_sign_in(user))
