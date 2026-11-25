from django.contrib import messages
from django.contrib.auth import (
    get_user_model,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.companies.backends import CompanyRoleBackend
from apps.companies.models import (
    Company,
    CompanyAuditEvent,
    CompanyMembership,
)
from apps.users.models import User

from .account_state import lock_and_validate

from .forms import (
    SecurePasswordChangeForm,
    SecureUserIdentityForm,
    SecuritySettingsForm,
)
from .models import (
    AccountLoginThrottle,
    BrowserLoginThrottle,
    SecurityAuditEvent,
    SecuritySettings,
)
from .services import (
    append_security_event,
    attach_browser_cookie,
    clear_login_failures,
    current_lock_state,
    ensure_browser_identity,
    register_failed_login,
    resolve_login_user,
    verify_audit_chain,
)


def _platform_admin_required(request):
    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or not getattr(
            request.user,
            "is_platform_admin",
            False,
        )
    ):
        raise PermissionDenied(
            "Platform Administrator authority is required."
        )


def _post_login_destination(user):
    if getattr(user, "must_change_password", False):
        return reverse(
            "users:user_change_password",
            args=[user.pk],
        )

    if getattr(
        user,
        "is_platform_admin",
        False,
    ):
        return reverse("users:control_center")

    memberships = (
        CompanyMembership.objects
        .active()
        .filter(user=user)
        .values_list("id", flat=True)[:2]
    )

    if len(list(memberships)) > 1:
        return reverse("pigs:company_select")

    return reverse("pigs:dashboard")


def _login_response(
    request,
    *,
    policy,
    context=None,
    status=200,
):
    response = render(
        request,
        "users/login.html",
        context or {},
        status=status,
    )

    return attach_browser_cookie(
        response,
        request,
        policy=policy,
    )


