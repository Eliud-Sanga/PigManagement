from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.urls import reverse

from .models import SecuritySettings
from .services import (
    append_security_event,
)
from .models import SecurityAuditEvent


class MaintenanceModeMiddleware:
    """
    Enforce maintenance on every request.

    Platform Administrators are exempt after authentication. Login remains
    reachable so an administrator can authenticate and enter the system.
    Maintenance activation/deactivation is evaluated against timezone-aware
    timestamps on every request; it does not depend on a background task.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        policy = SecuritySettings.get_solo()

        if not policy.maintenance_is_active():
            return self.get_response(request)

        user = getattr(
            request,
            "user",
            None,
        )

        if (
            user is not None
            and user.is_authenticated
            and getattr(
                user,
                "is_platform_admin",
                False,
            )
        ):
            return self.get_response(request)

        allowed_paths = {
            reverse("users:login"),
            reverse("users:logout"),
            reverse("users:maintenance"),
        }

        if request.path in allowed_paths:
            return self.get_response(request)

        session_key = (
            "maintenance_block_audited"
        )

        if not request.session.get(
            session_key
        ):
            append_security_event(
                request=request,
                category=(
                    SecurityAuditEvent
                    .Category
                    .MAINTENANCE
                ),
                action="REQUEST_BLOCKED_BY_MAINTENANCE",
                result=(
                    SecurityAuditEvent
                    .Result
                    .BLOCKED
                ),
                severity=(
                    SecurityAuditEvent
                    .Severity
                    .WARNING
                ),
                subject_user=(
                    user
                    if (
                        user is not None
                        and user.is_authenticated
                    )
                    else None
                ),
                reason_code="MAINTENANCE_ACTIVE",
                description=(
                    "Request blocked because system "
                    "maintenance is active."
                ),
            )

            request.session[
                session_key
            ] = True

        return render(
            request,
            "control_center/maintenance.html",
            {
                "policy": policy,
            },
            status=503,
        )

class ForcePasswordChangeMiddleware:
    """
    Force temporary credentials to be rotated before normal access.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(
            request,
            "user",
            None,
        )

        if (
            user is not None
            and user.is_authenticated
            and getattr(
                user,
                "must_change_password",
                False,
            )
        ):
            allowed_paths = {
                reverse("users:logout"),
                reverse(
                    "users:user_change_password",
                    args=[user.pk],
                ),
                reverse("users:maintenance"),
            }

            allowed_mobile_requests = {
                ("/api/mobile/v1/auth/session/", "GET"),
                ("/api/mobile/v1/auth/csrf/", "GET"),
                ("/api/mobile/v1/auth/logout/", "POST"),
                ("/api/mobile/v1/account/password/change/", "POST"),
            }

            if (
                request.path not in allowed_paths
                and (request.path_info, request.method)
                not in allowed_mobile_requests
            ):
                return redirect(
                    "users:user_change_password",
                    user_id=user.pk,
                )

        return self.get_response(request)




SESSION_STARTED_AT_KEY = "_pigs_session_started_at"
SESSION_LAST_ACTIVITY_AT_KEY = "_pigs_session_last_activity_at"


class SessionPolicyMiddleware:
    # Installation-wide authenticated session policy.
    #
    # Idle timeout is based on the last application request.
    # Absolute timeout cannot be extended by continued activity.
    # Expiration is server-enforced and written to Security Audit.

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _as_int(value, fallback):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def __call__(self, request):
        user = getattr(request, "user", None)

        if (
            user is None
            or not user.is_authenticated
        ):
            return self.get_response(request)

        policy = SecuritySettings.get_solo()
        now_ts = int(timezone.now().timestamp())

        started_at = self._as_int(
            request.session.get(
                SESSION_STARTED_AT_KEY
            ),
            now_ts,
        )
        last_activity_at = self._as_int(
            request.session.get(
                SESSION_LAST_ACTIVITY_AT_KEY
            ),
            now_ts,
        )

        idle_seconds = (
            int(policy.idle_timeout_minutes)
            * 60
        )
        absolute_seconds = (
            int(
                policy
                .absolute_session_timeout_minutes
            )
            * 60
        )

        action = ""
        reason_code = ""
        message = ""

        if (
            now_ts - started_at
            >= absolute_seconds
        ):
            action = "SESSION_ABSOLUTE_TIMEOUT"
            reason_code = "SESSION_MAX_LIFETIME_REACHED"
            message = _(
                "Your session reached its maximum lifetime. "
                "Please sign in again."
            )

        elif (
            now_ts - last_activity_at
            >= idle_seconds
        ):
            action = "SESSION_IDLE_TIMEOUT"
            reason_code = "SESSION_IDLE_LIMIT_REACHED"
            message = _(
                "You were signed out because the session was inactive."
            )

        if action:
            append_security_event(
                request=request,
                category=(
                    SecurityAuditEvent
                    .Category
                    .AUTHENTICATION
                ),
                action=action,
                result=(
                    SecurityAuditEvent
                    .Result
                    .BLOCKED
                ),
                severity=(
                    SecurityAuditEvent
                    .Severity
                    .INFO
                ),
                actor=user,
                subject_user=user,
                reason_code=reason_code,
                description=message,
                metadata={
                    "idle_timeout_minutes":
                        policy.idle_timeout_minutes,
                    "absolute_session_timeout_minutes":
                        policy.absolute_session_timeout_minutes,
                },
            )

            logout(request)
            messages.info(
                request,
                message,
            )

            response = redirect(
                "users:login"
            )
            response["Cache-Control"] = (
                "no-cache, no-store, must-revalidate"
            )
            return response

        request.session[
            SESSION_STARTED_AT_KEY
        ] = started_at
        request.session[
            SESSION_LAST_ACTIVITY_AT_KEY
        ] = now_ts

        remaining_absolute = max(
            60,
            absolute_seconds
            - (now_ts - started_at),
        )

        request.session.set_expiry(
            min(
                idle_seconds,
                remaining_absolute,
            )
        )

        return self.get_response(request)
