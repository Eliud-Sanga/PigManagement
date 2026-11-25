import hashlib
import json
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    AccountLoginThrottle,
    BrowserLoginThrottle,
    SecurityAuditEvent,
    SecuritySettings,
)


User = get_user_model()

DEVICE_COOKIE_NAME = "pigs_device"
SENSITIVE_METADATA_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
}


def normalize_identifier(value):
    return (value or "").strip().casefold()


def hash_value(value):
    return hashlib.sha256(
        (value or "").encode("utf-8")
    ).hexdigest()


def client_ip(request):
    forwarded = (
        request.META.get(
            "HTTP_X_FORWARDED_FOR",
            "",
        )
        if request is not None
        else ""
    )

    if forwarded:
        return forwarded.split(",", 1)[0].strip()

    if request is None:
        return None

    return request.META.get(
        "REMOTE_ADDR"
    )


def ensure_browser_identity(request):
    token = getattr(
        request,
        "_pigs_device_token",
        None,
    )

    if token:
        return token, hash_value(token)

    token = (
        request.COOKIES.get(
            DEVICE_COOKIE_NAME,
            "",
        )
        or ""
    ).strip()

    if len(token) < 32:
        token = secrets.token_urlsafe(32)
        request._pigs_device_cookie_new = True

    request._pigs_device_token = token

    return token, hash_value(token)


def attach_browser_cookie(
    response,
    request,
    *,
    policy=None,
):
    token = getattr(
        request,
        "_pigs_device_token",
        None,
    )

    if not token:
        return response

    if not getattr(
        request,
        "_pigs_device_cookie_new",
        False,
    ):
        return response

    policy = (
        policy
        or SecuritySettings.get_solo()
    )

    response.set_cookie(
        DEVICE_COOKIE_NAME,
        token,
        max_age=(
            int(policy.device_cookie_days)
            * 24
            * 60
            * 60
        ),
        httponly=True,
        secure=request.is_secure(),
        samesite="Lax",
        path="/",
    )

    return response


def resolve_login_user(identifier):
    normalized = normalize_identifier(
        identifier
    )

    if not normalized:
        return None

    return (
        User.objects
        .filter(
            Q(username__iexact=normalized)
            | Q(email__iexact=normalized)
        )
        .order_by("id")
        .first()
    )


def _scrub_metadata(value):
    if isinstance(value, dict):
        cleaned = {}

        for key, item in value.items():
            lowered = str(key).casefold()

            if any(
                marker in lowered
                for marker
                in SENSITIVE_METADATA_KEYS
            ):
                cleaned[str(key)] = "[REDACTED]"
            else:
                cleaned[str(key)] = (
                    _scrub_metadata(item)
                )

        return cleaned

    if isinstance(value, (list, tuple)):
        return [
            _scrub_metadata(item)
            for item
            in value
        ]

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
            type(None),
        ),
    ):
        return value

    return str(value)


def _event_payload(
    *,
    occurred_at,
    category,
    action,
    result,
    severity,
    actor_identifier,
    subject_identifier,
    attempted_identifier,
    reason_code,
    description,
    metadata,
    ip_address,
    browser_key_hash,
    request_path,
    request_method,
):
    return {
        "occurred_at":
            occurred_at.isoformat(),
        "category":
            str(category),
        "action":
            str(action),
        "result":
            str(result),
        "severity":
            str(severity),
        "actor_identifier":
            actor_identifier or "",
        "subject_identifier":
            subject_identifier or "",
        "attempted_identifier":
            attempted_identifier or "",
        "reason_code":
            reason_code or "",
        "description":
            description or "",
        "metadata":
            _scrub_metadata(
                metadata or {}
            ),
        "ip_address":
            ip_address or "",
        "browser_key_hash":
            browser_key_hash or "",
        "request_path":
            request_path or "",
        "request_method":
            request_method or "",
    }


def _hash_event(
    *,
    previous_hash,
    payload,
):
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    material = (
        (previous_hash or "")
        + "|"
        + canonical
    ).encode("utf-8")

    return hashlib.sha256(
        material
    ).hexdigest()