def secure_login(request):
    """
    Username-or-email login with:
      - browser and account throttling;
      - every POST attempt audited;
      - maintenance enforcement;
      - company membership enforcement for non-platform users.
    """

    policy = SecuritySettings.get_solo()
    ensure_browser_identity(request)

    if request.user.is_authenticated:
        return redirect(
            _post_login_destination(
                request.user
            )
        )

    maintenance_active = (
        policy.maintenance_is_active()
    )

    base_context = {
        "policy": policy,
        "maintenance_active":
            maintenance_active,
    }

    if request.method != "POST":
        lock_state = current_lock_state(
            request=request,
        )

        base_context[
            "browser_locked_until"
        ] = lock_state["locked_until"]

        return _login_response(
            request,
            policy=policy,
            context=base_context,
        )

    identifier = (
        request.POST.get(
            "identifier",
            "",
        )
        .strip()
    )

    password = request.POST.get(
        "password",
        "",
    )

    remember_me = (
        request.POST.get(
            "remember_me"
        )
        == "on"
    )

    target_user = resolve_login_user(
        identifier
    )

    lock_state = current_lock_state(
        request=request,
        user=target_user,
    )

    if lock_state["locked"]:
        append_security_event(
            request=request,
            category=(
                SecurityAuditEvent
                .Category
                .AUTHENTICATION
            ),
            action="LOGIN_ATTEMPT",
            result=(
                SecurityAuditEvent
                .Result
                .BLOCKED
            ),
            severity=(
                SecurityAuditEvent
                .Severity
                .HIGH
            ),
            subject_user=target_user,
            attempted_identifier=identifier,
            reason_code="LOGIN_TEMPORARILY_LOCKED",
            description=(
                "Login blocked by browser/account "
                "temporary lock."
            ),
            metadata={
                "locked_until":
                    (
                        lock_state[
                            "locked_until"
                        ].isoformat()
                        if lock_state[
                            "locked_until"
                        ]
                        else None
                    ),
            },
            browser_key_hash=(
                lock_state[
                    "browser_key_hash"
                ]
            ),
        )

        messages.error(
            request,
            (
                "Login imefungwa kwa muda kutokana na "
                "attempts zilizofeli. Jaribu baada ya muda uliowekwa."
            ),
        )

        base_context[
            "browser_locked_until"
        ] = lock_state["locked_until"]

        return _login_response(
            request,
            policy=policy,
            context=base_context,
            status=429,
        )

    if target_user is None:
        # Perform a real password hash to reduce user-enumeration timing
        # differences for unknown identifiers.
        make_password(
            password or "invalid-password"
        )

        failure = register_failed_login(
            request=request,
            identifier=identifier,
            user=None,
            policy=policy,
        )

        append_security_event(
            request=request,
            category=(
                SecurityAuditEvent
                .Category
                .AUTHENTICATION
            ),
            action="LOGIN_ATTEMPT",
            result=(
                SecurityAuditEvent
                .Result
                .FAILURE
            ),
            severity=(
                SecurityAuditEvent
                .Severity
                .WARNING
            ),
            attempted_identifier=identifier,
            reason_code="INVALID_CREDENTIALS",
            description=(
                "Login failed for an unknown identifier."
            ),
            metadata={
                "browser_failed_attempts":
                    failure[
                        "browser"
                    ].failed_attempts,
                "browser_locked_now":
                    failure[
                        "browser_locked_now"
                    ],
            },
            browser_key_hash=(
                failure[
                    "browser_key_hash"
                ]
            ),
        )

        messages.error(
            request,
            "Username/email au password si sahihi.",
        )

        return _login_response(
            request,
            policy=policy,
            context=base_context,
            status=401,
        )

    if not target_user.check_password(
        password
    ):
        failure = register_failed_login(
            request=request,
            identifier=identifier,
            user=target_user,
            policy=policy,
        )

        locked_now = (
            failure[
                "browser_locked_now"
            ]
            or failure[
                "account_locked_now"
            ]
        )

        append_security_event(
            request=request,
            category=(
                SecurityAuditEvent
                .Category
                .AUTHENTICATION
            ),
            action="LOGIN_ATTEMPT",
            result=(
                SecurityAuditEvent
                .Result
                .FAILURE
            ),
            severity=(
                SecurityAuditEvent
                .Severity
                .HIGH
                if locked_now
                else SecurityAuditEvent
                .Severity
                .WARNING
            ),
            subject_user=target_user,
            attempted_identifier=identifier,
            reason_code=(
                "LOCKOUT_TRIGGERED"
                if locked_now
                else "INVALID_CREDENTIALS"
            ),
            description=(
                "Login failed because password verification failed."
            ),
            metadata={
                "browser_failed_attempts":
                    failure[
                        "browser"
                    ].failed_attempts,
                "account_failed_attempts":
                    (
                        failure[
                            "account"
                        ].failed_attempts
                        if failure[
                            "account"
                        ]
                        else None
                    ),
                "locked_now":
                    locked_now,
            },
            browser_key_hash=(
                failure[
                    "browser_key_hash"
                ]
            ),
        )

        messages.error(
            request,
            (
                "Login imefungwa kwa muda."
                if locked_now
                else "Username/email au password si sahihi."
            ),
        )

        return _login_response(
            request,
            policy=policy,
            context=base_context,
            status=(
                429
                if locked_now
                else 401
            ),
        )

    if not target_user.is_active:
        append_security_event(
            request=request,
            category=(
                SecurityAuditEvent
                .Category
                .AUTHENTICATION
            ),
            action="LOGIN_ATTEMPT",
            result=(
                SecurityAuditEvent
                .Result
                .DENIED
            ),
            severity=(
                SecurityAuditEvent
                .Severity
                .HIGH
            ),
            subject_user=target_user,
            attempted_identifier=identifier,
            reason_code="ACCOUNT_INACTIVE",
            description=(
                "Correct credentials supplied for an inactive account."
            ),
        )

        messages.error(
            request,
            "Akaunti hii haijaruhusiwa kuingia.",
        )

        return _login_response(
            request,
            policy=policy,
            context=base_context,
            status=403,
        )

    backend = CompanyRoleBackend()

    if not backend.user_can_authenticate(
        target_user
    ):
        append_security_event(
            request=request,
            category=(
                SecurityAuditEvent
                .Category
                .AUTHENTICATION
            ),
            action="LOGIN_ATTEMPT",
            result=(
                SecurityAuditEvent
                .Result
                .DENIED
            ),
            severity=(
                SecurityAuditEvent
                .Severity
                .HIGH
            ),
            subject_user=target_user,
            attempted_identifier=identifier,
            reason_code="NO_ACTIVE_COMPANY",
            description=(
                "Credentials were valid but the non-platform user "
                "has no active company membership."
            ),
        )

        messages.error(
            request,
            (
                "Akaunti hii haina company active "
                "inayoruhusiwa kutumia PIGS."
            ),
        )

        return _login_response(
            request,
            policy=policy,
            context=base_context,
            status=403,
        )

    if (
        maintenance_active
        and not target_user.is_platform_admin
    ):
        append_security_event(
            request=request,
            category=(
                SecurityAuditEvent
                .Category
                .MAINTENANCE
            ),
            action="LOGIN_ATTEMPT",
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
            subject_user=target_user,
            attempted_identifier=identifier,
            reason_code="MAINTENANCE_ACTIVE",
            description=(
                "Valid non-admin login blocked by active maintenance."
            ),
        )

        messages.warning(
            request,
            policy.maintenance_message,
        )

        return _login_response(
            request,
            policy=policy,
            context=base_context,
            status=503,
        )

    login(
        request,
        target_user,
        backend=(
            "apps.companies.backends."
            "CompanyRoleBackend"
        ),
    )

    if remember_me:
        request.session.set_expiry(
            14 * 24 * 60 * 60
        )
    else:
        request.session.set_expiry(0)

    clear_login_failures(
        request=request,
        user=target_user,
    )

    append_security_event(
        request=request,
        category=(
            SecurityAuditEvent
            .Category
            .AUTHENTICATION
        ),
        action="LOGIN_ATTEMPT",
        result=(
            SecurityAuditEvent
            .Result
            .SUCCESS
        ),
        severity=(
            SecurityAuditEvent
            .Severity
            .INFO
        ),
        actor=target_user,
        subject_user=target_user,
        attempted_identifier=identifier,
        reason_code="LOGIN_SUCCESS",
        description=(
            "Successful PIGS login."
        ),
        metadata={
            "login_method":
                (
                    "email"
                    if "@"
                    in identifier
                    else "username"
                ),
            "remember_me":
                remember_me,
        },
    )

    messages.success(
        request,
        f"Karibu {target_user.username}.",
    )

    response = redirect(
        _post_login_destination(
            target_user
        )
    )

    return attach_browser_cookie(
        response,
        request,
        policy=policy,
    )


