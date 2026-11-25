from datetime import timedelta
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.control_center.models import SecurityAuditEvent
from apps.control_center.services import (
    append_security_event,
    attach_browser_cookie,
    clear_login_failures,
    client_ip,
    ensure_browser_identity,
    hash_value,
)
from apps.users.security import clear_step_up
from .models import PasswordRecoveryChallenge, PasswordRecoveryThrottle

User = get_user_model()

RECOVERY_SESSION_KEY = "pigs_password_recovery_challenge"
OTP_TTL_MINUTES = 10
OTP_VERIFY_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_RESENDS = 3
IDENTITY_WINDOW_MINUTES = 15
IDENTITY_MAX_FAILURES = 5
IDENTITY_LOCK_MINUTES = 30
MAX_CODES_PER_HOUR = 5
RESET_AFTER_VERIFY_MINUTES = 10


def _auth_response(request, response):
    ensure_browser_identity(request)
    return attach_browser_cookie(response, request)


def _render(request, template, context=None, status=200):
    return _auth_response(
        request,
        render(request, template, context or {}, status=status),
    )


def _redirect(request, route_name):
    return _auth_response(request, redirect(route_name))


def _normalize_text(value):
    return (value or "").strip().casefold()


def _normalize_phone(value):
    digits = "".join(c for c in (value or "") if c.isdigit())
    if digits.startswith("255") and len(digits) == 12:
        return digits[-9:]
    if digits.startswith("0") and len(digits) == 10:
        return digits[-9:]
    return digits


def _mask_email(email):
    local, sep, domain = (email or "").partition("@")
    if not sep:
        return "your verified email"
    masked = local[:2] + ("*" * max(1, min(6, len(local) - 2)))
    return f"{masked}@{domain}"


def _browser_context(request):
    _, browser_hash = ensure_browser_identity(request)
    return browser_hash, client_ip(request)


def _throttle_keys(request):
    browser_hash, ip = _browser_context(request)
    keys = [hash_value("recovery-browser:" + browser_hash)]
    if ip:
        keys.append(hash_value("recovery-ip:" + ip))
    return list(dict.fromkeys(keys)), browser_hash, ip


def _throttle_status(request):
    keys, _, _ = _throttle_keys(request)
    now = timezone.now()
    locked = None
    for row in PasswordRecoveryThrottle.objects.filter(key_hash__in=keys):
        if row.is_locked(now=now) and (locked is None or row.locked_until > locked):
            locked = row.locked_until
    return locked


@transaction.atomic
def _register_identity_failure(request):
    keys, _, _ = _throttle_keys(request)
    now = timezone.now()
    cutoff = now - timedelta(minutes=IDENTITY_WINDOW_MINUTES)
    locked = None

    for key in keys:
        row, _ = (
            PasswordRecoveryThrottle.objects
            .select_for_update()
            .get_or_create(key_hash=key)
        )
        if row.window_started_at is None or row.window_started_at < cutoff:
            row.failed_attempts = 0
            row.window_started_at = now

        row.failed_attempts += 1
        row.last_attempt_at = now

        if row.failed_attempts >= IDENTITY_MAX_FAILURES:
            row.locked_until = now + timedelta(minutes=IDENTITY_LOCK_MINUTES)

        row.save()

        if row.locked_until and (locked is None or row.locked_until > locked):
            locked = row.locked_until

    return locked


def _clear_identity_failures(request):
    keys, _, _ = _throttle_keys(request)
    PasswordRecoveryThrottle.objects.filter(key_hash__in=keys).update(
        failed_attempts=0,
        window_started_at=None,
        last_attempt_at=None,
        locked_until=None,
    )


def _audit(
    request,
    *,
    action,
    result,
    reason,
    description,
    user=None,
    identifier="",
    severity=None,
    metadata=None,
):
    if severity is None:
        severity = (
            SecurityAuditEvent.Severity.INFO
            if result == SecurityAuditEvent.Result.SUCCESS
            else SecurityAuditEvent.Severity.WARNING
        )

    append_security_event(
        request=request,
        category=SecurityAuditEvent.Category.AUTHENTICATION,
        action=action,
        result=result,
        severity=severity,
        subject_user=user,
        attempted_identifier=identifier,
        reason_code=reason,
        description=description,
        metadata=metadata or {},
    )


