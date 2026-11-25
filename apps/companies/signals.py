from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from apps.pigs.models import Pig, SlaughterBatch

from .constants import (
    DELEGABLE_COMPANY_PERMISSION_CODENAMES,
)
from .models import (
    CompanyMembership,
    CompanyRole,
)


def _invalid_local_permissions(pk_set):
    if not pk_set:
        return False

    allowed = (
        Q(content_type__app_label__in=("pigs", "transport"))
        |
        Q(
            content_type__app_label="companies",
            codename__in=(
                DELEGABLE_COMPANY_PERMISSION_CODENAMES
            ),
        )
    )

    return (
        Permission.objects
        .filter(pk__in=pk_set)
        .exclude(allowed)
        .exists()
    )


@receiver(
    m2m_changed,
    sender=CompanyRole.permissions.through,
)
def validate_role_permissions(
    sender,
    instance,
    action,
    pk_set,
    **kwargs,
):
    if action != "pre_add":
        return

    if _invalid_local_permissions(pk_set):
        raise ValidationError(
            "A company role can contain only company business permissions "
            "and approved company-local governance permissions."
        )


@receiver(
    m2m_changed,
    sender=CompanyMembership.granted_permissions.through,
)
def validate_membership_grants(
    sender,
    instance,
    action,
    pk_set,
    **kwargs,
):
    if action != "pre_add":
        return

    if _invalid_local_permissions(pk_set):
        raise ValidationError(
            "Invalid membership permission grant."
        )

    if instance.denied_permissions.filter(
        pk__in=pk_set,
    ).exists():
        raise ValidationError(
            "The same permission cannot be both granted and denied."
        )


@receiver(
    m2m_changed,
    sender=CompanyMembership.denied_permissions.through,
)
def validate_membership_denials(
    sender,
    instance,
    action,
    pk_set,
    **kwargs,
):
    if action != "pre_add":
        return

    if _invalid_local_permissions(pk_set):
        raise ValidationError(
            "Invalid membership permission denial."
        )

    if instance.granted_permissions.filter(
        pk__in=pk_set,
    ).exists():
        raise ValidationError(
            "The same permission cannot be both granted and denied."
        )


@receiver(
    m2m_changed,
    sender=SlaughterBatch.pigs.through,
)
def prevent_cross_company_batch_pigs(
    sender,
    instance,
    action,
    reverse,
    model,
    pk_set,
    **kwargs,
):
    """
    Guard the M2M relation even when code bypasses SlaughterBatch.add_pigs().
    """

    if action != "pre_add" or not pk_set:
        return

    if reverse:
        pig_company_id = instance.company_id

        invalid = (
            SlaughterBatch.all_objects
            .filter(pk__in=pk_set)
            .exclude(company_id=pig_company_id)
            .exists()
        )

    else:
        batch_company_id = instance.company_id

        invalid = (
            Pig.all_objects
            .filter(pk__in=pk_set)
            .exclude(company_id=batch_company_id)
            .exists()
        )

    if invalid:
        raise ValidationError(
            "Cross-company pig/batch relationship is not allowed."
        )