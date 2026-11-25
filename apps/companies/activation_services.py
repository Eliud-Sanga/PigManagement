import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .activation_identity import has_established_sign_in
from .models import (
    Company,
    CompanyAuditEvent,
    CompanyMembership,
    CompanyUserActivation,
)


ACTIVATION_TTL_HOURS = 24
TOKEN_BYTES = 32


def _token_hash(raw_token):
    return hashlib.sha256(
        (raw_token or "").encode("utf-8")
    ).hexdigest()


def activation_token_matches(activation, raw_token):
    if activation is None or not raw_token:
        return False

    return secrets.compare_digest(
        activation.token_hash,
        _token_hash(raw_token),
    )


def activation_is_usable(activation, *, now=None):
    now = now or timezone.now()

    if activation is None:
        return False

    membership = activation.membership
    user = activation.user

    return (
        activation.used_at is None
        and activation.expires_at > now
        and membership.status == CompanyMembership.Status.ACTIVE
        and membership.company.status == Company.Status.ACTIVE
        and membership.user_id == user.id
        and not user.is_active
        and not has_established_sign_in(user)
    )


@transaction.atomic
def issue_company_user_activation(
    *,
    membership,
    actor=None,
    ttl_hours=ACTIVATION_TTL_HOURS,
):
    membership = (
        CompanyMembership.objects
        .select_for_update()
        .select_related("company", "user", "role")
        .get(pk=membership.pk)
    )

    if membership.status != CompanyMembership.Status.ACTIVE:
        raise ValidationError(
            "Membership must be approved before activation."
        )

    if membership.company.status != Company.Status.ACTIVE:
        raise ValidationError(
            "Company must be active before user activation."
        )

    user = membership.user

    if has_established_sign_in(user):
        raise ValidationError(
            "This identity already has an established sign-in method."
        )

    if user.is_active:
        raise ValidationError(
            "Activation-pending identity must remain inactive."
        )

    now = timezone.now()
    raw_token = secrets.token_urlsafe(TOKEN_BYTES)

    activation = (
        CompanyUserActivation.objects
        .select_for_update()
        .filter(membership=membership)
        .first()
    )

    rotated = activation is not None

    if activation is None:
        activation = CompanyUserActivation(
            membership=membership,
            user=user,
        )
    else:
        activation.resend_count += 1

    activation.user = user
    activation.token_hash = _token_hash(raw_token)
    activation.expires_at = (
        now + timedelta(hours=int(ttl_hours))
    )
    activation.sent_at = None
    activation.used_at = None
    activation.full_clean()
    activation.save()

    CompanyAuditEvent.objects.create(
        company=membership.company,
        actor=actor,
        action=(
            "COMPANY_USER_ACTIVATION_ROTATED"
            if rotated
            else "COMPANY_USER_ACTIVATION_ISSUED"
        ),
        description=(
            "Company-user activation token rotated."
            if rotated
            else "Company-user activation token issued."
        ),
        metadata={
            "membership_id": membership.pk,
            "user_id": user.pk,
            "expires_at": activation.expires_at.isoformat(),
            "resend_count": activation.resend_count,
        },
    )

    return activation, raw_token


def send_company_user_activation_email(
    *,
    activation,
    raw_token,
    request,
    actor=None,
):
    if not activation_token_matches(activation, raw_token):
        raise ValidationError(
            "Activation token does not match stored hash."
        )

    if not activation_is_usable(activation):
        raise ValidationError(
            "Activation is no longer usable."
        )

    membership = activation.membership
    user = activation.user

    activation_url = request.build_absolute_uri(
        reverse(
            "users:company_user_activation_handoff",
            args=[activation.public_id, raw_token],
        )
    )

    language = (
        membership.approved_language
        or user.preferred_language
        or membership.company.default_language
        or "sw"
    ).lower()

    if language == "sw":
        subject = "Washa akaunti yako ya PIGS"
        body = (
            f"Habari {user.first_name or user.username},\n\n"
            f"Uanachama wako kwenye {membership.company.name} umeidhinishwa.\n\n"
            "Weka password yako mwenyewe kwa kutumia link hii:\n"
            f"{activation_url}\n\n"
            f"Link ita-expire baada ya {ACTIVATION_TTL_HOURS} saa.\n"
            "Owner na Platform Admin hawajui password yako.\n"
            "Kama hukuomba akaunti hii, usitumie link hii."
        )
    else:
        subject = "Activate your PIGS account"
        body = (
            f"Hello {user.first_name or user.username},\n\n"
            f"Your membership in {membership.company.name} has been approved.\n\n"
            "Choose your own password using this link:\n"
            f"{activation_url}\n\n"
            f"The link expires in {ACTIVATION_TTL_HOURS} hours.\n"
            "The Owner and Platform Admin never know your password.\n"
            "If you did not expect this account, do not use the link."
        )

    count = send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )

    if count != 1:
        raise ValidationError(
            "Activation email was not accepted for delivery."
        )

    activation.sent_at = timezone.now()
    activation.save(
        update_fields=["sent_at", "updated_at"]
    )

    CompanyAuditEvent.objects.create(
        company=membership.company,
        actor=actor,
        action="COMPANY_USER_ACTIVATION_EMAIL_SENT",
        description="Secure company-user activation email sent.",
        metadata={
            "membership_id": membership.pk,
            "user_id": user.pk,
            "activation_id": str(activation.public_id),
        },
    )

    return True


@transaction.atomic
def complete_company_user_activation(
    *,
    activation,
    new_password,
    expected_token_hash,
):
    activation = (
        CompanyUserActivation.objects
        .select_for_update()
        .select_related(
            "membership__company",
            "membership__role",
            "user",
        )
        .get(pk=activation.pk)
    )

    if (not isinstance(expected_token_hash, str)
            or not secrets.compare_digest(activation.token_hash, expected_token_hash)
            or not activation_is_usable(activation)):
        raise ValidationError(
            "Activation link is invalid, expired, or already used."
        )

    user = activation.user

    validate_password(
        new_password,
        user=user,
    )

    now = timezone.now()

    user.set_password(new_password)
    user.is_active = True
    user.must_change_password = False
    user.password_changed_at = now
    user.save(
        update_fields=[
            "password",
            "is_active",
            "must_change_password",
            "password_changed_at",
            "updated_at",
        ]
    )

    activation.used_at = now
    activation.save(
        update_fields=["used_at", "updated_at"]
    )

    (
        CompanyUserActivation.objects
        .filter(user=user, used_at__isnull=True)
        .exclude(pk=activation.pk)
        .update(used_at=now)
    )

    CompanyAuditEvent.objects.create(
        company=activation.membership.company,
        actor=user,
        action="COMPANY_USER_ACTIVATED",
        description=(
            "Company user chose a password and activated the identity."
        ),
        metadata={
            "membership_id": activation.membership_id,
            "user_id": user.pk,
            "activation_id": str(activation.public_id),
        },
    )

    return user