@transaction.atomic
def append_security_event(
    *,
    request=None,
    category,
    action,
    result,
    severity=SecurityAuditEvent.Severity.INFO,
    actor=None,
    subject_user=None,
    company=None,
    attempted_identifier="",
    reason_code="",
    description="",
    metadata=None,
    browser_key_hash="",
):
    previous = (
        SecurityAuditEvent.objects
        .select_for_update()
        .order_by("-id")
        .first()
    )

    previous_hash = (
        previous.event_hash
        if previous is not None
        else ""
    )

    occurred_at = timezone.now()

    if actor is None and request is not None:
        candidate = getattr(
            request,
            "user",
            None,
        )

        if (
            candidate is not None
            and candidate.is_authenticated
        ):
            actor = candidate

    actor_identifier = ""

    if actor is not None:
        actor_identifier = (
            actor.email
            or actor.username
            or str(actor.pk)
        )

    subject_identifier = ""

    if subject_user is not None:
        subject_identifier = (
            subject_user.email
            or subject_user.username
            or str(subject_user.pk)
        )

    if request is not None:
        _, detected_browser_hash = (
            ensure_browser_identity(
                request
            )
        )

        if not browser_key_hash:
            browser_key_hash = (
                detected_browser_hash
            )

    ip_address = (
        client_ip(request)
        if request is not None
        else None
    )

    request_path = (
        request.path[:300]
        if request is not None
        else ""
    )

    request_method = (
        request.method[:10]
        if request is not None
        else ""
    )

    user_agent = (
        request.META.get(
            "HTTP_USER_AGENT",
            "",
        )[:500]
        if request is not None
        else ""
    )

    clean_metadata = _scrub_metadata(
        metadata or {}
    )

    payload = _event_payload(
        occurred_at=occurred_at,
        category=category,
        action=action,
        result=result,
        severity=severity,
        actor_identifier=actor_identifier,
        subject_identifier=subject_identifier,
        attempted_identifier=(
            attempted_identifier or ""
        )[:254],
        reason_code=reason_code,
        description=description,
        metadata=clean_metadata,
        ip_address=ip_address,
        browser_key_hash=browser_key_hash,
        request_path=request_path,
        request_method=request_method,
    )

    event_hash = _hash_event(
        previous_hash=previous_hash,
        payload=payload,
    )

    return SecurityAuditEvent.objects.create(
        occurred_at=occurred_at,
        category=category,
        action=action,
        result=result,
        severity=severity,
        actor=actor,
        subject_user=subject_user,
        company=company,
        actor_identifier=actor_identifier,
        subject_identifier=subject_identifier,
        attempted_identifier=(
            attempted_identifier or ""
        )[:254],
        reason_code=reason_code,
        description=description,
        metadata=clean_metadata,
        ip_address=ip_address,
        user_agent=user_agent,
        browser_key_hash=browser_key_hash,
        request_path=request_path,
        request_method=request_method,
        previous_hash=previous_hash,
        event_hash=event_hash,
    )


def verify_audit_chain():
    previous_hash = ""
    checked = 0

    queryset = (
        SecurityAuditEvent.objects
        .order_by("id")
        .iterator(chunk_size=500)
    )

    for event in queryset:
        payload = _event_payload(
            occurred_at=event.occurred_at,
            category=event.category,
            action=event.action,
            result=event.result,
            severity=event.severity,
            actor_identifier=event.actor_identifier,
            subject_identifier=event.subject_identifier,
            attempted_identifier=event.attempted_identifier,
            reason_code=event.reason_code,
            description=event.description,
            metadata=event.metadata,
            ip_address=event.ip_address,
            browser_key_hash=event.browser_key_hash,
            request_path=event.request_path,
            request_method=event.request_method,
        )

        expected = _hash_event(
            previous_hash=previous_hash,
            payload=payload,
        )

        if (
            event.previous_hash
            != previous_hash
            or event.event_hash
            != expected
        ):
            return {
                "valid": False,
                "checked": checked,
                "broken_event_id":
                    str(event.event_id),
            }

        previous_hash = event.event_hash
        checked += 1

    return {
        "valid": True,
        "checked": checked,
        "broken_event_id": None,
    }


