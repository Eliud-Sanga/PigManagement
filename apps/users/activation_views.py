import secrets

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils import translation
from django.views.decorators.http import require_GET, require_http_methods

from apps.companies.activation_services import (
    activation_is_usable,
    activation_token_matches,
    complete_company_user_activation,
)
from apps.companies.models import CompanyUserActivation


ACTIVATION_SESSION_KEY = "pigs_company_user_activation"


def _activation(public_id):
    return (
        CompanyUserActivation.objects
        .select_related(
            "membership__company",
            "membership__role",
            "user",
        )
        .filter(public_id=public_id)
        .first()
    )


def _language(activation):
    if activation is None:
        return "sw"

    return (
        activation.membership.approved_language
        or activation.user.preferred_language
        or activation.membership.company.default_language
        or "sw"
    ).lower()


def _render(
    request,
    activation,
    *,
    errors=None,
    invalid=False,
    status=200,
):
    language = _language(activation)
    translation.activate(language)
    request.LANGUAGE_CODE = language

    response = render(
        request,
        "users/company_user_activation.html",
        {
            "activation": activation,
            "errors": errors or [],
            "invalid": invalid,
            "language": language,
        },
        status=status,
    )
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def company_user_activation_handoff(
    request,
    activation_id,
    token,
):
    activation = _activation(activation_id)

    if (
        activation is None
        or not activation_token_matches(activation, token)
        or not activation_is_usable(activation)
    ):
        request.session.pop(ACTIVATION_SESSION_KEY, None)
        return _render(
            request,
            activation,
            invalid=True,
            status=400,
        )

    request.session.cycle_key()
    request.session[ACTIVATION_SESSION_KEY] = {
        "id": str(activation.public_id),
        "token_hash": activation.token_hash,
    }
    request.session.modified = True

    response = redirect(
        "users:company_user_activation_complete"
    )
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


@require_http_methods(["GET", "POST"])
def company_user_activation_complete(request):
    proof = request.session.get(ACTIVATION_SESSION_KEY)
    activation_id = proof.get("id") if isinstance(proof, dict) else None
    expected_hash = proof.get("token_hash") if isinstance(proof, dict) else None

    activation = (
        _activation(activation_id)
        if activation_id
        else None
    )

    if (
        activation is None
        or not isinstance(expected_hash, str)
        or not secrets.compare_digest(activation.token_hash, expected_hash)
        or not activation_is_usable(activation)
    ):
        request.session.pop(ACTIVATION_SESSION_KEY, None)
        return _render(
            request,
            activation,
            invalid=True,
            status=400,
        )

    errors = []

    if request.method == "POST":
        password1 = request.POST.get(
            "new_password1",
            "",
        )
        password2 = request.POST.get(
            "new_password2",
            "",
        )

        if not password1:
            errors.append("New password is required.")

        if password1 != password2:
            errors.append("Passwords do not match.")

        if not errors:
            try:
                user = complete_company_user_activation(
                    activation=activation,
                    new_password=password1,
                    expected_token_hash=expected_hash,
                )
            except ValidationError as exc:
                errors.extend(list(exc.messages))
            else:
                request.session.pop(
                    ACTIVATION_SESSION_KEY,
                    None,
                )
                request.session.cycle_key()

                messages.success(
                    request,
                    (
                        "Akaunti imewashwa kwa usalama. "
                        "Sasa ingia kwa password yako mpya."
                    ),
                )

                response = redirect("users:login")
                response.set_cookie(
                    settings.LANGUAGE_COOKIE_NAME,
                    user.preferred_language or "sw",
                    max_age=60 * 60 * 24 * 365,
                    secure=settings.SESSION_COOKIE_SECURE,
                    samesite="Lax",
                )
                response["Referrer-Policy"] = "no-referrer"
                return response

    return _render(
        request,
        activation,
        errors=errors,
    )
