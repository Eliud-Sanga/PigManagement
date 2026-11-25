from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.shortcuts import redirect

from .models import Company


SAFE_METHODS = {
    "GET",
    "HEAD",
    "OPTIONS",
}


def company_context_required(view_func):
    """
    Tenant boundary for PIGS business views.

    Platform Admin may open the system with no company at all. Safe/read
    pages may render an empty, fail-closed business state until a company
    is selected.

    Normal users must always have a resolved active company context.

    No user, including Platform Admin, may mutate company-owned business
    data without a selected ACTIVE company.
    """

    @wraps(view_func)
    def wrapped(
        request: HttpRequest,
        *args,
        **kwargs,
    ):
        company = getattr(
            request,
            "company",
            None,
        )

        is_platform_admin = bool(
            getattr(
                request.user,
                "is_platform_admin",
                False,
            )
        )

        if is_platform_admin:
            return redirect(
                "users:control_center"
            )

        if company is None:
            if (
                is_platform_admin
                and request.method.upper()
                in SAFE_METHODS
            ):
                return view_func(
                    request,
                    *args,
                    **kwargs,
                )

            if (
                getattr(
                    request,
                    "company_choices_required",
                    False,
                )
                and request.method.upper()
                in SAFE_METHODS
            ):
                return redirect(
                    "pigs:company_select"
                )

            raise PermissionDenied(
                "Select a company before accessing company business data."
            )

        if (
            company.status != Company.Status.ACTIVE
            and request.method.upper() not in SAFE_METHODS
        ):
            raise PermissionDenied(
                "This company is not active. Business changes are blocked."
            )

        return view_func(
            request,
            *args,
            **kwargs,
        )

    return wrapped