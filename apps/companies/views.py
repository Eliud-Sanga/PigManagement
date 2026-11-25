from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .context import resolve_company_context
from .middleware import COMPANY_SESSION_KEY
from .models import Company, CompanyMembership


def _safe_next_url(request):
    candidate = (
        request.POST.get("next")
        or request.GET.get("next")
        or ""
    ).strip()

    if (
        candidate
        and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return candidate

    return reverse("pigs:dashboard")


@login_required
def company_select(request):
    """
    Platform Admin may enter PIGS with zero companies.
    Normal users see only active memberships in active companies.
    """

    if getattr(request.user, "is_platform_admin", False):
        return redirect(
            "users:company_list"
        )
    else:
        memberships = (
            CompanyMembership.objects
            .active()
            .filter(user=request.user)
            .select_related("company", "role")
            .order_by("company__name")
        )
        companies = None

    return render(
        request,
        "companies/company_select.html",
        {
            "companies": companies,
            "memberships": memberships,
            "active_company": getattr(request, "company", None),
            "next_url": _safe_next_url(request),
        },
    )


@login_required
@require_POST
def company_switch(request):
    public_id = (request.POST.get("company") or "").strip()

    if not public_id:
        raise PermissionDenied("A company must be selected.")

    if getattr(request.user, "is_platform_admin", False):
        company = get_object_or_404(
            Company,
            public_id=public_id,
        )
        request.session.pop(
            COMPANY_SESSION_KEY,
            None,
        )
        request.session.modified = True

        return redirect(
            "users:company_detail",
            company_id=company.public_id,
        )

    context = resolve_company_context(
        user=request.user,
        company_public_id=public_id,
    )

    request.session[COMPANY_SESSION_KEY] = str(
        context.company.public_id
    )
    request.session.modified = True

    return redirect(_safe_next_url(request))


@login_required
@require_POST
def company_clear(request):
    request.session.pop(COMPANY_SESSION_KEY, None)
    request.session.modified = True

    return redirect("pigs:company_select")
