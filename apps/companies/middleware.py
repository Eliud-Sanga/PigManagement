from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils import translation

from .authorization import (
    get_effective_permission_codes,
)
from .context import resolve_company_context
from .constants import SUPPORTED_LANGUAGE_CODES
from .models import CompanyMembership
from .scope import (
    pop_company_scope,
    push_company_scope,
)


COMPANY_SESSION_KEY = "active_company_public_id"


class CompanyContextMiddleware:
    """
    Resolve one company context per request.

    Platform Admin:
        May enter and use platform-level PIGS administration even when
        zero companies exist. Company context is required only for
        company-owned business data.

    Normal user:
        Must have at least one ACTIVE membership in an ACTIVE company.
        A user with no valid company is logged out immediately.

    One active membership:
        Auto-select that company.

    Multiple active memberships:
        No company is auto-selected. The user must choose a company.
        This supports one Owner/User holding different roles and
        permissions in multiple companies of this same PIGS system.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.company = None
        request.company_membership = None
        request.company_permissions = frozenset()
        request.company_choices_required = False

        user = getattr(
            request,
            "user",
            None,
        )

        scope_tokens = None

        if (
            user is not None
            and user.is_authenticated
        ):
            is_platform_admin = bool(
                getattr(
                    user,
                    "is_platform_admin",
                    False,
                )
            )

            active_membership_company_ids = []

            if not is_platform_admin:
                active_membership_company_ids = list(
                    CompanyMembership.objects
                    .active()
                    .filter(user=user)
                    .values_list(
                        "company__public_id",
                        flat=True,
                    )[:2]
                )

                # A non-platform user with no known active company must not
                # remain authenticated inside the system.
                if not active_membership_company_ids:
                    logout(request)

                    return redirect(
                        settings.LOGIN_URL
                    )

            if is_platform_admin:
                language = getattr(user, "preferred_language", None)
                if language in SUPPORTED_LANGUAGE_CODES:
                    translation.activate(language)
                    request.LANGUAGE_CODE = language
                request.session.pop(
                    COMPANY_SESSION_KEY,
                    None,
                )
                company_public_id = None
            else:
                company_public_id = request.session.get(
                    COMPANY_SESSION_KEY
                )

            if (
                not company_public_id
                and not is_platform_admin
            ):
                if len(
                    active_membership_company_ids
                ) == 1:
                    company_public_id = str(
                        active_membership_company_ids[0]
                    )

                    request.session[
                        COMPANY_SESSION_KEY
                    ] = company_public_id

                else:
                    request.company_choices_required = True

            if company_public_id:
                try:
                    context = resolve_company_context(
                        user=user,
                        company_public_id=company_public_id,
                    )

                except PermissionDenied:
                    request.session.pop(
                        COMPANY_SESSION_KEY,
                        None,
                    )

                    if not is_platform_admin:
                        # A stale company selection must not silently fall
                        # through into another tenant.
                        if len(
                            active_membership_company_ids
                        ) == 1:
                            company_public_id = str(
                                active_membership_company_ids[0]
                            )

                            request.session[
                                COMPANY_SESSION_KEY
                            ] = company_public_id

                            context = resolve_company_context(
                                user=user,
                                company_public_id=company_public_id,
                            )

                        else:
                            request.company_choices_required = True
                            context = None

                    else:
                        context = None

                if context is not None:
                    request.company = context.company
                    request.company_membership = context.membership

                    permission_codes = frozenset()

                    if context.membership is not None:
                        permission_codes = (
                            get_effective_permission_codes(
                                context.membership
                            )
                        )

                    request.company_permissions = (
                        permission_codes
                    )

                    scope_tokens = push_company_scope(
                        company=context.company,
                        membership=context.membership,
                        permissions=permission_codes,
                    )

                    allowed_languages = set(
                        context.company.allowed_languages
                        or []
                    )

                    language = getattr(
                        user,
                        "preferred_language",
                        None,
                    )

                    if language not in allowed_languages:
                        language = None

                    if (
                        language is None
                        and context.membership is not None
                    ):
                        candidate = (
                            context
                            .membership
                            .approved_language
                            or None
                        )

                        if candidate in allowed_languages:
                            language = candidate

                    if language is None:
                        candidate = (
                            context.company.default_language
                            or None
                        )

                        if candidate in allowed_languages:
                            language = candidate

                    if language:
                        translation.activate(language)
                        request.LANGUAGE_CODE = language

        try:
            return self.get_response(request)

        finally:
            if scope_tokens is not None:
                pop_company_scope(
                    scope_tokens
                )