@require_POST
def secure_logout(request):
    user = (
        request.user
        if request.user.is_authenticated
        else None
    )

    if user is not None:
        append_security_event(
            request=request,
            category=(
                SecurityAuditEvent
                .Category
                .AUTHENTICATION
            ),
            action="LOGOUT",
            result=(
                SecurityAuditEvent
                .Result
                .SUCCESS
            ),
            severity=(
                SecurityAuditEvent
                .Severity
                .INFO
            ),
            actor=user,
            subject_user=user,
            reason_code="USER_LOGOUT",
            description="User logged out.",
        )

    logout(request)

    response = redirect(
        "users:login"
    )

    response[
        "Cache-Control"
    ] = (
        "no-cache, no-store, must-revalidate"
    )

    return response


def maintenance_status(request):
    policy = SecuritySettings.get_solo()

    if not policy.maintenance_is_active():
        return redirect(
            "users:login"
        )

    return render(
        request,
        "control_center/maintenance.html",
        {
            "policy": policy,
        },
        status=503,
    )


@login_required
def dashboard(request):
    _platform_admin_required(request)

    policy = SecuritySettings.get_solo()

    context = {
        "policy": policy,
        "company_total":
            Company.objects.count(),
        "company_active":
            Company.objects.filter(
                status=Company.Status.ACTIVE
            ).count(),
        "company_suspended":
            Company.objects.filter(
                status=Company.Status.SUSPENDED
            ).count(),
        "pending_memberships":
            CompanyMembership.objects.filter(
                status=(
                    CompanyMembership
                    .Status
                    .PENDING_APPROVAL
                )
            ).count(),
        "user_total":
            User.objects.count(),
        "locked_accounts":
            AccountLoginThrottle.objects.filter(
                locked_until__gt=
                    timezone.now()
            ).count(),
        "locked_browsers":
            BrowserLoginThrottle.objects.filter(
                locked_until__gt=
                    timezone.now()
            ).count(),
        "recent_security_events":
            SecurityAuditEvent.objects
            .select_related(
                "actor",
                "subject_user",
                "company",
            )[:10],
    }

    return render(
        request,
        "control_center/dashboard.html",
        context,
    )