def _identity_matches(user, *, first_name, last_name, email, phone):
    if user is None or not user.is_active or not user.has_usable_password():
        return False

    if not all([
        (user.first_name or "").strip(),
        (user.last_name or "").strip(),
        (user.email or "").strip(),
        (user.phone or "").strip(),
    ]):
        return False

    return all([
        _normalize_text(user.first_name) == _normalize_text(first_name),
        _normalize_text(user.last_name) == _normalize_text(last_name),
        _normalize_text(user.email) == _normalize_text(email),
        _normalize_phone(user.phone) == _normalize_phone(phone),
    ])


def _generate_code():
    return f"{secrets.randbelow(1000000):06d}"


def _send_recovery_code(*, user, code):
    sent = send_mail(
        "PIGS password recovery code",
        (
            "A password recovery request was made for your PIGS account.\n\n"
            f"Your verification code is: {code}\n\n"
            f"This code expires in {OTP_TTL_MINUTES} minutes.\n"
            "Do not share this code with anyone.\n\n"
            "If you did not request this reset, contact your Platform Administrator."
        ),
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )
    return sent == 1


def _challenge_from_session(request):
    raw = request.session.get(RECOVERY_SESSION_KEY)
    if not raw:
        return None
    return (
        PasswordRecoveryChallenge.objects
        .select_related("user")
        .filter(public_id=raw)
        .first()
    )


def _challenge_matches_browser(request, challenge):
    browser_hash, _ = _browser_context(request)
    return secrets.compare_digest(challenge.browser_key_hash, browser_hash)


def _clear_recovery_session(request):
    request.session.pop(RECOVERY_SESSION_KEY, None)
    request.session.modified = True


def recovery_start(request):
    ensure_browser_identity(request)

    if request.user.is_authenticated:
        return _redirect(request, "users:profile_settings")

    if request.method == "GET":
        _clear_recovery_session(request)
        return _render(
            request,
            "users/password_recovery_start.html",
            {"step": 1, "locked_until": _throttle_status(request)},
        )

    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    locked_until = _throttle_status(request)
    if locked_until:
        _audit(
            request,
            action="PASSWORD_RECOVERY_IDENTITY",
            result=SecurityAuditEvent.Result.BLOCKED,
            reason="RECOVERY_TEMPORARILY_LOCKED",
            description="Password recovery identity verification blocked by throttle.",
            severity=SecurityAuditEvent.Severity.HIGH,
        )
        return _render(
            request,
            "users/password_recovery_start.html",
            {"step": 1, "locked_until": locked_until},
            status=429,
        )

    username = request.POST.get("username", "").strip()
    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    email = request.POST.get("email", "").strip()
    phone = request.POST.get("phone", "").strip()

    user = (
        User.objects
        .filter(username__iexact=username, email__iexact=email)
        .first()
    )

    if not _identity_matches(
        user,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
    ):
        locked_until = _register_identity_failure(request)
        _audit(
            request,
            action="PASSWORD_RECOVERY_IDENTITY",
            result=SecurityAuditEvent.Result.DENIED,
            reason="RECOVERY_IDENTITY_MISMATCH",
            description="Password recovery identity details did not match.",
            user=user,
            identifier=username,
        )
        messages.error(
            request,
            "Taarifa hazijalingana na akaunti. Hakiki username, majina, email na namba ya simu. Ikiendelea, wasiliana na Platform Administrator.",
        )
        return _render(
            request,
            "users/password_recovery_start.html",
            {"step": 1, "locked_until": locked_until},
        )

    now = timezone.now()
    recent_count = PasswordRecoveryChallenge.objects.filter(
        user=user,
        created_at__gte=now - timedelta(hours=1),
    ).count()
    recent = (
        PasswordRecoveryChallenge.objects
        .filter(user=user)
        .order_by("-last_sent_at", "-created_at")
        .first()
    )

    if (
        recent_count >= MAX_CODES_PER_HOUR
        or (
            recent
            and recent.last_sent_at
            and (now - recent.last_sent_at).total_seconds()
            < OTP_RESEND_COOLDOWN_SECONDS
        )
    ):
        _audit(
            request,
            action="PASSWORD_RECOVERY_CODE_SEND",
            result=SecurityAuditEvent.Result.BLOCKED,
            reason="RECOVERY_CODE_RATE_LIMIT",
            description="Password recovery code send blocked by rate limit.",
            user=user,
            identifier=username,
        )
        messages.error(request, "Ombi la code limefanyika mara nyingi. Subiri kidogo kisha jaribu tena.")
        return _render(request, "users/password_recovery_start.html", {"step": 1}, status=429)

    code = _generate_code()
    browser_hash, ip = _browser_context(request)

    challenge = PasswordRecoveryChallenge.objects.create(
        user=user,
        code_hash=make_password(code),
        expires_at=now + timedelta(minutes=OTP_TTL_MINUTES),
        last_sent_at=now,
        browser_key_hash=browser_hash,
        request_ip=ip,
    )

    try:
        sent = _send_recovery_code(user=user, code=code)
    except Exception:
        sent = False

    if not sent:
        challenge.delete()
        _audit(
            request,
            action="PASSWORD_RECOVERY_CODE_SEND",
            result=SecurityAuditEvent.Result.FAILURE,
            reason="RECOVERY_EMAIL_DELIVERY_FAILED",
            description="Password recovery identity verified but email delivery failed.",
            user=user,
            identifier=username,
            severity=SecurityAuditEvent.Severity.HIGH,
        )
        messages.error(
            request,
            "Mfumo haujaweza kutuma verification code kwa sasa. Wasiliana na Platform Administrator.",
        )
        return _render(request, "users/password_recovery_start.html", {"step": 1})

    PasswordRecoveryChallenge.objects.filter(
        user=user,
        used_at__isnull=True,
    ).exclude(pk=challenge.pk).update(used_at=now)

    request.session[RECOVERY_SESSION_KEY] = str(challenge.public_id)
    request.session.modified = True
    _clear_identity_failures(request)

    _audit(
        request,
        action="PASSWORD_RECOVERY_CODE_SEND",
        result=SecurityAuditEvent.Result.SUCCESS,
        reason="RECOVERY_CODE_SENT",
        description="Six-digit recovery code sent after full identity match.",
        user=user,
        identifier=username,
        metadata={"expires_minutes": OTP_TTL_MINUTES},
    )

    return _redirect(request, "users:password_recovery_verify")


