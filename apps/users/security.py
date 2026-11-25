import hashlib
import time

from django.core.exceptions import PermissionDenied


STEP_UP_SESSION_KEY = "pigs_security_step_up"


def _password_fingerprint(user):
    return hashlib.sha256(
        user.get_session_auth_hash().encode("utf-8")
    ).hexdigest()


def mark_step_up_verified(
    *,
    request,
    password,
):
    user = request.user

    if (
        user is None
        or not user.is_authenticated
        or not user.is_active
    ):
        raise PermissionDenied(
            "Authentication is required."
        )

    if not user.has_usable_password():
        raise PermissionDenied(
            "Local password confirmation is unavailable."
        )

    if not user.check_password(password or ""):
        raise PermissionDenied(
            "Password confirmation failed."
        )

    request.session[STEP_UP_SESSION_KEY] = {
        "user_id": user.pk,
        "verified_at": int(time.time()),
        "password_fingerprint": _password_fingerprint(user),
    }
    request.session.modified = True

    return True


def is_step_up_verified(
    *,
    request,
    max_age_seconds=300,
):
    user = getattr(request, "user", None)

    if (
        user is None
        or not user.is_authenticated
        or not user.is_active
    ):
        return False

    state = request.session.get(
        STEP_UP_SESSION_KEY
    )

    if not isinstance(state, dict):
        return False

    if state.get("user_id") != user.pk:
        return False

    if (
        state.get("password_fingerprint")
        != _password_fingerprint(user)
    ):
        return False

    try:
        verified_at = int(
            state.get("verified_at", 0)
        )
    except (TypeError, ValueError):
        return False

    age = int(time.time()) - verified_at

    return (
        age >= 0
        and age <= int(max_age_seconds)
    )


def require_step_up(
    *,
    request,
    max_age_seconds=300,
):
    if not is_step_up_verified(
        request=request,
        max_age_seconds=max_age_seconds,
    ):
        raise PermissionDenied(
            "Recent password confirmation is required."
        )

    return True


def clear_step_up(request):
    request.session.pop(
        STEP_UP_SESSION_KEY,
        None,
    )
    request.session.modified = True