@login_required
def policy_console(request):
    _platform_admin_required(request)

    policy = SecuritySettings.get_solo()

    context = {
        "policy": policy,
        "company_total":
            Company.objects.count(),
        "owner_delegation_enabled":
            Company.objects.filter(
                owner_user_management_enabled=True
            ).count(),
        "sw_default_companies":
            Company.objects.filter(
                default_language="sw"
            ).count(),
        "en_default_companies":
            Company.objects.filter(
                default_language="en"
            ).count(),
        "pending_memberships":
            CompanyMembership.objects.filter(
                status=(
                    CompanyMembership
                    .Status
                    .PENDING_APPROVAL
                )
            ).count(),
        "maintenance_active":
            policy.maintenance_is_active(),
    }

    return render(
        request,
        "control_center/policy_console.html",
        context,
    )


@login_required
def security_settings(request):
    _platform_admin_required(request)

    policy = SecuritySettings.get_solo()

    if request.method == "POST":
        form = SecuritySettingsForm(
            request.POST,
            instance=policy,
        )

        if form.is_valid():
            password = form.cleaned_data[
                "admin_password"
            ]

            if not request.user.check_password(
                password
            ):
                append_security_event(
                    request=request,
                    category=(
                        SecurityAuditEvent
                        .Category
                        .ADMIN
                    ),
                    action="SECURITY_SETTINGS_SAVE",
                    result=(
                        SecurityAuditEvent
                        .Result
                        .DENIED
                    ),
                    severity=(
                        SecurityAuditEvent
                        .Severity
                        .HIGH
                    ),
                    actor=request.user,
                    reason_code="ADMIN_PASSWORD_CONFIRMATION_FAILED",
                    description=(
                        "Security settings save denied because "
                        "administrator password confirmation failed."
                    ),
                )

                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                before = {
                    field:
                        getattr(policy, field)
                    for field in (
                        "max_failed_login_attempts",
                        "failed_attempt_window_minutes",
                        "login_lock_minutes",
                        "device_cookie_days",
                        "idle_timeout_minutes",
                        "absolute_session_timeout_minutes",
                        "maintenance_enabled",
                        "maintenance_starts_at",
                        "maintenance_ends_at",
                        "maintenance_message",
                    )
                }

                updated = form.save(
                    commit=False
                )
                updated.updated_by = (
                    request.user
                )
                updated.full_clean()
                updated.save()

                after = {
                    field:
                        getattr(updated, field)
                    for field
                    in before
                }

                append_security_event(
                    request=request,
                    category=(
                        SecurityAuditEvent
                        .Category
                        .ADMIN
                    ),
                    action="SECURITY_SETTINGS_SAVE",
                    result=(
                        SecurityAuditEvent
                        .Result
                        .SUCCESS
                    ),
                    severity=(
                        SecurityAuditEvent
                        .Severity
                        .HIGH
                    ),
                    actor=request.user,
                    reason_code="SETTINGS_UPDATED",
                    description=(
                        "Platform security settings were updated."
                    ),
                    metadata={
                        "before": before,
                        "after": after,
                    },
                )

                messages.success(
                    request,
                    "Security settings zimehifadhiwa.",
                )

                return redirect(
                    "users:security_settings"
                )

    else:
        form = SecuritySettingsForm(
            instance=policy,
        )

    return render(
        request,
        "control_center/security_settings.html",
        {
            "form": form,
            "policy": policy,
        },
    )