def recovery_verify(request):
    ensure_browser_identity(request)
    challenge = _challenge_from_session(request)

    if (
        challenge is None
        or challenge.used_at is not None
        or not _challenge_matches_browser(request, challenge)
    ):
        _clear_recovery_session(request)
        messages.error(request, "Anza password recovery upya.")
        return _redirect(request, "users:password_recovery")

    if challenge.verified_at:
        return _redirect(request, "users:password_recovery_reset")

    if challenge.expires_at <= timezone.now():
        challenge.used_at = timezone.now()
        challenge.save(update_fields=["used_at"])
        _clear_recovery_session(request)
        messages.error(request, "Verification code ime-expire. Anza tena.")
        return _redirect(request, "users:password_recovery")

    if request.method == "POST":
        code = "".join(c for c in request.POST.get("code", "") if c.isdigit())
        valid = len(code) == 6 and check_password(code, challenge.code_hash)

        if valid:
            challenge.verified_at = timezone.now()
            challenge.failed_attempts = 0
            challenge.save(update_fields=["verified_at", "failed_attempts"])
            _audit(
                request,
                action="PASSWORD_RECOVERY_CODE_VERIFY",
                result=SecurityAuditEvent.Result.SUCCESS,
                reason="RECOVERY_CODE_VERIFIED",
                description="Password recovery OTP verified successfully.",
                user=challenge.user,
            )
            return _redirect(request, "users:password_recovery_reset")

        challenge.failed_attempts += 1
        if challenge.failed_attempts >= OTP_VERIFY_MAX_ATTEMPTS:
            challenge.used_at = timezone.now()

        challenge.save(update_fields=["failed_attempts", "used_at"])
        _audit(
            request,
            action="PASSWORD_RECOVERY_CODE_VERIFY",
            result=SecurityAuditEvent.Result.DENIED,
            reason="RECOVERY_CODE_INVALID",
            description="Password recovery OTP verification failed.",
            user=challenge.user,
            metadata={"failed_attempts": challenge.failed_attempts},
        )

        if challenge.used_at:
            _clear_recovery_session(request)
            messages.error(
                request,
                "Verification attempts zimezidi. Anza recovery upya au wasiliana na Platform Administrator.",
            )
            return _redirect(request, "users:password_recovery")

        messages.error(request, "Verification code si sahihi.")

    return _render(
        request,
        "users/password_recovery_verify.html",
        {
            "step": 2,
            "masked_email": _mask_email(challenge.user.email),
            "expires_at": challenge.expires_at,
            "resend_count": challenge.resend_count,
            "max_resends": OTP_MAX_RESENDS,
        },
    )


