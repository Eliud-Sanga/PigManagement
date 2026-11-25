"""Google Identity bridge for existing, approved Home Management users.

The endpoint never auto-creates a company or membership.  A verified Google
account may only link to an existing active local account with the same email.
The immutable Google ``sub`` claim is persisted and used on future sign-ins.
"""

from __future__ import annotations

import logging
import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model, login as django_login
from django.db import transaction
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.companies.backends import CompanyRoleBackend
from apps.companies.onboarding_models import CompanyInvitationThrottle
from apps.users.models import GoogleIdentity
from apps.users.utils import create_audit_log

from .views import _json


User = get_user_model()
logger = logging.getLogger(__name__)
GOOGLE_LOGIN_LIMIT = 10
GOOGLE_LOGIN_WINDOW = timedelta(minutes=15)


@transaction.atomic
def _consume_google_login_slot(request):
    # Count before contacting Google's token verifier. Never trust a client
    # supplied forwarding header for the throttle identity.
    address = request.META.get("REMOTE_ADDR") or "unknown"
    key = hashlib.sha256(f"bizmanager-google-login:{address}".encode()).hexdigest()
    row, _ = CompanyInvitationThrottle.objects.select_for_update().get_or_create(
        key_hash=key
    )
    now = timezone.now()
    if row.locked_until and row.locked_until > now:
        return False
    if row.window_started_at is None or row.window_started_at <= now - GOOGLE_LOGIN_WINDOW:
        row.failed_attempts = 0
        row.window_started_at = now
        row.locked_until = None
    if row.failed_attempts >= GOOGLE_LOGIN_LIMIT:
        row.locked_until = now + GOOGLE_LOGIN_WINDOW
        row.last_attempt_at = now
        row.save(update_fields=["locked_until", "last_attempt_at"])
        return False
    row.failed_attempts += 1
    row.last_attempt_at = now
    row.save(update_fields=[
        "failed_attempts", "window_started_at", "last_attempt_at", "locked_until"
    ])
    return True


def _client_ids():
    configured = getattr(settings, "BIZMANAGER_GOOGLE_CLIENT_IDS", ())
    if isinstance(configured, str):
        configured = configured.split(",")
    return tuple(
        value.strip()
        for value in configured
        if isinstance(value, str) and value.strip()
    )


def _error(request, code, message, status):
    return _json(
        request,
        {"authenticated": False, "code": code, "message": message},
        status_code=status,
    )


def _verify_google_token(token, audiences):
    # Lazy imports keep the rest of mobile authentication usable even when a
    # deployment intentionally leaves the optional Google provider disabled.
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(
        token,
        google_requests.Request(),
        audience=None,
    )
    if claims.get("aud") not in audiences:
        raise ValueError("Google token audience is not allowed.")
    return claims


@never_cache
@require_GET
def provider_config(request):
    audiences = _client_ids()
    return _json(
        request,
        {
            "ok": True,
            "google": {
                "enabled": bool(audiences),
                "clientConfigured": bool(audiences),
            },
        },
    )


@never_cache
@require_POST
@csrf_protect
def google_login(request):
    audiences = _client_ids()
    if not audiences:
        return _error(
            request,
            "google_not_configured",
            "Google Sign-In is not configured on this server.",
            503,
        )

    token = (request.POST.get("id_token") or "").strip()
    if not _consume_google_login_slot(request):
        return _error(
            request, "google_rate_limited", "Too many sign-in attempts. Try again later.", 429
        )
    if not token or len(token) > 10000:
        return _error(
            request,
            "google_token_required",
            "A valid Google ID token is required.",
            400,
        )

    try:
        claims = _verify_google_token(token, audiences)
    except (ValueError, TypeError):
        return _error(
            request,
            "google_token_invalid",
            "Google could not verify this sign-in.",
            401,
        )
    except Exception as error:
        logger.warning(
            "Google identity verification is temporarily unavailable (%s).",
            error.__class__.__name__,
        )
        return _error(
            request,
            "google_verification_unavailable",
            "Google verification is temporarily unavailable. Try again shortly.",
            503,
        )

    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    email_verified = claims.get("email_verified") is True
    if not subject or not email or not email_verified:
        return _error(
            request,
            "google_identity_incomplete",
            "Google must provide a verified email address.",
            403,
        )

    with transaction.atomic():
        identity = (
            GoogleIdentity.objects.select_for_update()
            .select_related("user")
            .filter(subject=subject)
            .first()
        )
        if identity is None:
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                return _error(
                    request,
                    "google_account_not_linked",
                    "Ask a Platform Administrator to create or invite this email first.",
                    403,
                )
            if GoogleIdentity.objects.filter(user=user).exists():
                return _error(
                    request,
                    "google_link_conflict",
                    "This local account is already linked to another Google identity.",
                    409,
                )
            identity = GoogleIdentity(
                user=user,
                subject=subject,
                email_at_link=email,
            )
        else:
            user = identity.user

        if not user.is_active:
            return _error(
                request,
                "account_access_denied",
                "This account is inactive.",
                403,
            )
        if getattr(user, "is_platform_admin", False):
            return _error(
                request,
                "platform_admin_control_plane",
                "Platform Administrator must use the web Control Center.",
                403,
            )
        if not CompanyRoleBackend().user_can_authenticate(user):
            return _error(
                request,
                "company_access_required",
                "This Google account has no active company membership.",
                403,
            )

        identity.email_at_link = email
        identity.display_name = str(claims.get("name") or "")[:255]
        identity.picture_url = str(claims.get("picture") or "")[:500]
        identity.last_login_at = timezone.now()
        identity.full_clean()
        identity.save()

    django_login(
        request,
        user,
        backend="apps.companies.backends.CompanyRoleBackend",
    )
    remember_me = (request.POST.get("remember_me") or "") == "on"
    request.session.set_expiry(14 * 24 * 60 * 60 if remember_me else 0)
    create_audit_log(
        request,
        "LOGIN",
        "User",
        user.pk,
        "Successful Google Sign-In.",
        metadata={
            "login_method": "google",
            "remember_me": remember_me,
            "google_subject": subject,
        },
    )

    return _json(
        request,
        {
            "authenticated": True,
            "requiresPasswordChange": bool(user.must_change_password),
            "next": "/api/mobile/v1/auth/session/",
        },
    )