@login_required
def audit_list(request):
    _platform_admin_required(request)

    queryset = (
        SecurityAuditEvent.objects
        .select_related(
            "actor",
            "subject_user",
            "company",
        )
        .all()
    )

    query = (
        request.GET.get(
            "q",
            "",
        )
        .strip()
    )

    category = (
        request.GET.get(
            "category",
            "",
        )
        .strip()
    )

    result = (
        request.GET.get(
            "result",
            "",
        )
        .strip()
    )

    severity = (
        request.GET.get(
            "severity",
            "",
        )
        .strip()
    )

    if query:
        queryset = queryset.filter(
            Q(action__icontains=query)
            | Q(reason_code__icontains=query)
            | Q(actor_identifier__icontains=query)
            | Q(subject_identifier__icontains=query)
            | Q(attempted_identifier__icontains=query)
            | Q(ip_address__icontains=query)
            | Q(description__icontains=query)
        )

    if category:
        queryset = queryset.filter(
            category=category
        )

    if result:
        queryset = queryset.filter(
            result=result
        )

    if severity:
        queryset = queryset.filter(
            severity=severity
        )

    paginator = Paginator(
        queryset,
        50,
    )

    page = paginator.get_page(
        request.GET.get("page")
    )

    integrity = verify_audit_chain()

    return render(
        request,
        "control_center/audit_list.html",
        {
            "page_obj": page,
            "integrity": integrity,
            "categories":
                SecurityAuditEvent
                .Category
                .choices,
            "results":
                SecurityAuditEvent
                .Result
                .choices,
            "severities":
                SecurityAuditEvent
                .Severity
                .choices,
            "filters": {
                "q": query,
                "category": category,
                "result": result,
                "severity": severity,
            },
        },
    )


@login_required
def audit_detail(request, event_id):
    _platform_admin_required(request)

    event = get_object_or_404(
        SecurityAuditEvent.objects
        .select_related(
            "actor",
            "subject_user",
            "company",
        ),
        event_id=event_id,
    )

    return render(
        request,
        "control_center/audit_detail.html",
        {
            "event": event,
        },
    )


@login_required
def user_directory(request):
    _platform_admin_required(request)

    query = (
        request.GET.get(
            "q",
            "",
        )
        .strip()
    )

    users = (
        User.objects
        .prefetch_related(
            "company_memberships__company",
            "company_memberships__role",
        )
        .order_by("username")
    )

    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )

    return render(
        request,
        "control_center/user_directory.html",
        {
            "users": users,
            "query": query,
        },
    )


@login_required
def user_detail(request, user_id):
    _platform_admin_required(request)

    target = get_object_or_404(
        User.objects.prefetch_related(
            "company_memberships__company",
            "company_memberships__role",
        ),
        pk=user_id,
    )

    return render(
        request,
        "control_center/user_detail.html",
        {
            "target": target,
        },
    )


@login_required
def secure_user_edit(request, user_id):
    _platform_admin_required(request)

    target = get_object_or_404(
        User,
        pk=user_id,
    )

    before = {
        "username": target.username,
        "email": target.email,
        "first_name": target.first_name,
        "last_name": target.last_name,
        "is_active": target.is_active,
    }

    if request.method == "POST":
        form = SecureUserIdentityForm(
            request.POST,
            instance=target,
        )

        if form.is_valid():
            if not request.user.check_password(
                form.cleaned_data[
                    "admin_password"
                ]
            ):
                append_security_event(
                    request=request,
                    category=(
                        SecurityAuditEvent
                        .Category
                        .IDENTITY
                    ),
                    action="USER_IDENTITY_EDIT",
                    result=(
                        SecurityAuditEvent
                        .Result
                        .DENIED
                    ),
                    severity=(
                        SecurityAuditEvent
                        .Severity
                        .HIGH
                    ),
                    actor=request.user,
                    subject_user=target,
                    reason_code="ADMIN_PASSWORD_CONFIRMATION_FAILED",
                    description=(
                        "User identity edit denied because "
                        "administrator password confirmation failed."
                    ),
                )

                form.add_error(
                    "admin_password",
                    "Administrator password is incorrect.",
                )
            else:
                try:
                    with transaction.atomic():
                        locked = lock_and_validate(
                            request.user.pk, target.pk,
                            form.cleaned_data["is_active"],
                            form.cleaned_data.get("account_revision"),
                        )
                        # Reapply only validated identity fields to the locked,
                        # current record; never save the mutated ModelForm instance.
                        for field in ("username", "email", "phone", "first_name", "last_name", "is_active"):
                            setattr(locked, field, form.cleaned_data[field])
                        locked.save(update_fields=["username", "email", "phone", "first_name",
                                                   "last_name", "is_active", "updated_at"])
                        updated = locked
                        after = {
                            "username": updated.username, "email": updated.email,
                            "first_name": updated.first_name, "last_name": updated.last_name,
                            "is_active": updated.is_active,
                        }
                        append_security_event(
                            request=request, category=SecurityAuditEvent.Category.IDENTITY,
                            action="USER_IDENTITY_EDIT", result=SecurityAuditEvent.Result.SUCCESS,
                            severity=SecurityAuditEvent.Severity.HIGH, actor=request.user,
                            subject_user=updated, reason_code="IDENTITY_UPDATED",
                            description="Username/email/profile information updated after administrator password confirmation.",
                            metadata={"before": before, "after": after},
                        )
                    messages.success(request, "Taarifa za user zimebadilishwa kwa usalama.")
                    return redirect("users:user_detail", user_id=updated.pk)
                except ValidationError as exc:
                    form.add_error(None, "; ".join(exc.messages))
                except OperationalError:
                    form.add_error(None, "Account is busy. Reload before trying again.")

    else:
        form = SecureUserIdentityForm(
            instance=target,
        )

    return render(
        request,
        "control_center/user_edit.html",
        {
            "form": form,
            "target": target,
        },
    )