def _window_expired(
    *,
    last_failed_at,
    policy,
    now,
):
    if not last_failed_at:
        return True

    cutoff = now - timedelta(
        minutes=int(
            policy.failed_attempt_window_minutes
        )
    )

    return last_failed_at < cutoff


def browser_throttle_state(
    *,
    browser_key_hash,
):
    return (
        BrowserLoginThrottle.objects
        .filter(
            browser_key_hash=browser_key_hash
        )
        .first()
    )


def account_throttle_state(
    *,
    user,
):
    if user is None:
        return None

    return (
        AccountLoginThrottle.objects
        .filter(user=user)
        .first()
    )


def register_failed_login(
    *,
    request,
    identifier,
    user=None,
    policy=None,
):
    policy = (
        policy
        or SecuritySettings.get_solo()
    )

    _, browser_key_hash = (
        ensure_browser_identity(
            request
        )
    )

    identifier_hash = hash_value(
        normalize_identifier(identifier)
    )

    now = timezone.now()
    lock_delta = timedelta(
        minutes=int(
            policy.login_lock_minutes
        )
    )

    browser, _ = (
        BrowserLoginThrottle.objects
        .select_for_update()
        .get_or_create(
            browser_key_hash=
                browser_key_hash,
        )
    )

    if _window_expired(
        last_failed_at=
            browser.last_failed_at,
        policy=policy,
        now=now,
    ):
        browser.failed_attempts = 0

    browser.failed_attempts += 1
    browser.last_failed_at = now
    browser.last_identifier_hash = (
        identifier_hash
    )
    browser.last_ip_address = (
        client_ip(request)
    )

    browser_locked_now = False

    if (
        browser.failed_attempts
        >= policy.max_failed_login_attempts
    ):
        browser.locked_until = (
            now + lock_delta
        )
        browser_locked_now = True

    browser.save()

    account = None
    account_locked_now = False

    if user is not None:
        account, _ = (
            AccountLoginThrottle.objects
            .select_for_update()
            .get_or_create(
                user=user
            )
        )

        if _window_expired(
            last_failed_at=
                account.last_failed_at,
            policy=policy,
            now=now,
        ):
            account.failed_attempts = 0

        account.failed_attempts += 1
        account.last_failed_at = now
        account.last_ip_address = (
            client_ip(request)
        )

        if (
            account.failed_attempts
            >= policy.max_failed_login_attempts
        ):
            account.locked_until = (
                now + lock_delta
            )
            account_locked_now = True

        account.save()

    return {
        "browser":
            browser,
        "account":
            account,
        "browser_locked_now":
            browser_locked_now,
        "account_locked_now":
            account_locked_now,
        "browser_key_hash":
            browser_key_hash,
    }


def clear_login_failures(
    *,
    request,
    user,
):
    _, browser_key_hash = (
        ensure_browser_identity(
            request
        )
    )

    BrowserLoginThrottle.objects.filter(
        browser_key_hash=browser_key_hash
    ).update(
        failed_attempts=0,
        locked_until=None,
        last_failed_at=None,
    )

    AccountLoginThrottle.objects.filter(
        user=user
    ).update(
        failed_attempts=0,
        locked_until=None,
        last_failed_at=None,
    )


def current_lock_state(
    *,
    request,
    user=None,
):
    _, browser_key_hash = (
        ensure_browser_identity(
            request
        )
    )

    now = timezone.now()

    browser = browser_throttle_state(
        browser_key_hash=
            browser_key_hash
    )

    account = account_throttle_state(
        user=user
    )

    locked_until_candidates = []

    if browser and browser.is_locked(now=now):
        locked_until_candidates.append(
            browser.locked_until
        )

    if account and account.is_locked(now=now):
        locked_until_candidates.append(
            account.locked_until
        )

    locked_until = (
        max(locked_until_candidates)
        if locked_until_candidates
        else None
    )

    return {
        "locked":
            bool(locked_until),
        "locked_until":
            locked_until,
        "browser":
            browser,
        "account":
            account,
        "browser_key_hash":
            browser_key_hash,
    }
