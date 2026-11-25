from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.companies.constants import (
    ROLE_OWNER,
    SUPPORTED_LANGUAGE_CODES,
)
from apps.companies.models import Company, CompanyAuditEvent

from .forms_profile import (
    OwnerCompanySettingsForm,
    PersonalSettingsForm,
)


@login_required
@require_POST
def language_switch(request):
    company = getattr(
        request,
        "company",
        None,
    )

    allowed = set(
        company.allowed_languages
        if company is not None
        else SUPPORTED_LANGUAGE_CODES
    )

    language = (
        request.POST.get(
            "language",
            "",
        )
        .strip()
        .lower()
    )

    next_url = (
        request.POST.get(
            "next",
            "",
        )
        .strip()
    )

    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = (
            "users:control_center"
            if getattr(
                request.user,
                "is_platform_admin",
                False,
            )
            else "pigs:dashboard"
        )

    if language not in allowed:
        messages.error(
            request,
            "That language is not enabled for this company.",
        )
        return redirect(next_url)

    if (
        request.user.preferred_language
        != language
    ):
        request.user.preferred_language = language
        request.user.save(
            update_fields=[
                "preferred_language",
                "updated_at",
            ]
        )

    translation.activate(language)
    request.LANGUAGE_CODE = language

    response = redirect(next_url)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language,
        max_age=365 * 24 * 60 * 60,
        secure=request.is_secure(),
        httponly=False,
        samesite="Lax",
    )

    messages.success(
        request,
        "Language preference updated.",
    )

    return response


@login_required
def profile_settings(request):
    company = getattr(request, "company", None)

    before = {
        "username": request.user.username,
        "email": request.user.email,
        "phone": request.user.phone,
        "first_name": request.user.first_name,
        "last_name": request.user.last_name,
        "preferred_language":
            request.user.preferred_language,
        "theme_preference":
            request.user.theme_preference,
        "profile_photo":
            bool(request.user.profile_photo),
    }

    if request.method == "POST":
        form = PersonalSettingsForm(
            request.POST,
            request.FILES,
            instance=request.user,
            actor=request.user,
            company=company,
        )

        if form.is_valid():
            updated = form.save()

            if company is not None:
                CompanyAuditEvent.objects.create(
                    company=company,
                    actor=request.user,
                    action="USER_SELF_PROFILE_UPDATED",
                    description=(
                        "User updated personal profile/preferences "
                        "after password confirmation."
                    ),
                    metadata={
                        "before": before,
                        "after": {
                            "username": updated.username,
                            "email": updated.email,
                            "phone": updated.phone,
                            "first_name": updated.first_name,
                            "last_name": updated.last_name,
                            "preferred_language":
                                updated.preferred_language,
                            "theme_preference":
                                updated.theme_preference,
                            "profile_photo":
                                bool(updated.profile_photo),
                        },
                    },
                )

            messages.success(
                request,
                "Profile na preferences zimehifadhiwa kwa usalama.",
            )

            translation.activate(
                updated.preferred_language
            )

            response = redirect(
                "users:profile_settings"
            )
            response.set_cookie(
                settings.LANGUAGE_COOKIE_NAME,
                updated.preferred_language,
                max_age=365 * 24 * 60 * 60,
                secure=request.is_secure(),
                httponly=False,
                samesite="Lax",
            )
            return response
    else:
        form = PersonalSettingsForm(
            instance=request.user,
            actor=request.user,
            company=company,
        )

    base_template = (
        "control_center/base.html"
        if getattr(
            request.user,
            "is_platform_admin",
            False,
        )
        else "pigs/base.html"
    )

    return render(
        request,
        "users/profile_settings.html",
        {
            "form": form,
            "base_template": base_template,
            "company": company,
        },
    )


@login_required
def owner_company_settings(request):
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

    if membership.role.code != ROLE_OWNER:
        raise PermissionDenied(
            "Only the Company Owner can edit company profile."
        )

    before = {
        "name": company.name,
        "email": company.email,
        "phone": company.phone,
        "address": company.address,
        "logo": bool(company.logo),
    }

    if request.method == "POST":
        form = OwnerCompanySettingsForm(
            request.POST,
            request.FILES,
            instance=company,
            actor=request.user,
        )

        if form.is_valid():
            company = form.save()

            CompanyAuditEvent.objects.create(
                company=company,
                actor=request.user,
                action="OWNER_COMPANY_PROFILE_UPDATED",
                description=(
                    "Company Owner updated company profile "
                    "after password confirmation."
                ),
                metadata={
                    "before": before,
                    "after": {
                        "name": company.name,
                        "email": company.email,
                        "phone": company.phone,
                        "address": company.address,
                        "logo": bool(company.logo),
                    },
                },
            )

            messages.success(
                request,
                "Taarifa za kampuni zimehifadhiwa.",
            )

            return redirect(
                "users:owner_company_settings"
            )
    else:
        form = OwnerCompanySettingsForm(
            instance=company,
            actor=request.user,
        )

    return render(
        request,
        "companies/company_settings.html",
        {
            "form": form,
            "company": company,
            "membership": membership,
        },
    )