@login_required
def secure_password_change(request, user_id):
    target = get_object_or_404(
        User,
        pk=user_id,
    )

    if (
        request.user.pk != target.pk
        and not getattr(
            request.user,
            "is_platform_admin",
            False,
        )
    ):
        raise PermissionDenied(
            "You cannot change this password."
        )

    if request.method == "POST":
        form = SecurePasswordChangeForm(
            request.POST,
            actor=request.user,
            target=target,
        )

        if form.is_valid():
            target.set_password(
                form.cleaned_data[
                    "new_password1"
                ]
            )

            if hasattr(
                target,
                "must_change_password",
            ):
                # Self-service completes first-login rotation.
                # Admin reset creates a temporary credential.
                target.must_change_password = (
                    request.user.pk != target.pk
                )

            if hasattr(
                target,
                "password_changed_at",
            ):
                target.password_changed_at = (
                    timezone.now()
                )

            update_fields = [
                "password",
            ]

            if hasattr(
                target,
                "must_change_password",
            ):
                update_fields.append(
                    "must_change_password"
                )

            if hasattr(
                target,
                "password_changed_at",
            ):
                update_fields.append(
                    "password_changed_at"
                )

            target.save(
                update_fields=update_fields
            )

            AccountLoginThrottle.objects.filter(
                user=target
            ).update(
                failed_attempts=0,
                locked_until=None,
                last_failed_at=None,
            )

            if request.user.pk == target.pk:
                update_session_auth_hash(
                    request,
                    target,
                )

            append_security_event(
                request=request,
                category=(
                    SecurityAuditEvent
                    .Category
                    .IDENTITY
                ),
                action="PASSWORD_CHANGE",
                result=(
                    SecurityAuditEvent
                    .Result
                    .SUCCESS
                ),
                severity=(
                    SecurityAuditEvent
                    .Severity
                    .HIGH
                ),
                actor=request.user,
                subject_user=target,
                reason_code=(
                    "SELF_PASSWORD_CHANGE"
                    if request.user.pk
                    == target.pk
                    else "ADMIN_PASSWORD_RESET"
                ),
                description=(
                    "Password changed after current actor "
                    "password confirmation."
                ),
            )

            messages.success(
                request,
                "Password imebadilishwa kwa usalama.",
            )

            if (
                request.user.pk
                == target.pk
            ):
                return redirect(
                    _post_login_destination(
                        request.user
                    )
                )

            return redirect(
                "users:user_detail",
                user_id=target.pk,
            )
    else:
        form = SecurePasswordChangeForm(
            actor=request.user,
            target=target,
        )

    return render(
        request,
        "control_center/password_change.html",
        {
            "form": form,
            "target": target,
            "self_change":
                (
                    request.user.pk
                    == target.pk
                ),
        },
    )


@login_required
def retired_legacy_admin(request, *args, **kwargs):
    _platform_admin_required(request)

    messages.info(
        request,
        (
            "Legacy Group/direct-permission administration "
            "imeondolewa kwenye active security path. "
            "Tumia Control Center."
        ),
    )

    return redirect(
        "users:control_center"
    )
