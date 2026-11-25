"""Distinguish established identities from first-time activation accounts."""
from apps.companies.models import CompanyUserActivation
from apps.users.models import GoogleIdentity


def has_established_sign_in(user):
    return (user.has_usable_password()
            or GoogleIdentity.objects.filter(user_id=user.pk).exists()
            or CompanyUserActivation.objects.filter(user_id=user.pk, used_at__isnull=False).exists())
