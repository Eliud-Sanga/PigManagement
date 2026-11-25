"""Additive JSON bridge for Home Management mobile/web clients.

The existing PIGS secure_login / secure_logout functions remain the
authoritative authentication implementation. This module only adapts
their result to JSON and adds an exact DEBUG-only development CORS
allowlist for Flutter web.
"""

from django.conf import settings
from django.contrib.auth import get_user
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils.cache import patch_vary_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.control_center.views import secure_login, secure_logout


_DEV_WEB_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}


@never_cache
@require_GET
def health_live(request):
    """Process liveness probe; deliberately avoids the database."""
    return JsonResponse(
        {"status": "ok"},
        headers={"Cache-Control": "no-store"},
    )


@never_cache
@require_GET
def health_ready(request):
    """Readiness probe with a sanitized database connectivity result."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse(
            {"status": "unavailable"},
            status=503,
            headers={"Cache-Control": "no-store"},
        )
    return JsonResponse(
        {"status": "ready"},
        headers={"Cache-Control": "no-store"},
    )


def _apply_dev_cors(request, response):
    origin = request.headers.get("Origin", "")

    trusted_lan_origin = (
        origin.startswith("http://")
        and origin.endswith(":5173")
        and origin in getattr(settings, "CSRF_TRUSTED_ORIGINS", ())
    )
    if settings.DEBUG and (origin in _DEV_WEB_ORIGINS or trusted_lan_origin):
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
        patch_vary_headers(response, ("Origin",))

    return response


def _json(request, payload, *, status_code=200, original=None):
    response = JsonResponse(payload, status=status_code)
    response["Cache-Control"] = "no-store, private"

    if original is not None:
        # Preserve PIGS device/browser cookies created by secure_login.
        response.cookies.update(original.cookies)

    return _apply_dev_cors(request, response)


@never_cache
@require_GET
@ensure_csrf_cookie
def csrf(request):
    return _json(
        request,
        {"csrfToken": get_token(request)},
    )


@never_cache
@require_POST
@csrf_protect
def login(request):
    original = secure_login(request)

    # Do not trust the request.user object cached before secure_login.
    # Re-resolve from the session that secure_login just established.
    authenticated_user = get_user(request)

    if (
        authenticated_user.is_authenticated
        and 300 <= original.status_code < 400
    ):
        return _json(
            request,
            {
                "authenticated": True,
                "requiresPasswordChange": bool(
                    getattr(
                        authenticated_user,
                        "must_change_password",
                        False,
                    )
                ),
                "next": "/api/mobile/v1/auth/session/",
            },
            original=original,
        )

    if original.status_code == 429:
        status = 429
        code = "login_temporarily_locked"
        message = "Too many login attempts. Please try again later."
    elif original.status_code == 503:
        status = 503
        code = "maintenance"
        message = "The service is temporarily unavailable."
    elif original.status_code == 403:
        status = 403
        code = "account_access_denied"
        message = (
            "This account is not currently allowed to access this service."
        )
    else:
        status = 401
        code = "invalid_credentials"
        message = "Unable to sign in. Check your details or account access."

    return _json(
        request,
        {
            "authenticated": False,
            "code": code,
            "message": message,
        },
        status_code=status,
        original=original,
    )


@never_cache
@require_GET
def session(request):
    user = getattr(request, "user", None)

    if user is None or not user.is_authenticated:
        return _json(
            request,
            {
                "authenticated": False,
                "code": "authentication_required",
            },
            status_code=401,
        )

    company = getattr(request, "company", None)
    selected_company = None

    if company is not None:
        selected_company = {
            "id": str(company.public_id),
            "name": company.name,
        }

    return _json(
        request,
        {
            "authenticated": True,
            "user": {
                "id": str(user.pk),
                "username": user.get_username(),
                "requiresPasswordChange": bool(
                    getattr(
                        user,
                        "must_change_password",
                        False,
                    )
                ),
            },
            "selectedCompany": selected_company,
            "companySelectionRequired": bool(
                getattr(
                    request,
                    "company_choices_required",
                    False,
                )
            ),
        },
    )


@never_cache
@require_POST
@csrf_protect
def logout(request):
    original = secure_logout(request)

    return _json(
        request,
        {
            "authenticated": False,
            "signedOut": True,
        },
        original=original,
    )