def recovery_resend(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    ensure_browser_identity(request)
    challenge = _challenge_from_session(request)

    if (
        challenge is None
        or challenge.used_at is not None
        or challenge.verified_at is not None
        or not _challenge_matches_browser(request, challenge)
    ):
        _clear_recovery_session(request)
        return _redirect(request, "users:password_recovery")

    now = timezone.now()

    if challenge.resend_count >= OTP_MAX_RESENDS:
        messages.error(request, "Resend limit imefikiwa. Anza recovery upya.")
        return _redirect(request, "users:password_recovery_verify")

    if (
        challenge.last_sent_at
        and (now - challenge.last_sent_at).total_seconds()
        < OTP_RESEND_COOLDOWN_SECONDS
    ):
        messages.error(request, "Subiri kidogo kabla ya kutuma code nyingine.")
        return _redirect(request, "users:password_recovery_verify")

    code = _generate_code()
    old_hash = challenge.code_hash
    old_expiry = challenge.expires_at

    challenge.code_hash = make_password(code)
    challenge.expires_at = now + timedelta(minutes=OTP_TTL_MINUTES)
    challenge.failed_attempts = 0
    challenge.save(update_fields=["code_hash", "expires_at", "failed_attempts"])

    try:
        sent = _send_recovery_code(user=challenge.user, code=code)
    except Exception:
        sent = False

    if not sent:
        challenge.code_hash = old_hash
        challenge.expires_at = old_expiry
        challenge.save(update_fields=["code_hash", "expires_at"])
        _audit(
            request,
            action="PASSWORD_RECOVERY_CODE_SEND",
            result=SecurityAuditEvent.Result.FAILURE,
            reason="RECOVERY_RESEND_EMAIL_FAILED",
            description="Recovery OTP resend email failed.",
            user=challenge.user,
            severity=SecurityAuditEvent.Severity.HIGH,
        )
        messages.error(request, "Code haijaweza kutumwa kwa sasa. Wasiliana na Platform Administrator.")
        return _redirect(request, "users:password_recovery_verify")

    challenge.last_sent_at = now
    challenge.resend_count += 1
    challenge.save(update_fields=["last_sent_at", "resend_count"])

    _audit(
        request,
        action="PASSWORD_RECOVERY_CODE_SEND",
        result=SecurityAuditEvent.Result.SUCCESS,
        reason="RECOVERY_CODE_RESENT",
        description="Password recovery OTP resent.",
        user=challenge.user,
        metadata={"resend_count": challenge.resend_count},
    )

    messages.success(request, "Verification code mpya imetumwa.")
    return _redirect(request, "users:password_recovery_verify")


def recovery_reset(request):
    ensure_browser_identity(request)
    challenge = _challenge_from_session(request)
    now = timezone.now()

    if (
        challenge is None
        or challenge.used_at is not None
        or challenge.verified_at is None
        or not _challenge_matches_browser(request, challenge)
        or (now - challenge.verified_at) > timedelta(minutes=RESET_AFTER_VERIFY_MINUTES)
    ):
        _clear_recovery_session(request)
        messages.error(request, "Password recovery session ime-expire. Anza tena.")
        return _redirect(request, "users:password_recovery")

    errors = []

    if request.method == "POST":
        password1 = request.POST.get("new_password1", "")
        password2 = request.POST.get("new_password2", "")

        if password1 != password2:
            errors.append("Passwords do not match.")
        if not password1:
            errors.append("New password is required.")

        if not errors:
            try:
                validate_password(password1, user=challenge.user)
            except ValidationError as exc:
                errors.extend(list(exc.messages))

        if not errors:
            user = challenge.user
            user.set_password(password1)
            user.password_changed_at = now
            user.must_change_password = False
            user.save(
                update_fields=[
                    "password",
                    "password_changed_at",
                    "must_change_password",
                    "updated_at",
                ]
            )

            challenge.used_at = now
            challenge.save(update_fields=["used_at"])
            clear_login_failures(request=request, user=user)
            clear_step_up(request)
            _clear_recovery_session(request)
            request.session.cycle_key()

            _audit(
                request,
                action="PASSWORD_RECOVERY_COMPLETE",
                result=SecurityAuditEvent.Result.SUCCESS,
                reason="PASSWORD_RESET_BY_VERIFIED_OTP",
                description="Password reset completed after identity match and email OTP.",
                user=user,
                severity=SecurityAuditEvent.Severity.HIGH,
            )

            messages.success(
                request,
                "Password imebadilishwa kwa usalama. Sasa ingia kwa password mpya.",
            )
            return _redirect(request, "users:login")

    return _render(
        request,
        "users/password_recovery_reset.html",
        {"step": 3, "errors": errors},
    )
